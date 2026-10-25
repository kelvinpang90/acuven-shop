import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";

import { estimateMemberRefundPoints, memberOrderInView, readMemberOrder, runMemberRefund } from "../api/memberOrders";
import type { MemberOrderRead, MemberPointsEstimateRead, MemberRefundOutcome } from "../api/memberOrders";
import { useMemberSessionReader } from "../api/memberSession";
import type { MemberSessionReader, MemberSessionState } from "../api/memberSession";
import {
  clampQuantities,
  estimateRefundPoints,
  handOffSubmittedRefund,
  readLookupOrder,
  refundEstimate,
  refundKey,
  refundLines,
  runRefund,
  showsRefundPoints,
} from "../api/orderLookup";
import type {
  LookupFound,
  LookupLine,
  LookupOrder,
  PointsEstimate,
  PointsEstimateRead,
  RefundLineRequest,
  RefundOutcome,
  RefundRefusal,
  RefundRetry,
} from "../api/orderLookup";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ACCOUNT_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import { InfoIcon } from "./PayPage";
import { LineName, SessionExpired, TRACK_ORDER_PATH, usePrice } from "./TrackOrderPage";
import type { OrderScreen } from "./TrackOrderPage";

// 退款申请页 P10 的查单模式（docs/UX.md P10）：打开时以当前语言调用 GET /api/orders/lookup，取第一张订单；
// 之后重新读取（切换语言、提交被拒或失败之后）都按同一订单号取该单。401 或本浏览器对该单的授权已结束时整页显示 order.session_expired。
// 显示 refund.title、order.title、★ refund.demo_hint、order.lookup_access；refund.select_items 与各行（名称与规格、order.cash_paid 与逐件实付、
// detail.quantity 加减控件（0 到该行可退件数）与 refund.max_qty）；refund.estimate、refund.shipping_not_refunded、refund.coupon_not_restored、
// refund.submit 与 ◆ refund.submit_hint。手机上合计、◆ 提示与提交按钮固定在底部。
// 预计金额只把接口给的各行预计金额列表中对应件数的那一项相加，不在浏览器做乘除或分摊。
// 订单号与 CSRF 令牌只在页面内存（React 状态）里，不进网址或任何浏览器存储。
//
// 积分三行（两种模式；DESIGN 1.13「计价、优惠、积分与库存」第 5 条，HANDOFF 0.61 第（9）条）：下单时是会员的订单（placed_by_member）在所选件数变化
// 且至少一行大于 0 时请求积分预估（查单模式用游客预估，会员模式用会员预估），发出新请求时中止上一个，离开页面时中止；游客订单与后来被认领的游客订单不请求。
// 只有与当前所选件数对应的预估为 ok 且 showsRefundPoints 为真时显示 refund.points_back、refund.points_reversed 与 refund.expired_points_note：
// 桌面在 refund.estimate 之下、两句说明之前；手机 refund.expired_points_note 在各行之后的说明里，两个数在底部固定栏的合计之下。
// 预估的任何失败都不显示积分行、不另加提示，不影响提交，也不切换整页状态。
//
// 会员模式（/account/order/refund，本文件的 MemberRefundPage；DESIGN 1.13「权限与资料保护」第 3、5 条，HANDOFF 0.61）：打开时经 api/memberSession.ts 的
// 读取对象重读一次会话（读完之前为 aria-busy 的空主体；未登录以 replace 进入 /login；失败只显示 common.error_retry）；已登录时取 P13 经 api/memberOrders.ts
// 交接的订单号，没有（如整页刷新后）以 replace 回到 /account（Kelvin 2026-10-24 决定）。订单经会员接口读取，切换语言时重新读取同一订单；
// 会员接口回 401 时重读会话并以 replace 进入 /login，回 404 时以 replace 回到 /account。会员模式不显示 order.lookup_access，没有 order.session_expired 整页。
// 提交经会员接口、带会话里的 CSRF 令牌，幂等键规则同查单模式；成功后照查单模式交出订单号并前往 /account/order（P09 会员模式显示 refund.submitted）；
// 令牌失效时重读会话换用新令牌。离开页面时中止请求，之后不再更新；会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转。

function MinusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <path d="M5 12h14" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

// ◆ 退款提示（UX「演示提示汇总」）：与 ★ 同一提示样式，标记为菱形。
function ActionHint({ children }: { children: ReactNode }) {
  return (
    <p className="acs-hint">
      <svg className="acs-hint__mark" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3l9 9-9 9-9-9z" />
      </svg>
      <span>{children}</span>
    </p>
  );
}

// 被拒的申请（409）各自的提示。
const REFUSAL_COPY: Readonly<Record<RefundRefusal, CopyKey>> = {
  duplicate: "refund.duplicate",
  nothing_left: "refund.nothing_left",
  window_closed: "refund.window_closed",
};

// 正在进行的操作：提交申请、网络中断后确认订单；进行中按钮与加减控件禁用。
export type RefundBusy = "submitting" | "checking" | null;

// 提交之后的提示：三种被拒各自的文案，或 common.error_retry。
export type RefundNotice = RefundRefusal | "error" | null;

// 页面的模式：查单模式（/track/order/refund）或会员模式（/account/order/refund）。
export type RefundMode = "lookup" | "member";

export interface RefundViewProps {
  screen: OrderScreen;
  // 各行选定的件数，与订单行同序。
  quantities: readonly number[];
  busy: RefundBusy;
  notice: RefundNotice;
  onQuantity: (index: number, quantity: number) => void;
  onSubmit: () => void;
  // 不给时为查单模式。
  mode?: RefundMode | undefined;
  // 与当前所选件数对应的积分预估；没有请求、尚未返回或失败时为 null（或不给）。
  points?: PointsEstimate | null | undefined;
}

interface RefundRowProps {
  line: LookupLine;
  quantity: number;
  locked: boolean;
  onQuantity: (quantity: number) => void;
}

// 一行：名称 / 规格，order.cash_paid 与逐件实付（按件序，原样来自接口）；detail.quantity 与加减控件（0 到该行可退件数）、refund.max_qty。
// 可退件数为 0 的行两个按钮都禁用，不可选。
function RefundRow({ line, quantity, locked, onQuantity }: RefundRowProps) {
  const t = useCopy();
  const price = usePrice();
  const labelId = useId();
  return (
    <li className="site-refund__line">
      <span className="site-refund__info">
        <LineName name={line.name} variant={line.variant_label} />
        {line.unit_cash_paid_sen.length > 0 && (
          <span className="acs-body-s acs-muted site-refund__paid">
            <span>{t("order.cash_paid")}</span>
            {line.unit_cash_paid_sen.map((sen, index) => (
              <span key={index} className="acs-num">
                {price(sen)}
              </span>
            ))}
          </span>
        )}
      </span>
      <div className="site-refund__qty">
        <span id={labelId} className="acs-body-s">
          {t("detail.quantity")}
        </span>
        <div className="acs-stepper" role="group" aria-labelledby={labelId}>
          <button
            type="button"
            aria-label={t("common.a11y_qty_decrease")}
            disabled={locked || quantity <= 0}
            onClick={() => {
              onQuantity(quantity - 1);
            }}
          >
            <MinusIcon />
          </button>
          <output aria-live="polite">{quantity}</output>
          <button
            type="button"
            aria-label={t("common.a11y_qty_increase")}
            disabled={locked || quantity >= line.refundable_quantity}
            onClick={() => {
              onQuantity(quantity + 1);
            }}
          >
            <PlusIcon />
          </button>
        </div>
      </div>
      <span className="acs-caption site-refund__max">{t("refund.max_qty", { count: line.refundable_quantity })}</span>
    </li>
  );
}

