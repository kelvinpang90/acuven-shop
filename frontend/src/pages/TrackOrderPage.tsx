import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { memberOrderInView, readMemberOrder, runMemberConfirmReceipt } from "../api/memberOrders";
import type { MemberConfirmOutcome, MemberOrderRead } from "../api/memberOrders";
import { stashMemberPayOrder } from "../api/memberPay";
import { useMemberSessionReader } from "../api/memberSession";
import type { MemberSessionReader, MemberSessionState } from "../api/memberSession";
import {
  canConfirmReceipt,
  clearSubmittedRefund,
  readLookupOrder,
  receiptKey,
  REFUND_APPROVED,
  REFUND_REJECTED,
  REFUND_REQUESTED,
  runConfirmReceipt,
  showsRefundEntry,
  submittedRefundOrder,
} from "../api/orderLookup";
import type { ConfirmOutcome, LookupFound, LookupLine, LookupOrder, LookupRefund } from "../api/orderLookup";
import { AWAITING_PAYMENT } from "../api/pay";
import type { PayRecipient } from "../api/pay";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ACCOUNT_PATH, isPlainLeftClick, isRoutePath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import { InfoIcon, PAY_PATH, recipientParts, useMyStates } from "./PayPage";
import { TRACK_PATH } from "./TrackPage";

// 订单详情页 P09 的查单模式（docs/UX.md P09）：打开时以当前语言调用 GET /api/orders/lookup，取第一张订单（最近查询的）；
// 刚从退款申请页 P10 提交成功回来时取那一单并显示 refund.submitted。
// 之后重新读取（切换语言、确认收货之后）都按同一订单号取该单。401 或本浏览器对该单的授权已结束时整页显示 order.session_expired。
// 显示状态、进度五步、各行商品、金额明细与完整收货资料；只有 demo_shipped 且不是全部已退时可确认收货；待支付订单只说明查单不能支付或取消。
// 退款部分：接口判定在退款期内且剩余可退大于零时显示去 P10 的 order.request_refund 与截止时间；已支付时显示累计已退、剩余可退与申请记录；
// 接口判定全部已退时显示 order.fulfilment_frozen。
// 下单时是会员的订单显示优惠券与积分两行，游客订单与后来被认领的游客订单不显示（UX P09 M3），两种模式相同。
// 订单号与 CSRF 令牌只在页面内存（React 状态）里，不进网址或任何浏览器存储；页面不显示授权内容或其剩余时间。
// 金额只格式化接口返回的整数仙。
//
// 会员模式（/account/order，本文件的 MemberOrderPage；DESIGN 1.13「权限与资料保护」第 3、5 条，HANDOFF 0.61）：打开时经 api/memberSession.ts 的
// 读取对象重读一次会话（读完之前为 aria-busy 的空主体；未登录以 replace 进入 /login；失败只显示 common.error_retry）；已登录时先取 P10 提交后交来的订单号
// （取到即清除，记下以显示 refund.submitted），没有时取 P13 经 api/memberOrders.ts 交接的订单号，都没有（如整页刷新后）以 replace 回到 /account。
// 订单经会员接口读取，切换语言时重新读取同一订单；会员接口回 401 时重读会话并以 replace 进入 /login，回 404 时以 replace 回到 /account。
// 确认收货经会员接口、带会话里的 CSRF 令牌，幂等键规则同查单模式；令牌失效时重读会话换用新令牌。会员模式没有 order.session_expired 整页。
// 待支付订单有去 /pay 的 account.order_pay（UX P09 会员模式，DESIGN 1.14「权限与资料保护」第 7 条，HANDOFF 0.63）：点击时先经 api/memberPay.ts
// 把该单订单号写入当前订单暂存（覆盖之前的值），再以站内导航前往 /pay；订单号不进地址、查询参数与历史状态。
// 离开页面时中止请求，之后不再更新；会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转。

export const TRACK_ORDER_PATH: RoutePath = "/track/order";
export const REFUND_PATH: RoutePath = "/track/order/refund";

// 日期与时间按访客浏览器时区显示（UX P09 order.refund_deadline）；timeZone 只给测试固定时区用。
export function formatDateTime(iso: string, language: Language, timeZone?: string): string {
  const zone = timeZone === undefined ? {} : { timeZone };
  return new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeStyle: "short", ...zone }).format(new Date(iso));
}

// 只显示日期（申请记录的日期），同样按访客浏览器时区。
export function formatDate(iso: string, language: Language, timeZone?: string): string {
  const zone = timeZone === undefined ? {} : { timeZone };
  return new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", ...zone }).format(new Date(iso));
}

