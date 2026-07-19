"""后端运维命令入口。"""
from __future__ import annotations

import argparse

from app.core.rbac import initialize_rbac
from app.database.session import SessionLocal
from app.services.admin_bootstrap_service import (
    interactive_terminal_stream,
    print_admin_credentials,
    recover_admin,
)


def _recover_admin(username: str | None) -> int:
    stream = interactive_terminal_stream()
    if stream is None:
        raise RuntimeError("管理员恢复命令只能在交互式终端中运行")

    db = SessionLocal()
    try:
        initialize_rbac(db)
        credentials = recover_admin(db, username)
    finally:
        db.close()
    print_admin_credentials(credentials, stream)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fabric Defect Detection 运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    recover_parser = subparsers.add_parser(
        "recover-admin",
        help="恢复管理员权限并生成新的临时密码",
    )
    recover_parser.add_argument(
        "--username",
        help="指定要恢复或提升为管理员的用户名；只有一个管理员时可省略",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "recover-admin":
            return _recover_admin(args.username)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
