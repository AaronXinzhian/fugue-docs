"""
[INPUT]: 依赖 re
[OUTPUT]: 提供 validate_email(email) -> bool
[POS]: 工具层-通用校验
[PROTOCOL]: 变更时更新此头部,然后检查上级 PROJECT_INDEX.md
"""

import re

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")


def validate_email(email):
    return bool(_EMAIL_RE.match(email or ""))
