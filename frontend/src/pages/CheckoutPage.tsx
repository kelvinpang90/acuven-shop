import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { fetchCheckoutQuote, fetchRegions, latestRuns, quoteRequestBody } from "../api/checkout";
import type { CheckoutDestination, CheckoutRegions, DestinationQuote, MyState, Outcome, PhoneRegion, QuoteLine } from "../api/checkout";
import type { LocalizedText } from "../api/catalog";
import { guestOrderBody, orderAttemptFor, runGuestOrder } from "../api/orders";
import type { GuestOrderReply, GuestRecipient, OrderAttempt } from "../api/orders";
import { fetchSiteSettings } from "../api/siteSettings";
import { cartItemCount, clearCart, useCartLines } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import { formatMinorUnits, formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { browserStorage, useCopy, useLanguage } from "../i18n/language";
import { isRoutePath, Link, PRODUCTS_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { hasChangedLine } from "./CartPage";
import { decidePhone, displayPhone, MALAYSIA, regionOptions } from "./checkoutPhone";
import type { GuestNotice, RegionOption } from "./checkoutPhone";
import { InfoIcon, PAY_PATH } from "./PayPage";

// 结账页 P05 的游客路径（docs/UX.md 0.7 P05，docs/UX-COPY.md 0.6）。
// 打开时读站点设置（短信验证开关，api/siteSettings.ts）与结账参考数据（地区与呼叫码、马来西亚州属，api/checkout.ts），
// 读完之前第 1 步不可提交，失败时显示 common.error_retry。
// 第 1 步：手机号（国家码下拉列出全部地区、默认 MY，以加号开头时以输入为准），旁边常显开关对应的告知；
// 继续后按 checkoutPhone.ts 的规则以游客进入第 3 步，或（开关开启时的马新号码）停在第 1 步显示 common.service_unavailable。
// 第 3 步：游客提示、收货资料（姓名、国家、马来西亚州属或其他国家的地区、地址、邮编）、只读的第 1 步号码，摘要与下单。
// 会员路径、短信验证、优惠券与积分不在本页（之后的任务）；游客摘要不显示优惠券与积分两行。
//
// 摘要用购物车各行 SKU 与件数（选定国家，马来西亚另须选定州属后，加国家与州属）调用计价接口，只采用最后一次请求的结果；
// 金额只格式化接口返回的整数（formatSen、formatMinorUnits），不在浏览器相加或相乘。
// 下单请求体与幂等键规则见 api/orders.ts；201 或 200 时清空本浏览器购物车并以站内导航进入 /pay（不带订单号）。
// 电话与收货资料只在 React 状态里，不进网址、localStorage、sessionStorage 或 cookie（刷新后需重填）。

export const CART_PATH: RoutePath = "/cart";
const PRIVACY_PATH: RoutePath = "/privacy";

// 登录页 P12 的路径；进了路由表才渲染 checkout.login_password。
function loginPath(): RoutePath | null {
  const path: string = "/login";
  return isRoutePath(path) ? path : null;
}

// 第 3 步收货资料的输入：国家与州属为代码，初始未选（空串）；地区只用于马来西亚以外的国家，可空。
export interface RecipientInput {
  name: string;
  country: string;
  state: string;
  region: string;
  address: string;
  postcode: string;
}

// 下单按钮旁的提示：changed 为 409 order_not_placeable 后的 cart.item_changed（显示在各步内容之前）；
// error 为 common.error_retry；unavailable 为 common.service_unavailable。
export type PlaceNotice = "changed" | "error" | "unavailable" | null;

export interface CheckoutForm {
  step: 1 | 3;
  // 国家码下拉所选的地区代码；参考数据读完前为空串，读完后为接口给的默认地区（MY）。
  phoneRegion: string;
  phoneInput: string;
  // 第 1 步的错误：invalid 为 checkout.phone_invalid（422 phone_invalid）；unavailable 为 common.service_unavailable（过渡规则）。
  phoneError: "invalid" | "unavailable" | null;
  // 第 3 步顶部的游客提示。
  guestNotice: GuestNotice | null;
  recipient: RecipientInput;
  submitting: boolean;
  // 网络中断、正以同一幂等键重试（显示 common.network_check）。
  checking: boolean;
  notice: PlaceNotice;
  // 上一次提交的幂等键与请求体；请求体不变时沿用。
  attempt: OrderAttempt | null;
}

export const INITIAL_FORM: CheckoutForm = {
  step: 1,
  phoneRegion: "",
  phoneInput: "",
  phoneError: null,
  guestNotice: null,
  recipient: { name: "", country: "", state: "", region: "", address: "", postcode: "" },
  submitting: false,
  checking: false,
  notice: null,
  attempt: null,
};

// 站点设置与参考数据：读取中、失败、已读到。
export type Reference = { status: "loading" } | { status: "error" } | { status: "ready"; smsEnabled: boolean; regions: CheckoutRegions };

// 页面看到的计价结果：首次请求中、失败、已返回（pending 为更新的请求尚未返回、显示的是上一次的结果）。
export type SummaryQuote = { status: "loading" } | { status: "error" } | { status: "ready"; quote: DestinationQuote; pending: boolean };

export interface SettledCheckoutQuote {
  key: string;
  outcome: Outcome<DestinationQuote>;
  last: DestinationQuote | null;
}

// 计价目的地：未选国家，或选了马来西亚而未选州属时为 null（不带目的地，摘要显示 cart.shipping_later）。
export function quoteDestination(country: string, state: string): CheckoutDestination | null {
  if (country === "") {
    return null;
  }
  if (country === MALAYSIA) {
    return state === "" ? null : { country_code: MALAYSIA, state_code: state };
  }
  return { country_code: country, state_code: null };
}

// 计价请求的标识：语言、SKU 与件数、目的地，以及 409 order_not_placeable 后重新计价的轮次。购物车为空时不请求。
export function checkoutQuoteKey(language: Language, lines: readonly CartLine[], destination: CheckoutDestination | null, round: number): string | null {
  if (lines.length === 0) {
    return null;
  }
  return [language, JSON.stringify(quoteRequestBody(lines)), destination?.country_code ?? "", destination?.state_code ?? "", String(round)].join("|");
}

export function summaryQuote(key: string | null, settled: SettledCheckoutQuote | null): SummaryQuote {
  if (settled !== null && settled.key === key) {
    return settled.outcome.status === "ready" ? { status: "ready", quote: settled.outcome.quote, pending: false } : { status: "error" };
  }
  return settled?.last ? { status: "ready", quote: settled.last, pending: true } : { status: "loading" };
}

// 接口给了运费与合计，即已按选定的目的地计价。
function hasDestination(quote: DestinationQuote): quote is DestinationQuote & { shipping: NonNullable<DestinationQuote["shipping"]>; total_sen: number } {
  return quote.shipping !== null && quote.total_sen !== null;
}

// 下单请求里的收货资料：马来西亚只带州属，其他国家只带地区。
export function guestRecipient(recipient: RecipientInput): GuestRecipient {
  const malaysia = recipient.country === MALAYSIA;
  return {
    name: recipient.name,
    address: recipient.address,
    postal_code: recipient.postcode,
    country_code: recipient.country,
    state_code: malaysia ? recipient.state : null,
    region: malaysia ? null : recipient.region,
  };
}

// 必填项：姓名、国家（马来西亚另加州属）、地址、邮编；地区可空。格式与长度由服务端判定。
export function recipientComplete(recipient: RecipientInput): boolean {
  return (
    recipient.name.trim() !== "" &&
    recipient.address.trim() !== "" &&
    recipient.postcode.trim() !== "" &&
    recipient.country !== "" &&
    (recipient.country !== MALAYSIA || recipient.state !== "")
  );
}

// 第 1 步可继续：参考数据已读到，号码非空（格式由服务端在下单时判定）。
export function canContinue(form: CheckoutForm, reference: Reference): boolean {
  return reference.status === "ready" && form.phoneRegion !== "" && form.phoneInput.trim() !== "";
}

// 第 1 步继续后的判定（checkoutPhone.ts 的 decidePhone）。
export function continueFromPhone(form: CheckoutForm, smsEnabled: boolean): CheckoutForm {
  if (form.phoneRegion === "" || form.phoneInput.trim() === "") {
    return form;
  }
  const decision = decidePhone(smsEnabled, form.phoneRegion, form.phoneInput);
  if (decision.kind === "unavailable") {
    return { ...form, phoneError: "unavailable" };
  }
  return { ...form, step: 3, phoneError: null, guestNotice: decision.notice, notice: null };
}

// checkout.phone_change：回第 1 步，已填的收货资料与号码保留。
export function backToPhone(form: CheckoutForm): CheckoutForm {
  return { ...form, step: 1, notice: null };
}

// 下单回答之后的表单（已填内容一律保留）：
// 409 order_not_placeable 显示 cart.item_changed（页面另行重新计价）；403 sms_verification_required 回第 1 步按开关开启重新判定；
// 422 phone_invalid 回第 1 步显示 checkout.phone_invalid；409 idempotency_conflict 换新幂等键并显示 common.error_retry；
// 503 显示 common.service_unavailable；其他显示 common.error_retry。
export function afterReply(form: CheckoutForm, reply: Exclude<GuestOrderReply, "network">): CheckoutForm {
  const settled: CheckoutForm = { ...form, submitting: false, checking: false };
  switch (reply) {
    case "placed":
      return { ...settled, notice: null };
    case "not_placeable":
      return { ...settled, notice: "changed" };
    case "sms_required": {
      const decision = decidePhone(true, form.phoneRegion, form.phoneInput);
      return { ...settled, step: 1, notice: null, phoneError: decision.kind === "unavailable" ? "unavailable" : null };
    }
    case "phone_invalid":
      return { ...settled, step: 1, notice: null, phoneError: "invalid" };
    case "conflict":
      return { ...settled, attempt: null, notice: "error" };
    case "unavailable":
      return { ...settled, notice: "unavailable" };
    case "failed":
      return { ...settled, notice: "error" };
  }
}

// 下单回答在表单之外的效果：placed 清空购物车并进入 /pay；sms_required 把开关当作已开启；not_placeable 重新计价。
export interface ReplyEffects {
  placed: boolean;
  smsEnabled: boolean;
  requote: boolean;
}

export function replyEffects(reply: Exclude<GuestOrderReply, "network">): ReplyEffects {
  return { placed: reply === "placed", smsEnabled: reply === "sms_required", requote: reply === "not_placeable" };
}

// 下单成功：清空本浏览器购物车，以站内导航进入 /pay（不带订单号或任何查询参数；P06 凭服务端发的 cookie 读取订单）。
export function completeOrder(storage: CartStorage | null, navigate: (path: RoutePath) => void): void {
  clearCart(storage);
  navigate(PAY_PATH);
}

// 可下单：第 3 步、未在提交、必填项齐全、当前请求的计价已返回、选定了目的地且接口说可下单、没有非正常行。
export function canPlaceOrder(form: CheckoutForm, quote: SummaryQuote, lines: readonly CartLine[]): boolean {
  return (
    form.step === 3 &&
    !form.submitting &&
    lines.length > 0 &&
    recipientComplete(form.recipient) &&
    quote.status === "ready" &&
    !quote.pending &&
    quote.quote.can_place_order &&
    hasDestination(quote.quote) &&
    !hasChangedLine(quote.quote.lines)
  );
}

function fallbackLang(text: LocalizedText): "en" | undefined {
  return text.english_fallback ? "en" : undefined;
}

function SlashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M15 4L9 20" />
    </svg>
  );
}

