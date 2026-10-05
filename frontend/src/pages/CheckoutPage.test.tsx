import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CheckoutRegions, DestinationQuote, PricedQuoteLine, QuoteLine } from "../api/checkout";
import { guestOrderBody, orderAttemptFor, runGuestOrder } from "../api/orders";
import App from "../App";
import { CART_STORAGE_KEY, readCart } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  afterReply,
  backToPhone,
  canContinue,
  canPlaceOrder,
  checkoutQuoteKey,
  CheckoutView,
  completeOrder,
  continueFromPhone,
  guestRecipient,
  INITIAL_FORM,
  quoteDestination,
  recipientComplete,
  replyEffects,
  summaryQuote,
} from "./CheckoutPage";
import type { CheckoutActions, CheckoutForm, Reference, SummaryQuote } from "./CheckoutPage";
import { countryNames } from "./checkoutPhone";

afterEach(() => {
  vi.unstubAllGlobals();
});

// UX-COPY 中游客摘要不应出现的两行（字典只收录页面用到的键，所以按文档原文比较）。
const COUPON_ROW: Readonly<Record<Language, string>> = { en: "Coupon discount", zh: "优惠券抵扣", ms: "Diskaun kupon" };
const POINTS_ROW: Readonly<Record<Language, string>> = { en: "Points discount", zh: "积分抵扣", ms: "Diskaun mata" };

const REGIONS: CheckoutRegions = {
  regions: [
    { code: "GB", calling_code: 44 },
    { code: "MY", calling_code: 60 },
    { code: "SG", calling_code: 65 },
  ],
  my_states: [
    { code: "MY-10", name: "Selangor" },
    { code: "MY-14", name: "Wilayah Persekutuan Kuala Lumpur" },
  ],
  default_phone_region: "MY",
};

function referenceOf(smsEnabled: boolean): Reference {
  return { status: "ready", smsEnabled, regions: REGIONS };
}

function text(value: string, english_fallback = false) {
  return { text: value, english_fallback };
}

function priced(sku: string, overrides: Partial<PricedQuoteLine> = {}): PricedQuoteLine {
  return {
    sku,
    quantity: 1,
    status: "ok",
    available_stock: null,
    max_per_order: 10,
    product_slug: "crew-neck-tee",
    name: text("Crew Neck Tee"),
    options: [
      { code: "color", name: text("Colour"), value: { code: "black", name: text("Black") } },
      { code: "size", name: text("Size"), value: { code: "m", name: text("M") } },
    ],
    image: null,
    unit_price_sen: 3900,
    line_subtotal_sen: 3900,
    ...overrides,
  };
}

const CART: CartLine[] = [
  { sku: "tee-black-m", slug: "crew-neck-tee", quantity: 2 },
  { sku: "candle", slug: "candle", quantity: 1 },
];

const ROWS: QuoteLine[] = [
  priced("tee-black-m", { quantity: 2, line_subtotal_sen: 7800 }),
  priced("candle", { product_slug: "candle", name: text("Soy Wax Candle", true), options: [], line_subtotal_sen: 3200 }),
];

function quoteOf(overrides: Partial<DestinationQuote> = {}): DestinationQuote {
  return { lines: ROWS, subtotal_sen: 11000, shipping: null, total_sen: null, fx_reference: null, can_place_order: false, ...overrides };
}

const NO_COUNTRY = quoteOf();
const TO_BRITAIN = quoteOf({
  shipping: { fee_sen: 2500, zone_code: "GB", version: 1 },
  total_sen: 13500,
  fx_reference: { currency_code: "GBP", currency_decimals: 2, amount_minor: 2295, version: 1 },
  can_place_order: true,
});
const TO_SELANGOR = quoteOf({ shipping: { fee_sen: 800, zone_code: "MY-10", version: 1 }, total_sen: 11800, can_place_order: true });

function readyQuote(quote: DestinationQuote, pending = false): SummaryQuote {
  return { status: "ready", quote, pending };
}

const STEP_1: CheckoutForm = { ...INITIAL_FORM, phoneRegion: "MY" };

function step3(overrides: Partial<CheckoutForm> = {}): CheckoutForm {
  return {
    ...INITIAL_FORM,
    step: 3,
    phoneRegion: "GB",
    phoneInput: "7700 900123",
    guestNotice: "checkout.guest_other_country",
    recipient: { name: "Sam Taylor", country: "GB", state: "", region: "Greater London", address: "1 Example Street", postcode: "AB1 2CD" },
    ...overrides,
  };
}

const SELANGOR_FORM = step3({
  phoneRegion: "MY",
  phoneInput: "12-345 6789",
  guestNotice: "checkout.guest_sms_off",
  recipient: { name: "Aina Rahman", country: "MY", state: "MY-10", region: "", address: "12 Jalan Contoh 3", postcode: "47000" },
});

const noActions: CheckoutActions = {
  onPhoneRegion: () => undefined,
  onPhoneInput: () => undefined,
  onContinue: () => undefined,
  onChangePhone: () => undefined,
  onRecipient: () => undefined,
  onPlaceOrder: () => undefined,
};

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  lines?: readonly CartLine[];
  reference?: Reference;
  quote?: SummaryQuote;
  form?: CheckoutForm;
  language?: Language;
}

