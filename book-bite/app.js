/* Book Bite — guess the book from its opening lines. No dependencies. */
(function () {
  "use strict";

  const STORAGE_KEY = "book-bite:v1";
  const CHUNK_CHARS = 1024;

  const $ = (id) => document.getElementById(id);
  const el = {
    card: $("card"),
    meta: $("meta"),
    text: $("text"),
    status: $("status"),
    reveal: $("reveal"),
    title: $("book-title"),
    author: $("book-author"),
    link: $("book-link"),
    more: $("more"),
    revealBtn: $("reveal-btn"),
    next: $("next"),
    langEn: $("lang-en"),
    langRu: $("lang-ru"),
    originalsOnly: $("originals-only"),
    settingsBtn: $("settings-btn"),
    settings: $("settings"),
    count: $("count"),
    resetSeen: $("reset-seen"),
  };

  // ---------------------------------------------------------------- state --
  const defaults = {
    langs: { en: true, ru: true },
    originalsOnly: false,
    mode: "paragraph",
    size: "m",
    seen: [],
    current: null, // { id, shown, revealed }
  };

  let state = load();
  let index = [];
  let book = null; // currently loaded book JSON
  let units = []; // reveal units for the current mode

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return structuredClone(defaults);
      const saved = JSON.parse(raw);
      return Object.assign(structuredClone(defaults), saved, {
        langs: Object.assign({}, defaults.langs, saved.langs || {}),
      });
    } catch (_) {
      return structuredClone(defaults);
    }
  }

  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (_) {
      /* private mode or storage blocked: the app still works for this visit */
    }
  }

  // ---------------------------------------------------------- text units --
  const ABBREV = /\b(Mr|Mrs|Ms|Dr|St|Mme|Mlle|Prof|Sr|Jr|Capt|Col|Gen|Lieut|Esq|vs|т|е|г|гг|ул|им|св|ст|проф|тов|стр|напр|см|кн|д)\.$/i;

  function splitSentences(text) {
    const out = [];
    const re = /([.!?…]+["”»')\]]*)(\s+)(?=["“«(\[]?[A-ZА-ЯЁ0-9—–-])/g;
    let start = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      const end = m.index + m[1].length;
      const piece = text.slice(start, end);
      if (ABBREV.test(piece)) continue;
      out.push(piece);
      start = m.index + m[0].length;
    }
    if (start < text.length) out.push(text.slice(start));
    return out.filter((s) => s.trim());
  }

  // Each unit: { paragraphs: [[sentence, ...], ...] } appended to the view.
  function buildUnits(paragraphs, mode) {
    if (mode === "sentence") {
      const result = [];
      paragraphs.forEach((p, pi) => {
        splitSentences(p).forEach((s, si) => result.push({ p: pi, s: si, text: s }));
      });
      return result;
    }
    if (mode === "chunk") {
      const result = [];
      let acc = [];
      let size = 0;
      paragraphs.forEach((p, pi) => {
        acc.push({ p: pi, text: p });
        size += p.length;
        if (size >= CHUNK_CHARS) {
          result.push(acc);
          acc = [];
          size = 0;
        }
      });
      if (acc.length) result.push(acc);
      return result;
    }
    return paragraphs.map((p, pi) => [{ p: pi, text: p }]);
  }

  function unitPieces(unit) {
    return Array.isArray(unit) ? unit : [unit];
  }

  // ------------------------------------------------------------ rendering --
  function paragraphNode(pi) {
    let node = el.text.querySelector(`p[data-p="${pi}"]`);
    if (!node) {
      node = document.createElement("p");
      node.dataset.p = String(pi);
      el.text.appendChild(node);
    }
    return node;
  }

  function renderUnit(unit, animate) {
    unitPieces(unit).forEach((piece) => {
      const p = paragraphNode(piece.p);
      if (state.mode === "sentence") {
        const span = document.createElement("span");
        span.className = "sentence" + (animate ? " fresh" : "");
        span.textContent = (p.childNodes.length ? " " : "") + piece.text;
        p.appendChild(span);
      } else {
        p.textContent = piece.text;
        if (animate) p.classList.add("fresh");
      }
    });
  }

  function renderShown() {
    el.text.innerHTML = "";
    const shown = Math.min(state.current.shown, units.length);
    for (let i = 0; i < shown; i++) renderUnit(units[i], false);
    updateControls();
  }

  function updateControls() {
    const done = state.current.shown >= units.length;
    el.more.disabled = done;
    el.status.hidden = !done;
    el.status.textContent = done ? "That is all we keep of this book. Reveal it, or move on." : "";
    el.reveal.hidden = !state.current.revealed;
    el.revealBtn.disabled = state.current.revealed;
    el.meta.textContent = book
      ? `${book.lang === "ru" ? "Russian" : "English"}${book.translation ? " · translation" : ""} · ${describeProgress()}`
      : "";
  }

  function describeProgress() {
    const total = units.length;
    const shown = Math.min(state.current.shown, total);
    const noun = state.mode === "sentence" ? "sentence" : state.mode === "chunk" ? "chunk" : "paragraph";
    return `${noun} ${shown} of ${total}`;
  }

  function showReveal() {
    el.title.textContent = book.title;
    if (book.translation) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "translation";
      el.title.appendChild(badge);
    }
    let author = book.author || "";
    if (book.year) author += ` · ${book.year}`;
    if (book.original_title) author += ` · ${book.original_title}`;
    el.author.textContent = author;
    el.link.href = book.source_url;
    el.link.textContent = `Read at ${book.source || "source"}`;
    el.reveal.hidden = false;
    el.reveal.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function setStatus(message) {
    el.status.hidden = !message;
    el.status.textContent = message || "";
  }

  // --------------------------------------------------------------- picking --
  function pool() {
    return index.filter(
      (b) => state.langs[b.lang] && (!state.originalsOnly || !b.translation)
    );
  }

  function pickRandom() {
    const candidates = pool();
    if (!candidates.length) return null;
    const seen = new Set(state.seen);
    let fresh = candidates.filter((b) => !seen.has(b.id) && !(state.current && b.id === state.current.id));
    if (!fresh.length) {
      // Everything in this pool has been seen: start over, but avoid an immediate repeat.
      state.seen = state.seen.filter((id) => !candidates.some((b) => b.id === id));
      fresh = candidates.filter((b) => !(state.current && b.id === state.current.id));
      if (!fresh.length) fresh = candidates;
    }
    return fresh[Math.floor(Math.random() * fresh.length)];
  }

  async function loadBook(id) {
    const res = await fetch(`data/books/${id}.json`, { cache: "force-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function showBook(entry, restore) {
    el.text.innerHTML = "";
    el.reveal.hidden = true;
    el.title.textContent = "";
    setStatus("Loading…");
    try {
      book = await loadBook(entry.id);
    } catch (err) {
      setStatus(`Could not load this book (${err.message}). Try the next one.`);
      return;
    }
    el.text.lang = book.lang;
    document.documentElement.lang = book.lang;
    units = buildUnits(book.paragraphs, state.mode);
    if (!restore) {
      state.current = { id: book.id, shown: 1, revealed: false };
      if (!state.seen.includes(book.id)) state.seen.push(book.id);
    } else {
      state.current.shown = Math.max(1, Math.min(state.current.shown, units.length));
    }
    save();
    renderShown();
    if (state.current.revealed) showReveal();
    el.card.scrollTop = 0;
  }

  function nextBook() {
    const entry = pickRandom();
    if (!entry) {
      book = null;
      units = [];
      el.text.innerHTML = "";
      el.reveal.hidden = true;
      el.meta.textContent = "";
      setStatus("No books match these filters. Turn a language back on.");
      el.more.disabled = true;
      el.revealBtn.disabled = true;
      return;
    }
    showBook(entry, false);
  }

  function more() {
    if (!book || state.current.shown >= units.length) return;
    renderUnit(units[state.current.shown], true);
    state.current.shown += 1;
    save();
    updateControls();
    const last = el.text.lastElementChild;
    if (last) last.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function reveal() {
    if (!book || state.current.revealed) return;
    state.current.revealed = true;
    save();
    updateControls();
    showReveal();
  }

  // ---------------------------------------------------------- settings UI --
  function applySettingsToUI() {
    el.langEn.checked = state.langs.en;
    el.langRu.checked = state.langs.ru;
    el.originalsOnly.checked = state.originalsOnly;
    document.body.dataset.size = state.size;
    el.settings.querySelectorAll('input[name="mode"]').forEach((r) => (r.checked = r.value === state.mode));
    el.settings.querySelectorAll('input[name="size"]').forEach((r) => (r.checked = r.value === state.size));
    updateCount();
  }

  function updateCount() {
    const all = index.length;
    const en = index.filter((b) => b.lang === "en").length;
    const ru = all - en;
    const tr = index.filter((b) => b.translation).length;
    const seen = state.seen.filter((id) => index.some((b) => b.id === id)).length;
    el.count.textContent = `${all} books: ${en} English, ${ru} Russian (${tr} translations). Seen ${seen}.`;
  }

  function onFilterChange() {
    state.langs.en = el.langEn.checked;
    state.langs.ru = el.langRu.checked;
    state.originalsOnly = el.originalsOnly.checked;
    save();
    const stillFits = book && state.langs[book.lang] && (!state.originalsOnly || !book.translation);
    if (!stillFits) nextBook();
  }

  function onModeChange(mode) {
    if (mode === state.mode) return;
    const previousPieces = units.slice(0, state.current ? state.current.shown : 0).flatMap(unitPieces);
    state.mode = mode;
    if (book) {
      units = buildUnits(book.paragraphs, mode);
      // keep roughly the same amount of text visible
      const lastParagraph = previousPieces.length ? Math.max(...previousPieces.map((x) => x.p)) : 0;
      let shown = 0;
      for (let i = 0; i < units.length; i++) {
        shown = i + 1;
        if (unitPieces(units[i]).some((x) => x.p >= lastParagraph)) break;
      }
      state.current.shown = Math.max(1, shown);
      renderShown();
    }
    save();
  }

  // ---------------------------------------------------------------- wiring --
  el.more.addEventListener("click", more);
  el.revealBtn.addEventListener("click", reveal);
  el.next.addEventListener("click", nextBook);
  [el.langEn, el.langRu, el.originalsOnly].forEach((c) => c.addEventListener("change", onFilterChange));
  el.settingsBtn.addEventListener("click", () => {
    updateCount();
    el.settings.showModal();
  });
  el.settings.addEventListener("change", (e) => {
    if (e.target.name === "mode") onModeChange(e.target.value);
    if (e.target.name === "size") {
      state.size = e.target.value;
      document.body.dataset.size = state.size;
      save();
    }
  });
  el.resetSeen.addEventListener("click", () => {
    state.seen = state.current ? [state.current.id] : [];
    save();
    updateCount();
  });
  el.settings.addEventListener("click", (e) => {
    if (e.target === el.settings) el.settings.close();
  });

  document.addEventListener("keydown", (e) => {
    if (el.settings.open || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.code === "Space") {
      e.preventDefault();
      more();
    } else if (e.key === "Enter") {
      e.preventDefault();
      reveal();
    } else if (e.key === "n" || e.key === "N" || e.key === "ArrowRight") {
      e.preventDefault();
      nextBook();
    }
  });

  // Tapping the text itself also reveals more, which is handy on a phone.
  el.text.addEventListener("click", (e) => {
    if (window.getSelection && String(window.getSelection())) return;
    if (e.target.closest("a")) return;
    more();
  });

  async function init() {
    applySettingsToUI();
    setStatus("Loading library…");
    try {
      const res = await fetch("data/index.json", { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      index = (await res.json()).books;
    } catch (err) {
      setStatus(
        "Could not load the library. If you opened index.html directly, serve the folder instead " +
          "(for example: python3 -m http.server). " +
          err.message
      );
      el.more.disabled = true;
      el.revealBtn.disabled = true;
      return;
    }
    updateCount();
    const current = state.current && index.find((b) => b.id === state.current.id);
    if (current) showBook(current, true);
    else nextBook();
  }

  init();
})();