// cart.item_changed 提示条与字段错误的图形（取自视觉稿 P04-states、P05-states），读屏忽略。
function AlertIcon({ size }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16.5v.5" />
    </svg>
  );
}

// ◆ 下单提示（UX「演示提示汇总」）：与 ★ 同一提示样式，标记为菱形。
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

// 手机号旁与收货表单旁的告知：常显，无勾选框，旁边是链到隐私说明 P14 的 checkout.form_notice_link。
function FormNotice({ children }: { children: ReactNode }) {
  const t = useCopy();
  return (
    <div className="acs-hint acs-hint--block">
      <span>{children}</span>
      <Link to={PRIVACY_PATH}>{t("checkout.form_notice_link")}</Link>
    </div>
  );
}

function usePrice(): (sen: number) => string {
  const t = useCopy();
  return (sen) => t("common.price_myr", { amount: formatSen(sen) });
}

// 摘要的一行：名称 / 规格 × 件数与行小计；不可购买的行只有 SKU 与件数。
function SummaryLine({ row }: { row: QuoteLine }) {
  const price = usePrice();
  const quantity = `× ${String(row.quantity)}`;
  if (row.status === "unavailable") {
    return (
      <div className="acs-row acs-body-s">
        <span className="site-checkout__item">
          <span>{row.sku}</span>
          <span className="acs-num">{quantity}</span>
        </span>
      </div>
    );
  }
  return (
    <div className="acs-row acs-body-s">
      <span className="site-checkout__item">
        <span lang={fallbackLang(row.name)}>{row.name.text}</span>
        {row.options.map((option) => (
          <span key={option.code} className="site-checkout__option">
            <SlashIcon />
            <span lang={fallbackLang(option.value.name)}>{option.value.name.text}</span>
          </span>
        ))}
        <span className="acs-num">{quantity}</span>
      </span>
      <span className="acs-num">{price(row.line_subtotal_sen)}</span>
    </div>
  );
}

