import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { LookupLine, LookupOrder } from "../api/orderLookup";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { RefundView } from "./RefundPage";
import type { RefundViewProps } from "./RefundPage";

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
  // refund.expired_points_note 三语原文都不出现（字典里也没有这三个键），也没有 account.orders。
  it.each(LANGUAGES)("has no points rows and no member-mode link in %s", (language) => {
    expect(Object.keys(COPY)).not.toContain("refund.points_back");
    expect(Object.keys(COPY)).not.toContain("refund.points_reversed");
    expect(Object.keys(COPY)).not.toContain("refund.expired_points_note");
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
