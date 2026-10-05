import { Fragment, useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

import { attemptFor, AWAITING_PAYMENT, fetchMyStates, minutesLeft, nextTickDelay, PAY_METHODS, readPayOrder, remainingAtRead, runCancel, runPayment } from "../api/pay";
import type { PayMethod, PayOrder, PayRead, PayRecipient, PayResult, PaymentAttempt, WriteOutcome } from "../api/pay";
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

// 正在进行的操作：提交支付、取消、网络中断后确认订单；进行中所有按钮禁用。
export type PayBusy = "paying" | "cancelling" | "checking" | null;

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
  onMethod: (method: PayMethod) => void;
  onPay: (result: PayResult) => void;
  onCancel: () => void;
  onConfirmCancel: () => void;
  onKeepOrder: () => void;
}

// 未选支付方式或有操作进行中时，成功与失败按钮禁用。
export function canSubmit(method: PayMethod | null, busy: PayBusy): boolean {
  return method !== null && busy === null;
}

function CancelDialog({ onConfirm, onKeep }: { onConfirm: () => void; onKeep: () => void }) {
  const t = useCopy();
  const labelId = useId();
  return (
    <div
      className="site-pay__overlay"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onKeep();
        }
      }}
    >
      <div className="acs-dialog" role="dialog" aria-modal="true" aria-labelledby={labelId}>
        <p id={labelId}>{t("pay.cancel_confirm")}</p>
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

export function PayView({ screen, minutes, states, method, busy, failed, confirming, onMethod, onPay, onCancel, onConfirmCancel, onKeepOrder }: PayViewProps) {
  const t = useCopy();
  const methodName = useId();

  if (screen.status === "loading") {
    return <main className="site-pay" aria-busy="true"></main>;
  }
  if (screen.status === "expired") {
    return <SessionExpired />;
  }

  const head = (
    <div className="site-pay__head">
      <div className="site-pay__title">
        <h1 className="acs-display-l">{t("pay.title")}</h1>
        <span className="acs-tag acs-tag--demo">{t("common.demo_badge")}</span>
      </div>
      <DemoHint>{t("pay.demo_hint")}</DemoHint>
    </div>
  );

  if (screen.status === "error") {
    return (
      <main className="site-pay">
        {head}
        <ErrorNotice />
      </main>
    );
  }

  const { order } = screen;
  const submittable = canSubmit(method, busy);
  return (
    <main className="site-pay" aria-busy={busy !== null}>
      {head}
      <OrderCard order={order}>
        <ExpiresLine minutes={minutes} />
        <div className="acs-alert acs-alert--info">
          <LockIcon />
          <span>{t("pay.guest_access")}</span>
        </div>
      </OrderCard>
      <RecipientDetails recipient={order.recipient} states={states} />
      <section className="site-pay__choose">
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
        {busy === "checking" && (
          <div className="acs-alert acs-alert--info site-pay__status" role="status">
            <InfoIcon />
            <span>{t("common.network_check")}</span>
          </div>
        )}
        {failed && busy === null && <ErrorNotice />}
        <ActionHint>{t("pay.action_hint")}</ActionHint>
      </section>
      <div className="site-pay__cancel">
        <button className="acs-btn acs-btn--danger" type="button" disabled={busy !== null} onClick={onCancel}>
          {t("pay.cancel_order")}
        </button>
      </div>
      {confirming && <CancelDialog onConfirm={onConfirmCancel} onKeep={onKeepOrder} />}
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
  // 网络中断后确认仍待支付的那次支付：同一方式与结果的重试沿用它的幂等键。
  const [retry, setRetry] = useState<PaymentAttempt | null>(null);
  // 倒计时到 0 或切换语言时重新读取订单。
  const [reads, setReads] = useState(0);
  // 离开页面时中止进行中的写操作与确认。
  const lifetime = useRef<AbortController | null>(null);

  const order = screen.status === "ready" ? screen.order : null;
  const minutes = useCountdown(order, () => {
    setReads((count) => count + 1);
  });
  const states = useMyStates(order?.recipient ?? null);

  const showOrder = useCallback(
    (read: Extract<PayRead, { kind: "ok" }>) => {
      if (read.order.status !== AWAITING_PAYMENT) {
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

  const settle = (outcome: WriteOutcome | null, action: "pay" | "cancel") => {
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
      case "error":
        if (outcome.read) {
          showOrder(outcome.read);
        }
        if (action === "pay") {
          setRetry(null);
        }
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
      onMethod={setMethod}
      onPay={(result) => {
        if (order === null || csrfToken === null || method === null || !canSubmit(method, busy)) {
          return;
        }
        const attempt = attemptFor(retry, method, result);
        setBusy("paying");
        setFailed(false);
        void runPayment(language, order.order_number, csrfToken, attempt, writeOptions()).then((outcome) => {
          settle(outcome, "pay");
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
        setBusy("cancelling");
        setFailed(false);
        void runCancel(language, order.order_number, csrfToken, writeOptions()).then((outcome) => {
          settle(outcome, "cancel");
        });
      }}
    />
  );
}