// 摘要的金额：商品小计；未选定目的地时 cart.shipping_later，选定后示例运费、合计与参考外币（或 checkout.fx_none）。
// 游客不显示 checkout.summary_coupon 与 checkout.summary_points。
function SummaryAmounts({ quote }: { quote: DestinationQuote }) {
  const t = useCopy();
  const price = usePrice();
  return (
    <>
      <div className="acs-row site-checkout__subtotal">
        <span>{t("cart.subtotal")}</span>
        <span>{price(quote.subtotal_sen)}</span>
      </div>
      {hasDestination(quote) ? (
        <>
          <div className="acs-row">
            <span>{t("checkout.summary_shipping")}</span>
            <span>{price(quote.shipping.fee_sen)}</span>
          </div>
          <div className="acs-row acs-row--total">
            <span>{t("checkout.summary_total")}</span>
            <span>{price(quote.total_sen)}</span>
          </div>
          {quote.fx_reference === null ? (
            <p className="acs-caption">{t("checkout.fx_none")}</p>
          ) : (
            <>
              <div className="acs-fx">
                {t("common.fx_reference", {
                  currency: quote.fx_reference.currency_code,
                  amount: formatMinorUnits(quote.fx_reference.amount_minor, quote.fx_reference.currency_decimals),
                })}
              </div>
              <p className="acs-caption">{t("checkout.fx_note")}</p>
            </>
          )}
        </>
      ) : (
        <p className="acs-body-s acs-muted">{t("cart.shipping_later")}</p>
      )}
    </>
  );
}

