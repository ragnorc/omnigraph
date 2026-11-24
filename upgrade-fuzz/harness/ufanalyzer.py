"""Reference analyzers and lexical semantics, written from the documents.

* `e2`: edition 2's index analysis, Lance 13's default
  (`lance-index` `InvertedIndexParams::build`): the simple tokenizer, a
  filter dropping tokens of 40 bytes or more, lowercase, the Snowball
  English stemmer, the NLTK English stop list (`stop-words` 0.10, the list
  `lance-tokenizer` takes for English), ASCII folding; no NFC.
* `standard_v1`, `standard_folded_v1`, `english_v1`: the profiles of
  `docs/user/search/analyzers.md`, with `english_v1`'s stop list copied from
  `crates/omnigraph-compiler/src/schema/analysis.rs` at NEW.

ASCII folding is approximated (NFKD without combining marks, plus the
ligatures and letters NFKD leaves, accepted only when the result is ASCII),
so a mismatch on a folded profile is reported as the reference's, to be read
by hand, never as NEW's defect on its own.

`match_terms` and `bm25_v1` follow the formulas of `analyzers.md`.
"""

from __future__ import annotations

import math
import unicodedata
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
E2_STOP = frozenset(w.strip() for w in (HERE / "lance_en_stop.txt").read_text().splitlines() if w.strip())
V1_STOP = frozenset(w.strip() for w in (HERE / "english_v1_stop.txt").read_text().splitlines() if w.strip())
FOLD_EXTRA = {"ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "ł": "l", "đ": "d", "þ": "th", "ð": "d", "ı": "i",
              "ﬁ": "fi", "ﬂ": "fl", "ĳ": "ij", "ŋ": "n", "ħ": "h", "ŧ": "t", "ſ": "s"}


@lru_cache(maxsize=1)
def _stemmer():
    import snowballstemmer
    return snowballstemmer.stemmer("english")


@lru_cache(maxsize=200000)
def stem(w: str) -> str:
    return _stemmer().stemWord(w)


def tokens(text: str) -> list[str]:
    out, cur = [], []
    for ch in text:
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


@lru_cache(maxsize=200000)
def fold(tok: str) -> str:
    out = []
    for ch in tok:
        if ord(ch) < 128:
            out.append(ch)
            continue
        if ch in FOLD_EXTRA:
            out.append(FOLD_EXTRA[ch])
            continue
        d = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(d if d and all(ord(c) < 128 for c in d) else ch)
    return "".join(out)


def analyze(text: str | None, profile: str) -> list[str]:
    if not text:
        return []
    if profile != "e2":
        text = unicodedata.normalize("NFC", text)
    out = []
    for t in tokens(text):
        if profile == "e2" and len(t.encode()) >= 40:
            continue
        t = t.lower()
        if profile in ("e2", "english_v1"):
            t = stem(t)
            if t in (E2_STOP if profile == "e2" else V1_STOP):
                continue
        if profile in ("e2", "english_v1", "standard_folded_v1"):
            t = fold(t)
        out.append(t)
    return out


def lev(a: str, b: str, cap: int) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def matches(doc_terms: set, qterms: list, mode: str, edits: int) -> bool:
    if not qterms or not doc_terms:
        return False
    def hit(q):
        if q in doc_terms:
            return True
        return edits > 0 and any(lev(q, t, edits) <= edits for t in doc_terms)
    return any(hit(q) for q in qterms) if mode == "any" else all(hit(q) for q in qterms)


def bm25(corpus: dict, qterms: list, edits: int = 0) -> dict:
    """bm25_v1 over a field corpus {id: [terms]}: {id: score} for matching ids."""
    docs = {k: v for k, v in corpus.items() if v}
    n = len(docs)
    if not n:
        return {}
    avg = sum(len(v) for v in docs.values()) / n
    qs = sorted(set(qterms), key=lambda s: s.encode())
    tfs = {k: {} for k in docs}
    for k, v in docs.items():
        d = tfs[k]
        for t in v:
            d[t] = d.get(t, 0) + 1
    out = {}
    df = {}
    for q in qs:
        df[q] = sum(1 for k in docs if any(lev(q, t, edits) <= edits for t in tfs[k]) if edits or q in tfs[k])
    for k, v in docs.items():
        norm = 1.2 * (0.25 + 0.75 * len(v) / avg)
        score = 0.0
        hit = False
        for q in qs:
            best = 0.0
            for t, tf in tfs[k].items():
                e = 0 if t == q else (lev(q, t, edits) if edits else edits + 1)
                if e <= edits:
                    w = 2.2 * tf / (tf + norm)
                    best = max(best, (2.0 ** -e) * w)
            if best > 0:
                hit = True
                idf = math.log1p((n - df[q] + 0.5) / (df[q] + 0.5))
                score += idf * best
        if hit:
            out[k] = score
    return out


# ---------------------------------------------------------------- explain

def _profile(cfg, rd) -> str:
    t = cfg.node(rd["type_name"])
    p = t.prop(rd["prop"]) if t else None
    return (p.analyzer if p is not None and p.analyzer else "english_v1")


def _key(values) -> tuple:
    import json
    return tuple(json.dumps(v, sort_keys=True, ensure_ascii=False) for v in values)


def corpus_of(cfg, rd, export_rows: list) -> tuple[list, dict]:
    t = cfg.node(rd["type_name"])
    from ufquery import _ident
    ident = _ident(t)
    texts = []
    for row in export_rows:
        if row.get("type") == t.name:
            d = row.get("data") or {}
            texts.append((_key(d.get(c) for c in ident), d.get(rd["prop"])))
    return ident, texts


