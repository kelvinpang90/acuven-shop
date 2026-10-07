import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { advanceAdminOrder, readAdminOrder } from "../api/adminOrders";
import type { ActorType, AdminOrderDetail as OrderDetail, AdminOrderEvent, AdvanceTarget, DetailRead, OrderStatus } from "../api/adminOrders";
import { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_ORDERS_PATH, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import { recipientParts, useMyStates } from "./PayPage";
import { formatDateTime, LineName, usePrice } from "./TrackOrderPage";

// 后台订单 A02 的订单详情与模拟发货（docs/UX.md 0.10 A02 与「状态与补充（0.9）」，视觉稿 A02-desktop、A02-desktop-frozen 与 A02-phone-detail）：
// 桌面在订单列表右侧，手机只显示详情（顶部为返回订单列表的 common.back，只在手机显示）。
// 打开时以当前界面语言调用 GET /api/admin/orders/{内部 ID} 一次（服务端每次查看都写审计）；之后只在推进之后重新读取。
// 显示标题与状态、各行商品、金额明细、原始收货资料与审计提示、推进按钮与提示、事件记录；手机的金额明细、收货资料与事件记录为可折叠区块。
// 推进：demo_paid 时可标记已打包，demo_packed 时可标记已发货，其余状态与全部已退时都禁用（全部已退另显示 admin.frozen）。
// 订单不存在（404）或路径 ID 不合法时只显示 common.error_retry 与 common.back（Kelvin 2026-10-07）。
// 401 换成登录页 A01；离开页面或换到另一张订单时中止旧请求，旧请求的结果不再更新页面。
// 订单号、收货资料与 CSRF 令牌只在 React 状态与请求里，不进网址、浏览器存储或日志；页面标记里没有令牌。

// 状态名（order.status_*）与标签样式（取自视觉稿 A02：已取消为描边标签，其余为中性标签）。
export const STATUS_LABEL: Readonly<Record<OrderStatus, CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
  demo_cancelled: "order.status_cancelled",
};

export function statusTagClass(status: OrderStatus): string {
  return status === "demo_cancelled" ? "acs-tag acs-tag--outline" : "acs-tag acs-tag--neutral";
}

// 操作者类别（UX A02 [admin.actor_*]）：访客与会员都是顾客。
const ACTOR_LABEL: Readonly<Record<ActorType, CopyKey>> = {
  guest: "admin.actor_customer",
  member: "admin.actor_customer",
  admin: "admin.actor_admin",
  system: "admin.actor_system",
};

// 每个推进目标只在一种当前状态下可用：打包须已支付，发货须已打包；全部已退时都不可用。
const ADVANCE_FROM: Readonly<Record<AdvanceTarget, OrderStatus>> = {
  demo_packed: "demo_paid",
  demo_shipped: "demo_packed",
};

export function canAdvance(order: Pick<OrderDetail, "status" | "fully_refunded">, target: AdvanceTarget): boolean {
  return !order.fully_refunded && order.status === ADVANCE_FROM[target];
}

// 详情位置显示的内容：读取中、订单不存在（含路径 ID 不合法）、读取失败、已取到（带推进用的 CSRF 令牌）。
export type DetailScreen =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "failed" }
  | { status: "ready"; order: OrderDetail; csrfToken: string };

// 详情的状态：显示的内容、是否有请求进行中（推进与之后的重新读取；进行中按钮禁用）与提示。
export interface DetailState {
  screen: DetailScreen;
  busy: boolean;
  error: CopyKey | null;
}

export const INITIAL_DETAIL: DetailState = { screen: { status: "loading" }, busy: true, error: null };

// 按读取结果决定：取到显示详情；404 与不合法 ID 只显示 common.error_retry 与返回；401 回到 A01（login）；
// 网络中断显示 common.network_check，其他失败显示 common.error_retry。
export function readStep(read: DetailRead): DetailState | "login" {
  switch (read.kind) {
    case "ok":
      return { screen: { status: "ready", order: read.order, csrfToken: read.csrfToken }, busy: false, error: null };
    case "none":
      return "login";
    case "missing":
      return { screen: { status: "missing" }, busy: false, error: null };
    case "network":
      return { screen: { status: "failed" }, busy: false, error: "common.network_check" };
    case "failed":
      return { screen: { status: "failed" }, busy: false, error: "common.error_retry" };
  }
}

