import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MEMBER_ATTEMPTS_URL, MEMBER_CANCEL_URL, memberPayOrderUrl, memberStripeStartUrl } from "../api/memberPay";
import type { MemberPayRead, MemberStripeStart, MemberWriteOutcome } from "../api/memberPay";
import { createMemberSessionReader, MEMBER_SESSION_URL } from "../api/memberSession";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, payOrdersUrl, takePaidNotCancelled } from "../api/pay";
import type { PaymentAttempt, PayOrder, StripeStart, WriteOutcome } from "../api/pay";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  cancelAsMember,
  cancelMove,
  canSubmit,
  confirmsStripeCancel,
  copyText,
  countryName,
  guestExpiredStep,
  leaveAfterCancel,
  leavesPayPage,
  memberReadStep,
  memberStripeStep,
  memberWriteStep,
  openPayOrder,
  PAY_RESULT_PATH,
  payAsMember,
  PayView,
  recipientParts,
  rereadMemberOrder,
  showsCountdown,
  startStripeAsMember,
  stripeAction,
  stripeMove,
} from "./PayPage";
import type { CancelMove, MemberMoves, MemberStep, PayOpening, PayStash, PayViewProps, StripeMove } from "./PayPage";

// 会员模式的测试替换全局 fetch，每条之后复原。
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";

function payOrder(overrides: Partial<PayOrder> = {}): PayOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "awaiting_demo_payment",
    created_at: "2026-10-05T10:00:00Z",
    payment_expires_at: "2026-10-05T10:15:00Z",
    server_time: "2026-10-05T10:01:00Z",
    subtotal_sen: 12000,
    coupon_discount_sen: 0,
    points_redeemed: 0,
    shipping_fee_sen: 1500,
    total_sen: 13500,
    points_earned: 0,
    recipient: {
      name: "Sam Taylor",
      phone: "+447700900123",
      country_code: "GB",
      region: "Greater London",
      address: "1 Example Street",
      postal_code: "AB1 2CD",
    },
    last_payment: null,
    cancelled_by: null,
    payment_route: "simulated",
    stripe_state: null,
    stripe_refunded: false,
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

function props(overrides: Partial<PayViewProps> = {}): PayViewProps {
  return {
    screen: { status: "ready", order: payOrder() },
    minutes: 14,
    states: null,
    method: null,
    busy: null,
    failed: false,
    confirming: false,
    stripeUnavailable: false,
    methodChanged: false,
    cancelTryLater: false,
    onMethod: noop,
    onPay: noop,
    onStripe: noop,
    onCancel: noop,
    onConfirmCancel: noop,
    onKeepOrder: noop,
    ...overrides,
  };
}

function wrap(children: ReactNode, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/pay">{children}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<PayViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<PayView {...props(overrides)} />, language));
}

