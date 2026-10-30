"""读取站点设置：短信验证开关、支付方式与每日恢复库存开关的当前值；后台保存这三项。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条：「下单、发送短信、提交验证码与
确认注销时，服务端都按当时读取的开关值判定」；「数据模型」的 SiteSetting 一行：「每次判定都从
数据库读取当前值、不缓存，修改对之后的请求立即生效」。

支付方式依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 2 条：「`SiteSetting`
的支付方式取『模拟』（默认）或『Stripe 测试』，与短信验证开关一样每次从数据库读取」；「服务端在
访客发起支付时按当时的值判定」。

每日恢复库存开关（SHOP-TASK-179）依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与
库存」第 6 条：「定时任务每次运行时先从数据库读取开关的当前值（不缓存），再判断当天是否已完成
重置」「表里还没有站点设置那一行时视为开启，按默认值插入该行时也写开启」「保存请求里本开关可以
省略，省略即不改」「同一次保存的任何一项被拒……时，本开关的修改同样不生效」；「数据模型」的
SiteSetting 一行（每日恢复库存开关，布尔，默认开启）与 docs/HANDOFF.md 0.76 的落实写法（判定写在
app/jobs.py 的 reset_stock_job；审计操作名 admin_daily_stock_reset_changed，旧值与新值为 on 或
off）。

读取函数只发 SELECT、不写库。后台保存（save_site_settings，SHOP-TASK-122、168 与 179，接口见
app/api/admin_site_settings.py）一次提交支付方式、短信验证开关与每日恢复库存开关三项：锁定站点
设置行后在同一事务里改有变化的项，每项各写一条审计（admin_payment_method_changed、
admin_sms_verification_changed、admin_daily_stock_reset_changed；「数据模型」SiteSetting 一行
「后台修改写入 AuditEvent」）。凭据不可用时不能改为 Stripe 测试
（第 2 条「凭据未配置时不能切到 Stripe 测试」，docs/UX.md 0.11 A09「保存时服务端再次检查」）；
短信服务配置不齐全时不能保存「开启」（docs/DESIGN.md 1.15「边界与原则」短信验证开关一条：
「缺一或不合格即拒绝开启，本次保存不生效，同一次保存里的其他设置（如支付方式）也不生效（当前已
开启也一样）；保存『关闭』不检查」）；每日恢复库存开关的开启与关闭都不检查任何配置。短信服务
是否可用由接口在当次请求里算出后传入（HANDOFF 0.65：判定写在接口文件里，这里引用公开站点设置
接口会循环导入）。本模块不写日志。
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
ADMIN_SMS_VERIFICATION_CHANGED = "admin_sms_verification_changed"
ADMIN_DAILY_STOCK_RESET_CHANGED = "admin_daily_stock_reset_changed"
AUDIT_TARGET_SITE_SETTING = "site_setting"

# 短信验证开关审计的旧值与新值（HANDOFF 0.65）；每日恢复库存开关也用这两个值（HANDOFF 0.76）。
SMS_VERIFICATION_ON = "on"
SMS_VERIFICATION_OFF = "off"


class SiteSettingsRejected(Exception):
    """保存站点设置被拒；code 为 stripe_not_configured 或 sms_not_configured。"""

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


def is_daily_stock_reset_enabled(db: Session) -> bool:
    """每日恢复库存开关当前是否开启；表里没有那一行时视为开启（DESIGN 1.18 第 6 条）。

    写法与 is_sms_verification_enabled 相同：每次调用都向数据库发一条 SELECT、不缓存，只查列值
    而不加载 ORM 对象。每日库存重置的定时任务与 reset-stock 子命令在每次运行时经 app/jobs.py 的
    reset_stock_job 调用本函数，按这次读到的值判定。
    """
    enabled = db.scalar(
        select(SiteSetting.daily_stock_reset_enabled).where(SiteSetting.id == SITE_SETTING_ID)
    )
    return True if enabled is None else bool(enabled)


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


def _switch_audit_value(enabled: bool) -> str:
    return SMS_VERIFICATION_ON if enabled else SMS_VERIFICATION_OFF


# ---------------------------------------------------------------------------
# 保存支付方式、短信验证开关与每日恢复库存开关
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
    # 对方插入的行。新行的三项取服务端默认值：短信验证关闭、支付方式模拟、每日恢复库存开启。
    try:
        with db.begin_nested():
            db.add(
                SiteSetting(
                    id=SITE_SETTING_ID,
                    sms_verification_enabled=False,
                    payment_method=PAYMENT_METHOD_SIMULATED,
                    daily_stock_reset_enabled=True,
                    updated_at=now,
                )
            )
            db.flush()
    except IntegrityError:
        pass
    return db.scalars(lock_site_setting_statement()).one()


def save_site_settings(
    db: Session,
    admin_account_id: int,
    method: str,
    sms_enabled: bool | None,
    stripe_available: bool,
    sms_available: bool,
    now: datetime,
    *,
    stock_reset_enabled: bool | None = None,
) -> tuple[str, bool, bool, bool]:
    """保存支付方式、短信验证开关与每日恢复库存开关，返回保存后的支付方式、保存后的短信验证开关、
    保存后的每日恢复库存开关与是否实际改动（三项都与当前值相同为假）。

    method 须为 simulated 或 stripe_test（由接口校验，其他值是编程错误，抛 ValueError）；
    sms_enabled 为 None 表示请求里省略了该字段：不检查短信配置、不改开关（HANDOFF 0.65）；
    stock_reset_enabled 为 None 同样表示省略：不比较、不改（HANDOFF 0.76），开启与关闭都不检查
    任何配置；stripe_available 为 app/services/stripe_gateway.py 的 stripe_configured、
    sms_available 为 app/api/admin_site_settings.py 的 sms_service_configured，都在当次请求里算出；
    admin_account_id 须在调用前取出（这里先回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. method 为 stripe_test 而 stripe_available 为假：抛 stripe_not_configured；sms_enabled 为
       True 而 sms_available 为假：抛 sms_not_configured。两项检查都不查库、不写入三项中的任何
       一项（当前已是该值也一样），先查支付方式再查短信（HANDOFF 0.65）。切回 simulated 与保存
       「关闭」不看配置。
    2. 结束此前的事务（此前只有读取，回滚即可），以 SELECT … FOR UPDATE 锁定站点设置行，使锁定
       成为新事务的第一条语句；行不存在时在保存点里按默认值插入，撞上主键即回滚该保存点，然后
       再锁定。
    3. 三项各自与锁住那一行的当前值比较：支付方式按 get_payment_method 的规则解读（不认识的值
       视为 simulated），短信验证开关按 is_sms_verification_enabled 的规则解读（sms_enabled 为
       None 时不比较），每日恢复库存开关取该行的列值（stock_reset_enabled 为 None 时不比较）。
       都相同时回滚（释放锁），不写入、不写审计。
    4. 否则只改有变化的项与更新时间，每项变化各写一条审计（对象为该行；支付方式为
       admin_payment_method_changed，旧值与新值为 simulated 或 stripe_test；短信为
       admin_sms_verification_changed、每日恢复库存为 admin_daily_stock_reset_changed，旧值与
       新值为 on 或 off），审计依次为支付方式、短信、每日恢复库存，一起提交。
    """
    if method not in PAYMENT_METHODS:
        raise ValueError("payment method must be simulated or stripe_test")
    if method == PAYMENT_METHOD_STRIPE_TEST and not stripe_available:
        raise SiteSettingsRejected("stripe_not_configured")
    if sms_enabled is True and not sms_available:
        raise SiteSettingsRejected("sms_not_configured")

    db.rollback()
    setting = _lock_site_setting(db, now)
    current_method = _known_method(setting.payment_method)
    # 当前值取自锁住的那一行，不另发 SELECT；回滚与提交之后该对象过期，读取都在此之前。
    current_sms = bool(setting.sms_verification_enabled)
    current_stock_reset = bool(setting.daily_stock_reset_enabled)
    new_sms = current_sms if sms_enabled is None else sms_enabled
    new_stock_reset = current_stock_reset if stock_reset_enabled is None else stock_reset_enabled
    method_changed = current_method != method
    sms_changed = current_sms != new_sms
    stock_reset_changed = current_stock_reset != new_stock_reset
    if not method_changed and not sms_changed and not stock_reset_changed:
        db.rollback()
        return current_method, current_sms, current_stock_reset, False

    if method_changed:
        setting.payment_method = method
    if sms_changed:
        setting.sms_verification_enabled = new_sms
    if stock_reset_changed:
        setting.daily_stock_reset_enabled = new_stock_reset
    setting.updated_at = now
    db.flush()
    setting_id = setting.id
    if method_changed:
        record_audit(
            db,
            ADMIN_PAYMENT_METHOD_CHANGED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_SITE_SETTING,
            target_id=setting_id,
            old_value=current_method,
            new_value=method,
        )
    if sms_changed:
        record_audit(
            db,
            ADMIN_SMS_VERIFICATION_CHANGED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_SITE_SETTING,
            target_id=setting_id,
            old_value=_switch_audit_value(current_sms),
            new_value=_switch_audit_value(new_sms),
        )
    if stock_reset_changed:
        record_audit(
            db,
            ADMIN_DAILY_STOCK_RESET_CHANGED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_SITE_SETTING,
            target_id=setting_id,
            old_value=_switch_audit_value(current_stock_reset),
            new_value=_switch_audit_value(new_stock_reset),
        )
    db.commit()
    return method, new_sms, new_stock_reset, True
