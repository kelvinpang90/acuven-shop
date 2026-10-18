"""读取站点设置：短信验证开关与支付方式的当前值。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条：「下单、发送短信、提交验证码与
确认注销时，服务端都按当时读取的开关值判定」；「数据模型」的 SiteSetting 一行：「每次判定都从
数据库读取当前值、不缓存，修改对之后的请求立即生效」。

支付方式依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 2 条：「`SiteSetting`
的支付方式取『模拟』（默认）或『Stripe 测试』，与短信验证开关一样每次从数据库读取」；「服务端在
访客发起支付时按当时的值判定」。

本模块只读、不写；修改开关由之后的管理后台业务接口任务在管理员登录与审计记录就绪后实现。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.site import (
    PAYMENT_METHOD_SIMULATED,
    PAYMENT_METHODS,
    SITE_SETTING_ID,
    SiteSetting,
)


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
    if method in PAYMENT_METHODS:
        return method
    return PAYMENT_METHOD_SIMULATED