// PayView 返回的元素树（不展开其中的子组件），用来取按钮的 disabled 与点击处理；仍在语言与路由的上下文里渲染。
function tree(overrides: Partial<PayViewProps> = {}): ReactNode {
  const captured: ReactNode[] = [];
  const probe = () => {
    captured.push(PayView(props(overrides)));
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

function button(node: ReactNode, key: CopyKey): AnyElement {
  const found = elements(node).find((element) => element.type === "button" && element.props.children === COPY[key].en);
  if (!found) {
    throw new Error(`no button ${key}`);
  }
  return found;
}

function click(element: AnyElement): void {
  (element.props.onClick as () => void)();
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

describe("order and amount", () => {
  // SHOP-TASK-024 验收第 4 条「P06 在订单为 awaiting_demo_payment 时显示：订单号与 common.copy…、pay.save_order_no、pay.amount_due（应付）、pay.expires 倒计时、
  // pay.guest_access、该单收货资料…、pay.choose_method 与三种支付方式、pay.simulate_success、pay.simulate_failure、◆ pay.action_hint、pay.cancel_order、★ pay.demo_hint」。
  it.each(LANGUAGES)("shows every element of the wireframe in %s", (language) => {
    const html = render({}, language);
    const keys: CopyKey[] = [
      "pay.title",
      "common.demo_badge",
      "pay.demo_hint",
      "pay.order_no",
      "common.copy",
      "pay.save_order_no",
      "pay.amount_due",
      "pay.guest_access",
      "checkout.recipient_title",
      "pay.choose_method",
      "pay.method_card",
      "pay.method_bank",
      "pay.method_ewallet",
      "pay.simulate_success",
      "pay.simulate_failure",
      "pay.action_hint",
      "pay.cancel_order",
    ];
    for (const key of keys) {
      expect(html, key).toContain(escapeHtml(COPY[key][language]));
    }
    expect(html).toContain(`<span class="acs-num site-pay__number">${ORDER_NUMBER}</span>`);
    expect(html).toContain(escapeHtml(COPY["pay.expires"][language].replace("{minutes}", "14")));
    expect(html).toContain(`<span class="acs-price acs-price--l">RM 135.00</span>`);
  });

  // UX P06 桌面线框的顺序：[pay.title] ★ [pay.demo_hint]，[pay.order_no] 订单号 ([common.copy])，[pay.save_order_no]，[pay.amount_due]，[pay.expires]，
  // [pay.guest_access]，[checkout.recipient_title]，[pay.choose_method] 三种方式，成功、失败按钮，◆ [pay.action_hint]，[pay.cancel_order]。
  it("follows the order of the wireframe", () => {
    const html = render();
    const desktop = html.indexOf("site-desktop-only site-pay__recipient");
    const positions = [
      html.indexOf(COPY["pay.title"].en),
      html.indexOf(escapeHtml(COPY["pay.demo_hint"].en)),
      html.indexOf(COPY["pay.order_no"].en),
      html.indexOf(ORDER_NUMBER),
      html.indexOf(`>${COPY["common.copy"].en}<`),
      html.indexOf(escapeHtml(COPY["pay.save_order_no"].en)),
      html.indexOf(COPY["pay.amount_due"].en),
      html.indexOf("Complete within 14 min"),
      html.indexOf(escapeHtml(COPY["pay.guest_access"].en)),
      desktop,
      html.indexOf(COPY["pay.choose_method"].en),
      html.indexOf(COPY["pay.method_card"].en),
      html.indexOf(COPY["pay.method_bank"].en),
      html.indexOf(COPY["pay.method_ewallet"].en),
      html.indexOf(COPY["pay.simulate_success"].en),
      html.indexOf(COPY["pay.simulate_failure"].en),
      html.indexOf(COPY["pay.action_hint"].en),
      html.indexOf(COPY["pay.cancel_order"].en),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // SHOP-TASK-024 验收「不在浏览器计算任何金额」与第 9 条「金额原样来自接口」、UX P06 M1「应付金额（订单快照）」：
  // 合计与商品小计加运费不符时照样显示接口的 total_sen，不显示小计、运费或自行相加的数。
  it("shows the total exactly as the order returns it", () => {
    const html = render({ screen: { status: "ready", order: payOrder({ subtotal_sen: 10000, shipping_fee_sen: 1000, total_sen: 12345 }) } });
    expect(html).toContain(">RM 123.45<");
    expect(html).not.toContain("RM 110.00");
    expect(html).not.toContain("RM 100.00");
    expect(html).not.toContain("RM 10.00");
  });

  // SHOP-TASK-024 验收第 4 条「不出现任何卡号、有效期或 CVV 输入框，不显示参考外币」与 UX P06 说明「不出现任何卡号、有效期、CVV 输入框…支付页不显示（参考外币）」：
  // 页面上唯一的输入控件是三个支付方式单选框；没有 ≈ 参考金额或其他币种。
  it("has no card fields and no reference currency", () => {
    const html = render({ method: "card" });
    const inputs = [...html.matchAll(/<input\b[^>]*>/g)].map((m) => m[0]);
    expect(inputs).toHaveLength(3);
    for (const input of inputs) {
      expect(input).toContain(`type="radio"`);
    }
    expect(html).not.toMatch(/<(textarea|select)\b/);
    expect(html).not.toMatch(/cvv|cvc|expiry|autocomplete="cc-|inputmode=/i);
    expect(html).not.toContain("≈");
    expect(html).not.toMatch(/\b(SGD|USD|CNY|GBP|EUR)\b/);
  });

  // SHOP-TASK-024 验收第 2 条「页面不显示任何凭据内容或凭据剩余时间」与 UX「阅读说明」「pay.expires 倒计时是该单 15 分钟的支付时限…不是 30 分钟的凭据有效期」：
  // 唯一的倒计时是 pay.expires，{minutes} 为支付时限的剩余分钟；页面里没有任何链接带订单号。
  it("shows only the payment countdown and no order number in links", () => {
    const html = render({ minutes: 9 });
    expect(html).toContain(COPY["pay.expires"].en.replace("{minutes}", "9"));
    expect(html.match(/\bmin\b/g)).toHaveLength(1);
    for (const href of html.matchAll(/href="([^"]*)"/g)) {
      expect(href[1]).not.toContain(ORDER_NUMBER);
    }
  });
});

describe("shipping details", () => {
  // SHOP-TASK-024 验收第 4 条「该单收货资料（姓名、电话、地址、邮编、地区、国家）」与 UX P06 线框「[P1] <姓名> / <电话> / <地址> / <邮编> / <地区> / <国家>」：
  // 桌面与手机（折叠）各一份，国家按界面语言显示名称。
  it("shows name, phone, address, postcode, region and country", () => {
    const html = render();
    const desktop = /<section class="site-desktop-only site-pay__recipient">[\s\S]*?<\/section>/.exec(html)?.[0] ?? "";
    const phone = /<details class="acs-summary site-phone-only site-pay__recipient-toggle">[\s\S]*?<\/details>/.exec(html)?.[0] ?? "";
    for (const part of [desktop, phone]) {
      expect(part).toContain(COPY["checkout.recipient_title"].en);
      expect(visibleTexts(part).slice(1)).toEqual(["Sam Taylor", "·", "+447700900123", "1 Example Street", ",", "AB1 2CD", ",", "Greater London", ",", "United Kingdom"]);
    }
  });

  // 同一条，DESIGN 1.11 依赖的 SHOP-TASK-023「国家名称…前端按界面语言本地化」「州属名称…三种界面语言都用这一名称」：
  // 马来西亚的州属代码换成名称，名称表没取到时显示代码；国家名随语言。
  it("names the Malaysian state and the country", () => {
    const recipient = { name: "Aina", phone: "+60123456789", country_code: "MY", region: "MY-10", address: "12 Jalan Contoh", postal_code: "47000" };
    expect(recipientParts(recipient, "en", new Map([["MY-10", "Selangor"]])).region).toBe("Selangor");
    expect(recipientParts(recipient, "en", null).region).toBe("MY-10");
    expect(recipientParts({ ...recipient, country_code: "SG", region: "" }, "en", null).region).toBeNull();
    expect(countryName("GB", "en")).toBe("United Kingdom");
    expect(countryName("GB", "zh")).toBe("英国");
    expect(countryName("MY", "en")).toBe("Malaysia");
  });

  // 派生实现约束（实现选择）：没有收货资料记录（接口给 null）时不渲染收货资料区块。
  it("renders no shipping details without a recipient", () => {
    const html = render({ screen: { status: "ready", order: payOrder({ recipient: null }) } });
    expect(html).not.toContain(COPY["checkout.recipient_title"].en);
  });
});

describe("buttons", () => {
  // SHOP-TASK-024 验收第 4 条「未选支付方式时成功与失败按钮禁用」：未选时两个按钮都禁用，选了方式后都可用，选中的单选框为 checked。
  it("disables success and failure until a method is chosen", () => {
    const none = tree();
    expect(button(none, "pay.simulate_success").props.disabled).toBe(true);
    expect(button(none, "pay.simulate_failure").props.disabled).toBe(true);
    const chosen = tree({ method: "bank" });
    expect(button(chosen, "pay.simulate_success").props.disabled).toBe(false);
    expect(button(chosen, "pay.simulate_failure").props.disabled).toBe(false);
    const html = render({ method: "bank" });
    const checkedRadios = (html.match(/<input[^>]*type="radio"[^>]*>/g) ?? []).filter((tag) => tag.includes(`checked=""`));
    expect(checkedRadios).toHaveLength(1);
    expect(checkedRadios[0]).toContain(`value="bank"`);
    expect(render()).not.toContain(`checked=""`);
    expect(canSubmit(null, null)).toBe(false);
    expect(canSubmit("ewallet", null)).toBe(true);
  });

  // SHOP-TASK-024 验收第 6 条「提交期间按钮禁用并显示 pay.processing」：提交、取消与网络中断后确认期间，成功、失败、取消按钮与单选框都禁用；提交时显示 pay.processing。
  it("disables every control while busy and shows pay.processing while paying", () => {
    for (const busy of ["paying", "cancelling", "checking"] as const) {
      const node = tree({ method: "card", busy });
      expect(button(node, "pay.simulate_success").props.disabled).toBe(true);
      expect(button(node, "pay.simulate_failure").props.disabled).toBe(true);
      expect(button(node, "pay.cancel_order").props.disabled).toBe(true);
      const html = render({ method: "card", busy });
      expect(html.match(/<input type="radio"[^>]*disabled=""/g)).toHaveLength(3);
      expect(html.includes(COPY["pay.processing"].en)).toBe(busy === "paying");
    }
    expect(render({ method: "card" })).not.toContain(COPY["pay.processing"].en);
  });

  // SHOP-TASK-024 验收第 6 条与 UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单…再允许重试」：确认期间显示 common.network_check；
  // 失败（含 403 csrf_failed 重新取得令牌后、网络中断确认仍待支付后）显示 common.error_retry。
  it("shows common.network_check while checking and common.error_retry after a failure", () => {
    const checking = render({ method: "card", busy: "checking" });
    expect(checking).toContain(COPY["common.network_check"].en);
    expect(checking).not.toContain(COPY["common.error_retry"].en);
    const failed = render({ method: "card", failed: true });
    expect(failed).toContain(COPY["common.error_retry"].en);
    expect(failed).not.toContain(COPY["common.network_check"].en);
  });

  // SHOP-TASK-024 验收第 6 条「成功、失败按钮调用 POST /api/pay/attempts」：两个按钮分别以 success 与 failure 交给页面提交。
  it("passes success or failure to the page", () => {
    const onPay = vi.fn();
    const node = tree({ method: "card", onPay });
    click(button(node, "pay.simulate_success"));
    click(button(node, "pay.simulate_failure"));
    expect(onPay.mock.calls).toEqual([["success"], ["failure"]]);
  });
});

describe("cancel confirmation", () => {
  // SHOP-TASK-024 验收第 7 条「取消先弹出 pay.cancel_confirm 确认框（pay.cancel_confirm_yes、pay.cancel_confirm_no），确认后调用 POST /api/pay/cancel」：
  // 点 pay.cancel_order 只打开确认框（onCancel），不直接取消（onConfirmCancel 不被调用）；确认框里是/否分别接到确认取消与保留订单。
  it("asks for confirmation before cancelling", () => {
    const onCancel = vi.fn();
    const onConfirmCancel = vi.fn();
    click(button(tree({ onCancel, onConfirmCancel }), "pay.cancel_order"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirmCancel).not.toHaveBeenCalled();

    const onKeepOrder = vi.fn();
    const dialog = elements(tree({ confirming: true, onConfirmCancel, onKeepOrder })).find((element) => "onConfirm" in element.props);
    expect(dialog?.props.onConfirm).toBe(onConfirmCancel);
    expect(dialog?.props.onKeep).toBe(onKeepOrder);
  });

  // UX P06「点取消后确认框：[pay.cancel_confirm] ( [pay.cancel_confirm_yes] ) ([pay.cancel_confirm_no])」：确认框只在打开时渲染，是带标题关联的模态对话框。
  it.each(LANGUAGES)("renders the confirmation dialog only when open in %s", (language) => {
    expect(render({}, language)).not.toContain("role=\"dialog\"");
    const html = render({ confirming: true }, language);
    const dialog = /<div class="acs-dialog" role="dialog" aria-modal="true" aria-labelledby="([^"]+)">[\s\S]*?<\/div><\/div>/.exec(html);
    expect(dialog).not.toBeNull();
    expect(dialog?.[0]).toContain(`<p id="${dialog?.[1] ?? ""}">${escapeHtml(COPY["pay.cancel_confirm"][language])}</p>`);
    expect(dialog?.[0]).toContain(`>${escapeHtml(COPY["pay.cancel_confirm_yes"][language])}</button>`);
    expect(dialog?.[0]).toContain(`>${escapeHtml(COPY["pay.cancel_confirm_no"][language])}</button>`);
  });
});

describe("session expired, loading and errors", () => {
  // SHOP-TASK-024 验收第 3 条「接口 401 时整页显示 pay.session_expired，common.nav_track 按路由规则在订单查询页实现前不渲染」与 UX P06「游客凭据过期…时替换整页
  // [pay.session_expired] ( [common.nav_track] ) → P08」，SHOP-TASK-028 验收第 3 条「加入 /track 后…P06、P07 授权过期提示里的 common.nav_track 按路由规则出现并链到 /track」：
  // 只有提示与链到 /track 的 common.nav_track，没有订单内容或按钮。
  it.each(LANGUAGES)("replaces the whole page with pay.session_expired in %s", (language) => {
    const html = render({ screen: { status: "expired" } }, language);
    expect(visibleTexts(html)).toEqual([COPY["pay.session_expired"][language], COPY["common.nav_track"][language]]);
    expect(html).toContain(`<a class="acs-btn acs-btn--primary site-pay__track" href="/track">${COPY["common.nav_track"][language]}</a>`);
    expect([...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1])).toEqual(["/track"]);
    expect(html).not.toContain("<button");
  });

  // 派生实现约束（实现选择）：守住第 3 条「两页打开时都调用…GET /api/pay/orders」——接口返回之前主体为空并标 aria-busy；读取失败时显示 common.error_retry，不显示订单内容。
  it("shows nothing before the order arrives and an error when reading fails", () => {
    expect(render({ screen: { status: "loading" } })).toBe(`<main class="site-pay" aria-busy="true"></main>`);
    const html = render({ screen: { status: "error" } });
    expect(html).toContain(COPY["common.error_retry"].en);
    expect(html).toContain(COPY["pay.title"].en);
    expect(html).not.toContain(COPY["pay.order_no"].en);
  });
});

describe("copy to clipboard", () => {
  // SHOP-TASK-024 验收第 4 条「订单号与 common.copy（复制到剪贴板）」：写入剪贴板的是订单号；剪贴板不可用或被拒时返回 false，不抛错。
  it("writes the order number to the clipboard", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve());
    await expect(copyText(ORDER_NUMBER, { writeText })).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith(ORDER_NUMBER);
    await expect(copyText(ORDER_NUMBER, { writeText: () => Promise.reject(new Error("denied")) })).resolves.toBe(false);
    await expect(copyText(ORDER_NUMBER, undefined)).resolves.toBe(false);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-024 验收第 9 条「页面文字全部来自字典」：各状态下，除订单数据（订单号、收货资料、国家名与分隔符号）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const states: Partial<PayViewProps>[] = [
      {},
      { method: "card", busy: "paying" },
      { method: "card", busy: "checking" },
      { method: "card", failed: true, confirming: true },
      { screen: { status: "expired" } },
      { screen: { status: "error" } },
    ];
    const orderData = new Set([ORDER_NUMBER, "Sam Taylor", "+447700900123", "1 Example Street", "AB1 2CD", "Greater London", countryName("GB", language), "·", ","]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        const known = orderData.has(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

type StripeState = PayOrder["stripe_state"];

function stripeScreen(state: StripeState): PayViewProps["screen"] {
  return { status: "ready", order: payOrder({ payment_route: "stripe", stripe_state: state }) };
}

function renderStripe(state: StripeState, overrides: Partial<PayViewProps> = {}, language: Language = "en"): string {
  return render({ screen: stripeScreen(state), ...overrides }, language);
}

// Stripe 的跳转按钮文字后跟一个图标，children 是数组；按其中的文字找按钮，没有时为 undefined。
function labelled(node: ReactNode, key: CopyKey): AnyElement | undefined {
  return elements(node).find((element) => element.type === "button" && [element.props.children].flat().includes(COPY[key].en));
}

function shows(html: string, key: CopyKey, language: Language = "en"): boolean {
  return html.includes(escapeHtml(COPY[key][language]));
}

const STRIPE_KEYS: CopyKey[] = [
  "pay.stripe_title",
  "pay.stripe_demo_hint",
  "pay.stripe_notice",
  "pay.stripe_test_card",
  "pay.stripe_go",
  "pay.stripe_redirecting",
  "pay.stripe_in_progress",
  "pay.stripe_under_review",
  "pay.stripe_continue",
  "pay.stripe_unavailable",
];

const SIMULATED_ONLY: CopyKey[] = [
  "pay.title",
  "pay.demo_hint",
  "pay.choose_method",
  "pay.method_card",
  "pay.method_bank",
  "pay.method_ewallet",
  "pay.simulate_success",
  "pay.simulate_failure",
  "pay.action_hint",
];

const DIAMOND = `<path d="M12 3l9 9-9 9-9-9z"></path>`;

describe("payment route", () => {
  // UX 0.11「P06 Stripe 测试」「模拟：页面与上面的线框完全相同，不显示任何 Stripe 文字」：
  // 模拟途径在各状态下都没有 Stripe 的文案，也没有 Stripe 字样。
  it.each(LANGUAGES)("shows no Stripe text on the simulated route in %s", (language) => {
    const states: Partial<PayViewProps>[] = [{}, { method: "card", busy: "paying" }, { method: "card", failed: true }, { methodChanged: true }, { busy: "checking" }];
    for (const state of states) {
      const html = render(state, language);
      for (const key of STRIPE_KEYS) {
        expect(shows(html, key, language), key).toBe(false);
      }
      expect(html).not.toContain("Stripe");
    }
  });

  // UX 0.11「Stripe 测试：标题、★ 提示与操作区换成 Stripe 的，订单号、应付金额、游客授权说明、收货资料与取消按钮同上」与
  // 「支付途径为 Stripe 测试时不显示 [pay.choose_method]、三种演示方式、[pay.simulate_success]、[pay.simulate_failure] 与 [pay.action_hint]」。
  it.each(LANGUAGES)("swaps the title, ★ and actions on the Stripe route in %s", (language) => {
    for (const state of [null, "creating", "open", "under_review"] as const) {
      const html = renderStripe(state, {}, language);
      for (const key of ["pay.stripe_title", "pay.stripe_demo_hint", "common.demo_badge", "pay.order_no", "common.copy", "pay.save_order_no", "pay.amount_due", "pay.guest_access", "checkout.recipient_title", "pay.cancel_order"] as const) {
        expect(shows(html, key, language), `${String(state)} ${key}`).toBe(true);
      }
      for (const key of SIMULATED_ONLY) {
        expect(shows(html, key, language), `${String(state)} ${key}`).toBe(false);
      }
      expect(html).toContain(`<span class="acs-num site-pay__number">${ORDER_NUMBER}</span>`);
      expect(html).toContain(`<span class="acs-price acs-price--l">RM 135.00</span>`);
      expect(html).not.toContain("<input");
    }
  });

  // UX 0.11「◆ [pay.stripe_notice] 与 [pay.stripe_test_card] 在每次跳转前都显示，无勾选框（Stripe 节第 1、10 条）」与验收「用现有 ActionHint 的菱形标记」：
  // 各状态（含跳转中、不可用、失败）都有 ◆ 提示与测试卡号，◆ 是菱形标记、紧挨 pay.stripe_notice，测试卡号在其下；页面没有勾选框。
  it("always shows ◆ pay.stripe_notice and pay.stripe_test_card", () => {
    const cases: [StripeState, Partial<PayViewProps>][] = [
      [null, {}],
      ["creating", {}],
      ["open", {}],
      ["under_review", {}],
      [null, { busy: "redirecting" }],
      [null, { stripeUnavailable: true }],
      ["open", { failed: true }],
    ];
    for (const [state, overrides] of cases) {
      const html = renderStripe(state, overrides);
      expect(html).toContain(`${DIAMOND}</svg><span>${escapeHtml(COPY["pay.stripe_notice"].en)}</span>`);
      expect(html.indexOf(escapeHtml(COPY["pay.stripe_test_card"].en))).toBeGreaterThan(html.indexOf(escapeHtml(COPY["pay.stripe_notice"].en)));
      expect(html).not.toContain(`type="checkbox"`);
    }
  });

  // UX 0.11 桌面线框的顺序：[pay.stripe_title] ★ [pay.stripe_demo_hint]，订单卡片（未发起时 [pay.expires]），[pay.guest_access]，[checkout.recipient_title]，
  // 有进行中的会话时 [pay.stripe_in_progress]，◆ [pay.stripe_notice]，[pay.stripe_test_card]，跳转按钮，[pay.cancel_order]。
  it("follows the order of the Stripe wireframe", () => {
    const go = renderStripe(null);
    const goPositions = [
      go.indexOf(COPY["pay.stripe_title"].en),
      go.indexOf(escapeHtml(COPY["pay.stripe_demo_hint"].en)),
      go.indexOf(COPY["pay.order_no"].en),
      go.indexOf(COPY["pay.amount_due"].en),
      go.indexOf("Complete within 14 min"),
      go.indexOf(escapeHtml(COPY["pay.guest_access"].en)),
      go.indexOf("site-desktop-only site-pay__recipient"),
      go.indexOf(escapeHtml(COPY["pay.stripe_notice"].en)),
      go.indexOf(escapeHtml(COPY["pay.stripe_test_card"].en)),
      go.indexOf(COPY["pay.stripe_go"].en),
      go.indexOf(COPY["pay.cancel_order"].en),
    ];
    const open = renderStripe("open");
    const openPositions = [
      open.indexOf(escapeHtml(COPY["pay.guest_access"].en)),
      open.indexOf("site-desktop-only site-pay__recipient"),
      open.indexOf(COPY["pay.stripe_in_progress"].en),
      open.indexOf(escapeHtml(COPY["pay.stripe_notice"].en)),
      open.indexOf(escapeHtml(COPY["pay.stripe_test_card"].en)),
      open.indexOf(COPY["pay.stripe_continue"].en),
      open.indexOf(COPY["pay.cancel_order"].en),
    ];
    for (const positions of [goPositions, openPositions]) {
      for (const position of positions) {
        expect(position).toBeGreaterThanOrEqual(0);
      }
      expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    }
  });
});

describe("Stripe states", () => {
  // UX 0.11 说明「未发起…显示 [pay.expires] 倒计时」「创建结果未定…与未发起相同显示 [pay.stripe_go]，但不显示 [pay.expires]」
  // 「有进行中的会话…不显示 [pay.expires]，改显示 [pay.stripe_in_progress]；[pay.stripe_continue] 打开同一会话」
  // 「转人工核对时，不显示 [pay.stripe_go]，改显示 [pay.stripe_under_review]（取消按钮照常显示）」。
  it("shows the button and countdown each stripe_state calls for", () => {
    const expected: [StripeState, { expires: boolean; go: boolean; cont: boolean; progress: boolean; review: boolean }][] = [
      [null, { expires: true, go: true, cont: false, progress: false, review: false }],
      ["creating", { expires: false, go: true, cont: false, progress: false, review: false }],
      ["open", { expires: false, go: false, cont: true, progress: true, review: false }],
      ["under_review", { expires: false, go: false, cont: false, progress: false, review: true }],
    ];
    for (const [state, want] of expected) {
      const html = renderStripe(state);
      const node = tree({ screen: stripeScreen(state) });
      expect(html.includes("Complete within 14 min"), `${String(state)} expires`).toBe(want.expires);
      expect(labelled(node, "pay.stripe_go") !== undefined, `${String(state)} go`).toBe(want.go);
      expect(labelled(node, "pay.stripe_continue") !== undefined, `${String(state)} continue`).toBe(want.cont);
      expect(shows(html, "pay.stripe_in_progress"), `${String(state)} in progress`).toBe(want.progress);
      expect(shows(html, "pay.stripe_under_review"), `${String(state)} under review`).toBe(want.review);
      expect(button(node, "pay.cancel_order").props.disabled).toBe(false);
    }
    expect(stripeAction({ stripe_state: null })).toBe("go");
    expect(stripeAction({ stripe_state: "creating" })).toBe("go");
    expect(stripeAction({ stripe_state: "open" })).toBe("continue");
    expect(stripeAction({ stripe_state: "under_review" })).toBe("review");
    expect(stripeAction({ stripe_state: "complete" })).toBeNull();
  });

  // 验收「倒计时到 0 照现有写法重新读取（只在显示倒计时时）」与 UX 0.11「创建结果未定…这时订单要等对账才会取消」「有进行中的会话…会话结束前订单不会因 15 分钟到期被取消」：
  // 页面只在模拟途径与 Stripe 未发起时计时。
  it("counts down only where pay.expires is shown", () => {
    expect(showsCountdown({ payment_route: "simulated", stripe_state: null })).toBe(true);
    expect(showsCountdown({ payment_route: "stripe", stripe_state: null })).toBe(true);
    for (const state of ["creating", "open", "under_review"] as const) {
      expect(showsCountdown({ payment_route: "stripe", stripe_state: state })).toBe(false);
    }
  });

  // UX 0.11「打开本页时，本单若已有付款完成的会话…本页直接转到 P07」与 HANDOFF 0.55 第（5）项「P06 读到 complete 会话时转到结果页，由 P07 调用确认接口」：
  // 读到 complete 或订单已不是待支付时离开本页；待支付的其他状态留在本页。
  it("leaves for P07 on a complete session or a settled order", () => {
    expect(leavesPayPage({ status: "awaiting_demo_payment", stripe_state: "complete" })).toBe(true);
    expect(leavesPayPage({ status: "paid_demo", stripe_state: null })).toBe(true);
    for (const state of [null, "creating", "open", "under_review"] as const) {
      expect(leavesPayPage({ status: "awaiting_demo_payment", stripe_state: state })).toBe(false);
    }
  });
});

describe("Stripe redirect", () => {
  // UX 0.11「跳转中：按钮禁用，显示 [pay.stripe_redirecting]」与验收「进行中所有按钮禁用」：跳转中跳转按钮与取消按钮都禁用、按钮标 aria-busy，
  // 显示 pay.stripe_redirecting；不在跳转中时不显示。
  it("disables every button and shows pay.stripe_redirecting while redirecting", () => {
    for (const [state, key] of [
      [null, "pay.stripe_go"],
      ["open", "pay.stripe_continue"],
    ] as const) {
      const node = tree({ screen: stripeScreen(state), busy: "redirecting" });
      expect(labelled(node, key)?.props.disabled).toBe(true);
      expect(labelled(node, key)?.props["aria-busy"]).toBe(true);
      expect(button(node, "pay.cancel_order").props.disabled).toBe(true);
      expect(shows(renderStripe(state, { busy: "redirecting" }), "pay.stripe_redirecting")).toBe(true);
      const idle = tree({ screen: stripeScreen(state) });
      expect(labelled(idle, key)?.props.disabled).toBe(false);
      expect(shows(renderStripe(state), "pay.stripe_redirecting")).toBe(false);
    }
    for (const busy of ["cancelling", "checking"] as const) {
      expect(labelled(tree({ screen: stripeScreen(null), busy }), "pay.stripe_go")?.props.disabled).toBe(true);
    }
  });

  // 验收「点 pay.stripe_go 或 pay.stripe_continue…调用 startStripe」：两个按钮都交给页面的 onStripe（页面以读取给的令牌调用 startStripe）。
  it("passes both buttons to the page", () => {
    const onStripe = vi.fn();
    const go = labelled(tree({ screen: stripeScreen(null), onStripe }), "pay.stripe_go");
    const cont = labelled(tree({ screen: stripeScreen("open"), onStripe }), "pay.stripe_continue");
    if (!go || !cont) {
      throw new Error("no Stripe button");
    }
    click(go);
    click(cont);
    expect(onStripe).toHaveBeenCalledTimes(2);
  });

  // 验收「跳转」一条与 UX 0.11「随后在当前标签页打开服务端返回的 Stripe 付款页地址」「Stripe 暂不可用：按钮下显示 [pay.stripe_unavailable]，按钮恢复可点」
  // 「支付途径已改变：显示 [pay.method_changed]，重新读取订单并按新的途径显示」：startStripe 每种结果的去向。
  it("maps every startStripe result to where the page goes", () => {
    const url = "https://checkout.stripe.com/c/pay/cs_test_example";
    const cases: [StripeStart, StripeMove][] = [
      [{ kind: "redirect", url }, { kind: "redirect", url }],
      [{ kind: "complete" }, { kind: "result" }],
      [{ kind: "closed" }, { kind: "result" }],
      [{ kind: "unavailable" }, { kind: "unavailable" }],
      [{ kind: "under_review" }, { kind: "reread" }],
      [{ kind: "method_changed" }, { kind: "method_changed" }],
      [{ kind: "expired" }, { kind: "expired" }],
      [{ kind: "csrf" }, { kind: "error" }],
      [{ kind: "failed" }, { kind: "error" }],
      [{ kind: "network" }, { kind: "error" }],
    ];
    for (const [start, move] of cases) {
      expect(stripeMove(start), start.kind).toEqual(move);
    }
  });

  // UX 0.11「Stripe 暂不可用：按钮下显示 [pay.stripe_unavailable]，按钮恢复可点（再点即重试）」：提示在按钮之后，按钮可点；下一次跳转开始时不显示。
  it.each(LANGUAGES)("shows pay.stripe_unavailable under a clickable button in %s", (language) => {
    for (const [state, key] of [
      [null, "pay.stripe_go"],
      ["creating", "pay.stripe_go"],
      ["open", "pay.stripe_continue"],
    ] as const) {
      const html = renderStripe(state, { stripeUnavailable: true }, language);
      expect(html.indexOf(escapeHtml(COPY["pay.stripe_unavailable"][language]))).toBeGreaterThan(html.indexOf(escapeHtml(COPY[key][language])));
      expect(labelled(tree({ screen: stripeScreen(state), stripeUnavailable: true }), key)?.props.disabled).toBe(false);
      expect(shows(renderStripe(state, { stripeUnavailable: true, busy: "redirecting" }), "pay.stripe_unavailable")).toBe(false);
    }
  });

  // 验收「failed 与 network 显示 common.error_retry 并重新读取（已建成会话的按读到的 open 显示 pay.stripe_continue）」：
  // Stripe 途径失败时显示 common.error_retry，读到 open 时按钮为 pay.stripe_continue。
  it("shows common.error_retry after a failed start", () => {
    const html = renderStripe("open", { failed: true });
    expect(html).toContain(COPY["common.error_retry"].en);
    expect(labelled(tree({ screen: stripeScreen("open"), failed: true }), "pay.stripe_continue")?.props.disabled).toBe(false);
    expect(renderStripe(null)).not.toContain(COPY["common.error_retry"].en);
  });
});

describe("payment method changed", () => {
  // UX 0.11「支付途径已改变…页面显示 [pay.method_changed] 并重新读取。两种途径的页面都处理这一状态」：
  // 模拟途径（模拟支付遇到 method_changed，读到新的 Stripe 途径之前）与 Stripe 途径都显示 pay.method_changed；没有时不显示。
  it.each(LANGUAGES)("shows pay.method_changed on both routes in %s", (language) => {
    expect(shows(render({ methodChanged: true }, language), "pay.method_changed", language)).toBe(true);
    for (const state of [null, "creating", "open", "under_review"] as const) {
      expect(shows(renderStripe(state, { methodChanged: true }, language), "pay.method_changed", language)).toBe(true);
      expect(shows(renderStripe(state, {}, language), "pay.method_changed", language)).toBe(false);
    }
    expect(shows(render({}, language), "pay.method_changed", language)).toBe(false);
  });
});

describe("Stripe dictionary", () => {
  // 验收「页面文字全部来自字典」：Stripe 途径各状态下，除订单数据外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const cases: [StripeState, Partial<PayViewProps>][] = [
      [null, {}],
      ["creating", {}],
      ["open", {}],
      ["under_review", {}],
      [null, { busy: "redirecting" }],
      [null, { stripeUnavailable: true }],
      ["open", { failed: true, methodChanged: true }],
      ["open", { busy: "checking" }],
      [null, { confirming: true }],
    ];
    const orderData = new Set([ORDER_NUMBER, "Sam Taylor", "+447700900123", "1 Example Street", "AB1 2CD", "Greater London", countryName("GB", language), "·", ","]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const [state, overrides] of cases) {
      for (const value of visibleTexts(renderStripe(state, overrides, language))) {
        const known = orderData.has(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

// 确认框整段：开始标签的属性、其中的内容（到动作按钮所在的 div 结束）。
function dialogOf(html: string): { open: string; body: string } | null {
  const found = /(<div class="acs-dialog" role="dialog"[^>]*>)([\s\S]*?<\/div>)<\/div>/.exec(html);
  return found ? { open: found[1] ?? "", body: found[2] ?? "" } : null;
}

// 取消按钮所在的区块。
function cancelSection(html: string): string {
  return /<div class="site-pay__cancel[^"]*">[\s\S]*?<\/button><\/div>/.exec(html)?.[0] ?? "";
}

const READ = { kind: "ok", order: payOrder(), csrfToken: "csrf-new" } as const;

describe("Stripe cancel confirmation", () => {
  // UX 0.11「点取消后确认框：[pay.cancel_confirm]，有进行中的会话时其下另显示 [pay.cancel_confirm_stripe]」与视觉稿 P06-stripe-cancel-confirm：
  // Stripe 途径为 open 时补句紧接在 pay.cancel_confirm 之下、在按钮之前，作为对话框的说明（aria-describedby）。
  it.each(LANGUAGES)("adds pay.cancel_confirm_stripe under pay.cancel_confirm for an open session in %s", (language) => {
    const dialog = dialogOf(renderStripe("open", { confirming: true }, language));
    expect(dialog).not.toBeNull();
    const described = /aria-describedby="([^"]+)"/.exec(dialog?.open ?? "")?.[1] ?? "";
    expect(described).not.toBe("");
    expect(dialog?.body).toContain(
      `${escapeHtml(COPY["pay.cancel_confirm"][language])}</p><p id="${described}" class="acs-body-s acs-muted">${escapeHtml(COPY["pay.cancel_confirm_stripe"][language])}</p><div class="acs-dialog__actions">`,
    );
    expect(dialog?.body).toContain(`>${escapeHtml(COPY["pay.cancel_confirm_yes"][language])}</button>`);
    expect(dialog?.body).toContain(`>${escapeHtml(COPY["pay.cancel_confirm_no"][language])}</button>`);
  });

  // 同一句的「有进行中的会话时」与验收「其他情形（模拟途径、未发起、creating、under_review）确认框与现有相同」：
  // 这些情形的确认框里只有 pay.cancel_confirm 与两个按钮，没有补句与 aria-describedby，与模拟途径的确认框逐字相同。
  it.each(LANGUAGES)("keeps the existing dialog without an open session in %s", (language) => {
    // useId 给的编号随在树里的位置而变，比较前换成同一个占位。
    const withoutIds = (html: string) => html.replace(/(id|aria-labelledby)="[^"]*"/g, '$1="_"');
    const simulated = dialogOf(withoutIds(render({ confirming: true }, language)));
    expect(simulated).not.toBeNull();
    for (const html of [render({ confirming: true }, language), ...([null, "creating", "under_review"] as const).map((state) => renderStripe(state, { confirming: true }, language))]) {
      const dialog = dialogOf(withoutIds(html));
      expect(dialog).toEqual(simulated);
      expect(dialog?.open).not.toContain("aria-describedby");
      expect(shows(html, "pay.cancel_confirm_stripe", language)).toBe(false);
    }
    expect(confirmsStripeCancel({ payment_route: "stripe", stripe_state: "open" })).toBe(true);
    expect(confirmsStripeCancel({ payment_route: "simulated", stripe_state: "open" })).toBe(false);
    for (const state of [null, "creating", "under_review", "complete"] as const) {
      expect(confirmsStripeCancel({ payment_route: "stripe", stripe_state: state })).toBe(false);
    }
  });
});

describe("Stripe cancel not done", () => {
  // UX 0.11「取消时 Stripe 暂不可用：确认框关闭，显示 [pay.cancel_try_later]，订单保持待支付」、DESIGN 1.12 第 5 条「Stripe 不可用时本人取消提示稍后再试…订单保持待支付」
  // 与视觉稿 P06-stripe-states 的取消未成：提示在取消按钮旁（同一区块、按钮之上），订单号与应付金额照常显示，取消按钮可点（再点即重试）。
  it.each(LANGUAGES)("shows pay.cancel_try_later beside a clickable cancel button with the order kept in %s", (language) => {
    for (const screen of [stripeScreen("open"), stripeScreen(null), stripeScreen("creating"), stripeScreen("under_review")]) {
      const html = render({ screen, cancelTryLater: true }, language);
      const section = cancelSection(html);
      expect(section).toContain(`<div class="site-pay__cancel site-pay__cancel--try-later">`);
      expect(section).toContain(`role="alert"`);
      const notice = section.indexOf(escapeHtml(COPY["pay.cancel_try_later"][language]));
      expect(notice).toBeGreaterThan(0);
      expect(section.indexOf(escapeHtml(COPY["pay.cancel_order"][language]))).toBeGreaterThan(notice);
      expect(html).not.toContain(`role="dialog"`);
      expect(html).toContain(`<span class="acs-num site-pay__number">${ORDER_NUMBER}</span>`);
      expect(html).toContain(`<span class="acs-price acs-price--l">RM 135.00</span>`);
      expect(button(tree({ screen, cancelTryLater: true }), "pay.cancel_order").props.disabled).toBe(false);
    }
  });

  // 验收「下一次操作开始时收起」：操作进行中不显示；没有不可用时取消区块与现有相同、不出现 pay.cancel_try_later。
  it("hides pay.cancel_try_later while busy and when not set", () => {
    for (const busy of ["cancelling", "redirecting", "checking"] as const) {
      expect(shows(renderStripe("open", { cancelTryLater: true, busy }), "pay.cancel_try_later")).toBe(false);
    }
    const html = renderStripe("open");
    expect(shows(html, "pay.cancel_try_later")).toBe(false);
    expect(cancelSection(html)).toBe(`<div class="site-pay__cancel"><button class="acs-btn acs-btn--danger" type="button">${escapeHtml(COPY["pay.cancel_order"].en)}</button></div>`);
  });
});

describe("cancel outcome", () => {
  // DESIGN 1.12 第 5 条「Stripe 不可用时本人取消提示稍后再试」、UX 0.11「取消时会话已付款：→ P07 成功」与验收「其余结果（expired、csrf、failed、network）照现有写法」：
  // runCancel 每种结果的去向；转结果页时只有 Stripe 途径带交接（含 409 order_not_cancellable 归入的 result）。
  it("maps every cancel outcome to where the page goes", () => {
    const stripe = { payment_route: "stripe" } as const;
    const simulated = { payment_route: "simulated" } as const;
    const cases: [WriteOutcome, CancelMove, CancelMove][] = [
      [{ kind: "result" }, { kind: "result", handOver: true }, { kind: "result", handOver: false }],
      [{ kind: "unavailable" }, { kind: "try_later" }, { kind: "try_later" }],
      [{ kind: "expired" }, { kind: "expired" }, { kind: "expired" }],
      [{ kind: "method_changed" }, { kind: "error", read: null }, { kind: "error", read: null }],
      [{ kind: "error", read: null }, { kind: "error", read: null }, { kind: "error", read: null }],
      [{ kind: "error", read: READ }, { kind: "error", read: READ }, { kind: "error", read: READ }],
      [{ kind: "retry", read: READ, attempt: null }, { kind: "error", read: READ }, { kind: "error", read: READ }],
    ];
    for (const [outcome, onStripe, onSimulated] of cases) {
      expect(cancelMove(outcome, stripe), `stripe ${outcome.kind}`).toEqual(onStripe);
      expect(cancelMove(outcome, simulated), `simulated ${outcome.kind}`).toEqual(onSimulated);
    }
  });

  // HANDOFF 0.55 第（5）项「『取消时会话已付款』的提示只放在请求模块的内存里、读一次即失效」与验收「先以该订单号调用 rememberPaidNotCancelled 再用路由的 replace 换成 /pay/result」
  // 「模拟途径的取消与现有完全相同、不调用 remember」：路由以替身代替，replace 被调用时以 takePaidNotCancelled 核对交接已记下（Stripe）或没有（模拟）。
  it("remembers the hand-over before replacing with /pay/result only on the Stripe route", () => {
    takePaidNotCancelled("");
    for (const [route, expected] of [
      ["stripe", true],
      ["simulated", false],
    ] as const) {
      const calls: { path: RoutePath; handedOver: boolean }[] = [];
      const replace = (path: RoutePath) => {
        calls.push({ path, handedOver: takePaidNotCancelled(ORDER_NUMBER) });
      };
      const move = cancelMove({ kind: "result" }, { payment_route: route });
      if (move.kind !== "result") {
        throw new Error("not a result");
      }
      leaveAfterCancel(ORDER_NUMBER, move.handOver, replace);
      expect(calls, route).toEqual([{ path: PAY_RESULT_PATH, handedOver: expected }]);
      expect(PAY_RESULT_PATH).toBe("/pay/result");
    }
    expect(takePaidNotCancelled(ORDER_NUMBER)).toBe(false);
  });
});

describe("Stripe cancel dictionary", () => {
  // 验收「页面文字全部来自字典」：确认框补句与取消未成两种状态下（两种途径），除订单数据外每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const states: Partial<PayViewProps>[] = [
      { screen: stripeScreen("open"), confirming: true },
      { screen: stripeScreen("open"), cancelTryLater: true },
      { screen: stripeScreen("under_review"), cancelTryLater: true, failed: true },
      { cancelTryLater: true },
    ];
    const orderData = new Set([ORDER_NUMBER, "Sam Taylor", "+447700900123", "1 Example Street", "AB1 2CD", "Greater London", countryName("GB", language), "·", ","]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        const known = orderData.has(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

// ---- 会员模式（SHOP-TASK-161） ----

const TOKEN = "csrf-member";
const NEW_TOKEN = "csrf-member-new";
const GUEST_TOKEN = "csrf-guest";
const MEMBER_ORDER_EN = memberPayOrderUrl("en");
const GUEST_ORDERS_EN = payOrdersUrl("en");

// 回答：Response、网络中断，或在被请求时才生成的 Response（可在其中模拟离开页面）。
type Reply = Response | "network" | (() => Response);

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
      if (next === "network") {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      return Promise.resolve(typeof next === "function" ? next() : next);
    }),
  );
  return calls;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const memberSession = (token = TOKEN) => json(200, { phone_masked: "+60 12-*** 6789", has_password: true, expires_at: "2026-11-24T00:00:00Z", csrf_token: token });
const guestSession = () => json(401, { detail: "member_session_required" });
const memberOrder = (order: PayOrder = payOrder()) => json(200, order);
const guestOrders = (order: PayOrder = payOrder()) => json(200, { orders: [order], csrf_token: GUEST_TOKEN });
const notFound = () => json(404, { detail: "order_not_found" });
const unauthorized = () => json(401, { detail: "not_authenticated" });
const serverError = () => new Response(null, { status: 500 });

function urls(calls: readonly Call[]): string[] {
  return calls.map((call) => call.url);
}

function initOf(calls: readonly Call[], index: number): RequestInit {
  return calls[index]?.init ?? {};
}

function headerOf(init: RequestInit, name: string): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.[name];
}

function bodyOf(init: RequestInit): unknown {
  return JSON.parse(typeof init.body === "string" ? init.body : "null") as unknown;
}

// 暂存替身：current 取当前值，clear 清除并记下次数（真实暂存在 api/memberPay.ts，先内存、后 sessionStorage）。
function fakeStash(orderNumber: string | null) {
  let value = orderNumber;
  const clear = vi.fn(() => {
    value = null;
  });
  const stash: PayStash = { current: () => value, clear };
  return { stash, clear, current: () => value };
}

// 页面的处理替身：按顺序记下 same、reopen、redetermine 与 retry。
function fakeMemberMoves<T>() {
  const events: string[] = [];
  const values: T[] = [];
  const retries: [string | null, PayOrder | null][] = [];
  const moves: MemberMoves<T> = {
    same: (value) => {
      values.push(value);
      events.push("same");
    },
    reopen: () => {
      events.push("reopen");
    },
    redetermine: () => {
      events.push("redetermine");
    },
    retry: (token, order) => {
      retries.push([token, order]);
      events.push("retry");
    },
  };
  return { moves, events, values, retries };
}

const GUEST_READ = { kind: "ok", order: payOrder(), csrfToken: GUEST_TOKEN } as const;

describe("member mode: opening order ①", () => {
  // UX 0.13 P06「会员的当前订单」「本页与 P07 打开时先取页面内存里的订单号，没有再取 sessionStorage…① 有订单号时调用会员接口——回答正常照会员显示」
  // 与验收「ok 后经读取对象重读会话取 CSRF 令牌（会员读取不返回令牌），member 时显示会员 P06」：先以请求体里的订单号读会员订单（带页面的 signal），
  // 再重读会话（共享读取，不带页面的 signal），结果是会员订单与会话的令牌；暂存不清除，不读游客接口。
  it("reads the stashed order through the member API and takes the token from the session", async () => {
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_ORDER_EN]: [memberOrder()], [MEMBER_SESSION_URL]: [memberSession()] });
    const page = new AbortController();
    const opening = await openPayOrder("en", createMemberSessionReader(), page.signal, stash);
    expect(opening).toEqual<PayOpening>({ kind: "member", orderNumber: ORDER_NUMBER, order: payOrder(), csrfToken: TOKEN });
    expect(urls(calls)).toEqual([MEMBER_ORDER_EN, MEMBER_SESSION_URL]);
    expect(bodyOf(initOf(calls, 0))).toEqual({ order_number: ORDER_NUMBER });
    expect(initOf(calls, 0).signal).toBe(page.signal);
    expect(initOf(calls, 1).signal).toBeNull();
    expect(clear).not.toHaveBeenCalled();
  });

  // 同一段「回 401（会员会话已到期或已退出）清除暂存并按 ② 处理」与验收「guest 时与会员读取回 401 相同」「signed_out 时清除暂存并转 ②」：
  // 两种情形都清除暂存一次，再照游客读取，取到的订单照游客显示。
  it.each<[string, Record<string, Reply[]>, string[]]>([
    ["signed out", { [MEMBER_ORDER_EN]: [unauthorized()], [GUEST_ORDERS_EN]: [guestOrders()] }, [MEMBER_ORDER_EN, GUEST_ORDERS_EN]],
    [
      "read but the session is a guest",
      { [MEMBER_ORDER_EN]: [memberOrder()], [MEMBER_SESSION_URL]: [guestSession()], [GUEST_ORDERS_EN]: [guestOrders()] },
      [MEMBER_ORDER_EN, MEMBER_SESSION_URL, GUEST_ORDERS_EN],
    ],
  ])("clears the stash and goes to ② when %s", async (_name, routes, expected) => {
    const { stash, clear, current } = fakeStash(ORDER_NUMBER);
    const calls = stubFetch(routes);
    const opening = await openPayOrder("en", createMemberSessionReader(), new AbortController().signal, stash);
    expect(opening).toEqual<PayOpening>({ kind: "guest", read: GUEST_READ });
    expect(urls(calls)).toEqual(expected);
    expect(clear).toHaveBeenCalledTimes(1);
    expect(current()).toBeNull();
  });

  // 同一段「回 404（不是本人的订单）清除暂存并显示 [pay.member_reopen]」：清除暂存，不读会话与游客接口。
  it("clears the stash and shows pay.member_reopen when the order is not found", async () => {
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_ORDER_EN]: [notFound()] });
    const opening = await openPayOrder("en", createMemberSessionReader(), new AbortController().signal, stash);
    expect(opening).toEqual<PayOpening>({ kind: "reopen" });
    expect(urls(calls)).toEqual([MEMBER_ORDER_EN]);
    expect(clear).toHaveBeenCalledTimes(1);
  });

  // 同一段「回 5xx 或网络中断显示 [common.error_retry]，不清除暂存」与验收「failed 与 network（含重读会话 failed）时显示 common.error_retry、不清除暂存」。
  it.each<[string, Record<string, Reply[]>]>([
    ["the order read fails", { [MEMBER_ORDER_EN]: [serverError()] }],
    ["the network drops", { [MEMBER_ORDER_EN]: ["network"] }],
    ["the session read fails", { [MEMBER_ORDER_EN]: [memberOrder()], [MEMBER_SESSION_URL]: [serverError()] }],
    ["the session read drops", { [MEMBER_ORDER_EN]: [memberOrder()], [MEMBER_SESSION_URL]: ["network"] }],
  ])("shows common.error_retry and keeps the stash when %s", async (_name, routes) => {
    const { stash, clear, current } = fakeStash(ORDER_NUMBER);
    const calls = stubFetch(routes);
    const opening = await openPayOrder("en", createMemberSessionReader(), new AbortController().signal, stash);
    expect(opening).toEqual<PayOpening>({ kind: "error" });
    expect(urls(calls)).not.toContain(GUEST_ORDERS_EN);
    expect(clear).not.toHaveBeenCalled();
    expect(current()).toBe(ORDER_NUMBER);
  });

  // 验收「离开页面时中止请求，之后不再更新」「会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新」：
  // 读取回来时已离开不再读会话；重读会话期间离开则不给结果；两种情形都不清除暂存。
  it("gives no result after the page is left", async () => {
    const left = new AbortController();
    left.abort();
    const first = fakeStash(ORDER_NUMBER);
    const calls = stubFetch({ [MEMBER_ORDER_EN]: [memberOrder()] });
    await expect(openPayOrder("en", createMemberSessionReader(), left.signal, first.stash)).resolves.toBeNull();
    expect(urls(calls)).toEqual([MEMBER_ORDER_EN]);

    const page = new AbortController();
    const second = fakeStash(ORDER_NUMBER);
    stubFetch({
      [MEMBER_ORDER_EN]: [memberOrder()],
      [MEMBER_SESSION_URL]: [
        () => {
          page.abort();
          return guestSession();
        },
      ],
    });
    await expect(openPayOrder("en", createMemberSessionReader(), page.signal, second.stash)).resolves.toBeNull();
    expect(first.clear).not.toHaveBeenCalled();
    expect(second.clear).not.toHaveBeenCalled();
  });
});

describe("member mode: opening order ②", () => {
  // UX 0.13 P06「② 没有订单号时调用游客接口——取到订单照游客显示」与验收「游客路径其余行为不变」：只读游客接口，不读会员会话。
  it("reads the guest order without touching the member session", async () => {
    const { stash } = fakeStash(null);
    const calls = stubFetch({ [GUEST_ORDERS_EN]: [guestOrders()] });
    const opening = await openPayOrder("en", createMemberSessionReader(), new AbortController().signal, stash);
    expect(opening).toEqual<PayOpening>({ kind: "guest", read: GUEST_READ });
    expect(urls(calls)).toEqual([GUEST_ORDERS_EN]);
  });

  // 同一段「回 401 时读会员会话：有会话显示 [pay.member_reopen]…没有会话显示 [pay.session_expired]，读取会话失败显示 [common.error_retry]」
  // 与 HANDOFF 0.63 第（1）项「打开时游客读取回 401 才读会员会话」。
  it.each<[string, Reply, PayOpening]>([
    ["member", memberSession(), { kind: "reopen" }],
    ["guest", guestSession(), { kind: "expired" }],
    ["failed", serverError(), { kind: "error" }],
    ["network", "network", { kind: "error" }],
  ])("reads the member session after a guest 401 (%s)", async (_name, session, expected) => {
    const { stash } = fakeStash(null);
    const calls = stubFetch({ [GUEST_ORDERS_EN]: [unauthorized()], [MEMBER_SESSION_URL]: [session] });
    const opening = await openPayOrder("en", createMemberSessionReader(), new AbortController().signal, stash);
    expect(opening).toEqual(expected);
    expect(urls(calls)).toEqual([GUEST_ORDERS_EN, MEMBER_SESSION_URL]);
    expect(initOf(calls, 1).signal).toBeNull();
  });

  // 同一条「游客路径其余行为不变」：游客读取失败照现有写法为 common.error_retry，不读会员会话。
  it("shows common.error_retry for a failed guest read without reading the session", async () => {
    const { stash } = fakeStash(null);
    const calls = stubFetch({ [GUEST_ORDERS_EN]: [serverError()] });
    await expect(openPayOrder("en", createMemberSessionReader(), new AbortController().signal, stash)).resolves.toEqual({ kind: "error" });
    expect(urls(calls)).toEqual([GUEST_ORDERS_EN]);
  });

  // 同一段的三种会话结果，按纯函数逐一列出；尚未读完（unknown）按失败处理。
  it("maps the session after a guest 401", () => {
    expect(guestExpiredStep({ kind: "member", phoneMasked: "+60 12-*** 6789", hasPassword: true, csrfToken: TOKEN })).toEqual({ kind: "reopen" });
    expect(guestExpiredStep({ kind: "guest" })).toEqual({ kind: "expired" });
    expect(guestExpiredStep({ kind: "failed" })).toEqual({ kind: "error" });
    expect(guestExpiredStep({ kind: "unknown" })).toEqual({ kind: "error" });
  });
});

describe("member mode: content", () => {
  // HANDOFF 0.63「会员 P06 正常态照游客 P06 去掉 pay.guest_access（没有会员正常态的视觉稿）」与验收「会员 P06 的内容——与游客 P06 相同…不显示 pay.guest_access」：
  // 模拟途径与 Stripe 各状态下，会员页与游客页去掉 pay.guest_access 那一条提示之后逐字相同；游客页仍有。
  it.each(LANGUAGES)("is the guest page without pay.guest_access in %s", (language) => {
    const guestAccess = escapeHtml(COPY["pay.guest_access"][language]);
    const notice = new RegExp(`<div class="acs-alert acs-alert--info"><svg[^>]*>(?:(?!</svg>)[\\s\\S])*</svg><span>${guestAccess.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}</span></div>`);
    const states: Partial<PayViewProps>[] = [
      {},
      { method: "card", failed: true },
      { screen: stripeScreen(null) },
      { screen: stripeScreen("open"), confirming: true },
      { screen: stripeScreen("under_review"), cancelTryLater: true },
    ];
    for (const state of states) {
      const guest = render(state, language);
      const member = render({ ...state, member: true }, language);
      expect(guest).toMatch(notice);
      expect(member).not.toContain(guestAccess);
      expect(member).toBe(guest.replace(notice, ""));
    }
  });

  // UX 0.13 P06 线框「游客：[G] [pay.guest_access]」只对游客：会员页仍有订单号与复制、应付金额、倒计时、收货资料、支付途径与取消。
  it.each(LANGUAGES)("keeps every other element of the wireframe for a member in %s", (language) => {
    const html = render({ member: true }, language);
    for (const key of ["pay.title", "pay.order_no", "common.copy", "pay.save_order_no", "pay.amount_due", "checkout.recipient_title", "pay.choose_method", "pay.simulate_success", "pay.simulate_failure", "pay.action_hint", "pay.cancel_order"] as const) {
      expect(shows(html, key, language), key).toBe(true);
    }
    expect(html).toContain(escapeHtml(COPY["pay.expires"][language].replace("{minutes}", "14")));
    expect(shows(render({ member: true, screen: stripeScreen("open") }, language), "pay.stripe_continue", language)).toBe(true);
  });

  // UX 0.13 P06「会员找不到这笔订单时替换整页 | [pay.member_reopen] | ( [account.orders] ) → P13 我的订单」与视觉稿 P06-P07-member-reopen
  // （整页只留提示与 account.orders 主按钮）：只有这两段文字，唯一的链接去 /account，没有按钮与订单内容。
  it.each(LANGUAGES)("replaces the whole page with pay.member_reopen in %s", (language) => {
    for (const member of [true, false]) {
      const html = render({ screen: { status: "reopen" }, member }, language);
      expect(visibleTexts(html)).toEqual([COPY["pay.member_reopen"][language], COPY["account.orders"][language]]);
      expect(html).toContain(`<a class="acs-btn acs-btn--primary" href="/account">${escapeHtml(COPY["account.orders"][language])}</a>`);
      expect([...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1])).toEqual(["/account"]);
      expect(html).toContain(`<main class="site-pay site-pay--reopen">`);
      expect(html).not.toContain("<button");
      expect(html).not.toContain(ORDER_NUMBER);
    }
  });

  // 验收「页面文字全部来自字典」：会员页各状态与 pay.member_reopen 整页，除订单数据外每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const states: Partial<PayViewProps>[] = [
      { member: true },
      { member: true, method: "card", busy: "checking" },
      { member: true, screen: stripeScreen("open"), failed: true, confirming: true },
      { member: true, screen: { status: "error" } },
      { screen: { status: "reopen" } },
    ];
    const orderData = new Set([ORDER_NUMBER, "Sam Taylor", "+447700900123", "1 Example Street", "AB1 2CD", "Greater London", countryName("GB", language), "·", ","]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        const known = orderData.has(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

describe("member mode: rereading", () => {
  // 验收「切换语言时重新读取同一订单」：以各语言读取暂存的同一订单号，结果交给页面（令牌不变）。
  it.each(LANGUAGES)("rereads the same order in %s", async (language) => {
    const url = memberPayOrderUrl(language);
    const calls = stubFetch({ [url]: [memberOrder()] });
    const { stash } = fakeStash(ORDER_NUMBER);
    const { moves, events, values } = fakeMemberMoves<PayOrder | null>();
    await rereadMemberOrder(language, ORDER_NUMBER, createMemberSessionReader(), new AbortController().signal, moves, stash);
    expect(urls(calls)).toEqual([url]);
    expect(url).toContain(`lang=${language}`);
    expect(bodyOf(initOf(calls, 0))).toEqual({ order_number: ORDER_NUMBER });
    expect(events).toEqual(["same"]);
    expect(values).toEqual([payOrder()]);
  });

  // DESIGN 1.14 第 7 条「会员接口对它回答会话无效或订单不存在时」清除，与 UX P06 ① 同一去向：重读回 401 清除并重新判定，404 清除并显示 pay.member_reopen；
  // failed 与 network 照游客显示 common.error_retry（null），不清除。
  it.each<[string, Reply, string[], boolean]>([
    ["signed out", unauthorized(), ["redetermine"], true],
    ["not found", notFound(), ["reopen"], true],
    ["failed", serverError(), ["same"], false],
    ["network", "network", ["same"], false],
  ])("handles a reread that is %s", async (_name, reply, expected, cleared) => {
    stubFetch({ [MEMBER_ORDER_EN]: [reply] });
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    const { moves, events, values } = fakeMemberMoves<PayOrder | null>();
    await rereadMemberOrder("en", ORDER_NUMBER, createMemberSessionReader(), new AbortController().signal, moves, stash);
    expect(events).toEqual(expected);
    expect(clear).toHaveBeenCalledTimes(cleared ? 1 : 0);
    if (expected[0] === "same") {
      expect(values).toEqual([null]);
    }
  });

  // 同一去向按纯函数逐一列出：ok 为订单（令牌不变），signed_out 与 not_found 交给会员自己的去向，failed 与 network 为 null。
  it("maps every member read", () => {
    const cases: [MemberPayRead, MemberStep<PayOrder | null>][] = [
      [{ kind: "ok", order: payOrder() }, { kind: "same", value: payOrder() }],
      [{ kind: "signed_out" }, { kind: "signed_out" }],
      [{ kind: "not_found" }, { kind: "not_found" }],
      [{ kind: "failed" }, { kind: "same", value: null }],
      [{ kind: "network" }, { kind: "same", value: null }],
    ];
    for (const [read, step] of cases) {
      expect(memberReadStep(read), read.kind).toEqual(step);
    }
  });
});

const ATTEMPT: PaymentAttempt = { key: "attempt-key-1", method: "card", result: "success" };
const TARGET = { orderNumber: ORDER_NUMBER, csrfToken: TOKEN };

describe("member mode: writes", () => {
  // 验收「会员的写操作——模拟支付…经 SHOP-TASK-158 的会员版，以会话里的 CSRF 令牌提交，幂等键规则与游客相同；result 时与游客相同前往 /pay/result
  // （不清除暂存，由结果页在结果确定后清除）」：请求头带会话的令牌与给出的幂等键，请求体只有订单号、方式与结果；结果交给游客的处理（result），暂存不清除。
  it("pays with the session token and goes to the result like a guest without clearing the stash", async () => {
    const calls = stubFetch({ [MEMBER_ATTEMPTS_URL]: [json(201, { ok: true })] });
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    const { moves, events, values } = fakeMemberMoves<WriteOutcome>();
    const page = new AbortController();
    await payAsMember("en", TARGET, ATTEMPT, createMemberSessionReader(), { signal: page.signal }, moves, stash);
    expect(urls(calls)).toEqual([MEMBER_ATTEMPTS_URL]);
    expect(headerOf(initOf(calls, 0), CSRF_HEADER)).toBe(TOKEN);
    expect(headerOf(initOf(calls, 0), IDEMPOTENCY_HEADER)).toBe(ATTEMPT.key);
    expect(bodyOf(initOf(calls, 0))).toEqual({ order_number: ORDER_NUMBER, method: "card", result: "success" });
    expect(events).toEqual(["same"]);
    expect(values).toEqual([{ kind: "result" }]);
    expect(clear).not.toHaveBeenCalled();
  });

  // 同一条「signed_out 时清除暂存并按 ② 重新判定；not_found 时清除暂存并显示 pay.member_reopen」：支付、取消与 Stripe 发起都一样。
  it.each<[string, Response, string]>([
    ["signed out", unauthorized(), "redetermine"],
    ["not found", notFound(), "reopen"],
  ])("clears the stash when a write is %s", async (_name, reply, expected) => {
    // 每种写操作返回页面收到的处理顺序。
    const runs: [string, (stash: PayStash) => Promise<string[]>][] = [
      [
        MEMBER_ATTEMPTS_URL,
        async (stash) => {
          const { moves, events } = fakeMemberMoves<WriteOutcome>();
          await payAsMember("en", TARGET, ATTEMPT, createMemberSessionReader(), { signal: new AbortController().signal }, moves, stash);
          return events;
        },
      ],
      [
        MEMBER_CANCEL_URL,
        async (stash) => {
          const { moves, events } = fakeMemberMoves<WriteOutcome>();
          await cancelAsMember("en", TARGET, createMemberSessionReader(), { signal: new AbortController().signal }, moves, stash);
          return events;
        },
      ],
      [
        memberStripeStartUrl("en"),
        async (stash) => {
          const { moves, events } = fakeMemberMoves<StripeStart>();
          await startStripeAsMember("en", TARGET, createMemberSessionReader(), new AbortController().signal, moves, stash);
          return events;
        },
      ],
    ];
    for (const [url, run] of runs) {
      stubFetch({ [url]: [reply.clone()] });
      const { stash, clear } = fakeStash(ORDER_NUMBER);
      expect(await run(stash), url).toEqual([expected]);
      expect(clear, url).toHaveBeenCalledTimes(1);
    }
  });

  // 同一条「csrf 时重读会话——member 时换用新令牌并显示 common.error_retry，guest 时清除暂存并按 ② 重新判定，重读得到 failed 时保留原令牌并显示 common.error_retry」：
  // 403 之后接口先重新读取订单（仍待支付），再重读会话；retry 带新令牌或 null（保留原令牌）与重新读取到的订单。
  it.each<[string, Reply, string[], [string | null, PayOrder | null][], boolean]>([
    ["member", memberSession(NEW_TOKEN), ["retry"], [[NEW_TOKEN, payOrder()]], false],
    ["guest", guestSession(), ["redetermine"], [], true],
    ["failed", serverError(), ["retry"], [[null, payOrder()]], false],
    ["network", "network", ["retry"], [[null, payOrder()]], false],
  ])("rereads the session after a csrf failure (%s)", async (_name, session, expected, retries, cleared) => {
    const calls = stubFetch({ [MEMBER_ATTEMPTS_URL]: [json(403, { detail: "csrf_failed" })], [MEMBER_ORDER_EN]: [memberOrder()], [MEMBER_SESSION_URL]: [session] });
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    const fake = fakeMemberMoves<WriteOutcome>();
    await payAsMember("en", TARGET, ATTEMPT, createMemberSessionReader(), { signal: new AbortController().signal }, fake.moves, stash);
    expect(urls(calls)).toEqual([MEMBER_ATTEMPTS_URL, MEMBER_ORDER_EN, MEMBER_SESSION_URL]);
    expect(initOf(calls, 2).signal).toBeNull();
    expect(fake.events).toEqual(expected);
    expect(fake.retries).toEqual(retries);
    expect(clear).toHaveBeenCalledTimes(cleared ? 1 : 0);
  });

  // 同一条用于取消与 Stripe 发起：取消的 403 之后同样重新读取订单再重读会话；Stripe 发起的 403 直接重读会话（没有重新读取到的订单）。
  it("rereads the session after a csrf failure when cancelling or starting Stripe", async () => {
    stubFetch({ [MEMBER_CANCEL_URL]: [json(403, { detail: "csrf_failed" })], [MEMBER_ORDER_EN]: [memberOrder()], [MEMBER_SESSION_URL]: [memberSession(NEW_TOKEN)] });
    const cancel = fakeMemberMoves<WriteOutcome>();
    await cancelAsMember("en", TARGET, createMemberSessionReader(), { signal: new AbortController().signal }, cancel.moves, fakeStash(ORDER_NUMBER).stash);
    expect(cancel.retries).toEqual([[NEW_TOKEN, payOrder()]]);

    const calls = stubFetch({ [memberStripeStartUrl("en")]: [json(403, { detail: "csrf_failed" })], [MEMBER_SESSION_URL]: [memberSession(NEW_TOKEN)] });
    const stripe = fakeMemberMoves<StripeStart>();
    await startStripeAsMember("en", TARGET, createMemberSessionReader(), new AbortController().signal, stripe.moves, fakeStash(ORDER_NUMBER).stash);
    expect(urls(calls)).toEqual([memberStripeStartUrl("en"), MEMBER_SESSION_URL]);
    expect(headerOf(initOf(calls, 0), CSRF_HEADER)).toBe(TOKEN);
    expect(stripe.retries).toEqual([[NEW_TOKEN, null]]);
  });

  // 同一条「method_changed、unavailable、error、retry 与 Stripe 跳转与游客相同」「result 时与游客相同前往 /pay/result（不清除暂存）」：
  // 这些结果（含 409 order_not_payable 归入的 result）原样换成游客的形式交给游客的处理，暂存不清除。
  it.each<[string, Reply[], WriteOutcome]>([
    ["method_changed", [json(409, { detail: "method_changed" })], { kind: "method_changed" }],
    ["unavailable", [json(503, { detail: "stripe_unavailable" })], { kind: "unavailable" }],
    ["failed", [serverError()], { kind: "error", read: null }],
    ["closed", [json(409, { detail: "order_not_payable" })], { kind: "result" }],
  ])("hands %s to the guest handling", async (_name, replies, expected) => {
    stubFetch({ [MEMBER_ATTEMPTS_URL]: replies });
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    const { moves, values } = fakeMemberMoves<WriteOutcome>();
    await payAsMember("en", TARGET, ATTEMPT, createMemberSessionReader(), { signal: new AbortController().signal }, moves, stash);
    expect(values).toEqual([expected]);
    expect(clear).not.toHaveBeenCalled();
  });

  // 同一条「retry 与游客相同」「幂等键规则与游客相同」与 UX「全局框架」网络中断「先显示 [common.network_check] 并查询原订单…再允许重试」：
  // 网络中断时先通知页面显示 common.network_check，重新读取到仍待支付后为 retry，带提交时的令牌与同一幂等键。
  it("checks the order after a network failure and offers the same attempt again", async () => {
    stubFetch({ [MEMBER_ATTEMPTS_URL]: ["network"], [MEMBER_ORDER_EN]: [memberOrder()] });
    const onChecking = vi.fn();
    const { moves, values } = fakeMemberMoves<WriteOutcome>();
    await payAsMember("en", TARGET, ATTEMPT, createMemberSessionReader(), { signal: new AbortController().signal, onChecking }, moves, fakeStash(ORDER_NUMBER).stash);
    expect(onChecking).toHaveBeenCalledTimes(1);
    expect(values).toEqual([{ kind: "retry", read: { kind: "ok", order: payOrder(), csrfToken: TOKEN }, attempt: ATTEMPT }]);
  });

  // 同一条的 Stripe 部分：跳转、转人工核对与不可用原样交给 stripeMove；取消成功交给游客的取消处理（result）。
  it("hands Stripe starts and cancels to the guest handling", async () => {
    const url = "https://checkout.stripe.com/c/pay/cs_test_example";
    const cases: [Response, StripeStart][] = [
      [json(200, { result: "redirect", checkout_url: url }), { kind: "redirect", url }],
      [json(409, { detail: "stripe_under_review" }), { kind: "under_review" }],
      [json(503, { detail: "stripe_unavailable" }), { kind: "unavailable" }],
    ];
    for (const [reply, expected] of cases) {
      stubFetch({ [memberStripeStartUrl("en")]: [reply] });
      const { moves, values } = fakeMemberMoves<StripeStart>();
      await startStripeAsMember("en", TARGET, createMemberSessionReader(), new AbortController().signal, moves, fakeStash(ORDER_NUMBER).stash);
      expect(values).toEqual([expected]);
    }
    const calls = stubFetch({ [MEMBER_CANCEL_URL]: [json(200, { ok: true })] });
    const cancel = fakeMemberMoves<WriteOutcome>();
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    await cancelAsMember("en", TARGET, createMemberSessionReader(), { signal: new AbortController().signal }, cancel.moves, stash);
    expect(headerOf(initOf(calls, 0), CSRF_HEADER)).toBe(TOKEN);
    expect(bodyOf(initOf(calls, 0))).toEqual({ order_number: ORDER_NUMBER });
    expect(cancel.values).toEqual([{ kind: "result" }]);
    expect(clear).not.toHaveBeenCalled();
  });

  // 验收「会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：csrf 之后重读会话期间离开页面，不调用任何处理，也不清除暂存。
  it("does nothing after the page is left during the session reread", async () => {
    const page = new AbortController();
    stubFetch({
      [MEMBER_ATTEMPTS_URL]: [json(403, { detail: "csrf_failed" })],
      [MEMBER_ORDER_EN]: [memberOrder()],
      [MEMBER_SESSION_URL]: [
        () => {
          page.abort();
          return guestSession();
        },
      ],
    });
    const { stash, clear } = fakeStash(ORDER_NUMBER);
    const { moves, events } = fakeMemberMoves<WriteOutcome>();
    await payAsMember("en", TARGET, ATTEMPT, createMemberSessionReader(), { signal: page.signal }, moves, stash);
    expect(events).toEqual([]);
    expect(clear).not.toHaveBeenCalled();
  });

  // 验收「会员的写操作」各结果的去向，按纯函数逐一列出：与游客相同的换成游客的形式（retry 配上提交时的令牌），其余为会员自己的去向。
  it("maps every member write and Stripe start", () => {
    const writes: [MemberWriteOutcome, MemberStep<WriteOutcome>][] = [
      [{ kind: "result" }, { kind: "same", value: { kind: "result" } }],
      [{ kind: "method_changed" }, { kind: "same", value: { kind: "method_changed" } }],
      [{ kind: "unavailable" }, { kind: "same", value: { kind: "unavailable" } }],
      [{ kind: "error" }, { kind: "same", value: { kind: "error", read: null } }],
      [{ kind: "retry", order: payOrder(), attempt: ATTEMPT }, { kind: "same", value: { kind: "retry", read: { kind: "ok", order: payOrder(), csrfToken: TOKEN }, attempt: ATTEMPT } }],
      [{ kind: "signed_out" }, { kind: "signed_out" }],
      [{ kind: "not_found" }, { kind: "not_found" }],
      [{ kind: "csrf", order: null }, { kind: "csrf", order: null }],
      [{ kind: "csrf", order: payOrder() }, { kind: "csrf", order: payOrder() }],
    ];
    for (const [outcome, step] of writes) {
      expect(memberWriteStep(outcome, TOKEN), outcome.kind).toEqual(step);
    }
    const starts: [MemberStripeStart, MemberStep<StripeStart>][] = [
      [{ kind: "redirect", url: "https://checkout.stripe.com/x" }, { kind: "same", value: { kind: "redirect", url: "https://checkout.stripe.com/x" } }],
      [{ kind: "complete" }, { kind: "same", value: { kind: "complete" } }],
      [{ kind: "closed" }, { kind: "same", value: { kind: "closed" } }],
      [{ kind: "method_changed" }, { kind: "same", value: { kind: "method_changed" } }],
      [{ kind: "under_review" }, { kind: "same", value: { kind: "under_review" } }],
      [{ kind: "unavailable" }, { kind: "same", value: { kind: "unavailable" } }],
      [{ kind: "failed" }, { kind: "same", value: { kind: "failed" } }],
      [{ kind: "network" }, { kind: "same", value: { kind: "network" } }],
      [{ kind: "signed_out" }, { kind: "signed_out" }],
      [{ kind: "not_found" }, { kind: "not_found" }],
      [{ kind: "csrf" }, { kind: "csrf", order: null }],
    ];
    for (const [start, step] of starts) {
      expect(memberStripeStep(start), start.kind).toEqual(step);
    }
  });
});
