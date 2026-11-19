"""Synthetic schemas and data for the migration matrix.

Everything here is deterministic from a seed: the same call writes the same
bytes. The edition-2 schema (`BASE_PG`) carries every schema feature the
matrix names; `EVOLVED_PG` is what `schema apply` turns it into (an added
type, an added property, a renamed property and a dropped one).

The text columns are built from a vocabulary that probes what the GQ 3 RFC
says changes in full-text analysis: letter case, stemming, stop words, NFC
versus NFD, hyphens and apostrophes.
"""

from __future__ import annotations

import base64
import json
import random
import unicodedata
from pathlib import Path

BASE_PG = """\
// migration-matrix base schema (edition 2): every feature the matrix names
node Scalars {
    slug: String @key
    s: String
    s_opt: String?
    b: Bool @index
    b_opt: Bool?
    i32: I32
    i32_opt: I32?
    i64: I64 @index
    i64_opt: I64?
    u32: U32 @unique
    u32_opt: U32?
    u64: U64
    u64_opt: U64?
    f32: F32
    f32_opt: F32?
    f64: F64 @index
    f64_opt: F64?
    d: Date @index
    d_opt: Date?
    dt: DateTime
    dt_opt: DateTime?
    kind: enum(alpha, beta, gamma) @index
    kind_opt: enum(alpha, beta, gamma)?
    tags: [String]
    tags_opt: [String]?
    nums: [I32]?
    @range(i32, 0..1000)
}

node Doc {
    slug: String @key @index
    title: String @index
    body: String @index
    note: String?
    lang: enum(en, de, fr)
    year: I32
    emb: Vector(8) @index
    emb_opt: Vector(8)?
    emb_e: Vector(8)? @embed("title")
}

node Asset {
    slug: String @key
    content: Blob
    ext: Blob?
}

node Pair {
    a: String
    b: I32
    label: String?
    @key(a, b)
}

node Loose {
    name: String
    n: I32
}

node Person {
    email: String @key
    name: String
    age: I32?
}

node Company {
    slug: String @key
    name: String @index
}

edge Knows: Person -> Person {
    since: I32?
}

edge WorksAt: Person -> Company @card(0..1) {
    role: String
    @key(@src, @dst)
}

edge Cites: Doc -> Doc {
    weight: F64?
    @unique(@src, @dst)
}

edge Tagged: Doc -> Loose
"""


def evolved_pg(base: str = BASE_PG) -> str:
    """The schema `schema apply` evolves the base into."""
    out = base.replace("    f32_opt: F32?\n", "")  # drop a property
    out = out.replace("    note: String?\n", "    note: String?\n    extra: String?\n")  # add one
    out = out.replace('    n: I32\n', '    count: I32 @rename_from("n")\n')  # rename one
    out += "\nnode Added {\n    slug: String @key\n    v: I32?\n}\n"  # add a type
    return out


EVOLVED_PG = evolved_pg()

# Words chosen to separate analyzers: case, stems, stop words, NFC/NFD,
# punctuation inside words, and plain filler.
VOCAB = [
    "graph", "Graph", "GRAPH", "graphs", "running", "runs", "ran", "runner",
    "the", "a", "is", "of", "and", "café", unicodedata.normalize("NFD", "café"),
    "naïve", "Straße", "strasse", "data-base", "database", "e-mail", "email",
    "über", "ÜBER", "2026", "v2", "C++", "node.js", "don't", "dont", "colour",
    "color", "searching", "searched", "indexes", "indices", "lance", "vector",
    "omnigraph", "tariff", "Tariffs", "matter", "passage", "river", "stone",
    "quantum", "harbor", "harbour", "zebra", "kiwi", "maple", "ember", "orbit",
]
FILLER = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
          "hotel", "india", "juliet", "kilo", "lima", "mike", "november"]


def words(rng: random.Random, n: int) -> str:
    pool = VOCAB + FILLER
    return " ".join(rng.choice(pool) for _ in range(n))


def vec(rng: random.Random, dim: int = 8) -> list[float]:
    # Rounded to a value an F32 holds exactly, so text round trips are exact.
    return [round(rng.uniform(-1.0, 1.0) * 1024) / 1024 for _ in range(dim)]


def blob(payload: bytes) -> str:
    return "base64:" + base64.b64encode(payload).decode()


