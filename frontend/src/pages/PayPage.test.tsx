import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { PayOrder } from "../api/pay";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { canSubmit, copyText, countryName, PayView, recipientParts } from "./PayPage";
import type { PayViewProps } from "./PayPage";

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";

function payOrder(overrides: Partial<PayOrder> = {}): PayOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "awaiting_demo_payment",
    created_at: "2026-10-05T10:00:00Z",
    payment_expires_at: "2026-10-05T10:15:00Z",
    server_time: "2026-10-05T10:01:00Z",
    subtotal_sen: 12000,
    shipping_fee_sen: 1500,
    total_sen: 13500,
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
    onMethod: noop,
    onPay: noop,
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
