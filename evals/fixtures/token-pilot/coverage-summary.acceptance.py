"""Hidden acceptance for the coverage-summary pilot task; copied into the workspace only after the model finishes."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import geb_metrics as m

tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
ledger = root / "ledger"
for i, status in enumerate(["active", "missing_measurements", "measured_interval"]):
    m.save_json(ledger / (str(i) + ".json"), {"schema":"geb.metrics.v1", "run_id":str(i),
        "condition":"fugue", "status":status, "usage":{"total_tokens":10} if i == 2 else None})
r = m.summarize(ledger)
assert r["finished_runs"] == 2 and r["measurement_coverage"] == 0.5, r
assert r["status_counts"] == {"active":1,"missing_measurements":1,"measured_interval":1}, r
assert r["actual_total_tokens"] == 10
assert m.summarize(root / "empty")["measurement_coverage"] is None