def reference_rows(rd, profile: str, texts: dict) -> tuple:
    q = (rd.get("params") or {}).get("q")
    kind = rd["kind"]
    mode = "all" if kind == "lex_all" else "any"
    edits = {"lex_all": 1, "fuzzy": 2}.get(kind, 0)
    qt = list(dict.fromkeys(analyze(q, profile)))
    if kind in ("bm25", "bm25_ties"):
        corpus = {i: analyze(v, profile) for i, (k, v) in enumerate(texts)}
        sc = bm25(corpus, qt, 0)
        return "scores", [(texts[i][0], s) for i, s in sc.items()]
    hits = {k for k, v in texts if matches(set(analyze(v, profile)), qt, mode, edits)}
    return "set", hits


def check_new(rd, new: dict, cfg, export_rows: list) -> dict | None:
    """Oracle 2 with an independent reference: NEW's lexical rows (and
    `bm25` scores) are what the field's profile gives."""
    if rd["kind"] not in ("lex_any", "lex_all", "fuzzy", "bm25", "bm25_ties") or new.get("error"):
        return None
    profile = _profile(cfg, rd)
    t = cfg.node(rd["type_name"])
    p = t.prop(rd["prop"]) if t else None
    if p is None or (p.scorer == "none" and rd["kind"].startswith("bm25")):
        return None
    ident, texts = corpus_of(cfg, rd, export_rows)
    cols = [f"x.{c}" for c in ident]
    got = [_key(r.get(c) for c in cols) for r in new.get("rows") or []]
    kind, ref = reference_rows(rd, profile, texts)
    if kind == "set":
        if set(got) != ref:
            return {"profile": profile, "missing": sorted(map(str, ref - set(got)))[:5],
                    "extra": sorted(map(str, set(got) - ref))[:5], "n_ref": len(ref), "n_new": len(got),
                    "texts": {str(k): v for k, v in texts if k in (set(got) ^ ref)}}
        return {}
    # scores: NEW's rows in descending score, the top 10 (bm25) or all (bm25_ties)
    order = sorted(ref, key=lambda kv: -kv[1])
    lim = 10 if rd["kind"] == "bm25" else len(order)
    scores = [r.get("score") for r in new.get("rows") or []]
    want = [s for _, s in order[:lim]]
    by_key = {}
    for k, s in ref:
        by_key.setdefault(k, set()).add(round(s, 9))
    bad = None
    if len(got) != min(lim, len(order)):
        bad = f"row count NEW {len(got)} reference {min(lim, len(order))}"
    else:
        for g, gs in zip(got, scores):
            if gs is None or round(gs, 9) not in by_key.get(g, set()):
                bad = f"score of {g}: NEW {gs}, reference {sorted(by_key.get(g, set()))[:3]}"
                break
        if bad is None and [round(s, 9) for s in scores] != [round(s, 9) for s in want]:
            bad = "ranking"
    if bad:
        return {"profile": profile, "bad": bad, "new_top": list(zip(map(str, got[:5]), scores[:5])),
                "ref_top": [(str(k), s) for k, s in order[:5]]}
    return {}


def explain(rd, old: dict, new: dict, cfg, export_rows: list | None = None) -> dict:
    if export_rows is None:
        return {"explained": False, "why": "no corpus"}
    if rd["kind"] == "rrf":
        return {"explained": False, "why": "rrf: not modelled"}
    profile = _profile(cfg, rd)
    ident, texts = corpus_of(cfg, rd, export_rows)
    cols = [f"x.{c}" for c in ident]
    go = set(_key(r.get(c) for c in cols) for r in old.get("rows") or [])
    gn = set(_key(r.get(c) for c in cols) for r in new.get("rows") or [])
    if rd["kind"] in ("bm25",):
        k_e2, ref_e2 = reference_rows(rd, "e2", texts)
        k_v1, ref_v1 = reference_rows(rd, profile, texts)
        e2_set, v1_set = {k for k, _ in ref_e2}, {k for k, _ in ref_v1}
        if e2_set != v1_set or sorted(round(v, 6) for _, v in ref_e2) != sorted(round(v, 6) for _, v in ref_v1):
            return {"explained": True, "why": f"the corpus analyzes differently under edition 2 and {profile} (matching sets {len(e2_set)} vs {len(v1_set)})"}
        return {"explained": False, "why": "edition 2's and the profile's reference agree; the difference is elsewhere"}
    _, ref_e2 = reference_rows(rd, "e2", texts)
    _, ref_v1 = reference_rows(rd, profile, texts)
    if gn != ref_v1:
        return {"explained": False, "why": f"NEW's rows are not the {profile} reference's"}
    if ref_e2 == ref_v1:
        return {"explained": False, "why": "edition 2's reference agrees with the profile's; GQ 2 answered otherwise",
                "gq2_extra": sorted(map(str, go - ref_e2))[:4], "gq2_missing": sorted(map(str, ref_e2 - go))[:4]}
    if go == ref_e2:
        return {"explained": True, "why": f"edition 2's analysis and {profile} differ on this corpus, and each side answers its own"}
    if rd["kind"] == "fuzzy":
        return {"explained": False, "why": "fuzzy: GQ 2's expansion is not modelled (Lance keeps 50 expansions; transposition cost)",
                "gq2_extra": sorted(map(str, go - ref_e2))[:4], "gq2_missing": sorted(map(str, ref_e2 - go))[:4]}
    return {"explained": False, "why": "GQ 2's rows are not edition 2's reference",
            "gq2_extra": sorted(map(str, go - ref_e2))[:4], "gq2_missing": sorted(map(str, ref_e2 - go))[:4]}