// 提交之后的提示：网络中断后确认订单期间为 common.network_check；之后为被拒的文案或 common.error_retry。
function RefundNotices({ busy, notice }: { busy: RefundBusy; notice: RefundNotice }) {
  const t = useCopy();
  if (busy === "checking") {
    return (
      <div className="acs-alert acs-alert--info" role="status">
        <InfoIcon />
        <span>{t("common.network_check")}</span>
      </div>
    );
  }
  if (busy !== null || notice === null) {
    return null;
  }
  if (notice === "error") {
    return <ErrorNotice />;
  }
  return (
    <p className="acs-alert acs-alert--danger site-notice" role="alert">
      {t(REFUSAL_COPY[notice])}
    </p>
  );
}

// 会员模式不显示 order.lookup_access（凭会员会话，没有查单授权），其余两种模式相同。积分三行只在下单时是会员的订单、
// 给了预估且 showsRefundPoints 为真时显示。
export function RefundView({ screen, quantities, busy, notice, onQuantity, onSubmit, mode = "lookup", points = null }: RefundViewProps) {
  const t = useCopy();

  if (screen.status === "loading") {
    return <main className="site-refund" aria-busy="true"></main>;
  }
  if (screen.status === "expired") {
    return <SessionExpired />;
  }
  if (screen.status === "error") {
    return (
      <main className="site-refund">
        <ErrorNotice />
      </main>
    );
  }

  const { order } = screen;
  const estimate = refundEstimate(order.lines, quantities);
  const chosen = refundLines(order.lines, quantities).length > 0;
  const disabled = busy !== null || !chosen || estimate === null;
  const estimateText = estimate === null ? null : t("refund.estimate", { amount: formatSen(estimate) });
  const pointsRows = order.placed_by_member && points !== null && showsRefundPoints(points) ? points : null;
  const submit = (className: string) => (
    <button className={className} type="button" disabled={disabled} onClick={onSubmit}>
      {t("refund.submit")}
    </button>
  );

  return (
    <main className="site-refund" aria-busy={busy !== null}>
      <div className="site-refund__head">
        <div className="site-refund__title">
          <h1 className="acs-display-l">{t("refund.title")}</h1>
          <span className="acs-num acs-muted">{t("order.title", { orderNo: order.order_number })}</span>
        </div>
        <DemoHint>{t("refund.demo_hint")}</DemoHint>
      </div>
      {mode === "lookup" && <p className="acs-body-s acs-muted">{t("order.lookup_access")}</p>}
      <section className="site-refund__items">
        <h2 className="acs-display-s">{t("refund.select_items")}</h2>
        <ul className="site-refund__lines">
          {order.lines.map((line, index) => (
            <RefundRow
              key={line.line_index}
              line={line}
              quantity={quantities[index] ?? 0}
              locked={busy !== null}
              onQuantity={(quantity) => {
                onQuantity(index, quantity);
              }}
            />
          ))}
        </ul>
      </section>
      <div className="site-phone-only site-refund__notes">
        <p className="acs-body-s acs-muted">{t("refund.shipping_not_refunded")}</p>
        <p className="acs-body-s acs-muted">{t("refund.coupon_not_restored")}</p>
        {pointsRows !== null && <p className="acs-body-s acs-muted">{t("refund.expired_points_note")}</p>}
      </div>
      <section className="acs-summary site-desktop-only site-refund__summary">
        {estimateText !== null && <span className="acs-price acs-price--l">{estimateText}</span>}
        {pointsRows !== null && (
          <>
            <div className="acs-num site-refund__points">
              <span>{t("refund.points_back", { points: pointsRows.pointsBack })}</span>
              <span>{t("refund.points_reversed", { points: pointsRows.pointsReversed })}</span>
            </div>
            <p className="acs-body-s acs-muted">{t("refund.expired_points_note")}</p>
          </>
        )}
        <p className="acs-body-s acs-muted">
          <span>{t("refund.shipping_not_refunded")}</span> <span>{t("refund.coupon_not_restored")}</span>
        </p>
      </section>
      <div className="site-desktop-only site-refund__submit">
        {submit("acs-btn acs-btn--primary acs-btn--lg")}
        <ActionHint>{t("refund.submit_hint")}</ActionHint>
      </div>
      <RefundNotices busy={busy} notice={notice} />
      <div className="acs site-phone-only site-refund__bar">
        {estimateText !== null && <span className="acs-price">{estimateText}</span>}
        {pointsRows !== null && (
          <div className="acs-num acs-body-s site-refund__bar-points">
            <span>{t("refund.points_back", { points: pointsRows.pointsBack })}</span>
            <span>{t("refund.points_reversed", { points: pointsRows.pointsReversed })}</span>
          </div>
        )}
        <ActionHint>{t("refund.submit_hint")}</ActionHint>
        {submit("acs-btn acs-btn--primary acs-btn--block")}
      </div>
    </main>
  );
}

