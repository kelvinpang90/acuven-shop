"""管理员账号命令（app/admin.py）：create 与 reset-password。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条（「单一管理员也须服务端授权
与会话到期」）与第 6 条（「应用日志……不记录……密码」），以及 docs/HANDOFF.md 记录的
Kelvin 2026-10-04 管理员登录决定（下称「Kelvin 2026-10-04」）：「唯一的管理员账号由运营者在
服务器上于 API 容器内运行命令创建，密码以不回显的方式输入，不经命令行参数或环境变量」「库里
至多一个管理员账号，由账号表的单例槽唯一约束保证」「本轮审计只记……建账号、重设密码」。
每条测试的文档字符串写明它守住的设计原句或 Kelvin 的哪一项决定；没有直接原句的，写明守住的
是 SHOP-TASK-035 验收的哪一条。

读密码函数换成测试里按顺序回答的替身，会话工厂换成 SQLite 内存库（StaticPool，每个连接打开
外键检查）上的会话；命令的输出写进 StringIO，断言其中不含密码或哈希。
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import admin
from app.core.config import Settings
from app.db.base import Base
from app.models import AdminAccount, AdminSession, AuditEvent
from app.services.admin_auth import check_admin_session, issue_admin_session
from app.services.pw_hash import hash_password, verify_password

NOW = datetime(2026, 10, 6, 3, 0)
EARLIER = NOW - timedelta(days=3)
USERNAME = "shop_admin"
PASSWORD = "a long admin passphrase"
NEW_PASSWORD = "another long passphrase"
OLD_HASH = hash_password("the old admin passphrase")
SECRET = "sentinel-connection-detail"


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：内存库在各个会话之间始终是同一个连接，命令开的几个会话看到同一份数据。
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> Callable[[], Session]:
    return lambda: Session(engine)


class Reader:
    """读密码函数的替身：按顺序给出答案，记下提示；答案是异常时抛出它。"""

    def __init__(self, *answers: object, on_call: Callable[[int], None] | None = None) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.on_call = on_call

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.on_call is not None:
            self.on_call(len(self.prompts))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, str)
        return answer


class Output:
    def __init__(self) -> None:
        self.out = io.StringIO()
        self.err = io.StringIO()

    @property
    def text(self) -> str:
        return self.out.getvalue() + self.err.getvalue()


def _create(factory: Callable[[], Session], username: str, reader: Reader, output: Output) -> int:
    return admin.create_admin(
        factory, username, reader=reader, now=lambda: NOW, out=output.out, err=output.err
    )


def _reset(factory: Callable[[], Session], reader: Reader, output: Output) -> int:
    return admin.reset_admin_password(
        factory, reader=reader, now=lambda: NOW, out=output.out, err=output.err
    )


def _accounts(engine: Engine) -> list[AdminAccount]:
    with Session(engine) as db:
        return list(db.scalars(select(AdminAccount).order_by(AdminAccount.id)))


def _events(engine: Engine) -> list[tuple]:
    with Session(engine) as db:
        rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id))
        return [
            (
                r.occurred_at,
                r.admin_account_id,
                r.action,
                r.target_type,
                r.target_id,
                r.old_value,
                r.new_value,
            )
            for r in rows
        ]


def _existing_account(engine: Engine, username: str = USERNAME) -> int:
    with Session(engine) as db:
        account = AdminAccount(
            username=username,
            password_hash=OLD_HASH,
            created_at=EARLIER,
            password_updated_at=EARLIER,
        )
        db.add(account)
        db.commit()
        return account.id


def _assert_no_secrets(output: Output, *passwords: str) -> None:
    text_value = output.text
    for password in passwords:
        assert password not in text_value
    for account_hash in [OLD_HASH, "scrypt$", "n=16384"]:
        assert account_hash not in text_value


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_writes_account_and_audit_event(
    engine: Engine, factory: Callable[[], Session]
) -> None:
    """守住 Kelvin 2026-10-04「唯一的管理员账号由运营者……运行命令创建，密码以不回显的方式输入」
    与「本轮审计只记……建账号」：读两次密码，写入账号（哈希能校验该密码，创建与密码更新时间
    为当前时间）与一条 admin_account_created 审计记录并提交，以 0 退出，输出不含密码或哈希。"""
    reader = Reader(PASSWORD, PASSWORD)
    output = Output()

    assert _create(factory, USERNAME, reader, output) == admin.EXIT_OK

    assert len(reader.prompts) == 2
    (account,) = _accounts(engine)
    assert account.username == USERNAME
    assert account.singleton_slot == 1
    assert verify_password(PASSWORD, account.password_hash)
    assert account.created_at == NOW
    assert account.password_updated_at == NOW
    assert _events(engine) == [(NOW, account.id, "admin_account_created", None, None, None, None)]
    assert "admin account created" in output.out.getvalue()
    _assert_no_secrets(output, PASSWORD)
    assert account.password_hash not in output.text


def test_create_rejected_when_an_account_exists(
    engine: Engine, factory: Callable[[], Session]
) -> None:
    """守住「单一管理员」与 Kelvin 2026-10-04「库里至多一个管理员账号」：库里已有任何账号时
    （用户名不同也一样）拒绝、非零退出，不读密码、不写入。"""
    existing = _existing_account(engine, "first_admin")
    reader = Reader(PASSWORD, PASSWORD)
    output = Output()

    assert _create(factory, USERNAME, reader, output) == admin.EXIT_REJECTED

    assert reader.prompts == []
    assert [a.id for a in _accounts(engine)] == [existing]
    assert _accounts(engine)[0].password_hash == OLD_HASH
    assert _events(engine) == []
    assert "already exists" in output.err.getvalue()


def test_create_rejected_on_singleton_slot_conflict(
    engine: Engine, factory: Callable[[], Session]
) -> None:
    """守住 Kelvin 2026-10-04「库里至多一个管理员账号，由账号表的单例槽唯一约束保证」与验收
    「并发时插入遇唯一约束冲突时回滚并以同样的方式拒绝」：检查时库里还没有账号，读密码期间
    另一个 create 提交了账号；本次插入撞上单例槽唯一约束，回滚、拒绝，不写审计记录。"""

    def concurrent_create(call: int) -> None:
        if call == 1:
            _existing_account(engine, "other_admin")

    reader = Reader(PASSWORD, PASSWORD, on_call=concurrent_create)
    output = Output()

    assert _create(factory, USERNAME, reader, output) == admin.EXIT_REJECTED

    assert len(reader.prompts) == 2
    assert [a.username for a in _accounts(engine)] == ["other_admin"]
    assert _events(engine) == []
    assert "already exists" in output.err.getvalue()
    _assert_no_secrets(output, PASSWORD)


@pytest.mark.parametrize(
    ("first", "second", "message"),
    [
        (PASSWORD, PASSWORD + "x", "do not match"),
        ("short pass1", "short pass1", "12 to 256"),
        ("", "", "12 to 256"),
        ("x" * 257, "x" * 257, "12 to 256"),
    ],
    ids=["mismatch", "eleven-characters", "empty", "257-characters"],
)
def test_create_rejects_mismatched_or_bad_length_password(
    engine: Engine, factory: Callable[[], Session], first: str, second: str, message: str
) -> None:
    """守住 Kelvin 2026-10-05 补充「防线是至少 12 个字符的密码」与验收「两次密码不一致或少于
    12 个字符或多于 256 个字符时拒绝」：非零退出，不写入，输出不含密码。"""
    output = Output()

    assert _create(factory, USERNAME, Reader(first, second), output) == admin.EXIT_REJECTED

    assert _accounts(engine) == []
    assert _events(engine) == []
    assert message in output.err.getvalue()
    if len(first) >= 4:
        _assert_no_secrets(output, first, second)


@pytest.mark.parametrize("password", ["x" * 12, "管" * 256], ids=["12", "256"])
def test_create_accepts_password_length_bounds(
    engine: Engine, factory: Callable[[], Session], password: str
) -> None:
    """守住验收「少于 12 个字符或多于 256 个字符时拒绝」的边界：恰好 12 与 256 个字符（按字符
    而不是字节计）可以。"""
    output = Output()

    assert _create(factory, USERNAME, Reader(password, password), output) == admin.EXIT_OK
    assert verify_password(password, _accounts(engine)[0].password_hash)


@pytest.mark.parametrize(
    "username",
    ["ab", "a" * 33, "Shop_admin", "shop-admin", "shop admin", " shop_admin", "管理员账号", ""],
    ids=["too-short", "too-long", "uppercase", "hyphen", "space", "leading-space", "cjk", "empty"],
)
def test_create_rejects_invalid_username(
    engine: Engine, factory: Callable[[], Session], username: str
) -> None:
    """守住验收「用户名不合 3 到 32 个小写字母、数字或下划线时拒绝」（账号表只检查长度，字符集
    由写入方保证）：非零退出，不读密码、不写入。"""
    reader = Reader(PASSWORD, PASSWORD)
    output = Output()

    assert _create(factory, username, reader, output) == admin.EXIT_REJECTED

    assert reader.prompts == []
    assert _accounts(engine) == []
    assert "username" in output.err.getvalue()


@pytest.mark.parametrize("username", ["abc", "a" * 32, "admin_2026"])
def test_create_accepts_valid_usernames(
    engine: Engine, factory: Callable[[], Session], username: str
) -> None:
    """守住验收「3 到 32 个小写字母、数字或下划线」的边界：3 与 32 个字符、
    含数字与下划线的用户名可以。"""
    assert _create(factory, username, Reader(PASSWORD, PASSWORD), Output()) == admin.EXIT_OK
    assert [a.username for a in _accounts(engine)] == [username]


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()], ids=["eof", "interrupt"])
def test_create_rejects_when_no_password_entered(
    engine: Engine, factory: Callable[[], Session], error: BaseException
) -> None:
    """守住 Kelvin 2026-10-04「密码以不回显的方式输入」：输入被中断时拒绝、非零退出、不写入。"""
    output = Output()

    assert _create(factory, USERNAME, Reader(error), output) == admin.EXIT_REJECTED
    assert _accounts(engine) == []


# ---------------------------------------------------------------------------
# reset-password
# ---------------------------------------------------------------------------


def test_reset_password_updates_hash_revokes_sessions_and_audits(
    engine: Engine, factory: Callable[[], Session]
) -> None:
    """守住「单一管理员也须服务端授权与会话到期」与 Kelvin 2026-10-04「本轮审计只记……重设
    密码」：重设唯一账号的密码（新哈希能校验新密码、旧密码不再通过，密码更新时间为当前时间），
    撤销它的全部后台会话（之前已撤销的保留原撤销时间），写 admin_password_reset 并提交，
    以 0 退出，输出不含密码或哈希。"""
    account_id = _existing_account(engine)
    with Session(engine) as db:
        tokens = [issue_admin_session(db, account_id, EARLIER).token for _ in range(2)]
        old = check_admin_session(db, tokens[0], EARLIER)
        assert old is not None
        old.revoked_at = EARLIER + timedelta(hours=1)
        db.commit()
    output = Output()

    assert _reset(factory, Reader(NEW_PASSWORD, NEW_PASSWORD), output) == admin.EXIT_OK

    (account,) = _accounts(engine)
    assert verify_password(NEW_PASSWORD, account.password_hash)
    assert not verify_password("the old admin passphrase", account.password_hash)
    assert account.password_updated_at == NOW
    assert account.created_at == EARLIER
    with Session(engine) as db:
        for token in tokens:
            assert check_admin_session(db, token, NOW) is None
        revoked = sorted(s.revoked_at for s in db.scalars(select(AdminSession)))
    assert revoked == [EARLIER + timedelta(hours=1), NOW]
    assert _events(engine) == [(NOW, account_id, "admin_password_reset", None, None, None, None)]
    assert "1 session(s) revoked" in output.out.getvalue()
    _assert_no_secrets(output, NEW_PASSWORD)
    assert account.password_hash not in output.text


def test_reset_password_without_account_exits_nonzero(
    engine: Engine, factory: Callable[[], Session]
) -> None:
    """守住验收「reset-password……库里没有账号时以非零退出」：不读密码、不写入。"""
    reader = Reader(NEW_PASSWORD, NEW_PASSWORD)
    output = Output()

    assert _reset(factory, reader, output) == admin.EXIT_REJECTED

    assert reader.prompts == []
    assert _accounts(engine) == []
    assert _events(engine) == []
    assert "no admin account" in output.err.getvalue()


def test_reset_password_rejects_mismatch_without_changes(
    engine: Engine, factory: Callable[[], Session]
) -> None:
    """守住验收「两次密码不一致……时拒绝」：重设被拒时哈希、会话与审计记录都不变。"""
    account_id = _existing_account(engine)
    with Session(engine) as db:
        token = issue_admin_session(db, account_id, EARLIER).token
        db.commit()
    output = Output()

    assert _reset(factory, Reader(NEW_PASSWORD, PASSWORD), output) == admin.EXIT_REJECTED

    assert _accounts(engine)[0].password_hash == OLD_HASH
    with Session(engine) as db:
        assert check_admin_session(db, token, NOW) is not None
    assert _events(engine) == []
    _assert_no_secrets(output, NEW_PASSWORD, PASSWORD)


# ---------------------------------------------------------------------------
# 配置、命令行与输出
# ---------------------------------------------------------------------------


def test_not_configured_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收「数据库连接沿用 SHOP_DATABASE_URL……未配置时以非零退出」：两个子命令都以 2
    退出，不读密码。"""
    monkeypatch.setattr(admin, "get_settings", lambda: Settings(_env_file=None, database_url=""))
    prompts: list[str] = []
    monkeypatch.setattr(admin, "read_password", lambda prompt: prompts.append(prompt) or "")

    assert admin.main(["create", USERNAME]) == admin.EXIT_NOT_CONFIGURED
    assert admin.main(["reset-password"]) == admin.EXIT_NOT_CONFIGURED
    assert admin.create_admin(None, USERNAME, reader=Reader()) == admin.EXIT_NOT_CONFIGURED
    assert prompts == []


