"""Build the matrix graphs with OLD, one directory per combination.

A combination is (schema, index state, history). Steps run in this order,
since `schema apply` is main-only and the index state is meant to describe
main's head:

1. `init` with the edition-2 base schema, `load --mode merge` the base rows;
2. schema `evolved`: `schema apply` the evolved schema, then write the new
   property and type;
3. the history recipe (branches, merges, proposals, tags, many commits);
4. the index-state recipe on main;
5. history `cleanup`: `cleanup --keep 1 --confirm` last.

Each graph gets `meta.json`: the recipe, every OLD invocation it took, and
the ids a later step needs (commits, branch names, proposals).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from gen import BASE_PG, EVOLVED_PG, Data, write_jsonl
from lib import WORK, og, must, write_json

SCHEMAS = ["base", "evolved"]
INDEX_STATES = ["none", "complete", "tail", "deletes", "optimized", "fts_rebuilt", "lagging"]
HISTORIES = ["one", "many", "branches", "merge", "fork_deleted", "proposal", "tags", "cleanup"]


def mutations_e2(evolved: bool) -> str:
    n = "count" if evolved else "n"
    out = f"""
query h_age($e: String, $a: I32) {{
  update Person set {{ age: $a }} where email = $e
}}
query h_loose($nm: String, $i: I32) {{
  insert Loose {{ name: $nm, {n}: $i }}
}}
query h_person($e: String, $nm: String) {{
  insert Person {{ email: $e, name: $nm }}
}}
query h_title($s: String, $t: String) {{
  update Doc set {{ title: $t }} where slug = $s
}}
query h_doc_text($y: I32, $t: String, $b: String, $v: Vector(8)) {{
  update Doc set {{ title: $t, body: $b, emb: $v }} where year = $y
}}
query h_del_person($e: String) {{
  delete Person where email = $e
}}
query h_del_doc_year($y: I32) {{
  delete Doc where year = $y
}}
query h_del_scalar($s: String) {{
  delete Scalars where slug = $s
}}
query h_knows($a: String, $b: String) {{
  insert Knows {{ from: $a, to: $b, since: 2099 }}
}}
query h_proposal($e: String) as commit {{
  insert Person {{ email: $e, name: "proposed" }}
}}
"""
    if evolved:
        out += """