// 以下为两种模式共用的积分预估。
//
// 积分预估的请求：订单号与申请行；key 由两者得出，用来判断某次预估是否对应当前所选件数。
export interface PointsRequest {
  orderNumber: string;
  lines: RefundLineRequest[];
  key: string;
}

// 按当前订单与所选件数决定是否请求预估：只有下单时是会员的订单（placed_by_member）且至少一行大于 0 时才请求；
// 游客订单与后来被认领的游客订单（placed_by_member 为假）、没有选择或订单还没取到时为 null。
export function pointsRequest(order: LookupOrder | null, quantities: readonly number[]): PointsRequest | null {
  if (order === null || !order.placed_by_member) {
    return null;
  }
  const lines = refundLines(order.lines, quantities);
  if (lines.length === 0) {
    return null;
  }
  return { orderNumber: order.order_number, lines, key: JSON.stringify([order.order_number, lines]) };
}

// 查单模式的游客预估（estimateRefundPoints）与会员模式的会员预估（estimateMemberRefundPoints）。
export type PointsEstimator = (orderNumber: string, lines: readonly RefundLineRequest[], signal?: AbortSignal) => Promise<PointsEstimateRead | MemberPointsEstimateRead>;

// 一次预估的结果：对应的请求 key，ok 时为预估，其余（任何失败，含 401、404、409 与网络中断）为 null。
export interface PointsResult {
  key: string;
  estimate: PointsEstimate | null;
}

export function pointsStep(key: string, read: PointsEstimateRead | MemberPointsEstimateRead): PointsResult {
  if (read.kind !== "ok") {
    return { key, estimate: null };
  }
  return { key, estimate: { memberOrder: read.memberOrder, pointsBack: read.pointsBack, pointsReversed: read.pointsReversed } };
}

// 交给 View 的预估：只有结果对应当前的请求时才给（件数改变后、新的预估返回前不给旧数）；是否显示三行再由 View 按 showsRefundPoints 判断。
export function shownPoints(result: PointsResult | null, request: PointsRequest | null): PointsEstimate | null {
  if (result === null || request === null || result.key !== request.key) {
    return null;
  }
  return result.estimate;
}

// 请求一次预估并交出结果；signal 中止之后不再交出。
export async function estimatePoints(estimator: PointsEstimator, request: PointsRequest, signal: AbortSignal, show: (result: PointsResult) => void): Promise<void> {
  const read = await estimator(request.orderNumber, request.lines, signal);
  if (signal.aborted) {
    return;
  }
  show(pointsStep(request.key, read));
}

// 发出一次预估，返回中止它的函数：所选件数变化（发出新请求）与离开页面时调用。
export function startPointsEstimate(estimator: PointsEstimator, request: PointsRequest, show: (result: PointsResult) => void): () => void {
  const controller = new AbortController();
  void estimatePoints(estimator, request, controller.signal, show);
  return () => {
    controller.abort();
  };
}

// 页面里的积分预估：订单或所选件数变化时按 pointsRequest 重新决定，先中止上一个请求；返回交给 View 的预估。
function useRefundPoints(estimator: PointsEstimator, order: LookupOrder | null, quantities: readonly number[]): PointsEstimate | null {
  const [result, setResult] = useState<PointsResult | null>(null);
  const request = useMemo(() => pointsRequest(order, quantities), [order, quantities]);

  useEffect(() => (request === null ? undefined : startPointsEstimate(estimator, request, setResult)), [estimator, request]);

  return shownPoints(result, request);
}

