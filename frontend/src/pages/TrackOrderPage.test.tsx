import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { clearMemberOrder, handOffMemberOrder, MEMBER_CONFIRM_RECEIPT_URL, memberOrderInView, memberOrderViewUrl } from "../api/memberOrders";
import { clearMemberPayOrder, MEMBER_PAY_ORDER_KEY, stashedMemberPayOrder, stashMemberPayOrder } from "../api/memberPay";
import { createMemberSessionReader, MEMBER_SESSION_URL } from "../api/memberSession";
import { clearSubmittedRefund, handOffSubmittedRefund, receiptKey, submittedRefundOrder } from "../api/orderLookup";
import type { LookupLine, LookupOrder, LookupRefund } from "../api/orderLookup";
import { CSRF_HEADER, IDEMPOTENCY_HEADER } from "../api/pay";
import { formatSen } from "../format";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import { countryName } from "./PayPage";
import {
  confirmMemberOrder,
  continuePayment,
  formatDate,
  formatDateTime,
  loadMemberOrder,
  MEMBER_LOADING,
  memberRefundPath,
  memberScreen,
  openMemberOrder,
  openStep,
  readStep,
  startConfirm,
  stepState,
  takeMemberOrder,
  TrackOrderView,
} from "./TrackOrderPage";
import type { MemberOrderMoves, MemberOrderState, TrackOrderViewProps } from "./TrackOrderPage";

