import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { readLookupOrder, receiptKey, runConfirmReceipt, SHIPPED } from "../api/orderLookup";
import type { ConfirmOutcome, LookupFound, LookupLine, LookupOrder } from "../api/orderLookup";
import { AWAITING_PAYMENT } from "../api/pay";
import type { PayRecipient } from "../api/pay";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { Link } from "../router";
import { InfoIcon, recipientParts, useMyStates } from "./PayPage";
import { TRACK_PATH } from "./TrackPage";

// 订单详情页 P09 的查单模式（docs/UX.md P09）：打开时以当前语言调用 GET /api/orders/lookup，取第一张订单（最近查询的）；
// 之后重新读取（切换语言、确认收货之后）都按同一订单号取该单。401 或本浏览器对该单的授权已结束时整页显示 order.session_expired。
// 显示状态、进度五步、各行商品、金额明细与完整收货资料；只有 demo_shipped 可确认收货；待支付订单只说明查单不能支付或取消。
// 游客订单不显示优惠券与积分两行（UX 0.5）。退款入口与会员模式分别在退款申请页与会员中心实现前不渲染。
// 订单号与 CSRF 令牌只在页面内存（React 状态）里，不进网址或任何浏览器存储；页面不显示授权内容或其剩余时间。
// 金额只格式化接口返回的整数仙。

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

function usePrice(): (sen: number) => string {
  const t = useCopy();
  return (sen) => t("common.price_myr", { amount: formatSen(sen) });
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
        <span className="site-order__name">
          <span>{line.name}</span>
          {line.variant_label !== "" && (
            <span className="site-order__option">
              <SlashIcon />
              <span>{line.variant_label}</span>
            </span>
          )}
          <span className="acs-num">{`× ${String(line.quantity)}`}</span>
        </span>
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

// 金额明细：商品小计、示例运费、合计；游客订单不显示 checkout.summary_coupon 与 checkout.summary_points。
function AmountRows({ order }: { order: LookupOrder }) {
  const t = useCopy();
  const price = usePrice();
  return (
    <>
      <div className="acs-row">
        <span>{t("cart.subtotal")}</span>
        <span className="acs-num">{price(order.subtotal_sen)}</span>
      </div>
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

// 页面看到的订单：读取中、授权过期、读取失败、已取到。
export type OrderScreen = { status: "loading" } | { status: "expired" } | { status: "error" } | { status: "ready"; order: LookupOrder };

// 正在进行的操作：提交确认收货、网络中断后确认订单；进行中按钮禁用。
export type ConfirmBusy = "confirming" | "checking" | null;

export interface TrackOrderViewProps {
  screen: OrderScreen;
  states: ReadonlyMap<string, string> | null;
  busy: ConfirmBusy;
  // 显示 common.error_retry。
  failed: boolean;
  onConfirm: () => void;
}

// 查单授权过期或不属于该单时替换整页：order.session_expired 与去 P08 的 common.nav_track。
function SessionExpired() {
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

export function TrackOrderView({ screen, states, busy, failed, onConfirm }: TrackOrderViewProps) {
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
          {order.status === SHIPPED && (
            <section className="acs-card site-order__actions">
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
            </section>
          )}
          {/* 不放在确认收货卡片里：403 后重新读取到的订单可能已不是 demo_shipped，提示仍要显示。 */}
          {failed && busy === null && <ErrorNotice />}
          {order.status === AWAITING_PAYMENT && (
            <div className="acs-alert acs-alert--info">
              <InfoIcon />
              <span>{t("order.lookup_no_pay")}</span>
            </div>
          )}
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
  // 已显示的订单号：之后的重新读取取同一张订单。只在副作用与回调里读写。
  const shownOrder = useRef<string | null>(null);
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
      onConfirm={() => {
        if (order === null || csrfToken === null || busy !== null || order.status !== SHIPPED) {
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