export default function RefundPage() {
  const { language } = useLanguage();
  const { navigate } = useRouter();
  const [screen, setScreen] = useState<OrderScreen>({ status: "loading" });
  // CSRF 令牌只在内存里：每次读取订单时由接口给出。
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [quantities, setQuantities] = useState<number[]>([]);
  const [busy, setBusy] = useState<RefundBusy>(null);
  const [notice, setNotice] = useState<RefundNotice>(null);
  // 网络中断后确认没有写入的那次申请：以同一请求内容再提交时沿用它的幂等键。
  const [retry, setRetry] = useState<RefundRetry | null>(null);
  // 已显示的订单号：之后的重新读取取同一张订单。只在副作用与回调里读写。
  const shownOrder = useRef<string | null>(null);
  // 离开页面时中止进行中的申请与确认。
  const lifetime = useRef<AbortController | null>(null);

  const order = screen.status === "ready" ? screen.order : null;
  const points = useRefundPoints(estimateRefundPoints, order, quantities);

  // 读取（或重新读取）到订单：已选的件数保留，但不超过各行现在的可退件数。
  const show = useCallback((read: LookupFound) => {
    shownOrder.current = read.order.order_number;
    setScreen({ status: "ready", order: read.order });
    setCsrfToken(read.csrfToken);
    setQuantities((previous) => clampQuantities(read.order.lines, previous));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
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
        setNotice("error");
      }
    });
    return () => {
      controller.abort();
    };
  }, [language, show]);

  return (
    <RefundView
      screen={screen}
      quantities={quantities}
      busy={busy}
      notice={notice}
      points={points}
      onQuantity={(index, quantity) => {
        if (order === null || busy !== null) {
          return;
        }
        setQuantities((previous) => clampQuantities(order.lines, order.lines.map((_line, at) => (at === index ? quantity : (previous[at] ?? 0)))));
      }}
      onSubmit={() => {
        if (order === null || csrfToken === null || busy !== null) {
          return;
        }
        const lines = refundLines(order.lines, quantities);
        if (lines.length === 0) {
          return;
        }
        const orderNumber = order.order_number;
        const key = refundKey(retry, lines);
        setBusy("submitting");
        setNotice(null);
        void runRefund(language, order, csrfToken, key, lines, {
          signal: lifetime.current?.signal,
          onChecking: () => {
            setBusy("checking");
          },
        }).then((outcome: RefundOutcome | null) => {
          if (outcome === null) {
            return;
          }
          switch (outcome.kind) {
            case "submitted":
              // 回到 P09 并显示 refund.submitted：订单号只经本页内存交给 P09。
              handOffSubmittedRefund(orderNumber);
              navigate(TRACK_ORDER_PATH);
              return;
            case "leave":
              navigate(TRACK_ORDER_PATH);
              return;
            case "expired":
              setScreen({ status: "expired" });
              break;
            case "refused":
              if (outcome.read) {
                show(outcome.read);
              }
              setRetry(null);
              setNotice(outcome.reason);
              break;
            case "error":
              if (outcome.read) {
                show(outcome.read);
              }
              setRetry(null);
              setNotice("error");
              break;
            case "retry":
              show(outcome.read);
              setRetry(outcome.retry);
              setNotice("error");
              break;
          }
          setBusy(null);
        });
      }}
    />
  );
}

// 以下为会员模式（/account/order/refund）。

const LOGIN_PATH: RoutePath = "/login";
const MEMBER_ORDER_PATH: RoutePath = "/account/order";

