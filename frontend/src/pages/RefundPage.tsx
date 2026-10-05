import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

import { clampQuantities, handOffSubmittedRefund, readLookupOrder, refundEstimate, refundKey, refundLines, runRefund } from "../api/orderLookup";
import type { LookupFound, LookupLine, RefundOutcome, RefundRefusal, RefundRetry } from "../api/orderLookup";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatSen } from "../format";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { useRouter } from "../router";
import { InfoIcon } from "./PayPage";
import { LineName, SessionExpired, TRACK_ORDER_PATH, usePrice } from "./TrackOrderPage";
import type { OrderScreen } from "./TrackOrderPage";

// 退款申请页 P10 的查单模式（docs/UX.md P10）：打开时以当前语言调用 GET /api/orders/lookup，取第一张订单；
// 之后重新读取（切换语言、提交被拒或失败之后）都按同一订单号取该单。401 或本浏览器对该单的授权已结束时整页显示 order.session_expired。
// 显示 refund.title、order.title、★ refund.demo_hint、order.lookup_access；refund.select_items 与各行（名称与规格、order.cash_paid 与逐件实付、
// detail.quantity 加减控件（0 到该行可退件数）与 refund.max_qty）；refund.estimate、refund.shipping_not_refunded、refund.coupon_not_restored、
// refund.submit 与 ◆ refund.submit_hint。手机上合计、◆ 提示与提交按钮固定在底部。
// 游客订单不显示积分三行（refund.points_back、refund.points_reversed、refund.expired_points_note）；会员模式在会员中心实现前不渲染。
// 预计金额只把接口给的各行预计金额列表中对应件数的那一项相加，不在浏览器做乘除或分摊。
// 订单号与 CSRF 令牌只在页面内存（React 状态）里，不进网址或任何浏览器存储。

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

export interface RefundViewProps {
  screen: OrderScreen;
  // 各行选定的件数，与订单行同序。
  quantities: readonly number[];
  busy: RefundBusy;
  notice: RefundNotice;
  onQuantity: (index: number, quantity: number) => void;
  onSubmit: () => void;
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

export function RefundView({ screen, quantities, busy, notice, onQuantity, onSubmit }: RefundViewProps) {
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
      <p className="acs-body-s acs-muted">{t("order.lookup_access")}</p>
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
      </div>
      <section className="acs-summary site-desktop-only site-refund__summary">
        {estimateText !== null && <span className="acs-price acs-price--l">{estimateText}</span>}
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
        <ActionHint>{t("refund.submit_hint")}</ActionHint>
        {submit("acs-btn acs-btn--primary acs-btn--block")}
      </div>
    </main>
  );
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