// 进度五步（UX P09 [order.progress]），按履约顺序。
const STEPS = ["awaiting_demo_payment", "demo_paid", "demo_packed", "demo_shipped", "demo_completed"] as const;
type Step = (typeof STEPS)[number];

const STEP_LABEL: Readonly<Record<Step, CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
};

const STATUS_LABEL: Readonly<Record<string, CopyKey>> = { ...STEP_LABEL, demo_cancelled: "order.status_cancelled" };

// 当前状态之前的步为 done，当前一步为 current，之后的与已取消订单的各步不标。
export function stepState(status: string, step: Step): "done" | "current" | undefined {
  const reached = (STEPS as readonly string[]).indexOf(status);
  const index = STEPS.indexOf(step);
  if (reached < 0 || index > reached) {
    return undefined;
  }
  return index === reached ? "current" : "done";
}

// 取自视觉稿 P09：待支付与已取消为描边标签，其余为主色标签。
function statusTagClass(status: string): string {
  return status === AWAITING_PAYMENT || status === "demo_cancelled" ? "acs-tag acs-tag--outline" : "acs-tag acs-tag--accent";
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="5" y="10" width="14" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

function SlashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M15 4L9 20" />
    </svg>
  );
}

export function usePrice(): (sen: number) => string {
  const t = useCopy();
  return (sen) => t("common.price_myr", { amount: formatSen(sen) });
}

// 名称 / 规格（有的话）与 × 件数（给了的话）；P10 的各行与 P09 的申请记录共用。
export function LineName({ name, variant, quantity }: { name: string; variant: string; quantity?: number }) {
  return (
    <span className="site-order__name">
      <span>{name}</span>
      {variant !== "" && (
        <span className="site-order__option">
          <SlashIcon />
          <span>{variant}</span>
        </span>
      )}
      {quantity !== undefined && <span className="acs-num">{`× ${String(quantity)}`}</span>}
    </span>
  );
}

// 以逗号分隔的几段收货资料；空段不显示。
function Joined({ values }: { values: readonly (string | null)[] }) {
  const present = values.filter((value): value is string => value !== null && value !== "");
  return present.map((value, index) => (
    <Fragment key={index}>
      {index > 0 && ", "}
      <span>{value}</span>
    </Fragment>
  ));
}

// 一行商品：名称 / 规格 × 件数，order.unit_price 与单价；order.cash_paid 与逐件实付（按件序，原样来自接口；未支付时没有）。
function OrderLine({ line }: { line: LookupLine }) {
  const t = useCopy();
  const price = usePrice();
  return (
    <li className="site-order__line">
      <span className="site-order__info">
        <LineName name={line.name} variant={line.variant_label} quantity={line.quantity} />
        <span className="acs-body-s acs-muted site-order__unit">
          <span>{t("order.unit_price")}</span>
          <span className="acs-num">{price(line.unit_price_sen)}</span>
        </span>
      </span>
      {line.unit_cash_paid_sen.length > 0 && (
        <span className="site-order__paid">
          <span className="acs-caption">{t("order.cash_paid")}</span>
          {line.unit_cash_paid_sen.map((sen, index) => (
            <span key={index} className="acs-price">
              {price(sen)}
            </span>
          ))}
        </span>
      )}
    </li>
  );
}

// 金额明细：商品小计、示例运费、合计；下单时是会员的订单（两种模式相同）在小计之后另有 checkout.summary_coupon 与
// checkout.summary_points（为 0 时也显示，原样格式化、不加负号；积分数即抵扣的仙数）。游客订单与后来被认领的游客订单不显示这两行（UX P09 M3）。
function AmountRows({ order }: { order: LookupOrder }) {
  const t = useCopy();
  const price = usePrice();
  return (
    <>
      <div className="acs-row">
        <span>{t("cart.subtotal")}</span>
        <span className="acs-num">{price(order.subtotal_sen)}</span>
      </div>
      {order.placed_by_member && (
        <>
          <div className="acs-row">
            <span>{t("checkout.summary_coupon")}</span>
            <span className="acs-num">{price(order.coupon_discount_sen)}</span>
          </div>
          <div className="acs-row">
            <span>{t("checkout.summary_points")}</span>
            <span className="acs-num">{price(order.points_redeemed)}</span>
          </div>
        </>
      )}
      <div className="acs-row">
        <span>{t("checkout.summary_shipping")}</span>
        <span className="acs-num">{price(order.shipping_fee_sen)}</span>
      </div>
      <div className="acs-row acs-row--total">
        <span>{t("checkout.summary_total")}</span>
        <span className="acs-num">{price(order.total_sen)}</span>
      </div>
    </>
  );
}