// 会员模式的页面状态：读取会话中（空主体，标 aria-busy）、读取会话失败（只有 common.error_retry）、已登录。
// 已登录时 orderNumber 为打开时取到的交接订单号，csrfToken 来自打开时那次读取（csrf 之后的重读更新令牌）；order 为 null 时订单还没取到
// （此时 notice 为 error 即首次读取失败，只显示 common.error_retry）；quantities、busy、notice 与 retry 同查单模式。
export type MemberRefundState =
  | { status: "loading" }
  | { status: "failed" }
  | {
      status: "member";
      orderNumber: string;
      csrfToken: string;
      order: LookupOrder | null;
      quantities: number[];
      busy: RefundBusy;
      notice: RefundNotice;
      retry: RefundRetry | null;
    };

type SignedIn = Extract<MemberRefundState, { status: "member" }>;

export const MEMBER_REFUND_LOADING: MemberRefundState = { status: "loading" };

// 按打开时重读到的会话决定：member 时才取交接的订单号（take），没有时回 /account（account）；guest 去 /login（login）；
// failed 只显示 common.error_retry。重读的回答处理之后状态不会是 unknown，万一是也按失败处理。
export function openRefundStep(state: MemberSessionState, take: () => string | null): MemberRefundState | "login" | "account" {
  switch (state.kind) {
    case "member": {
      const orderNumber = take();
      if (orderNumber === null) {
        return "account";
      }
      return { status: "member", orderNumber, csrfToken: state.csrfToken, order: null, quantities: [], busy: null, notice: null, retry: null };
    }
    case "guest":
      return "login";
    case "failed":
    case "unknown":
      return { status: "failed" };
  }
}

// 对当前状态的一次改动（请求的结果按到达时的状态更新）；只改已登录的状态。
export type MemberRefundChange = (current: MemberRefundState) => MemberRefundState;

function signedIn(change: (current: SignedIn) => SignedIn): MemberRefundChange {
  return (current) => (current.status === "member" ? change(current) : current);
}

// 读取（或重新读取）到订单：照查单模式保留已选的件数，但不超过各行现在的可退件数；没有订单时沿用原来的。
function withOrder(current: SignedIn, order: LookupOrder | null): SignedIn {
  return order === null ? current : { ...current, order, quantities: clampQuantities(order.lines, current.quantities) };
}

// 按读取订单的结果决定：ok 显示该单；signed_out 重读会话后去 /login（login）；not_found 回 /account（account）；
// failed 与 network 照查单模式显示 common.error_retry（已显示的订单保留）。
export function readRefundStep(read: MemberOrderRead): MemberRefundChange | "login" | "account" {
  switch (read.kind) {
    case "ok":
      return signedIn((current) => withOrder(current, read.order));
    case "signed_out":
      return "login";
    case "not_found":
      return "account";
    case "failed":
    case "network":
      return signedIn((current) => ({ ...current, notice: "error" }));
  }
}

// 改一行的件数（照查单模式限在 0 到该行可退件数）；订单还没取到或正在提交时不改。
export function chooseQuantity(index: number, quantity: number): MemberRefundChange {
  return signedIn((current) => {
    const order = current.order;
    if (order === null || current.busy !== null) {
      return current;
    }
    return { ...current, quantities: clampQuantities(order.lines, order.lines.map((_line, at) => (at === index ? quantity : (current.quantities[at] ?? 0)))) };
  });
}

// 点提交：按钮与加减控件禁用，先前的提示收起。
export const startRefund: MemberRefundChange = signedIn((current) => ({ ...current, busy: "submitting", notice: null }));

// 网络中断、开始确认订单：显示 common.network_check。
export const startRefundChecking: MemberRefundChange = signedIn((current) => ({ ...current, busy: "checking" }));

