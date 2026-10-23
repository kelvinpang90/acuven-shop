import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { AWAITING_PAYMENT, confirmStripe, readPayOrder, resultKind, takePaidNotCancelled, wait } from "../api/pay";
import type { PayOrder, PayRead, StripeConfirm } from "../api/pay";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import type { Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { Link, PRODUCTS_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { ExpiresLine, InfoIcon, OrderCard, PAY_PATH, RecipientDetails, SessionExpired, useCountdown, useMyStates } from "./PayPage";

// 模拟支付结果页 P07（docs/UX.md P07，游客）：打开时以当前语言重新调用 GET /api/pay/orders，取第一张订单，按它的状态显示：
// 已模拟支付 → 成功（订单号与复制、应付金额、收货资料、result.guest_next、★ result.demo_hint、result.continue）；
// 最近一次支付失败且仍待支付 → 失败（pay.expires 倒计时与回 P06 的 result.retry），倒计时到 0 时重新读取；
// 已取消 → 按取消方 result.cancelled（超时）或 result.cancelled_by_you，以及 result.continue。
// 仍待支付而最近一次不是失败时换成支付页 P06。401 时整页 pay.session_expired。
// 成功页的 result.guest_next 之后是去注册页 /register 的 result.guest_register（UX P07 线框，视觉稿 P07-phone-guest）；
// result.track 按路由规则在会员订单详情实现前不渲染；游客不显示获得积分。
// Stripe 途径（docs/UX.md 0.11「P07 Stripe 测试」，DESIGN 1.12 第 4、5、6 条，HANDOFF 0.54、0.55，视觉稿 P07-stripe-confirming、
// P07-stripe-phone-slow 与 P07-stripe-states）：仍待支付且有 Stripe 记录时调用确认接口一次（带读取给的 CSRF 令牌），确认返回前按读取中显示；
// paid、refunded 重新读取订单并按读到的状态显示；not_paid 显示 result.stripe_not_paid 与回 P06 的 result.stripe_back_to_pay；
// 其余（confirming、failed、csrf、network）为确认中：每 5 秒自动重查、最多 12 次，用完后显示 result.confirming_slow 与 result.check_again。
// 每次重查都先重新读取订单（取得新令牌）再确认。仍待支付而没有 Stripe 记录、支付途径为 stripe 时一律换成 P06（Stripe 途径没有失败页）。
// 已取消且有来源为会话的 Stripe 退款尝试时以 result.stripe_late_refund 代替两种取消文案；从 P06 取消时会话已付款进入的，
// 成功区顶部另显示 result.paid_not_cancelled（只在内存里，读一次即失效）。

// 确认中每次自动重查之前的等待与自动重查的次数（UX 0.11「每 5 秒自动重查一次、最多 12 次」）。
export const AUTO_RECHECK_MS = 5000;
export const AUTO_RECHECKS = 12;

function CheckIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
      <path d="M7 7l10 10M17 7L7 17" />
    </svg>
  );
}

function PendingIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M12 3a9 9 0 1 1-9 9" />
    </svg>
  );
}

function WaitIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

function DoneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.5 2.5L16 9.5" />
    </svg>
  );
}

// 页面的状态：读取中（含确认返回之前）、凭据过期、读取失败、按订单显示（成功、失败、已取消）、
// Stripe 确认中（slow 为自动重查已用完，checking 为手动再查进行中）与 Stripe 尚未付款。
export type ResultScreen =
  | { status: "loading" }
  | { status: "expired" }
  | { status: "error" }
  | { status: "ready"; order: PayOrder }
  | { status: "confirming"; order: PayOrder; slow: boolean; checking: boolean }
  | { status: "not_paid" };

export const LOADING: ResultScreen = { status: "loading" };

export function confirming(order: PayOrder, slow: boolean): ResultScreen {
  return { status: "confirming", order, slow, checking: false };
}

// 读取结果的去向：show 换成某个状态；pay 用路由的 replace 换成 P06；confirm 调用确认接口；failed 为读取失败或网络中断。
export type ReadStep =
  | { kind: "show"; screen: ResultScreen }
  | { kind: "pay" }
  | { kind: "confirm"; order: PayOrder; csrfToken: string }
  | { kind: "failed" };

// 仍待支付且 stripe_state 不为 null 时确认；为 null 时支付途径为 stripe 一律回 P06，simulated 照 resultKind（失败页或回 P06）；
// 其他状态按订单显示；401 整页 pay.session_expired。
export function readStep(read: PayRead): ReadStep {
  if (read.kind === "expired") {
    return { kind: "show", screen: { status: "expired" } };
  }
  if (read.kind !== "ok") {
    return { kind: "failed" };
  }
  const { order } = read;
  if (order.status === AWAITING_PAYMENT) {
    if (order.stripe_state !== null) {
      return { kind: "confirm", order, csrfToken: read.csrfToken };
    }
    if (order.payment_route === "stripe" || resultKind(order) === "pay") {
      return { kind: "pay" };
    }
  }
  return { kind: "show", screen: { status: "ready", order } };
}

