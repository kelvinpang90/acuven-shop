import { readMemberPoints } from "../api/memberAccount";
import type { MemberPoints, MemberPointsRead, PointsActivity } from "../api/memberAccount";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { formatCouponDate } from "./AdminCouponsPage";
import { formatDate } from "./TrackOrderPage";

// 会员中心 P13 的我的积分（docs/UX.md 0.13 P13，docs/UX-COPY.md 0.14，视觉稿 P13-desktop-points 与 P13-phone-points；
// DESIGN 1.14「权限与资料保护」第 3、5 条与「计价、优惠、积分与库存」第 3、5 条，HANDOFF 0.63）。经 api/memberAccount.ts 读取本人的积分，
// 段表与会话由 AccountPage.tsx 接上。余额之后，退款后尚欠大于 0 才显示 account.points_pending，有即将到期的积分才显示 account.points_expiry
// （HANDOFF 0.63 第（6）条）；明细按接口顺序，为空时只显示 account.points_history 标题。数字原样显示接口的整数，不在浏览器计算。

// 我的积分：读取中（该段标 aria-busy）、已取到、读取失败（common.error_retry 或 common.network_check）。
export type PointsState = { status: "loading" } | { status: "ready"; points: MemberPoints } | { status: "failed"; error: CopyKey };

// 按读取结果决定：ok 原样取余额、尚欠、到期与明细；signed_out 重读会话后去 /login（login）；
// failed 为 common.error_retry；network 为 common.network_check。
export function pointsStep(read: MemberPointsRead): PointsState | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", points: { balance: read.balance, pending: read.pending, next_expiry: read.next_expiry, history: read.history } };
    case "signed_out":
      return "login";
    case "failed":
      return { status: "failed", error: "common.error_retry" };
    case "network":
      return { status: "failed", error: "common.network_check" };
  }
}

// 读取我的积分并把结果交给 settle（login 时由页面重读会话并进入 /login）；signal 中止之后不再交出结果。
export async function loadPoints(signal: AbortSignal, settle: (step: PointsState | "login") => Promise<void>): Promise<void> {
  const read = await readMemberPoints(signal);
  if (signal.aborted) {
    return;
  }
  await settle(pointsStep(read));
}

// 明细类型的名称（account.points_type_*）；未知类型为 null，不显示名称。
const TYPE_LABEL: Readonly<Record<string, CopyKey>> = {
  earned: "account.points_type_earned",
  redeemed: "account.points_type_redeemed",
  expired: "account.points_type_expired",
  returned: "account.points_type_returned",
  reversed: "account.points_type_reversed",
};

export function pointsTypeLabel(type: string): CopyKey | null {
  return TYPE_LABEL[type] ?? null;
}

// 带正负号的积分数：正数前加加号，0 与负数原样显示接口的整数。
export function signedPoints(points: number): string {
  return points > 0 ? `+${String(points)}` : String(points);
}

function TypeName({ type }: { type: string }) {
  const t = useCopy();
  const label = pointsTypeLabel(type);
  return <span>{label !== null && t(label)}</span>;
}

// 桌面一行（P13-desktop-points）：日期、类型、积分数（靠右）与订单号四列。
function DesktopRow({ activity }: { activity: PointsActivity }) {
  const { language } = useLanguage();
  return (
    <li className="site-account__activity">
      <span className="acs-body-s acs-muted">{formatDate(activity.created_at, language)}</span>
      <TypeName type={activity.type} />
      <span className="acs-num site-account__figure">{signedPoints(activity.points)}</span>
      <span className="acs-num acs-muted">{activity.order_number}</span>
    </li>
  );
}

// 手机一行（P13-phone-points）：日期与积分数、类型与订单号两行。
function PhoneRow({ activity }: { activity: PointsActivity }) {
  const { language } = useLanguage();
  return (
    <li className="site-account__activity">
      <span className="acs-body-s acs-muted">{formatDate(activity.created_at, language)}</span>
      <span className="acs-num site-account__figure">{signedPoints(activity.points)}</span>
      <TypeName type={activity.type} />
      <span className="acs-num acs-muted">{activity.order_number}</span>
    </li>
  );
}

// 已取到时：余额、尚欠与到期一块；桌面为 h3 account.points_history 与各行，手机为默认展开的 details（summary 为 account.points_history）。
function PointsContent({ points }: { points: MemberPoints }) {
  const t = useCopy();
  const { language } = useLanguage();
  const expiry = points.next_expiry;
  return (
    <>
      <div className="site-account__balance">
        <span className="acs-num">{t("account.points_balance", { points: points.balance })}</span>
        {points.pending > 0 && <span className="acs-body-s">{t("account.points_pending", { points: points.pending })}</span>}
        {expiry !== null && <span className="acs-body-s acs-muted">{t("account.points_expiry", { points: expiry.points, date: formatCouponDate(expiry.date, language) })}</span>}
      </div>
      <h3 className="acs-display-s site-desktop-only site-account__subhead">{t("account.points_history")}</h3>
      {points.history.length > 0 && (
        <ul className="site-desktop-only site-account__list site-account__history">
          {points.history.map((activity, index) => (
            <DesktopRow key={index} activity={activity} />
          ))}
        </ul>
      )}
      <details className="site-phone-only site-account__fold" open>
        <summary>{t("account.points_history")}</summary>
        {points.history.length > 0 && (
          <ul className="site-account__list site-account__history">
            {points.history.map((activity, index) => (
              <PhoneRow key={index} activity={activity} />
            ))}
          </ul>
        )}
      </details>
    </>
  );
}

// 我的积分的内容：段标题（手机由标签代替）；读取失败时为提示；已取到时为余额与明细。
export function PointsPanel({ points }: { points: PointsState }) {
  const t = useCopy();
  return (
    <>
      <h2 className="acs-display-m site-desktop-only">{t("account.points")}</h2>
      {points.status === "failed" && (
        <p className="acs-alert acs-alert--danger site-notice" role="alert">
          {t(points.error)}
        </p>
      )}
      {points.status === "ready" && <PointsContent points={points.points} />}
    </>
  );
}
