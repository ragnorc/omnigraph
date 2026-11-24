"""Seeded configurations: a schema, its data, and the text the fuzzer reads.

A configuration is drawn from one integer seed and nothing else, so
`draw(seed)` returns the same schema, rows and query texts every time. It
extends the migration matrix's fixed `gen.py` (one schema with every
feature) to a random draw over the dimensions the brief names: 1 to 6 node
types and 0 to 4 edge types; every scalar type, nullable and required;
lists, vectors with and without a distance, managed Blobs; `@key` single,
compound and absent; `@unique`, `@card`, `@range`; and the index mix this
run is about (plain `@index` on a String, which edition 2 builds as full
text; `@key` Strings with and without `@index`; `@index` on numeric, enum,
Bool and list properties; several full-text fields on one type; a type with
no index).

The schema is held as data (`Config`) and rendered to `.pg` text by
`render`, so a later step can respell one property (`@index(fulltext,
analyzer = …)`, a vector `distance`, a property-level `@index` on a list)
and render again. Edition 2 refuses some of what the draw can hold (a
`distance`, a property-level `@index` on a list); `gq2_refusable` names
those, and the case records GQ 2's refusal as data.
"""

from __future__ import annotations

import base64
import copy
import json
import random
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCALARS = ["String", "Bool", "I32", "I64", "U32", "U64", "F32", "F64", "Date", "DateTime", "enum"]
NUMERIC = {"I32", "I64", "U32", "U64", "F32", "F64"}
ANALYZERS = ["standard_v1", "standard_folded_v1", "english_v1"]
SCORERS = ["bm25_v1", "none"]
DISTANCES = ["l2", "cosine", "dot"]

# ---------------------------------------------------------------- text

STEMS = ["run", "running", "runs", "runner", "ran", "Running", "RUNS"]
STOPS = ["the", "a", "is", "of", "and", "only", "very", "any", "why", "during", "being", "beings",
         "human", "The", "AND", "were", "it's"]
ACCENT = ["café", "Café", "CAFÉ", "cafe", "naïve", "naive", "Straße", "strasse", "über", "ÜBER", "uber",
          "résumé", "résumés", "resumes", "resume", "Ångström", "angstrom", "ﬁle", "file", "Æon", "aeon"]
NFD = [unicodedata.normalize("NFD", w) for w in ("café", "résumé", "naïve", "Ångström", "über")]
EMOJI_CJK = ["🚀", "数据库", "图数据库", "東京", "한국어", "ü🙂", "🙂🙂", "데이터", "テスト", "Ελληνικά", "русский", "x²"]
PUNCT = ["!!!", "...", "-", "—", "''", "@#$%", "()", "C++", "node.js", "don't", "e-mail", "U.S.A."]
NEAR = ["graph", "grahp", "graf", "grapj", "graphs", "grph", "Graph", "GRAPH", "graphy", "giraffe"]
LONGTOK = ["supercalifragilisticexpialidociousxxxxxxxxxx", "a" * 40, "b" * 39, "tokentokentokentokentokentokentokentoken1"]
FILLER = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
          "kilo", "lima", "mike", "november", "river", "stone", "quantum", "harbor", "harbour", "zebra",
          "kiwi", "maple", "ember", "orbit", "lance", "vector", "tariff", "Tariffs", "colour", "color"]
POOL = STEMS + STOPS + ACCENT + NFD + EMOJI_CJK + PUNCT + NEAR + FILLER

TEXT_CATS = ["plain", "plain", "plain", "empty", "null", "long", "repeat", "stop_only", "stems",
             "case_accent", "nfc_nfd", "emoji_cjk", "punct", "near_dup", "longtok"]

# Query strings for the lexical reads: each probes one analyzer difference.
QUERY_POOL = ["run", "running", "the", "cafe", "café", unicodedata.normalize("NFD", "café"), "CAFÉ",
              "graph", "grph", "grahp graph", "数据库", "🚀", "resume", "résumés", "strasse", "Straße",
              "only very", "human beings", "don't", "dont", "zebra kiwi", "harbour", "colour color",
              LONGTOK[0], "a" * 40, "x²", "file", "ﬁle", "uber", "graphs running café", "", "...",
              "ångström", "aeon", "e-mail", "tariff", "Tariffs"]