query h_extra($y: I32) {
  update Doc set { extra: "extra text" } where year = $y
}
query h_added($s: String) {
  insert Added { slug: $s, v: 1 }
}
"""
    return out


class Builder:
    def __init__(self, root: Path, schema: str, index: str, history: str):
        self.root = root
        self.g = root / "graph"
        self.schema, self.index, self.history = schema, index, history
        self.log: list[dict] = []
        self.meta: dict = {"schema": schema, "index": index, "history": history, "ids": {}}
        self.data = Data(seed=7)
        self.mq = root / "h.gq"

    def old(self, *args, what: str = "", allow_fail: bool = False):
        r = og("old", *args, home=self.root / "home")
        self.log.append({"what": what or " ".join(map(str, args[:2])), **r.to_json()})
        if not allow_fail:
            must(r, what or str(args[:3]))
        return r

    def mutate(self, name: str, params: dict | None = None, branch: str | None = None, **kw):
        args = ["mutate", name, "--query", self.mq, "--target", self.g, "--json"]
        if params is not None:
            args += ["--params", json.dumps(params)]
        if branch:
            args += ["--branch", branch]
        return self.old(*args, what=f"mutate {name}", **kw)

    def commit_id(self, r) -> str | None:
        j = r.json() or {}
        c = j.get("commit") or {}
        return c.get("graph_commit_id")

    def build(self) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "base.pg").write_text(BASE_PG)
        (self.root / "evolved.pg").write_text(EVOLVED_PG)
        self.mq.write_text(mutations_e2(self.schema == "evolved"))
        rows = self.data.base_rows()
        write_jsonl(self.root / "base.jsonl", rows)
        self.old("init", "--schema", self.root / "base.pg", self.g, what="init")
        r = self.old("load", "--data", self.root / "base.jsonl", "--mode", "merge", "--target", self.g, "--json", what="load base")
        self.meta["ids"]["first_commit"] = self.commit_id(r)
        if self.schema == "evolved":
            self.old("schema", "apply", "--schema", self.root / "evolved.pg", "--target", self.g, "--json", what="schema apply evolved")
            self.mutate("h_extra", {"y": 1995})
            self.mutate("h_added", {"s": "ad1"})
        # An index pass publishes on main, which expires a pending proposal,
        # so the proposal history runs its recipe after the index state.
        if self.history == "proposal":
            getattr(self, f"i_{self.index}")()
            getattr(self, f"h_{self.history}")()
        else:
            getattr(self, f"h_{self.history}")()
            getattr(self, f"i_{self.index}")()
        if self.history == "cleanup":
            self.old("cleanup", "--keep", "1", "--confirm", "--target", self.g, "--json", what="cleanup keep 1")
        r = self.old("commit", "list", "--target", self.g, "--json", what="commit list")
        self.meta["ids"]["head"] = ((r.json() or {}).get("commits") or [{}])[0].get("graph_commit_id") if r.json() else None
        self.meta["log"] = self.log
        write_json(self.root / "meta.json", self.meta)
        return self.meta

    # ---- histories ----------------------------------------------------
    def h_one(self):
        pass

    def _many(self, n: int = 24):
        for i in range(n):
            if i % 3 == 0:
                self.mutate("h_age", {"e": f"p{i % 40:03d}@example.com", "a": 20 + i})
            elif i % 3 == 1:
                self.mutate("h_loose", {"nm": f"hist{i}", "i": 1000 + i})
            else:
                self.mutate("h_title", {"s": f"d{(i % 70) + 1:05d}", "t": f"history title {i} graph"})

    def h_many(self):
        self._many()

    def h_branches(self):
        g = self.g
        self.old("branch", "create", "live", "--from", "main", "--target", g, "--json")
        self.mutate("h_person", {"e": "live1@example.com", "nm": "Live"}, branch="live")
        self.mutate("h_age", {"e": "p010@example.com", "a": 77}, branch="live")
        self.old("branch", "create", "gone", "--from", "main", "--target", g, "--json")
        r = self.mutate("h_person", {"e": "gone1@example.com", "nm": "Gone"}, branch="gone")
        self.meta["ids"]["gone_commit"] = self.commit_id(r)
        self.old("branch", "delete", "gone", "--target", g, "--json")
        self.old("branch", "create", "again", "--from", "main", "--target", g, "--json")
        self.mutate("h_person", {"e": "again1@example.com", "nm": "Again1"}, branch="again")
        self.old("branch", "delete", "again", "--target", g, "--json")
        self.old("branch", "create", "again", "--from", "main", "--target", g, "--json")
        self.mutate("h_person", {"e": "again2@example.com", "nm": "Again2"}, branch="again")
        self.mutate("h_loose", {"nm": "main-after-branches", "i": 5}, )
        self.meta["ids"]["branches"] = ["main", "live", "again"]

    def h_merge(self):
        g = self.g
        r = self.old("commit", "list", "--target", g, "--json")
        self.old("branch", "create", "feat", "--from", "main", "--target", g, "--json")
        self.mutate("h_person", {"e": "feat1@example.com", "nm": "Feat"}, branch="feat")
        self.mutate("h_title", {"s": "d00003", "t": "feature graph title"}, branch="feat")
        self.mutate("h_age", {"e": "p011@example.com", "a": 66})  # main moves too
        r = self.old("branch", "merge", "feat", "--into", "main", "--delete-branch", "--target", g, "--json", what="merge feat")
        self.meta["ids"]["merge_commit"] = self.commit_id(r) or (r.json() or {}).get("graph_commit_id")
        # an OLD-made base for NEW to merge over: feat2 diverges from main
        self.old("branch", "create", "feat2", "--from", "main", "--target", g, "--json")
        self.mutate("h_person", {"e": "feat2@example.com", "nm": "Feat2"}, branch="feat2")
        self.mutate("h_title", {"s": "d00004", "t": "second feature graph title"}, branch="feat2")
        self.mutate("h_age", {"e": "p012@example.com", "a": 55})
        self.meta["ids"]["branches"] = ["main", "feat2"]

    def h_fork_deleted(self):
        g = self.g
        self.old("branch", "create", "parent", "--from", "main", "--target", g, "--json")
        self.mutate("h_person", {"e": "parent1@example.com", "nm": "Parent"}, branch="parent")
        self.old("branch", "create", "child", "--from", "parent", "--target", g, "--json")
        self.mutate("h_person", {"e": "child1@example.com", "nm": "Child"}, branch="child")
        self.old("branch", "delete", "parent", "--target", g, "--json")
        self.meta["ids"]["branches"] = ["main", "child"]

    def h_proposal(self):
        g = self.g
        r = self.mutate("h_proposal", {"e": "prop-pending@example.com"})
        self.meta["ids"]["proposal_pending"] = self.commit_id(r)
        # a merge proposal from a branch
        self.old("branch", "create", "pr", "--from", "main", "--target", g, "--json")
        self.mutate("h_person", {"e": "pr1@example.com", "nm": "PR"}, branch="pr")
        r = self.old("branch", "merge", "pr", "--into", "main", "--as-commit", "--target", g, "--json", what="merge as commit")
        j = r.json() or {}
        self.meta["ids"]["merge_proposal"] = self.commit_id(r) or j.get("graph_commit_id")
        self.meta["ids"]["branches"] = ["main", "pr"]
        self.meta["merge_proposal_receipt"] = j

    def h_tags(self):
        # A merge leaves the engine's own merge-input tags; a user tag on
        # `__manifest` is written with Lance itself (pylance 13.0.0), since
        # the CLI has no tag command.
        self.h_merge()
        import subprocess
        py = Path("/src/mm/venv/bin/python")
        code = (
            "import lance,sys\n"
            "ds=lance.dataset(sys.argv[1])\n"
            "ds.tags.create('mm_user_tag', ds.version)\n"
            "print(ds.version, ds.tags.list())\n"
        )
        p = subprocess.run([str(py), "-I", "-c", code, str(self.g / "__manifest")], capture_output=True, text=True)
        self.log.append({"what": "pylance tag create", "rc": p.returncode, "out": p.stdout[-2000:], "err": p.stderr[-2000:]})
        if p.returncode != 0:
            raise RuntimeError("tag create failed: " + p.stderr[-800:])
        self._many(4)  # move main past the tagged version

    def h_cleanup(self):
        self._many(12)
        self.old("branch", "create", "kept", "--from", "main", "--target", self.g, "--json")
        self.mutate("h_person", {"e": "kept1@example.com", "nm": "Kept"}, branch="kept")
        self.meta["ids"]["branches"] = ["main", "kept"]

    # ---- index states -------------------------------------------------
    def build_indexes(self):
        r = self.old("index", "build", "--target", self.g, "--json", what="index build")
        self.meta["index_build"] = r.json()

    def tail(self):
        d = Data(seed=99)
        rows = d.scalars(500, 15) + d.docs(500, 20)
        if self.schema == "evolved":  # the evolved schema dropped f32_opt
            for r in rows:
                r["data"].pop("f32_opt", None)
        write_jsonl(self.root / "tail.jsonl", rows)
        self.old("load", "--data", self.root / "tail.jsonl", "--mode", "merge", "--target", self.g, "--json", what="load tail")

    def deletes(self):
        self.mutate("h_del_doc_year", {"y": 1991})
        self.mutate("h_del_scalar", {"s": "s00010"})
        self.mutate("h_del_person", {"e": "p039@example.com"})

    def i_none(self):
        pass

    def i_complete(self):
        self.build_indexes()

    def i_tail(self):
        self.build_indexes()
        self.tail()

    def i_deletes(self):
        self.build_indexes()
        self.deletes()

    def i_optimized(self):
        self.build_indexes()
        self.tail()
        self.deletes()
        r = self.old("optimize", "--target", self.g, "--json", what="optimize")
        self.meta["optimize"] = r.json()

    def i_fts_rebuilt(self):
        self.build_indexes()
        r = self.old("index", "rebuild", "--kind", "full-text", "--target", self.g, "--json", what="index rebuild full-text")
        self.meta["fts_rebuild"] = r.json()

    def i_lagging(self):
        self.build_indexes()
        self.mutate("h_doc_text", {"y": 1993, "t": "lagging graph title", "b": "lagging river body",
                                   "v": [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]})
        self.mutate("h_title", {"s": "d00007", "t": "lagging quantum harbor"})


def combo_name(schema: str, index: str, history: str) -> str:
    return f"{schema}__{index}__{history}"


def build_one(args: tuple) -> dict:
    schema, index, history, out = args
    root = Path(out) / combo_name(schema, index, history)
    if root.exists():
        import shutil
        shutil.rmtree(root)
    try:
        meta = Builder(root, schema, index, history).build()
        return {"name": root.name, "ok": True}
    except Exception as e:  # recorded; the matrix lists a failed build as skipped
        (root / "BUILD_FAILED").write_text(str(e))
        return {"name": root.name, "ok": False, "error": str(e)[-2000:]}


if __name__ == "__main__":
    import concurrent.futures
    import itertools
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else WORK / "graphs"
    combos = [(s, i, h, str(out)) for s, i, h in itertools.product(SCHEMAS, INDEX_STATES, HISTORIES)]
    only = sys.argv[2:] if len(sys.argv) > 2 else None
    if only:
        combos = [c for c in combos if combo_name(*c[:3]) in only]
    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=int(__import__("os").environ.get("MM_JOBS", "8"))) as ex:
        for res in ex.map(build_one, combos):
            print(json.dumps(res)[:400], flush=True)
            results.append(res)
    write_json(out / "build_summary.json", results)