function render({ lines = CART, reference = referenceOf(false), quote = readyQuote(NO_COUNTRY), form = STEP_1, language = "en" }: RenderOptions = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/checkout">
        <CheckoutView lines={lines} reference={reference} quote={quote} form={form} actions={noActions} />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

function count(html: string, value: string): number {
  return html.split(value).length - 1;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

// 字典某一条在页面里的样子（HTML 转义后）。
function copy(key: CopyKey, language: Language = "en"): string {
  return escapeHtml(COPY[key][language]);
}

// 页面上访客能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/g)].map((m) => unescapeHtml(m[1] ?? ""));
  const textNodes = html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
  return [...textNodes, ...attributes].filter((value) => value !== "");
}

// 字典里某一条（变量换成任意值）的匹配式。
function copyPattern(template: string): RegExp {
  const parts = template.split(/\{\w+\}/).map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^${parts.join(".+")}$`);
}

describe("step 1: the mobile number", () => {
  // UX 0.7 P05「手机号旁 [checkout.phone_notice]…两处告知都常显，不设勾选框」与 SHOP-TASK-025 验收第 4 条「开关开启时常显 checkout.phone_notice…旁都有链到 P14 的 checkout.form_notice_link，无勾选框」。
  it.each(LANGUAGES)("shows checkout.phone_notice with SMS on in %s", (language) => {
    const html = render({ reference: referenceOf(true), language });
    expect(html).toContain(`<div class="acs-hint acs-hint--block"><span>${copy("checkout.phone_notice", language)}</span><a href="/privacy">${copy("checkout.form_notice_link", language)}</a></div>`);
    expect(html).not.toContain(copy("checkout.phone_notice_sms_off", language));
    expect(html).not.toContain(`type="checkbox"`);
  });

  // UX 0.7 P05「短信验证开关关闭时：第 1 步以 [checkout.phone_notice_sms_off] 代替 [checkout.phone_notice]」与 SHOP-TASK-025 验收第 4 条「关闭时常显 checkout.phone_notice_sms_off」。
  it.each(LANGUAGES)("shows checkout.phone_notice_sms_off with SMS off in %s", (language) => {
    const html = render({ reference: referenceOf(false), language });
    expect(html).toContain(`<div class="acs-hint acs-hint--block"><span>${copy("checkout.phone_notice_sms_off", language)}</span><a href="/privacy">${copy("checkout.form_notice_link", language)}</a></div>`);
    expect(html).not.toContain(copy("checkout.phone_notice", language));
    expect(html).not.toContain(`type="checkbox"`);
  });

  // UX P05 第 1 步线框「[checkout.phone_step_title]…[auth.phone]…[checkout.phone_step_hint]…( [checkout.phone_continue] )」与 SHOP-TASK-025 验收第 4 条
  // 「显示 checkout.phone_step_hint 与 checkout.phone_continue；checkout.login_password 按路由规则在登录页实现前不渲染」。
  it.each(LANGUAGES)("shows the step title, label, hint and continue button but no login link in %s", (language) => {
    const html = render({ form: { ...STEP_1, phoneInput: "12-345 6789" }, language });
    expect(html).toContain(`<h2 class="acs-display-s">${copy("checkout.phone_step_title", language)}</h2>`);
    expect(html).toMatch(new RegExp(`<label class="acs-field__label" id="[^"]+" for="[^"]+">${copy("auth.phone", language)}</label>`));
    expect(html).toContain(`<span class="acs-field__hint">${copy("checkout.phone_step_hint", language)}</span>`);
    expect(html).toContain(`<button class="acs-btn acs-btn--primary site-checkout__continue" type="submit">${copy("checkout.phone_continue", language)}</button>`);
    expect(html).not.toContain(copy("checkout.login_password", language));
    expect(html).not.toContain(`href="/login"`);
  });

  // SHOP-TASK-025 验收第 4 条「国家码下拉列出全部地区（显示本地化国家名与 +呼叫码，按名称排序），默认 MY」与 UX P05「国家码下拉列出所有国家，默认 +60」。
  it("lists every region with its calling code and selects Malaysia", () => {
    const select = part(render(), /<div class="acs-phone site-checkout__phone"><select[\s\S]*?<\/select>/);
    expect([...select.matchAll(/<option value="([A-Z]{2})"/g)].map((m) => m[1])).toEqual(["MY", "SG", "GB"]);
    expect(select).toContain(`<option value="MY" selected="">Malaysia +60</option>`);
    expect(select).toContain(`<option value="SG">Singapore +65</option>`);
    expect(select).toContain(`<option value="GB">United Kingdom +44</option>`);
  });

  // SHOP-TASK-025 验收第 3 条「国家名称用浏览器的 Intl.DisplayNames 按当前界面语言取得」：下拉里的名称随界面语言变化（中文、马来文与英文不同）。
  it("names the regions in the interface language", () => {
    const options = (language: Language) => [...render({ language }).matchAll(/<option value="MY"[^>]*>([^<]*)<\/option>/g)].map((m) => m[1]);
    expect(options("zh")).toEqual([`${countryNames("zh")("MY")} +60`]);
    expect(options("ms")).toEqual([`${countryNames("ms")("MY")} +60`]);
    expect(countryNames("zh")("MY")).not.toBe("Malaysia");
  });

  // SHOP-TASK-025 验收第 3 条「取不到时显示两位代码（Kelvin 2026-10-01 决定）」：浏览器没有 Intl.DisplayNames 时下拉与收货国家都显示代码。
  it("shows the two-letter code when the browser has no country names", () => {
    vi.stubGlobal("Intl", { ...Intl, DisplayNames: undefined });
    const first = render();
    expect(first).toContain(`<option value="MY" selected="">MY +60</option>`);
    const third = render({ form: step3(), quote: readyQuote(TO_BRITAIN) });
    expect(third).toContain(`<option value="GB" selected="">GB</option>`);
  });

  // SHOP-TASK-025 验收第 3 条「读取完成前第 1 步不可提交，失败时显示 common.error_retry」：读取中与失败时继续按钮与下拉都禁用，不显示任一告知；失败时显示提示。
  it.each(LANGUAGES)("cannot continue before the settings and regions are read in %s", (language) => {
    const loading = render({ reference: { status: "loading" }, language });
    const failed = render({ reference: { status: "error" }, language });
    for (const html of [loading, failed]) {
      expect(html).toContain(`type="submit" disabled="">${copy("checkout.phone_continue", language)}</button>`);
      expect(html).toMatch(/<select class="acs-select" aria-labelledby="[^"]+" disabled="">/);
      expect(html).not.toContain(copy("checkout.phone_notice", language));
      expect(html).not.toContain(copy("checkout.phone_notice_sms_off", language));
    }
    expect(loading).not.toContain(copy("common.error_retry", language));
    expect(failed).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.error_retry", language)}</p>`);
  });

  // SHOP-TASK-025 验收第 4 条「格式是否成立由服务端在下单时判定，浏览器只要求非空」：空白号码不能继续，任何非空号码（含明显不像号码的）都可以。
  it("only requires a non-empty number", () => {
    const ready = referenceOf(false);
    expect(canContinue({ ...STEP_1, phoneInput: "" }, ready)).toBe(false);
    expect(canContinue({ ...STEP_1, phoneInput: "   " }, ready)).toBe(false);
    expect(canContinue({ ...STEP_1, phoneInput: "12" }, ready)).toBe(true);
    expect(canContinue({ ...STEP_1, phoneInput: "abc" }, ready)).toBe(true);
    expect(canContinue({ ...STEP_1, phoneInput: "12" }, { status: "loading" })).toBe(false);
    expect(canContinue({ ...STEP_1, phoneInput: "12" }, { status: "error" })).toBe(false);
    expect(continueFromPhone({ ...STEP_1, phoneInput: " " }, false)).toEqual({ ...STEP_1, phoneInput: " " });
  });
});

