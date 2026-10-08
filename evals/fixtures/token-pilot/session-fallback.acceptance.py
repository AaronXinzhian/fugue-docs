"""Hidden acceptance for the session-fallback pilot task; copied into the workspace only after the model finishes."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import geb_metrics as m

tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
from unittest.mock import patch
import os
seen = []
with patch.dict(os.environ, {"CODEX_THREAD_ID": "", "CODEX_SESSION_ID": "fallback"}):
    with patch.object(m, "find_session", side_effect=lambda value: seen.append(value)):
        m.start_run(root, "fallback", root / "ledger1")
assert seen == ["fallback"], seen
seen.clear()
with patch.dict(os.environ, {"CODEX_THREAD_ID": "primary", "CODEX_SESSION_ID": "fallback"}):
    with patch.object(m, "find_session", side_effect=lambda value: seen.append(value)):
        m.start_run(root, "primary", root / "ledger2")
assert seen == ["primary"], seen
seen.clear()
with patch.object(m, "find_session", side_effect=lambda value: seen.append(value)):
    m.start_run(root, "explicit", root / "ledger3", session=root / "absent.jsonl")
assert not seen
