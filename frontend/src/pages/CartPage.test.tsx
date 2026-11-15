import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchQuote, latestQuoter } from "../api/checkout";
import type { CheckoutQuote, PricedQuoteLine, PromotionQuote, QuoteLine, UnavailableQuoteLine } from "../api/checkout";
import { MemberSessionProvider } from "../api/memberSession";
import type { MemberSessionReader, MemberSessionState } from "../api/memberSession";
import type { PromotionDisplay, PromotionResponse, RuleDisplay } from "../api/promotion";
import App from "../App";
import { CART_STORAGE_KEY, readCart } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { BRAND, COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, CopyVars, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PromotionProvider } from "../promotion";
import { RouterProvider } from "../router";
import {
  CartView,
  canIncrease,
  cartProgress,
  changeQuantity,
  hasChangedLine,
  lineQuantityInput,
  quantityLimit,
  quoteKey,
  quoteState,
  removeFromCart,
} from "./CartPage";
import type { QuantityDraft, QuoteState } from "./CartPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

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
    image: "/img/tee.svg",
    unit_price_sen: 3900,
    line_subtotal_sen: 3900,
    promotion_rule_id: null,
    promotion_item_discount_sen: 0,
    ...overrides,
  };
}

function unavailable(sku: string, quantity = 1): UnavailableQuoteLine {
  return { sku, quantity, status: "unavailable" };
}

function cartOf(rows: readonly QuoteLine[]): CartLine[] {
  return rows.map((row) => ({ sku: row.sku, slug: row.status === "unavailable" ? "gone" : row.product_slug, quantity: row.quantity }));
}

const WITHIN_LIMITS = { unit_limit: 2000, units_over_limit: false, amount_limit_sen: 99999999, amount_over_limit: false, promotion: null };

function ready(lines: QuoteLine[], subtotal_sen = 0, limits: Partial<CheckoutQuote> = {}): QuoteState {
  return { status: "ready", quote: { lines, subtotal_sen, ...WITHIN_LIMITS, ...limits }, pending: false };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  lines?: readonly CartLine[];
  quote?: QuoteState;
  writeFailed?: boolean;
  draft?: QuantityDraft | null;
  capped?: ReadonlyMap<string, number>;
  language?: Language;
  // 共享读取的活动（PromotionProvider 的 initial）；"loading" 为第一次读取尚未完成；不给时不包 PromotionProvider（没有活动）。
  promotion?: PromotionResponse | "loading";
  // 会员会话状态的替身；不给时不包 MemberSessionProvider。
  session?: MemberSessionState;
}

function sessionReader(state: MemberSessionState): MemberSessionReader {
  return {
    current: () => state,
    load: () => Promise.resolve(state),
    reload: () => Promise.resolve(state),
    subscribe: () => () => undefined,
  };
}

function render({
  lines = [],
  quote = { status: "loading" },
  writeFailed = false,
  draft = null,
  capped = new Map(),
  language = "en",
  promotion,
  session,
}: RenderOptions = {}): string {
  let view: ReactElement = (
    <CartView
      lines={lines}
      quote={quote}
      writeFailed={writeFailed}
      draft={draft}
      capped={capped}
      onQuantity={() => undefined}
      onDraft={() => undefined}
      onQuantityInput={() => undefined}
      onRemove={() => undefined}
    />
  );
  if (promotion !== undefined) {
    view = (
      <PromotionProvider
        initial={promotion === "loading" ? undefined : promotion}
        fetcher={() => new Promise<PromotionResponse>(() => undefined)}
        clock={() => 0}
      >
        {view}
      </PromotionProvider>
    );
  }
  if (session !== undefined) {
    view = <MemberSessionProvider reader={sessionReader(session)}>{view}</MemberSessionProvider>;
  }
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/cart">{view}</RouterProvider>
    </LanguageProvider>,
  );
}

