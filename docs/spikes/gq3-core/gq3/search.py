"""Section 4 of the RFC, reduced: the analyzer, `terms(...)`, membership,
`bm25_v1` over the field corpus, and the three vector distances.

The analyzer is reduced to NFC, lowercase and simple tokenization, as the
brief allows; no stemming or stop words.
"""
from __future__ import annotations

import math
import re
import struct
import unicodedata
from dataclasses import dataclass

from .model import GQError

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def analyze(text):
    if text is None:
        return []
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


@dataclass(frozen=True)
class TermsValue:
    terms: tuple          # distinct analyzed query terms, ascending UTF-8
    mode: str             # "all" | "any"
    max_edits: int


def make_terms(text, mode="all", max_edits=0) -> TermsValue:
    if text is None:
        raise GQError("T_TERMS_NULL", "terms(...) text must be a non-null String")
    if mode not in ("all", "any"):
        raise GQError("T_OPTION", f"mode must be all or any, not {mode!r}")
    if not isinstance(max_edits, int) or isinstance(max_edits, bool) or not 0 <= max_edits <= 2:
        raise GQError("T_OPTION_RANGE", f"max_edits must be in 0..=2, not {max_edits!r}")
    toks = sorted(set(analyze(text)), key=lambda s: s.encode("utf-8"))
    if not toks:
        raise GQError("T_TERMS_EMPTY", "a query with no searchable terms is a type error")
    return TermsValue(tuple(toks), mode, max_edits)


def edit_distance(a: str, b: str, cap: int) -> int:
    """Levenshtein over Unicode scalar values; a transposition costs two."""
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


def matches(doc_tokens, q: TermsValue) -> bool:
    if not doc_tokens:
        return False
    vocab = set(doc_tokens)
    hits = [any(edit_distance(qt, t, q.max_edits) <= q.max_edits for t in vocab) for qt in q.terms]
    return all(hits) if q.mode == "all" else any(hits)


class FieldCorpus:
    """The snapshot-visible field corpus: distinct entities owning the
    property with non-empty analyzed values, before any graph filter."""

    def __init__(self, docs: dict):
        self.docs = {k: v for k, v in docs.items() if v}
        self.N = len(self.docs)
        self.avg_len = (sum(len(v) for v in self.docs.values()) / self.N) if self.N else 0.0

    def df(self, qt: str, edits: int) -> int:
        return sum(1 for toks in self.docs.values()
                   if any(edit_distance(qt, t, edits) <= edits for t in set(toks)))


def bm25_v1(corpus: FieldCorpus, doc_tokens, q: TermsValue) -> float:
    if not matches(doc_tokens, q):
        raise AssertionError("bm25_v1 called on a non-member")
    len_d = len(doc_tokens)
    norm = 1.2 * (0.25 + 0.75 * len_d / corpus.avg_len)
    tf = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for qt in q.terms:  # ascending UTF-8 order, as the numeric profile fixes
        df = corpus.df(qt, q.max_edits)
        if df == 0:
            continue
        idf = math.log1p((corpus.N - df + 0.5) / (df + 0.5))
        best = 0.0
        for t, n in tf.items():
            e = edit_distance(qt, t, q.max_edits)
            if e <= q.max_edits:
                best = max(best, 2.0 ** (-e) * (2.2 * n / (n + norm)))
        score += idf * best
    if not (math.isfinite(score) and score > 0):
        raise GQError("E_NUMERIC", f"bm25_v1 produced {score}")
    return score


def to_f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def vector_distance(kind: str, v, q) -> float:
    if v is None:
        return None
    if len(v) != len(q):
        raise GQError("T_DIM", f"vector of dimension {len(v)} against a query of {len(q)}")
    if not all(math.isfinite(x) for x in v) or not all(math.isfinite(x) for x in q):
        raise GQError("E_NONFINITE", "a non-finite vector component")
    if kind == "l2":
        d = sum((a - b) ** 2 for a, b in zip(v, q))
    elif kind == "dot":
        d = 1.0 - sum(a * b for a, b in zip(v, q))
    elif kind == "cosine":
        nv = math.sqrt(sum(a * a for a in v))
        nq = math.sqrt(sum(b * b for b in q))
        if nv == 0 or nq == 0:
            raise GQError("E_NONFINITE", "cosine distance of a zero vector")
        d = 1.0 - sum(a * b for a, b in zip(v, q)) / (nv * nq)
    else:
        raise GQError("T_OPTION", f"unknown distance {kind}")
    return to_f32(d)
