import { readMemberCoupons } from "../api/memberAccount";
import type { AvailableCoupon, MemberCoupons, MemberCouponsRead, UsedCoupon } from "../api/memberAccount";
import { formatSen } from "../format";
import type { CopyKey, CopyVars } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { formatDate } from "./TrackOrderPage";

// 会员中心 P13 的我的优惠券（docs/UX.md 0.13 P13 与 Q19，docs/UX-COPY.md 0.14，视觉稿 P13-desktop-coupons 与 P13-phone-coupons；
// DESIGN 1.14「权限与资料保护」第 3、5 条，HANDOFF 0.63）。经 api/memberAccount.ts 读取：可用券对全部会员相同（服务端给出），
// 使用记录只有本人的；段表与会话由 AccountPage.tsx 接上。最低消费为 0 的券不显示 account.coupon_min_spend（HANDOFF 0.63 第（6）条）；
// 有效期按马来西亚日期显示。金额原样格式化接口的整数仙，不在浏览器计算。

// 有效期的日期按马来西亚时区取（valid_until 为带时区的 ISO 字符串）。
export const COUPON_TIME_ZONE = "Asia/Kuala_Lumpur";

// 我的优惠券：读取中（该段标 aria-busy）、已取到、读取失败（common.error_retry 或 common.network_check）。
export type CouponsState = { status: "loading" } | { status: "ready"; coupons: MemberCoupons } | { status: "failed"; error: CopyKey };

// 按读取结果决定：ok 原样取可用券与使用记录（按接口顺序）；signed_out 重读会话后去 /login（login）；
// failed 为 common.error_retry；network 为 common.network_check。
export function couponsStep(read: MemberCouponsRead): CouponsState | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", coupons: { available: read.available, used: read.used } };
    case "signed_out":
      return "login";
    case "failed":
      return { status: "failed", error: "common.error_retry" };
    case "network":
      return { status: "failed", error: "common.network_check" };
  }
}

// 读取我的优惠券并把结果交给 settle（login 时由页面重读会话并进入 /login）；signal 中止之后不再交出结果。
export async function loadCoupons(signal: AbortSignal, settle: (step: CouponsState | "login") => Promise<void>): Promise<void> {
  const read = await readMemberCoupons(signal);
  if (signal.aborted) {
    return;
  }
  await settle(couponsStep(read));
}

// 券的面额：固定额为 account.coupon_value_fixed（formatSen(amount_sen)），百分比为 account.coupon_value_percent；
// 类型与对应的值对不上时为 null，不显示面额。
export function couponValue(coupon: AvailableCoupon): { key: CopyKey; vars: CopyVars } | null {
  if (coupon.type === "fixed" && coupon.amount_sen !== null) {
    return { key: "account.coupon_value_fixed", vars: { amount: formatSen(coupon.amount_sen) } };
  }
  if (coupon.type === "percent" && coupon.percent !== null) {
    return { key: "account.coupon_value_percent", vars: { percent: coupon.percent } };
  }
  return null;
}

// 一张可用券（P13-desktop-coupons 的卡片）：代码、面额、最低消费（大于 0 时）与有效期上下排列。
function CouponCard({ coupon }: { coupon: AvailableCoupon }) {
  const t = useCopy();
  const { language } = useLanguage();
  const value = couponValue(coupon);
  return (
    <li className="acs-card site-account__coupon">
      <span className="acs-num">{coupon.code}</span>
      {value !== null && <span>{t(value.key, value.vars)}</span>}
      {coupon.min_spend_sen > 0 && <span className="acs-body-s acs-muted">{t("account.coupon_min_spend", { amount: formatSen(coupon.min_spend_sen) })}</span>}
      <span className="acs-body-s acs-muted">{t("account.coupon_valid_until", { date: formatDate(coupon.valid_until, language, COUPON_TIME_ZONE) })}</span>
    </li>
  );
}

// 使用记录的一行：桌面为代码、account.coupon_used_on 与减免金额（靠右）三列；手机为代码与金额、account.coupon_used_on 两行。
function UsedRow({ use, phone }: { use: UsedCoupon; phone: boolean }) {
  const t = useCopy();
  const code = <span className="acs-num">{use.code}</span>;
  const usedOn = t("account.coupon_used_on", { orderNo: use.order_number });
  const amount = <span className="acs-num site-account__figure">{t("common.price_myr", { amount: formatSen(use.discount_sen) })}</span>;
  return phone ? (
    <li className="site-account__used">
      {code}
      {amount}
      <span className="acs-body-s acs-muted">{usedOn}</span>
    </li>
  ) : (
    <li className="site-account__used">
      {code}
      <span>{usedOn}</span>
      {amount}
    </li>
  );
}

// 已取到时：account.coupons_available 与各张券、其后 account.coupon_use_hint（没有可用券时为 account.coupons_available_empty）；
// account.coupons_used 与使用记录（没有时为 account.coupons_used_empty），桌面与手机各一份（按 site.css 的媒体查询显隐）。
function CouponsContent({ coupons }: { coupons: MemberCoupons }) {
  const t = useCopy();
  return (
    <>
      <h3 className="acs-display-s">{t("account.coupons_available")}</h3>
      {coupons.available.length === 0 ? (
        <p className="acs-muted">{t("account.coupons_available_empty")}</p>
      ) : (
        <>
          <ul className="site-account__coupons">
            {coupons.available.map((coupon) => (
              <CouponCard key={coupon.code} coupon={coupon} />
            ))}
          </ul>
          <p className="acs-body-s acs-muted">{t("account.coupon_use_hint")}</p>
        </>
      )}
      <h3 className="acs-display-s site-account__subhead">{t("account.coupons_used")}</h3>
      {coupons.used.length === 0 ? (
        <p className="acs-muted">{t("account.coupons_used_empty")}</p>
      ) : (
        <>
          <ul className="site-desktop-only site-account__list site-account__uses">
            {coupons.used.map((use, index) => (
              <UsedRow key={index} use={use} phone={false} />
            ))}
          </ul>
          <ul className="site-phone-only site-account__list site-account__uses">
            {coupons.used.map((use, index) => (
              <UsedRow key={index} use={use} phone={true} />
            ))}
          </ul>
        </>
      )}
    </>
  );
}

// 我的优惠券的内容：段标题（手机由标签代替）；读取失败时为提示；已取到时为可用券与使用记录。
export function CouponsPanel({ coupons }: { coupons: CouponsState }) {
  const t = useCopy();
  return (
    <>
      <h2 className="acs-display-m site-desktop-only">{t("account.coupons")}</h2>
      {coupons.status === "failed" && (
        <p className="acs-alert acs-alert--danger site-notice" role="alert">
          {t(coupons.error)}
        </p>
      )}
      {coupons.status === "ready" && <CouponsContent coupons={coupons.coupons} />}
    </>
  );
}
