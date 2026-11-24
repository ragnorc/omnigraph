"""Seeded generator of edition-2 (GQ 2) schemas, seed data and queries.

Every case is (seed, schema, data, query). `draw_world(seed)` returns a schema
and its seed data; `draw_queries(world, seed, n)` returns queries drawn from
edition 2's grammar (crates/omnigraph-compiler/src/query/query.pest at
EDITION_2_SOURCE_COMMIT) and its type rules, type-directed against the schema.
Each query carries the set of edition-2 forms it contains (`forms`), which the
coverage report counts, and whether it was drawn to be refused by edition 2
(`intent`).

Only the Python standard library is used, and every random choice comes from
one `random.Random(seed)`, so a case is reproduced from its seed alone.
"""

from __future__ import annotations

GEN_VERSION = 2  # v2 adds the compatible-text mode for seeds >= COMPAT_SEEDS; seeds below draw exactly as v1

import json
import random
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Value pools: small, so ties, duplicates and repeats are frequent.
# --------------------------------------------------------------------------

STRINGS = [
    "", "the", "a", "and the", "run", "runs", "running", "Run", "RUN", "ran",
    "dog", "Dogs", "the dog", "dog house", "running dogs", "Hello World",
    "hello world", "café", "café", "naïve", "ab", "abc", "x", "x y z",
    "of the and", "Ünïcode", "data base", "database", "it's", "fox",
]
WORDS = ["run", "dog", "the", "Dogs", "running", "fox", "data", "x", "café", "hello", "a", "base", "an"]
TEXT_QUERIES = ["run", "dog", "dogs", "the", "Running", "fox", "hello world", "the and", "café", "cafe", "dgo", "rnu", "xyz", "data base", "a"]
INT_POOL = [0, 1, 2, 3, 7, 42, -1, -5, 100]
BIG_INTS = [2**31 - 1, -(2**31), 2**53, 2**53 + 1, 2**62, -(2**53) - 1, 9007199254740993]
FLOATS = [0.0, 1.5, -2.25, 3.0, 0.1, 1e10, 2.5, 7.0]
DATES = ["2020-01-01", "1999-12-31", "2024-02-29", "2020-01-01", "1970-01-01"]
DATETIMES = ["2020-01-01T00:00:00Z", "2020-01-01T00:00:00.123Z", "1999-12-31T23:59:59Z", "2024-02-29T12:00:00Z"]
ENUM_SETS = [["low", "mid", "high"], ["a", "b"], ["red", "green", "blue", "x-1"]]
BLOBS = ["base64:SGVsbG8=", "base64:", "base64:AAEC"]

NODE_NAMES = ["Person", "Doc", "Tag", "Item", "Org", "Place"]
EDGE_NAMES = ["Knows", "Wrote", "Tagged", "Likes", "Owns", "Cites", "Near", "Links"]
PROP_NAMES = ["name", "title", "body", "age", "score", "rank", "flag", "born", "seen", "tags",
              "nums", "vec", "level", "content", "note", "weight", "slug", "code", "size", "count"]

SCALARS = ["String", "Bool", "I32", "I64", "U32", "U64", "F32", "F64", "Date", "DateTime"]
INT_TYPES = {"I32": (-(2**31), 2**31 - 1), "I64": (-(2**63), 2**63 - 1), "U32": (0, 2**32 - 1), "U64": (0, 2**63 - 1)}
NUMERIC = {"I32", "I64", "U32", "U64", "F32", "F64"}


@dataclass
class Prop:
    name: str
    ty: str              # scalar name, "enum", "list", "vector", "blob"
    nullable: bool
    elem: str = ""       # list element scalar
    enum: list = field(default_factory=list)
    dim: int = 0
    index: bool = False  # property-level @index
    unique: bool = False
    key: bool = False    # property-level @key

    def decl(self) -> str:
        if self.ty == "enum":
            t = "enum(" + ", ".join(self.enum) + ")"
        elif self.ty == "list":
            t = f"[{self.elem}]"
        elif self.ty == "vector":
            t = f"Vector({self.dim})"
        elif self.ty == "blob":
            t = "Blob"
        else:
            t = self.ty
        s = f"{self.name}: {t}{'?' if self.nullable else ''}"
        if self.key:
            s += " @key"
        if self.unique:
            s += " @unique"
        if self.index:
            s += " @index"
        return s

    @property
    def scalar(self) -> bool:
        return self.ty in SCALARS or self.ty == "enum"


@dataclass
class NodeT:
    name: str
    props: list
    key: list = field(default_factory=list)       # property names of @key
    body: list = field(default_factory=list)      # body constraints text
    fts: list = field(default_factory=list)       # String props full-text-indexed in edition 2

    def prop(self, name):
        return next(p for p in self.props if p.name == name)


@dataclass
class EdgeT:
    name: str
    src: str
    dst: str
    props: list
    card: str = ""
    body: list = field(default_factory=list)
    keyed: bool = False

    @property
    def ident(self) -> str:
        return self.name[0].lower() + self.name[1:]

    def prop(self, name):
        return next(p for p in self.props if p.name == name)


