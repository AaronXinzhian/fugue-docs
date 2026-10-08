"""Hidden acceptance for the finish-duration pilot task; copied into the workspace only after the model finishes."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import geb_metrics as m

tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
from unittest.mock import patch
record = m.start_run(root, "duration", root / "ledger", session=root / "missing.jsonl")
record["started_at"] = "2026-09-30T00:00:00+00:00"
m.save_json(m.record_path(root / "ledger", record["run_id"]), record)
with patch.object(m, "now", return_value="2026-09-30T00:01:02.345000+00:00"):
    result = m.finish_run(root / "ledger", record["run_id"])
assert result["elapsed_seconds"] == 62.345, result
assert result["usage"] is None
assert m.finish_run(root / "ledger", record["run_id"]) == result