// 收货资料原文：桌面为卡片（姓名、电话、地址与邮编、地区与国家各一行），手机折叠（▸ checkout.recipient_title）。没有记录时不渲染。
function Recipient({ recipient, states }: { recipient: PayRecipient | null; states: ReadonlyMap<string, string> | null }) {
  const t = useCopy();
  const { language } = useLanguage();
  if (recipient === null) {
    return null;
  }
  const parts = recipientParts(recipient, language, states);
  return (
    <>
      <section className="acs-card site-desktop-only site-order__recipient">
        <h2 className="acs-display-s">{t("checkout.recipient_title")}</h2>
        <span>{parts.name}</span>
        <span className="acs-num">{parts.phone}</span>
        <span>
          <Joined values={[parts.address, parts.postcode]} />
        </span>
        <span>
          <Joined values={[parts.region, parts.country]} />
        </span>
      </section>
      <details className="acs-summary site-phone-only site-order__fold">
        <summary className="site-order__fold-head">{t("checkout.recipient_title")}</summary>
        <p className="acs-body-s">
          <span>{parts.name}</span>
          {" · "}
          <span>{parts.phone}</span>
          <br />
          <Joined values={[parts.address, parts.postcode, parts.region, parts.country]} />
        </p>
      </details>
    </>
  );
}

// 申请记录的状态名与标签样式（取自视觉稿 A03：审核中为演示标签、已批准为成功标签、已拒绝为描边标签）。
const REFUND_STATUS: Readonly<Record<string, { label: CopyKey; tag: string }>> = {
  [REFUND_REQUESTED]: { label: "order.refund_requested", tag: "acs-tag acs-tag--demo" },
  [REFUND_APPROVED]: { label: "order.refund_approved", tag: "acs-tag acs-tag--success" },
  [REFUND_REJECTED]: { label: "order.refund_rejected", tag: "acs-tag acs-tag--outline" },
};

// 退款申请记录：每笔为日期、各行商品与件数、申请金额与状态名（UX P09「<日期> <商品 x数量> [M4] [order.refund_requested|approved|rejected]」）。
function RefundRequestList({ requests }: { requests: readonly LookupRefund[] }) {
  const t = useCopy();
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <ul className="site-order__requests">
      {requests.map((request, index) => {
        const status = REFUND_STATUS[request.status];
        return (
          <li key={index} className="site-order__request">
            <span className="acs-num">{formatDate(request.created_at, language)}</span>
            <span className="site-order__request-items">
              {request.lines.map((line, lineIndex) => (
                <LineName key={lineIndex} name={line.name} variant={line.variant_label} quantity={line.quantity} />
              ))}
            </span>
            <span className="acs-num">{price(request.amount_sen)}</span>
            {status !== undefined && <span className={status.tag}>{t(status.label)}</span>}
          </li>
        );
      })}
    </ul>
  );
}

// 退款部分（只在已支付时渲染）：退款期内且有剩余可退时为 order.request_refund 与 order.refund_deadline；
// 之后为 order.refunded_total、order.refundable_left；有申请记录时桌面为列表，手机折叠（▸ order.refund_requests）。
// refundTo 为 order.request_refund 的去向；为 null 时（会员模式的退款页尚未进路由表）不渲染这个链接，截止时间照常。
function RefundPart({ order, refundTo }: { order: LookupOrder; refundTo: RoutePath | null }) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <>
      {showsRefundEntry(order) && (
        <>
          {refundTo !== null && (
            <Link className="acs-btn acs-btn--secondary acs-btn--block" to={refundTo}>
              {t("order.request_refund")}
            </Link>
          )}
          {order.refund_deadline !== null && (
            <p className="acs-body-s acs-muted">{t("order.refund_deadline", { date: formatDateTime(order.refund_deadline, language) })}</p>
          )}
        </>
      )}
      <div className="site-order__refunded">
        <span className="acs-num">{t("order.refunded_total", { amount: formatSen(order.refunded_total_sen) })}</span>
        <span className="acs-num">{t("order.refundable_left", { amount: formatSen(order.refundable_left_sen) })}</span>
      </div>
      {order.refund_requests.length > 0 && (
        <>
          <div className="site-desktop-only site-order__history">
            <h2 className="acs-field__label">{t("order.refund_requests")}</h2>
            <RefundRequestList requests={order.refund_requests} />
          </div>
          <details className="site-phone-only site-order__fold">
            <summary className="site-order__fold-head">{t("order.refund_requests")}</summary>
            <RefundRequestList requests={order.refund_requests} />
          </details>
        </>
      )}
    </>
  );
}