// 页面对结果的处理：显示详情状态，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface DetailMoves {
  show: (state: DetailState) => void;
  replace: (path: RoutePath) => void;
}

// 打开详情：读取一次；signal 中止（离开页面或已换到另一张订单）之后不再更新。
export async function openDetail(orderId: string, language: Language, signal: AbortSignal, moves: DetailMoves): Promise<void> {
  const read = await readAdminOrder(orderId, language, signal);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

type ReadyScreen = Extract<DetailScreen, { status: "ready" }>;

// 推进：200 后重新读取详情；409（不可推进或已冻结）与 403 也重新读取并显示 common.error_retry；401（推进或重新读取）回到 A01；
// 网络中断显示 common.network_check（再点即重发），其他失败显示 common.error_retry，都保留原来的详情。
// 重新读取失败时保留原来的详情并显示读取的提示。signal 中止之后不再更新。
export async function advanceOrder(
  orderId: string,
  current: ReadyScreen,
  target: AdvanceTarget,
  language: Language,
  signal: AbortSignal,
  moves: DetailMoves,
): Promise<void> {
  const reply = await advanceAdminOrder(orderId, target, current.csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  switch (reply) {
    case "none":
      moves.replace(ADMIN_LOGIN_PATH);
      return;
    case "network":
      moves.show({ screen: current, busy: false, error: "common.network_check" });
      return;
    case "failed":
      moves.show({ screen: current, busy: false, error: "common.error_retry" });
      return;
    case "done":
    case "conflict":
    case "csrf":
      break;
  }
  const read = await readAdminOrder(orderId, language, signal);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
    return;
  }
  if (step.screen.status === "failed") {
    moves.show({ screen: current, busy: false, error: step.error });
    return;
  }
  moves.show({ ...step, error: reply === "done" ? null : "common.error_retry" });
}

function BackIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}

// ★ 操作旁的演示提示（视觉稿 A02 的发货提示：菱形标记加文字）。
function ShipHint({ children }: { children: ReactNode }) {
  return (
    <p className="acs-hint site-admin-order__hint">
      <svg className="acs-hint__mark" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3l9 9-9 9-9-9z" />
      </svg>
      <span>{children}</span>
    </p>
  );
}

// 一行：左为名称，右为金额。
function Row({ label, value, total = false }: { label: ReactNode; value: string; total?: boolean }) {
  return (
    <div className="site-admin-order__row">
      {total ? <strong>{label}</strong> : <span>{label}</span>}
      {total ? <strong>{value}</strong> : <span>{value}</span>}
    </div>
  );
}

// 金额明细（UX A02 [order.amount_breakdown] [M1]）：商品小计、优惠券抵扣、积分抵扣、示例运费与合计；后台对游客订单也照常显示券与积分两行。
function AmountRows({ order }: { order: OrderDetail }) {
  const t = useCopy();
  const price = usePrice();
  return (
    <>
      <Row label={t("cart.subtotal")} value={price(order.subtotal_sen)} />
      <Row label={t("checkout.summary_coupon")} value={price(order.coupon_discount_sen)} />
      <Row label={t("checkout.summary_points")} value={price(order.points_discount_sen)} />
      <Row label={t("checkout.summary_shipping")} value={price(order.shipping_fee_sen)} />
      <Row label={t("checkout.summary_total")} value={price(order.total_sen)} total />
    </>
  );
}

