import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { LookupOrder } from "../api/orderLookup";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, RouterProvider } from "../router";
import { countryName } from "./PayPage";
import { stepState, TrackOrderView } from "./TrackOrderPage";
import type { TrackOrderViewProps } from "./TrackOrderPage";

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";
const STATES = new Map([["MY-10", "Selangor"]]);

function lookupOrder(overrides: Partial<LookupOrder> = {}): LookupOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "demo_shipped",
    created_at: "2026-10-05T10:00:00Z",
    paid_at: "2026-10-05T10:02:00Z",
    server_time: "2026-10-05T11:00:00Z",
    lines: [
      { name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 3900, line_subtotal_sen: 7800, unit_cash_paid_sen: [3369, 3368] },
      { name: "Soy Wax Candle", variant_label: "", quantity: 1, unit_price_sen: 3200, line_subtotal_sen: 3200, unit_cash_paid_sen: [2763] },
    ],
    subtotal_sen: 11000,
    shipping_fee_sen: 800,
    total_sen: 11800,
    recipient: {
      name: "Aina Rahman",
      phone: "+60123456789",
      country_code: "MY",
      region: "MY-10",
      address: "12 Jalan Contoh 3",
      postal_code: "47000",
    },
    ...overrides,
  };
}

const STATUSES = ["awaiting_demo_payment", "demo_paid", "demo_packed", "demo_shipped", "demo_completed", "demo_cancelled"] as const;

const STATUS_KEY: Readonly<Record<(typeof STATUSES)[number], CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
  demo_cancelled: "order.status_cancelled",
};

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function props(overrides: Partial<TrackOrderViewProps> = {}): TrackOrderViewProps {
  return { screen: { status: "ready", order: lookupOrder() }, states: STATES, busy: null, failed: false, onConfirm: noop, ...overrides };
}

function ready(order: LookupOrder): Partial<TrackOrderViewProps> {
  return { screen: { status: "ready", order } };
}

function wrap(children: ReactNode, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/track/order">{children}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<TrackOrderViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<TrackOrderView {...props(overrides)} />, language));
}

// TrackOrderView 返回的元素树（不展开其中的子组件），用来取按钮的 disabled 与点击处理；仍在语言与路由的上下文里渲染。
function tree(overrides: Partial<TrackOrderViewProps> = {}): ReactNode {
  const captured: ReactNode[] = [];
  const probe = () => {
    captured.push(TrackOrderView(props(overrides)));
    return null;
  };
  renderToStaticMarkup(wrap(createElement(probe), "en"));
  return captured[0];
}

type AnyElement = ReactElement<Record<string, unknown>>;

function elements(node: ReactNode): AnyElement[] {
  if (Array.isArray(node)) {
    return node.flatMap((child: ReactNode) => elements(child));
  }
  if (!isValidElement(node)) {
    return [];
  }
  const element = node as AnyElement;
  return [element, ...elements(element.props.children as ReactNode)];
}