function SummaryContent({ quote, rows }: { quote: SummaryQuote; rows: readonly QuoteLine[] }) {
  if (quote.status === "loading") {
    return null;
  }
  if (quote.status === "error") {
    return <ErrorNotice />;
  }
  return (
    <>
      {rows.map((row) => (
        <SummaryLine key={row.sku} row={row} />
      ))}
      <SummaryAmounts quote={quote.quote} />
    </>
  );
}

// 手机折叠摘要与底部固定栏的一行：选定目的地前为商品小计，选定后为合计。
function HeadlineAmount({ quote, className }: { quote: DestinationQuote; className: string }) {
  const t = useCopy();
  const price = usePrice();
  const [label, sen]: [CopyKey, number] = hasDestination(quote) ? ["checkout.summary_total", quote.total_sen] : ["cart.subtotal", quote.subtotal_sen];
  return (
    <span className={className}>
      <span>{t(label)}</span>
      <span className="acs-price">{price(sen)}</span>
    </span>
  );
}

// 有非正常行（或下单时 409 order_not_placeable）：cart.item_changed，并可回购物车（链接文字为页头同一条 common.nav_cart）。
function ChangedNotice({ lines }: { lines: readonly CartLine[] }) {
  const t = useCopy();
  return (
    <div className="site-checkout__changed">
      <div className="acs-alert acs-alert--danger" role="alert">
        <AlertIcon />
        <span>{t("cart.item_changed")}</span>
      </div>
      <Link className="acs-btn acs-btn--secondary site-checkout__back" to={CART_PATH}>
        {t("common.nav_cart", { count: cartItemCount(lines) })}
      </Link>
    </div>
  );
}

