"""读取站点设置：短信验证开关与支付方式的当前值；后台保存支付方式。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条：「下单、发送短信、提交验证码与
确认注销时，服务端都按当时读取的开关值判定」；「数据模型」的 SiteSetting 一行：「每次判定都从
数据库读取当前值、不缓存，修改对之后的请求立即生效」。

支付方式依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 2 条：「`SiteSetting`
的支付方式取『模拟』（默认）或『Stripe 测试』，与短信验证开关一样每次从数据库读取」；「服务端在
访客发起支付时按当时的值判定」。

读取函数只发 SELECT、不写库。只有支付方式可以修改（save_payment_method，SHOP-TASK-122，接口见
app/api/admin_site_settings.py）：锁定站点设置行后在同一事务里改值并写审计
admin_payment_method_changed（「数据模型」SiteSetting 一行「后台修改写入 AuditEvent」）；凭据
不可用时不能改为 Stripe 测试（第 2 条「凭据未配置时不能切到 Stripe 测试」，docs/UX.md 0.11 A09
「保存时服务端再次检查」）。短信验证开关按 Kelvin 2026-10-07 与 2026-10-15 的决定暂不提供写入。
本模块不写日志。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.site import (
    PAYMENT_METHOD_SIMULATED,
    PAYMENT_METHOD_STRIPE_TEST,
    PAYMENT_METHODS,
    SITE_SETTING_ID,
    SiteSetting,
)
from app.services.admin_auth import record_audit

# 审计操作名与对象类别（对象 ID 为站点设置行的 ID）。
ADMIN_PAYMENT_METHOD_CHANGED = "admin_payment_method_changed"
AUDIT_TARGET_SITE_SETTING = "site_setting"


class PaymentMethodRejected(Exception):
    """保存支付方式被拒；code 为 stripe_not_configured。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_sms_verification_enabled(db: Session) -> bool:
    """短信验证开关当前是否开启；表里没有那一行时视为关闭。

    每次调用都向数据库发一条 SELECT，不做任何进程内或 Redis 缓存；只查列值而不加载 ORM 对象，
    会话里已加载的旧对象也不会被当作当前值。

    下单、发送短信、提交验证码与确认注销时，都必须在当次请求里调用本函数，按当时的值判定，
    不得沿用之前请求、页面或前台接口给出的值：开关在流程途中被关闭后，提交的验证码不再核验，
    不登录、不注册、不重设密码，也不能以短信完成注销确认；开关在流程途中被开启后，已进入游客
    表单的马新号码下单被拒。前台经 GET /api/site-settings 读到的值只用于决定显示哪组界面。

    同一个数据库事务里，MySQL 默认的可重复读隔离级别可能让后几次读到事务开始后第一次读取时的
    快照；判定应在处理该请求的事务里读取，不要跨请求复用事务。
    """
    enabled = db.scalar(
        select(SiteSetting.sms_verification_enabled).where(SiteSetting.id == SITE_SETTING_ID)
    )
    return bool(enabled)


def get_payment_method(db: Session) -> str:
    """支付方式的当前值：simulated 或 stripe_test；表里没有那一行或值不认识时视为 simulated。

    写法与 is_sms_verification_enabled 相同：每次调用都向数据库发一条 SELECT、不缓存，只查列值。
    取值按原样比较、区分大小写：MySQL 默认排序规则下检查约束也接受大写的 STRIPE_TEST，这样的值
    不认识，视为 simulated。

    访客发起支付时由 app/services/stripe_checkout.py 的支付途径判定在当次请求里调用；
    订单已有不是 expired 的 Stripe 记录时途径不看本函数（第 2 条「不论开关当前取值」）。
    """
    method = db.scalar(select(SiteSetting.payment_method).where(SiteSetting.id == SITE_SETTING_ID))
    return _known_method(method)


def _known_method(method: str | None) -> str:
    if method is not None and method in PAYMENT_METHODS:
        return method
    return PAYMENT_METHOD_SIMULATED


# ---------------------------------------------------------------------------
# 保存支付方式
# ---------------------------------------------------------------------------


def lock_site_setting_statement() -> Select[tuple[SiteSetting]]:
    """锁定站点设置行的查询（SELECT … FOR UPDATE）。"""
    return (
        select(SiteSetting)
        .where(SiteSetting.id == SITE_SETTING_ID)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def _lock_site_setting(db: Session, now: datetime) -> SiteSetting:
    setting = db.scalars(lock_site_setting_statement()).one_or_none()
    if setting is not None:
        return setting
    # 迁移 0010 已写入这一行，正常不会走到这里。并发插入时后到的一方撞上主键，回滚保存点后再锁定
    # 对方插入的行。新行的两项取服务端默认值：短信验证关闭、支付方式模拟。
    try:
        with db.begin_nested():
            db.add(
                SiteSetting(
                    id=SITE_SETTING_ID,
                    sms_verification_enabled=False,
                    payment_method=PAYMENT_METHOD_SIMULATED,
                    updated_at=now,
                )
            )
            db.flush()
    except IntegrityError:
        pass
    return db.scalars(lock_site_setting_statement()).one()


def save_payment_method(
    db: Session,
    admin_account_id: int,
    method: str,
    stripe_available: bool,
    now: datetime,
) -> tuple[str, bool]:
    """保存支付方式，返回保存后的支付方式与是否实际改动（与当前值相同为假）。

    method 须为 simulated 或 stripe_test（由接口校验，其他值是编程错误，抛 ValueError）；
    stripe_available 为 app/services/stripe_gateway.py 的 stripe_configured 在当次请求里的结果；
    admin_account_id 须在调用前取出（这里先回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. method 为 stripe_test 而 stripe_available 为假：抛 stripe_not_configured，不查库、不写入
       （当前已是 stripe_test 也一样）。切回 simulated 不看配置。
    2. 结束此前的事务（此前只有读取，回滚即可），以 SELECT … FOR UPDATE 锁定站点设置行，使锁定
       成为新事务的第一条语句；行不存在时在保存点里按默认值插入，撞上主键即回滚该保存点，然后
       再锁定。
    3. 当前值按 get_payment_method 的规则解读（不认识的值视为 simulated）；与 method 相同时回滚
       （释放锁），不写入、不写审计。
    4. 否则改值与更新时间，写一条 admin_payment_method_changed 审计（对象为该行，旧值与新值为
       simulated 或 stripe_test），一起提交。短信验证开关不动。
    """
    if method not in PAYMENT_METHODS:
        raise ValueError("payment method must be simulated or stripe_test")
    if method == PAYMENT_METHOD_STRIPE_TEST and not stripe_available:
        raise PaymentMethodRejected("stripe_not_configured")

    db.rollback()
    setting = _lock_site_setting(db, now)
    current = _known_method(setting.payment_method)
    if current == method:
        db.rollback()
        return current, False

    setting.payment_method = method
    setting.updated_at = now
    db.flush()
    record_audit(
        db,
        ADMIN_PAYMENT_METHOD_CHANGED,
        admin_account_id,
        now,
        target_type=AUDIT_TARGET_SITE_SETTING,
        target_id=setting.id,
        old_value=current,
        new_value=method,
    )
    db.commit()
    return method, True
