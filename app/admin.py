"""管理员账号命令：建立唯一的管理员账号、重设它的密码。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条（单一管理员也须服务端授权
与会话到期）与第 6 条（应用日志不记录密码），以及 docs/HANDOFF.md 记录的 Kelvin 2026-10-04
管理员登录决定：唯一的管理员账号由运营者在服务器上于 API 容器内运行本命令创建，密码以不回显
的方式输入，不经命令行参数或环境变量。

用法（运营者在服务器上于 shop_api 容器内运行，例如在本仓库检出的根目录里
docker compose exec shop_api python -m app.admin create <用户名>）：
    python -m app.admin create <用户名>    建立唯一的管理员账号；库里已有任何账号时拒绝
    python -m app.admin reset-password     重设唯一账号的密码并撤销它的全部后台会话

两者都以不回显的方式读两次密码，不一致、少于 12 个字符或多于 256 个字符时拒绝。用户名须为
3 到 32 个小写字母、数字或下划线。成功时写对应的审计记录（admin_account_created /
admin_password_reset）并提交。

库里至多一个管理员账号：create 先检查库里没有账号再读密码；两个 create 并发时，后插入的
一个撞上账号表单例槽的唯一约束（SHOP-TASK-034），回滚后以同样的方式拒绝。

数据库连接沿用 SHOP_DATABASE_URL 与 app/db/session.py 的会话工厂。退出码：成功 0，被拒绝或
失败 1，未配置数据库 2。输出与退出码不含密码或哈希；数据库出错时只输出异常类名（异常消息里
可能带着写入的哈希）。
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import NoReturn, TextIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import _session_factory
from app.models.admin import USERNAME_MAX_LENGTH, USERNAME_MIN_LENGTH, AdminAccount
from app.services.admin_auth import (
    ADMIN_ACCOUNT_CREATED,
    ADMIN_PASSWORD_RESET,
    record_audit,
    revoke_all_admin_sessions,
)
from app.services.pw_hash import hash_password

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256

USERNAME_PATTERN = re.compile(rf"[a-z0-9_]{{{USERNAME_MIN_LENGTH},{USERNAME_MAX_LENGTH}}}")

# 退出码：成功 0，被拒绝或失败 1，未配置数据库 2。
EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_NOT_CONFIGURED = 2

SessionFactory = Callable[[], Session]
PasswordReader = Callable[[str], str]


class _Rejected(Exception):
    """命令被拒绝：消息是给运营者看的固定文字，不含密码或哈希。"""


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def read_password(prompt: str) -> str:
    """不回显地读一行密码（getpass）。测试换成自己的函数。"""
    return getpass.getpass(prompt)


def _read_new_password(reader: PasswordReader) -> str:
    """读两次密码并检查：两次一致，长度 12 到 256 个字符。"""
    try:
        first = reader("New admin password: ")
        second = reader("Repeat the password: ")
    except (EOFError, KeyboardInterrupt):
        raise _Rejected("no password entered") from None
    if first != second:
        raise _Rejected("passwords do not match")
    if not PASSWORD_MIN_LENGTH <= len(first) <= PASSWORD_MAX_LENGTH:
        raise _Rejected(
            f"password must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters"
        )
    return first


def _hash(password: str) -> str:
    try:
        return hash_password(password)
    except ValueError:
        raise _Rejected("password must be valid text") from None


def _the_account(db: Session) -> AdminAccount | None:
    return db.scalars(select(AdminAccount).order_by(AdminAccount.id)).first()


def _create(
    session_factory: SessionFactory,
    username: str,
    reader: PasswordReader,
    now: Callable[[], datetime],
) -> str:
    if not USERNAME_PATTERN.fullmatch(username):
        raise _Rejected(
            f"username must be {USERNAME_MIN_LENGTH} to {USERNAME_MAX_LENGTH}"
            " lowercase letters, digits or underscores"
        )
    # 先在一个短会话里检查，不在等运营者输入密码时占着事务。
    with session_factory() as db:
        if _the_account(db) is not None:
            raise _Rejected("an admin account already exists")
    password_hash = _hash(_read_new_password(reader))

    with session_factory() as db:
        moment = now()
        account = AdminAccount(
            username=username,
            password_hash=password_hash,
            created_at=moment,
            password_updated_at=moment,
        )
        db.add(account)
        try:
            db.flush()
        except IntegrityError:
            # 并发的另一个 create 先插入了：单例槽（或用户名）唯一约束拒绝。
            db.rollback()
            raise _Rejected("an admin account already exists") from None
        record_audit(db, ADMIN_ACCOUNT_CREATED, account.id, moment)
        db.commit()
    return "admin account created"


def _reset_password(
    session_factory: SessionFactory,
    reader: PasswordReader,
    now: Callable[[], datetime],
) -> str:
    with session_factory() as db:
        if _the_account(db) is None:
            raise _Rejected("no admin account exists")
    password_hash = _hash(_read_new_password(reader))

    with session_factory() as db:
        account = _the_account(db)
        if account is None:
            raise _Rejected("no admin account exists")
        moment = now()
        account.password_hash = password_hash
        account.password_updated_at = moment
        revoked = revoke_all_admin_sessions(db, account.id, moment)
        record_audit(db, ADMIN_PASSWORD_RESET, account.id, moment)
        db.commit()
    return f"admin password reset; {revoked} session(s) revoked"


def _run(
    action: Callable[[SessionFactory], str],
    session_factory: SessionFactory | None,
    out: TextIO | None,
    err: TextIO | None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    if session_factory is None:
        print("database is not configured (SHOP_DATABASE_URL)", file=err)
        return EXIT_NOT_CONFIGURED
    try:
        message = action(session_factory)
    except _Rejected as exc:
        print(str(exc), file=err)
        return EXIT_REJECTED
    except SQLAlchemyError as exc:
        # 只输出异常类名：消息与参数里可能有连接串或刚算出的哈希。
        print(f"database error: {type(exc).__name__}", file=err)
        return EXIT_REJECTED
    print(message, file=out)
    return EXIT_OK


def create_admin(
    session_factory: SessionFactory | None,
    username: str,
    *,
    reader: PasswordReader | None = None,
    now: Callable[[], datetime] = utc_now,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """建立唯一的管理员账号，返回退出码。库里已有任何账号时拒绝（不读密码、不写入）。

    reader 为空时用 read_password（getpass，不回显）。
    """
    read = reader or read_password
    return _run(lambda factory: _create(factory, username, read, now), session_factory, out, err)


def reset_admin_password(
    session_factory: SessionFactory | None,
    *,
    reader: PasswordReader | None = None,
    now: Callable[[], datetime] = utc_now,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """重设唯一账号的密码、更新密码更新时间并撤销它的全部后台会话，返回退出码。

    库里没有账号时以非零退出。reader 为空时用 read_password（getpass，不回显）。
    """
    read = reader or read_password
    return _run(lambda factory: _reset_password(factory, read, now), session_factory, out, err)


def configured_session_factory() -> SessionFactory | None:
    """按 SHOP_DATABASE_URL 取会话工厂；未配置为空。引擎在第一次开会话时才建。"""
    database_url = get_settings().database_url
    if not database_url:
        return None
    return lambda: _session_factory(database_url)()


class _ArgumentParser(argparse.ArgumentParser):
    """出错时不回显命令行内容：运营者若误把密码写进命令行，它不会再被打印出来。

    子命令的解析器沿用这个类（add_subparsers 默认取父解析器的类）。
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: invalid arguments (see --help)\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="python -m app.admin",
        description="Create the single admin account or reset its password.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create the single admin account")
    create.add_argument("username", help="3-32 lowercase letters, digits or underscores")
    commands.add_parser(
        "reset-password", help="reset the admin password and revoke all admin sessions"
    )
    args = parser.parse_args(argv)

    session_factory = configured_session_factory()
    if args.command == "create":
        return create_admin(session_factory, args.username)
    return reset_admin_password(session_factory)


if __name__ == "__main__":
    sys.exit(main())