// 按提交的去向决定（csrf 由 refundCsrfStep 处理）：submitted 交出订单号并前往 /account/order；leave 前往 /account/order；
// signed_out 重读会话后去 /login（login）；not_found 回 /account（account）；refused、error 与 retry 同查单模式（按重新读取到的订单显示，没有时沿用原来的）。
export function submitStep(outcome: Exclude<MemberRefundOutcome, { kind: "csrf" }>): MemberRefundChange | "submitted" | "leave" | "login" | "account" {
  switch (outcome.kind) {
    case "submitted":
    case "leave":
      return outcome.kind;
    case "signed_out":
      return "login";
    case "not_found":
      return "account";
    case "refused":
      return signedIn((current) => ({ ...withOrder(current, outcome.order), busy: null, notice: outcome.reason, retry: null }));
    case "error":
      return signedIn((current) => ({ ...withOrder(current, outcome.order), busy: null, notice: "error", retry: null }));
    case "retry":
      return signedIn((current) => ({ ...withOrder(current, outcome.order), busy: null, notice: "error", retry: outcome.retry }));
  }
}

// 令牌失效（403 csrf_failed）之后按重读到的会话决定：guest 去 /login（login）；member 换用新令牌，failed（含网络中断）保留原令牌，
// 两者都显示 common.error_retry，并按重新读取到的订单显示（没有时沿用原来的）。
export function refundCsrfStep(order: LookupOrder | null, state: MemberSessionState): MemberRefundChange | "login" {
  if (state.kind === "guest") {
    return "login";
  }
  return signedIn((current) => ({
    ...withOrder(current, order),
    csrfToken: state.kind === "member" ? state.csrfToken : current.csrfToken,
    busy: null,
    notice: "error",
    retry: null,
  }));
}

// 交给 RefundView 的画面：会话或订单读取中为 loading；会话读取失败或订单首次读取失败为 error；取到订单后为 ready。没有 expired。
export function memberRefundScreen(state: MemberRefundState): OrderScreen {
  if (state.status === "loading") {
    return { status: "loading" };
  }
  if (state.status === "failed") {
    return { status: "error" };
  }
  if (state.order === null) {
    return state.notice === "error" ? { status: "error" } : { status: "loading" };
  }
  return { status: "ready", order: state.order };
}

// 页面对结果的处理：换成某个状态、按当前状态改动、用路由的 replace 前往 /login 或 /account，或前往 /account/order。
export interface MemberRefundMoves {
  show: (state: MemberRefundState) => void;
  update: (change: MemberRefundChange) => void;
  replace: (path: RoutePath) => void;
  navigate: (path: RoutePath) => void;
}

// 重读会话（共享读取，不中止）之后以 replace 前往 path；signal 已中止（离开页面）时不跳转。
async function leaveAfterReload(reader: MemberSessionReader, signal: AbortSignal, path: RoutePath, replace: (path: RoutePath) => void): Promise<void> {
  await reader.reload();
  if (!signal.aborted) {
    replace(path);
  }
}

// 打开页面：重读一次会话，按结果取交接的订单号并显示（订单由页面另行读取）、进入 /login 或回到 /account。signal 中止之后不再更新或跳转。
export async function openMemberRefund(reader: MemberSessionReader, signal: AbortSignal, moves: MemberRefundMoves): Promise<void> {
  const state = await reader.reload();
  if (signal.aborted) {
    return;
  }
  const step = openRefundStep(state, memberOrderInView);
  if (step === "login") {
    moves.replace(LOGIN_PATH);
  } else if (step === "account") {
    moves.replace(ACCOUNT_PATH);
  } else {
    moves.show(step);
  }
}

// 以当前语言读取该单（打开后与切换语言时）；signal 中止之后不再更新或跳转。
export async function loadMemberRefundOrder(
  reader: MemberSessionReader,
  language: Language,
  orderNumber: string,
  signal: AbortSignal,
  moves: MemberRefundMoves,
): Promise<void> {
  const read = await readMemberOrder(language, orderNumber, signal);
  if (signal.aborted) {
    return;
  }
  const step = readRefundStep(read);
  if (step === "login") {
    await leaveAfterReload(reader, signal, LOGIN_PATH, moves.replace);
  } else if (step === "account") {
    moves.replace(ACCOUNT_PATH);
  } else {
    moves.update(step);
  }
}