// 收货资料原文（UX A02 [admin.recipient_raw] [P1]，与前台 P09 相同的拆分）：姓名 · 电话，其下为地址、邮编、州属或地区与国家；再下为审计提示。
function RecipientLines({ order, states }: { order: OrderDetail; states: ReadonlyMap<string, string> | null }) {
  const t = useCopy();
  const { language } = useLanguage();
  if (order.recipient === null) {
    return null;
  }
  const parts = recipientParts(order.recipient, language, states);
  const place = [parts.address, parts.postcode, parts.region, parts.country].filter((value): value is string => value !== null && value !== "");
  return (
    <>
      <span>
        <span>{parts.name}</span>
        <span aria-hidden="true"> · </span>
        <span>{parts.phone}</span>
      </span>
      <span>
        {place.map((value, index) => (
          <span key={index}>
            {index > 0 && ", "}
            {value}
          </span>
        ))}
      </span>
      <span className="acs-admin__muted">{t("admin.recipient_audited")}</span>
    </>
  );
}

// 手机事件记录的一条：变更后状态 · 操作者，其下为时间。
function EventItem({ event }: { event: AdminOrderEvent }) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <li className="site-admin-order__event">
      <span>
        <span>{t(STATUS_LABEL[event.status])}</span>
        <span aria-hidden="true"> · </span>
        <span>{t(ACTOR_LABEL[event.actor_type])}</span>
      </span>
      <span className="acs-admin__muted">{formatDateTime(event.created_at, language)}</span>
    </li>
  );
}

interface ReadyProps {
  screen: ReadyScreen;
  busy: boolean;
  error: CopyKey | null;
  states: ReadonlyMap<string, string> | null;
  onAdvance: (target: AdvanceTarget) => void;
}

// 推进按钮：可用时为主按钮，禁用时为次要按钮（取自视觉稿 A02-desktop 与 A02-desktop-frozen）。
function AdvanceButton({ order, target, busy, label, onAdvance }: { order: OrderDetail; target: AdvanceTarget; busy: boolean; label: CopyKey; onAdvance: (target: AdvanceTarget) => void }) {
  const t = useCopy();
  const usable = canAdvance(order, target);
  return (
    <button
      className={usable ? "acs-admin__btn site-admin-order__button" : "acs-admin__btn acs-admin__btn--secondary site-admin-order__button"}
      type="button"
      disabled={!usable || busy}
      onClick={() => {
        onAdvance(target);
      }}
    >
      {t(label)}
    </button>
  );
}

