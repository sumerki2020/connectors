# Book Bite

A small, dependency-free web app for "biting" books: it shows the opening of a
randomly chosen classic, you guess what it is, reveal the title, or move on.

- ~50 English books from the top of Project Gutenberg's fiction download ranking
- ~50 Russian books from Lib.ru / Lib.ru/Классика (hand-curated public-domain classics)
- translations are included on both sides and can be hidden with **Originals only**
- reveal the text paragraph by paragraph (default), sentence by sentence, or ~1 KB at a time
- mobile first, light/dark theme, installable to the home screen, keyboard shortcuts on desktop
  (`space` more, `enter` reveal, `n` next)

Everything is static: `index.html`, `app.js`, `style.css` and the pre-built
`data/` folder. There is no build step for the app itself and nothing is
fetched from third parties at runtime.

## Run it

Browsers block `fetch()` from `file://`, so serve the folder:

```sh
cd book-bite
python3 -m http.server 8000
# open http://localhost:8000
```

Or copy the folder to any static host (GitHub Pages, Netlify, nginx, S3). All
paths are relative, so it works from a sub-directory too.

## Data

`data/index.json` is the catalog (id, title, author, language, translation
flag). `data/books/<id>.json` holds one book: the metadata plus its opening
paragraphs (~3 KB of text). The app loads the index once and one book file per
bite.

### Rebuilding the data

```sh
python3 tools/build_data.py                 # 50 EN + the whole Russian catalog
python3 tools/build_data.py --limit-en 10 --limit-ru 5
python3 tools/build_data.py --only g1342    # one book, merged into the index
python3 tools/build_data.py --refresh       # ignore cached downloads
```

The script uses only the standard library. Raw downloads are cached in
`tools/.cache/` (git-ignored). After every run `tools/report.md` lists the
first paragraph of each book so bad starts are easy to spot.

- `tools/catalog_ru.json` — the hand-written Russian list (title, author, url,
  `translation`, `original_title`, `year`). Add or remove entries here.
- `tools/exclude_en.json` — Gutenberg ids to skip (anthologies, duplicates, non-narrative).
- `tools/overrides.json` — per-book fixes for the opening detection:
  `{"<id>": {"start_after": "<regex>"}}` starts the text after the last paragraph
  matching the regex, `{"<id>": {"skip": 2}}` drops the first N prose paragraphs
  (epigraphs, dedications).

How the opening is found: the text is split into paragraphs, the first real
chapter heading ("Chapter I", "Letter 1", "ГЛАВА 1", a bare "I") is located
while skipping table-of-contents runs, then title lines, illustrations and
front matter are skipped until the first prose paragraph. Paragraphs are
collected until about 3 000 characters.

## Sources and licensing

- English texts: [Project Gutenberg](https://www.gutenberg.org/), public domain in the USA.
  Metadata comes from the [Gutendex](https://gutendex.com/) API, including the
  translators list that drives the translation flag.
- Russian texts: [Lib.ru](http://lib.ru/) and [Lib.ru/Классика](http://az.lib.ru/).
  The curated list sticks to authors who died before 1950, so the originals are
  public domain in Russia. Translations of foreign classics come from the same
  library; only a short opening excerpt is stored and every book links back to
  its source page.