// 页面看到的订单：读取中、授权过期、读取失败、已取到。
export type OrderScreen = { status: "loading" } | { status: "expired" } | { status: "error" } | { status: "ready"; order: LookupOrder };

// 正在进行的操作：提交确认收货、网络中断后确认订单；进行中按钮禁用。
export type ConfirmBusy = "confirming" | "checking" | null;

// 页面的模式：查单模式（/track/order），或会员模式（/account/order；refundTo 为 order.request_refund 的去向，没有时不渲染该链接）。
export type TrackOrderMode = { kind: "lookup" } | { kind: "member"; refundTo: RoutePath | null };

const LOOKUP_MODE: TrackOrderMode = { kind: "lookup" };

export interface TrackOrderViewProps {
  screen: OrderScreen;
  states: ReadonlyMap<string, string> | null;
  busy: ConfirmBusy;
  // 显示 common.error_retry。
  failed: boolean;
  // 刚从 P10 提交退款申请回来：显示 refund.submitted。
  submitted: boolean;
  onConfirm: () => void;
  // 不给时为查单模式。
  mode?: TrackOrderMode | undefined;
  // 会员模式点 account.order_pay（普通左键点击）：写入暂存并前往 /pay（continuePayment）。不给时由链接自己前往 /pay。
  onPay?: (() => void) | undefined;
}

// 查单授权过期或不属于该单时替换整页：order.session_expired 与去 P08 的 common.nav_track。P10 共用。
export function SessionExpired() {
  const t = useCopy();
  return (
    <main className="site-order site-order--expired">
      <div className="acs-alert acs-alert--info" role="status">
        <LockIcon />
        <span>{t("order.session_expired")}</span>
      </div>
      <Link className="acs-btn acs-btn--primary" to={TRACK_PATH}>
        {t("common.nav_track")}
      </Link>
    </main>
  );
}