describe("deciding after continue", () => {
  // UX 0.7 P05「短信验证开关关闭时…显示 [checkout.guest_sms_off]，以游客表单进入第 3 步，马新号码也一样」与 SHOP-TASK-025 验收第 5 条。
  it.each([
    ["MY", "12-345 6789"],
    ["GB", "7700 900123"],
    ["GB", "+65 8123 4567"],
  ])("continues to step 3 as a guest with SMS off for %s %j", (region, input) => {
    const next = continueFromPhone({ ...STEP_1, phoneRegion: region, phoneInput: input }, false);
    expect(next.step).toBe(3);
    expect(next.guestNotice).toBe("checkout.guest_sms_off");
    expect(next.phoneError).toBeNull();
  });

  // SHOP-TASK-025 验收第 5 条「开关开启时，号码属于马新…在短信验证组件实现前停在第 1 步并显示 common.service_unavailable（Kelvin 2026-10-01 批准的过渡规则，docs/HANDOFF.md 0.25）」。
  it.each([
    ["MY", "12-345 6789"],
    ["SG", "8123 4567"],
    ["GB", "+60 12-345 6789"],
  ])("stays on step 1 with SMS on for %s %j", (region, input) => {
    const next = continueFromPhone({ ...STEP_1, phoneRegion: region, phoneInput: input }, true);
    expect(next.step).toBe(1);
    expect(next.phoneError).toBe("unavailable");
  });

  // 同一条：停在第 1 步时显示 common.service_unavailable 提示条，号码仍在输入框里。
  it.each(LANGUAGES)("shows common.service_unavailable on step 1 in %s", (language) => {
    const html = render({ reference: referenceOf(true), form: { ...STEP_1, phoneInput: "12-345 6789", phoneError: "unavailable" }, language });
    expect(html).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.service_unavailable", language)}</p>`);
    expect(html).toContain(`value="12-345 6789"`);
    expect(html).not.toContain(copy("checkout.recipient_title", language));
  });

  // SHOP-TASK-025 验收第 5 条「其他号码以游客继续并在第 3 步顶部显示 checkout.guest_other_country」与 UX P05「以 + 开头输入时以输入为准」：下拉仍是 MY 时输入 +44 也以游客继续。
  it.each([
    ["GB", "7700 900123"],
    ["MY", "+44 7700 900123"],
  ])("continues to step 3 as another country's number with SMS on for %s %j", (region, input) => {
    const next = continueFromPhone({ ...STEP_1, phoneRegion: region, phoneInput: input }, true);
    expect(next.step).toBe(3);
    expect(next.guestNotice).toBe("checkout.guest_other_country");
  });

  // UX P05 第 3 步线框「游客：[checkout.guest_other_country]…[checkout.guest_notice]」、0.7「显示 [checkout.guest_sms_off]」与 SHOP-TASK-025 验收第 5 条「第 3 步另显示 checkout.guest_notice」：
  // 两种游客提示各在第 3 步顶部（收货资料标题之前）显示其一，并都有 checkout.guest_notice。
  it.each(LANGUAGES)("shows the guest notices at the top of step 3 in %s", (language) => {
    for (const [shown, hidden] of [
      ["checkout.guest_other_country", "checkout.guest_sms_off"],
      ["checkout.guest_sms_off", "checkout.guest_other_country"],
    ] as const) {
      const html = render({ form: step3({ guestNotice: shown }), quote: readyQuote(TO_BRITAIN), language });
      expect(html).toMatch(new RegExp(`<div class="acs-alert acs-alert--info" role="status"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg><span>${copy(shown, language).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}</span></div>`));
      expect(html).not.toContain(copy(hidden, language));
      expect(html).toContain(`<p class="acs-body-s">${copy("checkout.guest_notice", language)}</p>`);
      expect(html.indexOf(copy(shown, language))).toBeLessThan(html.indexOf(copy("checkout.recipient_title", language)));
      expect(html.indexOf(copy("checkout.guest_notice", language))).toBeLessThan(html.indexOf(copy("checkout.recipient_title", language)));
    }
  });
});

describe("step 3: shipping details", () => {
  // SHOP-TASK-025 验收第 6 条「显示第 1 步的号码（只读）与 checkout.phone_change（回第 1 步）、checkout.phone_lookup_hint」与 UX P05「游客：[P3] <第 1 步号码>（只读）」。
  it.each(LANGUAGES)("shows the step 1 number read-only with change and the lookup hint in %s", (language) => {
    // react-dom/server 把 input 的 value 写在其他属性之后，所以逐个属性比较，不依赖属性顺序。
    const phoneField = (html: string) => {
      const found = new RegExp(`<label class="acs-field__label" for="([^"]+)">${copy("checkout.phone", language)}</label><div class="site-checkout__readonly"><input ([^>]*)/>`).exec(html);
      if (found === null) {
        throw new Error("phone field not found");
      }
      return { labelFor: found[1] ?? "", input: found[2] ?? "" };
    };
    const html = render({ form: step3(), quote: readyQuote(TO_BRITAIN), language });
    const { labelFor, input } = phoneField(html);
    expect(input).toContain(`class="acs-input"`);
    expect(input).toContain(`id="${labelFor}"`);
    expect(input).toContain(`type="tel"`);
    // react-dom/server 输出 readOnly=""；HTML 属性名不分大小写，按语义匹配。
    expect(input).toMatch(/(^|\s)readonly=""/i);
    expect(input).toContain(`value="+44 7700 900123"`);
    expect(html).toContain(`<button class="acs-btn acs-btn--quiet" type="button">${copy("checkout.phone_change", language)}</button>`);
    const hintId = /aria-describedby="([^"]+)"/.exec(input)?.[1] ?? "";
    expect(hintId).not.toBe("");
    expect(html).toContain(`<span class="acs-field__hint" id="${hintId}">${copy("checkout.phone_lookup_hint", language)}</span>`);
    const typed = phoneField(render({ form: step3({ phoneRegion: "MY", phoneInput: "+44 7700 900123" }), quote: readyQuote(TO_BRITAIN), language }));
    expect(typed.input).toMatch(/(^|\s)readonly=""/i);
    expect(typed.input).toContain(`value="+44 7700 900123"`);
  });

  // UX P05「游客的收货电话就是第 1 步的号码…要改须点 [checkout.phone_change] 回到第 1 步重新判定」与 SHOP-TASK-025 验收第 2 条「刷新后需重填可以接受」的反面——不刷新时不丢：
  // 回第 1 步只换步骤，号码与已填的收货资料都保留，再继续回到第 3 步时仍在。
  it("goes back to step 1 keeping what was entered", () => {
    const form = step3({ notice: "error" });
    const back = backToPhone(form);
    expect(back.step).toBe(1);
    expect(back.phoneInput).toBe(form.phoneInput);
    expect(back.recipient).toEqual(form.recipient);
    expect(back.notice).toBeNull();
    expect(continueFromPhone(back, false).recipient).toEqual(form.recipient);
  });

  // SHOP-TASK-025 验收第 6 条「收货表单旁常显 checkout.form_notice 与 checkout.form_notice_link，无勾选框」与 UX P05「收货表单旁 [checkout.form_notice]…常显，不设勾选框」。
  it.each(LANGUAGES)("shows checkout.form_notice next to the form without a checkbox in %s", (language) => {
    const html = render({ form: step3(), quote: readyQuote(TO_BRITAIN), language });
    expect(html).toContain(
      `<h2 class="acs-display-s">${copy("checkout.recipient_title", language)}</h2><div class="acs-hint acs-hint--block"><span>${copy("checkout.form_notice", language)}</span><a href="/privacy">${copy("checkout.form_notice_link", language)}</a></div>`,
    );
    expect(html).not.toContain(`type="checkbox"`);
  });

  // SHOP-TASK-025 验收第 6 条「字段为 checkout.name、checkout.country（全部地区，初始未选）、…其他国家的 checkout.region 自由文本（可空）、checkout.address、checkout.postcode；不收集任何其他资料」：
  // 其他国家时恰好这五个可填字段加只读电话，按线框顺序；没有多行文本框或别的输入。
  it.each(LANGUAGES)("asks only for the listed fields for another country in %s", (language) => {
    const html = render({ form: step3(), quote: readyQuote(TO_BRITAIN), language });
    const labels = [...html.matchAll(/<label class="acs-field__label"[^>]*>([^<]*)<\/label>/g)].map((m) => m[1]);
    expect(labels).toEqual(["checkout.name", "checkout.country", "checkout.region", "checkout.address", "checkout.postcode", "checkout.phone"].map((key) => copy(key as CopyKey, language)));
    expect(count(html, "<input")).toBe(5);
    expect(count(html, "<select")).toBe(1);
    expect(html).not.toContain("<textarea");
    expect(html).not.toMatch(/type="(email|password|checkbox|radio|file)"/);
    expect(html).toMatch(/autocomplete="address-level1" value="Greater London"/i);
    expect(html).not.toMatch(/autocomplete="address-level1"[^>]*required/i);
  });

  // 同一条「选马来西亚时的 checkout.state_my 下拉」与 SHOP-TASK-025 验收第 3 条「州属下拉显示接口返回的马来文官方名称，三种语言相同」：马来西亚时地区输入框换成州属下拉，名称三种语言相同。
  it.each(LANGUAGES)("asks for the Malaysian state instead of a region in %s", (language) => {
    const html = render({ form: SELANGOR_FORM, quote: readyQuote(TO_SELANGOR), language });
    const labels = [...html.matchAll(/<label class="acs-field__label"[^>]*>([^<]*)<\/label>/g)].map((m) => m[1]);
    expect(labels).toEqual(["checkout.name", "checkout.country", "checkout.state_my", "checkout.address", "checkout.postcode", "checkout.phone"].map((key) => copy(key as CopyKey, language)));
    expect(count(html, "<select")).toBe(2);
    expect(html).toContain(`<option value=""></option><option value="MY-10" selected="">Selangor</option><option value="MY-14">Wilayah Persekutuan Kuala Lumpur</option></select>`);
    expect(html).not.toContain(copy("checkout.region", language));
  });

  // 同一条「checkout.country（全部地区，初始未选）」：收货国家下拉有全部地区（按本地化名称排序），初始选中的是空选项。
  it("starts with no country selected and lists every region", () => {
    const html = render({ form: step3({ recipient: { ...step3().recipient, country: "" } }), quote: readyQuote(NO_COUNTRY) });
    const select = part(html, /<select class="acs-select" id="[^"]+" autocomplete="country"[\s\S]*?<\/select>/i);
    expect(select).toContain(`<option value="" selected=""></option><option value="MY">Malaysia</option><option value="SG">Singapore</option><option value="GB">United Kingdom</option></select>`);
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-020 的请求模型（姓名、地址、邮编必填，马来西亚必填州属）——缺必填项时不能下单，地区可空。
  it("requires the name, country, state for Malaysia, address and postcode", () => {
    const complete = step3().recipient;
    expect(recipientComplete(complete)).toBe(true);
    expect(recipientComplete({ ...complete, region: "" })).toBe(true);
    for (const missing of ["name", "country", "address", "postcode"] as const) {
      expect(recipientComplete({ ...complete, [missing]: " ".repeat(missing === "country" ? 0 : 2) }), missing).toBe(false);
    }
    expect(recipientComplete(SELANGOR_FORM.recipient)).toBe(true);
    expect(recipientComplete({ ...SELANGOR_FORM.recipient, state: "" })).toBe(false);
  });

  // UX P05 线框「游客：[checkout.coupon_members_only] [checkout.points_guest]」与视觉稿 P05-desktop-3-guest：游客只有这两句说明，没有优惠券或积分输入框。
  it.each(LANGUAGES)("explains coupons and points to guests without inputs in %s", (language) => {
    const html = render({ form: step3(), quote: readyQuote(TO_BRITAIN), language });
    expect(html).toContain(`<div class="site-checkout__members"><p class="acs-body-s">${copy("checkout.coupon_members_only", language)}</p><p class="acs-body-s acs-muted">${copy("checkout.points_guest", language)}</p></div>`);
  });
});

