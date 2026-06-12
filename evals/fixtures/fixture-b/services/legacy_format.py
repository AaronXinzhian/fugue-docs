"""
[INPUT]: 依赖 time
[OUTPUT]: 提供 format_date(timestamp) -> str(YYYY-MM-DD)
[POS]: 业务层-旧版格式化工具(待退役,仅 report.py 还在使用)
[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md
"""

import time


def format_date(timestamp):
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))
