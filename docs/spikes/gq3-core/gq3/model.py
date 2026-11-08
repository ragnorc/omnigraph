"""Values, types, schema and the graph snapshot of the GQ 3 core prototype.

Nothing here is engine code. A snapshot is an immutable in-memory graph; a
write returns a new snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional
import copy


class GQError(Exception):
    """A typed refusal or a typed execution error: a code and a message."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BoundViolation(AssertionError):
    """An operator produced more rows than the bound its table row states."""


# ---------------------------------------------------------------- types

INT_KINDS = ("I32", "I64")
FLOAT_KINDS = ("F32", "F64")
NUMERIC = INT_KINDS + FLOAT_KINDS
INT_RANGE = {"I32": (-(2 ** 31), 2 ** 31 - 1), "I64": (-(2 ** 63), 2 ** 63 - 1)}


@dataclass(frozen=True)
class T:
    kind: str                 # I32 I64 F32 F64 Bool String Vector List Node Edge Terms
    arg: Any = None           # Vector: dimension; List: element T; Node/Edge: type name
    nullable: bool = False

    def null(self, b: bool = True) -> "T":
        return replace(self, nullable=b)

    def base(self) -> "T":
        return replace(self, nullable=False)

    @property
    def is_ref(self) -> bool:
        return self.kind in ("Node", "Edge")

    @property
    def is_node(self) -> bool:
        return self.kind == "Node"

    def __str__(self) -> str:
        if self.kind == "Node":
            s = f"N<{self.arg}>"
        elif self.kind == "Edge":
            s = f"E<{self.arg}>"
        elif self.kind == "Vector":
            s = f"Vector({self.arg})"
        elif self.kind == "List":
            s = f"List<{self.arg}>"
        else:
            s = self.kind
        return s + ("?" if self.nullable else "")


I32, I64, F32, F64 = T("I32"), T("I64"), T("F32"), T("F64")
BOOL, STRING = T("Bool"), T("String")


def node_t(name: str, nullable: bool = False) -> T:
    return T("Node", name, nullable)


def edge_t(name: str, nullable: bool = False) -> T:
    return T("Edge", name, nullable)


# ---------------------------------------------------------------- values

@dataclass(frozen=True, order=False)
class NodeRef:
    type: str
    id: Any

    def __repr__(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True, order=False)
class EdgeRef:
    type: str
    id: Any
    src: NodeRef
    dst: NodeRef

    def __repr__(self) -> str:
        return f"{self.type}#{self.id}({self.src.id}->{self.dst.id})"


def value_key(v: Any):
    """A total order on non-null values of one column type.

    Identities order by (canonical type, id) — the rule this spike proposes
    for polymorphic columns, where an id alone is ambiguous.
    """
    if isinstance(v, bool):
        return (0, v)
    if isinstance(v, (int, float)):
        return (1, v)
    if isinstance(v, str):
        return (2, v.encode("utf-8"))
    if isinstance(v, tuple):
        return (3, tuple((0,) if x is None else (1, value_key(x)) for x in v))
    if isinstance(v, NodeRef):
        return (4, v.type, value_key(v.id))
    if isinstance(v, EdgeRef):
        return (5, v.type, value_key(v.id))
    raise TypeError(f"unorderable value {v!r}")


# ---------------------------------------------------------------- schema

@dataclass
class NodeType:
    name: str
    props: dict                 # prop -> T
    key: str
    implements: tuple = ()


@dataclass
class EdgeType:
    name: str
    src: str
    dst: str
    props: dict = field(default_factory=dict)
    card: Optional[tuple] = None


