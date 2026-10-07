import { useEffect, useRef, useState } from "react";

import { advanceAdminOrder, readAdminOrder } from "../api/adminOrders";
import type { ActorType, AdminOrderDetail, AdminOrderEvent, AdminOrderLine, AdvanceTarget, DetailRead, OrderStatus } from "../api/adminOrders";
import type { PayRecipient } from "../api/pay";
import { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_ORDERS_PATH, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import { recipientParts, useMyStates } from "./PayPage";
import { formatDateTime, usePrice } from "./TrackOrderPage";

// 后台订单 A02 的订单详情与模拟发货（docs/UX.md 0.10 A02 与「状态与补充（0.9）」，视觉稿 A02-desktop、A02-desktop-frozen
// 与 A02-phone-detail）：路由 /admin/orders/:id，桌面在列表右侧、手机只显示详情（由 site.css 按宽度显隐）。
// 打开时以当前界面语言调用一次 GET /api/admin/orders/{内部 ID}（服务端每次查看都写审计）：推迟到下一轮事件循环再发，
// 开发模式下 StrictMode 让 effect 执行两次时也只发一次；之后切换界面语言不重新读取，接口文字保持打开时的语言。
// 标题 admin.order_detail · 订单号与状态标签；商品各行、金额明细、收货资料原文与审计提示、推进按钮、事件记录。
// 手机的金额明细、收货资料与事件记录为可折叠区块。admin.mark_packed 只在 demo_paid、admin.mark_shipped 只在 demo_packed 时可点；
// 全部已退时两者都禁用并显示 admin.frozen；admin.ship_hint 始终显示。按钮之下依 UX 线框为 admin.ship_hint、admin.frozen
// （视觉稿 A02-desktop-frozen 把冻结提示画在 ◆ 提示之前，与 UX 冲突处按 UX）。
// 订单不存在（404）或路径 ID 不合法时只显示 common.error_retry 与返回（Kelvin 2026-10-07）；401 换成登录页 A01。
// 订单号、收货资料与 CSRF 令牌只在 React 状态与请求里；离开页面或换订单时中止请求，旧请求的结果不再更新页面。

// 状态名（order.status_*）与标签样式（取自视觉稿 A02：已取消为描边标签，其余为中性标签）；列表与详情共用。
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

// 操作者类别：顾客（游客与会员）、管理员、系统。
export const ACTOR_LABEL: Readonly<Record<ActorType, CopyKey>> = {
  guest: "admin.actor_customer",
  member: "admin.actor_customer",
  admin: "admin.actor_admin",
  system: "admin.actor_system",
};

// 两个推进按钮：目标状态、按钮文字与可点时订单须处于的状态。
const MARKS: readonly { target: AdvanceTarget; label: CopyKey; from: OrderStatus }[] = [
  { target: "demo_packed", label: "admin.mark_packed", from: "demo_paid" },
  { target: "demo_shipped", label: "admin.mark_shipped", from: "demo_packed" },
];

// 按钮可点：订单处于该按钮的前一状态，且不是全部已退（冻结履约）。
export function canMark(order: AdminOrderDetail, target: AdvanceTarget): boolean {
  const mark = MARKS.find((item) => item.target === target);
  return mark !== undefined && order.status === mark.from && !order.fully_refunded;
}

// 详情区的状态：读取中、订单不存在（含路径 ID 不合法）、读取失败（提示）、已取到
// （busy 为推进进行中，notice 为推进后的提示）。
export type DetailState =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "failed"; error: CopyKey }
  | { status: "ready"; order: AdminOrderDetail; busy: boolean; notice: CopyKey | null };

export const INITIAL_DETAIL: DetailState = { status: "loading" };

// 一步的结果：显示某个状态，或回到登录页 A01（login）。
export type DetailStep = DetailState | "login";

function ready(order: AdminOrderDetail, notice: CopyKey | null): DetailState {
  return { status: "ready", order, busy: false, notice };
}

// 打开时按读取结果决定：取到显示详情；401 回到 A01；404 与不合法 ID 为不存在；网络中断 common.network_check，其他失败 common.error_retry。
export function detailStep(read: DetailRead): DetailStep {
  switch (read.kind) {
    case "ok":
      return ready(read.order, null);
    case "none":
      return "login";
    case "missing":
      return { status: "missing" };
    case "network":
      return { status: "failed", error: "common.network_check" };
    case "failed":
      return { status: "failed", error: "common.error_retry" };
  }
}