@dataclass
class World:
    seed: int
    nodes: list
    edges: list
    rows: dict      # node name -> list of dict(id=..., data=...)
    erows: dict     # edge name -> list of dict(from, to, data)
    index_build: bool

    def node(self, name) -> NodeT:
        return next(n for n in self.nodes if n.name == name)

    def schema_text(self) -> str:
        out = []
        for n in self.nodes:
            out.append(f"node {n.name} {{")
            for p in n.props:
                out.append("  " + p.decl())
            for b in n.body:
                out.append("  " + b)
            out.append("}")
        for e in self.edges:
            head = f"edge {e.name}: {e.src} -> {e.dst}"
            if e.card:
                head += f" @card({e.card})"
            if e.props or e.body:
                out.append(head + " {")
                for p in e.props:
                    out.append("  " + p.decl())
                for b in e.body:
                    out.append("  " + b)
                out.append("}")
            else:
                out.append(head)
        return "\n".join(out) + "\n"

    def data_jsonl(self) -> str:
        lines = []
        for n in self.nodes:
            for r in self.rows[n.name]:
                rec = {"type": n.name}
                if not n.key:
                    rec["id"] = r["id"]
                rec["data"] = r["data"]
                lines.append(json.dumps(rec, ensure_ascii=False))
        for e in self.edges:
            for r in self.erows[e.name]:
                rec = {"edge": e.name, "from": r["from"], "to": r["to"]}
                if r["data"]:
                    rec["data"] = r["data"]
                lines.append(json.dumps(rec, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------


def scalar_value(rng: random.Random, ty: str, enum=None):
    if ty == "String":
        return rng.choice(STRINGS)
    if ty == "Bool":
        return rng.choice([True, False])
    if ty in INT_TYPES:
        lo, hi = INT_TYPES[ty]
        pool = INT_POOL + (BIG_INTS if ty in ("I64", "U64") else [lo, hi] if ty != "U32" else [hi])
        v = rng.choice(pool)
        return min(max(v, lo), hi)
    if ty == "F32":
        return rng.choice([0.0, 1.5, -2.25, 3.0, 0.5, 7.0, 2.5])
    if ty == "F64":
        return rng.choice(FLOATS)
    if ty == "Date":
        return rng.choice(DATES)
    if ty == "DateTime":
        return rng.choice(DATETIMES)
    if ty == "enum":
        return rng.choice(enum)
    raise ValueError(ty)


def prop_value(rng: random.Random, p: Prop):
    if p.nullable and rng.random() < 0.25:
        return None
    if p.name == "text" and p.ty == "String":
        return " ".join(rng.choice(WORDS + STRINGS[:12]) for _ in range(rng.choice([0, 1, 2, 3, 4]))).strip()
    if p.ty == "list":
        k = rng.choice([0, 1, 1, 2, 3])
        return [scalar_value(rng, p.elem) for _ in range(k)]
    if p.ty == "vector":
        base = rng.choice([[0.0] * p.dim, [1.0] + [0.0] * (p.dim - 1), [0.5] * p.dim, [1.0] * p.dim, [-1.0] * p.dim])
        if rng.random() < 0.5:
            base = [round(rng.uniform(-2, 2), 2) for _ in range(p.dim)]
        return base
    if p.ty == "blob":
        return rng.choice(BLOBS)
    return scalar_value(rng, p.ty, p.enum)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def draw_prop(rng: random.Random, name: str, allow_vec=True) -> Prop:
    r = rng.random()
    nullable = rng.random() < 0.55
    if r < 0.62:
        ty = rng.choice(SCALARS + ["String", "String", "I64", "I32"])
        return Prop(name, ty, nullable)
    if r < 0.72:
        return Prop(name, "enum", nullable, enum=rng.choice(ENUM_SETS))
    if r < 0.86:
        return Prop(name, "list", nullable, elem=rng.choice(["String", "String", "I64", "I32", "F64", "Bool", "Date"]))
    if r < 0.94 and allow_vec:
        return Prop(name, "vector", nullable, dim=rng.choice([2, 3]))
    return Prop(name, "blob", True)


def draw_world(seed: int) -> World:
    rng = random.Random(seed)
    n_nodes = rng.choice([1, 2, 2, 3, 3, 4, 5])
    names = rng.sample(NODE_NAMES, n_nodes)
    nodes = []
    for nm in names:
        k = rng.choice([1, 2, 3, 3, 4, 5])
        pnames = rng.sample(PROP_NAMES, k)
        props = [draw_prop(rng, pn) for pn in pnames]
        node = NodeT(nm, props)
        # Key: single, compound or absent.
        kr = rng.random()
        keyable = [p for p in props if p.ty in ("String", "I64", "I32")]
        if kr < 0.5 and keyable:
            kp = rng.choice(keyable)
            kp.nullable = False
            kp.key = True
            node.key = [kp.name]
        elif kr < 0.65 and len(keyable) >= 2:
            a, b = sorted(rng.sample(keyable, 2), key=props.index)
            a.nullable = b.nullable = False
            node.key = [a.name, b.name]
            node.body.append(f"@key({a.name}, {b.name})")
        # Unique, index, range.
        for p in props:
            if p.key:
                continue
            if p.ty in ("String", "I64", "I32", "U32", "U64", "Date", "enum") and rng.random() < 0.12:
                p.unique = True
            if p.ty in ("String", "I32", "I64", "U32", "U64", "F32", "F64", "Date", "DateTime", "enum", "Bool") and rng.random() < 0.4:
                p.index = True
        lists = [p for p in props if p.ty == "list"]
        if lists and rng.random() < 0.4:
            node.body.append(f"@index({rng.choice(lists).name})")
        nums = [p for p in props if p.ty in ("I32", "I64", "F64")]
        if nums and rng.random() < 0.25:
            p = rng.choice(nums)
            p._range = (-10, 1000)
            node.body.append(f"@range({p.name}, -10..1000)")
        nodes.append(node)
    # A share of worlds carries a full-text-indexed text property beside a
    # vector, so full-text, ranking and fusion forms are drawn often.
    if rng.random() < 0.45:
        node = rng.choice(nodes)
        node.props.append(Prop("text", "String", rng.random() < 0.6, index=True))
        if not any(p.ty == "vector" for p in node.props):
            node.props.append(Prop("emb", "vector", rng.random() < 0.5, dim=3))
    for node in nodes:
        node.fts = [p.name for p in node.props if p.ty == "String" and (p.index or (p.key and len(node.key) == 1))]
    # Edges.
    edges = []
    n_edges = rng.choice([0, 1, 1, 2, 2, 3, 4]) if n_nodes else 0
    enames = rng.sample(EDGE_NAMES, n_edges)
    for en in enames:
        src = rng.choice(names)
        dst = src if rng.random() < 0.35 else rng.choice(names)
        if edges and rng.random() < 0.25:  # parallel edge type over existing endpoints
            src, dst = edges[0].src, edges[0].dst
        eprops = []
        if rng.random() < 0.45:
            for pn in rng.sample(["since", "weight", "role", "w"], rng.choice([1, 2])):
                p = draw_prop(rng, pn, allow_vec=False)
                if p.ty == "blob":
                    p = Prop(pn, "I32", True)
                eprops.append(p)
        e = EdgeT(en, src, dst, eprops)
        cr = rng.random()
        if cr < 0.1:
            e.card = "0..1"
        elif cr < 0.15:
            e.card = "0..2"
        br = rng.random()
        if br < 0.12:
            e.body.append("@key(@src, @dst)")
            e.keyed = True
        elif br < 0.2:
            e.body.append("@unique(@src, @dst)")
        edges.append(e)
    world = World(seed, nodes, edges, {}, {}, index_build=rng.random() < 0.5)
    draw_data(rng, world)
    return world


def node_id(node: NodeT, data: dict, fallback: str) -> str:
    if not node.key:
        return fallback
    vals = [data[k] for k in sorted(node.key)]  # the catalog orders key members by name
    if len(vals) == 1:
        return str(vals[0])
    return json.dumps([str(v) for v in vals], separators=(",", ":"), ensure_ascii=False)


def draw_data(rng: random.Random, world: World):
    for n in world.nodes:
        rows = []
        seen_keys = set()
        seen_unique = {p.name: set() for p in n.props if p.unique}
        count = rng.choice([0, 1, 2, 3, 4, 5, 6, 8])
        for i in range(count * 2):
            if len(rows) >= count:
                break
            data = {}
            for p in n.props:
                v = prop_value(rng, p)
                rg = getattr(p, "_range", None)
                if rg and v is not None and not (rg[0] <= v <= rg[1]):
                    v = rng.choice([0, 1, 7, 42, -5])
                if v is None:
                    if rng.random() < 0.5:
                        continue  # absent rather than explicit null
                if p.ty == "String" and n.key == [p.name] and v == "":
                    v = "k"
                data[p.name] = v
            if any(data.get(k) is None for k in n.key):
                continue
            kid = node_id(n, data, f"{n.name[0].lower()}{i}")
            if kid in seen_keys:
                continue
            ok = True
            for pn, seen in seen_unique.items():
                if data.get(pn) is not None:
                    if json.dumps(data[pn]) in seen:
                        ok = False
            if not ok:
                continue
            for pn, seen in seen_unique.items():
                if data.get(pn) is not None:
                    seen.add(json.dumps(data[pn]))
            seen_keys.add(kid)
            rows.append({"id": kid, "data": data})
        world.rows[n.name] = rows
    for e in world.edges:
        src_ids = [r["id"] for r in world.rows[e.src]]
        dst_ids = [r["id"] for r in world.rows[e.dst]]
        rows = []
        pairs = set()
        out_count = {}
        maxc = int(e.card.split("..")[1]) if e.card and e.card.split("..")[1] else None
        if src_ids and dst_ids:
            for _ in range(rng.choice([0, 1, 2, 3, 4, 6, 8])):
                a = rng.choice(src_ids)
                b = a if (e.src == e.dst and rng.random() < 0.2) else rng.choice(dst_ids)
                if (e.keyed or "@unique(@src, @dst)" in e.body) and (a, b) in pairs:
                    continue
                if maxc is not None and out_count.get(a, 0) >= maxc:
                    continue
                pairs.add((a, b))
                out_count[a] = out_count.get(a, 0) + 1
                data = {}
                for p in e.props:
                    v = prop_value(rng, p)
                    while v is None and not p.nullable:
                        v = prop_value(rng, p)
                    if v is not None:
                        data[p.name] = v
                rows.append({"from": a, "to": b, "data": data})
            # a cycle for self edges
            if e.src == e.dst and len(src_ids) >= 2 and rng.random() < 0.5 and maxc is None and not e.keyed and "@unique(@src, @dst)" not in e.body:
                a, b = src_ids[0], src_ids[1]
                for x, y in ((a, b), (b, a)):
                    data = {}
                    for p in e.props:
                        v = prop_value(rng, p)
                        while v is None and not p.nullable:
                            v = prop_value(rng, p)
                        if v is not None:
                            data[p.name] = v
                    rows.append({"from": x, "to": y, "data": data})
        world.erows[e.name] = rows


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


@dataclass
class Query:
    name: str
    text: str
    params: dict
    kind: str                 # "read" | "write"
    forms: set
    intent: str = "accept"    # "accept" | "refuse"


def lit(v, ty: str) -> str:
    """An edition-2 literal for value v of scalar type ty (non-negative numbers only
    are literals; negatives are rendered through a parameter by the caller)."""
    if ty in ("String", "enum"):
        return json.dumps(v, ensure_ascii=False)
    if ty == "Bool":
        return "true" if v else "false"
    if ty == "Date":
        return f'date("{v}")'
    if ty == "DateTime":
        return f'datetime("{v}")'
    if ty in ("F32", "F64"):
        f = float(v)
        s = repr(abs(f))
        if "e" in s or "inf" in s:
            s = "10000000000.0"
        return s if "." in s else s + ".0"
    return str(abs(int(v)))


def param_type(p: Prop) -> str:
    if p.ty == "enum":
        return "String"
    if p.ty == "list":
        return f"[{p.elem}]"
    if p.ty == "vector":
        return f"Vector({p.dim})"
    return p.ty


class QGen:
    def __init__(self, world: World, rng: random.Random):
        self.w = world
        self.rng = rng

    # -- helpers -------------------------------------------------------------
    def fresh(self, base: str) -> str:
        self.vc += 1
        return f"${base}{self.vc}"

    def new_param(self, ty: str, value, nullable=False) -> str:
        self.pc += 1
        name = f"$arg{self.pc}"
        self.params_decl.append(f"{name}: {ty}{'?' if nullable else ''}")
        if value is not None:
            self.params[name[1:]] = value
        self.forms.add("param:" + ty.strip("[]").split("(")[0])
        return name

    def edges_from(self, tname):
        return [e for e in self.w.edges if e.src == tname]

    def edges_to(self, tname):
        return [e for e in self.w.edges if e.dst == tname]

    def value_expr(self, p: Prop, sample=None) -> str:
        """A literal or parameter for a value of p's scalar type."""
        ty = p.ty if p.ty != "list" else p.elem
        v = sample if sample is not None else scalar_value(self.rng, ty, p.enum)
        neg = isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0
        if neg or self.rng.random() < 0.25:
            return self.new_param("String" if ty == "enum" else ty, v)
        if ty in ("I32", "I64", "U32", "U64") and self.rng.random() < 0.12:
            self.forms.add("int_float_cmp")
            return lit(float(v), "F64")
        return lit(v, ty)

    def sample_value(self, tname, p: Prop):
        vals = [r["data"].get(p.name) for r in self.w.rows.get(tname, [])]
        vals = [v for v in vals if v is not None]
        if vals and self.rng.random() < 0.7:
            v = self.rng.choice(vals)
            if p.ty == "list":
                return self.rng.choice(v) if v else None
            return v
        return None

    def filter_on(self, var, tname, props, depth=0) -> str | None:
        """A Boolean filter over var's properties."""
        cands = [p for p in props if p.ty not in ("vector", "blob")]
        if not cands:
            return None
        p = self.rng.choice(cands)
        acc = f"{var}.{p.name}"
        r = self.rng.random()
        if p.nullable and r < 0.12:
            self.forms.add("is_null")
            return f"{acc} is {'not ' if self.rng.random() < 0.5 else ''}null"
        if p.ty == "list":
            sv = self.sample_value(tname, p)
            if r < 0.6:
                self.forms.add("contains_list")
                return f"{acc} contains {self.value_expr(p, sv)}"
            self.forms.add("in_list_prop")
            return f"{self.value_expr(p, sv)} in {acc}"
        if p.ty == "Bool":
            if r < 0.4:
                self.forms.add("bare_bool")
                return acc if self.rng.random() < 0.6 else f"not {acc}"
            return f"{acc} = {self.value_expr(p)}"
        sv = self.sample_value(tname, p)
        if p.ty == "String" and r < 0.45:
            op = self.rng.choice(["contains", "starts_with"])
            self.forms.add("contains_str" if op == "contains" else "starts_with")
            sub = sv[: self.rng.randint(0, len(sv))] if isinstance(sv, str) else self.rng.choice(["a", "", "run", "R", "the"])
            return f"{acc} {op} {self.value_expr(p, sub)}"
        if r < 0.62 and p.ty in ("String", "enum", "I32", "I64", "U32", "U64", "Date"):
            self.forms.add("in_literal_list")
            k = self.rng.choice([1, 2, 3])
            vals = [self.sample_value(tname, p) or scalar_value(self.rng, p.ty, p.enum) for _ in range(k)]
            if any(isinstance(v, int) and not isinstance(v, bool) and v < 0 for v in vals):
                return f"{acc} in {self.new_param('[' + ('String' if p.ty == 'enum' else p.ty) + ']', vals)}"
            return f"{acc} in [{', '.join(lit(v, p.ty) for v in vals)}]"
        op = self.rng.choice(["=", "!=", ">", "<", ">=", "<="] if p.ty != "enum" else ["=", "!="])
        if p.ty in ("I64", "U64") and self.rng.random() < 0.3:
            self.forms.add("big_int")
            sv = self.rng.choice(BIG_INTS) if p.ty == "I64" else self.rng.choice([2**53, 2**53 + 1, 2**62])
            if self.rng.random() < 0.5:
                self.forms.add("int_float_cmp")
                return f"{acc} {op} {float(sv)!r}" if float(sv) >= 0 else f"{acc} {op} {self.new_param('F64', float(sv))}"
        return f"{acc} {op} {self.value_expr(p, sv)}"

    def bool_combo(self, var, tname, props):
        a = self.filter_on(var, tname, props)
        if a is None:
            return None
        r = self.rng.random()
        if r < 0.25:
            b = self.filter_on(var, tname, props)
            if b:
                self.forms.add("or")
                return f"{a} or {b}"
        if r < 0.4:
            b = self.filter_on(var, tname, props)
            if b:
                self.forms.add("and")
                return f"{a} and {b}"
        if r < 0.47:
            self.forms.add("not_expr")
            return f"not ({a})"
        return a

    # -- read ----------------------------------------------------------------
    compat = False  # draw only edition-2 texts GQ 3 also parses (gen v2)

    def read(self, qname: str) -> Query:
        if self.compat:
            return self.read_compat(qname)
        rng = self.rng
        self.vc = 0
        self.pc = 0
        self.params = {}
        self.params_decl = []
        self.forms = set()
        clauses = []
        bound = {}       # var -> ("node"|"edge", type name)
        nodes = self.w.nodes
        start = rng.choice(nodes)
        v0 = self.fresh(start.name[0].lower())
        bound[v0] = ("node", start.name)
        # binding, maybe with property braces
        if rng.random() < 0.22:
            props = [p for p in start.props if p.scalar and p.ty != "Bool"]
            if props:
                p = rng.choice(props)
                self.forms.add("prop_braces")
                clauses.append(f"{v0}: {start.name} {{ {p.name}: {self.value_expr(p, self.sample_value(start.name, p))} }}")
            else:
                clauses.append(f"{v0}: {start.name}")
        else:
            clauses.append(f"{v0}: {start.name}")
        # traversals
        nhops = rng.choice([0, 0, 1, 1, 1, 2, 2, 3])
        frontier = [v0]
        hops = 0
        for _ in range(nhops):
            src_var = rng.choice([v for v in frontier if bound[v][0] == "node"])
            tname = bound[src_var][1]
            outs = self.edges_from(tname)
            ins = self.edges_to(tname)
            if not outs and not ins:
                break
            forward = bool(outs) and (not ins or rng.random() < 0.7)
            e = rng.choice(outs if forward else ins)
            other_t = e.dst if forward else e.src
            nv = self.fresh(other_t[0].lower())
            edge_txt = e.ident
            ebind = ""
            if e.props and rng.random() < 0.3:
                ev = self.fresh("e")
                ebind = f"{ev}:"
                bound[ev] = ("edge", e.name)
                self.forms.add("edge_binding")
            bounds = ""
            if e.src == e.dst and rng.random() < 0.25 and not ebind:
                lo = rng.choice([1, 1, 2, 1, 1, 2, 0] if rng.random() < 0.5 else [1, 2])
                hi = rng.choice([lo + 1, lo + 2, lo]) if lo > 0 else rng.choice([1, 2])
                bounds = f"{{{lo},{hi}}}"
                self.forms.add("hop_range")
            if e.src == e.dst and rng.random() < 0.15 and not bounds and not ebind:
                edge_txt = f"<{e.ident}>"
                self.forms.add("undirected")
            par = [x for x in self.w.edges if x.src == e.src and x.dst == e.dst and x is not e]
            if par and rng.random() < 0.2 and not ebind and not edge_txt.startswith("<"):
                edge_txt = f"({e.ident} | {par[0].ident})"
                self.forms.add("alternation")
            declare = rng.random() < 0.5
            if declare:
                clauses.append(f"{nv}: {other_t}")
            else:
                self.forms.add("triple_new_var")
            if forward:
                clauses.append(f"{src_var} {ebind}{edge_txt}{bounds} {nv}")
            else:
                if e.src != e.dst and rng.random() < 0.4:
                    # written against the declared direction (edition 2 resolves by type)
                    self.forms.add("reversed_triple")
                    clauses.append(f"{src_var} {ebind}{edge_txt}{bounds} {nv}")
                else:
                    clauses.append(f"{nv} {ebind}{edge_txt}{bounds} {src_var}")
            bound[nv] = ("node", other_t)
            frontier.append(nv)
            hops += 1
        if hops >= 2:
            self.forms.add("multi_hop")
        # disconnected component
        if rng.random() < 0.08 and len(nodes) >= 1:
            other = rng.choice(nodes)
            dv = self.fresh(other.name[0].lower())
            clauses.append(f"{dv}: {other.name}")
            bound[dv] = ("node", other.name)
            self.forms.add("disconnected")
        node_vars = [v for v, (k, _) in bound.items() if k == "node"]
        # filters in match
        for _ in range(rng.choice([0, 1, 1, 2])):
            v = rng.choice(list(bound))
            k, t = bound[v]
            props = self.w.node(t).props if k == "node" else next(e for e in self.w.edges if e.name == t).props
            f = self.bool_combo(v, t, props)
            if f:
                self.forms.add("filter_in_match")
                clauses.append(f)
        # blocks
        if rng.random() < 0.35:
            clauses.append(self.block(bound))
        # full-text predicates
        fts_vars = [(v, self.w.node(bound[v][1])) for v in node_vars if self.w.node(bound[v][1]).fts]
        if fts_vars and rng.random() < 0.45:
            v, n = rng.choice(fts_vars)
            p = rng.choice(n.fts)
            q = rng.choice(TEXT_QUERIES)
            qexpr = json.dumps(q, ensure_ascii=False) if rng.random() < 0.6 else self.new_param("String", q)
            kind = rng.choice(["search", "match_text", "fuzzy2", "fuzzy3"])
            if kind == "fuzzy3":
                call = f"fuzzy({v}.{p}, {qexpr}, {rng.choice([0, 1, 2, 3])})"
            elif kind == "fuzzy2":
                call = f"fuzzy({v}.{p}, {qexpr})"
            else:
                call = f"{kind}({v}.{p}, {qexpr})"
            self.forms.add(kind)
            if rng.random() < 0.15:
                call += " = true"
            clauses.append(call)
        # return / order / limit
        ret, order, limit, distinct = self.projection(bound, fts_vars)
        sep = "\n    " if rng.random() < 0.85 else "  "
        if rng.random() < 0.3 and len(clauses) > 1:
            self.forms.add("ws_list")
        body_m = sep.join(clauses)
        unused = rng.random() < 0.1
        if unused:
            ty = rng.choice(["String", "I64", "Bool", "Date"])
            self.new_param(ty, scalar_value(rng, ty))
            self.forms.add("unused_param")
        params = ", ".join(self.params_decl)
        text = f"query {qname}({params}) {{\n  match {{\n    {body_m}\n  }}\n  {ret}\n"
        if order:
            text += f"  {order}\n"
        if limit:
            text += f"  {limit}\n"
        text += "}\n"
        if order is None and limit:
            self.forms.add("limit_no_order")
        return Query(qname, text, dict(self.params), "read", set(self.forms))

    def read_compat(self, qname: str) -> Query:
        """A read both editions parse: one binding in `match` (edition 2 has no
        separator between elements, GQ 3 no filter inside the braces), comma
        lists, projections, aggregates, `distinct`, order and limit."""
        rng = self.rng
        self.vc = 0
        self.pc = 0
        self.params = {}
        self.params_decl = []
        self.forms = {"compat"}
        n = rng.choice(self.w.nodes)
        v = self.fresh(n.name[0].lower())
        bound = {v: ("node", n.name)}
        ret, order, limit, distinct = self.projection(bound, [], compat=True)
        params = ", ".join(self.params_decl)
        text = f"query {qname}({params}) {{\n  match {{ {v}: {n.name} }}\n  {ret}\n"
        if order:
            text += f"  {order}\n"
        if limit:
            text += f"  {limit}\n"
        text += "}\n"
        if order is None and limit:
            self.forms.add("limit_no_order")
        return Query(qname, text, dict(self.params), "read", set(self.forms))

    def block(self, bound) -> str:
        rng = self.rng
        node_vars = [v for v, (k, _) in bound.items() if k == "node"]
        v = rng.choice(node_vars)
        t = bound[v][1]
        inner = []
        outs = self.edges_from(t)
        ins = self.edges_to(t)
        nv = None
        if (outs or ins) and rng.random() < 0.8:
            forward = bool(outs) and (not ins or rng.random() < 0.6)
            e = rng.choice(outs if forward else ins)
            ot = e.dst if forward else e.src
            nv = self.fresh(ot[0].lower())
            if rng.random() < 0.5:
                inner.append(f"{nv}: {ot}")
            else:
                self.forms.add("block_triple_new_var")
            inner.append(f"{v} {e.ident} {nv}" if forward else f"{nv} {e.ident} {v}")
            if rng.random() < 0.5:
                f = self.filter_on(nv, ot, self.w.node(ot).props)
                if f:
                    inner.append(f)
                    self.forms.add("block_filter")
        else:
            f = self.filter_on(v, t, self.w.node(t).props)
            if not f:
                f = f"{v}.@id = \"x\""
            inner.append(f)
            nv = None
        sep = rng.choice([" ", "  ", "\n      "])
        body = "{ " + sep.join(inner) + " }"
        r = rng.random()
        if r < 0.4:
            only_filters = nv is None
            self.forms.add("not_filter" if only_filters else "not_pattern")
            return f"not {body}"
        if r < 0.6:
            self.forms.add("exists_block")
            return f"exists {body}"
        if r < 0.85 or nv is None:
            self.forms.add("count_block")
            return f"count {body} {rng.choice(['>', '>=', '=', '<', '!='])} {rng.choice([0, 1, 2])}"
        ot = self.w.node(next(b for b in [self._type_of_var(nv, inner)]))
        nums = [p for p in ot.props if p.ty in NUMERIC]
        if not nums:
            self.forms.add("count_block")
            return f"count {body} > 0"
        p = rng.choice(nums)
        agg = rng.choice(["sum", "avg", "min", "max"])
        self.forms.add("agg_block")
        return f"{agg}({nv}.{p.name}) {body} {rng.choice(['>', '<', '>='])} {rng.choice(['0', '1', '2.5'])}"

    def _type_of_var(self, nv, inner):
        # nv's type from its triple: find which edge connects
        for c in inner:
            parts = c.split()
            if len(parts) == 3 and nv in (parts[0], parts[2]):
                e = next(x for x in self.w.edges if x.ident == parts[1])
                return e.dst if parts[2] == nv else e.src
        raise KeyError(nv)

    def projection(self, bound, fts_vars, compat=False):
        rng = self.rng
        items = []
        aliases = []
        orderable = []     # (expr_or_alias, is_rank)
        node_vars = [v for v, (k, _) in bound.items() if k == "node"]
        agg_mode = rng.random() < 0.25
        distinct = (not agg_mode) and rng.random() < 0.1
        if distinct:
            self.forms.add("distinct")
        used = set()
        n_items = rng.choice([1, 1, 2, 2, 3])
        for _ in range(n_items):
            v = rng.choice(list(bound))
            k, t = bound[v]
            props = self.w.node(t).props if k == "node" else next(e for e in self.w.edges if e.name == t).props
            r = rng.random()
            if r < 0.1 and k == "node":
                expr = v
                self.forms.add("return_node")
            elif r < 0.18:
                expr = f"{v}.@id"
                self.forms.add("meta_id")
            elif r < 0.22 and k == "edge":
                expr = f"{v}.@src"
            else:
                cands = [p for p in props if p.ty not in ("blob",)]
                if not cands:
                    expr = f"{v}.@id"
                else:
                    p = rng.choice(cands)
                    expr = f"{v}.{p.name}"
            if expr in used:
                continue
            used.add(expr)
            if rng.random() < 0.25 and not expr.startswith("$") or False:
                pass
            alias = None
            if rng.random() < 0.3:
                alias = f"c{len(items)}"
            items.append(f"{expr} as {alias}" if alias else expr)
            orderable.append(alias or expr)
        if rng.random() < (0.08 if not agg_mode else 0.015) and (not compat or rng.random() < 0.6):
            # a Boolean return item (needs an alias in edition 2)
            v = rng.choice(node_vars)
            f = self.filter_on(v, bound[v][1], self.w.node(bound[v][1]).props)
            if f:
                items.append(f"{f} as b{len(items)}")
                self.forms.add("bool_item")
        aggs = []
        if agg_mode:
            self.forms.add("aggregate")
            for _ in range(rng.choice([1, 1, 2])):
                v = rng.choice(node_vars)
                t = bound[v][1]
                nums = [p for p in self.w.node(t).props if p.ty in NUMERIC]
                fn = rng.choice(["count", "count", "sum", "avg", "avg", "min", "max", "count_distinct"])
                if fn == "count":
                    a = f"count({v})"
                elif fn == "count_distinct":
                    cands = [p for p in self.w.node(t).props if p.scalar]
                    a = f"count(distinct {v}.{rng.choice(cands).name})" if cands else f"count({v})"
                    self.forms.add("count_distinct")
                elif nums:
                    p = rng.choice(nums)
                    a = f"{fn}({v}.{p.name})"
                    if fn == "avg" and p.ty in ("I64", "U64", "I32", "U32"):
                        self.forms.add("avg_int")
                else:
                    a = f"count({v})"
                al = f"a{len(aggs)}"
                aggs.append(al)
                items.append(f"{a} as {al}")
                orderable.append(al)
            if len(items) == len(aggs) and rng.random() < 0.5:
                pass
        # rankings
        rank_order = None
        vec_vars = [(v, self.w.node(bound[v][1])) for v in node_vars if any(p.ty == "vector" for p in self.w.node(bound[v][1]).props)]
        rk = rng.random() if not compat else 1.0
        rank_expr = None
        if vec_vars and rk < 0.25:
            v, n = rng.choice(vec_vars)
            p = rng.choice([p for p in n.props if p.ty == "vector"])
            vec = [round(rng.uniform(-1, 1), 1) for _ in range(p.dim)]
            q = self.new_param(f"Vector({p.dim})", vec)
            rank_expr = f"nearest({v}.{p.name}, {q})"
            self.forms.add("nearest_order")
        elif fts_vars and rk < 0.45:
            v, n = rng.choice(fts_vars)
            p = rng.choice(n.fts)
            q = rng.choice(TEXT_QUERIES)
            qexpr = json.dumps(q, ensure_ascii=False) if rng.random() < 0.5 else self.new_param("String", q)
            rank_expr = f"bm25({v}.{p}, {qexpr})"
            self.forms.add("bm25_order")
            if vec_vars and rng.random() < 0.6:
                v2, n2 = rng.choice(vec_vars)
                p2 = rng.choice([p for p in n2.props if p.ty == "vector"])
                vec = [round(rng.uniform(-1, 1), 1) for _ in range(p2.dim)]
                q2 = self.new_param(f"Vector({p2.dim})", vec)
                args = [f"nearest({v2}.{p2.name}, {q2})", rank_expr]
                if rng.random() < 0.5:
                    args.reverse()
                rank_expr = f"rrf({', '.join(args)}{', ' + str(rng.choice([1, 60])) if rng.random() < 0.5 else ''})"
                self.forms.discard("bm25_order")
                self.forms.add("rrf")
        if rank_expr and rng.random() < 0.4:
            if rng.random() < 0.5:
                items.append(f"{rank_expr} as r")
                self.forms.add("rank_return_aliased")
            else:
                items.append(rank_expr)
                self.forms.add("rank_return_unaliased")
        if agg_mode and rank_expr:
            self.forms.add("rank_under_aggregate")
        if distinct and rank_expr:
            self.forms.add("rank_under_distinct")
        sep = ", " if (compat or rng.random() < 0.7) else "  "
        if sep == "  " and len(items) > 1:
            self.forms.add("ws_list")
        ret = f"return {'distinct ' if distinct else ''}{{ {sep.join(items)} }}"
        order = None
        if rank_expr:
            keys = [f"{rank_expr}{' desc' if not rank_expr.startswith('nearest') and rng.random() < 0.5 else ''}"]
            if orderable and rng.random() < 0.3:
                keys.append(rng.choice(orderable))
            order = f"order {{ {', '.join(keys)} }}"
        elif orderable and rng.random() < 0.55:
            ks = rng.sample(orderable, rng.choice([1, 1, 2]) if len(orderable) > 1 else 1)
            ks = [k for k in ks if not (k.startswith("$") and "." not in k)]
            if agg_mode and rng.random() < 0.15:
                ks.append(f"{node_vars[0]}.@id")
                self.forms.add("grouped_order_meta")
            if ks:
                order = "order { " + ", ".join(f"{k}{rng.choice(['', ' asc', ' desc'])}" for k in ks) + " }"
                if agg_mode or distinct:
                    self.forms.add("order_grouped")
        limit = None
        if (rank_expr and (rank_expr.startswith("nearest") or rank_expr.startswith("rrf") or rng.random() < 0.85)) or rng.random() < 0.35:
            limit = f"limit {rng.choice([1, 2, 3, 5, 10])}"
            self.forms.add("limit")
        return ret, order, limit, distinct

    # -- write ---------------------------------------------------------------
    def write(self, qname: str) -> Query:
        if self.compat:
            return self.write_compat(qname)
        rng = self.rng
        self.vc = 0
        self.pc = 0
        self.params = {}
        self.params_decl = []
        self.forms = set()
        stmts = []
        n = rng.choice([1, 1, 2, 2, 3])
        mode = rng.choice(["ins", "ins", "upd", "del", "mix", "mix"])
        for _ in range(n):
            kind = rng.choice({"ins": ["insert"], "upd": ["update", "insert"], "del": ["delete"], "mix": ["insert", "update"] if rng.random() < 0.97 else ["insert", "delete"]}[mode])
            s = getattr(self, "w_" + kind)()
            if s:
                stmts.append(s)
        if not stmts:
            s = self.w_insert()
            if not s:
                return None
            stmts.append(s)
        if len(stmts) > 1:
            self.forms.add("multi_statement")
        params = ", ".join(self.params_decl)
        text = f"query {qname}({params}) {{\n" + "".join(f"  {s}\n" for s in stmts) + "}\n"
        return Query(qname, text, dict(self.params), "write", set(self.forms))

    def write_compat(self, qname: str) -> Query:
        """Node inserts with comma-separated assignments, which both editions parse."""
        rng = self.rng
        self.vc = 0
        self.pc = 0
        self.params = {}
        self.params_decl = []
        self.forms = {"compat"}
        stmts = []
        for _ in range(rng.choice([1, 1, 2, 3])):
            n = rng.choice(self.w.nodes)
            parts = []
            reuse = None
            if n.key and self.w.rows[n.name] and rng.random() < 0.3:
                reuse = rng.choice(self.w.rows[n.name])["data"]
                self.forms.add("insert_keyed_existing")
            for p in n.props:
                if p.name in n.key:
                    v = reuse[p.name] if reuse else scalar_value(rng, p.ty, p.enum)
                    if p.ty == "String" and v == "":
                        v = "k"
                    parts.append(f"{p.name}: {self.value_expr(p, v) if not (isinstance(v, int) and v < 0) else self.new_param(p.ty, v)}")
                elif not p.nullable or rng.random() < 0.5:
                    parts.append(f"{p.name}: {self.assign_val(p, n.name)}")
            if not parts:
                p = rng.choice(n.props)
                parts.append(f"{p.name}: {self.assign_val(p, n.name)}")
            self.forms.add("insert_keyed" if n.key else "insert_unkeyed")
            stmts.append(f"insert {n.name} {{ {', '.join(parts)} }}")
        if len(stmts) > 1:
            self.forms.add("multi_statement")
        text = f"query {qname}({', '.join(self.params_decl)}) {{\n" + "".join(f"  {s}\n" for s in stmts) + "}\n"
        return Query(qname, text, dict(self.params), "write", set(self.forms))

    def assign_val(self, p: Prop, tname=None):
        if p.ty == "list":
            vals = [scalar_value(self.rng, p.elem) for _ in range(self.rng.choice([0, 1, 2]))]
            if any(isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0 for v in vals) or self.rng.random() < 0.4:
                return self.new_param(f"[{p.elem}]", vals)
            self.forms.add("list_literal")
            return "[" + ", ".join(lit(v, p.elem) for v in vals) + "]"
        if p.ty == "vector":
            return self.new_param(f"Vector({p.dim})", [round(self.rng.uniform(-1, 1), 1) for _ in range(p.dim)])
        if p.ty == "blob":
            self.forms.add("blob_assign")
            b = self.rng.choice(BLOBS)
            return self.new_param("Blob", b) if self.rng.random() < 0.9 else json.dumps(b)
        if self.rng.random() < 0.1:
            self.forms.add("now_assign")
            if p.ty == "DateTime":
                return "now()"
        return self.value_expr(p, self.sample_value(tname, p) if tname else None)

    def w_insert(self):
        rng = self.rng
        if self.w.edges and rng.random() < 0.3:
            e = rng.choice(self.w.edges)
            srcs = [r["id"] for r in self.w.rows[e.src]]
            dsts = [r["id"] for r in self.w.rows[e.dst]]
            if not srcs or not dsts:
                return None
            parts = [f"from: {json.dumps(rng.choice(srcs) if rng.random() < 0.97 else 'missing')}", f"to: {json.dumps(rng.choice(dsts))}"]
            for p in e.props:
                if not p.nullable or rng.random() < 0.5:
                    parts.append(f"{p.name}: {self.assign_val(p)}")
            self.forms.add("edge_insert")
            return f"insert {e.name} {{ {', '.join(parts)} }}"
        n = rng.choice(self.w.nodes)
        parts = []
        reuse = None
        if n.key and self.w.rows[n.name] and rng.random() < 0.55:
            reuse = rng.choice(self.w.rows[n.name])["data"]
            self.forms.add("insert_keyed_existing")
        for p in n.props:
            if p.name in n.key:
                v = reuse[p.name] if reuse else scalar_value(rng, p.ty, p.enum)
                if p.ty == "String" and v == "":
                    v = "k"
                parts.append(f"{p.name}: {self.value_expr(p, v) if not (isinstance(v, int) and v < 0) else self.new_param(p.ty, v)}")
            elif not p.nullable or rng.random() < 0.5:
                parts.append(f"{p.name}: {self.assign_val(p, n.name)}")
        if not parts:
            p = rng.choice(n.props)
            parts.append(f"{p.name}: {self.assign_val(p, n.name)}")
        self.forms.add("insert_keyed" if n.key else "insert_unkeyed")
        sep = ", " if rng.random() < 0.7 else " "
        if sep == " " and len(parts) > 1:
            self.forms.add("ws_list")
        return f"insert {n.name} {{ {sep.join(parts)} }}"

    def mut_pred(self, props, tname, edge=None):
        rng = self.rng
        cands = [p for p in props if p.scalar or p.ty == "list"]
        r = rng.random()
        if edge is not None and r < 0.4:
            ids = [x["id"] for x in self.w.rows[edge.src]]
            self.forms.add("edge_from_pred")
            return f"from = {json.dumps(rng.choice(ids) if ids else 'x')}"
        if not cands or r < 0.1:
            ids = [x["id"] for x in self.w.rows.get(tname, [])] or ["x"]
            self.forms.add("meta_pred")
            return f"@id = {json.dumps(rng.choice(ids))}"
        p = rng.choice(cands)
        sv = self.sample_value(tname, p) if edge is None else None
        if p.ty == "list":
            return f"{p.name} contains {self.value_expr(p, sv)}"
        if p.nullable and rng.random() < 0.15:
            return f"{p.name} is null"
        op = rng.choice(["=", "=", "!=", ">", "<"] if p.ty not in ("enum", "Bool") else ["=", "!="])
        base = f"{p.name} {op} {self.value_expr(p, sv)}"
        if rng.random() < 0.2:
            q = rng.choice(cands)
            if q.scalar:
                base += f" {rng.choice(['and', 'or'])} {q.name} {'=' if q.ty in ('enum','Bool') else rng.choice(['=', '>', '<'])} {self.value_expr(q, self.sample_value(tname, q) if edge is None else None)}"
        return base

    def w_update(self):
        rng = self.rng
        n = rng.choice(self.w.nodes)
        settable = [p for p in n.props if p.name not in n.key]
        if not settable:
            return None
        ps = rng.sample(settable, min(len(settable), rng.choice([1, 1, 2])))
        sets = ", ".join(f"{p.name}: {self.assign_val(p, n.name)}" for p in ps)
        self.forms.add("update")
        return f"update {n.name} set {{ {sets} }} where {self.mut_pred(n.props, n.name)}"

    def w_delete(self):
        rng = self.rng
        if self.w.edges and rng.random() < 0.3:
            e = rng.choice(self.w.edges)
            self.forms.add("delete_edge")
            return f"delete {e.name} where {self.mut_pred(e.props, e.name, edge=e)}"
        n = rng.choice(self.w.nodes)
        self.forms.add("delete")
        return f"delete {n.name} where {self.mut_pred(n.props, n.name)}"


# --------------------------------------------------------------------------
# Deliberate edition-2 refusals (oracle 5)
# --------------------------------------------------------------------------

REFUSAL_MUTATORS = []


def refusal(fn):
    REFUSAL_MUTATORS.append(fn)
    return fn


@refusal
def upper_keywords(q: Query, rng):
    t = q.text.replace("match {", "MATCH {", 1) if rng.random() < 0.5 else q.text.replace("return ", "RETURN ", 1)
    return t, "mixed_case_keyword"


@refusal
def mutation_keyword(q: Query, rng):
    if q.kind != "write":
        return None
    return q.text.replace("query ", "mutation ", 1), "mutation_keyword"


@refusal
def optional_block(q: Query, rng):
    if q.kind != "read" or "not {" not in q.text and "exists {" not in q.text:
        return None
    return q.text.replace("not {", "optional {", 1).replace("exists {", "optional {", 1), "optional_block"


@refusal
def concat_bars(q: Query, rng):
    if q.kind != "read" or "return {" not in q.text:
        return None
    return q.text.replace("return { ", 'return { "x" || "y" as cc, ', 1), "concat_bars"


@refusal
def not_equal_angle(q: Query, rng):
    if " != " not in q.text:
        return None
    return q.text.replace(" != ", " <> ", 1), "angle_ne"


@refusal
def single_quotes(q: Query, rng):
    import re
    m = re.search(r'"([a-z ]*)"', q.text)
    if not m:
        return None
    return q.text[: m.start()] + "'" + m.group(1) + "'" + q.text[m.end():], "single_quote"


@refusal
def unknown_property(q: Query, rng):
    import re
    m = re.search(r"(\$[a-z]+\d+)\.([a-z_]+)", q.text)
    if not m:
        return None
    return q.text[: m.start(2)] + "nosuch" + q.text[m.end(2):], "unknown_property"


@refusal
def bool_item_no_alias(q: Query, rng):
    import re
    m = re.search(r"(\$[a-z]+\d+)\.([a-z_]+)", q.text)
    if q.kind != "read" or not m:
        return None
    return q.text.replace("return { ", f"return {{ {m.group(0)} is null, ", 1), "bool_item_unaliased"


@refusal
def order_key_not_returned(q: Query, rng):
    import re
    if q.kind != "read" or "order {" in q.text:
        return None
    m = re.search(r"(\$[a-z]+\d+)\.@id", q.text) or re.search(r"(\$[a-z]+\d+):", q.text)
    if not m:
        return None
    v = m.group(1)
    t = q.text.rstrip()
    assert t.endswith("}")
    lines = t[:-1].rstrip().splitlines()
    out = []
    for line in lines:
        if line.strip().startswith("limit"):
            out.append(f"  order {{ {v}.@id desc }}")
        out.append(line)
    if not any(line.strip().startswith("limit") for line in lines):
        out.append(f"  order {{ {v}.@id desc }}")
    return "\n".join(out) + "\n}\n", "order_key_not_returned"


@refusal
def triple_unbound(q: Query, rng):
    import re
    if q.kind != "read":
        return None
    m = re.search(r"\n    (\$[a-z]+\d+) ([a-z][A-Za-z]*) (\$[a-z]+\d+)\n", q.text)
    if not m:
        return None
    a, e, b = m.groups()
    return q.text.replace(m.group(0), f"\n    {a} {e} {b}\n    $zz1 {e} $zz2\n", 1), "unbound_triple"


@refusal
def search_under_not(q: Query, rng):
    import re
    m = re.search(r"\n    ((search|match_text|fuzzy)\([^\n]*\))\n", q.text)
    if not m:
        return None
    return q.text.replace(m.group(0), f"\n    not ({m.group(1)})\n", 1), "search_under_not"


@refusal
def rank_asc(q: Query, rng):
    if "order { bm25(" not in q.text and "order { rrf(" not in q.text:
        return None
    import re
    return re.sub(r"(order \{ (?:bm25|rrf)\(.*?\)\)?)( desc)?( \}|,)", r"\1 asc\3", q.text, count=1), "rank_asc"


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------


COMPAT_SEEDS = 500000  # seeds from here on draw gen v2's compatible texts


def draw_queries(world: World, seed: int, n: int, refuse_share: float = 0.12, write_share: float = 0.22):
    rng = random.Random(seed * 7919 + 17)
    g = QGen(world, rng)
    if seed >= COMPAT_SEEDS:
        g.compat = True
        refuse_share = 0.0
    out = []
    names = ["q", "rows", "people", "find", "count", "nearest", "bm25", "search", "lower", "match_terms", "terms", "from", "distance", "union", "exists", "contains", "now"]
    i = 0
    while len(out) < n:
        i += 1
        qname = f"q{i}"
        if rng.random() < 0.06:
            qname = rng.choice(names[4:])
        kind = "write" if rng.random() < write_share else "read"
        q = g.write(qname) if kind == "write" else g.read(qname)
        if q is None:
            continue
        if qname in names:
            q.forms.add("read_named_function" if kind == "read" else "write_named_function")
        if rng.random() < refuse_share:
            fns = list(REFUSAL_MUTATORS)
            rng.shuffle(fns)
            for fn in fns:
                res = fn(q, rng)
                if res and res[0] != q.text:
                    q.text, tag = res
                    q.forms.add("refuse:" + tag)
                    q.intent = "refuse"
                    break
        out.append(q)
    return out


if __name__ == "__main__":
    import sys
    s = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    w = draw_world(s)
    print(w.schema_text())
    print(w.data_jsonl())
    for q in draw_queries(w, s, int(sys.argv[2]) if len(sys.argv) > 2 else 8):
        print("#", q.kind, q.intent, sorted(q.forms), json.dumps(q.params))
        print(q.text)