function ReadyDetail({ screen, busy, error, states, onAdvance }: ReadyProps) {
  const t = useCopy();
  const price = usePrice();
  const { language } = useLanguage();
  const { order } = screen;
  const hasRecipient = order.recipient !== null;
  return (
    <>
      <div className="site-admin-order__head">
        <h2 className="acs-admin__h site-admin-order__title">
          <span>{t("admin.order_detail")}</span>
          <span aria-hidden="true"> · </span>
          <span>{order.order_number}</span>
        </h2>
        <span className={statusTagClass(order.status)}>{t(STATUS_LABEL[order.status])}</span>
      </div>
      <div className="acs-admin__panel site-admin-order__items">
        <span className="acs-admin__muted">{t("order.items")}</span>
        {order.lines.map((line, index) => (
          <Row key={index} label={<LineName name={line.name} variant={line.variant_label} quantity={line.quantity} />} value={price(line.line_subtotal_sen)} />
        ))}
      </div>
      <div className="site-admin-order__block site-admin-order__wide">
        <span className="acs-admin__muted">{t("order.amount_breakdown")}</span>
        <AmountRows order={order} />
      </div>
      <details className="acs-admin__panel site-admin-order__fold">
        <summary className="site-admin-order__summary">
          <span>{t("order.amount_breakdown")}</span>
          <span>{price(order.total_sen)}</span>
        </summary>
        <div className="site-admin-order__fold-body">
          <AmountRows order={order} />
        </div>
      </details>
      {hasRecipient && (
        <>
          <div className="site-admin-order__block site-admin-order__wide">
            <span className="acs-admin__muted">{t("admin.recipient_raw")}</span>
            <RecipientLines order={order} states={states} />
          </div>
          <details className="acs-admin__panel site-admin-order__fold" open>
            <summary className="site-admin-order__summary">{t("admin.recipient_raw")}</summary>
            <div className="site-admin-order__fold-body">
              <RecipientLines order={order} states={states} />
            </div>
          </details>
        </>
      )}
      <div className="site-admin-order__block site-admin-order__actions">
        <div className="site-admin-order__buttons">
          <AdvanceButton order={order} target="demo_packed" busy={busy} label="admin.mark_packed" onAdvance={onAdvance} />
          <AdvanceButton order={order} target="demo_shipped" busy={busy} label="admin.mark_shipped" onAdvance={onAdvance} />
        </div>
        {order.fully_refunded && (
          <p className="acs-admin__panel site-admin-order__frozen" role="status">
            {t("admin.frozen")}
          </p>
        )}
        {error !== null && <AdminAlert error={error} />}
        <ShipHint>{t("admin.ship_hint")}</ShipHint>
      </div>
      <div className="site-admin-order__block site-admin-order__wide">
        <span className="acs-admin__muted">{t("admin.event_log")}</span>
        <table className="acs-admin__table">
          <thead>
            <tr>
              <th>{t("admin.col_time")}</th>
              <th>{t("admin.col_event")}</th>
              <th>{t("admin.col_actor")}</th>
            </tr>
          </thead>
          <tbody>
            {order.events.map((event, index) => (
              <tr key={index}>
                <td>{formatDateTime(event.created_at, language)}</td>
                <td>{t(STATUS_LABEL[event.status])}</td>
                <td>{t(ACTOR_LABEL[event.actor_type])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="acs-admin__panel site-admin-order__fold">
        <summary className="site-admin-order__summary">{t("admin.event_log")}</summary>
        <ul className="site-admin-order__fold-body site-admin-order__events">
          {order.events.map((event, index) => (
            <EventItem key={index} event={event} />
          ))}
        </ul>
      </details>
    </>
  );
}

export interface AdminOrderDetailViewProps {
  state: DetailState;
  // 马来西亚州属代码与名称（读取前或失败时为 null，显示代码）。
  states: ReadonlyMap<string, string> | null;
  onAdvance: (target: AdvanceTarget) => void;
}

// 详情位置：顶部为只在手机显示的 common.back；其下按状态为详情、读取失败的提示或订单不存在时的 common.error_retry。
export function AdminOrderDetailView({ state, states, onAdvance }: AdminOrderDetailViewProps) {
  const t = useCopy();
  const { screen } = state;
  return (
    <section className="acs-admin__panel site-admin-order" aria-busy={state.busy}>
      <Link className="site-admin-order__back" to={ADMIN_ORDERS_PATH}>
        <BackIcon />
        <span>{t("common.back")}</span>
      </Link>
      {screen.status === "missing" && <AdminAlert error="common.error_retry" />}
      {screen.status === "failed" && state.error !== null && <AdminAlert error={state.error} />}
      {screen.status === "ready" && <ReadyDetail screen={screen} busy={state.busy} error={state.error} states={states} onAdvance={onAdvance} />}
    </section>
  );
}

// 详情的容器：挂载即读取一次（以打开时的界面语言）；换到另一张订单时由页面以新的 key 重新挂载。
export default function AdminOrderDetail({ orderId }: { orderId: string }) {
  const { language } = useLanguage();
  const { replace } = useRouter();
  const [state, setState] = useState<DetailState>(INITIAL_DETAIL);
  // 打开时的界面语言：之后切换语言不重新读取（每次查看都写审计），推进之后的重新读取用当时的语言。
  const openLanguage = useRef(language);
  // 离开页面时中止进行中的读取与推进，之后不再更新页面。
  const lifetime = useRef<AbortController | null>(null);

  const order = state.screen.status === "ready" ? state.screen.order : null;
  const states = useMyStates(order?.recipient ?? null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void openDetail(orderId, openLanguage.current, controller.signal, { show: setState, replace });
    return () => {
      controller.abort();
    };
  }, [orderId, replace]);

  return (
    <AdminOrderDetailView
      state={state}
      states={states}
      onAdvance={(target) => {
        const controller = lifetime.current;
        const { screen } = state;
        if (screen.status !== "ready" || state.busy || controller === null || !canAdvance(screen.order, target)) {
          return;
        }
        setState({ screen, busy: true, error: null });
        void advanceOrder(orderId, screen, target, language, controller.signal, { show: setState, replace });
      }}
    />
  );
}