def text_value(rng: random.Random, cat: str | None = None) -> str | None:
    cat = cat or rng.choice(TEXT_CATS)
    if cat == "null":
        return None
    if cat == "empty":
        return ""
    if cat == "long":
        return " ".join(rng.choice(POOL) for _ in range(rng.randint(200, 1200)))
    if cat == "repeat":
        w = rng.choice(POOL)
        return " ".join([w] * rng.randint(2, 30)) + " " + rng.choice(FILLER)
    if cat == "stop_only":
        return " ".join(rng.choice(STOPS) for _ in range(rng.randint(1, 5)))
    if cat == "stems":
        return " ".join(rng.choice(STEMS + FILLER[:4]) for _ in range(rng.randint(1, 6)))
    if cat == "case_accent":
        return " ".join(rng.choice(ACCENT + FILLER[:4]) for _ in range(rng.randint(1, 6)))
    if cat == "nfc_nfd":
        w = rng.choice(["café", "résumé", "naïve", "über"])
        return f"{w} {unicodedata.normalize('NFD', w)} {rng.choice(FILLER)}"
    if cat == "emoji_cjk":
        return "".join(rng.choice(EMOJI_CJK + [" "]) for _ in range(rng.randint(1, 6)))
    if cat == "punct":
        return " ".join(rng.choice(PUNCT) for _ in range(rng.randint(1, 4)))
    if cat == "near_dup":
        return " ".join(rng.choice(NEAR) for _ in range(rng.randint(1, 4)))
    if cat == "longtok":
        return rng.choice(LONGTOK) + " " + rng.choice(FILLER)
    return " ".join(rng.choice(POOL) for _ in range(rng.randint(1, 12)))


# ---------------------------------------------------------------- schema

@dataclass
class Prop:
    name: str
    ty: str                     # a SCALARS name, "list_str", "list_i32", "vector", "blob"
    nullable: bool = False
    dim: int = 0                # vector dimension
    enum_vals: list = field(default_factory=list)
    index: bool = False         # property-level `@index`
    body_index: bool = False    # body-level `@index(p)` (edition 2 accepts it on a list)
    unique: bool = False        # `@unique` on the property
    key: bool = False           # property-level `@key`
    # GQ 3 respellings, rendered only when set:
    fulltext: bool = False      # `@index(fulltext, ...)` instead of `@index`
    analyzer: str | None = None
    scorer: str | None = None
    distance: str | None = None
    rename_from: str | None = None
    rng_range: tuple | None = None  # `@range(p, lo..hi)`

    def ty_text(self) -> str:
        if self.ty == "enum":
            t = f"enum({', '.join(self.enum_vals)})"
        elif self.ty == "list_str":
            t = "[String]"
        elif self.ty == "list_i32":
            t = "[I32]"
        elif self.ty == "vector":
            t = f'Vector({self.dim}, distance = "{self.distance}")' if self.distance else f"Vector({self.dim})"
        elif self.ty == "blob":
            t = "Blob"
        else:
            t = self.ty
        return t + ("?" if self.nullable else "")

    @property
    def is_text(self) -> bool:
        return self.ty == "String" and not self.key


@dataclass
class NodeT:
    name: str
    props: list
    key_mode: str               # single | compound | none
    compound: list = field(default_factory=list)

    def prop(self, name):
        for p in self.props:
            if p.name == name:
                return p
        return None

    def key_props(self) -> list:
        if self.key_mode == "single":
            return [p.name for p in self.props if p.key]
        if self.key_mode == "compound":
            return list(self.compound)
        return []


@dataclass
class EdgeT:
    name: str
    src: str
    dst: str
    props: list
    card: str | None = None
    keyed: bool = False         # `@key(@src, @dst)`
    unique_pair: bool = False   # `@unique(@src, @dst)`

    @property
    def verb(self) -> str:
        return self.name[0].lower() + self.name[1:]


@dataclass
class Config:
    seed: int
    nodes: list
    edges: list
    sizes: dict
    builder: str                # rel | main
    queries: list
    vec_query: dict
    stress: bool = False

    def node(self, name):
        for n in self.nodes:
            if n.name == name:
                return n
        return None

    def to_json(self):
        return asdict(self)

    @staticmethod
    def from_json(d):
        c = Config(**{k: v for k, v in d.items() if k not in ("nodes", "edges")}, nodes=[], edges=[])
        c.nodes = [NodeT(**{**n, "props": [Prop(**p) for p in n["props"]]}) for n in d["nodes"]]
        c.edges = [EdgeT(**{**e, "props": [Prop(**p) for p in e["props"]]}) for e in d["edges"]]
        return c


