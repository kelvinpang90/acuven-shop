import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { clearMemberOrder, estimateMemberRefundPoints, handOffMemberOrder, MEMBER_POINTS_ESTIMATE_URL, MEMBER_REFUNDS_URL, memberOrderViewUrl } from "../api/memberOrders";
import { createMemberSessionReader, MEMBER_SESSION_URL } from "../api/memberSession";
import { clearSubmittedRefund, estimateRefundPoints, POINTS_ESTIMATE_URL, refundKey, submittedRefundOrder } from "../api/orderLookup";
import type { LookupLine, LookupOrder, LookupRefund, PointsEstimate } from "../api/orderLookup";
import { CSRF_HEADER, IDEMPOTENCY_HEADER } from "../api/pay";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import {
  chooseQuantity,
  estimatePoints,
  loadMemberRefundOrder,
  MEMBER_REFUND_LOADING,
  memberRefundScreen,
  openMemberRefund,
  openRefundStep,
  pointsRequest,
  RefundView,
  sendMemberRefund,
  shownPoints,
  startPointsEstimate,
  startRefund,
} from "./RefundPage";
import type { MemberRefundMoves, MemberRefundState, MemberRefundTarget, PointsEstimator, PointsRequest, PointsResult, RefundViewProps } from "./RefundPage";

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";

function orderLine(overrides: Partial<LookupLine> = {}): LookupLine {
  return {
    line_index: 0,
    name: "Crew Neck Tee",
    variant_label: "Black, M",
    quantity: 3,
    unit_price_sen: 3900,
    line_subtotal_sen: 11700,
    unit_cash_paid_sen: [3369, 3368, 3368],
    // 第一件已在申请中：还可退 2 件。预计金额列表由接口给出，故意不等于逐件实付之和（页面只取列表里的数）。
    refundable_quantity: 2,
    refund_estimates_sen: [3000, 6500],
    ...overrides,
  };
}

// 三行：T 恤可退 2 件；蜡烛可退 1 件；托特包已全部在申请中，可退 0 件。
const LINES: LookupLine[] = [
  orderLine(),
  orderLine({ line_index: 1, name: "Soy Wax Candle", variant_label: "", quantity: 1, unit_price_sen: 3200, line_subtotal_sen: 3200, unit_cash_paid_sen: [2763], refundable_quantity: 1, refund_estimates_sen: [2763] }),
  orderLine({ line_index: 2, name: "Canvas Tote", variant_label: "Natural", quantity: 1, unit_price_sen: 2500, line_subtotal_sen: 2500, unit_cash_paid_sen: [2159], refundable_quantity: 0, refund_estimates_sen: [] }),
];

function lookupOrder(overrides: Partial<LookupOrder> = {}): LookupOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "demo_shipped",
    created_at: "2026-10-05T10:00:00Z",
    paid_at: "2026-10-05T10:02:00Z",
    server_time: "2026-10-05T11:00:00Z",
    lines: LINES,
    subtotal_sen: 17400,
    placed_by_member: false,
    coupon_discount_sen: 0,
    points_redeemed: 0,
    shipping_fee_sen: 800,
    total_sen: 18200,
    recipient: null,
    refund_deadline: "2026-11-04T10:02:00Z",
    refund_window_open: true,
    refunded_total_sen: 0,
    refundable_left_sen: 9500,
    fully_refunded: false,
    refund_requests: [],
    ...overrides,
  };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function props(overrides: Partial<RefundViewProps> = {}): RefundViewProps {
  return { screen: { status: "ready", order: lookupOrder() }, quantities: [0, 0, 0], busy: null, notice: null, onQuantity: noop, onSubmit: noop, ...overrides };
}

function wrap(children: ReactNode, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/track/order/refund">{children}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<RefundViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<RefundView {...props(overrides)} />, language));
}