// 会员模式的测试会改动模块内存里的交接（P13 交出的订单号与 P10 提交后交来的订单号）、P06 的当前订单暂存（SHOP-TASK-163）与全局 fetch，每条之后复原。
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearMemberOrder();
  clearSubmittedRefund();
  clearMemberPayOrder();
});

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
    placed_by_member: false,
    coupon_discount_sen: 0,
    points_redeemed: 0,
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

  // SHOP-TASK-030 验收第 6 条「会员模式（account.orders 与积分行）在会员中心实现前不渲染」：查单模式在各状态下都不出现会员模式的文案（UX-COPY 原文）或链接
  // （会员中心 /account 由 SHOP-TASK-152 加入路由表，该行断言随之删去）。
  it("renders no member-mode links", () => {
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

// 以下为 SHOP-TASK-154 的会员模式（/account/order）与两种模式共有的券与积分两行。令牌、幂等键与手机号都是虚构值。
const TOKEN = "csrf-token-1";
const NEW_TOKEN = "csrf-token-2";
const KEY = "receipt-key-1";
const MASKED = "+60 12•••• 678";
// P13 交出的另一张订单。
const OTHER_ORDER = "K7Q29MXAB6TN2RJD";
// 会员模式退款申请的路径（SHOP-TASK-155 才加进路由表）：测试只把它当作 View 的去向，不断言它在不在路由表里。
const REFUND_TO_PATH: string = "/account/order/refund";
const REFUND_TO = REFUND_TO_PATH as RoutePath;
const VIEW_EN = memberOrderViewUrl("en");

type SignedIn = Extract<MemberOrderState, { status: "member" }>;

// 已登录、已取到订单的会员模式状态。
function signedIn(overrides: Partial<SignedIn> = {}): MemberOrderState {
  return { status: "member", orderNumber: ORDER_NUMBER, submitted: false, csrfToken: TOKEN, order: lookupOrder(), busy: null, failed: false, retryKey: null, ...overrides };
}

// 照 MemberOrderPage 把会员模式的状态交给 View 渲染。
function memberView(state: MemberOrderState, language: Language = "en", refundTo: RoutePath | null = REFUND_TO): string {
  const member = state.status === "member" ? state : null;
  return render(
    {
      screen: memberScreen(state),
      busy: member === null ? null : member.busy,
      failed: member !== null && member.failed,
      submitted: member !== null && member.submitted,
      mode: { kind: "member", refundTo },
    },
    language,
  );
}

type Reply = Response | "network";

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按地址各取一串回答，依次用掉；没有准备的请求一律失败。
function stubFetch(routes: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const next = routes[url]?.shift();
      if (next === undefined) {
        return Promise.reject(new Error(`unexpected request ${url}`));
      }
      return next === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const memberReply = (token = TOKEN) => json(200, { phone_masked: MASKED, has_password: true, expires_at: "2026-11-24T00:00:00Z", csrf_token: token });
const guestReply = () => json(401, { detail: "member_session_required" });
const orderReply = (order: LookupOrder = lookupOrder()) => json(200, order);

function initOf(calls: readonly Call[], index: number): RequestInit {
  return calls[index]?.init ?? {};
}

function headerOf(init: RequestInit, name: string): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.[name];
}

function bodyOf(init: RequestInit): unknown {
  return JSON.parse(typeof init.body === "string" ? init.body : "null") as unknown;
}

// 页面的处理替身：按顺序记下 show、update（及改动后的 busy）与 replace，并按它们推演页面状态。
function fakeMoves(initial: MemberOrderState = MEMBER_LOADING) {
  const events: string[] = [];
  let state = initial;
  const moves: MemberOrderMoves = {
    show: (next) => {
      state = next;
      events.push(`show ${next.status}`);
    },
    update: (change) => {
      state = change(state);
      events.push(`update ${state.status === "member" ? String(state.busy) : state.status}`);
    },
    replace: (path) => {
      events.push(`replace ${path}`);
    },
  };
  return { moves, events, current: () => state };
}

describe("member mode: opening", () => {
  // SHOP-TASK-154 验收第 2 条「会员模式打开时先经 SHOP-TASK-151 的读取对象重读会话…guest 时用 replace 进入 /login」与 HANDOFF 0.61 第（5）条
  // 「会员页打开时都重读一次会话」：未登录时只读会话、不读订单，replace 到 /login。
  it("replaces the page with /login for a guest", async () => {
    handOffMemberOrder(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [guestReply()] });
    const { moves, events } = fakeMoves();
    await openMemberOrder(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["replace /login"]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL]);
  });

  // 同一条「failed 时只显示 common.error_retry」：读取会话失败与网络中断都只有这一句提示，不读订单、不跳转。
  it.each<[string, () => Reply]>([
    ["failed", () => new Response(null, { status: 500 })],
    ["network", () => "network"],
  ])("shows only common.error_retry when reading the session %s", async (_name, reply) => {
    handOffMemberOrder(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [reply()] });
    const { moves, events, current } = fakeMoves();
    await openMemberOrder(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["show failed"]);
    expect(calls).toHaveLength(1);
    expect(visibleTexts(memberView(current()))).toEqual([COPY["common.error_retry"].en]);
    expect(openStep({ kind: "unknown" }, takeMemberOrder)).toEqual({ status: "failed" });
  });

  // Kelvin 2026-10-24 决定「会员在 P09（/account/order）刷新、页面内存里没有订单号时，用 replace 回到 P13 我的订单」：
  // 已登录而既没有 P10 交来的、也没有 P13 交接的订单号时，replace 到 /account，不读订单。
  it("goes back to /account without an order number", async () => {
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [memberReply()] });
    const { moves, events } = fakeMoves();
    await openMemberOrder(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["replace /account"]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL]);
  });

  // 验收第 2 条「读完前为 aria-busy 的空主体…member 时…取 SHOP-TASK-150 交接的订单号…经 SHOP-TASK-150 读取详情」与 HANDOFF 0.61 第（4）条
  // 「订单号经会员订单请求模块的模块内存交接…读取后不清除」：会话读完后记下订单号与令牌（订单读完前仍为空主体），再以请求体里的订单号读取该单；
  // 会话读取不带页面的 signal（共享读取），订单读取带。
  it("reads the handed-off order after the session", async () => {
    handOffMemberOrder(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [memberReply()], [VIEW_EN]: [orderReply()] });
    const reader = createMemberSessionReader();
    const page = new AbortController();
    const { moves, events, current } = fakeMoves();
    expect(memberView(current())).toBe(`<main class="site-order" aria-busy="true"></main>`);
    await openMemberOrder(reader, page.signal, moves);
    expect(events).toEqual(["show member"]);
    expect(current()).toEqual(signedIn({ order: null }));
    expect(memberView(current())).toBe(`<main class="site-order" aria-busy="true"></main>`);
    await loadMemberOrder(reader, "en", ORDER_NUMBER, page.signal, moves);
    expect(current()).toEqual(signedIn());
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL, VIEW_EN]);
    expect(initOf(calls, 0).signal ?? null).toBeNull();
    expect(initOf(calls, 1).method).toBe("POST");
    expect(initOf(calls, 1).signal).toBe(page.signal);
    expect(bodyOf(initOf(calls, 1))).toEqual({ order_number: ORDER_NUMBER });
    expect(memberOrderInView()).toBe(ORDER_NUMBER);
    expect(memberView(current())).toContain(`<h1 class="acs-display-l">${escapeHtml(formatCopy(COPY["order.title"].en, { orderNo: ORDER_NUMBER }))}</h1>`);
  });

  // 验收第 2 条「先取 orderLookup.ts 的 submittedRefundOrder（从 P10 提交回来），取到后即照查单模式调用 clearSubmittedRefund 清除（只在页面状态里记下，
  // 用来判断是否显示 refund.submitted），没有时取 SHOP-TASK-150 交接的订单号」与 HANDOFF 0.61 第（4）条「点下一张时覆盖」：
  // P10 交来的订单号优先，取到即清除并记下 submitted；交接的订单号不清除，之后再打开时取它；P13 交出别的订单号后取新值。
  it("prefers the order just refunded, clears it and later takes the handed-off order", async () => {
    handOffMemberOrder(OTHER_ORDER);
    handOffSubmittedRefund(ORDER_NUMBER);
    stubFetch({ [MEMBER_SESSION_URL]: [memberReply()] });
    const { moves, current } = fakeMoves();
    await openMemberOrder(createMemberSessionReader(), new AbortController().signal, moves);
    expect(current()).toEqual(signedIn({ order: null, submitted: true }));
    expect(submittedRefundOrder()).toBeNull();
    expect(memberOrderInView()).toBe(OTHER_ORDER);
    expect(takeMemberOrder()).toEqual({ orderNumber: OTHER_ORDER, submitted: false });
    handOffMemberOrder("H3PZ4W8CD8K4M0QX");
    expect(takeMemberOrder()).toEqual({ orderNumber: "H3PZ4W8CD8K4M0QX", submitted: false });
    expect(memberView(signedIn({ submitted: true }))).toContain(`<p class="acs-alert acs-alert--success" role="status">${escapeHtml(COPY["refund.submitted"].en)}</p>`);
    expect(has(memberView(signedIn()), "refund.submitted")).toBe(false);
  });

  // 验收第 2 条「not_found 时用 replace 回到 /account」（HANDOFF 0.61 第（5）条「P09、P10 会员模式回 404 时 replace 到 /account」）：不重读会话。
  it("goes back to /account when the order is not found", async () => {
    const calls = stubFetch({ [VIEW_EN]: [json(404, { detail: "order_not_found" })] });
    const { moves, events } = fakeMoves(signedIn({ order: null }));
    await loadMemberOrder(createMemberSessionReader(), "en", ORDER_NUMBER, new AbortController().signal, moves);
    expect(events).toEqual(["replace /account"]);
    expect(calls).toHaveLength(1);
  });

  // 验收第 2 条「signed_out 时重读会话并用 replace 进入 /login」（HANDOFF 0.61 第（5）条「任何会员接口回 401 时重读会话并 replace 到 /login」）：
  // 先重读会话（共享状态随之成为未登录，页头跟着变），再 replace。
  it("rereads the session and goes to /login when signed out", async () => {
    const calls = stubFetch({ [VIEW_EN]: [json(401, { detail: "member_session_required" })], [MEMBER_SESSION_URL]: [guestReply()] });
    const reader = createMemberSessionReader();
    const { moves, events } = fakeMoves(signedIn({ order: null }));
    await loadMemberOrder(reader, "en", ORDER_NUMBER, new AbortController().signal, moves);
    expect(events).toEqual(["replace /login"]);
    expect(calls.map((call) => call.url)).toEqual([VIEW_EN, MEMBER_SESSION_URL]);
    expect(reader.current().kind).toBe("guest");
  });

  // 验收第 2 条「failed 与 network 照查单模式显示 common.error_retry」：首次读取失败时页面只有这一句；已显示订单之后再读失败（如切换语言）保留订单并显示提示。
  it.each<[string, () => Reply]>([
    ["failed", () => new Response(null, { status: 500 })],
    ["network", () => "network"],
  ])("shows common.error_retry when reading the order %s", async (_name, reply) => {
    stubFetch({ [VIEW_EN]: [reply(), reply()] });
    const reader = createMemberSessionReader();
    const first = fakeMoves(signedIn({ order: null }));
    await loadMemberOrder(reader, "en", ORDER_NUMBER, new AbortController().signal, first.moves);
    expect(visibleTexts(memberView(first.current()))).toEqual([COPY["common.error_retry"].en]);
    const later = fakeMoves(signedIn());
    await loadMemberOrder(reader, "en", ORDER_NUMBER, new AbortController().signal, later.moves);
    expect(later.current()).toEqual(signedIn({ failed: true }));
    const html = memberView(later.current());
    expect(has(html, "common.error_retry")).toBe(true);
    expect(html).toContain(escapeHtml(formatCopy(COPY["order.title"].en, { orderNo: ORDER_NUMBER })));
  });

  // 验收第 2 条「切换语言时重新读取同一订单」：以该语言的地址、同一订单号读取。
  it.each(LANGUAGES)("reads the same order again in %s", async (language) => {
    const calls = stubFetch({ [memberOrderViewUrl(language)]: [orderReply()] });
    const { moves, current } = fakeMoves(signedIn());
    await loadMemberOrder(createMemberSessionReader(), language, ORDER_NUMBER, new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([memberOrderViewUrl(language)]);
    expect(bodyOf(initOf(calls, 0))).toEqual({ order_number: ORDER_NUMBER });
    expect(current()).toEqual(signedIn());
  });

  // 验收第 6 条「离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：
  // 重读会话期间已离开时不显示、不跳转，也不取走 P10 交来的订单号（共享状态照常更新）；订单读取回来时已离开也不更新、不重读会话。
  it("stops after leaving the page", async () => {
    handOffSubmittedRefund(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [memberReply()], [VIEW_EN]: [json(401, { detail: "member_session_required" })] });
    const reader = createMemberSessionReader();
    const gone = new AbortController();
    gone.abort();
    const { moves, events } = fakeMoves();
    await openMemberOrder(reader, gone.signal, moves);
    expect(reader.current().kind).toBe("member");
    expect(submittedRefundOrder()).toBe(ORDER_NUMBER);
    await loadMemberOrder(reader, "en", ORDER_NUMBER, gone.signal, moves);
    expect(events).toEqual([]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL, VIEW_EN]);
  });

  // 验收第 2 条「会员模式不出现 order.session_expired 整页」：会员模式的各种状态都不会成为过期画面，401 是去 /login。
  it("never shows order.session_expired", () => {
    const states: MemberOrderState[] = [MEMBER_LOADING, { status: "failed" }, signedIn({ order: null }), signedIn({ order: null, failed: true }), signedIn(), signedIn({ failed: true })];
    for (const state of states) {
      expect(memberScreen(state).status).not.toBe("expired");
      expect(memberView(state)).not.toContain(escapeHtml(COPY["order.session_expired"].en));
    }
    expect(readStep({ kind: "signed_out" })).toBe("login");
  });
});