def _prop_ann(p: Prop, edition: int) -> str:
    out = []
    if p.key:
        out.append("@key")
    if p.index:
        if p.fulltext:
            opts = []
            if p.analyzer:
                opts.append(f'analyzer = "{p.analyzer}"')
            if p.scorer:
                opts.append(f'scorer = "{p.scorer}"')
            out.append("@index(fulltext" + "".join(", " + o for o in opts) + ")")
        else:
            out.append("@index")
    if p.unique:
        out.append("@unique")
    if p.rename_from:
        out.append(f'@rename_from("{p.rename_from}")')
    return (" " + " ".join(out)) if out else ""


def render(cfg: Config, edition: int = 2) -> str:
    lines = [f"// upgrade-fuzz seed {cfg.seed}"]
    for n in cfg.nodes:
        lines.append(f"node {n.name} {{")
        for p in n.props:
            lines.append(f"    {p.name}: {p.ty_text()}{_prop_ann(p, edition)}")
        if n.key_mode == "compound":
            lines.append(f"    @key({', '.join(n.compound)})")
        for p in n.props:
            if p.body_index:
                lines.append(f"    @index({p.name})")
            if p.rng_range:
                lines.append(f"    @range({p.name}, {p.rng_range[0]}..{p.rng_range[1]})")
        lines.append("}")
    for e in cfg.edges:
        card = f" @card({e.card})" if e.card else ""
        lines.append(f"edge {e.name}: {e.src} -> {e.dst}{card} {{")
        for p in e.props:
            lines.append(f"    {p.name}: {p.ty_text()}{_prop_ann(p, edition)}")
        if e.keyed:
            lines.append("    @key(@src, @dst)")
        if e.unique_pair:
            lines.append("    @unique(@src, @dst)")
        lines.append("}")
    return "\n".join(lines) + "\n"


def one_way_constructs(cfg: Config) -> set:
    """The constructs NEW's upgrade guide lists as one-way, as (construct,
    type, property), read from the configuration the way the guide states
    them. The case compares this with NEW's `one_way_constructs`."""
    out = set()
    for t in list(cfg.nodes) + list(cfg.edges):
        for p in t.props:
            if p.ty == "vector" and p.distance:
                out.add(("vector_distance", t.name, p.name))
            if p.ty in ("list_str", "list_i32") and p.index:
                out.add(("list_index", t.name, p.name))
            if p.index and p.fulltext:
                out.add(("full_text_index", t.name, p.name))
                if p.analyzer:
                    out.add(("full_text_analyzer", t.name, p.name))
                if p.scorer:
                    out.add(("full_text_scorer", t.name, p.name))
    return out


def gq2_refusable(cfg: Config) -> list:
    """What edition 2's parser refuses in this configuration."""
    out = []
    for t in list(cfg.nodes) + list(cfg.edges):
        for p in t.props:
            if p.ty == "vector" and p.distance:
                out.append(f"{t.name}.{p.name}: distance")
            if p.ty in ("list_str", "list_i32") and p.index:
                out.append(f"{t.name}.{p.name}: @index on a list")
            if p.fulltext:
                out.append(f"{t.name}.{p.name}: @index(fulltext)")
    return out


def _scalar(rng, name, ty, nullable=None) -> Prop:
    p = Prop(name=name, ty=ty, nullable=rng.random() < 0.4 if nullable is None else nullable)
    if ty == "enum":
        p.enum_vals = rng.sample(["alpha", "beta", "gamma", "delta", "omega"], rng.randint(2, 4))
    return p