// 查单模式在标题之后是 order.lookup_access 与回 P08 的 order.lookup_another；会员模式换成回 P13 的 account.orders（桌面与手机各一处），
// 退款入口去会员模式的退款页，待支付订单在 order.lookup_no_pay 的位置换成去 /pay 的 account.order_pay（主按钮样式的链接）。其余两种模式相同。
export function TrackOrderView({ screen, states, busy, failed, submitted, onConfirm, mode = LOOKUP_MODE, onPay }: TrackOrderViewProps) {
  const t = useCopy();
  const price = usePrice();

  if (screen.status === "loading") {
    return <main className="site-order" aria-busy="true"></main>;
  }
  if (screen.status === "expired") {
    return <SessionExpired />;
  }
  if (screen.status === "error") {
    return (
      <main className="site-order">
        <ErrorNotice />
      </main>
    );
  }

  const { order } = screen;
  const label = STATUS_LABEL[order.status];
  const confirmable = canConfirmReceipt(order);
  const paid = order.paid_at !== null;
  const member = mode.kind === "member";
  return (
    <main className="site-order" aria-busy={busy !== null}>
      <div className="site-order__head">
        <div className="site-order__title">
          <h1 className="acs-display-l">{t("order.title", { orderNo: order.order_number })}</h1>
          {label !== undefined && (
            <span className="site-order__status">
              <span className="acs-body-s">{t("order.current_status")}</span>
              <span className={statusTagClass(order.status)}>{t(label)}</span>
            </span>
          )}
        </div>
        <DemoHint>{t("order.demo_hint")}</DemoHint>
      </div>
      {submitted && (
        <p className="acs-alert acs-alert--success" role="status">
          {t("refund.submitted")}
        </p>
      )}
      {member ? (
        <>
          <Link className="site-desktop-only site-order__orders" to={ACCOUNT_PATH}>
            {t("account.orders")}
          </Link>
          <Link className="site-phone-only site-order__orders" to={ACCOUNT_PATH}>
            {t("account.orders")}
          </Link>
        </>
      ) : (
        <>
          <div className="acs-alert acs-alert--info site-desktop-only site-order__access">
            <span className="site-order__access-text">
              <LockIcon />
              <span>{t("order.lookup_access")}</span>
            </span>
            <Link className="site-order__another" to={TRACK_PATH}>
              {t("order.lookup_another")}
            </Link>
          </div>
          <div className="site-phone-only site-order__access-phone">
            <p className="acs-body-s">{t("order.lookup_access")}</p>
            <Link className="site-order__another" to={TRACK_PATH}>
              {t("order.lookup_another")}
            </Link>
          </div>
        </>
      )}
      <div className="site-order__progress">
        <span className="acs-field__label">{t("order.progress")}</span>
        <ol className="acs-progress">
          {STEPS.map((step) => {
            const state = stepState(order.status, step);
            return (
              <li key={step} data-state={state} aria-current={state === "current" ? "step" : undefined}>
                {t(STEP_LABEL[step])}
              </li>
            );
          })}
        </ol>
      </div>
      <div className="site-order__body">
        <section className="site-order__main">
          <h2 className="acs-display-s">{t("order.items")}</h2>
          <ul className="site-order__lines">
            {order.lines.map((line, index) => (
              <OrderLine key={index} line={line} />
            ))}
          </ul>
          <div className="acs-summary site-desktop-only site-order__amounts">
            <AmountRows order={order} />
          </div>
          <details className="acs-summary site-phone-only site-order__fold">
            <summary className="site-order__fold-head">
              <span>{t("order.amount_breakdown")}</span>
              <span className="acs-num">{price(order.total_sen)}</span>
            </summary>
            <AmountRows order={order} />
          </details>
        </section>
        <aside className="site-order__aside">
          <Recipient recipient={order.recipient} states={states} />
          {(confirmable || paid) && (
            <section className="acs-card site-order__actions">
              {confirmable && (
                <>
                  <button className="acs-btn acs-btn--primary acs-btn--block" type="button" disabled={busy !== null} onClick={onConfirm}>
                    {t("order.confirm_receipt")}
                  </button>
                  <p className="acs-body-s acs-muted">{t("order.confirm_receipt_hint")}</p>
                  {busy === "checking" && (
                    <div className="acs-alert acs-alert--info" role="status">
                      <InfoIcon />
                      <span>{t("common.network_check")}</span>
                    </div>
                  )}
                </>
              )}
              {paid && <RefundPart order={order} refundTo={mode.kind === "member" ? mode.refundTo : REFUND_PATH} />}
            </section>
          )}
          {/* 不放在确认收货卡片里：403 后重新读取到的订单可能已不是 demo_shipped，提示仍要显示。 */}
          {failed && busy === null && <ErrorNotice />}
          {order.fully_refunded && (
            <div className="acs-alert acs-alert--info">
              <InfoIcon />
              <span>{t("order.fulfilment_frozen")}</span>
            </div>
          )}
          {order.status === AWAITING_PAYMENT &&
            (member ? (
              <Link
                className="acs-btn acs-btn--primary site-order__pay"
                to={PAY_PATH}
                onClick={(event) => {
                  if (onPay !== undefined && isPlainLeftClick(event)) {
                    event.preventDefault();
                    onPay();
                  }
                }}
              >
                {t("account.order_pay")}
              </Link>
            ) : (
              <div className="acs-alert acs-alert--info">
                <InfoIcon />
                <span>{t("order.lookup_no_pay")}</span>
              </div>
            ))}
        </aside>
      </div>
    </main>
  );
}

