"""定时任务运行器：常驻进程每分钟执行一轮任务；另有手动执行一次超时取消的命令。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「计价、优惠、积分与库存」第 6 条：15 分钟未支付自动取消并释放。
- 「订单与退款状态」第 3 条：待支付订单超时为 demo_cancelled，释放库存（券与积分预占的释放
  由之后的优惠券与积分任务扩展 SHOP-TASK-021 的超时取消路径，这里不变）。
- 「失败、并发与重试」第 6 条：定时任务可重复运行、只生效一次；失败有告警与人工补跑办法。
  运营告警邮件接入之前，以容器不健康代替告警（见下文心跳）。

用法（生产栈里的 shop_jobs 容器与 shop_api 同一镜像）：
    python -m app.jobs run              常驻循环，SIGTERM 或 SIGINT 时退出
    python -m app.jobs cancel-expired   执行一次超时取消后退出（运营者手动补跑用）

数据库连接沿用 SHOP_DATABASE_URL 与 app/db/session.py 的会话工厂。

每一轮：
1. 按任务列表逐个执行任务，每个任务一个新会话、一个独立事务，成功即提交。时间取当前 UTC
   （与库里一样不带时区）。任务抛异常（含数据库不可用）时回滚，只记一条含任务名与异常类名
   的日志，不退出，也不影响同一轮的其他任务与下一轮。
2. 写心跳文件 HEARTBEAT_PATH（容器的健康检查看它的修改时间）。任一任务已连续失败、且距它
   最近一次成功（从未成功过则距进程启动）满 UNHEALTHY_AFTER_SECONDS 时不写，容器因此变为
   不健康；该任务再次成功后恢复写心跳。
3. 睡到距本轮开始满 ROUND_SECONDS；收到停止信号即醒来，不再开始新一轮。

任务列表：本任务只有超时取消（每轮最多 CANCEL_BATCH_LIMIT 张，剩余的下一轮继续）。之后的
每日库存重置等任务按同样方式加入 default_jobs()：写一个 Job（名称与执行函数）。每个任务必须
可重复运行且只生效一次（一轮执行到一半失败或运营者手动补跑都会再次运行它），判定本次无需
执行而跳过也算成功、正常返回，只有真正出错才抛异常。

日志只记录任务名、生效条数（超时取消即取消张数）与异常类名，不记录订单号、个人资料、
连接串或异常消息原文。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import _session_factory
from app.services.payment import expire_overdue_orders

logger = logging.getLogger(__name__)

# 每轮从开始到下一轮开始的秒数。
ROUND_SECONDS = 60.0
# 任一任务连续失败、距它最近一次成功（或进程启动）满这么久即不再写心跳。
UNHEALTHY_AFTER_SECONDS = 600.0
# 超时取消每轮最多处理的订单数；剩余的下一轮继续。
CANCEL_BATCH_LIMIT = 100
# 容器内固定的临时目录路径；docker-compose.yml 里 shop_jobs 的健康检查用同一路径。
HEARTBEAT_PATH = Path("/tmp/acuven_shop_jobs_heartbeat")

# cancel-expired 的退出码：成功 0，任务失败 1，未配置数据库 2。
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 2

SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class Job:
    """一个定时任务。

    name 只用于日志与失败计时。run 拿到本次的会话与当前时间（不带时区的 UTC），返回本次
    生效的条数；可以自己提交，返回后运行器再提交一次，抛异常时运行器回滚。
    """

    name: str
    run: Callable[[Session, datetime], int]


def cancel_expired_job(limit: int = CANCEL_BATCH_LIMIT) -> Job:
    """超时取消：SHOP-TASK-021 的 expire_overdue_orders，每次最多 limit 张。"""

    def run(db: Session, now: datetime) -> int:
        return expire_overdue_orders(db, now, limit=limit)

    return Job(name="cancel_expired_orders", run=run)


def default_jobs() -> list[Job]:
    """run 每轮按顺序执行的任务。"""
    return [cancel_expired_job()]


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def run_job(job: Job, session_factory: SessionFactory, now: datetime) -> int:
    """在新会话的独立事务里执行一个任务并提交，返回生效条数；出错时回滚后原样抛出。"""
    with session_factory() as db:
        try:
            count = job.run(db, now)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return count


class Runner:
    """常驻循环。时间、计时与睡眠都可注入，测试不真实等待。

    session_factory 为空表示未配置数据库：每轮记一条警告，所有任务按失败计时。
    sleep 默认是停止事件的 wait：收到停止信号时立即醒来。
    """

    def __init__(
        self,
        jobs: Sequence[Job],
        session_factory: SessionFactory | None,
        heartbeat_path: Path = HEARTBEAT_PATH,
        *,
        now: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self._jobs = list(jobs)
        self._session_factory = session_factory
        self._heartbeat_path = heartbeat_path
        self._now = now
        self._monotonic = monotonic
        self.stop_event = threading.Event()
        self._sleep = self.stop_event.wait if sleep is None else sleep
        self._started = monotonic()
        # 每个任务最近一次成功的时刻（monotonic）；没有成功过的不在里面。
        self.last_success: dict[str, float] = {}
        self._failing: set[str] = set()
        self._beating = True

    def request_stop(self, signum: int | None = None, frame: FrameType | None = None) -> None:
        """停止：当前这一轮照常做完，不再开始新一轮。可直接用作信号处理函数。"""
        self.stop_event.set()

    def run_forever(self) -> None:
        # 上一次运行留下的心跳不代表这个进程。
        try:
            self._heartbeat_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not remove old heartbeat: %s", type(exc).__name__)
        while not self.stop_event.is_set():
            started = self._monotonic()
            self.run_round()
            remaining = ROUND_SECONDS - (self._monotonic() - started)
            if remaining > 0 and not self.stop_event.is_set():
                self._sleep(remaining)

    def run_round(self) -> None:
        if self._session_factory is None:
            logger.warning("database is not configured; jobs skipped")
            self._failing.update(job.name for job in self._jobs)
        else:
            for job in self._jobs:
                self._run_one(job, self._session_factory)
        self._beat()

    def healthy(self) -> bool:
        """没有任何任务已连续失败满 UNHEALTHY_AFTER_SECONDS。"""
        current = self._monotonic()
        for name in self._failing:
            since = self.last_success.get(name, self._started)
            if current - since >= UNHEALTHY_AFTER_SECONDS:
                return False
        return True

    def _run_one(self, job: Job, session_factory: SessionFactory) -> None:
        try:
            count = run_job(job, session_factory, self._now())
        except Exception as exc:
            self._failing.add(job.name)
            logger.error("job %s failed: %s", job.name, type(exc).__name__)
            return
        self._failing.discard(job.name)
        self.last_success[job.name] = self._monotonic()
        if count:
            logger.info("job %s done: %d changed", job.name, count)

    def _beat(self) -> None:
        if not self.healthy():
            if self._beating:
                logger.error("heartbeat stopped: a job has been failing for 10 minutes")
            self._beating = False
            return
        if not self._beating:
            logger.info("heartbeat resumed")
        self._beating = True
        try:
            self._heartbeat_path.write_text(self._now().isoformat() + "Z\n", encoding="ascii")
        except OSError as exc:
            logger.warning("could not write heartbeat: %s", type(exc).__name__)


def install_signal_handlers(runner: Runner) -> None:
    signal.signal(signal.SIGTERM, runner.request_stop)
    signal.signal(signal.SIGINT, runner.request_stop)


def cancel_expired(
    session_factory: SessionFactory | None,
    *,
    now: Callable[[], datetime] = utc_now,
    limit: int = CANCEL_BATCH_LIMIT,
) -> int:
    """执行一次超时取消，返回退出码。取消张数等于上限时可能还有剩余，可再执行一次。"""
    if session_factory is None:
        logger.error("database is not configured")
        return EXIT_NOT_CONFIGURED
    job = cancel_expired_job(limit)
    try:
        count = run_job(job, session_factory, now())
    except Exception as exc:
        logger.error("job %s failed: %s", job.name, type(exc).__name__)
        return EXIT_FAILED
    logger.info("job %s done: %d changed", job.name, count)
    return EXIT_OK


def configured_session_factory() -> SessionFactory | None:
    """按 SHOP_DATABASE_URL 取会话工厂；未配置为空。引擎在第一次开会话时才建。"""
    database_url = get_settings().database_url
    if not database_url:
        return None
    return lambda: _session_factory(database_url)()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs", description="Scheduled jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="run the jobs every minute until SIGTERM or SIGINT")
    commands.add_parser("cancel-expired", help="cancel overdue unpaid orders once and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    session_factory = configured_session_factory()
    if args.command == "cancel-expired":
        return cancel_expired(session_factory)

    runner = Runner(default_jobs(), session_factory)
    install_signal_handlers(runner)
    runner.run_forever()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