// 确认结果的去向：paid、refunded 重新读取订单；not_paid 尚未付款；expired 整页 pay.session_expired；
// confirming、failed、csrf 与 network 都为确认中。
export type ConfirmStep = { kind: "show"; screen: ResultScreen } | { kind: "reread" } | { kind: "confirming" };

export function confirmStep(confirm: StripeConfirm): ConfirmStep {
  switch (confirm.kind) {
    case "paid":
    case "refunded":
      return { kind: "reread" };
    case "not_paid":
      return { kind: "show", screen: { status: "not_paid" } };
    case "expired":
      return { kind: "show", screen: { status: "expired" } };
    case "confirming":
    case "failed":
    case "csrf":
    case "network":
      return { kind: "confirming" };
  }
}

// 打开或切换语言时读取失败：已按订单显示或尚未付款的保留；确认中的保留并出现 result.check_again（不再有自动重查）；其余 common.error_retry。
export function keepOnFailure(current: ResultScreen): ResultScreen {
  switch (current.status) {
    case "ready":
    case "not_paid":
      return current;
    case "confirming":
      return { ...current, slow: true, checking: false };
    default:
      return { status: "error" };
  }
}

// 点 result.check_again：自动重查用完的确认中才可以，按钮禁用并标 aria-busy。
export function startChecking(current: ResultScreen): ResultScreen {
  return current.status === "confirming" && current.slow && !current.checking ? { ...current, checking: true } : current;
}

// 页面对结果的处理：换成某个状态、按当前状态改动、用路由的 replace 换成 P06，
// 以及首次读到订单时的 takePaidNotCancelled 结果（notice）。
export interface ResultMoves {
  show: (screen: ResultScreen) => void;
  update: (change: (current: ResultScreen) => ResultScreen) => void;
  replace: (path: RoutePath) => void;
  notice: (shown: boolean) => void;
}

// 每次自动重查之前的等待，默认 api/pay.ts 的 wait（中止时提前结束）。
export type Delay = (ms: number, signal?: AbortSignal) => Promise<void>;

export interface ResultOptions {
  delay?: Delay;
  // 读到订单时以其订单号调用一次 takePaidNotCancelled（页面只在首次读到订单之前为 true）。
  takeNotice?: boolean;
}

// 确认中之后的一步：读取失败时沿用 order 仍为确认中；仍待支付且有 Stripe 记录时为读到的订单（确认中）；
// 其余照读到的结果显示或回 P06，返回 null。
function follow(step: ReadStep, order: PayOrder, moves: ResultMoves): PayOrder | null {
  switch (step.kind) {
    case "failed":
      return order;
    case "confirm":
      return step.order;
    case "pay":
      moves.replace(PAY_PATH);
      return null;
    case "show":
      moves.show(step.screen);
      return null;
  }
}

// 调用确认接口一次并按结果显示；仍为确认中时返回当时的订单，已按结果显示或已中止时返回 null。
async function confirmOnce(order: PayOrder, csrfToken: string, language: Language, signal: AbortSignal, moves: ResultMoves): Promise<PayOrder | null> {
  const confirm = await confirmStripe(order.order_number, csrfToken, signal);
  if (signal.aborted) {
    return null;
  }
  const step = confirmStep(confirm);
  if (step.kind === "show") {
    moves.show(step.screen);
    return null;
  }
  if (step.kind === "confirming") {
    return order;
  }
  const read = await readPayOrder(language, signal);
  if (signal.aborted) {
    return null;
  }
  return follow(readStep(read), order, moves);
}

// 一次重查：重新读取订单（取得新令牌），仍待支付且有 Stripe 记录时再确认一次；读取失败与确认失败一样仍为确认中。
async function recheck(order: PayOrder, language: Language, signal: AbortSignal, moves: ResultMoves): Promise<PayOrder | null> {
  const read = await readPayOrder(language, signal);
  if (signal.aborted) {
    return null;
  }
  const step = readStep(read);
  if (step.kind === "confirm") {
    return confirmOnce(step.order, step.csrfToken, language, signal, moves);
  }
  return follow(step, order, moves);
}