class Data:
    """The base rows of one graph, and later batches, all from one seed."""

    def __init__(self, seed: int = 7, scale: int = 1):
        self.rng = random.Random(seed)
        self.scale = scale
        self.n_scalars = 60 * scale
        self.n_doc = 80 * scale
        self.n_person = 40 * scale
        self.n_company = 8
        self.n_loose = 30 * scale
        self.n_pair = 30 * scale
        self.n_asset = 10

    # ---- nodes -------------------------------------------------------
    def scalars(self, start: int, count: int) -> list[dict]:
        rng = self.rng
        rows = []
        for i in range(start, start + count):
            row = {
                "slug": f"s{i:05d}",
                "s": words(rng, 3),
                "b": i % 3 == 0,
                "i32": (i * 37) % 1000,
                "i64": [-(2**63), 2**63 - 1, 9007199254740993, 9007199254740992, -5, 0, 3][i % 7]
                if i < 7 else i * 1000003,
                "u32": 4000000000 - i,
                "u64": [2**64 - 1, 0, 2**53 + 1][i % 3] if i < 3 else i * 7,
                "f32": [0.1, -0.0, 3.4028234663852886e38, 1e-7][i % 4] if i < 4 else i / 8,
                "f64": [0.1, -0.0, 1.7976931348623157e308, 5e-324, 3.0][i % 5] if i < 5 else i / 3,
                "d": ["1970-01-01", "2000-02-29", "2262-04-11", "1900-01-01"][i % 4]
                if i < 4 else f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "dt": ["1970-01-01T00:00:00Z", "2026-11-18T12:34:56.789Z"][i % 2]
                if i < 2 else f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T{i % 24:02d}:00:00Z",
                "kind": ["alpha", "beta", "gamma"][i % 3],
                "tags": [rng.choice(["rust", "Rust", "go", "zig", "c"]) for _ in range(i % 4)],
            }
            if i % 2 == 0:
                row.update({
                    "s_opt": words(rng, 2), "b_opt": i % 4 == 0, "i32_opt": i,
                    "i64_opt": -i, "u32_opt": i, "u64_opt": i, "f32_opt": i / 4,
                    "f64_opt": i / 7, "d_opt": "2001-01-01", "dt_opt": "2001-01-01T01:02:03Z",
                    "kind_opt": ["gamma", "alpha"][i % 2], "tags_opt": ["x"], "nums": [i, -i, 0],
                })
            rows.append({"type": "Scalars", "data": row})
        return rows

    def docs(self, start: int, count: int) -> list[dict]:
        rng = self.rng
        rows = []
        for i in range(start, start + count):
            row = {
                "slug": f"d{i:05d}" if i % 9 else f"Graph-{i:05d}",
                "title": words(rng, rng.randint(2, 6)),
                "body": words(rng, rng.randint(8, 24)),
                "lang": ["en", "de", "fr"][i % 3],
                "year": 1990 + i % 40,
                "emb": vec(rng),
            }
            if i % 3:
                row["note"] = words(rng, 2)
            if i % 2:
                row["emb_opt"] = vec(rng)
            if i % 5 == 0:
                row["emb_e"] = vec(rng)
            rows.append({"type": "Doc", "data": row})
        return rows

    def others(self) -> list[dict]:
        rng = self.rng
        out = []
        for i in range(self.n_asset):
            row = {"slug": f"a{i:03d}", "content": blob(f"payload-{i}".encode() * (i + 1))}
            if i % 2 == 0:
                row["ext"] = blob(b"")  # a valid empty managed value
            out.append({"type": "Asset", "data": row})
        for i in range(self.n_pair):
            row = {"a": ["x", "y", "z"][i % 3], "b": i}
            if i % 2:
                row["label"] = words(rng, 1)
            out.append({"type": "Pair", "data": row})
        for i in range(self.n_loose):
            out.append({"type": "Loose", "data": {"name": f"loose{i % 10}", "n": i}})
        for i in range(self.n_person):
            row = {"email": f"p{i:03d}@example.com", "name": words(rng, 2)}
            if i % 4:
                row["age"] = 18 + i
            out.append({"type": "Person", "data": row})
        for i in range(self.n_company):
            out.append({"type": "Company", "data": {"slug": f"c{i}", "name": words(rng, 2)}})
        return out

    # ---- edges -------------------------------------------------------
    def edges(self) -> list[dict]:
        rng = self.rng
        out = []
        p = self.n_person
        for i in range(p * 2):
            a, b = i % p, (i * 7 + 3) % p
            row = {"edge": "Knows", "from": f"p{a:03d}@example.com", "to": f"p{b:03d}@example.com", "data": {}}
            if i % 2:
                row["data"]["since"] = 2000 + i
            out.append(row)
        # parallel edges: the same pair twice, and a self-loop
        out.append({"edge": "Knows", "from": "p000@example.com", "to": "p003@example.com", "data": {"since": 1}})
        out.append({"edge": "Knows", "from": "p001@example.com", "to": "p001@example.com", "data": {}})
        for i in range(0, p, 2):
            out.append({"edge": "WorksAt", "from": f"p{i:03d}@example.com", "to": f"c{i % self.n_company}",
                        "data": {"role": rng.choice(["eng", "pm", "ceo"])}})
        doc_ids = [d["data"]["slug"] for d in self.docs_cache]
        seen = set()
        for i in range(self.n_doc):
            a, b = doc_ids[i], doc_ids[(i * 5 + 1) % len(doc_ids)]
            if (a, b) in seen:
                continue
            seen.add((a, b))
            row = {"edge": "Cites", "from": a, "to": b, "data": {}}
            if i % 2:
                row["data"]["weight"] = i / 10
            out.append(row)
        return out

    def base_rows(self) -> list[dict]:
        self.docs_cache = self.docs(0, self.n_doc)
        return self.scalars(0, self.n_scalars) + self.docs_cache + self.others() + self.edges()


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def search_corpus(path: Path, n: int, seed: int = 11) -> Path:
    """A larger Doc-only corpus for search timings (same `Doc` type)."""
    d = Data(seed=seed, scale=1)
    rows = d.docs(0, n)
    return write_jsonl(path, rows)


SEARCH_PG = """\
node Doc {
    slug: String @key @index
    title: String @index
    body: String @index
    note: String?
    lang: enum(en, de, fr)
    year: I32
    emb: Vector(8) @index
    emb_opt: Vector(8)?
    emb_e: Vector(8)? @embed("title")
}
"""
