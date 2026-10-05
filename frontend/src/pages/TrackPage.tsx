import { useId, useState } from "react";
import type { FormEvent } from "react";

import { submitLookup } from "../api/orderLookup";
import type { LookupReply } from "../api/orderLookup";
import { DemoHint } from "../components/SiteFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { useRouter } from "../router";
import type { RoutePath } from "../router";

// 订单查询页 P08（docs/UX.md P08）：以订单号与下单电话调用 POST /api/orders/lookup，通过（204）后转到订单详情 P09 查单模式。
// 订单号与电话只在页面内存（React 状态）与查单请求体里，不进网址或任何浏览器存储；每次打开本页输入框为空，不预填上一单。
// 浏览器只要求两项非空，格式由服务端判定；失败留在本页并保留已输入内容。页面不显示查单授权的内容或剩余时间。

export const TRACK_PATH: RoutePath = "/track";
export const TRACK_ORDER_PATH: RoutePath = "/track/order";

// 查单失败的提示：404 不区分「不存在」与「电话不符」；其他意外回答显示 common.error_retry。
export const LOOKUP_ERROR: Readonly<Record<Exclude<LookupReply, "found">, CopyKey>> = {
  not_found: "lookup.not_found",
  rate_limited: "common.rate_limited",
  unavailable: "common.service_unavailable",
  network: "common.network_check",
  failed: "common.error_retry",
};

export interface TrackFields {
  orderNumber: string;
  phone: string;
}

export const EMPTY_FIELDS: TrackFields = { orderNumber: "", phone: "" };

// 两项都非空且没有进行中的查询时才提交。
export function canLookup(fields: TrackFields, busy: boolean): boolean {
  return !busy && fields.orderNumber.trim() !== "" && fields.phone.trim() !== "";
}

// 提交查单：通过时转到 P09（go）并返回 null；否则返回要显示的提示，页面留在本页。
export async function lookupAndGo(fields: TrackFields, go: (path: RoutePath) => void): Promise<CopyKey | null> {
  const reply = await submitLookup(fields.orderNumber, fields.phone);
  if (reply === "found") {
    go(TRACK_ORDER_PATH);
    return null;
  }
  return LOOKUP_ERROR[reply];
}

// 提示条图形（取自视觉稿 P08-phone），读屏忽略。
function AlertIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

export interface TrackViewProps {
  fields: TrackFields;
  busy: boolean;
  error: CopyKey | null;
  onChange: (fields: TrackFields) => void;
  onSubmit: () => void;
}

// 输入框不设 name：不用脚本时表单也不会把订单号或电话放进网址的查询参数。
export function TrackView({ fields, busy, error, onChange, onSubmit }: TrackViewProps) {
  const t = useCopy();
  const orderId = useId();
  const phoneId = useId();
  const hintId = useId();
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit();
  };
  return (
    <main className="site-track" aria-busy={busy}>
      <h1 className="acs-display-l">{t("lookup.title")}</h1>
      <DemoHint>{t("lookup.demo_hint")}</DemoHint>
      <form className="acs-card site-track__form" method="post" onSubmit={handleSubmit}>
        <div className="acs-field">
          <label className="acs-field__label" htmlFor={orderId}>
            {t("pay.order_no")}
          </label>
          <input
            className="acs-input acs-num"
            id={orderId}
            autoComplete="off"
            required
            value={fields.orderNumber}
            onChange={(event) => {
              onChange({ ...fields, orderNumber: event.currentTarget.value });
            }}
          />
        </div>
        <div className="acs-field">
          <label className="acs-field__label" htmlFor={phoneId}>
            {t("lookup.phone")}
          </label>
          <input
            className="acs-input"
            id={phoneId}
            inputMode="tel"
            autoComplete="off"
            required
            aria-describedby={hintId}
            value={fields.phone}
            onChange={(event) => {
              onChange({ ...fields, phone: event.currentTarget.value });
            }}
          />
          <span className="acs-field__hint" id={hintId}>
            {t("lookup.phone_hint")}
          </span>
        </div>
        <button className="acs-btn acs-btn--primary site-track__submit" type="submit" disabled={busy}>
          {t("lookup.submit")}
        </button>
      </form>
      <p className="acs-body-s">{t("lookup.access_note")}</p>
      <p className="acs-body-s acs-muted">{t("lookup.privacy_warning")}</p>
      {error !== null && (
        <div className="acs-alert acs-alert--danger" role="alert">
          <AlertIcon />
          <span>{t(error)}</span>
        </div>
      )}
    </main>
  );
}

export default function TrackPage() {
  const { navigate } = useRouter();
  const [fields, setFields] = useState<TrackFields>(EMPTY_FIELDS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CopyKey | null>(null);
  return (
    <TrackView
      fields={fields}
      busy={busy}
      error={error}
      onChange={setFields}
      onSubmit={() => {
        if (!canLookup(fields, busy)) {
          return;
        }
        setBusy(true);
        setError(null);
        void lookupAndGo(fields, navigate).then((found) => {
          setError(found);
          setBusy(false);
        });
      }}
    />
  );
}
