import { Fragment, useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  attemptFor,
  AWAITING_PAYMENT,
  fetchMyStates,
  minutesLeft,
  nextTickDelay,
  PAY_METHODS,
  readPayOrder,
  remainingAtRead,
  rememberPaidNotCancelled,
  runCancel,
  runPayment,
  startStripe,
} from "../api/pay";
import type { PayMethod, PayOrder, PayRead, PayRecipient, PayResult, PaymentAttempt, StripeStart, WriteOutcome } from "../api/pay";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { isRoutePath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";

// 模拟支付页 P06（docs/UX.md P06，游客）：打开时以当前语言调用 GET /api/pay/orders，取第一张订单（最近签发的授权）。
// 待支付时显示订单号与复制、应付金额、支付时限倒计时、游客访问说明、收货资料、三种演示支付方式与成功、失败、取消；
// 订单不是待支付状态时直接换成结果页 P07；401 时整页显示 pay.session_expired。
// 订单号、电话与 CSRF 令牌只在页面内存（React 状态）里，不进网址或任何浏览器存储；页面不显示凭据或其剩余时间。
// 金额只格式化接口返回的整数仙；不出现卡号、有效期或 CVV 输入框，不显示参考外币。
// 支付途径为 Stripe 测试时（docs/UX.md 0.11「P06 Stripe 测试」，SHOP-TASK-134）：标题、★ 与操作区换成 Stripe 的，
// 按 stripe_state 显示跳转、进行中或转人工核对；跳转只在当前标签页打开服务端返回的付款页地址。
// 读到已付款完成的会话（complete）时换成 P07，由 P07 调用确认接口（HANDOFF 0.55 第（5）项）。
// 取消（SHOP-TASK-135）：有进行中的会话时确认框另显示 pay.cancel_confirm_stripe；Stripe 暂不可用时在取消按钮旁显示 pay.cancel_try_later；
// Stripe 途径的取消转到 P07 之前记下「取消时会话已付款」的交接（rememberPaidNotCancelled），由 P07 按订单已付款决定是否显示。
// 本文件另导出 P07 共用的订单卡片、收货资料、倒计时与凭据过期整页。

export const PAY_PATH: RoutePath = "/pay";
export const PAY_RESULT_PATH: RoutePath = "/pay/result";

const METHOD_LABEL: Readonly<Record<PayMethod, CopyKey>> = {
  card: "pay.method_card",
  bank: "pay.method_bank",
  ewallet: "pay.method_ewallet",
};

// 订单查询页 P08 的路径；进了路由表才渲染凭据过期后的 common.nav_track。
function trackPath(): RoutePath | null {
  const path: string = "/track";
  return isRoutePath(path) ? path : null;
}

function ClockIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="13" r="8" />
      <path d="M12 9v4l3 2M9 2h6" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="5" y="10" width="14" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

export function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5v.5" />
    </svg>
  );
}

// 以下图标取自视觉稿 P06-stripe-desktop、P06-stripe-phone-open 与 P06-stripe-states。
function PendingIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4" />
    </svg>
  );
}

function AlertIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

function CardIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <rect x="2.5" y="5" width="19" height="14" rx="2" />
      <path d="M2.5 9.5h19M6 15h4" />
    </svg>
  );
}

function ExternalIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M7 17L17 7M9 7h8v8" />
    </svg>
  );
}

// ◆ 模拟支付提示（UX「演示提示汇总」）：与 ★ 同一提示样式，标记为菱形。
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

// 复制到剪贴板；没有剪贴板或写入被拒时返回 false。
export async function copyText(text: string, clipboard: Pick<Clipboard, "writeText"> | undefined = typeof navigator === "undefined" ? undefined : navigator.clipboard): Promise<boolean> {
  if (!clipboard) {
    return false;
  }
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function CopyButton({ text }: { text: string }) {
  const t = useCopy();
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="acs-btn acs-btn--secondary acs-btn--sm"
      type="button"
      aria-live="polite"
      onClick={() => {
        void copyText(text).then(setCopied);
      }}
    >
      {t(copied ? "common.copied" : "common.copy")}
    </button>
  );
}