export default function TrackOrderPage() {
  const { language } = useLanguage();
  const [screen, setScreen] = useState<OrderScreen>({ status: "loading" });
  // CSRF 令牌只在内存里：每次读取订单时由接口给出。
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [busy, setBusy] = useState<ConfirmBusy>(null);
  const [failed, setFailed] = useState(false);
  // 网络中断后确认仍为 demo_shipped 的那次确认收货的幂等键：下一次点击沿用它。
  const [retryKey, setRetryKey] = useState<string | null>(null);
  // 刚从 P10 提交成功回来时那张订单的订单号（只在页面内存里交接，打开后即清除）。
  const [submittedFor] = useState(submittedRefundOrder);
  // 已显示的订单号：之后的重新读取取同一张订单；从 P10 回来时一开始就取那一单。只在副作用与回调里读写。
  const shownOrder = useRef<string | null>(submittedFor);
  // 离开页面时中止进行中的确认收货与确认。
  const lifetime = useRef<AbortController | null>(null);

  const order = screen.status === "ready" ? screen.order : null;
  const states = useMyStates(order?.recipient ?? null);

  const show = useCallback((read: LookupFound) => {
    shownOrder.current = read.order.order_number;
    setScreen({ status: "ready", order: read.order });
    setCsrfToken(read.csrfToken);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    clearSubmittedRefund();
    return () => {
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void readLookupOrder(language, shownOrder.current, controller.signal).then((read) => {
      if (controller.signal.aborted) {
        return;
      }
      if (read.kind === "ok") {
        show(read);
      } else if (read.kind === "expired") {
        setScreen({ status: "expired" });
      } else {
        setScreen((current) => (current.status === "ready" ? current : { status: "error" }));
        setFailed(true);
      }
    });
    return () => {
      controller.abort();
    };
  }, [language, show]);

  const settle = (outcome: ConfirmOutcome | null) => {
    if (outcome === null) {
      return;
    }
    switch (outcome.kind) {
      case "expired":
        setScreen({ status: "expired" });
        break;
      case "read":
        show(outcome.read);
        setRetryKey(null);
        break;
      case "error":
        if (outcome.read) {
          show(outcome.read);
        }
        setRetryKey(null);
        setFailed(true);
        break;
      case "retry":
        show(outcome.read);
        setRetryKey(outcome.key);
        setFailed(true);
        break;
    }
    setBusy(null);
  };

  return (
    <TrackOrderView
      screen={screen}
      states={states}
      busy={busy}
      failed={failed}
      submitted={order !== null && order.order_number === submittedFor}
      onConfirm={() => {
        if (order === null || csrfToken === null || busy !== null || !canConfirmReceipt(order)) {
          return;
        }
        const key = receiptKey(retryKey);
        setBusy("confirming");
        setFailed(false);
        void runConfirmReceipt(language, order.order_number, csrfToken, key, {
          signal: lifetime.current?.signal,
          onChecking: () => {
            setBusy("checking");
          },
        }).then(settle);
      }}
    />
  );
}

// 以下为会员模式（/account/order）。

const LOGIN_PATH: RoutePath = "/login";

// 会员模式退款申请 P10 的路径；进了路由表（SHOP-TASK-155）才渲染 order.request_refund。
export function memberRefundPath(): RoutePath | null {
  const path: string = "/account/order/refund";
  return isRoutePath(path) ? path : null;
}

// 点 account.order_pay：先把该单订单号写入当前订单暂存（stashMemberPayOrder，覆盖之前的值，DESIGN 1.14 第 7 条），再以站内导航前往 /pay。
export function continuePayment(orderNumber: string, navigate: (path: RoutePath) => void, stash: (orderNumber: string) => void = stashMemberPayOrder): void {
  stash(orderNumber);
  navigate(PAY_PATH);
}

// 会员模式打开时取到的订单：订单号，以及是否刚从 P10 提交退款申请回来（显示 refund.submitted）。
export interface MemberOrderPick {
  orderNumber: string;
  submitted: boolean;
}

// 先取 P10 提交后交来的订单号（取到即清除，照查单模式），没有时取 P13 交接的订单号（读取不清除，P13 交出新值时覆盖）；都没有时为 null。
export function takeMemberOrder(): MemberOrderPick | null {
  const submitted = submittedRefundOrder();
  if (submitted !== null) {
    clearSubmittedRefund();
    return { orderNumber: submitted, submitted: true };
  }
  const viewing = memberOrderInView();
  return viewing === null ? null : { orderNumber: viewing, submitted: false };
}

// 会员模式的页面状态：读取会话中（空主体，标 aria-busy）、读取会话失败（只有 common.error_retry）、已登录。
// 已登录时 orderNumber 与 submitted 来自打开时取到的订单，csrfToken 来自打开时那次读取（csrf 之后的重读更新令牌）；
// order 为 null 时订单还没取到（此时 failed 为真即首次读取失败，只显示 common.error_retry）；busy、failed 与 retryKey 同查单模式。
export type MemberOrderState =
  | { status: "loading" }
  | { status: "failed" }
  | {
      status: "member";
      orderNumber: string;
      submitted: boolean;
      csrfToken: string;
      order: LookupOrder | null;
      busy: ConfirmBusy;
      failed: boolean;
      retryKey: string | null;
    };

type SignedIn = Extract<MemberOrderState, { status: "member" }>;

export const MEMBER_LOADING: MemberOrderState = { status: "loading" };

// 按打开时重读到的会话决定：member 时才取订单号（take），没有订单号时回 /account（account）；guest 去 /login（login）；
// failed 只显示 common.error_retry。重读的回答处理之后状态不会是 unknown，万一是也按失败处理。
export function openStep(state: MemberSessionState, take: () => MemberOrderPick | null): MemberOrderState | "login" | "account" {
  switch (state.kind) {
    case "member": {
      const pick = take();
      if (pick === null) {
        return "account";
      }
      return { status: "member", ...pick, csrfToken: state.csrfToken, order: null, busy: null, failed: false, retryKey: null };
    }
    case "guest":
      return "login";
    case "failed":
    case "unknown":
      return { status: "failed" };
  }
}

// 对当前状态的一次改动（请求的结果按到达时的状态更新）；只改已登录的状态。
export type MemberOrderChange = (current: MemberOrderState) => MemberOrderState;

function signedIn(change: (current: SignedIn) => SignedIn): MemberOrderChange {
  return (current) => (current.status === "member" ? change(current) : current);
}

// 按读取订单的结果决定：ok 显示该单；signed_out 重读会话后去 /login（login）；not_found 回 /account（account）；
// failed 与 network 显示 common.error_retry（已显示的订单保留，照查单模式）。
export function readStep(read: MemberOrderRead): MemberOrderChange | "login" | "account" {
  switch (read.kind) {
    case "ok":
      return signedIn((current) => ({ ...current, order: read.order }));
    case "signed_out":
      return "login";
    case "not_found":
      return "account";
    case "failed":
    case "network":
      return signedIn((current) => ({ ...current, failed: true }));
  }
}

// 点确认收货：按钮禁用，先前的提示收起。
export const startConfirm: MemberOrderChange = signedIn((current) => ({ ...current, busy: "confirming", failed: false }));

// 网络中断、开始确认订单：显示 common.network_check。
export const startChecking: MemberOrderChange = signedIn((current) => ({ ...current, busy: "checking" }));

// 按确认收货的去向决定（csrf 由 csrfStep 处理）：read 按重新读取到的订单显示；error 显示 common.error_retry（没有订单时沿用原来的）；
// retry 显示 common.error_retry 并记下幂等键供下一次点击沿用；signed_out 重读会话后去 /login（login）；not_found 回 /account（account）。
export function confirmStep(outcome: Exclude<MemberConfirmOutcome, { kind: "csrf" }>): MemberOrderChange | "login" | "account" {
  switch (outcome.kind) {
    case "read":
      return signedIn((current) => ({ ...current, order: outcome.order, busy: null, retryKey: null }));
    case "error":
      return signedIn((current) => ({ ...current, order: outcome.order ?? current.order, busy: null, failed: true, retryKey: null }));
    case "retry":
      return signedIn((current) => ({ ...current, order: outcome.order, busy: null, failed: true, retryKey: outcome.key }));
    case "signed_out":
      return "login";
    case "not_found":
      return "account";
  }
}

// 令牌失效（403 csrf_failed）之后按重读到的会话决定：guest 去 /login（login）；member 换用新令牌，failed（含网络中断）保留原令牌，
// 两者都显示 common.error_retry，并按重新读取到的订单显示（没有时沿用原来的）。
export function csrfStep(order: LookupOrder | null, state: MemberSessionState): MemberOrderChange | "login" {
  if (state.kind === "guest") {
    return "login";
  }
  return signedIn((current) => ({
    ...current,
    order: order ?? current.order,
    csrfToken: state.kind === "member" ? state.csrfToken : current.csrfToken,
    busy: null,
    failed: true,
    retryKey: null,
  }));
}

// 交给 TrackOrderView 的画面：会话或订单读取中为 loading；会话读取失败或订单首次读取失败为 error；取到订单后为 ready。没有 expired。
export function memberScreen(state: MemberOrderState): OrderScreen {
  if (state.status === "loading") {
    return { status: "loading" };
  }
  if (state.status === "failed") {
    return { status: "error" };
  }
  if (state.order === null) {
    return state.failed ? { status: "error" } : { status: "loading" };
  }
  return { status: "ready", order: state.order };
}

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 前往 /login 或 /account。
export interface MemberOrderMoves {
  show: (state: MemberOrderState) => void;
  update: (change: MemberOrderChange) => void;
  replace: (path: RoutePath) => void;
}

// 重读会话（共享读取，不中止）之后以 replace 前往 path；signal 已中止（离开页面）时不跳转。
async function leaveAfterReload(reader: MemberSessionReader, signal: AbortSignal, path: RoutePath, replace: (path: RoutePath) => void): Promise<void> {
  await reader.reload();
  if (!signal.aborted) {
    replace(path);
  }
}

// 打开页面：重读一次会话，按结果取订单号并显示（订单由页面另行读取）、进入 /login 或回到 /account。signal 中止之后不再更新或跳转。
export async function openMemberOrder(reader: MemberSessionReader, signal: AbortSignal, moves: MemberOrderMoves): Promise<void> {
  const state = await reader.reload();
  if (signal.aborted) {
    return;
  }
  const step = openStep(state, takeMemberOrder);
  if (step === "login") {
    moves.replace(LOGIN_PATH);
  } else if (step === "account") {
    moves.replace(ACCOUNT_PATH);
  } else {
    moves.show(step);
  }
}

// 以当前语言读取该单（打开后与切换语言时）；signal 中止之后不再更新或跳转。
export async function loadMemberOrder(reader: MemberSessionReader, language: Language, orderNumber: string, signal: AbortSignal, moves: MemberOrderMoves): Promise<void> {
  const read = await readMemberOrder(language, orderNumber, signal);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read);
  if (step === "login") {
    await leaveAfterReload(reader, signal, LOGIN_PATH, moves.replace);
  } else if (step === "account") {
    moves.replace(ACCOUNT_PATH);
  } else {
    moves.update(step);
  }
}

