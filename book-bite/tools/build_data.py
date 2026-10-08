#!/usr/bin/env python3
"""Build the Book Bite dataset.

Fetches the most popular English fiction from Project Gutenberg (via the
Gutendex API) and a hand-curated list of Russian books from lib.ru /
az.lib.ru, extracts the opening paragraphs of each book and writes small
static JSON files that the web app loads.

Usage:
    python3 build_data.py                 # full build (50 EN + catalog RU)
    python3 build_data.py --limit-en 10 --limit-ru 5
    python3 build_data.py --only g1342 --only ru-anna-karenina
    python3 build_data.py --refresh       # ignore cached downloads

Only the standard library is used.
"""

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
APP_DIR = TOOLS_DIR.parent
DATA_DIR = APP_DIR / "data"
BOOKS_DIR = DATA_DIR / "books"
CACHE_DIR = TOOLS_DIR / ".cache"

GUTENDEX_URL = (
    "https://gutendex.com/books/?languages=en&sort=popular&topic=fiction&page={page}"
)
GUTENBERG_TXT_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
GUTENBERG_PAGE_URL = "https://www.gutenberg.org/ebooks/{id}"

USER_AGENT = "book-bite-builder/1.0 (+https://github.com/sumerki2020/connectors)"
TARGET_CHARS = 3000  # how much text to keep per book
MIN_PARAGRAPHS = 2
MAX_PARAGRAPHS = 40

# Titles that are not a single narrative work even though Gutendex tags them as fiction.
JUNK_TITLE_RE = re.compile(
    r"complete works|collected works|the works of|selected (works|stories|tales)|anthology|dictionary|"
    r"\bvol(ume)?s?\.? ?([ivxl]+|\d+)\b|part (two|three|ii|iii)|"
    r"short stories|tales of|stories of|poems|poetry|verse|index of",
    re.I,
)

# Gutendex bookshelves that mark a well-known work. The raw download ranking is
# polluted with forgotten pulp beyond the first ~35 titles; a book must sit on one
# of these shelves (or be listed in include_en.json) to make the catalog.
CLASSIC_SHELF_RE = re.compile(
    r"Best Books Ever|Classics of Literature|Banned Books|Harvard Classics|Children's Literature|"
    r"Detective Fiction|Precursors of Science Fiction|Gothic Fiction|Movie Books",
    re.I,
)

# Chapter / part headings in English texts.
EN_HEADING_RE = re.compile(
    r"^(chapter|book|part|letter|stave|canto|act)\s+(i|1|one|the first|first)\b[.:]?(\s|$)",
    re.I,
)
EN_BARE_NUMERAL_RE = re.compile(r"^(i|1|one)[.:]?$", re.I)

# Chapter / part headings in Russian texts.
RU_HEADING_RE = re.compile(
    r"^(глава|часть|книга|том|письмо)\s+(i|1|первая|первое|первый|один)\b[.:]?(\s|$)",
    re.I,
)
RU_BARE_NUMERAL_RE = re.compile(r"^(i|1|первая|первое)[.:]?$", re.I)

ANY_HEADING_RE = re.compile(
    r"^(chapter|book|part|letter|stave|canto|act|глава|часть|книга|том|письмо)\s+\S+",
    re.I,
)
ROMAN_RE = re.compile(r"^[ivxlc]+[.:]?$|^\d{1,3}[.:]?$", re.I)

FRONT_MATTER_RE = re.compile(
    r"project gutenberg|transcriber|produced by|^contents\b|^preface\b|^introduction\b|"
    r"^dedication\b|^foreword\b|^to the reader|^note\b|^illustrations?\b|^by the same author|"
    r"^copyright|all rights reserved|^printed in|^published by|^translated by|^edited by|"
    r"this etext|etext was produced|^produced from|^original publication|^first published|"
    r"^от издателя|^от автора|^предисловие|^вступление|^оглавление|^содержание|^примечани|"
    r"источник текста|оригинал здесь|\bocr\b|spellcheck|сканирован|собрание сочинений|^текст с |^перевод с ",
    re.I,
)

ILLUSTRATION_RE = re.compile(r"^\[?(illustration|иллюстрация)", re.I)


# ----------------------------------------------------------------------------
# Networking with a disk cache
# ----------------------------------------------------------------------------