// 推进之后重新取详情：取到时显示新详情与 notice；401 回到 A01；404 为不存在；
// 读取本身失败时保留原来的详情并显示 common.network_check 或 common.error_retry。
async function reread(id: string, order: AdminOrderDetail, language: Language, signal: AbortSignal, notice: CopyKey | null): Promise<DetailStep> {
  const read = await readAdminOrder(id, language, signal);
  switch (read.kind) {
    case "ok":
      return ready(read.order, notice);
    case "none":
      return "login";
    case "missing":
      return { status: "missing" };
    case "network":
      return ready(order, "common.network_check");
    case "failed":
      return ready(order, "common.error_retry");
  }
}

// 推进一步：200 后重新取详情；409（order_not_advanceable 或 fulfilment_frozen）与 403 重新取详情并显示 common.error_retry；
// 401 回到 A01；网络中断显示 common.network_check（再点即重发，已是目标状态时服务端回答 200）；其他失败 common.error_retry。
// 后两种保留当前详情，不重新读取。
export async function advanceStep(id: string, order: AdminOrderDetail, target: AdvanceTarget, language: Language, signal: AbortSignal): Promise<DetailStep> {
  const reply = await advanceAdminOrder(id, target, order.csrf_token);
  switch (reply) {
    case "done":
      return reread(id, order, language, signal, null);
    case "conflict":
    case "csrf":
      return reread(id, order, language, signal, "common.error_retry");
    case "none":
      return "login";
    case "network":
      return ready(order, "common.network_check");
    case "failed":
      return ready(order, "common.error_retry");
  }
}

// 页面对一步结果的处理：显示详情区，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface DetailMoves {
  show: (state: DetailState) => void;
  replace: (path: RoutePath) => void;
}

// 离开页面或换订单（signal 已中止）之后不再更新。
export function settleDetail(step: DetailStep, signal: AbortSignal, moves: DetailMoves): void {
  if (signal.aborted) {
    return;
  }
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

export async function openDetail(id: string, language: Language, signal: AbortSignal, moves: DetailMoves): Promise<void> {
  const read = await readAdminOrder(id, language, signal);
  settleDetail(detailStep(read), signal, moves);
}

export async function advanceOrder(
  id: string,
  order: AdminOrderDetail,
  target: AdvanceTarget,
  language: Language,
  signal: AbortSignal,
  moves: DetailMoves,
): Promise<void> {
  settleDetail(await advanceStep(id, order, target, language, signal), signal, moves);
}

// 推迟到下一轮事件循环再执行 run，返回清理函数（取消尚未执行的 run 并中止 controller）。
// StrictMode 在开发模式下让 effect「执行—清理—再执行」时，第一次的 run 在清理时就被取消，所以只发一次请求。
export function startDeferred(controller: AbortController, run: () => void): () => void {
  const timer = setTimeout(() => {
    if (!controller.signal.aborted) {
      run();
    }
  }, 0);
  return () => {
    clearTimeout(timer);
    controller.abort();
  };
}

function BackIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}

function DemoMark() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 3l9 9-9 9-9-9z" />
    </svg>
  );
}

// 一行商品：名称 / 规格 × 件数，右侧为行小计。
function ItemRow({ line }: { line: AdminOrderLine }) {
  const price = usePrice();
  return (
    <div className="site-admin-order__row">
      <span className="site-admin-order__line-name">
        <span>{line.name}</span>
        {line.variant_label !== "" && (
          <>
            <span>/</span>
            <span>{line.variant_label}</span>
          </>
        )}
        <span>{`× ${String(line.quantity)}`}</span>
      </span>
      <span>{price(line.line_subtotal_sen)}</span>
    </div>
  );
}

