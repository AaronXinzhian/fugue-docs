"""
[INPUT]: 依赖 json, os
[OUTPUT]: 提供 load_users() -> list, save_users(users), DATA_PATH
[POS]: 持久化层-JSON 文件读写
[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md
"""

import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), "users.json")


def load_users():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_users(users):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
