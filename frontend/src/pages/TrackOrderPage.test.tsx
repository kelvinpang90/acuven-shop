import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { LookupLine, LookupOrder, LookupRefund } from "../api/orderLookup";
import { formatSen } from "../format";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, RouterProvider } from "../router";
import { countryName } from "./PayPage";
import { formatDate, formatDateTime, stepState, TrackOrderView } from "./TrackOrderPage";
import type { TrackOrderViewProps } from "./TrackOrderPage";

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";
const STATES = new Map([["MY-10", "Selangor"]]);
const DEADLINE = "2026-11-04T10:02:00Z";

function orderLine(overrides: Partial<LookupLine> = {}): LookupLine {
  return {
    line_index: 0,
    name: "Crew Neck Tee",
    variant_label: "Black, M",
    quantity: 2,
    unit_price_sen: 3900,
    line_subtotal_sen: 7800,
    unit_cash_paid_sen: [3369, 3368],
    refundable_quantity: 2,
    refund_estimates_sen: [3369, 6737],
    ...overrides,
  };
}

function lookupOrder(overrides: Partial<LookupOrder> = {}): LookupOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "demo_shipped",
    created_at: "2026-10-05T10:00:00Z",
    paid_at: "2026-10-05T10:02:00Z",
    server_time: "2026-10-05T11:00:00Z",
    lines: [
      orderLine(),
      orderLine({ line_index: 1, name: "Soy Wax Candle", variant_label: "", quantity: 1, unit_price_sen: 3200, line_subtotal_sen: 3200, unit_cash_paid_sen: [2763], refundable_quantity: 1, refund_estimates_sen: [2763] }),
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
    refund_deadline: DEADLINE,
    refund_window_open: true,
    refunded_total_sen: 0,
    refundable_left_sen: 9500,
    fully_refunded: false,
    refund_requests: [],
    ...overrides,
  };
}

// 三种状态的申请记录各一笔，按创建时间从新到旧；金额与行金额故意不是单价的倍数。
const REQUESTS: LookupRefund[] = [
  { created_at: "2026-10-09T03:00:00Z", status: "requested", amount_sen: 2763, lines: [{ name: "Soy Wax Candle", variant_label: "", quantity: 1, amount_sen: 2763 }] },
  { created_at: "2026-10-08T03:00:00Z", status: "approved", amount_sen: 3369, lines: [{ name: "Crew Neck Tee", variant_label: "Black, M", quantity: 1, amount_sen: 3369 }] },
  {
    created_at: "2026-10-07T03:00:00Z",
    status: "rejected",
    amount_sen: 6105,
    lines: [
      { name: "Crew Neck Tee", variant_label: "Black, M", quantity: 1, amount_sen: 3342 },
      { name: "Soy Wax Candle", variant_label: "", quantity: 1, amount_sen: 2763 },
    ],
  },
];

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
  return { screen: { status: "ready", order: lookupOrder() }, states: STATES, busy: null, failed: false, submitted: false, onConfirm: noop, ...overrides };
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

  // SHOP-TASK-028 验收第 2 条「页面不显示任何凭据内容或授权剩余时间」、SHOP-TASK-030 验收第 2 条「订单号与 CSRF 令牌不进任何路径、查询参数」与 UX「阅读说明」订单号不进路径：
  // 页面里的链接只去 /track 与退款申请页 /track/order/refund，都不带订单号或查询参数。
  it("links only to the lookup and refund pages and never with the order number", () => {
    const hrefs = [...render().matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
    expect(new Set(hrefs)).toEqual(new Set(["/track", "/track/order/refund"]));
  });
});

