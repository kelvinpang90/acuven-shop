import { useEffect, useState } from "react";

import { readPayOrder, resultKind } from "../api/pay";
import type { PayOrder } from "../api/pay";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { useCopy, useLanguage } from "../i18n/language";
import { Link, PRODUCTS_PATH, useRouter } from "../router";
import { ExpiresLine, InfoIcon, OrderCard, PAY_PATH, RecipientDetails, SessionExpired, useCountdown, useMyStates } from "./PayPage";

// 模拟支付结果页 P07（docs/UX.md P07，游客）：打开时以当前语言重新调用 GET /api/pay/orders，取第一张订单，按它的状态显示：
// 已模拟支付 → 成功（订单号与复制、应付金额、收货资料、result.guest_next、★ result.demo_hint、result.continue）；
// 最近一次支付失败且仍待支付 → 失败（pay.expires 倒计时与回 P06 的 result.retry），倒计时到 0 时重新读取；
// 已取消 → 按取消方 result.cancelled（超时）或 result.cancelled_by_you，以及 result.continue。
// 仍待支付而最近一次不是失败时换成支付页 P06。401 时整页 pay.session_expired。
// result.guest_register、result.track 按路由规则在注册页、会员订单详情实现前不渲染；游客不显示获得积分。

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

export type ResultScreen = { status: "loading" } | { status: "expired" } | { status: "error" } | { status: "ready"; order: PayOrder };

export interface PayResultViewProps {
  screen: ResultScreen;
  minutes: number;
  states: ReadonlyMap<string, string> | null;
}

export function PayResultView({ screen, minutes, states }: PayResultViewProps) {
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
    return (
      <main className="site-pay site-result">
        <div className="acs-alert acs-alert--info" role="status">
          <InfoIcon />
          <span>{t(kind === "cancelled" ? "result.cancelled" : "result.cancelled_by_you")}</span>
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
      <div className="site-result__head">
        <span className="acs-tag acs-tag--success site-result__icon">
          <CheckIcon />
        </span>
        <h1 className="acs-display-l">{t("result.success_title")}</h1>
        <p className="acs-body-l">{t("result.success_body")}</p>
      </div>
      <OrderCard order={order} />
      <RecipientDetails recipient={order.recipient} states={states} />
      <div className="acs-alert acs-alert--info">
        <InfoIcon />
        <span>{t("result.guest_next")}</span>
      </div>
      <DemoHint>{t("result.demo_hint")}</DemoHint>
      {continueLink}
    </main>
  );
}

export default function PayResultPage() {
  const { language } = useLanguage();
  const { replace } = useRouter();
  const [screen, setScreen] = useState<ResultScreen>({ status: "loading" });
  // 失败页的倒计时到 0 或切换语言时重新读取订单。
  const [reads, setReads] = useState(0);

  const order = screen.status === "ready" ? screen.order : null;
  const failure = order !== null && resultKind(order) === "failure" ? order : null;
  const minutes = useCountdown(failure, () => {
    setReads((count) => count + 1);
  });
  const states = useMyStates(order?.recipient ?? null);

  useEffect(() => {
    const controller = new AbortController();
    void readPayOrder(language, controller.signal).then((read) => {
      if (controller.signal.aborted) {
        return;
      }
      if (read.kind === "ok") {
        if (resultKind(read.order) === "pay") {
          replace(PAY_PATH);
          return;
        }
        setScreen({ status: "ready", order: read.order });
      } else if (read.kind === "expired") {
        setScreen({ status: "expired" });
      } else {
        setScreen((current) => (current.status === "ready" ? current : { status: "error" }));
      }
    });
    return () => {
      controller.abort();
    };
  }, [language, reads, replace]);

  return <PayResultView screen={screen} minutes={minutes} states={states} />;
}