def draw(seed: int, stress: bool = False) -> Config:
    rng = random.Random(seed)
    n_nodes = rng.randint(1, 6)
    n_edges = rng.randint(0, 4) if n_nodes else 0
    nodes = []
    for ti in range(n_nodes):
        name = f"T{ti}"
        km = rng.choices(["single", "compound", "none"], [0.6, 0.15, 0.25])[0]
        props = []
        if km == "single":
            kty = rng.choices(["String", "I64"], [0.8, 0.2])[0]
            kp = Prop(name="k", ty=kty, key=True)
            if kty == "String" and rng.random() < 0.5:
                kp.index = True  # `@key` String with `@index`: edition 2 builds full text on it
            props.append(kp)
        compound = []
        if km == "compound":
            props += [Prop(name="ka", ty="String"), Prop(name="kb", ty="I32")]
            compound = ["ka", "kb"]
        # text fields: none, one, or several on one type
        n_text = rng.choices([0, 1, 2, 3], [0.2, 0.4, 0.25, 0.15])[0]
        for i in range(n_text):
            p = Prop(name=f"txt{i}", ty="String", nullable=rng.random() < 0.4)
            p.index = rng.random() < 0.7
            props.append(p)
        for i in range(rng.randint(0, 5)):
            ty = rng.choice(SCALARS[1:])
            p = _scalar(rng, f"s{i}", ty)
            r = rng.random()
            if r < 0.35:
                p.index = True
            elif r < 0.45 and ty not in ("Bool", "enum", "F32", "F64"):
                p.unique = True
            if ty in ("I32", "I64") and rng.random() < 0.15 and not p.nullable:
                p.rng_range = (0, 100000)
            props.append(p)
        for i in range(rng.choices([0, 1, 2], [0.6, 0.3, 0.1])[0]):
            p = Prop(name=f"l{i}", ty=rng.choice(["list_str", "list_i32"]), nullable=rng.random() < 0.4)
            r = rng.random()
            if r < 0.25:
                p.body_index = True          # edition 2 accepts this form
            elif r < 0.3:
                p.index = True               # edition 2 refuses this form: recorded as data
            props.append(p)
        for i in range(rng.choices([0, 1, 2], [0.55, 0.35, 0.1])[0]):
            p = Prop(name=f"v{i}", ty="vector", dim=rng.choice([2, 4, 8]), nullable=rng.random() < 0.4)
            p.index = rng.random() < 0.5
            if rng.random() < 0.04:
                p.distance = rng.choice(DISTANCES)  # edition 2 refuses: recorded as data
            props.append(p)
        if rng.random() < 0.25:
            props.append(Prop(name="blob", ty="blob", nullable=rng.random() < 0.5))
        if rng.random() < 0.03:
            for p in props:
                if p.is_text and p.index:
                    p.fulltext = True  # edition 2 refuses `@index(fulltext)`: recorded as data
                    break
        nodes.append(NodeT(name=name, props=props, key_mode=km, compound=compound))
    keyed = [n for n in nodes if n.key_mode == "single" and n.prop("k").ty == "String"]
    edges = []
    if keyed:
        for ei in range(n_edges):
            a, b = rng.choice(keyed), rng.choice(keyed)
            props = []
            if rng.random() < 0.5:
                props.append(_scalar(rng, "w", rng.choice(["I32", "F64", "I64"]), nullable=True))
                props[-1].index = rng.random() < 0.3
            if rng.random() < 0.3:
                props.append(Prop(name="note", ty="String", nullable=True, index=rng.random() < 0.5))
            e = EdgeT(name=f"Rel{ei}", src=a.name, dst=b.name, props=props)
            r = rng.random()
            if r < 0.2:
                e.keyed = True
                if rng.random() < 0.5:
                    e.card = "0..1"
            elif r < 0.35:
                e.unique_pair = True
            elif r < 0.45:
                e.card = rng.choice(["0..3", "0..5"])
            edges.append(e)
    sizes = {}
    for n in nodes:
        if stress:
            sizes[n.name] = 100000 if n is nodes[0] else rng.randint(10, 200)
        else:
            sizes[n.name] = rng.choices(
                [rng.randint(10, 60), rng.randint(60, 400), rng.randint(400, 3000), rng.randint(3000, 20000)],
                [0.45, 0.3, 0.17, 0.08])[0]
    queries = rng.sample(QUERY_POOL, 3)
    vec_query = {}
    for n in nodes:
        for p in n.props:
            if p.ty == "vector":
                vec_query[f"{n.name}.{p.name}"] = vec(rng, p.dim)
    builder = rng.choices(["rel", "main"], [0.75, 0.25])[0]
    return Config(seed=seed, nodes=nodes, edges=edges, sizes=sizes, builder=builder,
                  queries=queries, vec_query=vec_query, stress=stress)


# ---------------------------------------------------------------- data

def vec(rng, dim: int) -> list:
    v = [round(rng.uniform(-1.0, 1.0) * 1024) / 1024 for _ in range(dim)]
    if all(x == 0 for x in v):
        v[0] = 0.5
    return v


def blob(payload: bytes) -> str:
    return "base64:" + base64.b64encode(payload).decode()


def key_value(n: NodeT, i: int):
    kp = n.prop("k")
    if kp is not None and kp.ty == "I64":
        return i * 7 - 3
    return f"{n.name.lower()}-{i:06d}"