// 金额明细：商品小计、券折扣、积分抵扣、示例运费与合计（接口给的整数仙快照，只格式化）。
function AmountRows({ order }: { order: AdminOrderDetail }) {
  const t = useCopy();
  const price = usePrice();
  const rows: readonly [CopyKey, number][] = [
    ["cart.subtotal", order.subtotal_sen],
    ["checkout.summary_coupon", order.coupon_discount_sen],
    ["checkout.summary_points", order.points_discount_sen],
    ["checkout.summary_shipping", order.shipping_fee_sen],
    ["checkout.summary_total", order.total_sen],
  ];
  return rows.map(([label, sen]) => (
    <div key={label} className={label === "checkout.summary_total" ? "site-admin-order__row site-admin-order__total" : "site-admin-order__row"}>
      <span>{t(label)}</span>
      <span>{price(sen)}</span>
    </div>
  ));
}

// 收货资料原文（取自视觉稿：姓名 · 电话，其下 地址, 邮编, 州属或地区, 国家），其下 admin.recipient_audited。
function RecipientLines({ recipient, states }: { recipient: PayRecipient; states: ReadonlyMap<string, string> | null }) {
  const t = useCopy();
  const { language } = useLanguage();
  const parts = recipientParts(recipient, language, states);
  const place = [parts.address, parts.postcode, parts.region, parts.country].filter((value): value is string => value !== null && value !== "");
  return (
    <>
      <span>
        <span>{parts.name}</span>
        {" · "}
        <span>{parts.phone}</span>
      </span>
      <span>{place.join(", ")}</span>
      <span className="acs-admin__muted">{t("admin.recipient_audited")}</span>
    </>
  );
}