function ServiceUnavailable() {
  const t = useCopy();
  return (
    <p className="acs-alert acs-alert--danger site-notice" role="alert">
      {t("common.service_unavailable")}
    </p>
  );
}

export interface CheckoutActions {
  onPhoneRegion: (code: string) => void;
  onPhoneInput: (value: string) => void;
  onContinue: () => void;
  onChangePhone: () => void;
  onRecipient: (patch: Partial<RecipientInput>) => void;
  onPlaceOrder: () => void;
}

interface StepProps {
  form: CheckoutForm;
  reference: Reference;
  options: readonly RegionOption[];
  actions: CheckoutActions;
}

function PhoneStep({ form, reference, options, actions }: StepProps) {
  const t = useCopy();
  const labelId = useId();
  const inputId = useId();
  const errorId = useId();
  const login = loginPath();
  const invalid = form.phoneError === "invalid";
  return (
    <section className="site-checkout__step">
      <h2 className="acs-display-s">{t("checkout.phone_step_title")}</h2>
      {reference.status === "error" && <ErrorNotice />}
      {reference.status === "ready" && <FormNotice>{t(reference.smsEnabled ? "checkout.phone_notice" : "checkout.phone_notice_sms_off")}</FormNotice>}
      <form
        className="site-checkout__form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          actions.onContinue();
        }}
      >
        <div className={invalid ? "acs-field acs-field--invalid" : "acs-field"}>
          <label className="acs-field__label" id={labelId} htmlFor={inputId}>
            {t("auth.phone")}
          </label>
          <div className="acs-phone site-checkout__phone">
            <select
              className="acs-select"
              aria-labelledby={labelId}
              value={form.phoneRegion}
              disabled={reference.status !== "ready"}
              onChange={(event) => {
                actions.onPhoneRegion(event.target.value);
              }}
            >
              {options.map((option) => (
                <option key={option.code} value={option.code}>
                  {`${option.name} +${String(option.callingCode)}`}
                </option>
              ))}
            </select>
            <input
              className="acs-input"
              id={inputId}
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              value={form.phoneInput}
              aria-invalid={invalid ? true : undefined}
              aria-describedby={invalid ? errorId : undefined}
              onChange={(event) => {
                actions.onPhoneInput(event.target.value);
              }}
            />
          </div>
          <span className="acs-field__hint">{t("checkout.phone_step_hint")}</span>
          {invalid && (
            <span className="acs-field__error" id={errorId}>
              <AlertIcon size={16} />
              {t("checkout.phone_invalid")}
            </span>
          )}
        </div>
        {form.phoneError === "unavailable" && <ServiceUnavailable />}
        <button className="acs-btn acs-btn--primary site-checkout__continue" type="submit" disabled={!canContinue(form, reference)}>
          {t("checkout.phone_continue")}
        </button>
      </form>
      {login && (
        <Link className="site-checkout__login" to={login}>
          {t("checkout.login_password")}
        </Link>
      )}
    </section>
  );
}

const NO_REGIONS: readonly PhoneRegion[] = [];