def test_main_uses_database_url_and_hides_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """守住验收「数据库连接沿用 SHOP_DATABASE_URL 与 app/db/session.py 的会话工厂」「输出不含
    密码或哈希」：经 main 走会话工厂；这里的内存库没有建表，命令失败、非零退出，
    只输出异常类名。"""
    url = "sqlite://"
    monkeypatch.setattr(admin, "get_settings", lambda: Settings(_env_file=None, database_url=url))

    assert admin.main(["create", USERNAME]) == admin.EXIT_REJECTED

    captured = capsys.readouterr()
    assert captured.err.strip() == "database error: OperationalError"


def test_database_error_prints_only_class_name(engine: Engine) -> None:
    """守住「应用日志……不记录……密码」与验收「输出与退出码不含密码或哈希」：数据库出错时只输出
    异常类名，不输出异常消息（其中可能有连接信息或写入的哈希）。"""

    def broken() -> Session:
        raise OperationalError("INSERT ...", {"password_hash": OLD_HASH}, Exception(SECRET))

    output = Output()

    assert _create(broken, USERNAME, Reader(PASSWORD, PASSWORD), output) == admin.EXIT_REJECTED
    assert output.err.getvalue().strip() == "database error: OperationalError"
    assert SECRET not in output.text
    _assert_no_secrets(output, PASSWORD)


