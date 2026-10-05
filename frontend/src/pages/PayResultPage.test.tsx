import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PayOrder } from "../api/pay";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, RouterProvider } from "../router";
import { countryName } from "./PayPage";
import { PayResultView } from "./PayResultPage";
import type { ResultScreen } from "./PayResultPage";

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";

function payOrder(overrides: Partial<PayOrder> = {}): PayOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "demo_paid",
    created_at: "2026-10-05T10:00:00Z",
    payment_expires_at: "2026-10-05T10:15:00Z",
    server_time: "2026-10-05T10:03:00Z",
    subtotal_sen: 12000,
    shipping_fee_sen: 1500,
    total_sen: 13500,
    recipient: {
      name: "Aina Rahman",
      phone: "+60123456789",
      country_code: "MY",
      region: "MY-10",
      address: "12 Jalan Contoh 3",
      postal_code: "47000",
    },
    last_payment: { method: "card", result: "success", created_at: "2026-10-05T10:02:00Z" },
    cancelled_by: null,
    ...overrides,
  };
}

const failed = payOrder({ status: "awaiting_demo_payment", last_payment: { method: "bank", result: "failure", created_at: "2026-10-05T10:02:00Z" } });
const timedOut = payOrder({ status: "demo_cancelled", last_payment: null, cancelled_by: "timeout" });
const cancelledByYou = payOrder({ status: "demo_cancelled", last_payment: null, cancelled_by: "self" });
const STATES = new Map([["MY-10", "Selangor"]]);

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function render(screen: ResultScreen, language: Language = "en", minutes = 12): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/pay/result">
        <PayResultView screen={screen} minutes={minutes} states={STATES} />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function ready(order: PayOrder): ResultScreen {
  return { status: "ready", order };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
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

function copyPattern(template: string): RegExp {
  const parts = template.split(/\{\w+\}/).map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^${parts.join(".+")}$`);
}

function has(html: string, key: CopyKey, language: Language = "en"): boolean {
  return html.includes(escapeHtml(COPY[key][language]));
}

describe("success", () => {
  // SHOP-TASK-024 验收第 8 条「demo_paid 显示 result.success_title、result.success_body、订单号与 common.copy、pay.save_order_no、pay.amount_due、收货资料、
  // result.guest_next、★ result.demo_hint 与 result.continue（去商品列表）」。
  it.each(LANGUAGES)("shows the paid order in %s", (language) => {
    const html = render(ready(payOrder()), language);
    const keys: CopyKey[] = [
      "result.success_title",
      "result.success_body",
      "pay.order_no",
      "common.copy",
      "pay.save_order_no",
      "pay.amount_due",
      "checkout.recipient_title",
      "result.guest_next",
      "result.demo_hint",
    ];
    for (const key of keys) {
      expect(has(html, key, language), key).toBe(true);
    }
    expect(html).toContain(`<span class="acs-num site-pay__number">${ORDER_NUMBER}</span>`);
    expect(html).toContain(">RM 135.00<");
    expect(html).toContain(`<a class="acs-btn acs-btn--secondary site-result__continue" href="/products">${COPY["result.continue"][language]}</a>`);
    expect(html).toContain("<span>Selangor</span>");
    expect(html).toContain(`<span>${countryName("MY", language)}</span>`);
  });

  // SHOP-TASK-024 验收第 8 条「result.guest_register 与 result.track 按路由规则在注册页、会员订单详情实现前不渲染，不显示获得积分」与
  // UX P07 说明「游客结果页不提供确认收货、退款或其他订单的入口，也不设直达 P09 的按钮」：成功页唯一的链接是去商品列表。
  it("has no register, order detail or points", () => {
    const html = render(ready(payOrder()));
    expect(isRoutePath("/register")).toBe(false);
    expect(isRoutePath("/account/order")).toBe(false);
    for (const key of ["result.guest_register", "result.track", "result.points_earned", "result.retry"]) {
      expect(html).not.toContain(key);
    }
    expect(html).not.toMatch(/Register|View this order|demo points/);
    expect([...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1])).toEqual(["/products"]);
  });

  // UX P07 线框「成功」的顺序：[result.success_title]、[result.success_body]、订单号、[pay.save_order_no]、[pay.amount_due]、收货资料、[result.guest_next]、
  // ★ [result.demo_hint]、[result.continue]。
  it("follows the order of the wireframe", () => {
    const html = render(ready(payOrder()));
    const positions = [
      html.indexOf(COPY["result.success_title"].en),
      html.indexOf(escapeHtml(COPY["result.success_body"].en)),
      html.indexOf(ORDER_NUMBER),
      html.indexOf(escapeHtml(COPY["pay.save_order_no"].en)),
      html.indexOf(COPY["pay.amount_due"].en),
      html.indexOf(COPY["checkout.recipient_title"].en),
      html.indexOf(escapeHtml(COPY["result.guest_next"].en)),
      html.indexOf(escapeHtml(COPY["result.demo_hint"].en)),
      html.indexOf(COPY["result.continue"].en),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // SHOP-TASK-024 验收第 9 条「金额原样来自接口」与 UX P07 M1「已模拟支付金额（快照）」：合计与小计加运费不符时照样显示 total_sen；不显示参考外币。
  it("shows the total exactly as the order returns it", () => {
    const html = render(ready(payOrder({ subtotal_sen: 10000, shipping_fee_sen: 1000, total_sen: 9999 })));
    expect(html).toContain(">RM 99.99<");
    expect(html).not.toContain("RM 110.00");
    expect(html).not.toContain("≈");
  });

  // 派生实现约束（实现选择）：守住第 8 条「按重新读取的订单显示」——已模拟支付之后的状态（如管理员已模拟发货）也按成功显示。
  it("shows later statuses as success", () => {
    expect(render(ready(payOrder({ status: "demo_shipped" })))).toContain(COPY["result.success_title"].en);
  });
});

describe("failure", () => {
  // SHOP-TASK-024 验收第 8 条「最近一次支付失败且仍待支付时显示 result.failure_title、result.failure_body、pay.expires 倒计时与 result.retry（回 P06）」：
  // 没有成功页的内容与 result.continue。
  it.each(LANGUAGES)("shows the failed payment with the countdown and a retry link in %s", (language) => {
    const html = render(ready(failed), language, 7);
    expect(has(html, "result.failure_title", language)).toBe(true);
    expect(has(html, "result.failure_body", language)).toBe(true);
    expect(html).toContain(escapeHtml(COPY["pay.expires"][language].replace("{minutes}", "7")));
    expect(html).toContain(`<a class="acs-btn acs-btn--primary site-result__retry" href="/pay">${COPY["result.retry"][language]}</a>`);
    expect(has(html, "result.success_title", language)).toBe(false);
    expect(has(html, "result.continue", language)).toBe(false);
    expect(html).not.toContain(ORDER_NUMBER);
  });
});

describe("cancelled", () => {
  // SHOP-TASK-024 验收第 8 条「demo_cancelled 按取消方显示 result.cancelled（超时）或 result.cancelled_by_you，以及 result.continue」：
  // 两种文案互斥，取消方未知时按超时。
  it.each(LANGUAGES)("chooses the text by who cancelled in %s", (language) => {
    const timeout = render(ready(timedOut), language);
    expect(has(timeout, "result.cancelled", language)).toBe(true);
    expect(has(timeout, "result.cancelled_by_you", language)).toBe(false);
    const self = render(ready(cancelledByYou), language);
    expect(has(self, "result.cancelled_by_you", language)).toBe(true);
    expect(has(self, "result.cancelled", language)).toBe(false);
    const unknown = render(ready(payOrder({ status: "demo_cancelled", cancelled_by: null })), language);
    expect(has(unknown, "result.cancelled", language)).toBe(true);
    for (const html of [timeout, self]) {
      expect(html).toContain(`href="/products">${COPY["result.continue"][language]}</a>`);
      expect(has(html, "result.retry", language)).toBe(false);
      expect(has(html, "result.success_title", language)).toBe(false);
    }
  });
});

describe("session expired and loading", () => {
  // SHOP-TASK-024 验收第 3 条「接口 401 时整页显示 pay.session_expired，common.nav_track 按路由规则在订单查询页实现前不渲染」与
  // SHOP-TASK-028 验收第 3 条「加入 /track 后…P06、P07 授权过期提示里的 common.nav_track 按路由规则出现并链到 /track」、UX P07「游客凭据过期 [pay.session_expired] ( [common.nav_track] ) → P08」：
  // 结果页同样只有这一句与链到 /track 的 common.nav_track。
  it.each(LANGUAGES)("replaces the whole page with pay.session_expired in %s", (language) => {
    const html = render({ status: "expired" }, language);
    expect(isRoutePath("/track")).toBe(true);
    expect(visibleTexts(html)).toEqual([COPY["pay.session_expired"][language], COPY["common.nav_track"][language]]);
    expect([...html.matchAll(/<a [^>]*href="([^"]*)"/g)].map((m) => m[1])).toEqual(["/track"]);
  });

  // 派生实现约束（实现选择）：接口返回之前主体为空并标 aria-busy；读取失败时只显示 common.error_retry；仍待支付而最近一次不是失败（页面正换成 P06）时不显示任何结果。
  it("shows nothing before the order arrives", () => {
    expect(render({ status: "loading" })).toBe(`<main class="site-pay" aria-busy="true"></main>`);
    expect(visibleTexts(render({ status: "error" }))).toEqual([COPY["common.error_retry"].en]);
    expect(render(ready(payOrder({ status: "awaiting_demo_payment", last_payment: null })))).toBe(`<main class="site-pay" aria-busy="true"></main>`);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-024 验收第 9 条「页面文字全部来自字典」：各状态下，除订单数据（订单号、收货资料、州属与国家名、分隔符号）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const screens: ResultScreen[] = [ready(payOrder()), ready(failed), ready(timedOut), ready(cancelledByYou), { status: "expired" }, { status: "error" }];
    const orderData = new Set([ORDER_NUMBER, "Aina Rahman", "+60123456789", "12 Jalan Contoh 3", "47000", "Selangor", countryName("MY", language), "·", ","]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const screen of screens) {
      for (const value of visibleTexts(render(screen, language))) {
        const known = orderData.has(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});