describe("order summary", () => {
  // SHOP-TASK-025 验收第 6 条「未选国家时显示 cart.shipping_later 与小计」与 UX P05「未选国家时不显示 [M7]、[checkout.fx_note] 或 [checkout.fx_none]」：每行与小计，没有运费、合计或参考外币。
  it.each(LANGUAGES)("shows the lines, subtotal and shipping note before a country is chosen in %s", (language) => {
    const aside = part(render({ language }), /<aside[\s\S]*?<\/aside>/);
    expect(aside).toContain(`<h2 class="acs-display-s acs-summary__title">${copy("checkout.summary_title", language)}</h2>`);
    expect(aside).toMatch(/<span>Crew Neck Tee<\/span><span class="site-checkout__option"><svg[^>]*aria-hidden="true">[\s\S]*?<\/svg><span>Black<\/span><\/span><span class="site-checkout__option"><svg[\s\S]*?<\/svg><span>M<\/span><\/span><span class="acs-num">× 2<\/span><\/span><span class="acs-num">RM 78.00<\/span>/);
    expect(aside).toContain(`<span lang="en">Soy Wax Candle</span><span class="acs-num">× 1</span></span><span class="acs-num">RM 32.00</span>`);
    expect(aside).toContain(`<div class="acs-row site-checkout__subtotal"><span>${copy("cart.subtotal", language)}</span><span>RM 110.00</span></div>`);
    expect(aside).toContain(`<p class="acs-body-s acs-muted">${copy("cart.shipping_later", language)}</p>`);
    for (const key of ["checkout.summary_shipping", "checkout.summary_total", "checkout.fx_note", "checkout.fx_none"] as const) {
      expect(aside).not.toContain(copy(key, language));
    }
    expect(aside).not.toContain("≈");
  });

  // SHOP-TASK-025 验收第 6 条「选定后显示 checkout.summary_shipping、checkout.summary_total 与参考外币（checkout.fx_note…）」与 UX P05 M5–M7：运费、合计与 common.fx_reference，没有 cart.shipping_later。
  it.each(LANGUAGES)("shows shipping, total and the reference currency after a country is chosen in %s", (language) => {
    const aside = part(render({ form: step3(), quote: readyQuote(TO_BRITAIN), language }), /<aside[\s\S]*?<\/aside>/);
    expect(aside).toContain(`<div class="acs-row"><span>${copy("checkout.summary_shipping", language)}</span><span>RM 25.00</span></div>`);
    expect(aside).toContain(`<div class="acs-row acs-row--total"><span>${copy("checkout.summary_total", language)}</span><span>RM 135.00</span></div>`);
    expect(aside).toContain(`<div class="acs-fx">${escapeHtml(COPY["common.fx_reference"][language].replace("{currency}", "GBP").replace("{amount}", "22.95"))}</div>`);
    expect(aside).toContain(`<p class="acs-caption">${copy("checkout.fx_note", language)}</p>`);
    expect(aside).not.toContain(copy("checkout.fx_none", language));
    expect(aside).not.toContain(copy("cart.shipping_later", language));
  });

  // 同一条「或该国无汇率时 checkout.fx_none」与 UX P05 M7「该国无汇率时只显示 MYR」。
  it.each(LANGUAGES)("shows checkout.fx_none when the country has no demo rate in %s", (language) => {
    const aside = part(render({ form: SELANGOR_FORM, quote: readyQuote(TO_SELANGOR), language }), /<aside[\s\S]*?<\/aside>/);
    expect(aside).toContain(`<p class="acs-caption">${copy("checkout.fx_none", language)}</p>`);
    expect(aside).not.toContain("≈");
    expect(aside).not.toContain(copy("checkout.fx_note", language));
  });

  // SHOP-TASK-025 验收第 6 条「游客不显示 checkout.summary_coupon 与 checkout.summary_points」与 UX 0.4「游客结账…不显示…两行，桌面与手机相同」：选国家前后、各语言都没有这两行。
  it.each(LANGUAGES)("never shows the coupon and points rows to guests in %s", (language) => {
    for (const state of [{ language }, { form: step3(), quote: readyQuote(TO_BRITAIN), language }, { form: SELANGOR_FORM, quote: readyQuote(TO_SELANGOR), language }]) {
      const html = render(state);
      expect(html).not.toContain(COUPON_ROW[language]);
      expect(html).not.toContain(POINTS_ROW[language]);
    }
  });

  // UX P05 M1–M6「按服务端返回」与 DESIGN「计价、优惠、积分与库存」第 1 条（金额由服务端计算）：行小计、小计、运费与合计彼此不符时照样显示接口给的数，不在浏览器相加。
  it("shows the amounts exactly as the quote returns them", () => {
    const odd = quoteOf({ subtotal_sen: 12345, shipping: { fee_sen: 100, zone_code: "GB", version: 1 }, total_sen: 99999, can_place_order: true });
    const html = render({ form: step3(), quote: readyQuote(odd) });
    expect(html).toContain(">RM 123.45<");
    expect(html).toContain(">RM 999.99<");
    expect(html).not.toContain("RM 110.00");
    expect(html).not.toContain("RM 124.45");
  });

  // SHOP-TASK-025 验收第 6 条「手机版顶部折叠摘要与底部固定栏在选国家前显示小计、选定后显示合计」与 UX 0.3「手机版结账顶部折叠摘要（及底部固定栏）在选定收货国家前显示商品小计而不是合计」。
  it.each(LANGUAGES)("shows the subtotal, then the total, in the phone summary and bar in %s", (language) => {
    const fold = (html: string) => part(html, /<details class="acs-summary site-phone-only site-checkout__fold"><summary class="site-checkout__fold-head">[\s\S]*?<\/summary>/);
    const bar = (html: string) => part(html, /<div class="acs site-phone-only site-checkout__bar">[\s\S]*?<button/);
    const before = render({ form: step3({ recipient: { ...step3().recipient, country: "" } }), quote: readyQuote(NO_COUNTRY), language });
    const after = render({ form: step3(), quote: readyQuote(TO_BRITAIN), language });
    expect(fold(before)).toContain(`<span>${copy("checkout.summary_title", language)}</span><span class="acs-num site-checkout__fold-amount"><span>${copy("cart.subtotal", language)}</span><span class="acs-price">RM 110.00</span></span>`);
    expect(bar(before)).toContain(`<span class="acs-row site-checkout__bar-amount"><span>${copy("cart.subtotal", language)}</span><span class="acs-price">RM 110.00</span></span>`);
    expect(fold(after)).toContain(`<span class="acs-num site-checkout__fold-amount"><span>${copy("checkout.summary_total", language)}</span><span class="acs-price">RM 135.00</span></span>`);
    expect(bar(after)).toContain(`<span class="acs-row site-checkout__bar-amount"><span>${copy("checkout.summary_total", language)}</span><span class="acs-price">RM 135.00</span></span>`);
    expect(fold(before)).not.toContain(copy("checkout.summary_total", language));
    expect(bar(before)).not.toContain(copy("checkout.summary_total", language));
  });

  // SHOP-TASK-025 验收第 6 条「★ checkout.demo_hint 常显」：第 1 步、第 3 步、读取中、计价失败与空购物车都在标题旁显示。
  it.each(LANGUAGES)("always shows the demo hint in %s", (language) => {
    const states: RenderOptions[] = [
      { language },
      { form: step3(), quote: readyQuote(TO_BRITAIN), language },
      { reference: { status: "loading" }, quote: { status: "loading" }, language },
      { quote: { status: "error" }, language },
      { lines: [], language },
    ];
    for (const state of states) {
      const head = part(render(state), /<div class="site-checkout__head">[\s\S]*?<\/div>/);
      expect(head).toContain(`<h1 class="acs-display-l">${copy("checkout.title", language)}</h1><p class="acs-hint">`);
      expect(head).toContain(copy("checkout.demo_hint", language));
    }
  });

  // SHOP-TASK-025 验收第 2 条「购物车为空时显示 cart.empty 与 cart.continue」：没有步骤内容、摘要或下单按钮。
  it.each(LANGUAGES)("shows cart.empty and cart.continue for an empty cart in %s", (language) => {
    const html = render({ lines: [], form: step3(), language });
    expect(html).toContain(`<p class="acs-display-s">${copy("cart.empty", language)}</p>`);
    expect(html).toContain(`<a class="acs-btn acs-btn--secondary" href="/products">${copy("cart.continue", language)}</a>`);
    expect(html).not.toContain(copy("checkout.place_order", language));
    expect(html).not.toContain(copy("checkout.summary_title", language));
    expect(html).not.toContain("<form");
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-018 验收「计价请求失败时显示 common.error_retry」在结账页的延续——摘要处显示提示，不能下单。
  it("shows common.error_retry when the quote fails", () => {
    const html = render({ form: step3(), quote: { status: "error" } });
    expect(part(html, /<aside[\s\S]*?<\/aside>/)).toContain(copy("common.error_retry"));
    expect(canPlaceOrder(step3(), { status: "error" }, CART)).toBe(false);
  });
});

describe("changed lines", () => {
  // SHOP-TASK-025 验收第 6 条「有任何非正常行时显示 cart.item_changed、禁用下单并可回购物车」：不可购买、超出限购、库存不足各自都会触发；链接回 /cart。
  it.each(LANGUAGES)("shows cart.item_changed, disables placing and links back to the cart in %s", (language) => {
    const changed: QuoteLine[] = [
      { sku: "gone-sku", quantity: 1, status: "unavailable" },
      priced("tee-black-m", { status: "over_limit", quantity: 2 }),
      priced("tee-black-m", { status: "insufficient_stock", quantity: 2, available_stock: 0 }),
    ];
    for (const row of changed) {
      const rows = [row, ROWS[1] ?? row];
      const lines = rows.map((line) => ({ sku: line.sku, slug: "x", quantity: line.quantity }));
      const quote = readyQuote(quoteOf({ ...TO_BRITAIN, lines: rows, can_place_order: true }));
      const html = render({ lines, form: step3(), quote, language });
      expect(html).toContain(`<div class="acs-alert acs-alert--danger" role="alert"><svg`);
      expect(html).toContain(`<span>${copy("cart.item_changed", language)}</span></div>`);
      expect(html).toMatch(new RegExp(`<a class="acs-btn acs-btn--secondary site-checkout__back" href="/cart">${escapeHtml(COPY["common.nav_cart"][language]).replace(/[()]/g, "\\$&").replace("{count}", "\\d+")}</a>`));
      expect(count(html, `disabled="">${copy("checkout.place_order", language)}</button>`)).toBe(2);
      expect(canPlaceOrder(step3(), quote, lines)).toBe(false);
    }
  });

  // 同一条：全部正常时没有提示，可以下单（桌面摘要旁与手机底部栏各一个按钮）。
  it("lets a complete guest place the order when every line is fine", () => {
    const html = render({ form: step3(), quote: readyQuote(TO_BRITAIN) });
    expect(html).not.toContain(copy("cart.item_changed"));
    expect(count(html, `<button class="acs-btn acs-btn--primary acs-btn--lg acs-btn--block" type="button">${copy("checkout.place_order")}</button>`)).toBe(2);
  });
});

describe("placing the order", () => {
  // SHOP-TASK-025 验收第 8 条「checkout.place_order 旁显示 ◆ checkout.place_order_hint」：桌面与手机的按钮之后各有一条菱形标记的提示。
  it.each(LANGUAGES)("shows the order hint next to the button in %s", (language) => {
    const html = render({ form: step3(), quote: readyQuote(TO_BRITAIN), language });
    const hint = `</button><p class="acs-hint"><svg class="acs-hint__mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l9 9-9 9-9-9z"></path></svg><span>${copy("checkout.place_order_hint", language)}</span></p>`;
    expect(count(html, hint)).toBe(2);
  });

  // UX P05 第 1 步线框：第 1 步没有下单按钮与 ◆ 提示（摘要只有各行、小计与 cart.shipping_later）。
  it("has no order button on step 1", () => {
    const html = render({ quote: readyQuote(TO_BRITAIN) });
    expect(html).not.toContain(copy("checkout.place_order"));
    expect(html).not.toContain(copy("checkout.place_order_hint"));
  });

  // SHOP-TASK-025 验收第 8 条「提交期间按钮禁用并显示 checkout.submitting」与 UX「全局框架」「按钮提交期间禁用」：按钮文字换成 checkout.submitting，字段与 checkout.phone_change 也禁用。
  it.each(LANGUAGES)("disables the button and shows checkout.submitting while submitting in %s", (language) => {
    const form = step3({ submitting: true });
    const html = render({ form, quote: readyQuote(TO_BRITAIN), language });
    expect(count(html, `disabled="" aria-busy="true">${copy("checkout.submitting", language)}</button>`)).toBe(2);
    expect(html).not.toContain(`>${copy("checkout.place_order", language)}</button>`);
    expect(html).toContain(`type="button" disabled="">${copy("checkout.phone_change", language)}</button>`);
    expect(canPlaceOrder(form, readyQuote(TO_BRITAIN), CART)).toBe(false);
  });

  // SHOP-TASK-025 验收第 9 条「网络中断时显示 common.network_check」与视觉稿 P05-states：重试期间显示提示条，按钮仍禁用。
  it.each(LANGUAGES)("shows common.network_check while retrying in %s", (language) => {
    const html = render({ form: step3({ submitting: true, checking: true }), quote: readyQuote(TO_BRITAIN), language });
    expect(count(html, `<div class="acs-alert acs-alert--info" role="status"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 11v6M12 7.5v.5"></path></svg><span>${copy("common.network_check", language)}</span></div>`)).toBe(2);
  });

  // 派生实现约束（实现选择）：守住 UX P05 M5–M6「选定收货国家、服务端算出示例运费后才显示合计」——当前请求的计价未返回、未选定目的地或接口说不可下单时都不能下单。
  it("only places an order on a current quote for the chosen destination", () => {
    const form = step3();
    expect(canPlaceOrder(form, readyQuote(TO_BRITAIN), CART)).toBe(true);
    expect(canPlaceOrder(form, readyQuote(TO_BRITAIN, true), CART)).toBe(false);
    expect(canPlaceOrder(form, { status: "loading" }, CART)).toBe(false);
    expect(canPlaceOrder(form, readyQuote(NO_COUNTRY), CART)).toBe(false);
    expect(canPlaceOrder(form, readyQuote({ ...TO_BRITAIN, can_place_order: false }), CART)).toBe(false);
    expect(canPlaceOrder({ ...form, step: 1 }, readyQuote(TO_BRITAIN), CART)).toBe(false);
    expect(canPlaceOrder({ ...form, recipient: { ...form.recipient, postcode: "" } }, readyQuote(TO_BRITAIN), CART)).toBe(false);
    expect(canPlaceOrder(form, readyQuote(TO_BRITAIN), [])).toBe(false);
  });

  // SHOP-TASK-025 验收第 8 条「请求体只含购物车行（SKU 与件数）、第 1 步号码原文与所选地区代码、收货资料、国家、州属或地区…不带任何金额」：
  // 由页面表单得到的请求体字段齐全；马来西亚只带州属（之前在别国填过的地区不带），其他国家只带地区；号码是第 1 步原文。
  it("builds the order body from the form", () => {
    const britain = guestOrderBody(CART, { input: step3().phoneInput, region: step3().phoneRegion }, guestRecipient(step3().recipient));
    expect(britain).toEqual({
      lines: [
        { sku: "tee-black-m", quantity: 2 },
        { sku: "candle", quantity: 1 },
      ],
      phone: "7700 900123",
      phone_region: "GB",
      name: "Sam Taylor",
      address: "1 Example Street",
      postal_code: "AB1 2CD",
      country_code: "GB",
      region: "Greater London",
    });
    const malaysia = guestOrderBody(CART, { input: "12-345 6789", region: "MY" }, guestRecipient({ ...SELANGOR_FORM.recipient, region: "typed before" }));
    expect(malaysia).toMatchObject({ phone: "12-345 6789", phone_region: "MY", country_code: "MY", state_code: "MY-10" });
    expect(Object.keys(malaysia)).not.toContain("region");
    for (const body of [britain, malaysia]) {
      expect(JSON.stringify(body)).not.toMatch(/price|_sen|total|shipping|fx|amount|coupon|points/);
    }
  });
});

describe("answers to the order", () => {
  const form = step3({ submitting: true, checking: true, attempt: { key: "old-key-000000000000", request: "{}" } });

  function expectKept(next: CheckoutForm) {
    expect(next.recipient).toEqual(form.recipient);
    expect(next.phoneInput).toBe(form.phoneInput);
    expect(next.phoneRegion).toBe(form.phoneRegion);
    expect(next.submitting).toBe(false);
    expect(next.checking).toBe(false);
  }

  // SHOP-TASK-025 验收第 9 条「201 或 200 时清空本浏览器购物车并以站内导航进入 /pay（不带订单号）」：只有成功清空购物车并导航，目标恰好是 /pay。
  it("empties the cart and goes to /pay after the order is placed", () => {
    expect(replyEffects("placed")).toEqual({ placed: true, smsEnabled: false, requote: false });
    for (const reply of ["not_placeable", "sms_required", "phone_invalid", "conflict", "unavailable", "failed"] as const) {
      expect(replyEffects(reply).placed, reply).toBe(false);
    }
    const data: Record<string, string> = { [CART_STORAGE_KEY]: JSON.stringify(CART) };
    const storage: CartStorage = { getItem: (key) => data[key] ?? null, setItem: (key, value) => void (data[key] = value) };
    const visited: RoutePath[] = [];
    completeOrder(storage, (path) => visited.push(path));
    expect(readCart(storage)).toEqual([]);
    expect(visited).toEqual(["/pay"]);
  });

  // SHOP-TASK-025 验收第 9 条「409 order_not_placeable 时重新计价并显示 cart.item_changed」：表单保留，标记重新计价，页面显示 cart.item_changed。
  it("re-quotes and shows cart.item_changed after order_not_placeable", () => {
    const next = afterReply(form, "not_placeable");
    expectKept(next);
    expect(next.step).toBe(3);
    expect(replyEffects("not_placeable").requote).toBe(true);
    const html = render({ form: next, quote: readyQuote(TO_BRITAIN) });
    expect(html).toContain(`<span>${copy("cart.item_changed")}</span>`);
    expect(checkoutQuoteKey("en", CART, quoteDestination("GB", ""), 1)).not.toBe(checkoutQuoteKey("en", CART, quoteDestination("GB", ""), 0));
  });

  // SHOP-TASK-025 验收第 9 条「403 sms_verification_required（开关在填表途中被打开）时回第 1 步按开启状态重新判定」与 UX 0.7「开关在填表途中被打开时下单被拒，页面回到第 1 步重新判定」：
  // 页面把开关当作已开启；马新号码回第 1 步显示 common.service_unavailable，其他号码回第 1 步无错误、继续后为 checkout.guest_other_country。
  it("returns to step 1 and decides again with SMS on after sms_verification_required", () => {
    expect(replyEffects("sms_required").smsEnabled).toBe(true);
    const malaysian = afterReply({ ...form, phoneRegion: "MY", phoneInput: "12-345 6789" }, "sms_required");
    expect(malaysian.step).toBe(1);
    expect(malaysian.phoneError).toBe("unavailable");
    expect(malaysian.recipient).toEqual(form.recipient);
    const other = afterReply(form, "sms_required");
    expectKept(other);
    expect(other.step).toBe(1);
    expect(other.phoneError).toBeNull();
    expect(continueFromPhone(other, true).guestNotice).toBe("checkout.guest_other_country");
    const html = render({ reference: referenceOf(true), form: malaysian });
    expect(html).toContain(copy("common.service_unavailable"));
    expect(html).toContain(copy("checkout.phone_notice"));
  });

  // SHOP-TASK-025 验收第 9 条「422 phone_invalid 时回第 1 步显示 checkout.phone_invalid」与视觉稿 P05-states：字段标为错误，提示在号码字段下，读屏关联到输入框。
  it.each(LANGUAGES)("returns to step 1 with checkout.phone_invalid after phone_invalid in %s", (language) => {
    const next = afterReply(form, "phone_invalid");
    expectKept(next);
    expect(next.step).toBe(1);
    const html = render({ form: next, language });
    expect(html).toContain(`<div class="acs-field acs-field--invalid">`);
    expect(html).toMatch(new RegExp(`aria-invalid="true" aria-describedby="([^"]+)"[\\s\\S]*<span class="acs-field__error" id="\\1"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg>${copy("checkout.phone_invalid", language)}</span>`));
  });

  // SHOP-TASK-025 验收第 9 条「409 idempotency_conflict 时换新幂等键并显示 common.error_retry」：丢掉原来的键，下一次提交（即使内容相同）生成新键。
  it("drops the key and shows common.error_retry after idempotency_conflict", () => {
    const next = afterReply(form, "conflict");
    expectKept(next);
    expect(next.attempt).toBeNull();
    expect(next.notice).toBe("error");
    const body = guestOrderBody(CART, { input: next.phoneInput, region: next.phoneRegion }, guestRecipient(next.recipient));
    const previous = orderAttemptFor(null, body, () => "first-key-0000000000");
    expect(orderAttemptFor(next.attempt, body, () => "second-key-000000000").key).toBe("second-key-000000000");
    expect(previous.key).toBe("first-key-0000000000");
    expect(count(render({ form: next, quote: readyQuote(TO_BRITAIN) }), `<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.error_retry")}</p>`)).toBe(2);
  });

  // SHOP-TASK-025 验收第 9 条「503 显示 common.service_unavailable；其他错误显示 common.error_retry，不清空已填内容」。
  it.each(LANGUAGES)("shows common.service_unavailable after 503 and common.error_retry otherwise in %s", (language) => {
    const unavailable = afterReply(form, "unavailable");
    const failed = afterReply(form, "failed");
    for (const next of [unavailable, failed]) {
      expectKept(next);
      expect(next.step).toBe(3);
      expect(next.attempt).toEqual(form.attempt);
    }
    const unavailableHtml = render({ form: unavailable, quote: readyQuote(TO_BRITAIN), language });
    const failedHtml = render({ form: failed, quote: readyQuote(TO_BRITAIN), language });
    expect(count(unavailableHtml, copy("common.service_unavailable", language))).toBe(2);
    expect(unavailableHtml).not.toContain(copy("common.error_retry", language));
    expect(count(failedHtml, copy("common.error_retry", language))).toBe(2);
    expect(failedHtml).toContain(`value="Sam Taylor"`);
    expect(failedHtml).toContain(`value="1 Example Street"`);
  });
});

describe("which quote is used", () => {
  // SHOP-TASK-025 验收第 6 条「用购物车各行 SKU 与件数（选了国家后加国家与州属）调用计价接口」：未选国家、或选了马来西亚而未选州属时不带目的地；马来西亚带州属，其他国家只带国家。
  it("adds the destination once it is complete", () => {
    expect(quoteDestination("", "")).toBeNull();
    expect(quoteDestination("MY", "")).toBeNull();
    expect(quoteDestination("MY", "MY-10")).toEqual({ country_code: "MY", state_code: "MY-10" });
    expect(quoteDestination("GB", "MY-10")).toEqual({ country_code: "GB", state_code: null });
  });

  // SHOP-TASK-025 验收第 6 条「改国家或州属后重新计价，只采用最后一次请求的结果」：请求标识随国家、州属、语言与件数变化；结果只在属于当前请求时直接采用，否则标为等待中。
  it("keys the request by language, lines and destination", () => {
    const key = checkoutQuoteKey("en", CART, quoteDestination("GB", ""), 0);
    expect(checkoutQuoteKey("en", CART, quoteDestination("SG", ""), 0)).not.toBe(key);
    expect(checkoutQuoteKey("en", CART, quoteDestination("MY", "MY-10"), 0)).not.toBe(checkoutQuoteKey("en", CART, quoteDestination("MY", "MY-14"), 0));
    expect(checkoutQuoteKey("zh", CART, quoteDestination("GB", ""), 0)).not.toBe(key);
    expect(checkoutQuoteKey("en", [{ sku: "tee-black-m", slug: "other", quantity: 2 }, CART[1] ?? { sku: "candle", slug: "candle", quantity: 1 }], quoteDestination("GB", ""), 0)).toBe(key);
    expect(checkoutQuoteKey("en", [], null, 0)).toBeNull();

    expect(summaryQuote("k1", null)).toEqual({ status: "loading" });
    expect(summaryQuote("k1", { key: "k1", outcome: { status: "ready", quote: TO_BRITAIN }, last: TO_BRITAIN })).toEqual(readyQuote(TO_BRITAIN));
    expect(summaryQuote("k2", { key: "k1", outcome: { status: "ready", quote: TO_BRITAIN }, last: TO_BRITAIN })).toEqual(readyQuote(TO_BRITAIN, true));
    expect(summaryQuote("k2", { key: "k2", outcome: { status: "error" }, last: TO_BRITAIN })).toEqual({ status: "error" });
  });
});

describe("browser storage and addresses", () => {
  // SHOP-TASK-025 验收第 2 条「收货资料与电话只保存在页面内存，不写进网址、localStorage、sessionStorage 或 cookie」：
  // 从第 1 步继续、填写收货资料、下单（含网络中断后的重试）到成功的整个过程，localStorage 只被写了一次——清空购物车（值为 []），
  // sessionStorage、cookie 与历史记录都没有写入；请求地址与页面上的链接都不含电话或收货资料。
  it("keeps the phone and the shipping details in memory only", async () => {
    const storage = () => ({ getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() });
    const local = storage();
    const session = storage();
    const cookieWrites: string[] = [];
    const doc = {};
    Object.defineProperty(doc, "cookie", {
      get: () => "",
      set: (value: string) => {
        cookieWrites.push(value);
      },
    });
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", session);
    vi.stubGlobal("document", doc);
    vi.stubGlobal("history", history);
    const urls: string[] = [];
    const replies = [() => Promise.reject(new TypeError("Failed to fetch")), () => Promise.resolve(new Response("{}", { status: 200 }))];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        urls.push(input);
        return (replies.shift() ?? (() => Promise.reject(new Error("unexpected"))))();
      }),
    );

    let form = continueFromPhone({ ...STEP_1, phoneRegion: "GB", phoneInput: "7700 900123" }, false);
    form = { ...form, recipient: step3().recipient };
    const html = render({ form, quote: readyQuote(TO_BRITAIN) });
    const body = guestOrderBody(CART, { input: form.phoneInput, region: form.phoneRegion }, guestRecipient(form.recipient));
    const reply = await runGuestOrder(body, orderAttemptFor(null, body, () => "0d9f2c1e-7a55-4b2b-9a0e-3f1c2d4e5f60").key, { delay: () => Promise.resolve() });
    expect(reply).toBe("placed");
    const visited: RoutePath[] = [];
    completeOrder(local, (path) => visited.push(path));

    expect(local.setItem.mock.calls).toEqual([[CART_STORAGE_KEY, "[]"]]);
    expect(session.setItem).not.toHaveBeenCalled();
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(visited).toEqual(["/pay"]);
    expect(urls).toEqual(["/api/orders/guest", "/api/orders/guest"]);
    for (const href of [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "")) {
      expect(href).not.toMatch(/7700|900123|Sam|Example|AB1|\?/);
    }
  });
});

