"""Portable wrapper; frozen evaluator itself is unchanged."""
import json,sys
from pathlib import Path
import evaluate
if len(sys.argv)!=3:
 raise SystemExit("python3.12 harness/check-delivery.py deliveries/codex/1800 /tmp/zeka-delivery-check")
code=Path(sys.argv[1]).resolve(); output=Path(sys.argv[2]).resolve()
evaluate.PY=sys.executable
payload=json.dumps({n:(code/n).read_text() for n in ['app.py','index.html','README.md']})
print(json.dumps(evaluate.evaluate(payload,output/'code'),ensure_ascii=False,indent=2))