def scalar_value(rng, p: Prop, i: int):
    t = p.ty
    if t == "String":
        return text_value(rng) or "x"
    if t == "Bool":
        return rng.random() < 0.5
    if p.unique and p.rng_range:
        return i
    if p.rng_range:
        return rng.randint(p.rng_range[0], p.rng_range[1])
    if t == "I32":
        return i * 7 + 1 if p.unique else rng.randint(-1000, 1000)
    if t == "I64":
        if p.unique:
            return i * 1000003 - 7
        return rng.choice([-(2**63), 2**63 - 1, 9007199254740993, 0, rng.randint(-10**12, 10**12)]) if i < 5 else rng.randint(-10**6, 10**6)
    if t == "U32":
        return 4000000000 - i if p.unique else rng.randint(0, 2**32 - 1)
    if t == "U64":
        return i * 7 + 1 if p.unique else rng.choice([2**64 - 1, 0, 2**53 + 1, rng.randint(0, 10**9)])
    if t == "F32":
        return rng.choice([0.1, -0.0, 1.5, 1e-7, round(rng.uniform(-100, 100), 3)])
    if t == "F64":
        return rng.choice([0.1, -0.0, 1e300, 5e-324, 3.0, rng.uniform(-1e6, 1e6)])
    if t == "Date" and p.unique:
        return f"{1970 + i // 336}-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}"
    if t == "DateTime" and p.unique:
        return f"2000-01-01T00:00:00Z" if i == 0 else f"{2000 + i // 8064}-{(i // 672) % 12 + 1:02d}-{(i // 24) % 28 + 1:02d}T{i % 24:02d}:00:00Z"
    if t == "Date":
        return f"{rng.randint(1970, 2200)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    if t == "DateTime":
        return f"{rng.randint(1970, 2100)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00Z"
    if t == "enum":
        return rng.choice(p.enum_vals)
    if t == "list_str":
        return [rng.choice(["rust", "Rust", "go", "zig", "c", "café", "数据"]) for _ in range(rng.randint(0, 4))]
    if t == "list_i32":
        return [rng.randint(-50, 50) for _ in range(rng.randint(0, 4))]
    if t == "vector":
        return vec(rng, p.dim)
    if t == "blob":
        return blob(f"payload-{i}-{rng.random()}".encode() * rng.randint(0, 3))
    raise ValueError(t)


def node_row(rng, n: NodeT, i: int, text_cat: str | None = None) -> dict:
    row = {}
    for p in n.props:
        if p.key:
            row[p.name] = key_value(n, i)
            continue
        if n.key_mode == "compound" and p.name in n.compound:
            row[p.name] = ["x", "y", "z", "ü"][i % 4] if p.name == "ka" else i // 4
            continue
        if p.ty == "String" and p.is_text:
            v = text_value(rng, text_cat)
            if v is None:
                if p.nullable:
                    continue
                v = ""
            row[p.name] = v
            continue
        if p.nullable and rng.random() < 0.3:
            continue
        if p.unique and p.ty == "String":
            row[p.name] = f"u-{n.name}-{i}"
            continue
        row[p.name] = scalar_value(rng, p, i)
    return {"type": n.name, "data": row}


def edge_rows(rng, cfg: Config, e: EdgeT, n_src: int, n_dst: int, count: int, start: int = 0) -> list:
    src, dst = cfg.node(e.src), cfg.node(e.dst)
    out, seen, per_src = [], set(), {}
    hi = {"0..1": 1, "0..3": 3, "0..5": 5}.get(e.card or "", 10**9)
    for j in range(count * 3):
        if len(out) >= count:
            break
        a, b = rng.randrange(max(n_src, 1)), rng.randrange(max(n_dst, 1))
        if (e.keyed or e.unique_pair) and (a, b) in seen:
            continue
        if per_src.get(a, 0) >= hi:
            continue
        seen.add((a, b))
        per_src[a] = per_src.get(a, 0) + 1
        data = {}
        for p in e.props:
            if p.nullable and rng.random() < 0.3:
                continue
            data[p.name] = text_value(rng) if p.ty == "String" else scalar_value(rng, p, start + j)
            if data[p.name] is None:
                data.pop(p.name)
        out.append({"edge": e.name, "from": key_value(src, a), "to": key_value(dst, b), "data": data})
    return out


def base_rows(cfg: Config) -> list:
    rng = random.Random(cfg.seed * 7919 + 1)
    rows = []
    for n in cfg.nodes:
        for i in range(cfg.sizes[n.name]):
            rows.append(node_row(rng, n, i))
    for e in cfg.edges:
        ns, nd = cfg.sizes[e.src], cfg.sizes[e.dst]
        rows += edge_rows(rng, cfg, e, ns, nd, min(ns * 2, 4000))
    return rows


def write_jsonl(path: Path, rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def clone(cfg: Config) -> Config:
    return copy.deepcopy(cfg)