function confirmButton(node: ReactNode): AnyElement | undefined {
  return elements(node).find((element) => element.type === "button" && element.props.children === COPY["order.confirm_receipt"].en);
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
  return html.includes(`>${escapeHtml(COPY[key][language])}<`);
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const desktopAmounts = (html: string) => part(html, /<div class="acs-summary site-desktop-only site-order__amounts">[\s\S]*?<\/div><\/div>/);
const phoneAmounts = (html: string) => part(html, /<details class="acs-summary site-phone-only site-order__fold"><summary class="site-order__fold-head"><span>[\s\S]*?<\/details>/);

describe("order", () => {
  // SHOP-TASK-028 验收第 5 条「显示 order.title（含订单号）、order.current_status 与对应的 order.status_*、★ order.demo_hint、order.lookup_access、
  // order.lookup_another（回 /track）、order.progress 五步、order.items 各行（名称、规格、件数、order.unit_price、order.cash_paid）、cart.subtotal、
  // checkout.summary_shipping、checkout.summary_total、checkout.recipient_title 与收货资料原文」。
  it.each(LANGUAGES)("shows every element of the lookup-mode wireframe in %s", (language) => {
    const html = render({}, language);
    expect(html).toContain(`<h1 class="acs-display-l">${escapeHtml(formatCopy(COPY["order.title"][language], { orderNo: ORDER_NUMBER }))}</h1>`);
    const keys: CopyKey[] = [
      "order.current_status",
      "order.status_shipped",
      "order.demo_hint",
      "order.lookup_access",
      "order.lookup_another",
      "order.progress",
      "order.status_awaiting",
      "order.status_paid",
      "order.status_packed",
      "order.status_completed",
      "order.items",
      "order.unit_price",
      "order.cash_paid",
      "cart.subtotal",
      "checkout.summary_shipping",
      "checkout.summary_total",
      "checkout.recipient_title",
      "order.amount_breakdown",
      "order.confirm_receipt",
      "order.confirm_receipt_hint",
    ];
    for (const key of keys) {
      expect(has(html, key, language), key).toBe(true);
    }
    expect(html).toContain(`<a class="site-order__another" href="/track">${escapeHtml(COPY["order.lookup_another"][language])}</a>`);
    expect(html).toMatch(/<span>Crew Neck Tee<\/span><span class="site-order__option"><svg[^>]*aria-hidden="true">[\s\S]*?<\/svg><span>Black, M<\/span><\/span><span class="acs-num">× 2<\/span>/);
    expect(html).toContain(`<span>Soy Wax Candle</span><span class="acs-num">× 1</span>`);
    expect(html).toContain(`<span>${escapeHtml(COPY["order.unit_price"][language])}</span><span class="acs-num">RM 39.00</span>`);
    expect(html).toContain(`<span class="acs-caption">${escapeHtml(COPY["order.cash_paid"][language])}</span><span class="acs-price">RM 33.69</span><span class="acs-price">RM 33.68</span>`);
  });

  // UX P09 桌面线框的顺序：[order.title] [order.current_status] [order.status_*] ★ [order.demo_hint]，[order.lookup_access] ([order.lookup_another])，
  // [order.progress] 五步，[order.items] 各行，金额明细，[checkout.recipient_title]，( [order.confirm_receipt] ) [order.confirm_receipt_hint]。
  it("follows the order of the wireframe", () => {
    const html = render();
    const positions = [
      html.indexOf(`Order ${ORDER_NUMBER}`),
      html.indexOf(`>${COPY["order.current_status"].en}<`),
      html.indexOf(`class="acs-tag acs-tag--accent">${COPY["order.status_shipped"].en}<`),
      html.indexOf(COPY["order.demo_hint"].en),
      html.indexOf(COPY["order.lookup_access"].en),
      html.indexOf(COPY["order.lookup_another"].en),
      html.indexOf(COPY["order.progress"].en),
      html.indexOf(`>${COPY["order.items"].en}<`),
      html.indexOf("Crew Neck Tee"),
      html.indexOf("Soy Wax Candle"),
      html.indexOf(COPY["cart.subtotal"].en),
      html.indexOf(COPY["checkout.summary_shipping"].en),
      html.indexOf(COPY["checkout.summary_total"].en),
      html.indexOf(COPY["checkout.recipient_title"].en),
      html.indexOf(`>${COPY["order.confirm_receipt"].en}<`),
      html.indexOf(escapeHtml(COPY["order.confirm_receipt_hint"].en)),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // SHOP-TASK-028 验收第 5 条「order.progress 五步」与 UX P09「[order.status_awaiting] → [order.status_paid] → [order.status_packed] → [order.status_shipped] → [order.status_completed]」：
  // 五步按顺序，当前状态之前为 done，当前为 current（aria-current="step"），之后不标。
  it("marks the progress up to the current status", () => {
    const progress = part(render(), /<ol class="acs-progress">[\s\S]*?<\/ol>/);
    expect(progress).toBe(
      `<ol class="acs-progress"><li data-state="done">${COPY["order.status_awaiting"].en}</li><li data-state="done">${COPY["order.status_paid"].en}</li>` +
        `<li data-state="done">${COPY["order.status_packed"].en}</li><li data-state="current" aria-current="step">${COPY["order.status_shipped"].en}</li>` +
        `<li>${COPY["order.status_completed"].en}</li></ol>`,
    );
    expect(stepState("awaiting_demo_payment", "awaiting_demo_payment")).toBe("current");
    expect(stepState("awaiting_demo_payment", "demo_paid")).toBeUndefined();
    expect(stepState("demo_completed", "demo_shipped")).toBe("done");
    expect(stepState("demo_completed", "demo_completed")).toBe("current");
    for (const step of ["awaiting_demo_payment", "demo_paid", "demo_packed", "demo_shipped", "demo_completed"] as const) {
      expect(stepState("demo_cancelled", step)).toBeUndefined();
    }
  });

  // SHOP-TASK-028 验收第 5 条「order.current_status 与对应的 order.status_*」：每种状态显示对应的状态名。
  it.each(STATUSES)("shows the status name for %s", (status) => {
    const html = render(ready(lookupOrder({ status })));
    expect(html).toMatch(new RegExp(`<span class="acs-body-s">${COPY["order.current_status"].en}</span><span class="acs-tag acs-tag--(accent|outline)">${COPY[STATUS_KEY[status]].en.replace(/[()]/g, "\\$&")}</span>`));
  });

  // SHOP-TASK-028 验收第 2 条「页面不显示任何凭据内容或授权剩余时间」与 UX「阅读说明」订单号不进路径：页面里的链接只去 /track，不带订单号。
  it("links only to the lookup page and never with the order number", () => {
    const hrefs = [...render().matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
    expect(new Set(hrefs)).toEqual(new Set(["/track"]));
  });
});

describe("amounts", () => {
  // SHOP-TASK-028 验收第 5 条「不显示优惠券与积分两行（游客订单，UX 0.5 起的规则）」与 UX 0.5 修订要点「游客订单…不显示 [checkout.summary_coupon]、[checkout.summary_points] 两行」：
  // 桌面金额明细与手机折叠里都只有商品小计、示例运费、合计三行，没有优惠券或积分抵扣（UX-COPY 的三语原文）。
  it.each(LANGUAGES)("has no coupon or points rows in %s", (language) => {
    const html = render({}, language);
    const labels = [COPY["cart.subtotal"][language], COPY["checkout.summary_shipping"][language], COPY["checkout.summary_total"][language]];
    for (const amounts of [desktopAmounts(html), phoneAmounts(html)]) {
      const rows = [...amounts.matchAll(/<div class="acs-row[^"]*"><span>([^<]*)<\/span>/g)].map((m) => unescapeHtml(m[1] ?? ""));
      expect(rows).toEqual(labels);
    }
    for (const text of ["Coupon discount", "优惠券抵扣", "Diskaun kupon", "Points discount", "积分抵扣", "Diskaun mata"]) {
      expect(html).not.toContain(text);
    }
  });

  // SHOP-TASK-028 验收第 8 条「金额原样来自接口」与「不在浏览器计算任何金额」：小计、运费与合计彼此对不上时照样显示接口的数；
  // 逐件实付原样逐件显示，不相加成行合计；单价不乘件数。
  it("shows every amount exactly as the order returns it", () => {
    const odd = lookupOrder({
      subtotal_sen: 10000,
      shipping_fee_sen: 1000,
      total_sen: 12345,
      lines: [{ name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 3900, line_subtotal_sen: 9999, unit_cash_paid_sen: [3001, 2999] }],
    });
    const html = render(ready(odd));
    for (const amounts of [desktopAmounts(html), phoneAmounts(html)]) {
      expect(amounts).toContain(`<span class="acs-num">RM 100.00</span>`);
      expect(amounts).toContain(`<span class="acs-num">RM 10.00</span>`);
      expect(amounts).toContain(`<span class="acs-num">RM 123.45</span>`);
    }
    expect(phoneAmounts(html)).toContain(`<span>${COPY["order.amount_breakdown"].en}</span><span class="acs-num">RM 123.45</span>`);
    expect(html).toContain(`<span class="acs-price">RM 30.01</span><span class="acs-price">RM 29.99</span>`);
    for (const computed of ["RM 110.00", "RM 60.00", "RM 78.00", "RM 99.99"]) {
      expect(html).not.toContain(computed);
    }
  });

  // UX P09 手机线框「▸ [order.amount_breakdown] [M3]」「▸ [checkout.recipient_title]」与 SHOP-TASK-028 验收第 5 条「手机上金额明细与收货资料按线框折叠」：
  // 手机为两个折叠块（默认收起），桌面为金额明细与收货资料卡片；按宽度只显示其一（site.css）。
  it("folds the amounts and the shipping details on phones", () => {
    const html = render();
    const folds = [...html.matchAll(/<details class="acs-summary site-phone-only site-order__fold">/g)];
    expect(folds).toHaveLength(2);
    expect(html).not.toMatch(/<details[^>]*\sopen/);
    expect(html).toContain(`<summary class="site-order__fold-head">${COPY["checkout.recipient_title"].en}</summary>`);
    expect(html).toContain(`<section class="acs-card site-desktop-only site-order__recipient"><h2 class="acs-display-s">${COPY["checkout.recipient_title"].en}</h2>`);
  });
});

describe("shipping details", () => {
  // SHOP-TASK-028 验收第 5 条「checkout.recipient_title 与收货资料原文」与 UX P09「[P1] <姓名> [P1] <电话> [P1] <地址>/<邮编> <地区>/<国家>」：
  // 桌面卡片与手机折叠各一份；州属代码换成名称，国家按界面语言显示名称。
  it.each(LANGUAGES)("shows the full shipping details in %s", (language) => {
    const html = render({}, language);
    const country = countryName("MY", language);
    const desktop = part(html, /<section class="acs-card site-desktop-only site-order__recipient">[\s\S]*?<\/section>/);
    expect(visibleTexts(desktop)).toEqual([COPY["checkout.recipient_title"][language], "Aina Rahman", "+60123456789", "12 Jalan Contoh 3", ",", "47000", "Selangor", ",", country]);
    const phone = part(html, /<details class="acs-summary site-phone-only site-order__fold"><summary class="site-order__fold-head">[^<]*<\/summary>[\s\S]*?<\/details>/);
    expect(visibleTexts(phone)).toEqual([COPY["checkout.recipient_title"][language], "Aina Rahman", "·", "+60123456789", "12 Jalan Contoh 3", ",", "47000", ",", "Selangor", ",", country]);
  });

  // 派生实现约束（实现选择）：没有收货资料记录（接口给 null）时不渲染收货资料区块。
  it("renders no shipping details without a recipient", () => {
    expect(render(ready(lookupOrder({ recipient: null })))).not.toContain(COPY["checkout.recipient_title"].en);
  });
});

describe("confirm receipt", () => {
  // SHOP-TASK-028 验收第 6 条「只有 demo_shipped 时显示 order.confirm_receipt 与 order.confirm_receipt_hint」与 UX P09「确认收货：仅 demo_shipped 可点」：
  // 其他每种状态都没有这个按钮与说明。
  it.each(STATUSES)("shows confirm receipt only when shipped (%s)", (status) => {
    const html = render(ready(lookupOrder({ status })));
    const shipped = status === "demo_shipped";
    expect(has(html, "order.confirm_receipt")).toBe(shipped);
    expect(html.includes(escapeHtml(COPY["order.confirm_receipt_hint"].en))).toBe(shipped);
    expect(confirmButton(tree(ready(lookupOrder({ status })))) !== undefined).toBe(shipped);
  });

  // SHOP-TASK-028 验收第 6 条「提交期间按钮禁用」：确认与网络中断后确认订单期间按钮禁用，平时可点并接到页面的确认处理。
  it("disables the button while busy and hands clicks to the page", () => {
    const onConfirm = vi.fn();
    const idle = confirmButton(tree({ onConfirm }));
    expect(idle?.props.disabled).toBe(false);
    (idle?.props.onClick as () => void)();
    expect(onConfirm).toHaveBeenCalledTimes(1);
    for (const busy of ["confirming", "checking"] as const) {
      expect(confirmButton(tree({ busy }))?.props.disabled).toBe(true);
    }
  });

  // UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单…再允许重试」与 SHOP-TASK-028 验收第 6 条「403 csrf_failed 时…显示 common.error_retry」「网络中断时显示 common.network_check」：
  // 确认订单期间显示 common.network_check；失败后显示 common.error_retry。请求带幂等键与 CSRF 令牌、网络中断后先查订单并以同一幂等键重试由 api/orderLookup.test.ts 守住。
  it("shows common.network_check while checking and common.error_retry after a failure", () => {
    const checking = render({ busy: "checking" });
    expect(has(checking, "common.network_check")).toBe(true);
    expect(has(checking, "common.error_retry")).toBe(false);
    const failed = render({ failed: true });
    expect(has(failed, "common.error_retry")).toBe(true);
    expect(has(failed, "common.network_check")).toBe(false);
    expect(has(render(), "common.error_retry")).toBe(false);
  });
});

describe("unpaid orders and missing entries", () => {
  // SHOP-TASK-028 验收第 5 条「待支付订单显示 order.lookup_no_pay，没有支付与取消按钮」与 UX P09「待支付订单——查单模式：[order.lookup_no_pay]（无支付、取消按钮）」：
  // 待支付订单只有说明，没有任何按钮，也没有去支付页的链接；其他状态不显示这条说明。
  it.each(LANGUAGES)("explains that an unpaid order cannot be paid or cancelled here in %s", (language) => {
    const html = render(ready(lookupOrder({ status: "awaiting_demo_payment", paid_at: null, lines: [{ name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 3900, line_subtotal_sen: 7800, unit_cash_paid_sen: [] }] })), language);
    expect(has(html, "order.lookup_no_pay", language)).toBe(true);
    expect(html).not.toContain("<button");
    expect(html).not.toContain(`href="/pay`);
    for (const key of ["pay.simulate_success", "pay.simulate_failure", "pay.cancel_order", "pay.title"] as const) {
      expect(has(html, key, language), key).toBe(false);
    }
    for (const status of STATUSES.filter((value) => value !== "awaiting_demo_payment")) {
      expect(has(render(ready(lookupOrder({ status })), language), "order.lookup_no_pay", language)).toBe(false);
    }
  });

  // SHOP-TASK-028 验收第 7 条「退款入口（order.request_refund、order.refund_deadline、退款金额与记录、order.fulfilment_frozen）按路由规则在退款申请页实现前不渲染；
  // 会员模式（account.orders、account.order_pay）在会员中心实现前不渲染」：退款申请页与会员中心都不在路由表里，各状态下都不出现这些文案（UX-COPY 原文）或链接。
  it("renders no refund entry and no member-mode links", () => {
    expect(isRoutePath("/track/order/refund")).toBe(false);
    expect(isRoutePath("/account")).toBe(false);
    const texts = [
      "Request a refund",
      "Refunds can be requested until",
      "Refunded so far",
      "Still refundable",
      "Refund requests",
      "All items have been refunded",
      "My orders",
      "Continue payment",
    ];
    for (const status of STATUSES) {
      const html = render(ready(lookupOrder({ status })));
      for (const text of texts) {
        expect(html).not.toContain(text);
      }
      expect(html).not.toMatch(/href="\/(account|track\/order\/refund)/);
    }
  });
});

describe("session expired, loading and errors", () => {
  // SHOP-TASK-028 验收第 5 条「401 时整页替换为 order.session_expired 与 common.nav_track」与 UX P09「查单授权过期或不属于该单时替换整页
  // [order.session_expired] ( [common.nav_track] ) → P08」：只有提示与链到 /track 的按钮，没有订单内容。
  it.each(LANGUAGES)("replaces the whole page with order.session_expired in %s", (language) => {
    const html = render({ screen: { status: "expired" } }, language);
    expect(visibleTexts(html)).toEqual([COPY["order.session_expired"][language], COPY["common.nav_track"][language]]);
    expect(html).toContain(`<a class="acs-btn acs-btn--primary" href="/track">${COPY["common.nav_track"][language]}</a>`);
    expect(html).not.toContain("<button");
  });

  // 派生实现约束（实现选择）：守住第 5 条「P09 打开时调用 GET /api/orders/lookup」——接口返回之前主体为空并标 aria-busy；读取失败时只显示 common.error_retry。
  it("shows nothing before the order arrives and an error when reading fails", () => {
    expect(render({ screen: { status: "loading" } })).toBe(`<main class="site-order" aria-busy="true"></main>`);
    expect(visibleTexts(render({ screen: { status: "error" } }))).toEqual([COPY["common.error_retry"].en]);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-028 验收第 8 条「页面文字全部来自字典」：各状态下，除订单数据（订单号、商品名与规格、件数、收货资料、州属与国家名、分隔符号）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const states: Partial<TrackOrderViewProps>[] = [
      ...STATUSES.map((status) => ready(lookupOrder({ status }))),
      { busy: "checking" },
      { failed: true },
      { screen: { status: "expired" } },
      { screen: { status: "error" } },
    ];
    const orderData = new Set([
      "Crew Neck Tee",
      "Black, M",
      "Soy Wax Candle",
      "Aina Rahman",
      "+60123456789",
      "12 Jalan Contoh 3",
      "47000",
      "Selangor",
      countryName("MY", language),
      "·",
      ",",
    ]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        const known = orderData.has(value) || /^× \d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});