describe("dictionary", () => {
  // SHOP-TASK-025 验收第 10 条「页面文字全部来自字典」：除接口返回的商品数据（名称、规格值、不可购买行的 SKU）、件数（× n）、
  // 地区名称（按 Intl.DisplayNames，带或不带 +呼叫码）与州属名称外，各状态下每段文字都是当前语言的某条字典文案（带变量的按模板匹配）。
  it.each(LANGUAGES)("shows only dictionary text besides product and region data in %s", (language) => {
    const names = countryNames(language);
    const regionNames = new Set(REGIONS.regions.flatMap((region) => [names(region.code), `${names(region.code)} +${String(region.calling_code)}`]));
    const data = new Set(["Crew Neck Tee", "Soy Wax Candle", "Black", "M", "gone-sku", ...REGIONS.my_states.map((state) => state.name), ...regionNames]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    const withGone = quoteOf({ ...TO_BRITAIN, lines: [...ROWS, { sku: "gone-sku", quantity: 2, status: "unavailable" }] });
    const states: RenderOptions[] = [
      { language },
      { language, lines: [] },
      { language, reference: { status: "loading" }, quote: { status: "loading" } },
      { language, reference: { status: "error" }, quote: { status: "error" } },
      { language, reference: referenceOf(true), form: { ...STEP_1, phoneInput: "12", phoneError: "unavailable" } },
      { language, form: { ...STEP_1, phoneInput: "12", phoneError: "invalid" } },
      { language, form: step3(), quote: readyQuote(TO_BRITAIN) },
      { language, form: step3({ guestNotice: "checkout.guest_sms_off", submitting: true, checking: true }), quote: readyQuote(TO_BRITAIN) },
      { language, form: step3({ notice: "unavailable" }), quote: readyQuote(withGone) },
      { language, form: step3({ notice: "error" }), quote: { status: "error" } },
      { language, form: SELANGOR_FORM, quote: readyQuote(TO_SELANGOR) },
    ];
    for (const state of states) {
      for (const value of visibleTexts(render(state))) {
        const known = data.has(value) || /^× \d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });

  // 同一条，经全站框架与路由：服务端渲染 /checkout 时没有本浏览器购物车，是空购物车，不出现行内样式。
  it("renders the empty checkout through the app", () => {
    const html = renderToStaticMarkup(<App initialPath="/checkout" storage={languageStorage("en")} />);
    expect(html).toContain(copy("cart.empty"));
    expect(html).toContain(copy("checkout.demo_hint"));
    expect(html).not.toContain("style=");
  });
});

// 键名用于类型检查：本页用到的字典键都存在。
const CHECKOUT_KEYS: readonly CopyKey[] = [
  "checkout.title",
  "checkout.demo_hint",
  "checkout.phone_step_title",
  "checkout.phone_notice",
  "checkout.phone_notice_sms_off",
  "checkout.form_notice_link",
  "auth.phone",
  "checkout.phone_step_hint",
  "checkout.phone_continue",
  "checkout.phone_invalid",
  "checkout.login_password",
  "checkout.guest_other_country",
  "checkout.guest_sms_off",
  "checkout.guest_notice",
  "checkout.recipient_title",
  "checkout.form_notice",
  "checkout.name",
  "checkout.country",
  "checkout.state_my",
  "checkout.region",
  "checkout.address",
  "checkout.postcode",
  "checkout.phone",
  "checkout.phone_change",
  "checkout.phone_lookup_hint",
  "checkout.coupon_members_only",
  "checkout.points_guest",
  "checkout.summary_title",
  "checkout.summary_shipping",
  "checkout.summary_total",
  "checkout.fx_note",
  "checkout.fx_none",
  "checkout.place_order",
  "checkout.place_order_hint",
  "checkout.submitting",
  "common.fx_reference",
  "common.service_unavailable",
  "common.network_check",
  "common.error_retry",
  "common.price_myr",
  "common.nav_cart",
  "cart.subtotal",
  "cart.shipping_later",
  "cart.item_changed",
  "cart.empty",
  "cart.continue",
];

describe("dictionary keys", () => {
  // SHOP-TASK-025 验收第 1 条「文案以 docs/UX-COPY.md 0.6 为准」：P05 用到的文案键都在字典里且三列非空（与文档的逐字比较由 i18n/copy.test.ts 覆盖）。
  it.each(CHECKOUT_KEYS)("has %s in all three languages", (key) => {
    for (const language of LANGUAGES) {
      expect(COPY[key][language]).not.toBe("");
    }
  });
});
