"""Replay a saved (shrunk) case and print its findings.

    python ufreplay.py ../repro/NAME.json [--keep]

Uses the binaries in $UF_BIN (default /src/uf/bin/{rel,main,new}/omnigraph).
"""

import json
import sys
from pathlib import Path

import ufcommon as C
import ufgen as G
import ufcase as U

saved = json.loads(Path(sys.argv[1]).read_text())
cfg = G.Config.from_json(saved["cfg"])
cls = U.Case
if saved.get("alternate"):
    import ufmixed
    cls = ufmixed.Alternate
root = C.WORK / "replay" / Path(sys.argv[1]).stem
rec = cls(saved["seed"], cfg=cfg, script=saved["script"], root=root).run()
print("schema:\n" + G.render(cfg))
print("script:", json.dumps(saved["script"], ensure_ascii=False))
print("status:", rec["status"], rec.get("error") or "")
for f in rec["findings"]:
    print(f"FINDING oracle {f['oracle']} {f['kind']}: {f['sig']} at {f['where']}")
    print("  " + json.dumps(f["detail"], ensure_ascii=False, default=str)[:2000])
hit = any(f["kind"] == saved["kind"] and f["sig"].startswith(saved["sig_prefix"]) for f in rec["findings"])
print("REPRODUCED" if hit else "NOT REPRODUCED", saved["kind"], saved["sig_prefix"])
sys.exit(0 if hit else 1)