function RecipientStep({ form, reference, options, actions }: StepProps) {
  const t = useCopy();
  const nameId = useId();
  const countryId = useId();
  const areaId = useId();
  const addressId = useId();
  const postcodeId = useId();
  const phoneId = useId();
  const phoneHintId = useId();
  const { recipient, submitting } = form;
  const regions = reference.status === "ready" ? reference.regions.regions : NO_REGIONS;
  const states: readonly MyState[] = reference.status === "ready" ? reference.regions.my_states : [];
  return (
    <>
      {form.guestNotice !== null && (
        <div className="site-checkout__guest">
          <div className="acs-alert acs-alert--info" role="status">
            <InfoIcon />
            <span>{t(form.guestNotice)}</span>
          </div>
          <p className="acs-body-s">{t("checkout.guest_notice")}</p>
        </div>
      )}
      <section className="site-checkout__step">
        <h2 className="acs-display-s">{t("checkout.recipient_title")}</h2>
        <FormNotice>{t("checkout.form_notice")}</FormNotice>
        <div className="site-checkout__fields">
          <div className="acs-field site-checkout__wide">
            <label className="acs-field__label" htmlFor={nameId}>
              {t("checkout.name")}
            </label>
            <input
              className="acs-input"
              id={nameId}
              autoComplete="name"
              required
              disabled={submitting}
              value={recipient.name}
              onChange={(event) => {
                actions.onRecipient({ name: event.target.value });
              }}
            />
          </div>
          <div className="acs-field">
            <label className="acs-field__label" htmlFor={countryId}>
              {t("checkout.country")}
            </label>
            <select
              className="acs-select"
              id={countryId}
              autoComplete="country"
              required
              disabled={submitting}
              value={recipient.country}
              onChange={(event) => {
                actions.onRecipient({ country: event.target.value });
              }}
            >
              <option value=""></option>
              {options.map((option) => (
                <option key={option.code} value={option.code}>
                  {option.name}
                </option>
              ))}
            </select>
          </div>
          {recipient.country === MALAYSIA ? (
            <div className="acs-field">
              <label className="acs-field__label" htmlFor={areaId}>
                {t("checkout.state_my")}
              </label>
              <select
                className="acs-select"
                id={areaId}
                required
                disabled={submitting}
                value={recipient.state}
                onChange={(event) => {
                  actions.onRecipient({ state: event.target.value });
                }}
              >
                <option value=""></option>
                {states.map((state) => (
                  <option key={state.code} value={state.code}>
                    {state.name}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="acs-field">
              <label className="acs-field__label" htmlFor={areaId}>
                {t("checkout.region")}
              </label>
              <input
                className="acs-input"
                id={areaId}
                autoComplete="address-level1"
                disabled={submitting}
                value={recipient.region}
                onChange={(event) => {
                  actions.onRecipient({ region: event.target.value });
                }}
              />
            </div>
          )}
          <div className="acs-field site-checkout__wide">
            <label className="acs-field__label" htmlFor={addressId}>
              {t("checkout.address")}
            </label>
            <input
              className="acs-input"
              id={addressId}
              autoComplete="street-address"
              required
              disabled={submitting}
              value={recipient.address}
              onChange={(event) => {
                actions.onRecipient({ address: event.target.value });
              }}
            />
          </div>
          <div className="acs-field">
            <label className="acs-field__label" htmlFor={postcodeId}>
              {t("checkout.postcode")}
            </label>
            <input
              className="acs-input"
              id={postcodeId}
              autoComplete="postal-code"
              required
              disabled={submitting}
              value={recipient.postcode}
              onChange={(event) => {
                actions.onRecipient({ postcode: event.target.value });
              }}
            />
          </div>
          <div className="acs-field site-checkout__wide">
            <label className="acs-field__label" htmlFor={phoneId}>
              {t("checkout.phone")}
            </label>
            <div className="site-checkout__readonly">
              <input className="acs-input" id={phoneId} type="tel" readOnly value={displayPhone(form.phoneRegion, form.phoneInput, regions)} aria-describedby={phoneHintId} />
              <button className="acs-btn acs-btn--quiet" type="button" disabled={submitting} onClick={actions.onChangePhone}>
                {t("checkout.phone_change")}
              </button>
            </div>
            <span className="acs-field__hint" id={phoneHintId}>
              {t("checkout.phone_lookup_hint")}
            </span>
          </div>
        </div>
      </section>
      <div className="site-checkout__members">
        <p className="acs-body-s">{t("checkout.coupon_members_only")}</p>
        <p className="acs-body-s acs-muted">{t("checkout.points_guest")}</p>
      </div>
    </>
  );
}

// 下单按钮与 ◆ 提示；提交期间禁用并显示 checkout.submitting，网络中断重试时显示 common.network_check，回答后的提示在按钮之前。
function PlaceOrder({ form, placeable, onPlaceOrder }: { form: CheckoutForm; placeable: boolean; onPlaceOrder: () => void }) {
  const t = useCopy();
  return (
    <div className="site-checkout__place">
      {form.checking && (
        <div className="acs-alert acs-alert--info" role="status">
          <InfoIcon />
          <span>{t("common.network_check")}</span>
        </div>
      )}
      {!form.submitting && form.notice === "error" && <ErrorNotice />}
      {!form.submitting && form.notice === "unavailable" && <ServiceUnavailable />}
      <button
        className="acs-btn acs-btn--primary acs-btn--lg acs-btn--block"
        type="button"
        disabled={!placeable}
        aria-busy={form.submitting ? true : undefined}
        onClick={onPlaceOrder}
      >
        {t(form.submitting ? "checkout.submitting" : "checkout.place_order")}
      </button>
      <ActionHint>{t("checkout.place_order_hint")}</ActionHint>
    </div>
  );
}

export interface CheckoutViewProps {
  lines: readonly CartLine[];
  reference: Reference;
  quote: SummaryQuote;
  form: CheckoutForm;
  actions: CheckoutActions;
}

export function CheckoutView({ lines, reference, quote, form, actions }: CheckoutViewProps) {
  const t = useCopy();
  const { language } = useLanguage();
  const regions = reference.status === "ready" ? reference.regions.regions : NO_REGIONS;
  const options = useMemo(() => regionOptions(regions, language), [regions, language]);

  const head = (
    <div className="site-checkout__head">
      <h1 className="acs-display-l">{t("checkout.title")}</h1>
      <DemoHint>{t("checkout.demo_hint")}</DemoHint>
    </div>
  );

  if (lines.length === 0) {
    return (
      <main className="site-checkout">
        {head}
        <div className="site-checkout__empty">
          <p className="acs-display-s">{t("cart.empty")}</p>
          <Link className="acs-btn acs-btn--secondary" to={PRODUCTS_PATH}>
            {t("cart.continue")}
          </Link>
        </div>
      </main>
    );
  }

  const busy = reference.status === "loading" || quote.status === "loading" || (quote.status === "ready" && quote.pending) || form.submitting;
  const ready = quote.status === "ready" ? quote.quote : null;
  // 已从购物车移除的行不等新结果返回就不再显示。
  const inCart = new Set(lines.map((line) => line.sku));
  const rows = ready === null ? [] : ready.lines.filter((row) => inCart.has(row.sku));
  const changed = (ready !== null && hasChangedLine(rows)) || form.notice === "changed";
  const placeable = canPlaceOrder(form, quote, lines);
  const step = { form, reference, options, actions };

  return (
    <main className="site-checkout" aria-busy={busy}>
      {head}
      <details className="acs-summary site-phone-only site-checkout__fold">
        <summary className="site-checkout__fold-head">
          <span>{t("checkout.summary_title")}</span>
          {ready !== null && <HeadlineAmount quote={ready} className="acs-num site-checkout__fold-amount" />}
        </summary>
        <SummaryContent quote={quote} rows={rows} />
      </details>
      <div className="site-checkout__body">
        <div className="site-checkout__main">
          {changed && <ChangedNotice lines={lines} />}
          {form.step === 1 ? <PhoneStep {...step} /> : <RecipientStep {...step} />}
          {form.step === 3 && (
            <div className="acs-summary site-phone-only site-checkout__amounts">
              {quote.status === "error" && <ErrorNotice />}
              {ready !== null && <SummaryAmounts quote={ready} />}
            </div>
          )}
        </div>
        <aside className="site-desktop-only site-checkout__aside">
          <div className="acs-summary">
            <h2 className="acs-display-s acs-summary__title">{t("checkout.summary_title")}</h2>
            <SummaryContent quote={quote} rows={rows} />
          </div>
          {form.step === 3 && <PlaceOrder form={form} placeable={placeable} onPlaceOrder={actions.onPlaceOrder} />}
        </aside>
      </div>
      {form.step === 3 && (
        <div className="acs site-phone-only site-checkout__bar">
          {ready !== null && <HeadlineAmount quote={ready} className="acs-row site-checkout__bar-amount" />}
          <PlaceOrder form={form} placeable={placeable} onPlaceOrder={actions.onPlaceOrder} />
        </div>
      )}
    </main>
  );
}

export default function CheckoutPage() {
  const { language } = useLanguage();
  const { navigate } = useRouter();
  const storage = browserStorage();
  const lines = useCartLines(storage);
  const [reference, setReference] = useState<Reference>({ status: "loading" });
  const [form, setForm] = useState<CheckoutForm>(INITIAL_FORM);
  const [quoter] = useState(() => latestRuns<DestinationQuote>());
  const [settled, setSettled] = useState<SettledCheckoutQuote | null>(null);
  // 409 order_not_placeable 后加一，重新计价。
  const [round, setRound] = useState(0);
  // 离开页面时中止进行中的下单与重试。
  const lifetime = useRef<AbortController | null>(null);

  const destination = useMemo(() => quoteDestination(form.recipient.country, form.recipient.state), [form.recipient.country, form.recipient.state]);
  const key = checkoutQuoteKey(language, lines, destination, round);
  const quote = summaryQuote(key, settled);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => {
      controller.abort();
    };
  }, []);

  // 打开时读开关与参考数据；读完之前第 1 步不可提交。
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([fetchSiteSettings(controller.signal), fetchRegions(controller.signal)]).then(
      ([settings, regions]) => {
        setReference({ status: "ready", smsEnabled: settings.sms_verification_enabled, regions });
        setForm((current) => (current.phoneRegion === "" ? { ...current, phoneRegion: regions.default_phone_region } : current));
      },
      () => {
        if (!controller.signal.aborted) {
          setReference({ status: "error" });
        }
      },
    );
    return () => {
      controller.abort();
    };
  }, []);

  // 购物车、语言或目的地变了就重新计价；作废的请求由 latestRuns 中止并丢弃。
  useEffect(() => {
    if (key === null) {
      return undefined;
    }
    void quoter.run((signal) => fetchCheckoutQuote(language, lines, destination, signal)).then((outcome) => {
      if (outcome !== null) {
        setSettled((previous) => ({
          key,
          outcome,
          last: outcome.status === "ready" ? outcome.quote : (previous?.last ?? null),
        }));
      }
    });
    return () => {
      quoter.cancel();
    };
  }, [key, language, lines, destination, quoter]);

  const placeOrder = () => {
    if (!canPlaceOrder(form, quote, lines)) {
      return;
    }
    const body = guestOrderBody(lines, { input: form.phoneInput, region: form.phoneRegion }, guestRecipient(form.recipient));
    const attempt = orderAttemptFor(form.attempt, body);
    setForm((current) => ({ ...current, attempt, submitting: true, checking: false, notice: null }));
    void runGuestOrder(body, attempt.key, {
      signal: lifetime.current?.signal,
      onChecking: () => {
        setForm((current) => ({ ...current, checking: true }));
      },
    }).then((reply) => {
      if (reply === null) {
        return;
      }
      const effects = replyEffects(reply);
      if (effects.placed) {
        completeOrder(storage, navigate);
        return;
      }
      if (effects.smsEnabled) {
        setReference((current) => (current.status === "ready" ? { ...current, smsEnabled: true } : current));
      }
      if (effects.requote) {
        setRound((count) => count + 1);
      }
      setForm((current) => afterReply(current, reply));
    });
  };

  const actions: CheckoutActions = {
    onPhoneRegion: (code) => {
      setForm((current) => ({ ...current, phoneRegion: code, phoneError: null }));
    },
    onPhoneInput: (value) => {
      setForm((current) => ({ ...current, phoneInput: value, phoneError: null }));
    },
    onContinue: () => {
      if (reference.status === "ready") {
        const { smsEnabled } = reference;
        setForm((current) => continueFromPhone(current, smsEnabled));
      }
    },
    onChangePhone: () => {
      setForm(backToPhone);
    },
    onRecipient: (patch) => {
      setForm((current) => ({ ...current, recipient: { ...current.recipient, ...patch } }));
    },
    onPlaceOrder: placeOrder,
  };

  return <CheckoutView lines={lines} reference={reference} quote={quote} form={form} actions={actions} />;
}