function renderRows(rows: QuoteLine[], options: Omit<RenderOptions, "lines" | "quote"> & { subtotal?: number; limits?: Partial<CheckoutQuote> } = {}): string {
  const { subtotal = 0, limits = {}, ...rest } = options;
  return render({ lines: cartOf(rows), quote: ready(rows, subtotal, limits), ...rest });
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

function items(html: string): string[] {
  return [...html.matchAll(/<div class="site-cart__item">[\s\S]*?<\/button><\/div>(?:<p class="acs-field__(?:hint|error) site-cart__note"[^>]*>[^<]*<\/p>)*<\/div>/g)].map((m) => m[0]);
}

function regexEscape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const quantityInput = (html: string) => part(html, /<input class="acs-input site-cart__qty-input"[^>]*\/>/);

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

const increase = (language: Language) => escapeHtml(COPY["common.a11y_qty_increase"][language]);
const decrease = (language: Language) => escapeHtml(COPY["common.a11y_qty_decrease"][language]);

describe("lines and amounts", () => {
  // UX P04 线框「[图] <名称> / <规格> (-) 2 (+) [M1] ([cart.remove])」与 SHOP-TASK-018 验收第 3 条「显示返回的每行图片、名称与规格、件数、行小计」：
  // 名称链到商品详情，规格值之间是图形分隔，件数在步进器的数量输入框里（0.17 起数量可直接输入，原为 output；本任务改这一条断言）。
  it.each(LANGUAGES)("shows the image, name, options, quantity, line subtotal and remove button in %s", (language) => {
    const row = priced("tee-black-m", { quantity: 2, line_subtotal_sen: 7800 });
    const [item = ""] = items(renderRows([row], { language }));
    expect(item).toContain(`<span class="acs-cat__img site-cart__img"><img class="site-img" src="/img/tee.svg" alt="" loading="lazy"/></span>`);
    expect(item).toContain(`<a href="/products/crew-neck-tee">Crew Neck Tee</a>`);
    expect(item).toMatch(/<span>Black<\/span><\/span><span class="site-cart__option"><svg[^>]*aria-hidden="true">[\s\S]*?<\/svg><span>M<\/span>/);
    expect(quantityInput(item)).toContain(`value="2"`);
    expect(item).toContain(`<span class="acs-price site-cart__price">RM 78.00</span>`);
    expect(item).toContain(`type="button">${COPY["cart.remove"][language]}</button>`);
  });

  // UX P04 的 M1「行小计由服务端按当前价格返回，不信任浏览器保存的价格」、M2 与 SHOP-TASK-018 验收第 3 条「金额只格式化接口返回的整数仙，不在浏览器相加或相乘」：
  // 行小计与单价×件数不符、商品小计与各行之和不符时，照样显示接口给的数。
  it("shows the amounts exactly as the quote returns them", () => {
    const rows = [priced("tee-black-m", { quantity: 2, unit_price_sen: 3900, line_subtotal_sen: 7799 }), priced("candle", { product_slug: "candle", line_subtotal_sen: 3200 })];
    const html = renderRows(rows, { subtotal: 12345 });
    expect(html).toContain(">RM 77.99<");
    expect(html).toContain(">RM 32.00<");
    expect(html).not.toContain("RM 78.00");
    expect(html).not.toContain("RM 39.00");
    expect(html).not.toContain("RM 109.99");
    const summary = part(html, /<aside class="acs-summary site-desktop-only site-cart__summary">[\s\S]*?<\/aside>/);
    expect(summary).toContain(`<div class="acs-row"><span>${COPY["cart.subtotal"].en}</span><span class="acs-price">RM 123.45</span></div>`);
    const bar = part(html, /<div class="acs site-phone-only site-cart__bar"><div class="acs-row">[\s\S]*?<\/div>/);
    expect(bar).toContain(`<span class="acs-price">RM 123.45</span>`);
  });

  // UX P04 M1、M2「仅 MYR」：不显示参考外币与运费金额。
  it("shows no reference currency or shipping amount", () => {
    const html = renderRows([priced("tee-black-m")], { subtotal: 3900 });
    expect(html).not.toContain("≈");
    expect(html).not.toMatch(/SGD|USD|CNY/);
  });

  // UX P04「演示提示：★ [cart.demo_hint]；[cart.price_recheck]」与线框的 [cart.shipping_later]、[cart.price_recheck]：桌面在小计栏，手机在各行之后。
  it.each(LANGUAGES)("shows the demo hint, the shipping note and the price recheck note in %s", (language) => {
    const html = renderRows([priced("tee-black-m")], { language });
    expect(part(html, /<div class="site-cart__head">[\s\S]*?<\/div>/)).toContain(`<p class="acs-hint">`);
    expect(html).toContain(escapeHtml(COPY["cart.demo_hint"][language]));
    const summary = part(html, /<aside[\s\S]*?<\/aside>/);
    const notes = part(html, /<div class="site-phone-only site-cart__notes">[\s\S]*?<\/div>/);
    for (const section of [summary, notes]) {
      expect(section).toContain(`<p class="acs-body-s acs-muted">${COPY["cart.shipping_later"][language]}</p>`);
      expect(section).toContain(`<p class="acs-body-s acs-muted">${COPY["cart.price_recheck"][language]}</p>`);
    }
  });

  // UX P04 桌面线框的顺序：[cart.title] ★ [cart.demo_hint]，各行，[cart.item_changed]，再是小计栏（[cart.subtotal]、[cart.shipping_later]、[cart.price_recheck]、[cart.continue]）。
  it("follows the order of the wireframe", () => {
    const html = renderRows([priced("tee-black-m", { status: "insufficient_stock" })], { subtotal: 3900 });
    const aside = html.indexOf("<aside");
    const positions = [
      html.indexOf(COPY["cart.title"].en),
      html.indexOf(COPY["cart.demo_hint"].en),
      html.indexOf("Crew Neck Tee"),
      html.indexOf(COPY["cart.item_changed"].en),
      html.indexOf(COPY["cart.subtotal"].en, aside),
      html.indexOf(COPY["cart.shipping_later"].en, aside),
      html.indexOf(COPY["cart.price_recheck"].en, aside),
      html.indexOf(COPY["cart.continue"].en, aside),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // UX P04「去向：P05；P02（继续购物）」与 SHOP-TASK-025 验收第 2 条「本任务起 P04 的 cart.checkout 按路由规则渲染并链到 /checkout」：
  // 小计栏（桌面）与底部固定栏（手机）各有链到 /checkout 的 cart.checkout，另有链到商品列表的 cart.continue。
  it.each(LANGUAGES)("links cart.continue to the product list and cart.checkout to the checkout in %s", (language) => {
    const html = renderRows([priced("tee-black-m")], { language });
    expect(html).toContain(`<a class="acs-btn acs-btn--quiet site-cart__continue" href="/products">${COPY["cart.continue"][language]}</a>`);
    expect(html).toContain(`<a class="acs-btn acs-btn--primary acs-btn--block site-cart__checkout" href="/checkout">${COPY["cart.checkout"][language]}</a>`);
    expect(html).toContain(`<a class="acs-btn acs-btn--primary acs-btn--block" href="/checkout">${COPY["cart.checkout"][language]}</a>`);
  });

  // UX P04 金额元素 M1「不信任浏览器保存的价格」与 SHOP-TASK-018 验收第 3 条：从购物车移除的行在新结果返回前就不再显示（用上一次的结果时只显示仍在购物车里的行）。
  it("hides a removed line before the new quote arrives", () => {
    const rows = [priced("tee-black-m"), priced("candle", { product_slug: "candle", name: text("Soy Wax Candle") })];
    const html = render({ lines: cartOf(rows).slice(1), quote: { status: "ready", quote: { lines: rows, subtotal_sen: 0, ...WITHIN_LIMITS }, pending: true } });
    expect(html).not.toContain("Crew Neck Tee");
    expect(html).toContain("Soy Wax Candle");
    expect(html).toContain(`<main class="site-cart" aria-busy="true">`);
  });
});

describe("changed lines and the purchase limit", () => {
  // SHOP-TASK-018 验收第 4 条「任何一行不是正常状态时显示 cart.item_changed」与 UX P04「[cart.item_changed]（服务端校验有变化时）」：不可购买、超出限购、库存不足各自都会显示，全部正常时不显示。
  it.each(LANGUAGES)("shows cart.item_changed only when a line is not ok in %s", (language) => {
    const changed = [unavailable("gone-sku"), priced("tee-black-m", { status: "over_limit", quantity: 11 }), priced("tee-black-m", { status: "insufficient_stock", available_stock: 0 })];
    const alert = `<div class="acs-alert acs-alert--danger" role="alert"><svg`;
    for (const row of changed) {
      const html = renderRows([priced("candle", { product_slug: "candle" }), row], { language });
      expect(html).toContain(alert);
      expect(html).toContain(`<span>${COPY["cart.item_changed"][language]}</span></div>`);
      expect(hasChangedLine([row])).toBe(true);
    }
    const fine = renderRows([priced("tee-black-m"), priced("candle", { product_slug: "candle" })], { language });
    expect(fine).not.toContain(COPY["cart.item_changed"][language]);
    expect(fine).not.toContain(`role="alert"`);
  });

  // UX P04 说明「计价接口把某商品的各行标为超出限购时，在这些行下显示 [cart.over_limit]（{count} 为接口返回的限购件数）」：只在超出限购的行下，其他行没有。
  it.each(LANGUAGES)("shows cart.over_limit under the lines over the limit only in %s", (language) => {
    const rows = [
      priced("tee-black-m", { status: "over_limit", quantity: 4, max_per_order: 5 }),
      priced("tee-white-s", { status: "over_limit", quantity: 2, max_per_order: 5 }),
      priced("candle", { product_slug: "candle", status: "insufficient_stock", max_per_order: 3 }),
    ];
    const [first = "", second = "", third = ""] = items(renderRows(rows, { language }));
    const note = `<p class="acs-field__error site-cart__note">${COPY["cart.over_limit"][language].replace("{count}", "5")}</p></div>`;
    expect(first.endsWith(note)).toBe(true);
    expect(second.endsWith(note)).toBe(true);
    expect(third).not.toContain("site-cart__note");
  });

  // UX P04 说明「同一商品各行的件数合计达到每单限购时，这些行的数量 (+) 禁用」与 SHOP-TASK-018 验收第 4 条：限购 5，同一商品两行 2 + 3 件，两行 (+) 都禁用，另一件商品不受影响。
  it("disables (+) on every line of a product that reached its limit", () => {
    const rows = [
      priced("tee-black-m", { quantity: 2, max_per_order: 5 }),
      priced("tee-white-s", { quantity: 3, max_per_order: 5 }),
      priced("candle", { product_slug: "candle", quantity: 1, max_per_order: 5 }),
    ];
    const [tee1 = "", tee2 = "", candle = ""] = items(renderRows(rows));
    expect(tee1).toContain(`aria-label="${increase("en")}" disabled=""`);
    expect(tee2).toContain(`aria-label="${increase("en")}" disabled=""`);
    expect(candle).not.toContain(`aria-label="${increase("en")}" disabled=""`);
  });

  // 同一条：合计按本浏览器购物车里的件数（尚未返回新结果时也按最新件数）；低于限购时 (+) 可用，超出限购（over_limit）时也禁用。
  it("counts the cart quantities against max_per_order", () => {
    const black = priced("tee-black-m", { quantity: 2, max_per_order: 5 });
    const white = priced("tee-white-s", { quantity: 2, max_per_order: 5 });
    const rows = [black, white];
    expect(canIncrease(rows, cartOf(rows), black, 2)).toBe(true);
    const moved = [{ sku: "tee-black-m", slug: "crew-neck-tee", quantity: 3 }, { sku: "tee-white-s", slug: "crew-neck-tee", quantity: 2 }];
    expect(canIncrease(rows, moved, black, 3)).toBe(false);
    const over = [{ sku: "tee-black-m", slug: "crew-neck-tee", quantity: 6 }, { sku: "tee-white-s", slug: "crew-neck-tee", quantity: 2 }];
    expect(canIncrease(rows, over, white, 2)).toBe(false);
    expect(canIncrease([priced("big", { max_per_order: 99 })], [{ sku: "big", slug: "crew-neck-tee", quantity: 98 }], priced("big", { max_per_order: 99 }), 98)).toBe(true);
  });

  // UX 0.5「阅读说明」(-)(+) 读屏标签与 SHOP-TASK-018 验收第 4 条「件数不低于 1」：1 件时 (-) 禁用，多于 1 件时可用；按钮是带读屏标签的图形。
  it.each(LANGUAGES)("disables (-) at one item in %s", (language) => {
    const [one = "", two = ""] = items(renderRows([priced("a", { quantity: 1 }), priced("b", { quantity: 2 })], { language }));
    expect(one).toMatch(new RegExp(`<button type="button" aria-label="${decrease(language)}" disabled=""><svg[^>]*aria-hidden="true"`));
    expect(two).toMatch(new RegExp(`<button type="button" aria-label="${decrease(language)}"><svg[^>]*aria-hidden="true"`));
  });

  // SHOP-TASK-018 验收第 4 条「不可购买的行只显示接口返回的内容并可移除」：只有 SKU、件数与 cart.remove，没有名称链接、价格或步进器。
  it.each(LANGUAGES)("shows only the SKU, the quantity and remove for an unavailable line in %s", (language) => {
    const [item = ""] = items(renderRows([unavailable("gone-sku", 3)], { language }));
    expect(visibleTexts(item)).toEqual(["gone-sku", "3", COPY["cart.remove"][language]]);
    expect(item).not.toContain("<a ");
    expect(item).not.toContain("acs-stepper");
    expect(item).not.toContain("RM ");
  });
});

describe("order caps and typed quantities", () => {
  const line = (sku: string, slug: string, quantity: number): CartLine => ({ sku, slug, quantity });

  // UX P04「整单上限」「购物车全部行的件数合计达到 2,000 时，各行的数量 (+) 禁用」：三件商品 1000 + 500 + 500 件（每单限购都是 1000），
  // 后两行没到限购也没到每行 1000 件，(+) 仍禁用；合计 1999 件时可用。
  it("disables (+) on every line when the cart holds 2000 items", () => {
    const tee = priced("tee-black-m", { quantity: 1000, max_per_order: 1000 });
    const candle = priced("candle", { product_slug: "candle", quantity: 500, max_per_order: 1000 });
    const tote = priced("tote", { product_slug: "tote", quantity: 500, max_per_order: 1000 });
    const rows = [tee, candle, tote];
    const full = [line("tee-black-m", "crew-neck-tee", 1000), line("candle", "candle", 500), line("tote", "tote", 500)];
    expect(canIncrease(rows, full, candle, 500)).toBe(false);
    expect(canIncrease(rows, full, tote, 500)).toBe(false);
    const almost = [line("tee-black-m", "crew-neck-tee", 1000), line("candle", "candle", 500), line("tote", "tote", 499)];
    expect(canIncrease(rows, almost, tote, 499)).toBe(true);
    expect(canIncrease(rows, almost, candle, 500)).toBe(true);
    const all = items(render({ lines: full, quote: ready(rows) }));
    expect(all).toHaveLength(3);
    for (const item of all) {
      expect(item).toContain(`aria-label="${increase("en")}" disabled=""`);
    }
  });

  // UX P04「(-) 与 (+) 之间是数字输入框（读屏标签 [detail.quantity]）」与视觉稿 P03-P04-caps 的 acs-input、inputmode numeric：
  // 每个可购买行的 (-) 与 (+) 之间是显示件数的输入框；输入中的文字原样显示；不可购买的行照旧只显示件数（见上一组的最后一条）。
  it.each(LANGUAGES)("puts a numeric input with the quantity label between (-) and (+) in %s", (language) => {
    const rows = [priced("tee-black-m", { quantity: 3 }), priced("candle", { product_slug: "candle", quantity: 1 })];
    const [tee = "", candle = ""] = items(renderRows(rows, { language, draft: { sku: "candle", text: "12" } }));
    const stepper = part(tee, /<div class="acs-stepper site-cart__qty">[\s\S]*?<\/div>/);
    expect(stepper).toMatch(new RegExp(`aria-label="${decrease(language)}"[\\s\\S]*<input class="acs-input site-cart__qty-input"[\\s\\S]*aria-label="${increase(language)}"`));
    const input = quantityInput(tee);
    expect(input).toMatch(/\sinputmode="numeric"/i);
    expect(input).toContain(`aria-label="${escapeHtml(COPY["detail.quantity"][language])}"`);
    expect(input).toContain(`value="3"`);
    expect(quantityInput(candle)).toContain(`value="12"`);
  });

  // UX P04「为空、小于 1 或不是整数时恢复为原来的件数」（经 parseQuantityInput，与 P03 同一规则）：件数仍为原来的 3，不显示 common.qty_capped。
  it("restores the quantity when the input is empty, below 1 or not a whole number", () => {
    for (const text of ["", "  ", "0", "-2", "1.5", "2e1", "abc"]) {
      expect(lineQuantityInput(text, 3, 10), text).toEqual({ quantity: 3, capped: false });
    }
  });

  // UX P04「输入值不大于该行现有件数时照收（减少件数不受上限约束）」：后台调低限购到 5、该行已有 8 件（上限即 8），输入 6 照收；
  // 不超过上限的增加也照收。
  it("accepts a lower quantity even above the purchase limit", () => {
    const row = priced("tee-black-m", { quantity: 8, max_per_order: 5, status: "over_limit" });
    const limit = quantityLimit([row], [line("tee-black-m", "crew-neck-tee", 8)], row, 8);
    expect(limit).toBe(8);
    expect(lineQuantityInput("6", 8, limit)).toEqual({ quantity: 6, capped: false });
    expect(lineQuantityInput("8", 8, limit)).toEqual({ quantity: 8, capped: false });
    expect(lineQuantityInput("7", 2, 10)).toEqual({ quantity: 7, capped: false });
    expect(lineQuantityInput("10", 2, 10)).toEqual({ quantity: 10, capped: false });
  });

  // UX P04 上限「该行现有件数 + max(0, min(限购件数减去该商品各行合计, 2,000 减去全部行合计))」：
  // 限购 10、同一商品两行 2 + 3 件，该行上限 2 + 5 = 7；全部行合计 1995 件时整单剩 5 件，限购 1000 的那行上限为现有件数 + 5。
  it("computes the limit from the purchase limit and the 2000-item cap", () => {
    const black = priced("tee-black-m", { quantity: 2, max_per_order: 10 });
    const white = priced("tee-white-s", { quantity: 3, max_per_order: 10 });
    expect(quantityLimit([black, white], [line("tee-black-m", "crew-neck-tee", 2), line("tee-white-s", "crew-neck-tee", 3)], black, 2)).toBe(7);
    const big = priced("big", { product_slug: "big", quantity: 995, max_per_order: 1000 });
    const other = priced("candle", { product_slug: "candle", quantity: 1000, max_per_order: 1000 });
    expect(quantityLimit([big, other], [line("big", "big", 995), line("candle", "candle", 1000)], big, 995)).toBe(1000);
    const many = priced("many", { product_slug: "many", quantity: 10, max_per_order: 1000 });
    const rest = [priced("x", { product_slug: "x", quantity: 1000 }), priced("y", { product_slug: "y", quantity: 985 })];
    expect(quantityLimit([many, ...rest], [line("many", "many", 10), line("x", "x", 1000), line("y", "y", 985)], many, 10)).toBe(15);
  });

  // UX P04「超过上限时改为上限，并在数量下方显示 [common.qty_capped]（{count} 为上限）」与 Kelvin 2026-10-30 决定（HANDOFF 0.80 第 2 条）：
  // 上限 10、输入 25 时改为 10 并提示；提示以 acs-field__hint 与 role status 显示在该行下（视觉稿 P03-P04-caps），只在该行。
  it.each(LANGUAGES)("caps the quantity and shows common.qty_capped under that line in %s", (language) => {
    expect(lineQuantityInput("25", 2, 10)).toEqual({ quantity: 10, capped: true });
    const rows = [priced("tee-black-m", { quantity: 10, max_per_order: 10 }), priced("candle", { product_slug: "candle" })];
    const [tee = "", candle = ""] = items(renderRows(rows, { language, capped: new Map([["tee-black-m", 10]]) }));
    const hint = `<p class="acs-field__hint site-cart__note" role="status">${escapeHtml(COPY["common.qty_capped"][language].replace("{count}", "10"))}</p></div>`;
    expect(tee.endsWith(hint)).toBe(true);
    expect(candle).not.toContain("acs-field__hint");
    expect(renderRows(rows, { language })).not.toContain("acs-field__hint");
  });

  // UX P04「后台调低限购后购物车可能已超出，这时上限就是现有件数：输入更大的数只恢复为原件数，不显示 [common.qty_capped]」：
  // 限购调到 5、该行 7 件，输入 9 后仍为 7、不提示。
  it("only restores the quantity when the limit equals the current quantity", () => {
    const row = priced("tee-black-m", { quantity: 7, max_per_order: 5, status: "over_limit" });
    const limit = quantityLimit([row], [line("tee-black-m", "crew-neck-tee", 7)], row, 7);
    expect(limit).toBe(7);
    expect(lineQuantityInput("9", 7, limit)).toEqual({ quantity: 7, capped: false });
  });

  // UX P04「计价接口返回超出整单件数上限的标记时显示 [cart.units_over_limit]（{count} 为接口返回的上限件数），返回超出整单金额上限的标记时显示
  // [cart.amount_over_limit]（{amount} 为接口返回的上限金额，按整数仙格式化）；…同时存在时都显示，样式与 [cart.item_changed] 相同，位置在它之下」，
  // HANDOFF 0.80 第 3 条「{amount} 用 formatSen（不分千位）」：依次为 item_changed、件数、金额，同一提示条写法；cart.checkout 照旧显示。
  it.each(LANGUAGES)("shows both cap alerts below cart.item_changed in %s", (language) => {
    const rows = [priced("tee-black-m", { status: "insufficient_stock" })];
    const html = renderRows(rows, { language, limits: { unit_limit: 2000, units_over_limit: true, amount_limit_sen: 99999999, amount_over_limit: true } });
    const alert = (copy: string) => `<div class="acs-alert acs-alert--danger" role="alert"><svg[^>]*aria-hidden="true">(?:(?!</svg>)[\\s\\S])*</svg><span>${regexEscape(escapeHtml(copy))}</span></div>`;
    const changed = alert(COPY["cart.item_changed"][language]);
    const units = alert(COPY["cart.units_over_limit"][language].replace("{count}", "2000"));
    const amount = alert(COPY["cart.amount_over_limit"][language].replace("{amount}", "999999.99"));
    expect(html).toMatch(new RegExp(changed + units + amount));
    // 三条用同一图形（cart.item_changed 的图形，视觉稿 P03-P04-caps）。
    const icons = [...html.matchAll(/role="alert">(<svg[\s\S]*?<\/svg>)/g)].map((m) => m[1]);
    expect(icons).toHaveLength(3);
    expect(new Set(icons).size).toBe(1);
    expect(html).toContain(`href="/checkout">${COPY["cart.checkout"][language]}</a>`);
  });

  // 同一条「两者与各行的标注互不影响」与线框「[cart.item_changed] 的位置下另显示」：全部行正常时提示条在 cart.item_changed 的位置，
  // 只超出金额时只有金额一条；数字取接口返回的值。
  it("shows a cap alert on its own when every line is fine", () => {
    const rows = [priced("tee-black-m")];
    const amountOnly = renderRows(rows, { limits: { amount_over_limit: true, amount_limit_sen: 12345 } });
    expect(amountOnly).not.toContain(COPY["cart.item_changed"].en);
    expect(amountOnly).not.toContain(COPY["cart.units_over_limit"].en.replace("{count}", "2000"));
    expect(amountOnly).toContain(`<span>${escapeHtml(COPY["cart.amount_over_limit"].en.replace("{amount}", "123.45"))}</span>`);
    const unitsOnly = renderRows(rows, { limits: { units_over_limit: true, unit_limit: 1800 } });
    expect(unitsOnly).toContain(`<span>${COPY["cart.units_over_limit"].en.replace("{count}", "1800")}</span>`);
    expect(unitsOnly).not.toContain("RM 999999.99");
    const lines = part(unitsOnly, /<div class="site-cart__lines">[\s\S]*?<div class="acs-alert/);
    expect(lines).toContain("Crew Neck Tee");
  });

  // UX P04「计价接口返回超出…的标记时」才显示：未超出时两条都不显示（夹具为 2000、false、99999999、false）。
  it.each(LANGUAGES)("shows no cap alert within the limits in %s", (language) => {
    const html = renderRows([priced("tee-black-m")], { language });
    expect(html).not.toContain(COPY["cart.units_over_limit"][language].split("{count}")[0] ?? "");
    expect(html).not.toContain(escapeHtml(COPY["cart.amount_over_limit"][language].split("{amount}")[0] ?? ""));
    expect(html).not.toContain(`role="alert"`);
  });
});

describe("empty cart, loading and errors", () => {
  // UX P04「空：[cart.empty] ([cart.continue])」与 SHOP-TASK-018 验收第 5 条：空购物车显示 cart.empty 与链到商品列表的 cart.continue，仍有 ★ cart.demo_hint，不显示小计。
  it.each(LANGUAGES)("shows the empty cart in %s", (language) => {
    const html = render({ language });
    expect(html).toContain(`<p class="acs-display-s">${COPY["cart.empty"][language]}</p>`);
    expect(html).toContain(`<a class="acs-btn acs-btn--secondary" href="/products">${COPY["cart.continue"][language]}</a>`);
    expect(html).toContain(escapeHtml(COPY["cart.demo_hint"][language]));
    expect(html).not.toContain(COPY["cart.subtotal"][language]);
    expect(html).not.toContain("aria-busy");
  });

  // UX P04「演示提示：★ [cart.demo_hint]；[cart.price_recheck]」与 SHOP-TASK-018 验收第 5 条「显示 ★ cart.demo_hint、cart.shipping_later 与 cart.price_recheck」：空购物车、计价返回之前与失败时也显示这两条说明。
  it.each(LANGUAGES)("shows the shipping note and the price recheck note without a quote in %s", (language) => {
    const states: RenderOptions[] = [
      { language },
      { lines: [{ sku: "a", slug: "tee", quantity: 1 }], language },
      { lines: [{ sku: "a", slug: "tee", quantity: 1 }], quote: { status: "error" }, language },
    ];
    for (const state of states) {
      const html = render(state);
      expect(html).toContain(`<p class="acs-body-s acs-muted">${COPY["cart.shipping_later"][language]}</p>`);
      expect(html).toContain(`<p class="acs-body-s acs-muted">${COPY["cart.price_recheck"][language]}</p>`);
    }
  });

  // 同一条，经全站框架与路由：服务端渲染 /cart 时没有本浏览器购物车，是空购物车。
  it("renders the empty cart through the app", () => {
    const html = renderToStaticMarkup(<App initialPath="/cart" storage={languageStorage("en")} />);
    expect(html).toContain(COPY["cart.empty"].en);
    expect(html).not.toContain("style=");
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-018 验收第 3 条「显示返回的…」——计价返回之前不显示任何行或金额，主体标 aria-busy。
  it("shows no lines or amounts while the first quote is loading", () => {
    const html = render({ lines: [{ sku: "a", slug: "tee", quantity: 1 }] });
    expect(html).toContain(`<main class="site-cart" aria-busy="true">`);
    expect(html).not.toContain("RM ");
    expect(html).not.toContain("site-cart__item");
  });

  // SHOP-TASK-018 验收第 5 条「计价请求失败时显示 common.error_retry」。
  it.each(LANGUAGES)("shows common.error_retry when the quote fails in %s", (language) => {
    const html = render({ lines: [{ sku: "a", slug: "tee", quantity: 1 }], quote: { status: "error" }, language });
    expect(html).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${COPY["common.error_retry"][language]}</p>`);
    expect(html).not.toContain(COPY["cart.empty"][language]);
  });

  // SHOP-TASK-018 验收第 5 条「计价请求失败时…不清空购物车」：请求失败（网络错误或非 2xx）后本浏览器购物车原样保留。
  it("keeps the cart when the quote request fails", async () => {
    const lines = [{ sku: "tee-black-m", slug: "crew-neck-tee", quantity: 2 }];
    const storage = memoryStorage(lines);
    for (const failure of [() => Promise.reject(new TypeError("network")), () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })]) {
      vi.stubGlobal("fetch", vi.fn(failure));
      await expect(latestQuoter(fetchQuote).run("en", readCart(storage))).resolves.toEqual({ status: "error" });
      expect(readCart(storage)).toEqual(lines);
    }
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-017 验收「读写失败时页面照常可用」在购物车页的延续——写回本浏览器失败时显示 common.error_retry，行照常显示。
  it("reports a failed write next to the lines", () => {
    const html = render({ lines: cartOf([priced("a")]), quote: ready([priced("a")]), writeFailed: true });
    expect(html).toContain(COPY["common.error_retry"].en);
    expect(html).toContain("site-cart__item");
  });
});

describe("which quote is shown", () => {
  const first: CheckoutQuote = { lines: [priced("a")], subtotal_sen: 3900, ...WITHIN_LIMITS };

  // SHOP-TASK-018 验收第 3 条「用购物车各行的 SKU 与件数…调用计价接口」：请求标识只由语言与 SKU、件数决定（slug 变化不重新计价），空购物车不请求。
  it("keys the request by language, SKU and quantity only", () => {
    expect(quoteKey("en", [])).toBeNull();
    const key = quoteKey("en", [{ sku: "a", slug: "tee", quantity: 1 }]);
    expect(quoteKey("en", [{ sku: "a", slug: "other", quantity: 1 }])).toBe(key);
    expect(quoteKey("en", [{ sku: "a", slug: "tee", quantity: 2 }])).not.toBe(key);
    expect(quoteKey("zh", [{ sku: "a", slug: "tee", quantity: 1 }])).not.toBe(key);
  });

  // SHOP-TASK-018 验收第 3 条「页面打开、修改件数或移除后…调用计价接口并显示返回的…；连续修改时只采用最后一次请求的结果」：
  // 结果属于当前请求时直接显示；改了件数、新结果返回前继续显示上一次的结果并标为等待中；首次请求前为加载中；当前请求失败为 error。
  it("shows the result that belongs to the current request", () => {
    expect(quoteState("k1", null)).toEqual({ status: "loading" });
    expect(quoteState("k1", { key: "k1", outcome: { status: "ready", quote: first }, last: first })).toEqual({ status: "ready", quote: first, pending: false });
    expect(quoteState("k2", { key: "k1", outcome: { status: "ready", quote: first }, last: first })).toEqual({ status: "ready", quote: first, pending: true });
    expect(quoteState("k2", { key: "k2", outcome: { status: "error" }, last: first })).toEqual({ status: "error" });
    expect(quoteState("k3", { key: "k2", outcome: { status: "error" }, last: null })).toEqual({ status: "loading" });
  });
});

function memoryStorage(initial: readonly CartLine[] = []): CartStorage {
  const data: Record<string, string> = { [CART_STORAGE_KEY]: JSON.stringify(initial) };
  return {
    getItem: (key) => data[key] ?? null,
    setItem: (key, value) => {
      data[key] = value;
    },
  };
}

describe("writing back to the browser cart", () => {
  const lines = [
    { sku: "tee-black-m", slug: "crew-neck-tee", quantity: 2 },
    { sku: "candle", slug: "candle", quantity: 1 },
  ];

  // UX P04「修改已选商品…数量」与 SHOP-TASK-018 验收第 6 条「改件数写回购物车」：只改该行件数，件数低于 1 时不改。
  it("writes a changed quantity back", () => {
    const storage = memoryStorage(lines);
    expect(changeQuantity(storage, readCart(storage), "candle", 3)).toBe(true);
    expect(readCart(storage)).toEqual([lines[0], { sku: "candle", slug: "candle", quantity: 3 }]);
    expect(changeQuantity(storage, readCart(storage), "candle", 0)).toBe(false);
    expect(readCart(storage)[1]?.quantity).toBe(3);
  });

  // UX P04 线框 ([cart.remove]) 与 SHOP-TASK-018 验收第 6 条「移除…写回购物车」。
  it("writes a removal back", () => {
    const storage = memoryStorage(lines);
    expect(removeFromCart(storage, readCart(storage), "tee-black-m")).toBe(true);
    expect(readCart(storage)).toEqual([lines[1]]);
  });

  // 派生实现约束（实现选择）：写入失败时返回 false，不抛错。
  it("reports a failed write", () => {
    const failing: CartStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(changeQuantity(failing, lines, "candle", 2)).toBe(false);
    expect(removeFromCart(null, lines, "candle")).toBe(false);
  });
});

// 节日促销活动（SHOP-TASK-258）的夹具：数值取自视觉稿 P04-promo-desktop——两件打 8 折的行（78.00 → 62.40、89.00 → 71.20）、
// 一件买 2 送 1 的蜡烛，商品小计 199.00，商品级优惠 33.40、满 150 减 15 的整单级优惠 15.00，下一档满 300 减 40 还差 134.40，免运费（满 120）已达。
function ruleDisplay(rule_id: number, position: number, overrides: Partial<RuleDisplay> = {}): RuleDisplay {
  return {
    rule_id,
    position,
    kind: "item_discount",
    discount_mode: null,
    percent: null,
    amount_sen: null,
    buy_quantity: null,
    get_quantity: null,
    get_percent: null,
    threshold_sen: null,
    tiers: [],
    categories: [],
    has_products: false,
    ...overrides,
  };
}

const ITEM_RULE = ruleDisplay(11, 1, { discount_mode: "percent", percent: 20 });
const BXGY_RULE = ruleDisplay(14, 2, { kind: "buy_x_get_y", buy_quantity: 2, get_quantity: 1, get_percent: 100 });
const SPEND_RULE = ruleDisplay(12, 3, {
  kind: "spend_discount",
  tiers: [
    { threshold_sen: 15000, amount_sen: 1500, percent: null },
    { threshold_sen: 30000, amount_sen: 4000, percent: null },
  ],
});
const SHIPPING_RULE = ruleDisplay(13, 4, { kind: "free_shipping", threshold_sen: 12000 });

const SERVER_TIME = "2026-02-10T00:00:00Z";

function sale(overrides: Partial<PromotionDisplay> = {}): PromotionResponse {
  return {
    promotion: {
      promotion_id: 7,
      title: "Lunar New Year Sale",
      subtitle: null,
      badge: "Lunar deal",
      strip: null,
      pack: "cny",
      palette: "cny",
      ambient: "none",
      home_poster: "split",
      list_strip: "band",
      card_badge: "tag",
      detail_ribbon: "ribbon",
      cart_progress: "bar",
      poster_mode: "template",
      poster_desktop: null,
      poster_phone: null,
      poster_alt: null,
      ends_at: null,
      coupon_combinable: true,
      points_combinable: true,
      rules: [ITEM_RULE, BXGY_RULE, SPEND_RULE, SHIPPING_RULE],
      ...overrides,
    },
    server_time: SERVER_TIME,
  };
}

const NO_SALE: PromotionResponse = { promotion: null, server_time: SERVER_TIME };

function promotionQuote(overrides: Partial<PromotionQuote> = {}): PromotionQuote {
  return {
    promotion_id: 7,
    item_discount_sen: 3340,
    order_discount_sen: 1500,
    after_item_subtotal_sen: 16560,
    spend_mode: "amount",
    spend_tier_reached: 1,
    spend_next_threshold_sen: 30000,
    spend_next_value: 4000,
    spend_next_gap_sen: 13440,
    free_shipping_threshold_sen: 12000,
    free_shipping_reached: true,
    free_shipping_gap_sen: 0,
    shipping_waived_sen: null,
    participates: null,
    buy_x_get_y: [],
    ...overrides,
  };
}

// 计价结果里没有任何活动规则（金额为 0、没有门槛）。
const BARE: Partial<PromotionQuote> = {
  item_discount_sen: 0,
  order_discount_sen: 0,
  after_item_subtotal_sen: 19900,
  spend_mode: null,
  spend_tier_reached: null,
  spend_next_threshold_sen: null,
  spend_next_value: null,
  spend_next_gap_sen: null,
  free_shipping_threshold_sen: null,
  free_shipping_reached: false,
  free_shipping_gap_sen: null,
};

const TEE = priced("tee-black-m", { quantity: 2, line_subtotal_sen: 7800, promotion_rule_id: 11, promotion_item_discount_sen: 1560 });
const HOODIE = priced("hoodie-grey-l", {
  product_slug: "pullover-hoodie",
  name: text("Pullover Hoodie"),
  unit_price_sen: 8900,
  line_subtotal_sen: 8900,
  promotion_rule_id: 11,
  promotion_item_discount_sen: 1780,
});
const CANDLE = priced("candle", { product_slug: "candle", name: text("Soy Wax Candle"), options: [], unit_price_sen: 3200, line_subtotal_sen: 3200, promotion_rule_id: 14 });
const SALE_ROWS: QuoteLine[] = [TEE, HOODIE, CANDLE];
// 同一批商品在没有活动时的计价行。
const PLAIN_ROWS: QuoteLine[] = SALE_ROWS.map((row) => (row.status === "unavailable" ? row : { ...row, promotion_rule_id: null, promotion_item_discount_sen: 0 }));

interface SaleOptions {
  // 计价结果的 promotion；不给时为 promotionQuote()。
  quote?: PromotionQuote | null;
  rows?: QuoteLine[];
  // 共享读取；不给时为 sale()，null 时不包 PromotionProvider。
  shared?: PromotionResponse | "loading" | null;
  session?: MemberSessionState;
  language?: Language;
}

function renderSale({ quote = promotionQuote(), rows = SALE_ROWS, shared = sale(), session, language = "en" }: SaleOptions = {}): string {
  return render({
    lines: cartOf(rows),
    quote: ready(rows, 19900, { promotion: quote }),
    language,
    ...(shared === null ? {} : { promotion: shared }),
    ...(session === undefined ? {} : { session }),
  });
}

const MEMBER: MemberSessionState = { kind: "member", phoneMasked: "+60 12-*** 5678", hasPassword: false, csrfToken: "token" };

const PHONE_BLOCK_START = `<div class="acs-summary site-phone-only site-cart__promo-phone">`;

const aside = (html: string) => part(html, /<aside[\s\S]*?<\/aside>/);
const phoneBlock = (html: string) => part(html, /<div class="acs-summary site-phone-only site-cart__promo-phone">[\s\S]*?(?=<div class="site-phone-only site-cart__notes">)/);
const bottomBar = (html: string) => part(html, /<div class="acs site-phone-only site-cart__bar">[\s\S]*$/);

// 金额行：标签与金额。
function amountRows(html: string): [string, string][] {
  return [...html.matchAll(/<div class="acs-row"><span>([^<]*)<\/span><span[^>]*>([^<]*)<\/span><\/div>/g)].map((m) => [unescapeHtml(m[1] ?? ""), m[2] ?? ""]);
}

// 凑单提示的文字，依次排列；每条前是读屏忽略的活动图案。
function hintTexts(html: string): string[] {
  return [...html.matchAll(/<p class="acs-promo-progress__hint"><span class="acs-promo-motif" aria-hidden="true"><\/span><span>([^<]*)<\/span><\/p>/g)].map((m) =>
    unescapeHtml(m[1] ?? ""),
  );
}

const PROGRESS = `<div class="acs-promo-progress">`;
const copy = (key: CopyKey, language: Language, vars?: CopyVars) => translate(language, key, vars);
const tag = (value: string) => `<span class="acs-promo-badge acs-promo-badge--tag"><span>${escapeHtml(value)}</span></span>`;
const badges = (rule: string) => `<span class="acs-promo-badges">${tag("Lunar deal")}${tag(rule)}</span>`;
const percentBadge = (language: Language) => copy("promo.badge_percent", language, { percent: 20 });
const bxgyBadge = (language: Language) => copy("promo.badge_bxgy_free", language, { x: 2, y: 1 });

describe("sale amounts", () => {
  // UX 0.20 P04 线框「右栏依次为：[cart.subtotal] [M2] / [cart.promo_discount] [M8]（有活动优惠时）/ [cart.after_promo] [M9]（有活动优惠时）」与说明
  // 「[cart.promo_discount] 为商品级与整单级活动优惠之和，[cart.after_promo] 为商品小计减去它」：33.40 + 15.00 = 48.40，199.00 − 48.40 = 150.60；
  // 手机线框「底部固定栏改为显示 [cart.after_promo] [M9]」，两行在手机放在 [cart.shipping_later] 之前的一块里。
  it.each(LANGUAGES)("shows cart.promo_discount and cart.after_promo after the subtotal in %s", (language) => {
    const html = renderSale({ language });
    expect(amountRows(aside(html))).toEqual([
      [copy("cart.subtotal", language), "RM 199.00"],
      [copy("cart.promo_discount", language), "RM 48.40"],
      [copy("cart.after_promo", language), "RM 150.60"],
    ]);
    expect(amountRows(phoneBlock(html))).toEqual([
      [copy("cart.promo_discount", language), "RM 48.40"],
      [copy("cart.after_promo", language), "RM 150.60"],
    ]);
    expect(amountRows(bottomBar(html))).toEqual([[copy("cart.after_promo", language), "RM 150.60"]]);
    expect(html.indexOf(PHONE_BLOCK_START)).toBeLessThan(html.indexOf(`<div class="site-phone-only site-cart__notes">`));
  });

  // UX 0.20 P04「为 0 时这两行都不显示」与手机线框「没有活动优惠时仍为 [cart.subtotal] [M2]」：
  // 优惠为 0 时两行都不出现，底部固定栏照旧是商品小计（还有满额规则时提示照样显示）。
  it.each(LANGUAGES)("hides both rows when the sale discount is 0 in %s", (language) => {
    const html = renderSale({ language, rows: PLAIN_ROWS, quote: promotionQuote({ item_discount_sen: 0, order_discount_sen: 0 }) });
    expect(html).not.toContain(escapeHtml(copy("cart.promo_discount", language)));
    expect(html).not.toContain(escapeHtml(copy("cart.after_promo", language)));
    expect(amountRows(aside(html))).toEqual([[copy("cart.subtotal", language), "RM 199.00"]]);
    expect(amountRows(bottomBar(html))).toEqual([[copy("cart.subtotal", language), "RM 199.00"]]);
  });

  // 验收「为 0、promotion 为 null 或没有活动时与现在完全相同」（UX 0.20 P04「有活动生效时」才有这些内容）：
  // 计价结果没有活动、或有活动而金额为 0 且没有任何门槛时，页面与不包共享读取、没有活动的渲染逐字相同。
  it.each(LANGUAGES)("renders exactly as before without a sale in %s", (language) => {
    const before = renderSale({ language, rows: PLAIN_ROWS, quote: null, shared: null });
    expect(before).not.toContain("acs-promo");
    expect(renderSale({ language, rows: PLAIN_ROWS, quote: null })).toBe(before);
    expect(renderSale({ language, rows: PLAIN_ROWS, quote: null, shared: NO_SALE })).toBe(before);
    expect(renderSale({ language, rows: PLAIN_ROWS, quote: promotionQuote(BARE) })).toBe(before);
  });
});

describe("sale lines", () => {
  // UX 0.20 P04 线框「打折的行：[M1] 处先显示划线的原价行小计，再显示活动价行小计；行下显示 <规则角标>」与 M1「打折的行另显示活动价行小计」：
  // 行小计 78.00 减该行优惠 15.60 为 62.40，各以读屏标签开头；角标为活动的角标短语与该规则的角标（视觉稿 P04-promo-desktop 放在名称与规格之下）。
  it.each(LANGUAGES)("shows the struck line subtotal, the sale line subtotal and the badges on a discounted line in %s", (language) => {
    const [tee = "", hoodie = ""] = items(renderSale({ language }));
    const prices = (was: string, now: string) =>
      `<span class="site-cart__price"><span class="acs-prices">` +
      `<span class="acs-price-was"><span class="acs-vh">${escapeHtml(copy("promo.a11y_original", language))}</span>${was}</span>` +
      `<span class="acs-price-sale"><span class="acs-vh">${escapeHtml(copy("promo.a11y_sale", language))}</span>${now}</span></span></span>`;
    expect(tee).toContain(prices("RM 78.00", "RM 62.40"));
    expect(hoodie).toContain(prices("RM 89.00", "RM 71.20"));
    expect(tee).toContain(`${badges(percentBadge(language))}</span><div class="acs-stepper site-cart__qty">`);
    expect(tee).not.toContain(`<span class="acs-price site-cart__price">`);
  });

  // 同一条「打折的行」：买 X 享 Y 的行该行优惠为 0，行小计照旧不划线，名称之下仍显示活动角标与买 X 享 Y 的角标。
  it.each(LANGUAGES)("shows the badges but no struck price on a buy-x-get-y line in %s", (language) => {
    const [, , candle = ""] = items(renderSale({ language }));
    expect(candle).toContain(`<span class="acs-price site-cart__price">RM 32.00</span>`);
    expect(candle).not.toContain("acs-price-was");
    expect(candle).toContain(badges(bxgyBadge(language)));
  });

  // UX 0.20 P04「行下显示 <规则角标>」只对共享读取里有的规则：行命中的规则不在共享读取的活动里时不显示角标，金额仍照计价结果划线。
  it("shows no badges when the line's rule is not in the shared sale", () => {
    const [tee = ""] = items(renderSale({ rows: [{ ...TEE, promotion_rule_id: 99 }] }));
    expect(tee).not.toContain("acs-promo-badges");
    expect(tee).toContain("RM 62.40");
  });
});

describe("sale hints", () => {
  // UX 0.20 P04「满额减或满额折还有下一档时 [cart.hint_tier_amount] 或 [cart.hint_tier_percent]（{amount} 为还差的金额，{save}、{percent} 为下一档的优惠）」
  // 与「有免运费规则时…已达显示 [cart.free_shipping_reached]」：满额减还差 134.40 可减 40.00，免运费已达；桌面与手机各一份，每条一行。
  it.each(LANGUAGES)("shows the next fixed-amount tier and the reached free shipping in %s", (language) => {
    const html = renderSale({ language });
    const expected = [copy("cart.hint_tier_amount", language, { amount: "134.40", save: "40.00" }), copy("cart.free_shipping_reached", language)];
    expect(hintTexts(aside(html))).toEqual(expected);
    expect(hintTexts(phoneBlock(html))).toEqual(expected);
  });

  // 同一条的满额折：spend_mode 为 percent 时用 [cart.hint_tier_percent]，{percent} 为下一档的百分比；已到最高档（没有下一档）时不显示这一条。
  it.each(LANGUAGES)("shows the next percentage tier in %s", (language) => {
    const percent = renderSale({ language, quote: promotionQuote({ spend_mode: "percent", spend_next_value: 10 }) });
    expect(hintTexts(aside(percent))[0]).toBe(copy("cart.hint_tier_percent", language, { amount: "134.40", percent: 10 }));
    const top = renderSale({ language, quote: promotionQuote({ spend_next_threshold_sen: null, spend_next_value: null, spend_next_gap_sen: null }) });
    expect(hintTexts(aside(top))).toEqual([copy("cart.free_shipping_reached", language)]);
  });

  // UX 0.20 P04「有免运费规则时，未达门槛显示 [cart.hint_free_shipping]，已达显示 [cart.free_shipping_reached]（门槛为 0 时不显示提示）」：
  // 未达时 {amount} 为接口返回的差额 25.00；门槛为 0 时两种都不显示。
  it.each(LANGUAGES)("shows the free shipping hint only for a threshold above 0 in %s", (language) => {
    const short = renderSale({ language, quote: promotionQuote({ spend_next_gap_sen: null, spend_next_threshold_sen: null, free_shipping_reached: false, free_shipping_gap_sen: 2500 }) });
    expect(hintTexts(aside(short))).toEqual([copy("cart.hint_free_shipping", language, { amount: "25.00" })]);
    const zero = renderSale({ language, quote: promotionQuote({ spend_next_gap_sen: null, spend_next_threshold_sen: null, free_shipping_threshold_sen: 0, free_shipping_reached: true }) });
    expect(hintTexts(zero)).toEqual([]);
  });

  // UX 0.20 P04「每条买 X 享 Y 规则的范围内已有商品时显示 [cart.hint_bxgy]（{rule} 为该规则的角标文字，{n} 为接口返回的再加件数，{y} 为 Y）」：
  // 范围内没有商品（units_in_scope 为 0）的规则与共享读取里找不到的规则都不显示；不包共享读取时也不显示（其余提示照计价结果显示）。
  it.each(LANGUAGES)("shows cart.hint_bxgy only for rules with items in scope that the shared sale has in %s", (language) => {
    const quote = promotionQuote({
      buy_x_get_y: [
        { rule_id: 14, buy_quantity: 2, get_quantity: 1, get_percent: 100, units_in_scope: 1, units_to_next: 2 },
        { rule_id: 15, buy_quantity: 3, get_quantity: 1, get_percent: 50, units_in_scope: 0, units_to_next: 3 },
        { rule_id: 16, buy_quantity: 1, get_quantity: 1, get_percent: 50, units_in_scope: 4, units_to_next: 1 },
      ],
    });
    const html = renderSale({ language, quote, shared: sale({ rules: [ITEM_RULE, BXGY_RULE, ruleDisplay(15, 5, { kind: "buy_x_get_y", buy_quantity: 3, get_quantity: 1, get_percent: 50 })] }) });
    const bxgy = copy("cart.hint_bxgy", language, { rule: bxgyBadge(language), n: 2, y: 1 });
    expect(hintTexts(aside(html))).toEqual([copy("cart.hint_tier_amount", language, { amount: "134.40", save: "40.00" }), copy("cart.free_shipping_reached", language), bxgy]);
    const unshared = renderSale({ language, quote, shared: null });
    expect(hintTexts(aside(unshared))).toEqual([copy("cart.hint_tier_amount", language, { amount: "134.40", save: "40.00" }), copy("cart.free_shipping_reached", language)]);
  });
});

describe("sale progress", () => {
  // UX 0.20 P04「进度条按 cart_progress 的变体只画离得最近的一个门槛」与 COMPONENTS「Cart progress」的 bar 变体（__track + __fill，--progress）：
  // 满额下一档 300.00 还差 134.40，进度 floor(16560 × 100 ÷ 30000) = 55；__labels（读屏忽略）为已达 165.60 与门槛 300.00。
  it("draws the bar to the next tier with its labels", () => {
    const html = aside(renderSale());
    expect(html).toContain(
      `${PROGRESS}<div class="acs-promo-progress__track" aria-hidden="true"><span class="acs-promo-progress__fill" style="--progress:55%"></span></div>` +
        `<div class="acs-promo-progress__labels" aria-hidden="true"><span>RM 165.60</span><span>RM 300.00</span></div></div>`,
    );
    expect(phoneBlock(renderSale())).toContain(`style="--progress:55%"`);
  });

  // 同一条的 steps 变体（__steps 的十格，点亮的格带 data-on）：百分比与格数都向下取整——差 100 时 floor(29900 × 100 ÷ 30000) = 99，点亮 9 格。
  it("lights whole steps rounded down", () => {
    const quote = promotionQuote({ spend_next_gap_sen: 100 });
    expect(cartProgress(quote)).toEqual({ percent: 99, labels: { reached: 29900, threshold: 30000 } });
    const html = aside(renderSale({ quote, shared: sale({ cart_progress: "steps" }) }));
    const steps = part(html, /<div class="acs-promo-progress__steps" aria-hidden="true">[\s\S]*?<\/div>/);
    expect(steps.match(/<span data-on=""><\/span>/g)).toHaveLength(9);
    expect(steps.match(/<span><\/span>/g)).toHaveLength(1);
    expect(html).not.toContain("acs-promo-progress__track");
    expect(html).toContain(`<div class="acs-promo-progress__labels" aria-hidden="true"><span>RM 299.00</span><span>RM 300.00</span></div>`);
    expect(aside(renderSale({ quote }))).toContain(`style="--progress:99%"`);
  });

  // 同一条「只画离得最近的一个门槛（满额减下一档或免运费）」：免运费 120.00 差 20.00 比满额差 134.40 近，画免运费（floor(10000 × 100 ÷ 12000) = 83，8 格）；
  // 差额相等时画满额。
  it("draws only the nearer threshold, preferring the tier on a tie", () => {
    const nearer = promotionQuote({ free_shipping_reached: false, free_shipping_gap_sen: 2000 });
    expect(cartProgress(nearer)).toEqual({ percent: 83, labels: { reached: 10000, threshold: 12000 } });
    const html = aside(renderSale({ quote: nearer, shared: sale({ cart_progress: "steps" }) }));
    expect(html.match(/<span data-on=""><\/span>/g)).toHaveLength(8);
    expect(html).toContain(`<span>RM 100.00</span><span>RM 120.00</span>`);
    expect(html.match(/<div class="acs-promo-progress">/g)).toHaveLength(1);
    const tie = promotionQuote({ spend_next_gap_sen: 2000, free_shipping_reached: false, free_shipping_gap_sen: 2000 });
    expect(cartProgress(tie)).toEqual({ percent: 93, labels: { reached: 28000, threshold: 30000 } });
  });

  // UX 0.20 P04「已全部达到时显示满格」：没有未达的门槛而有满额规则或门槛大于 0 的免运费规则时为 100%、10 格，不显示 __labels。
  it("shows a full bar when every threshold is reached", () => {
    const top = { spend_next_threshold_sen: null, spend_next_value: null, spend_next_gap_sen: null };
    for (const quote of [promotionQuote(top), promotionQuote({ ...top, free_shipping_threshold_sen: null }), promotionQuote({ ...BARE, free_shipping_threshold_sen: 12000, free_shipping_reached: true })]) {
      expect(cartProgress(quote)).toEqual({ percent: 100, labels: null });
    }
    const bar = aside(renderSale({ quote: promotionQuote(top) }));
    expect(bar).toContain(`style="--progress:100%"`);
    expect(bar).not.toContain("acs-promo-progress__labels");
    const steps = aside(renderSale({ quote: promotionQuote(top), shared: sale({ cart_progress: "steps" }) }));
    expect(steps.match(/<span data-on=""><\/span>/g)).toHaveLength(10);
  });

  // 同一条「进度条…只画…门槛（满额减下一档或免运费）」：两类规则都没有（或免运费门槛为 0）时不画进度条。
  it("draws no bar without a tier or a free shipping threshold", () => {
    expect(cartProgress(promotionQuote(BARE))).toBeNull();
    expect(cartProgress(promotionQuote({ ...BARE, free_shipping_threshold_sen: 0, free_shipping_reached: true }))).toBeNull();
    expect(renderSale({ quote: promotionQuote({ ...BARE, item_discount_sen: 3340, buy_x_get_y: [] }) })).not.toContain(PROGRESS);
  });

  // 验收「进度条与行角标要用共享读取的装饰与规则，只在 usePromotion 有活动且其 promotion_id 与计价结果相同时显示」（UX 0.20 P04「有活动生效时」）：
  // 读取中、没有活动、不包共享读取或 promotion_id 不一致时不画进度条、不显示行角标；金额行、划线价与文字提示照计价结果显示。
  it.each([
    ["loading", "loading"],
    ["no sale", NO_SALE],
    ["no provider", null],
    ["another sale", sale({ promotion_id: 8 })],
  ] as const)("draws no bar or badges with %s in the shared reading", (_, shared) => {
    const html = renderSale({ shared });
    expect(html).not.toContain(PROGRESS);
    expect(html).not.toContain("acs-promo-badges");
    expect(amountRows(aside(html))).toContainEqual([COPY["cart.after_promo"].en, "RM 150.60"]);
    expect(hintTexts(aside(html))[0]).toBe(copy("cart.hint_tier_amount", "en", { amount: "134.40", save: "40.00" }));
    expect(html).toContain("RM 62.40");
  });
});

describe("member note", () => {
  // UX 0.20 P04「已登录会员且有活动优惠时显示 [cart.member_scheme_note]…；游客不显示」与线框（凑单提示之后、[cart.shipping_later] 之前）：
  // 会员看到桌面与手机各一份；游客、尚未读完与读取失败时不显示；会员而活动优惠为 0 时也不显示。
  it.each(LANGUAGES)("shows cart.member_scheme_note to members with a sale discount only in %s", (language) => {
    const note = `<p class="acs-body-s">${escapeHtml(copy("cart.member_scheme_note", language))}</p>`;
    const member = renderSale({ language, session: MEMBER });
    expect(aside(member)).toContain(`</p></div>${note}<p class="acs-body-s acs-muted">${escapeHtml(copy("cart.shipping_later", language))}</p>`);
    expect(phoneBlock(member)).toContain(note);
    for (const session of [{ kind: "guest" }, { kind: "unknown" }, { kind: "failed" }] as const) {
      expect(renderSale({ language, session })).not.toContain(note);
    }
    expect(renderSale({ language })).not.toContain(note);
    expect(renderSale({ language, session: MEMBER, rows: PLAIN_ROWS, quote: promotionQuote({ item_discount_sen: 0, order_discount_sen: 0 }) })).not.toContain(note);
  });
});

describe("re-quoting when the sale changes", () => {
  // UX 0.20 P04「活动在本页停留期间结束时，倒计时到 0 后重新计价并去掉这些内容」：共享读取的 generation 变化时请求标识随之变化（即重新计价），
  // 新结果返回前照现有写法显示上一份结果（pending、aria-busy）；新结果没有活动时去掉全部活动内容。
  it("re-quotes on a new generation and drops the sale when the new quote has none", () => {
    const lines = cartOf(SALE_ROWS);
    expect(quoteKey("en", lines, 0)).toBe(quoteKey("en", lines));
    expect(quoteKey("en", lines, 3)).not.toBe(quoteKey("en", lines, 2));
    const before: CheckoutQuote = { lines: SALE_ROWS, subtotal_sen: 19900, ...WITHIN_LIMITS, promotion: promotionQuote() };
    const settled = { key: quoteKey("en", lines, 2) ?? "", outcome: { status: "ready" as const, quote: before }, last: before };
    const pending = quoteState(quoteKey("en", lines, 3), settled);
    expect(pending).toEqual({ status: "ready", quote: before, pending: true });
    const waiting = render({ lines, quote: pending, promotion: NO_SALE });
    expect(waiting).toContain(`<main class="site-cart" aria-busy="true">`);
    expect(waiting).toContain(COPY["cart.after_promo"].en);

    const after: CheckoutQuote = { lines: PLAIN_ROWS, subtotal_sen: 19900, ...WITHIN_LIMITS };
    const html = render({ lines, quote: quoteState(quoteKey("en", lines, 3), { key: quoteKey("en", lines, 3) ?? "", outcome: { status: "ready", quote: after }, last: after }), promotion: NO_SALE, session: MEMBER });
    expect(html).not.toContain("acs-promo");
    expect(html).not.toContain(COPY["cart.promo_discount"].en);
    expect(html).not.toContain(COPY["cart.after_promo"].en);
    expect(html).not.toContain(escapeHtml(COPY["cart.member_scheme_note"].en));
    expect(amountRows(bottomBar(html))).toEqual([[COPY["cart.subtotal"].en, "RM 199.00"]]);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-018 验收第 6 条「页面文字全部来自字典」：除接口返回的商品数据（名称、规格值、不可购买行的 SKU）、件数与金额模板外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides product data in %s", (language) => {
    const rows: QuoteLine[] = [
      priced("tee-black-m", { status: "over_limit", quantity: 6, max_per_order: 5, line_subtotal_sen: 23400 }),
      priced("candle", { product_slug: "candle", name: text("Soy Wax Candle", true), options: [], image: null }),
      unavailable("gone-sku", 2),
    ];
    const states: RenderOptions[] = [
      { language },
      { language, lines: cartOf(rows) },
      { language, lines: cartOf(rows), quote: { status: "error" } },
      { language, lines: cartOf(rows), quote: ready(rows, 27300) },
      { language, lines: cartOf(rows), quote: ready(rows, 27300), writeFailed: true },
      // 本任务加的状态：输入超过上限的提示与两条整单上限提示。
      {
        language,
        lines: cartOf(rows),
        quote: ready(rows, 27300, { units_over_limit: true, amount_over_limit: true }),
        capped: new Map([["candle", 4]]),
      },
    ];
    const productData = new Set(["Crew Neck Tee", "Soy Wax Candle", "Black", "M", "gone-sku"]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state))) {
        const known = productData.has(value) || /^\d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });

  // 同一条，延伸到 UX 0.20 P04 的活动内容：金额行、凑单提示、会员说明与读屏标签都是字典文案；活动的角标短语是接口给的活动数据。
  it.each(LANGUAGES)("shows only dictionary text besides product and sale data with a sale in %s", (language) => {
    const quote = promotionQuote({
      free_shipping_reached: false,
      free_shipping_gap_sen: 2500,
      buy_x_get_y: [{ rule_id: 14, buy_quantity: 2, get_quantity: 1, get_percent: 100, units_in_scope: 1, units_to_next: 2 }],
    });
    const data = new Set(["Crew Neck Tee", "Pullover Hoodie", "Soy Wax Candle", "Black", "M", "Lunar deal"]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const value of visibleTexts(renderSale({ language, quote, session: MEMBER }))) {
      const known = data.has(value) || /^\d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
      expect(known, value).toBe(true);
    }
  });
});

// 键名用于类型检查：本页用到的字典键都存在。
const CART_KEYS: readonly CopyKey[] = [
  "cart.title",
  "cart.empty",
  "cart.remove",
  "cart.subtotal",
  "cart.shipping_later",
  "cart.price_recheck",
  "cart.item_changed",
  "cart.over_limit",
  "cart.units_over_limit",
  "cart.amount_over_limit",
  "cart.checkout",
  "cart.continue",
  "cart.demo_hint",
  "cart.promo_discount",
  "cart.after_promo",
  "cart.hint_tier_amount",
  "cart.hint_tier_percent",
  "cart.hint_free_shipping",
  "cart.free_shipping_reached",
  "cart.hint_bxgy",
  "cart.member_scheme_note",
  "common.nav_cart",
  "common.price_myr",
  "common.error_retry",
  "common.a11y_qty_decrease",
  "common.a11y_qty_increase",
  "common.qty_capped",
  "detail.quantity",
];

describe("dictionary keys", () => {
  // SHOP-TASK-018 验收第 1 条「文案以 docs/UX-COPY.md 0.5 为准」：P04 的文案键都在字典里且三列非空（与文档的逐字比较由 i18n/copy.test.ts 覆盖）。
  it.each(CART_KEYS)("has %s in all three languages", (key) => {
    for (const language of LANGUAGES) {
      expect(COPY[key][language]).not.toBe("");
    }
  });
});