// 订单卡片：pay.order_no 与订单号（common.copy）、pay.save_order_no、pay.amount_due 与合计；children 在其后（倒计时、游客访问说明）。
export function OrderCard({ order, children }: { order: PayOrder; children?: ReactNode }) {
  const t = useCopy();
  return (
    <section className="acs-card site-pay__card">
      <div className="site-pay__order-no">
        <span className="acs-field__label">{t("pay.order_no")}</span>
        <span className="site-pay__copy">
          <span className="acs-num site-pay__number">{order.order_number}</span>
          <CopyButton text={order.order_number} />
        </span>
      </div>
      <p className="acs-body-s acs-muted">{t("pay.save_order_no")}</p>
      <div className="acs-row site-pay__due">
        <span className="acs-field__label">{t("pay.amount_due")}</span>
        <span className="acs-price acs-price--l">{t("common.price_myr", { amount: formatSen(order.total_sen) })}</span>
      </div>
      {children}
    </section>
  );
}

// pay.expires：{minutes} 为支付时限的剩余分钟。
export function ExpiresLine({ minutes }: { minutes: number }) {
  const t = useCopy();
  return (
    <div className="site-pay__expires">
      <ClockIcon />
      <span className="acs-body-s">{t("pay.expires", { minutes })}</span>
    </div>
  );
}