// RefundView 返回的元素树（不展开其中的子组件），用来取按钮与各行的处理函数；仍在语言与路由的上下文里渲染。
function tree(overrides: Partial<RefundViewProps> = {}): ReactNode {
  const captured: ReactNode[] = [];
  const probe = () => {
    captured.push(RefundView(props(overrides)));
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

function submitButtons(node: ReactNode): AnyElement[] {
  return elements(node).filter((element) => element.type === "button" && element.props.children === COPY["refund.submit"].en);
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

function copy(key: CopyKey, language: Language = "en", vars?: Record<string, string | number>): string {
  return escapeHtml(formatCopy(COPY[key][language], vars));
}

function has(html: string, key: CopyKey, language: Language = "en"): boolean {
  return html.includes(`>${copy(key, language)}<`);
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const rows = (html: string) => [...html.matchAll(/<li class="site-refund__line">[\s\S]*?<\/li>/g)].map((m) => m[0]);
const desktopSummary = (html: string) => part(html, /<section class="acs-summary site-desktop-only site-refund__summary">[\s\S]*?<\/section>/);
const phoneBar = (html: string) => part(html, /<div class="acs site-phone-only site-refund__bar">[\s\S]*?<\/button><\/div>/);
const estimate = (html: string, amount: string, language: Language = "en") => html.includes(`>${copy("refund.estimate", language, { amount })}<`);

describe("page", () => {
  // SHOP-TASK-030 验收第 4 条「按线框显示 refund.title、order.title（含订单号）、★ refund.demo_hint、order.lookup_access、refund.select_items、各行名称与规格、
  // order.cash_paid…、detail.quantity 加减控件…与 refund.max_qty、refund.estimate、refund.shipping_not_refunded、refund.coupon_not_restored、refund.submit 与 ◆ refund.submit_hint」。
  it.each(LANGUAGES)("shows every element of the lookup-mode wireframe in %s", (language) => {
    const html = render({}, language);
    expect(html).toContain(`<h1 class="acs-display-l">${copy("refund.title", language)}</h1><span class="acs-num acs-muted">${copy("order.title", language, { orderNo: ORDER_NUMBER })}</span>`);
    const keys: CopyKey[] = [
      "refund.demo_hint",
      "order.lookup_access",
      "refund.select_items",
      "order.cash_paid",
      "detail.quantity",
      "refund.shipping_not_refunded",
      "refund.coupon_not_restored",
      "refund.submit",
      "refund.submit_hint",
    ];
    for (const key of keys) {
      expect(has(html, key, language), key).toBe(true);
    }
    expect(estimate(html, "0.00", language)).toBe(true);
    // ★ 与 ◆ 两种提示标记。
    expect(html).toContain(`<path d="M12 2.8l2.8 5.8 6.3.9-4.6 4.4 1.1 6.3L12 17.2l-5.6 3 1.1-6.3L2.9 9.5l6.3-.9z"></path></svg><span>${copy("refund.demo_hint", language)}</span>`);
    expect(html).toContain(`<path d="M12 3l9 9-9 9-9-9z"></path></svg><span>${copy("refund.submit_hint", language)}</span>`);
    const [tee, candle, tote] = rows(html);
    expect(tee).toMatch(/<span>Crew Neck Tee<\/span><span class="site-order__option"><svg[^>]*aria-hidden="true">[\s\S]*?<\/svg><span>Black, M<\/span><\/span>/);
    expect(candle).toContain(`<span class="site-order__name"><span>Soy Wax Candle</span></span>`);
    expect(tote).toContain(`<span>Canvas Tote</span>`);
    expect(tee).toContain(`>${copy("refund.max_qty", language, { count: 2 })}<`);
  });

  // UX P10 桌面线框的顺序：[refund.title] [order.title] ★ [refund.demo_hint]，[order.lookup_access]，[refund.select_items] 与各行（名称、[order.cash_paid]、[detail.quantity] (-) 0 (+) [refund.max_qty]），
  // [refund.estimate]，[refund.shipping_not_refunded] [refund.coupon_not_restored]，( [refund.submit] )，◆ [refund.submit_hint]。只看桌面部分（去掉手机才显示的说明与底部栏）。
  it("follows the order of the wireframe", () => {
    const html = render()
      .replace(/<div class="site-phone-only site-refund__notes">[\s\S]*?<\/div>/, "")
      .replace(/<div class="acs site-phone-only site-refund__bar">[\s\S]*$/, "");
    const positions = [
      html.indexOf(`>${COPY["refund.title"].en}<`),
      html.indexOf(`Order ${ORDER_NUMBER}`),
      html.indexOf(COPY["refund.demo_hint"].en),
      html.indexOf(COPY["order.lookup_access"].en),
      html.indexOf(COPY["refund.select_items"].en),
      html.indexOf("Crew Neck Tee"),
      html.indexOf(COPY["order.cash_paid"].en),
      html.indexOf(`>${COPY["detail.quantity"].en}<`),
      html.indexOf(COPY["common.a11y_qty_decrease"].en),
      html.indexOf(COPY["common.a11y_qty_increase"].en),
      html.indexOf("Up to 2"),
      html.indexOf("Soy Wax Candle"),
      html.indexOf("Estimated refund (demo)"),
      html.indexOf(COPY["refund.shipping_not_refunded"].en),
      html.indexOf(COPY["refund.coupon_not_restored"].en),
      html.indexOf(`>${COPY["refund.submit"].en}<`),
      html.indexOf(COPY["refund.submit_hint"].en),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // SHOP-TASK-030 验收第 4 条「不显示积分三行（游客订单）」与第 6 条「会员模式（account.orders 与积分行）…不渲染」：refund.points_back、refund.points_reversed、
  // refund.expired_points_note 三语原文都不出现，也没有 account.orders。
  // SHOP-TASK-155 验收第 7 条：三个键已加入字典，原来断言字典没有这三个键的三行改为断言游客订单（placed_by_member 为假）即使给了预估也不显示三行。
  it.each(LANGUAGES)("has no points rows and no member-mode link in %s", (language) => {
    expect(lookupOrder().placed_by_member).toBe(false);
    const guestWithPoints = render({ quantities: [2, 1, 0], points: { memberOrder: true, pointsBack: 146, pointsReversed: 27 } }, language);
    expect(guestWithPoints).not.toContain(copy("refund.expired_points_note", language));
    const html = render({ quantities: [2, 1, 0] }, language);
    const texts = [
      "Points returned",
      "返还积分",
      "Mata dikembalikan",
      "Points taken back",
      "追回积分",
      "Mata ditarik balik",
      "Points that have already expired are not returned.",
      "已过期的积分不返还。",
      "Mata yang telah tamat tempoh tidak dikembalikan.",
      "My orders",
    ];
    for (const text of texts) {
      expect(html).not.toContain(text);
    }
    expect(html).not.toContain("href=");
  });

  // SHOP-TASK-030 验收第 4 条「手机上合计与提交按钮固定在底部」与 UX P10 手机线框「底部固定：[refund.estimate] … ◆ [refund.submit_hint] ( [refund.submit] )」：
  // 手机底部栏依次为合计、◆ 提示、提交按钮；运费与优惠券说明在各行之后（手机）；桌面为汇总块与其后的提交按钮。按宽度只显示其一（site.css）。
  it("pins the estimate and the submit button to the bottom on phones", () => {
    const html = render({ quantities: [1, 0, 0] });
    const bar = phoneBar(html);
    const positions = [bar.indexOf("Estimated refund (demo): RM 30.00"), bar.indexOf(COPY["refund.submit_hint"].en), bar.indexOf(`>${COPY["refund.submit"].en}<`)];
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    expect(html).toContain(`<div class="site-phone-only site-refund__notes"><p class="acs-body-s acs-muted">${copy("refund.shipping_not_refunded")}</p><p class="acs-body-s acs-muted">${copy("refund.coupon_not_restored")}</p></div>`);
    expect(desktopSummary(html)).toContain("Estimated refund (demo): RM 30.00");
    expect(html).toContain(`<div class="site-desktop-only site-refund__submit"><button class="acs-btn acs-btn--primary acs-btn--lg"`);
    expect(html.indexOf("site-refund__lines")).toBeLessThan(html.indexOf("site-refund__notes"));
  });
});

describe("quantities", () => {
  const stepper = (row: string) => part(row, /<div class="acs-stepper"[\s\S]*?<\/div>/);
  const decrease = (disabled: boolean) => `<button type="button" aria-label="${COPY["common.a11y_qty_decrease"].en}"${disabled ? ` disabled=""` : ""}>`;
  const increase = (disabled: boolean) => `<button type="button" aria-label="${COPY["common.a11y_qty_increase"].en}"${disabled ? ` disabled=""` : ""}>`;

  // SHOP-TASK-030 验收第 4 条「detail.quantity 加减控件（0 到该行可退件数）与 refund.max_qty」：件数上限等于该行可退件数（T 恤买 3 件、可退 2 件时为 2），
  // 0 件时 (-) 禁用，到可退件数时 (+) 禁用；refund.max_qty 显示可退件数而不是购买件数；值在 output 里。
  it("limits each line from zero to its refundable quantity", () => {
    const [atZero] = rows(render({ quantities: [0, 0, 0] }));
    expect(stepper(atZero ?? "")).toContain(decrease(true));
    expect(stepper(atZero ?? "")).toContain(increase(false));
    expect(atZero).toContain(`<output aria-live="polite">0</output>`);
    const [atOne] = rows(render({ quantities: [1, 0, 0] }));
    expect(stepper(atOne ?? "")).toContain(decrease(false));
    expect(stepper(atOne ?? "")).toContain(increase(false));
    const [atMax, candle] = rows(render({ quantities: [2, 1, 0] }));
    expect(stepper(atMax ?? "")).toContain(decrease(false));
    expect(stepper(atMax ?? "")).toContain(increase(true));
    expect(atMax).toContain(`<span class="acs-caption site-refund__max">${copy("refund.max_qty", "en", { count: 2 })}</span>`);
    expect(atMax).not.toContain(copy("refund.max_qty", "en", { count: 3 }));
    expect(stepper(candle ?? "")).toContain(increase(true));
    expect(candle).toContain(copy("refund.max_qty", "en", { count: 1 }));
  });

  // SHOP-TASK-030 验收第 4 条「可退件数为 0 的行不可选」：两个按钮都禁用，上限显示 0。
  it("does not let a line with nothing refundable be chosen", () => {
    const tote = rows(render())[2] ?? "";
    expect(stepper(tote)).toContain(decrease(true));
    expect(stepper(tote)).toContain(increase(true));
    expect(tote).toContain(copy("refund.max_qty", "en", { count: 0 }));
  });

  // SHOP-TASK-030 验收第 5 条「提交期间按钮禁用」（派生：加减控件同样禁用，提交内容在请求期间不变）：提交与确认期间各行加减都禁用。
  it.each(["submitting", "checking"] as const)("locks the quantities while %s", (busy) => {
    for (const row of rows(render({ busy, quantities: [1, 0, 0] }))) {
      expect(stepper(row)).toContain(decrease(true));
      expect(stepper(row)).toContain(increase(true));
    }
  });

  // 派生实现约束（实现选择）：守住第 4 条「detail.quantity 加减控件」——每行的加减交给页面，带该行在订单行中的位置。
  it("hands quantity changes to the page with the line position", () => {
    const onQuantity = vi.fn();
    const lineRows = elements(tree({ onQuantity, quantities: [1, 0, 0] })).filter((element) => element.props.line !== undefined);
    expect(lineRows).toHaveLength(3);
    (lineRows[1]?.props.onQuantity as (quantity: number) => void)(1);
    expect(onQuantity).toHaveBeenCalledWith(1, 1);
    expect(lineRows.map((row) => row.props.quantity)).toEqual([1, 0, 0]);
  });
});

describe("estimate and submit", () => {
  // SHOP-TASK-030 验收第 5 条「refund.estimate 的金额取各行预计金额列表中对应件数的那一项再相加（只做整数仙加法，不做乘除或分摊）」与第 7 条「预计金额来自接口给出的列表并只做加法」：
  // T 恤 2 件取列表第 2 项 6500（不是逐件实付 3369 + 3368，也不是第 1 项 3000 的两倍），加上蜡烛 1 件 2763。
  it.each(LANGUAGES)("adds the listed estimates in %s", (language) => {
    const html = render({ quantities: [2, 1, 0] }, language);
    expect(estimate(html, "92.63", language)).toBe(true);
    for (const computed of ["67.37", "60.00", "94.00", "95.00"]) {
      expect(estimate(html, computed, language)).toBe(false);
    }
    expect(estimate(render({ quantities: [1, 0, 0] }, language), "30.00", language)).toBe(true);
  });

  // SHOP-TASK-030 验收第 7 条「金额原样来自接口」与 UX P10 M1「每件…实付快照」：order.cash_paid 之后逐件显示接口的实付金额，不相加成行合计。
  it("shows the amount paid for each item as returned", () => {
    const [tee, candle] = rows(render());
    expect(tee).toContain(`<span class="acs-body-s acs-muted site-refund__paid"><span>${copy("order.cash_paid")}</span><span class="acs-num">RM 33.69</span><span class="acs-num">RM 33.68</span><span class="acs-num">RM 33.68</span></span>`);
    expect(candle).toContain(`<span class="acs-num">RM 27.63</span>`);
    expect(render()).not.toContain("RM 101.05");
  });

  // SHOP-TASK-030 验收第 5 条「全部为 0 件时提交按钮禁用」「提交期间按钮禁用」：桌面与手机两个提交按钮都按同一规则；有件数且空闲时可点并交给页面。
  it("enables submitting only with something chosen and nothing in progress", () => {
    for (const button of submitButtons(tree({ quantities: [0, 0, 0] }))) {
      expect(button.props.disabled).toBe(true);
    }
    for (const busy of ["submitting", "checking"] as const) {
      for (const button of submitButtons(tree({ busy, quantities: [1, 0, 0] }))) {
        expect(button.props.disabled).toBe(true);
      }
    }
    const onSubmit = vi.fn();
    const ready = submitButtons(tree({ onSubmit, quantities: [0, 1, 0] }));
    expect(ready).toHaveLength(2);
    for (const button of ready) {
      expect(button.props.disabled).toBe(false);
      (button.props.onClick as () => void)();
    }
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  // 派生实现约束（实现选择）：守住第 5 条「只做整数仙加法」——件数超出接口列表时没有金额，不显示合计也不能提交。
  it("shows no estimate and blocks submitting beyond the listed quantities", () => {
    const html = render({ screen: { status: "ready", order: lookupOrder({ lines: [orderLine({ refundable_quantity: 3 })] }) }, quantities: [3] });
    expect(html).not.toContain("Estimated refund");
    for (const button of submitButtons(tree({ screen: { status: "ready", order: lookupOrder({ lines: [orderLine({ refundable_quantity: 3 })] }) }, quantities: [3] }))) {
      expect(button.props.disabled).toBe(true);
    }
  });
});

describe("notices", () => {
  // SHOP-TASK-030 验收第 5 条「409 refund_duplicate、refund_nothing_left、refund_window_closed 分别显示 refund.duplicate、refund.nothing_left、refund.window_closed」与 UX P10「错误：…」：
  // 各自的文案在危险提示条里，位于 ◆ 提示之后；其他两种不出现。
  it.each(LANGUAGES)("shows each refusal in %s", (language) => {
    const reasons = [
      ["duplicate", "refund.duplicate"],
      ["nothing_left", "refund.nothing_left"],
      ["window_closed", "refund.window_closed"],
    ] as const;
    for (const [reason, key] of reasons) {
      const html = render({ notice: reason }, language);
      expect(html).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy(key, language)}</p>`);
      expect(html.indexOf(copy(key, language))).toBeGreaterThan(html.indexOf(copy("refund.submit_hint", language)));
      for (const [, other] of reasons.filter(([, candidate]) => candidate !== key)) {
        expect(html).not.toContain(copy(other, language));
      }
      expect(has(html, "common.error_retry", language)).toBe(false);
    }
  });

  // SHOP-TASK-030 验收第 5 条「409 idempotency_conflict…显示 common.error_retry」「403 csrf_failed…显示 common.error_retry」「网络中断时显示 common.network_check」：
  // 失败后显示 common.error_retry；确认订单期间只显示 common.network_check；平时都不显示。
  it("shows common.error_retry after a failure and common.network_check while checking", () => {
    expect(has(render({ notice: "error" }), "common.error_retry")).toBe(true);
    const checking = render({ busy: "checking", notice: "error" });
    expect(checking).toContain(`<span>${copy("common.network_check")}</span>`);
    expect(has(checking, "common.error_retry")).toBe(false);
    const idle = render();
    expect(has(idle, "common.error_retry")).toBe(false);
    expect(idle).not.toContain(copy("common.network_check"));
  });
});

describe("session expired, loading and errors", () => {
  // SHOP-TASK-030 验收第 4 条「401 时整页显示 order.session_expired 与 common.nav_track」与 UX P10「过期显示 [order.session_expired]」：只有提示与链到 /track 的按钮，没有订单内容或表单控件。
  it.each(LANGUAGES)("replaces the whole page with order.session_expired in %s", (language) => {
    const html = render({ screen: { status: "expired" } }, language);
    expect(visibleTexts(html)).toEqual([COPY["order.session_expired"][language], COPY["common.nav_track"][language]]);
    expect(html).toContain(`<a class="acs-btn acs-btn--primary" href="/track">${COPY["common.nav_track"][language]}</a>`);
    expect(html).not.toContain("<button");
  });

  // 派生实现约束（实现选择）：守住第 4 条「P10 打开时调用 GET /api/orders/lookup」——接口返回之前主体为空并标 aria-busy；读取失败时只显示 common.error_retry。
  it("shows nothing before the order arrives and an error when reading fails", () => {
    expect(render({ screen: { status: "loading" } })).toBe(`<main class="site-refund" aria-busy="true"></main>`);
    expect(visibleTexts(render({ screen: { status: "error" } }))).toEqual([COPY["common.error_retry"].en]);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-030 验收第 7 条「页面文字全部来自字典」：各状态下，除订单数据（商品名与规格、件数）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const states: Partial<RefundViewProps>[] = [
      {},
      { quantities: [2, 1, 0] },
      { busy: "submitting", quantities: [1, 0, 0] },
      { busy: "checking", quantities: [1, 0, 0] },
      { notice: "duplicate" },
      { notice: "nothing_left" },
      { notice: "window_closed" },
      { notice: "error" },
      { screen: { status: "expired" } },
      { screen: { status: "error" } },
    ];
    const orderData = new Set(["Crew Neck Tee", "Black, M", "Soy Wax Candle", "Canvas Tote", "Natural"]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        const known = orderData.has(value) || /^\d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

// 以下为 SHOP-TASK-155 的积分三行（两种模式）与会员模式（/account/order/refund）。令牌与幂等键都是虚构值。
const TOKEN = "csrf-token-1";
const NEW_TOKEN = "csrf-token-2";
const KEY = "refund-key-1";
const MASKED = "+60 12•••• 678";
const VIEW_EN = memberOrderViewUrl("en");
const POINTS: PointsEstimate = { memberOrder: true, pointsBack: 146, pointsReversed: 27 };
// 下单时是会员的订单。
const MEMBER_ORDER = lookupOrder({ placed_by_member: true });
const CHOSEN = [{ line_index: 0, quantity: 1 }];
const REQUEST: LookupRefund = {
  created_at: "2026-10-06T09:00:00Z",
  status: "requested",
  amount_sen: 3000,
  lines: [{ name: "Crew Neck Tee", variant_label: "Black, M", quantity: 1, amount_sen: 3000 }],
};

afterEach(() => {
  vi.unstubAllGlobals();
  clearMemberOrder();
  clearSubmittedRefund();
});

// 下单时是会员的订单、选了 T 恤 1 件、给了预估的 View 属性。
function withPoints(overrides: Partial<RefundViewProps> = {}): Partial<RefundViewProps> {
  return { screen: { status: "ready", order: MEMBER_ORDER }, quantities: [1, 0, 0], points: POINTS, ...overrides };
}

// 三行都不在页面上：没有 refund.expired_points_note，也没有任何文字匹配两个带 {points} 的模板。
const noPointsText = (html: string, language: Language = "en") =>
  !html.includes(copy("refund.expired_points_note", language)) &&
  !visibleTexts(html).some((value) => copyPattern(COPY["refund.points_back"][language]).test(value) || copyPattern(COPY["refund.points_reversed"][language]).test(value));

function requestFor(order: LookupOrder, quantities: readonly number[]): PointsRequest {
  const request = pointsRequest(order, quantities);
  if (request === null) {
    throw new Error("no request");
  }
  return request;
}

type SignedIn = Extract<MemberRefundState, { status: "member" }>;

// 已登录、已取到订单、选了 T 恤 1 件的会员模式状态。
function signedIn(overrides: Partial<SignedIn> = {}): SignedIn {
  return { status: "member", orderNumber: ORDER_NUMBER, csrfToken: TOKEN, order: MEMBER_ORDER, quantities: [1, 0, 0], busy: null, notice: null, retry: null, ...overrides };
}

// 照 MemberRefundPage 把会员模式的状态交给 View 渲染。
function memberView(state: MemberRefundState, language: Language = "en", points: PointsEstimate | null = null): string {
  const member = state.status === "member" ? state : null;
  return render(
    {
      screen: memberRefundScreen(state),
      quantities: member === null ? [] : member.quantities,
      busy: member === null ? null : member.busy,
      notice: member === null ? null : member.notice,
      mode: "member",
      points,
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
const orderReply = (order: LookupOrder = MEMBER_ORDER) => json(200, order);
const pointsReply = (back: number, reversed: number) => json(200, { member_order: true, points_back: back, points_reversed: reversed });

function initOf(calls: readonly Call[], index: number): RequestInit {
  return calls[index]?.init ?? {};
}

function headerOf(init: RequestInit, name: string): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.[name];
}

function bodyOf(init: RequestInit): unknown {
  return JSON.parse(typeof init.body === "string" ? init.body : "null") as unknown;
}

// 页面的处理替身：按顺序记下 show、update（及改动后的 busy）、replace 与 navigate，并按它们推演页面状态。
function fakeMoves(initial: MemberRefundState = MEMBER_REFUND_LOADING) {
  const events: string[] = [];
  let state = initial;
  const moves: MemberRefundMoves = {
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
    navigate: (path) => {
      events.push(`navigate ${path}`);
    },
  };
  return { moves, events, current: () => state };
}

function memberState(state: MemberRefundState): SignedIn {
  if (state.status !== "member") {
    throw new Error(`not signed in: ${state.status}`);
  }
  return state;
}

describe("points rows", () => {
  // UX P10 桌面线框「[refund.estimate] / 会员订单：[refund.points_back] [refund.points_reversed] / [refund.expired_points_note] /
  // [refund.shipping_not_refunded] [refund.coupon_not_restored]」与视觉稿 P10-desktop-member：桌面三行在合计之下、两句说明之前，两个数在同一行；两种模式相同。
  it.each(LANGUAGES)("puts the points rows under the estimate on desktops in %s", (language) => {
    for (const mode of ["lookup", "member"] as const) {
      const summary = desktopSummary(render(withPoints({ mode }), language));
      expect(summary).toContain(
        `<div class="acs-num site-refund__points"><span>${copy("refund.points_back", language, { points: 146 })}</span><span>${copy("refund.points_reversed", language, { points: 27 })}</span></div>`,
      );
      const positions = [
        summary.indexOf(copy("refund.estimate", language, { amount: "30.00" })),
        summary.indexOf(copy("refund.points_back", language, { points: 146 })),
        summary.indexOf(copy("refund.points_reversed", language, { points: 27 })),
        summary.indexOf(copy("refund.expired_points_note", language)),
        summary.indexOf(copy("refund.shipping_not_refunded", language)),
        summary.indexOf(copy("refund.coupon_not_restored", language)),
      ];
      expect(positions.every((position) => position >= 0)).toBe(true);
      expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    }
  });

  // UX P10 手机线框「[refund.shipping_not_refunded] [refund.coupon_not_restored] [refund.expired_points_note]」与「底部固定：[refund.estimate]
  // [refund.points_back] [refund.points_reversed] ◆ [refund.submit_hint] ( [refund.submit] )」：说明在各行之后，两个数在底部栏合计之下、◆ 提示之前，各占一行。
  it.each(LANGUAGES)("puts the note after the lines and the numbers under the total on phones in %s", (language) => {
    for (const mode of ["lookup", "member"] as const) {
      const html = render(withPoints({ mode }), language);
      expect(html).toContain(
        `<div class="site-phone-only site-refund__notes"><p class="acs-body-s acs-muted">${copy("refund.shipping_not_refunded", language)}</p><p class="acs-body-s acs-muted">${copy("refund.coupon_not_restored", language)}</p><p class="acs-body-s acs-muted">${copy("refund.expired_points_note", language)}</p></div>`,
      );
      const bar = phoneBar(html);
      expect(bar).toContain(
        `<div class="acs-num acs-body-s site-refund__bar-points"><span>${copy("refund.points_back", language, { points: 146 })}</span><span>${copy("refund.points_reversed", language, { points: 27 })}</span></div>`,
      );
      const positions = [
        bar.indexOf(copy("refund.estimate", language, { amount: "30.00" })),
        bar.indexOf(copy("refund.points_back", language, { points: 146 })),
        bar.indexOf(copy("refund.points_reversed", language, { points: 27 })),
        bar.indexOf(copy("refund.submit_hint", language)),
        bar.indexOf(`>${copy("refund.submit", language)}<`),
      ];
      expect(positions.every((position) => position >= 0)).toBe(true);
      expect([...positions].sort((a, b) => a - b)).toEqual(positions);
      expect(bar).not.toContain(copy("refund.expired_points_note", language));
    }
  });

  // UX P10 说明「积分行只在该单属于会员（下单时为会员）时显示；由会员认领的游客订单…服务端返回零时不显示积分行」与 HANDOFF 0.61 第（9）条
  // 「预估失败…不显示积分行…两个数都为 0 时不显示三行」：两个数为 0、member_order 为假、没有预估（失败或尚未返回）、游客订单都没有三行，提交按钮照常可用。
  it.each<[string, Partial<RefundViewProps>]>([
    ["both numbers are zero", { points: { memberOrder: true, pointsBack: 0, pointsReversed: 0 } }],
    ["member_order is false", { points: { memberOrder: false, pointsBack: 146, pointsReversed: 27 } }],
    ["there is no estimate", { points: null }],
    ["the order was not placed by a member", { screen: { status: "ready", order: lookupOrder() } }],
  ])("shows no points rows when %s", (_name, overrides) => {
    for (const mode of ["lookup", "member"] as const) {
      const html = render(withPoints({ mode, ...overrides }));
      expect(noPointsText(html)).toBe(true);
      expect(html).not.toContain("site-refund__points");
      expect(html).not.toContain("site-refund__bar-points");
      for (const button of submitButtons(tree(withPoints({ mode, ...overrides })))) {
        expect(button.props.disabled).toBe(false);
      }
    }
  });

  // 验收第 4 条「{points} 为接口给的整数，原样显示」：不加千位分隔；只有一个数为 0 时三行照常显示，0 原样显示。
  it("shows the numbers exactly as returned", () => {
    const html = render(withPoints({ points: { memberOrder: true, pointsBack: 1000, pointsReversed: 0 } }));
    expect(html).toContain(`<span>${copy("refund.points_back", "en", { points: 1000 })}</span>`);
    expect(html).toContain("Points returned: 1000<");
    expect(html).not.toContain("1,000");
    expect(html).toContain(`<span>${copy("refund.points_reversed", "en", { points: 0 })}</span>`);
    expect(html).toContain(copy("refund.expired_points_note"));
  });
});

describe("points requests", () => {
  // HANDOFF 0.61 第（9）条「P10 的积分预估只对 placed_by_member 为真的订单请求」与验收第 4 条「placed_by_member 为假（游客订单与后来被认领的游客订单）
  // 或没有选择时不请求」：游客订单与认领单（两者 placed_by_member 都为假）、全部为 0 件、订单还没取到都不请求；会员下单的订单按所选件数请求，件数不同 key 不同。
  it("requests an estimate only for orders placed by a member with something chosen", () => {
    expect(pointsRequest(lookupOrder(), [2, 1, 0])).toBeNull();
    expect(pointsRequest(lookupOrder({ placed_by_member: false, refund_requests: [REQUEST] }), [1, 0, 0])).toBeNull();
    expect(pointsRequest(MEMBER_ORDER, [0, 0, 0])).toBeNull();
    expect(pointsRequest(null, [1, 0, 0])).toBeNull();
    const request = requestFor(MEMBER_ORDER, [2, 1, 0]);
    expect(request.orderNumber).toBe(ORDER_NUMBER);
    expect(request.lines).toEqual([
      { line_index: 0, quantity: 2 },
      { line_index: 1, quantity: 1 },
    ]);
    expect(requestFor(MEMBER_ORDER, [2, 1, 0]).key).toBe(request.key);
    expect(requestFor(MEMBER_ORDER, [1, 1, 0]).key).not.toBe(request.key);
  });

  // 验收第 4 条「查单模式用 SHOP-TASK-149 的游客预估，会员模式用 SHOP-TASK-150 的会员预估」：两种模式各自的地址，请求体只有订单号与申请行，带页面的 signal；
  // ok 时交出对应这次请求的预估。
  it.each<[string, PointsEstimator, string]>([
    ["lookup", estimateRefundPoints, POINTS_ESTIMATE_URL],
    ["member", estimateMemberRefundPoints, MEMBER_POINTS_ESTIMATE_URL],
  ])("asks the %s estimate for the chosen lines", async (_mode, estimator, url) => {
    const calls = stubFetch({ [url]: [pointsReply(146, 27)] });
    const request = requestFor(MEMBER_ORDER, [2, 1, 0]);
    const shown: PointsResult[] = [];
    const signal = new AbortController().signal;
    await estimatePoints(estimator, request, signal, (result) => {
      shown.push(result);
    });
    expect(calls.map((call) => call.url)).toEqual([url]);
    expect(initOf(calls, 0).method).toBe("POST");
    expect(initOf(calls, 0).signal).toBe(signal);
    expect(bodyOf(initOf(calls, 0))).toEqual({ order_number: ORDER_NUMBER, lines: request.lines });
    expect(shown).toEqual([{ key: request.key, estimate: POINTS }]);
    expect(shownPoints(shown[0] ?? null, request)).toEqual(POINTS);
  });

  // 验收第 4 条「发出新请求时中止上一个未完成的」与 HANDOFF 0.61 第（9）条「件数改变即重新请求并中止上一个」：改件数时先中止上一个（其 fetch 的 signal 已中止），
  // 之后只交出新请求的结果。
  it("aborts the previous estimate when the quantities change", async () => {
    const calls = stubFetch({ [POINTS_ESTIMATE_URL]: [pointsReply(146, 27), pointsReply(300, 19)] });
    const first = requestFor(MEMBER_ORDER, [1, 0, 0]);
    const second = requestFor(MEMBER_ORDER, [2, 0, 0]);
    const shown: PointsResult[] = [];
    const show = (result: PointsResult) => {
      shown.push(result);
    };
    const stopFirst = startPointsEstimate(estimateRefundPoints, first, show);
    stopFirst();
    const stopSecond = startPointsEstimate(estimateRefundPoints, second, show);
    expect(initOf(calls, 0).signal?.aborted).toBe(true);
    expect(initOf(calls, 1).signal?.aborted).toBe(false);
    await vi.waitFor(() => {
      expect(shown).toHaveLength(1);
    });
    expect(shown).toEqual([{ key: second.key, estimate: { memberOrder: true, pointsBack: 300, pointsReversed: 19 } }]);
    stopSecond();
  });

  // 验收第 4 条「离开页面时中止」与第 5 条「离开页面时中止请求，之后不再更新」：中止之后回来的预估不交出。
  it("drops the estimate after leaving the page", async () => {
    const calls = stubFetch({ [MEMBER_POINTS_ESTIMATE_URL]: [pointsReply(146, 27)] });
    const shown: PointsResult[] = [];
    const page = new AbortController();
    const pending = estimatePoints(estimateMemberRefundPoints, requestFor(MEMBER_ORDER, [1, 0, 0]), page.signal, (result) => {
      shown.push(result);
    });
    page.abort();
    await pending;
    expect(shown).toEqual([]);
    expect(initOf(calls, 0).signal?.aborted).toBe(true);
  });

  // 验收第 4 条「件数改变后、新的预估返回前不显示旧数」：旧件数的结果对新件数不给出，View 没有三行。
  it("hides the old numbers once the quantities change", () => {
    const before = requestFor(MEMBER_ORDER, [1, 0, 0]);
    const after = requestFor(MEMBER_ORDER, [2, 0, 0]);
    const result: PointsResult = { key: before.key, estimate: POINTS };
    expect(shownPoints(result, before)).toEqual(POINTS);
    expect(shownPoints(result, after)).toBeNull();
    expect(shownPoints(result, null)).toBeNull();
    expect(shownPoints(null, after)).toBeNull();
    expect(noPointsText(render(withPoints({ quantities: [2, 0, 0], points: shownPoints(result, after) })))).toBe(true);
  });

  // 验收第 4 条「预估的任何失败（含 401、409 与 network）都不显示积分行、不另加提示，不改变提交按钮是否可用，也不切换整页状态」与 HANDOFF 0.61 第（9）条：
  // 两种预估的各种失败都只交出空预估；两种模式的页面仍是订单页（不是 order.session_expired 或错误页），没有 common.error_retry，提交按钮可用。
  it.each<[string, PointsEstimator, string, () => Reply]>([
    ["lookup 401", estimateRefundPoints, POINTS_ESTIMATE_URL, () => json(401, { detail: "lookup_session_required" })],
    ["lookup 409", estimateRefundPoints, POINTS_ESTIMATE_URL, () => json(409, { detail: "refund_window_closed" })],
    ["lookup network", estimateRefundPoints, POINTS_ESTIMATE_URL, () => "network"],
    ["lookup bad body", estimateRefundPoints, POINTS_ESTIMATE_URL, () => json(200, { member_order: true, points_back: -1, points_reversed: 0 })],
    ["member 401", estimateMemberRefundPoints, MEMBER_POINTS_ESTIMATE_URL, () => json(401, { detail: "member_session_required" })],
    ["member 404", estimateMemberRefundPoints, MEMBER_POINTS_ESTIMATE_URL, () => json(404, { detail: "order_not_found" })],
    ["member 409", estimateMemberRefundPoints, MEMBER_POINTS_ESTIMATE_URL, () => json(409, { detail: "refund_nothing_left" })],
    ["member network", estimateMemberRefundPoints, MEMBER_POINTS_ESTIMATE_URL, () => "network"],
  ])("shows nothing more when the estimate fails (%s)", async (_name, estimator, url, reply) => {
    stubFetch({ [url]: [reply()] });
    const request = requestFor(MEMBER_ORDER, [1, 0, 0]);
    const shown: PointsResult[] = [];
    await estimatePoints(estimator, request, new AbortController().signal, (result) => {
      shown.push(result);
    });
    expect(shown).toEqual([{ key: request.key, estimate: null }]);
    const points = shownPoints(shown[0] ?? null, request);
    expect(points).toBeNull();
    for (const mode of ["lookup", "member"] as const) {
      const html = render(withPoints({ mode, points }));
      expect(noPointsText(html)).toBe(true);
      expect(has(html, "common.error_retry")).toBe(false);
      expect(html).not.toContain(copy("order.session_expired"));
      expect(html).toContain(`<main class="site-refund" aria-busy="false">`);
      for (const button of submitButtons(tree(withPoints({ mode, points })))) {
        expect(button.props.disabled).toBe(false);
      }
    }
  });
});

describe("member mode: content", () => {
  // 验收第 2 条「会员模式不显示 order.lookup_access…其余内容（标题、各行、加减控件、预计金额、两句说明、提交与 ◆ 提示）与查单模式相同」与 HANDOFF 0.61
  // 「P10 会员模式去掉查单授权一行」：没有 order.lookup_access，其余各项都在。
  it.each(LANGUAGES)("has everything but order.lookup_access in %s", (language) => {
    const html = memberView(signedIn(), language);
    expect(has(html, "order.lookup_access", language)).toBe(false);
    expect(html).toContain(`<h1 class="acs-display-l">${copy("refund.title", language)}</h1><span class="acs-num acs-muted">${copy("order.title", language, { orderNo: ORDER_NUMBER })}</span>`);
    const keys: CopyKey[] = ["refund.demo_hint", "refund.select_items", "order.cash_paid", "detail.quantity", "refund.shipping_not_refunded", "refund.coupon_not_restored", "refund.submit", "refund.submit_hint"];
    for (const key of keys) {
      expect(has(html, key, language), key).toBe(true);
    }
    expect(estimate(html, "30.00", language)).toBe(true);
    expect(rows(html)).toHaveLength(3);
    expect(html).not.toContain("href=");
    // 查单模式照旧有这一行。
    expect(has(render({}, language), "order.lookup_access", language)).toBe(true);
  });

  // 验收第 2 条「读完前为 aria-busy 的空主体」「failed 时只显示 common.error_retry」「会员模式…不出现 order.session_expired 整页」：
  // 会话或订单读取中为空主体；会话失败与订单首次读取失败只有 common.error_retry；各状态都不是 order.session_expired。
  it("shows an empty body while reading and only common.error_retry after failures", () => {
    const empty = `<main class="site-refund" aria-busy="true"></main>`;
    expect(memberView(MEMBER_REFUND_LOADING)).toBe(empty);
    expect(memberView(signedIn({ order: null }))).toBe(empty);
    expect(visibleTexts(memberView({ status: "failed" }))).toEqual([COPY["common.error_retry"].en]);
    expect(visibleTexts(memberView(signedIn({ order: null, notice: "error" })))).toEqual([COPY["common.error_retry"].en]);
    const states: MemberRefundState[] = [MEMBER_REFUND_LOADING, { status: "failed" }, signedIn({ order: null }), signedIn({ order: null, notice: "error" }), signedIn()];
    for (const state of states) {
      expect(memberRefundScreen(state).status).not.toBe("expired");
      expect(memberView(state)).not.toContain(copy("order.session_expired"));
    }
  });

  // 验收第 6 条「页面文字全部来自字典，带变量的条目按模板匹配」：会员模式各状态与两种模式的积分三行，除订单数据外每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text in member mode and with points in %s", (language) => {
    const orderData = new Set(["Crew Neck Tee", "Black, M", "Soy Wax Candle", "Canvas Tote", "Natural"]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    const pages = [
      memberView(signedIn(), language, POINTS),
      memberView(signedIn({ busy: "checking" }), language, POINTS),
      memberView(signedIn({ notice: "duplicate" }), language),
      memberView(signedIn({ notice: "error" }), language),
      memberView({ status: "failed" }, language),
      render(withPoints(), language),
    ];
    for (const html of pages) {
      for (const value of visibleTexts(html)) {
        const known = orderData.has(value) || /^\d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

describe("member mode: opening", () => {
  // 验收第 2 条「会员模式打开时先经 SHOP-TASK-151 的读取对象重读会话…guest 时用 replace 进入 /login」与 HANDOFF 0.61 第（5）条「会员页打开时都重读一次会话」：
  // 未登录时只读会话、不读订单，replace 到 /login。
  it("replaces the page with /login for a guest", async () => {
    handOffMemberOrder(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [guestReply()] });
    const { moves, events } = fakeMoves();
    await openMemberRefund(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["replace /login"]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL]);
  });

  // 同一条「failed 时只显示 common.error_retry」：读取会话失败与网络中断都只显示这一句，不读订单、不跳转。
  it.each<[string, () => Reply]>([
    ["failed", () => new Response(null, { status: 500 })],
    ["network", () => "network"],
  ])("shows only common.error_retry when reading the session %s", async (_name, reply) => {
    handOffMemberOrder(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [reply()] });
    const { moves, events, current } = fakeMoves();
    await openMemberRefund(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["show failed"]);
    expect(calls).toHaveLength(1);
    expect(visibleTexts(memberView(current()))).toEqual([COPY["common.error_retry"].en]);
  });

  // Kelvin 2026-10-24 决定「会员在 P10（/account/order/refund）刷新、页面内存里没有订单号时，用 replace 回到 P13 我的订单」：
  // 已登录而没有交接的订单号时 replace 到 /account，不读订单。
  it("goes back to /account without an order number", async () => {
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [memberReply()] });
    const { moves, events } = fakeMoves();
    await openMemberRefund(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["replace /account"]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL]);
    expect(openRefundStep({ kind: "member", phoneMasked: MASKED, hasPassword: true, csrfToken: TOKEN }, () => null)).toBe("account");
  });

  // 验收第 2 条「member 时取 SHOP-TASK-150 交接的订单号…经 SHOP-TASK-150 读取详情」：会话读完后记下订单号与令牌（订单读完前仍为空主体），
  // 再以请求体里的订单号读取该单；会话读取不带页面的 signal（共享读取），订单读取带。
  it("reads the handed-off order after the session", async () => {
    handOffMemberOrder(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [memberReply()], [VIEW_EN]: [orderReply()] });
    const reader = createMemberSessionReader();
    const page = new AbortController();
    const { moves, events, current } = fakeMoves();
    await openMemberRefund(reader, page.signal, moves);
    expect(events).toEqual(["show member"]);
    expect(current()).toEqual(signedIn({ order: null, quantities: [] }));
    await loadMemberRefundOrder(reader, "en", ORDER_NUMBER, page.signal, moves);
    expect(current()).toEqual(signedIn({ quantities: [0, 0, 0] }));
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL, VIEW_EN]);
    expect(initOf(calls, 0).signal ?? null).toBeNull();
    expect(initOf(calls, 1).method).toBe("POST");
    expect(initOf(calls, 1).signal).toBe(page.signal);
    expect(bodyOf(initOf(calls, 1))).toEqual({ order_number: ORDER_NUMBER });
  });

  // 验收第 2 条「切换语言时重新读取同一订单」：以新语言的地址读取同一订单号，已选的件数保留。
  it.each(LANGUAGES)("reads the same order again in %s", async (language) => {
    const calls = stubFetch({ [memberOrderViewUrl(language)]: [orderReply()] });
    const { moves, current } = fakeMoves(signedIn({ quantities: [2, 1, 0] }));
    await loadMemberRefundOrder(createMemberSessionReader(), language, ORDER_NUMBER, new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([memberOrderViewUrl(language)]);
    expect(bodyOf(initOf(calls, 0))).toEqual({ order_number: ORDER_NUMBER });
    expect(memberState(current()).quantities).toEqual([2, 1, 0]);
  });

  // 验收第 2 条「not_found 时用 replace 回到 /account」（HANDOFF 0.61 第（5）条「P09、P10 会员模式回 404 时 replace 到 /account」）：不重读会话。
  it("goes back to /account when the order is not found", async () => {
    const calls = stubFetch({ [VIEW_EN]: [json(404, { detail: "order_not_found" })] });
    const { moves, events } = fakeMoves(signedIn({ order: null }));
    await loadMemberRefundOrder(createMemberSessionReader(), "en", ORDER_NUMBER, new AbortController().signal, moves);
    expect(events).toEqual(["replace /account"]);
    expect(calls.map((call) => call.url)).toEqual([VIEW_EN]);
  });

  // 验收第 2 条「signed_out 时重读会话并用 replace 进入 /login」（HANDOFF 0.61 第（5）条「任何会员接口回 401 时重读会话并 replace 到 /login」）：
  // 先重读会话，之后才 replace。
  it("reloads the session and goes to /login when signed out", async () => {
    const calls = stubFetch({ [VIEW_EN]: [guestReply()], [MEMBER_SESSION_URL]: [guestReply()] });
    const { moves, events } = fakeMoves(signedIn({ order: null }));
    await loadMemberRefundOrder(createMemberSessionReader(), "en", ORDER_NUMBER, new AbortController().signal, moves);
    expect(events).toEqual(["replace /login"]);
    expect(calls.map((call) => call.url)).toEqual([VIEW_EN, MEMBER_SESSION_URL]);
  });

  // 验收第 2 条「failed 与 network 照查单模式显示 common.error_retry」：首次读取失败只有这一句；已显示订单时订单保留并在提交之后显示提示。
  it.each<[string, () => Reply]>([
    ["failed", () => new Response(null, { status: 500 })],
    ["network", () => "network"],
  ])("shows common.error_retry when reading the order %s", async (_name, reply) => {
    stubFetch({ [VIEW_EN]: [reply(), reply()] });
    const first = fakeMoves(signedIn({ order: null }));
    await loadMemberRefundOrder(createMemberSessionReader(), "en", ORDER_NUMBER, new AbortController().signal, first.moves);
    expect(visibleTexts(memberView(first.current()))).toEqual([COPY["common.error_retry"].en]);
    const later = fakeMoves(signedIn());
    await loadMemberRefundOrder(createMemberSessionReader(), "en", ORDER_NUMBER, new AbortController().signal, later.moves);
    expect(memberState(later.current()).order).toEqual(MEMBER_ORDER);
    const html = memberView(later.current());
    expect(has(html, "common.error_retry")).toBe(true);
    expect(rows(html)).toHaveLength(3);
  });

  // 验收第 5 条「离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：打开与读取订单在中止后都不更新或跳转。
  it("stops updating after leaving the page", async () => {
    handOffMemberOrder(ORDER_NUMBER);
    stubFetch({ [MEMBER_SESSION_URL]: [memberReply()], [VIEW_EN]: [orderReply()] });
    const page = new AbortController();
    page.abort();
    const { moves, events } = fakeMoves();
    await openMemberRefund(createMemberSessionReader(), page.signal, moves);
    await loadMemberRefundOrder(createMemberSessionReader(), "en", ORDER_NUMBER, page.signal, moves);
    expect(events).toEqual([]);
  });
});

describe("member mode: submitting", () => {
  const target = (overrides: Partial<MemberRefundTarget> = {}): MemberRefundTarget => ({ order: MEMBER_ORDER, csrfToken: TOKEN, key: KEY, lines: CHOSEN, ...overrides });

  // 验收第 3 条「submitted 时以 orderLookup.ts 的 handOffSubmittedRefund 交出订单号并前往 /account/order（P09 会员模式显示 refund.submitted）」与
  // 「以会话里的 CSRF 令牌提交，幂等键规则与查单模式相同」：请求去会员版地址，请求头带令牌与幂等键，请求体只有订单号与申请行。
  it("hands off the order number and goes to /account/order once submitted", async () => {
    const calls = stubFetch({ [MEMBER_REFUNDS_URL]: [json(201, {})] });
    const { moves, events } = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, moves);
    expect(events).toEqual(["navigate /account/order"]);
    expect(submittedRefundOrder()).toBe(ORDER_NUMBER);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_REFUNDS_URL]);
    const init = initOf(calls, 0);
    expect(init.method).toBe("POST");
    expect(headerOf(init, CSRF_HEADER)).toBe(TOKEN);
    expect(headerOf(init, IDEMPOTENCY_HEADER)).toBe(KEY);
    expect(bodyOf(init)).toEqual({ order_number: ORDER_NUMBER, lines: CHOSEN });
  });

  // 同一条「幂等键规则与查单模式相同（refundKey）」与「提交期间按钮禁用」：点提交时进入 submitting、收起先前的提示；每次新生成键，
  // 只有网络中断后以同一内容重试时沿用；提交中改件数不生效，平时照查单模式限在 0 到可退件数。
  it("starts submitting, keys like lookup mode and locks the quantities", () => {
    expect(startRefund(signedIn({ notice: "error" }))).toEqual(signedIn({ busy: "submitting", notice: null }));
    expect(refundKey(null, CHOSEN, () => "fresh")).toBe("fresh");
    expect(refundKey({ key: KEY, lines: CHOSEN }, CHOSEN, () => "fresh")).toBe(KEY);
    expect(refundKey({ key: KEY, lines: CHOSEN }, [{ line_index: 0, quantity: 2 }], () => "fresh")).toBe("fresh");
    expect(memberState(chooseQuantity(0, 5)(signedIn())).quantities).toEqual([2, 0, 0]);
    expect(memberState(chooseQuantity(2, 1)(signedIn())).quantities).toEqual([1, 0, 0]);
    expect(memberState(chooseQuantity(1, 1)(signedIn({ busy: "submitting" }))).quantities).toEqual([1, 0, 0]);
  });

  // 验收第 3 条「leave 时前往 /account/order」：409 order_not_refundable 时不交出订单号（P09 不显示 refund.submitted）。
  it("goes to /account/order without the hand-off when the order cannot be refunded", async () => {
    stubFetch({ [MEMBER_REFUNDS_URL]: [json(409, { detail: "order_not_refundable" })] });
    const { moves, events } = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, moves);
    expect(events).toEqual(["navigate /account/order"]);
    expect(submittedRefundOrder()).toBeNull();
  });

  // 验收第 3 条「signed_out 时重读会话并用 replace 进入 /login；not_found 时用 replace 回到 /account」。
  it("reloads the session and goes to /login when signed out, and back to /account when not found", async () => {
    const calls = stubFetch({ [MEMBER_REFUNDS_URL]: [guestReply(), json(404, { detail: "order_not_found" })], [MEMBER_SESSION_URL]: [guestReply()] });
    const out = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, out.moves);
    expect(out.events).toEqual(["replace /login"]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_REFUNDS_URL, MEMBER_SESSION_URL]);
    const gone = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, gone.moves);
    expect(gone.events).toEqual(["replace /account"]);
  });

  // 验收第 3 条「csrf 时重读会话——member 时换用新令牌并显示 common.error_retry，guest 时用 replace 进入 /login，重读得到 failed（含网络中断）时保留原令牌
  // 并显示 common.error_retry」：先按接口重新读取订单，再重读会话。
  it.each<[string, () => Reply, string | null]>([
    ["member", () => memberReply(NEW_TOKEN), NEW_TOKEN],
    ["guest", guestReply, null],
    ["failed", () => new Response(null, { status: 500 }), TOKEN],
    ["network", () => "network", TOKEN],
  ])("reloads the session after a CSRF failure (%s)", async (_name, session, token) => {
    const calls = stubFetch({ [MEMBER_REFUNDS_URL]: [json(403, { detail: "csrf_failed" })], [VIEW_EN]: [orderReply()], [MEMBER_SESSION_URL]: [session()] });
    const { moves, events, current } = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_REFUNDS_URL, VIEW_EN, MEMBER_SESSION_URL]);
    if (token === null) {
      expect(events).toEqual(["replace /login"]);
      return;
    }
    expect(events).toEqual(["update null"]);
    const state = memberState(current());
    expect(state.csrfToken).toBe(token);
    expect(state.notice).toBe("error");
    expect(state.busy).toBeNull();
    expect(has(memberView(state), "common.error_retry")).toBe(true);
  });

  // 验收第 3 条「refused、error 与 retry 与查单模式相同」：被拒时按重新读取到的订单显示对应提示，件数不超过新的可退件数；
  // 其他失败显示 common.error_retry 并沿用原来的订单。
  it("shows the refusal with the order read again, or common.error_retry", async () => {
    const reread = lookupOrder({ placed_by_member: true, lines: LINES.map((line, index) => (index === 0 ? { ...line, refundable_quantity: 0, refund_estimates_sen: [] } : line)) });
    stubFetch({ [MEMBER_REFUNDS_URL]: [json(409, { detail: "refund_duplicate" }), new Response(null, { status: 500 })], [VIEW_EN]: [orderReply(reread)] });
    const refused = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, refused.moves);
    expect(refused.events).toEqual(["update null"]);
    expect(memberState(refused.current())).toEqual(signedIn({ order: reread, quantities: [0, 0, 0], notice: "duplicate" }));
    expect(memberView(refused.current())).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("refund.duplicate")}</p>`);
    const failed = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, failed.moves);
    expect(memberState(failed.current())).toEqual(signedIn({ notice: "error" }));
  });

  // 同一条「retry 与查单模式相同」：网络中断后先确认订单（common.network_check），申请没有写入时记下幂等键与请求内容供重试沿用；已写入时照常交出订单号并前往 /account/order。
  it("checks the order after a network failure", async () => {
    stubFetch({ [MEMBER_REFUNDS_URL]: ["network", "network"], [VIEW_EN]: [orderReply(), orderReply(lookupOrder({ placed_by_member: true, refund_requests: [REQUEST] }))] });
    const retry = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, retry.moves);
    expect(retry.events).toEqual(["update checking", "update null"]);
    const state = memberState(retry.current());
    expect(state.retry).toEqual({ key: KEY, lines: CHOSEN });
    expect(state.notice).toBe("error");
    expect(refundKey(state.retry, CHOSEN, () => "fresh")).toBe(KEY);
    const done = fakeMoves(signedIn({ busy: "submitting" }));
    await sendMemberRefund(createMemberSessionReader(), "en", target(), new AbortController().signal, done.moves);
    expect(done.events).toEqual(["update checking", "navigate /account/order"]);
    expect(submittedRefundOrder()).toBe(ORDER_NUMBER);
  });

  // 验收第 5 条「离开页面时中止请求，之后不再更新」：提交回来时页面已离开则不更新、不跳转、不交出订单号。
  it("stops after leaving the page", async () => {
    stubFetch({ [MEMBER_REFUNDS_URL]: [json(201, {})] });
    const page = new AbortController();
    const { moves, events } = fakeMoves(signedIn({ busy: "submitting" }));
    const pending = sendMemberRefund(createMemberSessionReader(), "en", target(), page.signal, moves);
    page.abort();
    await pending;
    expect(events).toEqual([]);
    expect(submittedRefundOrder()).toBeNull();
  });
});