class Fetcher:
    def __init__(self, refresh=False, delay=0.5):
        self.refresh = refresh
        self.delay = delay
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, url):
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        return CACHE_DIR / digest

    def get(self, url, retries=4):
        path = self._cache_path(url)
        if path.exists() and not self.refresh:
            return path.read_bytes()
        wait = 2.0
        last_error = None
        for _ in range(retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = resp.read()
                path.write_bytes(data)
                time.sleep(self.delay)
                return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 404:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            time.sleep(wait)
            wait *= 2
        raise RuntimeError(f"failed to fetch {url}: {last_error}")


# ----------------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------------


def normalize_space(text):
    return re.sub(r"\s+", " ", text).strip()


def typographic(text, lang):
    """Plain-text conventions -> real punctuation; drop footnote markers."""
    text = re.sub(r"(?<!-)--(?!-)", "—", text)
    text = re.sub(r"(?<![.])\.{3}(?![.])", "…", text)
    text = re.sub(
        r"\s?\[\d{1,3}\]|\s?\(\*\d{1,3}\)|\s?\{\d{1,4}\}", "", text
    )  # [5] (*1) {12}
    if lang == "ru":
        # Russian plain text uses a spaced hyphen for the dash and brackets for editor's notes.
        text = re.sub(r"(?<=\s)-(?=\s)", "—", text)
        text = re.sub(r"^-(?=\s)", "—", text)
        text = re.sub(r"\s?\[[^\[\]]{1,120}\]", "", text)
        text = re.sub(r"\s?\{[^{}]{1,300}\}", "", text)
    return normalize_space(text)


def strip_tags(fragment):
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return html.unescape(fragment)


def decode_cyrillic(raw, preferred):
    """Decode bytes that are either KOI8-R or Windows-1251."""
    candidates = [preferred] + [
        e for e in ("koi8-r", "cp1251", "utf-8") if e != preferred
    ]
    best_text, best_score = "", -1
    for enc in candidates:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            text = raw.decode(enc, "replace")
        score = sum(text.count(ch) for ch in "оеаинтсрвл") - text.count("�") * 10
        if score > best_score:
            best_text, best_score = text, score
    return best_text


def is_heading(paragraph, lang):
    """True for a short heading of the FIRST chapter/part/book/letter."""
    if len(paragraph) > 90:
        return False
    text = paragraph.strip("#[] ").strip()
    if lang == "ru":
        return bool(RU_HEADING_RE.match(text) or RU_BARE_NUMERAL_RE.match(text))
    return bool(EN_HEADING_RE.match(text) or EN_BARE_NUMERAL_RE.match(text))


def is_any_heading(paragraph):
    """True for any chapter-like heading (any number), e.g. a table-of-contents line."""
    if len(paragraph) > 200:
        return False
    text = paragraph.strip("#[] ").strip()
    return bool(ANY_HEADING_RE.match(text) or ROMAN_RE.match(text))


def is_narrative(paragraph):
    """Heuristic: does this look like a real paragraph of prose?"""
    text = paragraph.strip()
    if len(text) < 60:
        return False
    if ILLUSTRATION_RE.match(text) or FRONT_MATTER_RE.search(text[:80]):
        return False
    if is_any_heading(text):
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    lower_ratio = sum(ch.islower() for ch in letters) / len(letters)
    if lower_ratio < 0.6:  # headings, title pages, dramatis personae
        return False
    if text.count("http") or text.count("@"):
        return False
    if (
        len(text) >= 200
    ):  # long enough to be prose even without a sentence end (e.g. ends with ':')
        return True
    return bool(re.search(r"[.!?…»”\"']\s*$|[.!?…][»”\"']?\s", text))


def find_first_chapter(paragraphs, lang):
    """Index of the paragraph following the real first-chapter heading, or 0.

    A table of contents repeats the headings, so a candidate counts only when
    prose follows it within a few paragraphs, before any other heading-like
    line (TOC entries come in runs: "Letter 1", "Letter 2", ...).
    """
    for i, paragraph in enumerate(paragraphs):
        if not is_heading(paragraph, lang):
            continue
        for following in paragraphs[
            i + 1 : i + 30
        ]:  # epigraph verse may sit in between
            if is_narrative(following):
                return i + 1
            if is_any_heading(following) and not is_heading(following, lang):
                break
    return 0


def pick_opening(paragraphs, lang, override=None):
    """Return the list of opening paragraphs.

    paragraphs: list of strings; headings are prefixed with "## ".
    override: optional dict with "start_after" (regex matched against a
    paragraph; the opening starts after the LAST paragraph matching it) and/or
    "skip" (number of narrative paragraphs to skip).
    """
    override = override or {}
    start = 0

    if "start_after" in override:
        pattern = re.compile(override["start_after"], re.I)
        matches = [i for i, p in enumerate(paragraphs) if pattern.search(p)]
        if not matches:
            raise RuntimeError(
                f"override start_after {override['start_after']!r} matched nothing"
            )
        start = matches[-1] + 1
    else:
        start = find_first_chapter(paragraphs, lang)

    collected = []
    total = 0
    to_skip = int(override.get("skip", 0))
    for paragraph in paragraphs[start:]:
        if paragraph.startswith("## "):
            if collected and total >= 1500:
                break
            continue
        if not collected and not is_narrative(paragraph):
            continue
        if to_skip:
            to_skip -= 1
            continue
        if ILLUSTRATION_RE.match(paragraph):
            continue
        collected.append(paragraph)
        total += len(paragraph)
        if total >= TARGET_CHARS and len(collected) >= MIN_PARAGRAPHS:
            break
        if len(collected) >= MAX_PARAGRAPHS:
            break
    return collected


# ----------------------------------------------------------------------------
# Project Gutenberg
# ----------------------------------------------------------------------------


def gutenberg_paragraphs(text):
    text = text.replace("\r\n", "\n")
    start = re.search(
        r"\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", text
    )
    end = re.search(r"\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG EBOOK", text)
    body = text[start.end() if start else 0 : end.start() if end else len(text)]
    paragraphs = []
    for block in re.split(r"\n\s*\n", body):
        block = normalize_space(block)
        if not block:
            continue
        block = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", block)  # _italics_ markers
        if is_heading(block, "en"):
            block = "## " + block
        paragraphs.append(block)
    return paragraphs


def clean_author(name):
    """'Austen, Jane' -> 'Jane Austen'; drops parenthetical expansions."""
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        first = re.sub(r"\b(graf|comte|baron|Sir|Mrs\.?)\b", "", first).strip()
        name = f"{first} {last}".strip()
    return name


def clean_title(title):
    title = normalize_space(title)
    title = re.split(
        r"\s*[;:]\s*(or|a novel|an autobiography|being)\b|\s*:\s|\s*[—-]\s*complete\b|,\s*complete\b",
        title,
        maxsplit=1,
        flags=re.I,
    )[0]
    return title.strip(" ,.;:")


def gutenberg_catalog(fetcher, limit, exclude_ids, include_ids):
    books = []
    seen = set()
    page = 1
    while len(books) < limit and page <= 20:
        payload = json.loads(fetcher.get(GUTENDEX_URL.format(page=page)))
        for entry in payload["results"]:
            gid = entry["id"]
            if gid in exclude_ids:
                continue
            if not any(k.startswith("text/plain") for k in entry["formats"]):
                continue
            title = clean_title(entry["title"])
            if JUNK_TITLE_RE.search(entry["title"]):
                continue
            shelves = " | ".join(entry.get("bookshelves", []))
            if gid not in include_ids and not CLASSIC_SHELF_RE.search(shelves):
                continue
            authors = [clean_author(a["name"]) for a in entry.get("authors", [])]
            if not authors:
                continue
            key = (re.sub(r"\W+", "", title.lower()), authors[0].lower())
            if key in seen:
                continue
            seen.add(key)
            books.append(
                {
                    "id": f"g{gid}",
                    "gutenberg_id": gid,
                    "title": title,
                    "author": ", ".join(authors[:2]),
                    "lang": "en",
                    "translation": bool(entry.get("translators")),
                    "translators": [
                        clean_author(t["name"]) for t in entry.get("translators", [])
                    ],
                    "source": "Project Gutenberg",
                    "source_url": GUTENBERG_PAGE_URL.format(id=gid),
                    "downloads": entry.get("download_count", 0),
                }
            )
            if len(books) >= limit:
                break
        if not payload.get("next"):
            break
        page += 1
    return books


def gutenberg_text(fetcher, gid):
    try:
        raw = fetcher.get(GUTENBERG_TXT_URL.format(id=gid))
    except RuntimeError:
        raw = fetcher.get(f"https://www.gutenberg.org/ebooks/{gid}.txt.utf-8")
    return raw.decode("utf-8", "replace")


# ----------------------------------------------------------------------------
# lib.ru / az.lib.ru
# ----------------------------------------------------------------------------


def _blocks_from_html(fragment, heading_tag):
    """Turn a lib.ru HTML fragment into paragraphs, marking headings with '## '."""
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    fragment = re.sub(r"<script.*?</script>", " ", fragment, flags=re.S | re.I)
    # epigraphs and right-aligned notes
    fragment = re.sub(
        r'<div align="?right"?>.*?</div>', " ", fragment, flags=re.S | re.I
    )
    fragment = re.sub(
        rf"<{heading_tag}[^>]*>(.*?)</{heading_tag}>",
        lambda m: "\n\n## " + normalize_space(strip_tags(m.group(1))) + "\n\n",
        fragment,
        flags=re.S | re.I,
    )
    fragment = re.sub(
        r"<(dd|p|br|li|ul|/ul|div|/div|h\d|/h\d|pre|/pre|tr)\b[^>]*>",
        "\n\n",
        fragment,
        flags=re.I,
    )
    text = strip_tags(fragment)
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        block = normalize_space(block)
        if not block or set(block) <= set("-=_* "):
            continue
        if block.startswith("##"):
            paragraphs.append("## " + block.lstrip("# ").strip())
        else:
            paragraphs.append(block)
    return paragraphs


def az_lib_paragraphs(raw):
    text = decode_cyrillic(raw, "cp1251")
    start = text.find("<!--Section Begins-->")
    if start < 0:
        start = text.find("Собственно произведение")
    end = text.find("<!--Section Ends-->")
    body = text[max(start, 0) : end if end > 0 else len(text)]
    paragraphs = []
    for block in _blocks_from_html(body, "h4"):
        if not block.startswith("## ") and is_heading(block, "ru"):
            block = "## " + block
        paragraphs.append(block)
    return paragraphs


def lib_ru_paragraphs(raw):
    text = decode_cyrillic(raw, "koi8-r")
    # The text sits between the second <pre> and the footer <pre><hr ...>
    pres = [m.start() for m in re.finditer(r"<pre>", text, flags=re.I)]
    start = pres[1] if len(pres) > 1 else (pres[0] if pres else 0)
    footer = re.search(r"<pre>\s*<hr", text[start + 5 :], flags=re.I)
    end = start + 5 + footer.start() if footer else len(text)
    body = text[start:end]
    body = re.sub(
        r"<h2>(.*?)</h2>",
        lambda m: "\n\n@@H@@" + strip_tags(m.group(1)) + "\n\n",
        body,
        flags=re.S | re.I,
    )
    body = strip_tags(body)

    paragraphs = []
    current = []

    def flush():
        if current:
            block = normalize_space(" ".join(current))
            current.clear()
            if block and not set(block) <= set("-=_* "):
                if block.startswith("@@H@@"):
                    paragraphs.append("## " + block[5:].strip())
                else:
                    paragraphs.append(block)

    for line in body.split("\n"):
        if not line.strip():
            flush()
            continue
        if re.match(r"\s{2,}\S", line) or line.lstrip().startswith("@@H@@"):
            flush()
        current.append(line.strip())
    flush()
    return [
        ("## " + p if not p.startswith("## ") and is_heading(p, "ru") else p)
        for p in paragraphs
    ]


def russian_catalog(path, limit=None):
    entries = json.loads(path.read_text(encoding="utf-8"))
    books = []
    for entry in entries:
        book = {
            "id": entry["id"],
            "title": entry["title"],
            "author": entry["author"],
            "lang": "ru",
            "translation": bool(entry.get("translation", False)),
            "source": (
                "Lib.ru" if "lib.ru" in entry["url"] else entry.get("source", "lib.ru")
            ),
            "source_url": entry["url"],
        }
        if entry.get("original_title"):
            book["original_title"] = entry["original_title"]
        if entry.get("year"):
            book["year"] = entry["year"]
        books.append(book)
    return books[:limit] if limit else books


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def build(args):
    fetcher = Fetcher(refresh=args.refresh)
    overrides = json.loads((TOOLS_DIR / "overrides.json").read_text(encoding="utf-8"))
    exclude_ids = set(
        json.loads((TOOLS_DIR / "exclude_en.json").read_text(encoding="utf-8"))
    )
    include_ids = set(
        json.loads((TOOLS_DIR / "include_en.json").read_text(encoding="utf-8"))
    )

    catalog = []
    if args.limit_en:
        print(
            f"Collecting {args.limit_en} English books from Gutendex ...",
            file=sys.stderr,
        )
        catalog += gutenberg_catalog(fetcher, args.limit_en, exclude_ids, include_ids)
    if args.limit_ru != 0:
        catalog += russian_catalog(TOOLS_DIR / "catalog_ru.json", args.limit_ru)
    if args.only:
        catalog = [b for b in catalog if b["id"] in set(args.only)]

    index = []
    report = [
        "| id | lang | tr | title | paragraphs | chars | opening |",
        "|---|---|---|---|---|---|---|",
    ]
    failures = []
    for book in catalog:
        try:
            if book["lang"] == "en":
                paragraphs = gutenberg_paragraphs(
                    gutenberg_text(fetcher, book["gutenberg_id"])
                )
            elif "az.lib.ru" in book["source_url"]:
                paragraphs = az_lib_paragraphs(fetcher.get(book["source_url"]))
            else:
                paragraphs = lib_ru_paragraphs(fetcher.get(book["source_url"]))
            opening = pick_opening(paragraphs, book["lang"], overrides.get(book["id"]))
            opening = [typographic(p, book["lang"]) for p in opening]
            if len(opening) < 1:
                raise RuntimeError("no opening paragraphs found")
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures.append((book["id"], str(exc)))
            print(f"  FAIL {book['id']}: {exc}", file=sys.stderr)
            continue

        record = {
            k: v for k, v in book.items() if k not in ("downloads", "gutenberg_id")
        }
        record["paragraphs"] = opening
        write_json(BOOKS_DIR / f"{book['id']}.json", record)

        entry = {
            "id": book["id"],
            "title": book["title"],
            "author": book["author"],
            "lang": book["lang"],
            "translation": book["translation"],
        }
        index.append(entry)
        chars = sum(len(p) for p in opening)
        preview = opening[0][:110].replace("|", "¦")
        report.append(
            f"| {book['id']} | {book['lang']} | {'yes' if book['translation'] else ''} | "
            f"{book['title'].replace('|', '¦')} | {len(opening)} | {chars} | {preview}… |"
        )
        print(
            f"  ok   {book['id']}: {book['title']} ({len(opening)} paragraphs, {chars} chars)",
            file=sys.stderr,
        )

    if not args.only:
        # Remove stale book files that are no longer in the catalog.
        keep = {f"{b['id']}.json" for b in index}
        for stale in BOOKS_DIR.glob("*.json"):
            if stale.name not in keep:
                stale.unlink()
        write_json(
            DATA_DIR / "index.json",
            {"generated": time.strftime("%Y-%m-%d"), "books": index},
        )
    else:
        # Merge into the existing index.
        index_path = DATA_DIR / "index.json"
        existing = (
            json.loads(index_path.read_text(encoding="utf-8"))
            if index_path.exists()
            else {"books": []}
        )
        merged = {b["id"]: b for b in existing["books"]}
        for entry in index:
            merged[entry["id"]] = entry
        write_json(
            index_path,
            {"generated": time.strftime("%Y-%m-%d"), "books": list(merged.values())},
        )

    (TOOLS_DIR / "report.md").write_text(
        "# Build report\n\nFirst paragraph of every book, for eyeballing bad starts.\n\n"
        + "\n".join(report)
        + (
            "\n\n## Failures\n\n" + "\n".join(f"- {i}: {e}" for i, e in failures)
            if failures
            else ""
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"\nWrote {len(index)} books, {len(failures)} failures. See tools/report.md",
        file=sys.stderr,
    )
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--limit-en", type=int, default=50, help="number of English books (0 = none)"
    )
    parser.add_argument(
        "--limit-ru",
        type=int,
        default=None,
        help="number of Russian books (default: whole catalog)",
    )
    parser.add_argument(
        "--only", action="append", help="build only these ids (repeatable)"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="ignore cached downloads"
    )
    sys.exit(build(parser.parse_args()))


if __name__ == "__main__":
    main()
