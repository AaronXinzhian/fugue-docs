"""Hidden acceptance for the comparison-rate pilot task; copied into the workspace only after the model finishes."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import geb_metrics as m

tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
base = {"schema":"geb.metrics.v1", "run_id":"base", "task":"same", "condition":"baseline",
        "root":str(root), "status":"measured_interval", "git":{"commit":"abc","dirty":False},
        "start":{"model":"test","session_id":"a"}, "usage":{"total_tokens":100}}
fugue = dict(base, run_id="fugue", condition="fugue", start={"model":"test","session_id":"b"},
             usage={"total_tokens":150})
evidence = root / "review.json"
m.save_json(evidence, {"baseline_run_id":"base","fugue_run_id":"fugue","quality_passed":True,
                       "reviewer":"pilot-validator","method":"acceptance assertions"})
assert m.compare_records(base, fugue, evidence)["saving_rate"] == -0.5
base["usage"]["total_tokens"] = 0
assert m.compare_records(base, fugue, evidence)["saving_rate"] is None
try:
    m.compare_records(base, fugue, None)
except ValueError:
    pass
else:
    raise AssertionError("missing quality evidence accepted")