// 确认中的自动重查：显示确认中，每次等待 AUTO_RECHECK_MS 后重查，最多 AUTO_RECHECKS 次；用完仍为确认中时显示 result.check_again。
// 自动重查期间不切换为读取中；signal 中止（离开页面或切换语言）之后不再更新。
export async function recheckAutomatically(order: PayOrder, language: Language, signal: AbortSignal, moves: ResultMoves, delay: Delay = wait): Promise<void> {
  let current = order;
  moves.show(confirming(current, false));
  for (let count = 1; count <= AUTO_RECHECKS; count += 1) {
    await delay(AUTO_RECHECK_MS, signal);
    if (signal.aborted) {
      return;
    }
    const next = await recheck(current, language, signal, moves);
    if (next === null) {
      return;
    }
    current = next;
    moves.show(confirming(current, count === AUTO_RECHECKS));
  }
}

// 确认（读到仍待支付且有 Stripe 记录的订单之后）：确认返回前按读取中显示，确认中时进入自动重查。
export async function confirmResult(
  order: PayOrder,
  csrfToken: string,
  language: Language,
  signal: AbortSignal,
  moves: ResultMoves,
  delay: Delay = wait,
): Promise<void> {
  moves.show(LOADING);
  const next = await confirmOnce(order, csrfToken, language, signal, moves);
  if (next !== null) {
    await recheckAutomatically(next, language, signal, moves, delay);
  }
}

// 读取（打开页面、失败页倒计时到 0 与切换语言）；signal 中止之后不再更新。读取失败时照现有写法保留已显示的结果（keepOnFailure）。
export async function openResult(language: Language, signal: AbortSignal, moves: ResultMoves, { delay = wait, takeNotice = false }: ResultOptions = {}): Promise<void> {
  const read = await readPayOrder(language, signal);
  if (signal.aborted) {
    return;
  }
  if (read.kind === "ok" && takeNotice) {
    moves.notice(takePaidNotCancelled(read.order.order_number));
  }
  const step = readStep(read);
  switch (step.kind) {
    case "failed":
      moves.update(keepOnFailure);
      return;
    case "pay":
      moves.replace(PAY_PATH);
      return;
    case "show":
      moves.show(step.screen);
      return;
    case "confirm":
      await confirmResult(step.order, step.csrfToken, language, signal, moves, delay);
  }
}

// 手动再查（result.check_again，页面已按 startChecking 标为进行中）：重查一次，仍为确认中时按钮恢复可用。
export async function checkAgain(order: PayOrder, language: Language, signal: AbortSignal, moves: ResultMoves): Promise<void> {
  const next = await recheck(order, language, signal, moves);
  if (next !== null) {
    moves.show(confirming(next, true));
  }
}

export interface PayResultViewProps {
  screen: ResultScreen;
  minutes: number;
  states: ReadonlyMap<string, string> | null;
  // 从 P06 取消时会话已付款进入（成功区顶部的 result.paid_not_cancelled）。
  paidNotCancelled: boolean;
  onCheckAgain: () => void;
}