describe("member mode: content", () => {
  // UX P09 线框「会员模式：([account.orders]) → P13」与验收第 3 条「order.lookup_access 与 order.lookup_another 换成去 /account 的 account.orders
  // （桌面与手机各一处）」「与查单模式相同（标题、状态、★ order.demo_hint、进度、各行、金额明细、收货资料、确认收货、退款部分…）」：
  // 两处都是去 /account 的 account.orders，没有查单授权说明与回 /track 的链接；其余各项照常。
  it.each(LANGUAGES)("links back to My orders instead of the lookup notice in %s", (language) => {
    const html = memberView(signedIn(), language);
    const orders = escapeHtml(COPY["account.orders"][language]);
    expect(html).toContain(`<a class="site-desktop-only site-order__orders" href="/account">${orders}</a>`);
    expect(html).toContain(`<a class="site-phone-only site-order__orders" href="/account">${orders}</a>`);
    expect(html.match(/href="\/account"/g)).toHaveLength(2);
    expect(has(html, "order.lookup_access", language)).toBe(false);
    expect(has(html, "order.lookup_another", language)).toBe(false);
    expect(html).not.toContain(`href="/track`);
    expect(html).toContain(`<h1 class="acs-display-l">${escapeHtml(formatCopy(COPY["order.title"][language], { orderNo: ORDER_NUMBER }))}</h1>`);
    const keys: CopyKey[] = [
      "order.current_status",
      "order.status_shipped",
      "order.demo_hint",
      "order.progress",
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
      "order.request_refund",
    ];
    for (const key of keys) {
      expect(has(html, key, language), key).toBe(true);
    }
  });

  // UX P09 桌面线框的顺序：[order.title] … ★ [order.demo_hint]，会员模式 ([account.orders])，之后 [order.progress]。
  it("puts My orders between the demo hint and the progress", () => {
    const html = memberView(signedIn());
    const positions = [
      html.indexOf(`Order ${ORDER_NUMBER}`),
      html.indexOf(COPY["order.demo_hint"].en),
      html.indexOf(`>${COPY["account.orders"].en}<`),
      html.indexOf(COPY["order.progress"].en),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // UX P09「会员模式：( [account.order_pay] ) → P06（继续支付或取消，Q17 已决）」与 SHOP-TASK-163 验收「会员模式下状态为待支付的订单显示指向 /pay 的
  // account.order_pay（主按钮样式的链接）」。本条原为 HANDOFF 0.61 第（3）条的「两者都不显示」，SHOP-TASK-163 取消了 SHOP-TASK-154 的暂缓，改为：
  // 待支付订单有去 /pay 的 account.order_pay（地址里没有订单号），仍没有查单模式的 order.lookup_no_pay，也没有任何按钮。
  it.each(LANGUAGES)("shows account.order_pay but not order.lookup_no_pay for an unpaid order in %s", (language) => {
    const unpaid = lookupOrder({ status: "awaiting_demo_payment", paid_at: null, refund_deadline: null, refund_window_open: false, lines: [orderLine({ unit_cash_paid_sen: [] })] });
    const html = memberView(signedIn({ order: unpaid }), language);
    expect(has(html, "order.lookup_no_pay", language)).toBe(false);
    expect(html).toContain(`<a class="acs-btn acs-btn--primary site-order__pay" href="/pay">${escapeHtml(COPY["account.order_pay"][language])}</a>`);
    expect(html.match(/href="\/pay/g)).toHaveLength(1);
    expect(html).not.toContain("<button");
    expect(has(html, "order.status_awaiting", language)).toBe(true);
  });

  // 验收第 3 条「与查单模式相同（…全部已退冻结、refund.submitted）」：全部已退时显示 order.fulfilment_frozen，没有确认收货与退款入口。
  it("freezes a fully refunded order as in lookup mode", () => {
    const frozen = lookupOrder({ fully_refunded: true, refundable_left_sen: 0, refunded_total_sen: 9500, refund_requests: REQUESTS });
    const html = memberView(signedIn({ order: frozen }));
    expect(has(html, "order.fulfilment_frozen")).toBe(true);
    expect(has(html, "order.confirm_receipt")).toBe(false);
    expect(has(html, "order.request_refund")).toBe(false);
    expect(has(html, "order.refund_requests")).toBe(true);
  });

  // 验收第 7 条「页面文字全部来自字典」：会员模式各状态下（含券与积分两行、退款入口有无、待支付、全部已退、刚提交回来、确认中与失败），
  // 除订单数据（订单号、商品名与规格、件数、收货资料、州属与国家名、申请日期、分隔符号）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const states: [MemberOrderState, RoutePath | null][] = [
      ...STATUSES.map((status): [MemberOrderState, RoutePath | null] => [signedIn({ order: lookupOrder({ status }) }), REFUND_TO]),
      [signedIn({ order: lookupOrder({ placed_by_member: true, coupon_discount_sen: 1000, points_redeemed: 500, refund_requests: REQUESTS }) }), null],
      [signedIn({ order: lookupOrder({ fully_refunded: true, refundable_left_sen: 0, refund_requests: REQUESTS }) }), REFUND_TO],
      [signedIn({ submitted: true }), REFUND_TO],
      [signedIn({ busy: "checking" }), REFUND_TO],
      [signedIn({ failed: true }), REFUND_TO],
      [{ status: "failed" }, REFUND_TO],
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
    for (const [state, refundTo] of states) {
      for (const value of visibleTexts(memberView(state, language, refundTo))) {
        const known = orderData.has(value) || /^× \d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

// 本标签页 sessionStorage 的替身：只记 P06 当前订单暂存这类键值。
function stubSessionStorage(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
  });
  return store;
}

const UNPAID = lookupOrder({ status: "awaiting_demo_payment", paid_at: null, refund_deadline: null, refund_window_open: false, lines: [orderLine({ unit_cash_paid_sen: [] })] });

describe("member mode: continue payment (SHOP-TASK-163)", () => {
  // SHOP-TASK-163 验收「其他状态不显示」与 UX P09「待支付订单…会员模式：( [account.order_pay] )」：只有待支付订单有 account.order_pay 与去 /pay 的链接。
  it.each(LANGUAGES)("shows account.order_pay only for an unpaid order in %s", (language) => {
    for (const status of STATUSES.filter((value) => value !== "awaiting_demo_payment")) {
      const html = memberView(signedIn({ order: lookupOrder({ status }) }), language);
      expect(has(html, "account.order_pay", language), status).toBe(false);
      expect(html, status).not.toContain(`href="/pay`);
    }
  });

  // 同一条「放在订单状态区里查单模式显示 order.lookup_no_pay 的位置，桌面与手机相同；account.orders 返回链接的位置不变」：
  // 两种模式下，待支付的那一项都是右栏的最后一项（只有一份，不分桌面与手机）；会员模式的 account.orders 仍是桌面与手机各一处、在进度之前。
  it("puts account.order_pay where lookup mode shows order.lookup_no_pay", () => {
    const member = memberView(signedIn({ order: UNPAID }));
    const lookup = render(ready(UNPAID));
    const pay = `<a class="acs-btn acs-btn--primary site-order__pay" href="/pay">${escapeHtml(COPY["account.order_pay"].en)}</a>`;
    expect(member).toContain(`${pay}</aside>`);
    expect(member.match(/site-order__pay/g)).toHaveLength(1);
    expect(lookup).toContain(`<span>${escapeHtml(COPY["order.lookup_no_pay"].en)}</span></div></aside>`);
    expect(member.match(/class="site-(?:desktop|phone)-only site-order__orders" href="\/account"/g)).toHaveLength(2);
    expect(member.indexOf(`>${COPY["account.orders"].en}<`)).toBeLessThan(member.indexOf(COPY["order.progress"].en));
  });

  // 同一条「查单模式不变（待支付仍为 order.lookup_no_pay，没有 account.order_pay）」：各状态的查单模式都没有 account.order_pay 与去 /pay 的链接。
  it.each(LANGUAGES)("keeps lookup mode without account.order_pay in %s", (language) => {
    expect(has(render(ready(UNPAID), language), "order.lookup_no_pay", language)).toBe(true);
    for (const status of STATUSES) {
      const html = render(ready(lookupOrder({ status })), language);
      expect(has(html, "account.order_pay", language), status).toBe(false);
      expect(html, status).not.toContain(`href="/pay`);
    }
  });

  // 同一条「点击时先经 SHOP-TASK-158 写入该单订单号，再以站内导航前往 /pay」与 DESIGN 1.14「权限与资料保护」第 7 条：先写入、后导航，导航只带路径。
  it("writes the stash before going to /pay", () => {
    const events: string[] = [];
    continuePayment(
      ORDER_NUMBER,
      (path) => {
        events.push(`navigate ${path}`);
      },
      (orderNumber) => {
        events.push(`stash ${orderNumber}`);
      },
    );
    expect(events).toEqual([`stash ${ORDER_NUMBER}`, "navigate /pay"]);
  });

  // 同一条「覆盖之前暂存的值」「订单号不进地址、查询参数与历史状态」与 UX 0.13 P06「从 P09 会员模式 [account.order_pay] 进入本页时…暂存在本标签页的 sessionStorage」：
  // 默认经 stashMemberPayOrder 写入；导航时暂存已是该单（模块变量与 sessionStorage 都是），覆盖了之前的另一张；导航的去向只是 /pay。
  it("overwrites the previous stash through SHOP-TASK-158", () => {
    const store = stubSessionStorage();
    stashMemberPayOrder(OTHER_ORDER);
    const seen: [RoutePath, string | null, string | undefined][] = [];
    continuePayment(ORDER_NUMBER, (path) => {
      seen.push([path, stashedMemberPayOrder(), store.get(MEMBER_PAY_ORDER_KEY)]);
    });
    expect(seen).toEqual([["/pay", ORDER_NUMBER, ORDER_NUMBER]]);
    expect(stashedMemberPayOrder()).toBe(ORDER_NUMBER);
  });
});

describe("member mode: refund entry", () => {
  // 验收第 3 条「order.request_refund 指向 /account/order/refund，该路径进入路由表之前不渲染这个链接（截止时间、已退与可退照常）」与
  // UX P09「已支付且在退款期内：( [order.request_refund] ) [order.refund_deadline]」：props 给出去向时链接去会员模式的退款页（不去查单模式的），
  // 没有去向时不渲染链接，截止时间、累计已退与剩余可退照常。
  it.each(LANGUAGES)("links to the member refund page only when given in %s", (language) => {
    const deadline = escapeHtml(formatCopy(COPY["order.refund_deadline"][language], { date: formatDateTime(DEADLINE, language) }));
    const withLink = memberView(signedIn(), language);
    expect(withLink).toContain(`<a class="acs-btn acs-btn--secondary acs-btn--block" href="/account/order/refund">${escapeHtml(COPY["order.request_refund"][language])}</a>`);
    expect(withLink).not.toContain(`href="/track/order/refund"`);
    const without = memberView(signedIn(), language, null);
    expect(has(without, "order.request_refund", language)).toBe(false);
    expect(without).not.toContain("/order/refund");
    for (const html of [withLink, without]) {
      expect(html).toContain(`<p class="acs-body-s acs-muted">${deadline}</p>`);
      expect(html).toContain(escapeHtml(formatCopy(COPY["order.refunded_total"][language], { amount: "0.00" })));
      expect(html).toContain(escapeHtml(formatCopy(COPY["order.refundable_left"][language], { amount: "95.00" })));
    }
  });

  // 同一条「照 frontend/src/pages/CheckoutPage.tsx 的 loginPath 写法以 isRoutePath 判断并以 props 传给 View」：去向随路由表而定
  // （不断言 /account/order/refund 现在在不在表里，以免 SHOP-TASK-155 加路由后失败）。
  it("derives the refund destination from the route table", () => {
    expect(memberRefundPath()).toBe(isRoutePath(REFUND_TO_PATH) ? REFUND_TO_PATH : null);
  });
});

describe("member mode: confirm receipt", () => {
  const target = { orderNumber: ORDER_NUMBER, csrfToken: TOKEN, key: KEY };
  const completed = lookupOrder({ status: "demo_completed" });
  const csrfReply = () => json(403, { detail: "csrf_failed" });

  // 验收第 4 条「会员模式经 SHOP-TASK-150 的会员版提交去向，以会话里的 CSRF 令牌提交，幂等键规则与查单模式相同（receiptKey）」「read…与查单模式相同」
  // 与 UX P09「确认收货…幂等，须 CSRF 令牌」：请求体只有订单号，请求头带会话的令牌与幂等键；200 与 409 order_not_confirmable 之后按重新读取到的订单显示，
  // 确认收货按钮随之消失。
  it.each<[string, () => Response]>([
    ["done", () => json(200, {})],
    ["closed", () => json(409, { detail: "order_not_confirmable" })],
  ])("shows the re-read order after %s", async (_name, reply) => {
    const calls = stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [reply()], [VIEW_EN]: [orderReply(completed)] });
    const { moves, events, current } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, moves);
    expect(events).toEqual(["update null"]);
    expect(current()).toEqual(signedIn({ order: completed }));
    expect(calls.map((call) => call.url)).toEqual([MEMBER_CONFIRM_RECEIPT_URL, VIEW_EN]);
    const submit = initOf(calls, 0);
    expect(submit.method).toBe("POST");
    expect(headerOf(submit, CSRF_HEADER)).toBe(TOKEN);
    expect(headerOf(submit, IDEMPOTENCY_HEADER)).toBe(KEY);
    expect(bodyOf(submit)).toEqual({ order_number: ORDER_NUMBER });
    expect(has(memberView(current()), "order.confirm_receipt")).toBe(false);
  });

  // 同一条「幂等键规则与查单模式相同（receiptKey）」与 UX P09「提交期间按钮禁用」：点击时进入 confirming（按钮禁用、先前的提示收起），
  // 只有网络中断后确认仍为 demo_shipped 时才沿用记下的键，否则新生成。
  it("disables the button while confirming and reuses only the retry key", () => {
    expect(startConfirm(signedIn({ failed: true, retryKey: KEY }))).toEqual(signedIn({ busy: "confirming", failed: false, retryKey: KEY }));
    expect(receiptKey(KEY, () => "fresh")).toBe(KEY);
    expect(receiptKey(null, () => "fresh")).toBe("fresh");
    expect(memberView(signedIn({ busy: "confirming" }))).toMatch(/<button class="acs-btn acs-btn--primary acs-btn--block" type="button" disabled="">/);
  });

  // 验收第 4 条「signed_out 时重读会话并用 replace 进入 /login」：
  it("rereads the session and goes to /login when signed out", async () => {
    const calls = stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [json(401, { detail: "member_session_required" })], [MEMBER_SESSION_URL]: [guestReply()] });
    const { moves, events } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, moves);
    expect(events).toEqual(["replace /login"]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_CONFIRM_RECEIPT_URL, MEMBER_SESSION_URL]);
  });

  // 验收第 4 条「not_found 时用 replace 回到 /account」：
  it("goes back to /account when the order is not found", async () => {
    const calls = stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [json(404, { detail: "order_not_found" })] });
    const { moves, events } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, moves);
    expect(events).toEqual(["replace /account"]);
    expect(calls).toHaveLength(1);
  });

  // 验收第 4 条「csrf 时重读会话——member 时换用新令牌并显示 common.error_retry」：先重新读取订单，再重读会话，换用新令牌，按钮恢复并显示提示。
  it("switches to the new token and shows common.error_retry after csrf", async () => {
    const calls = stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [csrfReply()], [VIEW_EN]: [orderReply()], [MEMBER_SESSION_URL]: [memberReply(NEW_TOKEN)] });
    const { moves, events, current } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, moves);
    expect(events).toEqual(["update null"]);
    expect(current()).toEqual(signedIn({ csrfToken: NEW_TOKEN, failed: true }));
    expect(calls.map((call) => call.url)).toEqual([MEMBER_CONFIRM_RECEIPT_URL, VIEW_EN, MEMBER_SESSION_URL]);
    expect(has(memberView(current()), "common.error_retry")).toBe(true);
  });

  // 同一条「guest 时用 replace 进入 /login」：
  it("goes to /login when the reread after csrf finds no session", async () => {
    stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [csrfReply()], [VIEW_EN]: [orderReply()], [MEMBER_SESSION_URL]: [guestReply()] });
    const { moves, events } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, moves);
    expect(events).toEqual(["replace /login"]);
  });

  // 同一条「重读得到 failed（含网络中断）时保留原令牌并显示 common.error_retry」：
  it.each<[string, () => Reply]>([
    ["failed", () => new Response(null, { status: 500 })],
    ["network", () => "network"],
  ])("keeps the old token when the reread after csrf is %s", async (_name, reply) => {
    stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [csrfReply()], [VIEW_EN]: [orderReply()], [MEMBER_SESSION_URL]: [reply()] });
    const { moves, events, current } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, moves);
    expect(events).toEqual(["update null"]);
    expect(current()).toEqual(signedIn({ failed: true }));
    expect(has(memberView(current()), "common.error_retry")).toBe(true);
  });

  // 验收第 4 条「read、error 与 retry 与查单模式相同」：其他失败（如 500）显示 common.error_retry，订单沿用原来的，下一次另生成幂等键。
  it("keeps the order and shows common.error_retry after another failure", async () => {
    stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [new Response(null, { status: 500 })] });
    const { moves, current } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, moves);
    expect(current()).toEqual(signedIn({ failed: true }));
  });

  // 同一条与 UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单…再允许重试」：网络中断时先进入 checking（显示 common.network_check），
  // 重新读取到仍为 demo_shipped 时显示 common.error_retry 并记下同一幂等键；已不是 demo_shipped 时按读取到的订单显示，不再沿用键。
  it("checks the order after a network failure and keeps the key only while still shipped", async () => {
    stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: ["network", "network"], [VIEW_EN]: [orderReply(), orderReply(completed)] });
    const shipped = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, shipped.moves);
    expect(shipped.events).toEqual(["update checking", "update null"]);
    expect(has(memberView(signedIn({ busy: "checking" })), "common.network_check")).toBe(true);
    expect(shipped.current()).toEqual(signedIn({ failed: true, retryKey: KEY }));
    const done = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, new AbortController().signal, done.moves);
    expect(done.current()).toEqual(signedIn({ order: completed }));
  });

  // 验收第 6 条「离开页面时中止请求，之后不再更新」：提交回来时已离开，不更新、不再读取。
  it("stops after leaving the page", async () => {
    const calls = stubFetch({ [MEMBER_CONFIRM_RECEIPT_URL]: [json(200, {})] });
    const gone = new AbortController();
    gone.abort();
    const { moves, events } = fakeMoves(signedIn({ busy: "confirming" }));
    await confirmMemberOrder(createMemberSessionReader(), "en", target, gone.signal, moves);
    expect(events).toEqual([]);
    expect(calls).toHaveLength(1);
  });
});