// 一次确认收货：订单号、会话里的 CSRF 令牌与幂等键（receiptKey）。
export interface MemberConfirmTarget {
  orderNumber: string;
  csrfToken: string;
  key: string;
}

// 提交确认收货并按去向更新或跳转；csrf 时重读会话再决定。signal 中止之后不再更新或跳转。
export async function confirmMemberOrder(
  reader: MemberSessionReader,
  language: Language,
  target: MemberConfirmTarget,
  signal: AbortSignal,
  moves: MemberOrderMoves,
): Promise<void> {
  const outcome = await runMemberConfirmReceipt(language, target.orderNumber, target.csrfToken, target.key, {
    signal,
    onChecking: () => {
      moves.update(startChecking);
    },
  });
  if (outcome === null || signal.aborted) {
    return;
  }
  if (outcome.kind === "csrf") {
    const state = await reader.reload();
    if (signal.aborted) {
      return;
    }
    const step = csrfStep(outcome.order, state);
    if (step === "login") {
      moves.replace(LOGIN_PATH);
    } else {
      moves.update(step);
    }
    return;
  }
  const step = confirmStep(outcome);
  if (step === "login") {
    await leaveAfterReload(reader, signal, LOGIN_PATH, moves.replace);
  } else if (step === "account") {
    moves.replace(ACCOUNT_PATH);
  } else {
    moves.update(step);
  }
}