@pytest.mark.parametrize(
    "argv",
    [
        ["create", USERNAME, "--password", PASSWORD],
        ["create", USERNAME, PASSWORD],
        ["reset-password", "--password", PASSWORD],
        ["create"],
        [],
        ["delete"],
    ],
    ids=["create-option", "create-extra", "reset-option", "no-username", "none", "unknown"],
)
def test_password_is_not_accepted_from_command_line(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """守住 Kelvin 2026-10-04「密码……不经命令行参数或环境变量」：命令行里带密码的写法被拒绝
    （以非零退出），只有 create <用户名> 与 reset-password 两个子命令；验收「输出不含密码」：
    出错时不回显命令行里多出的参数。"""
    with pytest.raises(SystemExit) as raised:
        admin.main(argv)

    assert raised.value.code != 0
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err


def test_password_is_not_read_from_environment(
    engine: Engine, factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """守住 Kelvin 2026-10-04「密码……不经命令行参数或环境变量」：环境变量里的同名值不被使用，
    密码只来自读密码函数。"""
    for name in ["SHOP_ADMIN_PASSWORD", "ADMIN_PASSWORD", "PASSWORD"]:
        monkeypatch.setenv(name, "environment password value")

    assert _create(factory, USERNAME, Reader(PASSWORD, PASSWORD), Output()) == admin.EXIT_OK
    stored = _accounts(engine)[0].password_hash
    assert verify_password(PASSWORD, stored)
    assert not verify_password("environment password value", stored)


def test_default_reader_uses_getpass(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住 Kelvin 2026-10-04「密码以不回显的方式输入」：默认的读密码函数用 getpass。"""
    prompts: list[str] = []

    def fake_getpass(prompt: str = "Password: ") -> str:
        prompts.append(prompt)
        return PASSWORD

    monkeypatch.setattr(admin.getpass, "getpass", fake_getpass)

    assert admin.read_password("New admin password: ") == PASSWORD
    assert prompts == ["New admin password: "]
