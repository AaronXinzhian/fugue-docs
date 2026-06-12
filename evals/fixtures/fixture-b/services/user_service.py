"""
[INPUT]: 依赖 storage.store, utils
[OUTPUT]: 提供 add_user(name, email), list_users(), remove_user(user_id)
[POS]: 业务层-用户管理
[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md
"""

from storage.store import load_users, save_users
from utils import validate_email


def add_user(name, email):
    if not validate_email(email):
        raise ValueError("invalid email: %s" % email)
    users = load_users()
    next_id = max((int(u["id"]) for u in users), default=0) + 1
    user = {"id": str(next_id), "name": name, "email": email}
    users.append(user)
    save_users(users)
    return user


def list_users():
    return load_users()


def remove_user(user_id):
    users = load_users()
    remaining = [u for u in users if u["id"] != str(user_id)]
    if len(remaining) == len(users):
        return False
    save_users(remaining)
    return True