export function PayResultView({ screen, minutes, states, paidNotCancelled, onCheckAgain }: PayResultViewProps) {
  const t = useCopy();

  if (screen.status === "loading") {
    return <main className="site-pay" aria-busy="true"></main>;
  }
  if (screen.status === "expired") {
    return <SessionExpired />;
  }
  if (screen.status === "error") {
    return (
      <main className="site-pay">
        <ErrorNotice />
      </main>
    );
  }

  if (screen.status === "not_paid") {
    return (
      <main className="site-pay site-result">
        <div className="site-result__stripe">
          <div className="acs-alert acs-alert--info" role="status">
            <InfoIcon />
            <span>{t("result.stripe_not_paid")}</span>
          </div>
          <Link className="acs-btn acs-btn--primary" to={PAY_PATH}>
            {t("result.stripe_back_to_pay")}
          </Link>
        </div>
      </main>
    );
  }

  const guestNext = (
    <div className="acs-alert acs-alert--info">
      <InfoIcon />
      <span>{t("result.guest_next")}</span>
    </div>
  );

  if (screen.status === "confirming") {
    // 确认中不显示 result.demo_hint 与 pay.expires（UX 0.11）。
    return (
      <main className="site-pay site-result">
        <span className="site-result__pending">
          <PendingIcon />
        </span>
        <div className="site-result__confirming" role="status">
          <h1 className="acs-display-l">{t("result.confirming_title")}</h1>
          <p className="acs-body-l">{t("result.confirming_body")}</p>
        </div>
        <OrderCard order={screen.order} />
        {guestNext}
        {screen.slow && (
          <>
            <div className="acs-alert acs-alert--info" role="status">
              <WaitIcon />
              <span>{t("result.confirming_slow")}</span>
            </div>
            <button className="acs-btn acs-btn--primary site-result__check" type="button" disabled={screen.checking} aria-busy={screen.checking} onClick={onCheckAgain}>
              {t("result.check_again")}
            </button>
          </>
        )}
      </main>
    );
  }

  const { order } = screen;
  const kind = resultKind(order);
  const continueLink = (
    <Link className="acs-btn acs-btn--secondary site-result__continue" to={PRODUCTS_PATH}>
      {t("result.continue")}
    </Link>
  );

  if (kind === "failure") {
    return (
      <main className="site-pay site-result">
        <div className="site-result__head">
          <span className="acs-tag acs-tag--danger site-result__icon">
            <CrossIcon />
          </span>
          <h1 className="acs-display-l">{t("result.failure_title")}</h1>
          <p className="acs-body-l">{t("result.failure_body")}</p>
        </div>
        <ExpiresLine minutes={minutes} />
        <Link className="acs-btn acs-btn--primary site-result__retry" to={PAY_PATH}>
          {t("result.retry")}
        </Link>
      </main>
    );
  }

  if (kind === "cancelled" || kind === "cancelled_by_you") {
    // 有来源为会话的 Stripe 退款尝试时不论取消方都以 result.stripe_late_refund 代替（UX 0.11）。
    const text = order.stripe_refunded ? "result.stripe_late_refund" : kind === "cancelled" ? "result.cancelled" : "result.cancelled_by_you";
    return (
      <main className="site-pay site-result">
        <div className="acs-alert acs-alert--info" role="status">
          <InfoIcon />
          <span>{t(text)}</span>
        </div>
        {continueLink}
      </main>
    );
  }

  if (kind === "pay") {
    // 页面正在换成支付页。
    return <main className="site-pay" aria-busy="true"></main>;
  }

  return (
    <main className="site-pay site-result">
      {paidNotCancelled && (
        <div className="acs-alert acs-alert--success" role="status">
          <DoneIcon />
          <span>{t("result.paid_not_cancelled")}</span>
        </div>
      )}
      <div className="site-result__head">
        <span className="acs-tag acs-tag--success site-result__icon">
          <CheckIcon />
        </span>
        <h1 className="acs-display-l">{t("result.success_title")}</h1>
        <p className="acs-body-l">{t("result.success_body")}</p>
      </div>
      <OrderCard order={order} />
      <RecipientDetails recipient={order.recipient} states={states} />
      {guestNext}
      <Link className="site-result__register" to="/register">
        {t("result.guest_register")}
      </Link>
      <DemoHint>{t("result.demo_hint")}</DemoHint>
      {continueLink}
    </main>
  );
}

function pageMoves(setScreen: Dispatch<SetStateAction<ResultScreen>>, replace: (path: RoutePath) => void, notice: (shown: boolean) => void): ResultMoves {
  return {
    show: (screen) => {
      setScreen(screen);
    },
    update: (change) => {
      setScreen(change);
    },
    replace: (path) => {
      replace(path);
    },
    notice,
  };
}

export default function PayResultPage() {
  const { language } = useLanguage();
  const { replace } = useRouter();
  const [screen, setScreen] = useState<ResultScreen>(LOADING);
  // 失败页的倒计时到 0 或切换语言时重新读取订单。
  const [reads, setReads] = useState(0);
  const [paidNotCancelled, setPaidNotCancelled] = useState(false);
  // 首次读到订单之后不再调用 takePaidNotCancelled（它读一次即失效）。
  const noticeTaken = useRef(false);
  // 当前读取与其后的确认、重查共用的中止；离开页面、切换语言或重新读取时中止，自动重查次数随新的读取从头计。
  const lifetime = useRef<AbortController | null>(null);

  const order = screen.status === "ready" ? screen.order : null;
  const failure = order !== null && resultKind(order) === "failure" ? order : null;
  const minutes = useCountdown(failure, () => {
    setReads((count) => count + 1);
  });
  const states = useMyStates(order?.recipient ?? null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    const moves = pageMoves(setScreen, replace, (shown) => {
      noticeTaken.current = true;
      setPaidNotCancelled(shown);
    });
    void openResult(language, controller.signal, moves, { takeNotice: !noticeTaken.current });
    return () => {
      controller.abort();
    };
  }, [language, reads, replace]);

  return (
    <PayResultView
      screen={screen}
      minutes={minutes}
      states={states}
      paidNotCancelled={paidNotCancelled}
      onCheckAgain={() => {
        const controller = lifetime.current;
        const checking = startChecking(screen);
        if (checking === screen || checking.status !== "confirming" || controller === null) {
          return;
        }
        setScreen(checking);
        void checkAgain(checking.order, language, controller.signal, pageMoves(setScreen, replace, setPaidNotCancelled));
      }}
    />
  );
}