describe("coupon and points rows", () => {
  const rowsOf = (amounts: string) =>
    [...amounts.matchAll(/<div class="acs-row[^"]*"><span>([^<]*)<\/span><span class="acs-num">([^<]*)<\/span><\/div>/g)].map((m) => [unescapeHtml(m[1] ?? ""), m[2] ?? ""]);
  const priceIn = (language: Language, amount: string) => formatCopy(COPY["common.price_myr"][language], { amount });
  // 视觉稿 P09-desktop 的金额：小计 RM 110.00、优惠券 RM 10.00、积分 500（RM 5.00）、运费 RM 8.00、合计 RM 103.00。
  const placed = lookupOrder({ placed_by_member: true, coupon_discount_sen: 1000, points_redeemed: 500, total_sen: 10300 });
  // 两种模式各渲染一次。
  const both = (order: LookupOrder, language: Language) => [render(ready(order), language), memberView(signedIn({ order }), language)];

  // UX P09 线框金额明细「[cart.subtotal] [checkout.summary_coupon] [checkout.summary_points] [checkout.summary_shipping] [checkout.summary_total]」、
  // M3「…查单模式与会员模式相同」与 HANDOFF 0.61 第（8）条「金额为正数、不加负号，积分抵扣按仙数格式化为金额（100 积分抵 RM 1）」（视觉稿 P09-desktop）：
  // 下单时是会员的订单在两种模式、桌面与手机折叠里都是五行，按此顺序，金额不带负号。
  it.each(LANGUAGES)("shows five rows for an order placed by a member in both modes in %s", (language) => {
    const keys: CopyKey[] = ["cart.subtotal", "checkout.summary_coupon", "checkout.summary_points", "checkout.summary_shipping", "checkout.summary_total"];
    const amounts = ["110.00", "10.00", "5.00", "8.00", "103.00"];
    const expected = keys.map((key, index) => [COPY[key][language], priceIn(language, amounts[index] ?? "")]);
    for (const html of both(placed, language)) {
      for (const block of [desktopAmounts(html), phoneAmounts(html)]) {
        expect(rowsOf(block)).toEqual(expected);
      }
    }
  });

  // HANDOFF 0.61 第（8）条「券与积分两行在会员下单的订单上即使为 0 也显示」：
  it("shows the two rows at zero", () => {
    const zero = lookupOrder({ placed_by_member: true, coupon_discount_sen: 0, points_redeemed: 0 });
    for (const html of both(zero, "en")) {
      for (const block of [desktopAmounts(html), phoneAmounts(html)]) {
        const rows = rowsOf(block);
        expect(rows[1]).toEqual([COPY["checkout.summary_coupon"].en, "RM 0.00"]);
        expect(rows[2]).toEqual([COPY["checkout.summary_points"].en, "RM 0.00"]);
      }
    }
  });

  // UX P09 M3「游客订单（下单时不是会员的，含之后被认领的）不显示优惠与积分两行，查单模式与会员模式相同」与验收第 5 条「placed_by_member 为假…时仍只有三行」：
  // 会员模式里的认领订单同样只有三行，快照里有非零的数也不显示。
  it.each(LANGUAGES)("keeps three rows for a guest order in both modes in %s", (language) => {
    const claimed = lookupOrder({ placed_by_member: false, coupon_discount_sen: 1000, points_redeemed: 500 });
    const labels = (["cart.subtotal", "checkout.summary_shipping", "checkout.summary_total"] as const).map((key) => COPY[key][language]);
    for (const html of both(claimed, language)) {
      for (const block of [desktopAmounts(html), phoneAmounts(html)]) {
        expect(rowsOf(block).map(([label]) => label)).toEqual(labels);
      }
      expect(has(html, "checkout.summary_coupon", language)).toBe(false);
      expect(has(html, "checkout.summary_points", language)).toBe(false);
    }
  });

  // 验收第 5 条「金额原样格式化…不在浏览器计算」与 DESIGN 1.13「计价、优惠、积分与库存」第 7 条（已有订单按快照）：五行照接口的数显示，
  // 彼此对不上也不改；积分数原样当作仙数（1 积分即 RM 0.01）。
  it("shows every amount exactly as the order returns it", () => {
    const odd = lookupOrder({ placed_by_member: true, subtotal_sen: 10000, coupon_discount_sen: 1234, points_redeemed: 1, shipping_fee_sen: 1000, total_sen: 99999 });
    for (const html of both(odd, "en")) {
      for (const block of [desktopAmounts(html), phoneAmounts(html)]) {
        expect(rowsOf(block).map(([, amount]) => amount)).toEqual(["RM 100.00", "RM 12.34", "RM 0.01", "RM 10.00", "RM 999.99"]);
      }
    }
  });
});
