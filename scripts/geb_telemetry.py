#!/usr/bin/env python3
"""
[INPUT]: 依赖 contextlib, json, os, pathlib, sqlite3, uuid
[OUTPUT]: 提供只读 Codex 会话定位、绑定诊断与数值遥测快照
[POS]: fugue-docs 工具层-隔离会话日志格式与分页索引兼容逻辑
[PROTOCOL]: 修改解析与发现规则时补充 evals/test_metrics.py
"""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import uuid

COUNTERS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")


def codex_home():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def session_identity(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            for _ in range(10):
                line = stream.readline()
                if not line:
                    break
                event = json.loads(line)
                if event.get("type") == "session_meta":
                    payload = event.get("payload", {})
                    return {"session_id": payload.get("id"), "cwd": payload.get("cwd")}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def resolve_session(session=None, session_id=None):
    """Prefer the indexed current page, never the newest unrelated session."""
    if session:
        path = Path(session).expanduser().resolve()
        identity = session_identity(path)
        return {"status": "bound" if identity else "explicit_source_unavailable",
                "method": "explicit", "path": str(path),
                "session_id": (identity or {}).get("session_id")}
    session_id = session_id or os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    result = {"status": "missing_session_id", "method": None, "path": None,
              "session_id": session_id}
    if not session_id:
        return result
    try:
        uuid.UUID(session_id)
    except (ValueError, TypeError):
        result["status"] = "invalid_session_id"
        return result
    home = codex_home().resolve()
    databases = sorted((p for p in home.glob("state_*.sqlite")
                        if p.stem.removeprefix("state_").isdigit()),
                       key=lambda p: int(p.stem.removeprefix("state_")), reverse=True)
    for database in databases:
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
                row = conn.execute("SELECT rollout_path FROM threads WHERE id = ?", (session_id,)).fetchone()
            if row:
                path = Path(row[0]).resolve()
                allowed = any(parent in path.parents for parent in (home / "sessions", home / "archived_sessions"))
                identity = session_identity(path) if allowed else None
                if identity and identity["session_id"] == session_id:
                    return dict(result, status="bound", method="codex_index", path=str(path))
                return dict(result, status="indexed_source_unavailable", method="codex_index")
        except (sqlite3.Error, OSError, ValueError):
            continue
    matches = set()
    for folder in ("sessions", "archived_sessions"):
        for path in (home / folder).glob("**/*" + session_id + "*.jsonl"):
            if (session_identity(path) or {}).get("session_id") == session_id:
                matches.add(path.resolve())
    result["candidate_count"] = len(matches)
    if len(matches) == 1:
        return dict(result, status="bound", method="unique_metadata_match", path=str(matches.pop()))
    result["status"] = "ambiguous_session_pages" if matches else "session_not_found"
    return result


def find_session(session_id):
    binding = resolve_session(session_id=session_id) if session_id else {}
    return Path(binding["path"]) if binding.get("status") == "bound" else None


def session_snapshot(path):
    """Read numeric telemetry only. Prompts and responses never enter the ledger."""
    if path is None or not Path(path).is_file():
        return None
    session_id, cwd, model, effort, provider, latest = None, None, None, None, None, None
    model_epoch, counter_epoch = 0, 0
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if event.get("type") == "session_meta":
                session_id, cwd = payload.get("id"), payload.get("cwd")
                provider = payload.get("model_provider")
            if event.get("type") == "turn_context":
                new_model = payload.get("model", model)
                new_effort = payload.get("effort", payload.get("reasoning_effort", effort))
                if model and (new_model, new_effort) != (model, effort):
                    model_epoch += 1
                model, effort = new_model, new_effort
            if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            usage = (payload.get("info") or {}).get("total_token_usage")
            if not usage:
                continue
            if (not all(isinstance(usage.get(k), int) and not isinstance(usage[k], bool)
                        and usage[k] >= 0 for k in COUNTERS)
                    or usage["cached_input_tokens"] > usage["input_tokens"]
                    or usage["input_tokens"] + usage["output_tokens"] != usage["total_tokens"]):
                raise ValueError("invalid_telemetry_counters")
            if latest and any(usage[k] < latest["usage"][k] for k in COUNTERS):
                counter_epoch += 1
            latest = {"session_id": session_id, "cwd": cwd, "model": model,
                      "reasoning_effort": effort, "model_provider": provider,
                      "model_epoch": model_epoch, "counter_epoch": counter_epoch,
                      "timestamp": event.get("timestamp"), "source": str(Path(path).resolve()),
                      "usage": {k: usage[k] for k in COUNTERS}}
    return latest


def observe(session=None, session_id=None):
    binding = resolve_session(session, session_id)
    snapshot = None
    if binding["status"] == "bound":
        try:
            snapshot = session_snapshot(binding["path"])
            if snapshot is None:
                binding["status"] = "no_token_events"
            elif not snapshot.get("model") or not snapshot.get("session_id"):
                binding["status"] = "missing_telemetry_identity"
                snapshot = None
        except (OSError, ValueError) as error:
            binding["status"] = "telemetry_unreadable"
            binding["error"] = str(error)
    return binding, snapshot
