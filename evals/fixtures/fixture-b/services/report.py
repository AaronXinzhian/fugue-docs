"""
[INPUT]: 依赖 time, storage.store, services.legacy_format
[OUTPUT]: 提供 build_report() -> str
[POS]: 业务层-报表生成
[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md
"""

import time

from storage.store import load_users
from services.legacy_format import format_date


def build_report():
    users = load_users()
    lines = ["Team Roster Report — %s" % format_date(time.time()),
             "total: %d" % len(users), ""]
    for u in users:
        lines.append("#%s  %-12s %s" % (u["id"], u["name"], u["email"]))
    return "\n".join(lines)