function EventTable({ events }: { events: readonly AdminOrderEvent[] }) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <table className="acs-admin__table">
      <thead>
        <tr>
          <th>{t("admin.col_time")}</th>
          <th>{t("admin.col_event")}</th>
          <th>{t("admin.col_actor")}</th>
        </tr>
      </thead>
      <tbody>
        {events.map((event, index) => (
          <tr key={index}>
            <td>{formatDateTime(event.created_at, language)}</td>
            <td>{t(STATUS_LABEL[event.status])}</td>
            <td>{t(ACTOR_LABEL[event.actor_type])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// 手机的事件记录（A02-phone-detail）：每条为 状态 · 操作者，其下为时间。
function EventList({ events }: { events: readonly AdminOrderEvent[] }) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <ul className="site-admin-order__fold-body site-admin-order__event-list">
      {events.map((event, index) => (
        <li key={index}>
          <span>
            <span>{t(STATUS_LABEL[event.status])}</span>
            {" · "}
            <span>{t(ACTOR_LABEL[event.actor_type])}</span>
          </span>
          <span className="acs-admin__muted">{formatDateTime(event.created_at, language)}</span>
        </li>
      ))}
    </ul>
  );
}

interface ReadyProps {
  order: AdminOrderDetail;
  busy: boolean;
  notice: CopyKey | null;
  states: ReadonlyMap<string, string> | null;
  onMark: (target: AdvanceTarget) => void;
}

// 已取到的详情。桌面为一张面板，各区块依次排列；手机面板不绘制（site.css），金额明细、收货资料与事件记录改为可折叠区块。
// 桌面区块与手机折叠两套都渲染，由 site.css 按宽度只显示其一。
function OrderDetail({ order, busy, notice, states, onMark }: ReadyProps) {
  const t = useCopy();
  const price = usePrice();
  return (
    <div className="acs-admin__panel site-admin-order__panel">
      <div className="site-admin-order__head">
        <h2 className="acs-admin__h">
          <span>{t("admin.order_detail")}</span>
          <span aria-hidden="true"> · </span>
          <span>{order.order_number}</span>
        </h2>
        <span className={statusTagClass(order.status)}>{t(STATUS_LABEL[order.status])}</span>
      </div>
      <div className="acs-admin__panel site-admin-order__items">
        <span className="acs-admin__muted">{t("order.items")}</span>
        {order.lines.map((line, index) => (
          <ItemRow key={index} line={line} />
        ))}
      </div>
      <div className="site-admin-order__block site-admin-order__desktop">
        <span className="acs-admin__muted">{t("order.amount_breakdown")}</span>
        <AmountRows order={order} />
      </div>
      <details className="acs-admin__panel site-admin-order__fold">
        <summary>
          <span>{t("order.amount_breakdown")}</span>
          <span>{price(order.total_sen)}</span>
        </summary>
        <div className="site-admin-order__fold-body">
          <AmountRows order={order} />
        </div>
      </details>
      {order.recipient !== null && (
        <>
          <div className="site-admin-order__block site-admin-order__desktop">
            <span className="acs-admin__muted">{t("admin.recipient_raw")}</span>
            <RecipientLines recipient={order.recipient} states={states} />
          </div>
          <details className="acs-admin__panel site-admin-order__fold" open>
            <summary>{t("admin.recipient_raw")}</summary>
            <div className="site-admin-order__fold-body site-admin-order__recipient">
              <RecipientLines recipient={order.recipient} states={states} />
            </div>
          </details>
        </>
      )}
      <div className="site-admin-order__actions">
        <div className="site-admin-order__buttons">
          {MARKS.map((mark) => {
            const possible = canMark(order, mark.target);
            return (
              <button
                key={mark.target}
                className={possible ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary"}
                type="button"
                disabled={!possible || busy}
                onClick={() => {
                  onMark(mark.target);
                }}
              >
                {t(mark.label)}
              </button>
            );
          })}
        </div>
        {notice !== null && <AdminAlert error={notice} />}
        <div className="site-admin-order__hint">
          <DemoMark />
          <span>{t("admin.ship_hint")}</span>
        </div>
        {order.fully_refunded && (
          <div className="site-admin-order__frozen" role="status">
            {t("admin.frozen")}
          </div>
        )}
      </div>
      <div className="site-admin-order__block site-admin-order__desktop">
        <span className="acs-admin__muted">{t("admin.event_log")}</span>
        <div className="site-admin-order__events">
          <EventTable events={order.events} />
        </div>
      </div>
      <details className="acs-admin__panel site-admin-order__fold">
        <summary>{t("admin.event_log")}</summary>
        <EventList events={order.events} />
      </details>
    </div>
  );
}

export interface AdminOrderDetailViewProps {
  state: DetailState;
  // 马来西亚州属代码对应的名称（读取前或读取失败时为 null，显示代码）。
  states: ReadonlyMap<string, string> | null;
  onMark: (target: AdvanceTarget) => void;
}

// 详情区：顶部为返回 /admin/orders 的链接（只在手机显示）；读取中标 aria-busy；不存在或读取失败时只有提示，不显示详情。
export function AdminOrderDetailView({ state, states, onMark }: AdminOrderDetailViewProps) {
  const t = useCopy();
  return (
    <section className="site-admin-order" aria-busy={state.status === "loading"}>
      <Link className="site-admin-order__back" to={ADMIN_ORDERS_PATH}>
        <BackIcon />
        <span>{t("common.back")}</span>
      </Link>
      {state.status === "missing" && <AdminAlert error="common.error_retry" />}
      {state.status === "failed" && <AdminAlert error={state.error} />}
      {state.status === "ready" && <OrderDetail order={state.order} busy={state.busy} notice={state.notice} states={states} onMark={onMark} />}
    </section>
  );
}

// 一张订单的详情：路径里的 ID 换了（换订单）时由调用方以 key 重新挂载。
export default function AdminOrderDetailContent({ id }: { id: string }) {
  const { replace } = useRouter();
  const { language } = useLanguage();
  const [state, setState] = useState<DetailState>(INITIAL_DETAIL);
  // 打开时的界面语言：之后切换语言不重新读取（每次查看都写审计，只请求一次优先）。
  const languageRef = useRef(language);
  useEffect(() => {
    languageRef.current = language;
  });
  // 离开页面或换订单时中止读取与推进后的重新读取，之后不再更新页面。
  const lifetime = useRef<AbortController | null>(null);
  const states = useMyStates(state.status === "ready" ? state.order.recipient : null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openDetail(id, languageRef.current, controller.signal, { show: setState, replace });
    });
  }, [id, replace]);

  return (
    <AdminOrderDetailView
      state={state}
      states={states}
      onMark={(target) => {
        const controller = lifetime.current;
        if (state.status !== "ready" || state.busy || controller === null || !canMark(state.order, target)) {
          return;
        }
        setState({ ...state, busy: true, notice: null });
        void advanceOrder(id, state.order, target, language, controller.signal, { show: setState, replace });
      }}
    />
  );
}