// 一次退款申请：提交时显示的订单（取订单号与已有的申请笔数）、会话里的 CSRF 令牌、幂等键（refundKey）与申请行（refundLines）。
export interface MemberRefundTarget {
  order: LookupOrder;
  csrfToken: string;
  key: string;
  lines: RefundLineRequest[];
}

// 提交退款申请并按去向更新或跳转；submitted 时以 handOffSubmittedRefund 交出订单号再前往 /account/order；csrf 时重读会话再决定。
// signal 中止之后不再更新或跳转。
export async function sendMemberRefund(
  reader: MemberSessionReader,
  language: Language,
  target: MemberRefundTarget,
  signal: AbortSignal,
  moves: MemberRefundMoves,
): Promise<void> {
  const outcome = await runMemberRefund(language, target.order, target.csrfToken, target.key, target.lines, {
    signal,
    onChecking: () => {
      moves.update(startRefundChecking);
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
    const step = refundCsrfStep(outcome.order, state);
    if (step === "login") {
      moves.replace(LOGIN_PATH);
    } else {
      moves.update(step);
    }
    return;
  }
  const step = submitStep(outcome);
  if (step === "submitted") {
    // 回到 P09 会员模式并显示 refund.submitted：订单号只经本页内存交给 P09（照查单模式）。
    handOffSubmittedRefund(target.order.order_number);
    moves.navigate(MEMBER_ORDER_PATH);
  } else if (step === "leave") {
    moves.navigate(MEMBER_ORDER_PATH);
  } else if (step === "login") {
    await leaveAfterReload(reader, signal, LOGIN_PATH, moves.replace);
  } else if (step === "account") {
    moves.replace(ACCOUNT_PATH);
  } else {
    moves.update(step);
  }
}

function refundMoves(
  setState: Dispatch<SetStateAction<MemberRefundState>>,
  navigate: (path: RoutePath) => void,
  replace: (path: RoutePath) => void,
): MemberRefundMoves {
  return {
    show: (state) => {
      setState(state);
    },
    update: (change) => {
      setState(change);
    },
    replace,
    navigate,
  };
}

const NO_QUANTITIES: readonly number[] = [];

export function MemberRefundPage() {
  const { language } = useLanguage();
  const { navigate, replace } = useRouter();
  const reader = useMemberSessionReader();
  const [state, setState] = useState<MemberRefundState>(MEMBER_REFUND_LOADING);
  // 提交用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  const member = state.status === "member" ? state : null;
  const orderNumber = member === null ? null : member.orderNumber;
  const order = member === null ? null : member.order;
  const quantities = member === null ? NO_QUANTITIES : member.quantities;
  const points = useRefundPoints(estimateMemberRefundPoints, order, quantities);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openMemberRefund(reader, controller.signal, refundMoves(setState, navigate, replace));
    });
  }, [reader, navigate, replace]);

  // 取到订单号之后读取该单；切换语言时重新读取同一订单，并中止上一次读取。
  useEffect(() => {
    if (orderNumber === null) {
      return;
    }
    const controller = new AbortController();
    void loadMemberRefundOrder(reader, language, orderNumber, controller.signal, refundMoves(setState, navigate, replace));
    return () => {
      controller.abort();
    };
  }, [reader, navigate, replace, language, orderNumber]);

  return (
    <RefundView
      screen={memberRefundScreen(state)}
      quantities={quantities}
      busy={member === null ? null : member.busy}
      notice={member === null ? null : member.notice}
      mode="member"
      points={points}
      onQuantity={(index, quantity) => {
        setState(chooseQuantity(index, quantity));
      }}
      onSubmit={() => {
        const controller = lifetime.current;
        if (member === null || member.order === null || controller === null || member.busy !== null) {
          return;
        }
        const lines = refundLines(member.order.lines, member.quantities);
        if (lines.length === 0) {
          return;
        }
        const target = { order: member.order, csrfToken: member.csrfToken, key: refundKey(member.retry, lines), lines };
        setState(startRefund);
        void sendMemberRefund(reader, language, target, controller.signal, refundMoves(setState, navigate, replace));
      }}
    />
  );
}