function memberMoves(setState: Dispatch<SetStateAction<MemberOrderState>>, replace: (path: RoutePath) => void): MemberOrderMoves {
  return {
    show: (state) => {
      setState(state);
    },
    update: (change) => {
      setState(change);
    },
    replace,
  };
}

export function MemberOrderPage() {
  const { language } = useLanguage();
  const { navigate, replace } = useRouter();
  const reader = useMemberSessionReader();
  const [state, setState] = useState<MemberOrderState>(MEMBER_LOADING);
  // 确认收货用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  const member = state.status === "member" ? state : null;
  const orderNumber = member === null ? null : member.orderNumber;
  const states = useMyStates(member?.order?.recipient ?? null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openMemberOrder(reader, controller.signal, memberMoves(setState, replace));
    });
  }, [reader, replace]);

  // 取到订单号之后读取该单；切换语言时重新读取同一订单，并中止上一次读取。
  useEffect(() => {
    if (orderNumber === null) {
      return;
    }
    const controller = new AbortController();
    void loadMemberOrder(reader, language, orderNumber, controller.signal, memberMoves(setState, replace));
    return () => {
      controller.abort();
    };
  }, [reader, replace, language, orderNumber]);

  return (
    <TrackOrderView
      screen={memberScreen(state)}
      states={states}
      busy={member === null ? null : member.busy}
      failed={member !== null && member.failed}
      submitted={member !== null && member.submitted}
      mode={{ kind: "member", refundTo: memberRefundPath() }}
      onConfirm={() => {
        const controller = lifetime.current;
        if (member === null || member.order === null || controller === null || member.busy !== null || !canConfirmReceipt(member.order)) {
          return;
        }
        const target = { orderNumber: member.orderNumber, csrfToken: member.csrfToken, key: receiptKey(member.retryKey) };
        setState(startConfirm);
        void confirmMemberOrder(reader, language, target, controller.signal, memberMoves(setState, replace));
      }}
      onPay={() => {
        if (member !== null) {
          continuePayment(member.orderNumber, navigate);
        }
      }}
    />
  );
}