@dataclass
class Schema:
    nodes: dict = field(default_factory=dict)        # name -> NodeType
    edges: dict = field(default_factory=dict)        # lower-case traversal name -> EdgeType
    interfaces: dict = field(default_factory=dict)   # name -> {prop: T}
    # field options: (type, prop) -> {"distance": "l2"|"cosine"|"dot", "fulltext": bool}
    field_opts: dict = field(default_factory=dict)

    def implementors(self, name: str) -> list:
        if name in self.nodes:
            return [name]
        if name in self.interfaces:
            return sorted(n for n, nt in self.nodes.items() if name in nt.implements)
        raise GQError("T1", f"unknown node type {name}")

    def is_node_type(self, name: str) -> bool:
        return name in self.nodes or name in self.interfaces

    def prop_type(self, tname: str, prop: str) -> T:
        if tname in self.nodes:
            props = self.nodes[tname].props
        elif tname in self.interfaces:
            props = self.interfaces[tname]
        elif tname in self.edge_by_type:
            props = self.edge_by_type[tname].props
        else:
            raise GQError("T1", f"unknown type {tname}")
        if prop not in props:
            raise GQError("T27", f"{prop} is not a declared property of {tname}")
        return props[prop]

    @property
    def edge_by_type(self) -> dict:
        return {e.name: e for e in self.edges.values()}

    def field_distance(self, tname: str, prop: str) -> str:
        for impl in self.implementors(tname):
            opt = self.field_opts.get((impl, prop))
            if opt and "distance" in opt:
                return opt["distance"]
        return "l2"

    def is_fulltext(self, tname: str, prop: str) -> bool:
        return any(self.field_opts.get((impl, prop), {}).get("fulltext")
                   for impl in self.implementors(tname))


# ---------------------------------------------------------------- graph

@dataclass
class Edge:
    id: Any
    type: str
    src: Any
    dst: Any
    props: dict = field(default_factory=dict)


class Graph:
    """One snapshot. Nodes keyed by (type, key); edges by type."""

    def __init__(self, schema: Schema):
        self.schema = schema
        self.nodes: dict = {name: {} for name in schema.nodes}    # type -> id -> props
        self.edges: dict = {e.name: [] for e in schema.edges.values()}
        self._next_edge = 0
        self._adj_cache = None

    def copy(self) -> "Graph":
        g = Graph.__new__(Graph)
        g.schema = self.schema
        g.nodes = copy.deepcopy(self.nodes)
        g.edges = {k: [replace(e, props=dict(e.props)) for e in v] for k, v in self.edges.items()}
        g._next_edge = self._next_edge
        g._adj_cache = None
        return g

    def add_node(self, tname: str, **props) -> NodeRef:
        nt = self.schema.nodes[tname]
        key = props[nt.key]
        self.nodes[tname][key] = dict(props)
        self._adj_cache = None
        return NodeRef(tname, key)

    def add_edge(self, ename: str, src: Any, dst: Any, eid: Any = None, **props) -> EdgeRef:
        et = self.schema.edges[ename] if ename in self.schema.edges else self.schema.edge_by_type[ename]
        if eid is None:
            self._next_edge += 1
            eid = f"e{self._next_edge}"
        self.edges[et.name].append(Edge(eid, et.name, src, dst, dict(props)))
        self._adj_cache = None
        return EdgeRef(et.name, eid, NodeRef(et.src, src), NodeRef(et.dst, dst))

    def node_count(self, tname: str) -> int:
        return sum(len(self.nodes[i]) for i in self.schema.implementors(tname))

    def all_nodes(self, tname: str) -> list:
        out = []
        for impl in self.schema.implementors(tname):
            out.extend(NodeRef(impl, k) for k in self.nodes[impl])
        return out

    def props(self, ref) -> Optional[dict]:
        if isinstance(ref, NodeRef):
            return self.nodes.get(ref.type, {}).get(ref.id)
        if isinstance(ref, EdgeRef):
            for e in self.edges.get(ref.type, []):
                if e.id == ref.id:
                    return e.props
            return None
        raise GQError("T", f"property access on non-reference {ref!r}")

    def edge_ref(self, e: Edge) -> EdgeRef:
        et = self.schema.edge_by_type[e.type]
        return EdgeRef(e.type, e.id, NodeRef(et.src, e.src), NodeRef(et.dst, e.dst))

    def adjacency(self):
        """(edge type, direction) -> {node ref: [(neighbour ref, edge ref)]}."""
        if self._adj_cache is None:
            adj = {}
            for et in self.schema.edges.values():
                fwd, rev = {}, {}
                for e in self.edges[et.name]:
                    er = self.edge_ref(e)
                    fwd.setdefault(er.src, []).append((er.dst, er))
                    rev.setdefault(er.dst, []).append((er.src, er))
                adj[(et.name, "out")] = fwd
                adj[(et.name, "in")] = rev
            self._adj_cache = adj
        return self._adj_cache

    def fingerprint(self):
        return (tuple(sorted((t, tuple(sorted((k, tuple(sorted(p.items(), key=lambda kv: kv[0]))) for k, p in ns.items())))
                             for t, ns in self.nodes.items())),
                tuple(sorted((t, tuple(sorted((e.id, e.src, e.dst) for e in es))) for t, es in self.edges.items())))