describe("amounts", () => {
  // SHOP-TASK-028 验收第 5 条「不显示优惠券与积分两行（游客订单，UX 0.5 起的规则）」与 UX 0.5 修订要点「游客订单…不显示 [checkout.summary_coupon]、[checkout.summary_points] 两行」：
  // 桌面金额明细与手机折叠里都只有商品小计、示例运费、合计三行，没有优惠券或积分抵扣行（行名为 UX-COPY 的三语原文）。
  // order.cash_paid 的说明文字本身含「积分抵扣」，所以只检查金额明细的行与独立成段的行名，不禁止整页出现这个词。
  it.each(LANGUAGES)("has no coupon or points rows in %s", (language) => {
    const html = render({}, language);
    const labels = [COPY["cart.subtotal"][language], COPY["checkout.summary_shipping"][language], COPY["checkout.summary_total"][language]];
    const rowNames = ["Coupon discount", "优惠券抵扣", "Diskaun kupon", "Points discount", "积分抵扣", "Diskaun mata"];
    for (const amounts of [desktopAmounts(html), phoneAmounts(html)]) {
      const rows = [...amounts.matchAll(/<div class="acs-row[^"]*"><span>([^<]*)<\/span>/g)].map((m) => unescapeHtml(m[1] ?? ""));
      expect(rows).toEqual(labels);
      for (const text of rowNames) {
        expect(amounts).not.toContain(text);
      }
    }
    for (const text of visibleTexts(html)) {
      expect(rowNames).not.toContain(text);
    }
  });

  // SHOP-TASK-028 验收第 8 条「金额原样来自接口」与「不在浏览器计算任何金额」：小计、运费与合计彼此对不上时照样显示接口的数；
  // 逐件实付原样逐件显示，不相加成行合计；单价不乘件数。
  it("shows every amount exactly as the order returns it", () => {
    const odd = lookupOrder({
      subtotal_sen: 10000,
      shipping_fee_sen: 1000,
      total_sen: 12345,
      lines: [orderLine({ line_subtotal_sen: 9999, unit_cash_paid_sen: [3001, 2999] })],
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

  // SHOP-TASK-028 验收第 6 条「403 csrf_failed 时重新读取订单取得新令牌并显示 common.error_retry」：重新读取到的订单已不是 demo_shipped
  // （例如已被确认为 demo_completed）时，确认收货按钮消失，但 common.error_retry 仍显示。
  it.each(STATUSES)("keeps common.error_retry after a failure when the re-read order is %s", (status) => {
    const html = render({ ...ready(lookupOrder({ status })), failed: true });
    expect(has(html, "common.error_retry")).toBe(true);
  });
});

describe("unpaid orders and missing entries", () => {
  // SHOP-TASK-028 验收第 5 条「待支付订单显示 order.lookup_no_pay，没有支付与取消按钮」与 UX P09「待支付订单——查单模式：[order.lookup_no_pay]（无支付、取消按钮）」：
  // 待支付订单只有说明，没有任何按钮，也没有去支付页的链接；其他状态不显示这条说明。
  it.each(LANGUAGES)("explains that an unpaid order cannot be paid or cancelled here in %s", (language) => {
    const html = render(ready(lookupOrder({ status: "awaiting_demo_payment", paid_at: null, refund_deadline: null, refund_window_open: false, lines: [orderLine({ unit_cash_paid_sen: [] })] })), language);
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

  // SHOP-TASK-030 验收第 6 条「会员模式（account.orders 与积分行）在会员中心实现前不渲染」：会员中心不在路由表里，各状态下都不出现会员模式的文案（UX-COPY 原文）或链接。
  it("renders no member-mode links", () => {
    expect(isRoutePath("/account")).toBe(false);
    for (const status of STATUSES) {
      const html = render(ready(lookupOrder({ status, refund_requests: REQUESTS })));
      for (const text of ["My orders", "Continue payment"]) {
        expect(html).not.toContain(text);
      }
      expect(html).not.toMatch(/href="\/account/);
    }
  });
});

describe("refund entry", () => {
  const entry = (html: string) => html.includes(`<a class="acs-btn acs-btn--secondary acs-btn--block" href="/track/order/refund">`);

  // SHOP-TASK-030 验收第 3 条「P09 在接口判定退款期内且剩余可退大于零时显示 order.request_refund（链到 /track/order/refund）与 order.refund_deadline」与
  // UX P09「已支付且在退款期内：( [order.request_refund] ) [order.refund_deadline]」：截止时间按 order.refund_deadline 的模板显示。
  it.each(LANGUAGES)("links to the refund page with the deadline in %s", (language) => {
    const html = render({}, language);
    expect(html).toContain(`<a class="acs-btn acs-btn--secondary acs-btn--block" href="/track/order/refund">${escapeHtml(COPY["order.request_refund"][language])}</a>`);
    const deadline = formatCopy(COPY["order.refund_deadline"][language], { date: formatDateTime(DEADLINE, language) });
    expect(html).toContain(`<p class="acs-body-s acs-muted">${escapeHtml(deadline)}</p>`);
  });

  // 同一条「接口判定退款期内且剩余可退大于零」：退款期已过（接口判定）或剩余可退为零时都没有入口与截止时间；未支付时也没有。
  it.each<[string, Partial<LookupOrder>]>([
    ["window closed", { refund_window_open: false }],
    ["nothing left", { refundable_left_sen: 0 }],
    ["unpaid", { status: "awaiting_demo_payment", paid_at: null, refund_deadline: null, refund_window_open: false }],
  ])("shows no refund entry when %s", (_name, overrides) => {
    const html = render(ready(lookupOrder(overrides)));
    expect(entry(html)).toBe(false);
    expect(has(html, "order.request_refund")).toBe(false);
    expect(html).not.toContain("Refunds can be requested until");
    expect(html).not.toContain(`href="/track/order/refund"`);
  });

  // SHOP-TASK-030 验收第 3 条「截止时间按访客浏览器时区显示日期与时间」：同一时刻在不同时区显示为不同的日期或时间，且带时间（与只显示日期的写法不同）；
  // 不给时区时用运行环境（浏览器）的时区。
  it("formats the deadline with date and time in the visitor's time zone", () => {
    expect(formatDateTime(DEADLINE, "en", "UTC")).toMatch(/Nov 4, 2026.*10:02/);
    expect(formatDateTime(DEADLINE, "en", "Asia/Kuala_Lumpur")).toMatch(/Nov 4, 2026.*6:02/);
    expect(formatDateTime("2026-11-04T20:30:00Z", "en", "Asia/Kuala_Lumpur")).toMatch(/Nov 5, 2026.*4:30/);
    for (const language of LANGUAGES) {
      expect(formatDateTime(DEADLINE, language, "UTC")).toContain("2026");
      expect(formatDateTime(DEADLINE, language, "UTC")).toMatch(/10[.:]02/);
      expect(formatDateTime(DEADLINE, language, "UTC")).not.toBe(formatDate(DEADLINE, language, "UTC"));
    }
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(formatDateTime(DEADLINE, "en")).toBe(formatDateTime(DEADLINE, "en", zone));
  });

  // UX P09 桌面线框的顺序：确认收货与说明，之后 ( [order.request_refund] ) [order.refund_deadline]，[order.refunded_total] [order.refundable_left]，[order.refund_requests] 与各条记录。
  it("follows the order of the wireframe", () => {
    const html = render(ready(lookupOrder({ refund_requests: REQUESTS })));
    const positions = [
      html.indexOf(`>${COPY["order.confirm_receipt"].en}<`),
      html.indexOf(escapeHtml(COPY["order.confirm_receipt_hint"].en)),
      html.indexOf(`>${COPY["order.request_refund"].en}<`),
      html.indexOf("Refunds can be requested until"),
      html.indexOf("Refunded so far (demo)"),
      html.indexOf("Still refundable"),
      html.indexOf(`>${COPY["order.refund_requests"].en}<`),
      html.indexOf(`>${COPY["order.refund_requested"].en}<`),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });
});

describe("refund amounts and requests", () => {
  // SHOP-TASK-030 验收第 3 条「订单已支付时显示 order.refunded_total、order.refundable_left」与第 7 条「金额原样来自接口」：两项金额照接口的数显示，
  // 不由申请记录相加或由合计相减（这里故意给对不上的数）。
  it.each(LANGUAGES)("shows the refunded and refundable amounts as returned in %s", (language) => {
    const html = render(ready(lookupOrder({ refunded_total_sen: 1234, refundable_left_sen: 5, refund_requests: REQUESTS })), language);
    expect(html).toContain(`<span class="acs-num">${escapeHtml(formatCopy(COPY["order.refunded_total"][language], { amount: "12.34" }))}</span>`);
    expect(html).toContain(`<span class="acs-num">${escapeHtml(formatCopy(COPY["order.refundable_left"][language], { amount: "0.05" }))}</span>`);
  });

  // 同一条「订单已支付时」：未支付订单（paid_at 为空）没有累计已退、剩余可退与申请记录。
  it("shows no refund amounts for unpaid orders", () => {
    for (const status of ["awaiting_demo_payment", "demo_cancelled"]) {
      const html = render(ready(lookupOrder({ status, paid_at: null, refund_deadline: null, refund_window_open: false })));
      for (const key of ["order.refunded_total", "order.refundable_left"] as const) {
        expect(html).not.toContain(COPY[key].en.split("{")[0] ?? "");
      }
      expect(has(html, "order.refund_requests")).toBe(false);
    }
  });

  // SHOP-TASK-030 验收第 3 条「order.refund_requests 记录（日期、商品与件数、金额、order.refund_requested、order.refund_approved 或 order.refund_rejected）」与
  // UX P09「<日期> <商品 x数量> [M4] [order.refund_requested|approved|rejected]」：三种状态各自的文案；日期、各行名称规格与件数、申请金额（原样）按此顺序。
  it.each(LANGUAGES)("lists every request with its date, items, amount and status in %s", (language) => {
    const html = render(ready(lookupOrder({ refund_requests: REQUESTS })), language);
    const desktop = part(html, /<div class="site-desktop-only site-order__history">[\s\S]*?<\/ul><\/div>/);
    expect(desktop).toContain(`<h2 class="acs-field__label">${escapeHtml(COPY["order.refund_requests"][language])}</h2>`);
    const items = [...desktop.matchAll(/<li class="site-order__request">([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");
    expect(items).toHaveLength(3);
    const statuses: [string, CopyKey][] = [
      ["acs-tag acs-tag--demo", "order.refund_requested"],
      ["acs-tag acs-tag--success", "order.refund_approved"],
      ["acs-tag acs-tag--outline", "order.refund_rejected"],
    ];
    for (const [index, request] of REQUESTS.entries()) {
      const item = items[index] ?? "";
      const status = statuses[index];
      if (status === undefined) {
        throw new Error("missing status");
      }
      const [tag, key] = status;
      expect(item.startsWith(`<span class="acs-num">${escapeHtml(formatDate(request.created_at, language))}</span>`)).toBe(true);
      expect(item.endsWith(`<span class="acs-num">RM ${formatSen(request.amount_sen)}</span><span class="${tag}">${escapeHtml(COPY[key][language])}</span>`)).toBe(true);
    }
    expect(items[2]).toMatch(/<span>Crew Neck Tee<\/span><span class="site-order__option"><svg[^>]*>[\s\S]*?<\/svg><span>Black, M<\/span><\/span><span class="acs-num">× 1<\/span><\/span><span class="site-order__name"><span>Soy Wax Candle<\/span><span class="acs-num">× 1<\/span>/);
    // 申请金额是接口的合计，不是各行金额的重算（RM 61.05 = 33.42 + 27.63 恰好相等，另以对不上的数验证）。
    const odd = render(ready(lookupOrder({ refund_requests: [{ ...REQUESTS[0], amount_sen: 101 } as LookupRefund] })), language);
    expect(odd).toContain(`<span class="acs-num">RM 1.01</span>`);
  });

  // UX P09 手机线框「▸ [order.refund_requests]」：手机为默认收起的折叠块，内容与桌面列表相同；没有记录时两处都不渲染。
  it("folds the requests on phones", () => {
    const html = render(ready(lookupOrder({ refund_requests: REQUESTS })));
    const fold = part(html, /<details class="site-phone-only site-order__fold">[\s\S]*?<\/details>/);
    expect(fold).toContain(`<summary class="site-order__fold-head">${COPY["order.refund_requests"].en}</summary>`);
    expect(fold.match(/<li class="site-order__request">/g)).toHaveLength(3);
    expect(html).not.toMatch(/<details[^>]*\sopen/);
    const none = render();
    expect(none).not.toContain(COPY["order.refund_requests"].en);
    expect(none).not.toContain("site-order__history");
  });

  // 派生实现约束（实现选择）：守住第 3 条「order.refund_requested、order.refund_approved 或 order.refund_rejected」——未知状态不显示状态标签，其余照常。
  it("shows no status label for an unknown status", () => {
    const html = render(ready(lookupOrder({ refund_requests: [{ ...REQUESTS[1], status: "unknown" } as LookupRefund] })));
    const desktop = part(html, /<div class="site-desktop-only site-order__history">[\s\S]*?<\/ul><\/div>/);
    expect(desktop).not.toContain("acs-tag");
    expect(desktop).toContain(`<span class="acs-num">RM 33.69</span>`);
  });
});

describe("fully refunded and submitted", () => {
  // SHOP-TASK-030 验收第 3 条「接口判定全部已退时显示 order.fulfilment_frozen 且不显示确认收货按钮」与 UX P09「全部已退时：[order.fulfilment_frozen]」：
  // 已发货但全部已退时没有确认收货按钮与说明（HTML 与元素树两种方式），显示冻结说明；没有全部已退时不显示这句。
  it.each(LANGUAGES)("freezes the order once everything is refunded in %s", (language) => {
    const frozen = lookupOrder({ fully_refunded: true, refund_window_open: true, refundable_left_sen: 0, refunded_total_sen: 9500 });
    const html = render(ready(frozen), language);
    expect(has(html, "order.fulfilment_frozen", language)).toBe(true);
    expect(has(html, "order.confirm_receipt", language)).toBe(false);
    expect(html.includes(escapeHtml(COPY["order.confirm_receipt_hint"][language]))).toBe(false);
    expect(confirmButton(tree(ready(frozen)))).toBeUndefined();
    expect(has(html, "order.request_refund", language)).toBe(false);
    for (const status of STATUSES) {
      expect(has(render(ready(lookupOrder({ status })), language), "order.fulfilment_frozen", language)).toBe(false);
    }
  });

  // SHOP-TASK-030 验收第 5 条「201 或 200 时回到 /track/order 并显示 refund.submitted」与 UX P10「去向：P09（提交后显示 [refund.submitted] 与记录）」：
  // 刚提交回来时显示成功提示，平时不显示。
  it.each(LANGUAGES)("shows refund.submitted after coming back from the refund page in %s", (language) => {
    const html = render({ ...ready(lookupOrder({ refund_requests: REQUESTS })), submitted: true }, language);
    expect(html).toContain(`<p class="acs-alert acs-alert--success" role="status">${escapeHtml(COPY["refund.submitted"][language])}</p>`);
    expect(has(render({}, language), "refund.submitted", language)).toBe(false);
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
  // SHOP-TASK-028 验收第 8 条与 SHOP-TASK-030 验收第 7 条「页面文字全部来自字典」：各状态下（含退款入口、申请记录、全部已退与刚提交回来），
  // 除订单数据（订单号、商品名与规格、件数、收货资料、州属与国家名、申请日期、分隔符号）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const states: Partial<TrackOrderViewProps>[] = [
      ...STATUSES.map((status) => ready(lookupOrder({ status }))),
      ready(lookupOrder({ refund_requests: REQUESTS })),
      ready(lookupOrder({ fully_refunded: true, refundable_left_sen: 0, refund_requests: REQUESTS })),
      { submitted: true },
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
      ...REQUESTS.map((request) => formatDate(request.created_at, language)),
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
