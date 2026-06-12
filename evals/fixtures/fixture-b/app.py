"""
[INPUT]: 依赖 sys, services.user_service, services.report
[OUTPUT]: 提供 main() 命令行入口(add / list / remove / report 命令)
[POS]: 入口层-命令分发
[PROTOCOL]: 变更时更新此头部,然后检查上级 PROJECT_INDEX.md
"""

import sys

from services.user_service import add_user, list_users, remove_user
from services.report import build_report


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: app.py [add <name> <email> | list | remove <id> | report]",
              file=sys.stderr)
        return 1
    cmd = args[0]
    if cmd == "add" and len(args) == 3:
        user = add_user(args[1], args[2])
        print("added #%s %s" % (user["id"], user["name"]))
    elif cmd == "list":
        for u in list_users():
            print("#%s %s <%s>" % (u["id"], u["name"], u["email"]))
    elif cmd == "remove" and len(args) == 2:
        ok = remove_user(args[1])
        print("removed" if ok else "not found")
    elif cmd == "report":
        print(build_report())
    else:
        print("unknown command: %s" % cmd, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