// 国家名称按界面语言本地化（接口只给两位代码，SHOP-TASK-023）；取不到时显示代码。
export function countryName(code: string, language: Language): string {
  try {
    return new Intl.DisplayNames([htmlLang(language)], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export interface RecipientParts {
  name: string;
  phone: string;
  address: string;
  postcode: string;
  region: string | null;
  country: string;
}

// 收货资料原文：马来西亚的州属代码换成州属名称（取不到时显示代码），其他国家的地区为自由文本或空。
export function recipientParts(recipient: PayRecipient, language: Language, states: ReadonlyMap<string, string> | null): RecipientParts {
  const region = recipient.region === null || recipient.region === "" ? null : recipient.region;
  return {
    name: recipient.name,
    phone: recipient.phone,
    address: recipient.address,
    postcode: recipient.postal_code,
    region: region !== null && recipient.country_code === "MY" ? (states?.get(region) ?? region) : region,
    country: countryName(recipient.country_code, language),
  };
}

// 取自视觉稿：姓名 · 电话，换行后 地址, 邮编, 地区, 国家。
function RecipientText({ parts }: { parts: RecipientParts }) {
  const place = [parts.address, parts.postcode, parts.region, parts.country].filter((value): value is string => value !== null && value !== "");
  return (
    <>
      <span>{parts.name}</span>
      {" · "}
      <span>{parts.phone}</span>
      <br />
      {place.map((value, index) => (
        <Fragment key={index}>
          {index > 0 && ", "}
          <span>{value}</span>
        </Fragment>
      ))}
    </>
  );
}

// 收货资料：桌面为标题与正文，手机折叠（▸ checkout.recipient_title）；按宽度只显示其一。没有收货资料记录时不渲染。
export function RecipientDetails({ recipient, states }: { recipient: PayRecipient | null; states: ReadonlyMap<string, string> | null }) {
  const t = useCopy();
  const { language } = useLanguage();
  if (recipient === null) {
    return null;
  }
  const parts = recipientParts(recipient, language, states);
  return (
    <>
      <section className="site-desktop-only site-pay__recipient">
        <h2 className="acs-display-s">{t("checkout.recipient_title")}</h2>
        <p>
          <RecipientText parts={parts} />
        </p>
      </section>
      <details className="acs-summary site-phone-only site-pay__recipient-toggle">
        <summary className="acs-field__label">{t("checkout.recipient_title")}</summary>
        <p className="acs-body-s">
          <RecipientText parts={parts} />
        </p>
      </details>
    </>
  );
}

// 凭据过期或不属于该单时替换整页：pay.session_expired；common.nav_track 在订单查询页实现前不渲染。
export function SessionExpired() {
  const t = useCopy();
  const track = trackPath();
  return (
    <main className="site-pay site-pay--expired">
      <div className="acs-alert acs-alert--info" role="status">
        <LockIcon />
        <span>{t("pay.session_expired")}</span>
      </div>
      {track && (
        <Link className="acs-btn acs-btn--primary site-pay__track" to={track}>
          {t("common.nav_track")}
        </Link>
      )}
    </main>
  );
}

// 支付时限倒计时：读取时的剩余时间只由接口的支付到期时间与服务器当前时间相减得到，之后的流逝以单调时钟（performance.now）计，
// 不读访客电脑的时钟；分钟数每变一次（每分钟）更新，到 0 时调用 onZero 一次。订单换了（重新读取）就重新开始。
export function useCountdown(order: PayOrder | null, onZero: () => void): number {
  const [elapsed, setElapsed] = useState<{ order: PayOrder | null; ms: number }>({ order: null, ms: 0 });
  const onZeroRef = useRef(onZero);
  useEffect(() => {
    onZeroRef.current = onZero;
  });
  useEffect(() => {
    if (order === null) {
      return undefined;
    }
    const base = remainingAtRead(order);
    const start = performance.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = (left: number) => {
      timer = setTimeout(() => {
        const passed = performance.now() - start;
        setElapsed({ order, ms: passed });
        if (base - passed <= 0) {
          onZeroRef.current();
        } else {
          schedule(base - passed);
        }
      }, nextTickDelay(left));
    };
    schedule(base);
    return () => {
      clearTimeout(timer);
    };
  }, [order]);
  if (order === null) {
    return 0;
  }
  return minutesLeft(remainingAtRead(order) - (elapsed.order === order ? elapsed.ms : 0));
}

// 马来西亚收货地址才读取州属名称表；读取失败时显示州属代码。
export function useMyStates(recipient: PayRecipient | null): ReadonlyMap<string, string> | null {
  const [states, setStates] = useState<ReadonlyMap<string, string> | null>(null);
  const needed = recipient?.country_code === "MY";
  useEffect(() => {
    if (!needed) {
      return undefined;
    }
    const controller = new AbortController();
    void fetchMyStates(controller.signal).then((found) => {
      if (!controller.signal.aborted) {
        setStates(found);
      }
    });
    return () => {
      controller.abort();
    };
  }, [needed]);
  return states;
}

// 页面看到的订单：读取中、凭据过期、读取失败、已取到。
export type PayScreen = { status: "loading" } | { status: "expired" } | { status: "error" } | { status: "ready"; order: PayOrder };

// 正在进行的操作：提交支付、取消、网络中断后确认订单、发起 Stripe 跳转（拿到地址后保持到页面离开）；进行中所有按钮禁用。
export type PayBusy = "paying" | "cancelling" | "checking" | "redirecting" | null;

export interface PayViewProps {
  screen: PayScreen;
  minutes: number;
  states: ReadonlyMap<string, string> | null;
  method: PayMethod | null;
  busy: PayBusy;
  // 显示 common.error_retry。
  failed: boolean;
  // 取消确认框是否打开。
  confirming: boolean;
  // 显示 pay.stripe_unavailable（Stripe 途径的跳转按钮下）。
  stripeUnavailable: boolean;
  // 显示 pay.method_changed（两种途径都有）。
  methodChanged: boolean;
  // 显示 pay.cancel_try_later（取消时 Stripe 暂不可用，取消按钮旁）。
  cancelTryLater: boolean;
  onMethod: (method: PayMethod) => void;
  onPay: (result: PayResult) => void;
  onStripe: () => void;
  onCancel: () => void;
  onConfirmCancel: () => void;
  onKeepOrder: () => void;
}

// 未选支付方式或有操作进行中时，成功与失败按钮禁用。
export function canSubmit(method: PayMethod | null, busy: PayBusy): boolean {
  return method !== null && busy === null;
}

export function isStripeRoute(order: Pick<PayOrder, "payment_route">): boolean {
  return order.payment_route === "stripe";
}

// Stripe 途径的操作区按 stripe_state 显示哪一项（UX 0.11「P06 Stripe 测试」说明）：
// 未发起（null）与创建结果未定（creating）为 pay.stripe_go；有进行中的会话（open）为 pay.stripe_continue；
// 转人工核对（under_review）为 pay.stripe_under_review、没有跳转按钮；complete 不在本页显示（换成 P07），也没有按钮。
export type StripeAction = "go" | "continue" | "review" | null;

export function stripeAction(order: Pick<PayOrder, "stripe_state">): StripeAction {
  switch (order.stripe_state) {
    case null:
    case "creating":
      return "go";
    case "open":
      return "continue";
    case "under_review":
      return "review";
    case "complete":
      return null;
  }
}

// pay.expires 倒计时只在模拟途径与 Stripe 未发起时显示；创建中、进行中与转人工核对时订单不因 15 分钟到期取消（UX 0.11，DESIGN 1.12 第 3、5 条）。
export function showsCountdown(order: Pick<PayOrder, "payment_route" | "stripe_state">): boolean {
  return !isStripeRoute(order) || order.stripe_state === null;
}

// 订单已不是待支付，或已有付款完成的会话（交给 P07 确认，HANDOFF 0.55 第（5）项）时，本页换成 P07。
export function leavesPayPage(order: Pick<PayOrder, "status" | "stripe_state">): boolean {
  return order.status !== AWAITING_PAYMENT || order.stripe_state === "complete";
}

// startStripe 的结果之后页面的去向：
// redirect 在当前标签页打开服务端返回的地址；result 换成 P07（complete、closed）；expired 整页 pay.session_expired；
// unavailable 在按钮下显示 pay.stripe_unavailable、按钮恢复可点；method_changed 显示 pay.method_changed 并重新读取；
// reread 重新读取并按读到的状态显示（under_review）；error 显示 common.error_retry 并重新读取（csrf 取得新令牌，failed、network 按读到的状态显示）。
export type StripeMove =
  | { kind: "redirect"; url: string }
  | { kind: "result" }
  | { kind: "expired" }
  | { kind: "unavailable" }
  | { kind: "method_changed" }
  | { kind: "reread" }
  | { kind: "error" };

export function stripeMove(start: StripeStart): StripeMove {
  switch (start.kind) {
    case "redirect":
      return { kind: "redirect", url: start.url };
    case "complete":
    case "closed":
      return { kind: "result" };
    case "expired":
      return { kind: "expired" };
    case "unavailable":
      return { kind: "unavailable" };
    case "method_changed":
      return { kind: "method_changed" };
    case "under_review":
      return { kind: "reread" };
    case "csrf":
    case "failed":
    case "network":
      return { kind: "error" };
  }
}

// 取消确认框在 pay.cancel_confirm 之下另显示 pay.cancel_confirm_stripe：只在 Stripe 途径有进行中的会话（open）时（UX 0.11「点取消后确认框」）。
export function confirmsStripeCancel(order: Pick<PayOrder, "payment_route" | "stripe_state">): boolean {
  return isStripeRoute(order) && order.stripe_state === "open";
}

// runCancel 的结果之后页面的去向：
// result 换成 P07，Stripe 途径的订单先记下交接（handOver，HANDOFF 0.55 第（5）项；P07 只在按成功显示时才显示 result.paid_not_cancelled），模拟途径不记；
// expired 整页 pay.session_expired；try_later 为 Stripe 暂不可用（503 stripe_unavailable，DESIGN 1.12 第 5 条）：在取消按钮旁显示 pay.cancel_try_later、订单保持待支付；
// error 照现有写法显示 common.error_retry（read 为重新读取到的订单与新令牌：csrf、网络中断后确认仍待支付；method_changed 与 failed 没有）。
export type CancelMove =
  | { kind: "result"; handOver: boolean }
  | { kind: "expired" }
  | { kind: "try_later" }
  | { kind: "error"; read: Extract<PayRead, { kind: "ok" }> | null };

export function cancelMove(outcome: WriteOutcome, order: Pick<PayOrder, "payment_route">): CancelMove {
  switch (outcome.kind) {
    case "result":
      return { kind: "result", handOver: isStripeRoute(order) };
    case "expired":
      return { kind: "expired" };
    case "unavailable":
      return { kind: "try_later" };
    case "method_changed":
      return { kind: "error", read: null };
    case "error":
    case "retry":
      return { kind: "error", read: outcome.read };
  }
}

// 取消之后转到结果页：handOver 时先以该订单号记下交接，再用路由的 replace 换成 /pay/result。
export function leaveAfterCancel(orderNumber: string, handOver: boolean, replace: (path: RoutePath) => void): void {
  if (handOver) {
    rememberPaidNotCancelled(orderNumber);
  }
  replace(PAY_RESULT_PATH);
}

// pay.method_changed：服务端按当时的开关拒绝了不符的提交，页面已重新读取（UX 0.11「支付途径已改变」）。
function MethodChangedNotice() {
  const t = useCopy();
  return (
    <div className="acs-alert acs-alert--info" role="status">
      <RefreshIcon />
      <span>{t("pay.method_changed")}</span>
    </div>
  );
}

// stripe 时 pay.cancel_confirm 之下另有 pay.cancel_confirm_stripe（视觉稿 P06-stripe-cancel-confirm），作为对话框的说明。
function CancelDialog({ stripe, onConfirm, onKeep }: { stripe: boolean; onConfirm: () => void; onKeep: () => void }) {
  const t = useCopy();
  const labelId = useId();
  const descriptionId = useId();
  return (
    <div
      className="site-pay__overlay"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onKeep();
        }
      }}
    >
      <div className="acs-dialog" role="dialog" aria-modal="true" aria-labelledby={labelId} aria-describedby={stripe ? descriptionId : undefined}>
        <p id={labelId}>{t("pay.cancel_confirm")}</p>
        {stripe && (
          <p id={descriptionId} className="acs-body-s acs-muted">
            {t("pay.cancel_confirm_stripe")}
          </p>
        )}
        <div className="acs-dialog__actions">
          <button className="acs-btn acs-btn--secondary" type="button" autoFocus onClick={onKeep}>
            {t("pay.cancel_confirm_no")}
          </button>
          <button className="acs-btn acs-btn--danger" type="button" onClick={onConfirm}>
            {t("pay.cancel_confirm_yes")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PayView({
  screen,
  minutes,
  states,
  method,
  busy,
  failed,
  confirming,
  stripeUnavailable,
  methodChanged,
  cancelTryLater,
  onMethod,
  onPay,
  onStripe,
  onCancel,
  onConfirmCancel,
  onKeepOrder,
}: PayViewProps) {
  const t = useCopy();
  const methodName = useId();

  if (screen.status === "loading") {
    return <main className="site-pay" aria-busy="true"></main>;
  }
  if (screen.status === "expired") {
    return <SessionExpired />;
  }

  // 标题与 ★：Stripe 途径为 pay.stripe_title 与 pay.stripe_demo_hint（UX「演示提示汇总」）。
  const head = (stripe: boolean) => (
    <div className="site-pay__head">
      <div className="site-pay__title">
        <h1 className="acs-display-l">{t(stripe ? "pay.stripe_title" : "pay.title")}</h1>
        <span className="acs-tag acs-tag--demo">{t("common.demo_badge")}</span>
      </div>
      <DemoHint>{t(stripe ? "pay.stripe_demo_hint" : "pay.demo_hint")}</DemoHint>
    </div>
  );

  if (screen.status === "error") {
    return (
      <main className="site-pay">
        {head(false)}
        <ErrorNotice />
      </main>
    );
  }

  const { order } = screen;
  const stripe = isStripeRoute(order);
  const submittable = canSubmit(method, busy);
  const action = stripeAction(order);
  const tryLater = cancelTryLater && busy === null;
  const checking = busy === "checking" && (
    <div className="acs-alert acs-alert--info site-pay__status" role="status">
      <InfoIcon />
      <span>{t("common.network_check")}</span>
    </div>
  );
  return (
    <main className="site-pay" aria-busy={busy !== null}>
      {head(stripe)}
      <OrderCard order={order}>
        {showsCountdown(order) && <ExpiresLine minutes={minutes} />}
        <div className="acs-alert acs-alert--info">
          <LockIcon />
          <span>{t("pay.guest_access")}</span>
        </div>
      </OrderCard>
      <RecipientDetails recipient={order.recipient} states={states} />
      {stripe ? (
        <section className="site-pay__choose site-pay__stripe">
          {methodChanged && <MethodChangedNotice />}
          {action === "continue" && (
            <div className="acs-alert acs-alert--info site-pay__in-progress" role="status">
              <PendingIcon />
              <span>{t("pay.stripe_in_progress")}</span>
            </div>
          )}
          <ActionHint>{t("pay.stripe_notice")}</ActionHint>
          <div className="site-pay__test-card">
            <CardIcon />
            <p>{t("pay.stripe_test_card")}</p>
          </div>
          <div className={busy === "redirecting" ? "site-pay__go site-pay__go--redirecting" : "site-pay__go"}>
            {(action === "go" || action === "continue") && (
              <button
                className="acs-btn acs-btn--primary acs-btn--lg"
                type="button"
                disabled={busy !== null}
                aria-busy={busy === "redirecting"}
                onClick={onStripe}
              >
                {t(action === "go" ? "pay.stripe_go" : "pay.stripe_continue")}
                {busy !== "redirecting" && <ExternalIcon />}
              </button>
            )}
            {action === "review" && (
              <div className="acs-alert acs-alert--info" role="status">
                <PendingIcon />
                <span>{t("pay.stripe_under_review")}</span>
              </div>
            )}
            {busy === "redirecting" && (
              <p className="acs-body-s" role="status">
                {t("pay.stripe_redirecting")}
              </p>
            )}
            {checking}
            {stripeUnavailable && busy === null && (
              <div className="acs-alert acs-alert--danger" role="alert">
                <AlertIcon />
                <span>{t("pay.stripe_unavailable")}</span>
              </div>
            )}
            {failed && busy === null && <ErrorNotice />}
          </div>
        </section>
      ) : (
      <section className="site-pay__choose">
        {methodChanged && <MethodChangedNotice />}
        <fieldset className="site-pay__methods">
          <legend className="acs-display-s">{t("pay.choose_method")}</legend>
          {PAY_METHODS.map((option) => (
            <label key={option} className="acs-card site-pay__method">
              <input
                type="radio"
                name={methodName}
                value={option}
                checked={method === option}
                disabled={busy !== null}
                onChange={() => {
                  onMethod(option);
                }}
              />
              <span>{t(METHOD_LABEL[option])}</span>
            </label>
          ))}
        </fieldset>
        <div className="site-pay__actions">
          <button
            className="acs-btn acs-btn--primary acs-btn--lg"
            type="button"
            disabled={!submittable}
            onClick={() => {
              onPay("success");
            }}
          >
            {t("pay.simulate_success")}
          </button>
          <button
            className="acs-btn acs-btn--secondary acs-btn--lg"
            type="button"
            disabled={!submittable}
            onClick={() => {
              onPay("failure");
            }}
          >
            {t("pay.simulate_failure")}
          </button>
        </div>
        {busy === "paying" && (
          <p className="acs-body-s site-pay__status" role="status">
            {t("pay.processing")}
          </p>
        )}
        {checking}
        {failed && busy === null && <ErrorNotice />}
        <ActionHint>{t("pay.action_hint")}</ActionHint>
      </section>
      )}
      {/* 取消未成（视觉稿 P06-stripe-states）：pay.cancel_try_later 在取消按钮之上、同一区块里，按钮可点 */}
      <div className={tryLater ? "site-pay__cancel site-pay__cancel--try-later" : "site-pay__cancel"}>
        {tryLater && (
          <div className="acs-alert acs-alert--danger" role="alert">
            <AlertIcon />
            <span>{t("pay.cancel_try_later")}</span>
          </div>
        )}
        <button className="acs-btn acs-btn--danger" type="button" disabled={busy !== null} onClick={onCancel}>
          {t("pay.cancel_order")}
        </button>
      </div>
      {confirming && <CancelDialog stripe={confirmsStripeCancel(order)} onConfirm={onConfirmCancel} onKeep={onKeepOrder} />}
    </main>
  );
}

export default function PayPage() {
  const { language } = useLanguage();
  const { replace } = useRouter();
  const [screen, setScreen] = useState<PayScreen>({ status: "loading" });
  // CSRF 令牌只在内存里：每次读取订单时由接口给出。
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [method, setMethod] = useState<PayMethod | null>(null);
  const [busy, setBusy] = useState<PayBusy>(null);
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [stripeUnavailable, setStripeUnavailable] = useState(false);
  const [methodChanged, setMethodChanged] = useState(false);
  const [cancelTryLater, setCancelTryLater] = useState(false);
  // 网络中断后确认仍待支付的那次支付：同一方式与结果的重试沿用它的幂等键。
  const [retry, setRetry] = useState<PaymentAttempt | null>(null);
  // 倒计时到 0、切换语言或 Stripe 发起之后需要按服务端的状态显示时重新读取订单。
  const [reads, setReads] = useState(0);
  // 离开页面时中止进行中的写操作与确认。
  const lifetime = useRef<AbortController | null>(null);

  const order = screen.status === "ready" ? screen.order : null;
  // 只在显示 pay.expires 时计时，到 0 才重新读取。
  const minutes = useCountdown(order !== null && showsCountdown(order) ? order : null, () => {
    setReads((count) => count + 1);
  });
  const states = useMyStates(order?.recipient ?? null);

  const reread = () => {
    setReads((count) => count + 1);
  };

  // 下一次操作开始：收起上一次的提示。
  const startOperation = (next: Exclude<PayBusy, null>) => {
    setBusy(next);
    setFailed(false);
    setStripeUnavailable(false);
    setMethodChanged(false);
    setCancelTryLater(false);
  };

  const showOrder = useCallback(
    (read: Extract<PayRead, { kind: "ok" }>) => {
      if (leavesPayPage(read.order)) {
        replace(PAY_RESULT_PATH);
        return;
      }
      setScreen({ status: "ready", order: read.order });
      setCsrfToken(read.csrfToken);
    },
    [replace],
  );

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => {
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void readPayOrder(language, controller.signal).then((read) => {
      if (controller.signal.aborted) {
        return;
      }
      if (read.kind === "ok") {
        showOrder(read);
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
  }, [language, reads, showOrder]);

  // 模拟支付的去向。
  const settle = (outcome: WriteOutcome | null) => {
    if (outcome === null) {
      return;
    }
    switch (outcome.kind) {
      case "result":
        replace(PAY_RESULT_PATH);
        return;
      case "expired":
        setScreen({ status: "expired" });
        return;
      case "method_changed":
        // 模拟支付时本单已走 Stripe 途径：显示 pay.method_changed 并重新读取，按新的途径显示。
        setRetry(null);
        setMethodChanged(true);
        setBusy(null);
        reread();
        return;
      case "unavailable":
        break;
      case "error":
        if (outcome.read) {
          showOrder(outcome.read);
        }
        setRetry(null);
        break;
      case "retry":
        showOrder(outcome.read);
        if (outcome.attempt) {
          setRetry(outcome.attempt);
        }
        break;
    }
    setFailed(true);
    setBusy(null);
  };

  // 取消的去向按 cancelMove（SHOP-TASK-135）；cancelled 为发起取消时的订单。
  const settleCancel = (outcome: WriteOutcome | null, cancelled: PayOrder) => {
    if (outcome === null) {
      return;
    }
    const next = cancelMove(outcome, cancelled);
    switch (next.kind) {
      case "result":
        leaveAfterCancel(cancelled.order_number, next.handOver, replace);
        return;
      case "expired":
        setScreen({ status: "expired" });
        return;
      case "try_later":
        setCancelTryLater(true);
        break;
      case "error":
        if (next.read) {
          showOrder(next.read);
        }
        setFailed(true);
        break;
    }
    setBusy(null);
  };

  // 只在点击处理里调用：网络中断后确认订单时显示 common.network_check。
  const writeOptions = () => ({
    signal: lifetime.current?.signal,
    onChecking: () => {
      setBusy("checking");
    },
  });

  return (
    <PayView
      screen={screen}
      minutes={minutes}
      states={states}
      method={method}
      busy={busy}
      failed={failed}
      confirming={confirming}
      stripeUnavailable={stripeUnavailable}
      methodChanged={methodChanged}
      cancelTryLater={cancelTryLater}
      onMethod={setMethod}
      onPay={(result) => {
        if (order === null || csrfToken === null || method === null || !canSubmit(method, busy)) {
          return;
        }
        const attempt = attemptFor(retry, method, result);
        startOperation("paying");
        void runPayment(language, order.order_number, csrfToken, attempt, writeOptions()).then((outcome) => {
          settle(outcome);
        });
      }}
      onStripe={() => {
        if (order === null || csrfToken === null || busy !== null || !isStripeRoute(order)) {
          return;
        }
        const signal = lifetime.current?.signal;
        startOperation("redirecting");
        void startStripe(language, order.order_number, csrfToken, signal).then((start) => {
          if (signal?.aborted) {
            return;
          }
          const move = stripeMove(start);
          switch (move.kind) {
            case "redirect":
              // 当前标签页打开服务端返回的地址；按钮保持禁用直到页面离开。
              window.location.assign(move.url);
              return;
            case "result":
              replace(PAY_RESULT_PATH);
              return;
            case "expired":
              setScreen({ status: "expired" });
              return;
            case "unavailable":
              setStripeUnavailable(true);
              break;
            case "method_changed":
              setMethodChanged(true);
              reread();
              break;
            case "reread":
              reread();
              break;
            case "error":
              setFailed(true);
              reread();
              break;
          }
          setBusy(null);
        });
      }}
      onCancel={() => {
        setConfirming(true);
      }}
      onKeepOrder={() => {
        setConfirming(false);
      }}
      onConfirmCancel={() => {
        setConfirming(false);
        if (order === null || csrfToken === null || busy !== null) {
          return;
        }
        startOperation("cancelling");
        void runCancel(language, order.order_number, csrfToken, writeOptions()).then((outcome) => {
          settleCancel(outcome, order);
        });
      }}
    />
  );
}
