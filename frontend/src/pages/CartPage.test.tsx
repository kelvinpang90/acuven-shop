import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchQuote, latestQuoter } from "../api/checkout";
import type { CheckoutQuote, PricedQuoteLine, QuoteLine, UnavailableQuoteLine } from "../api/checkout";
import App from "../App";
import { CART_STORAGE_KEY, readCart } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { CartView, canIncrease, changeQuantity, hasChangedLine, quoteKey, quoteState, removeFromCart } from "./CartPage";
import type { QuoteState } from "./CartPage";

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
    ...overrides,
  };
}

function unavailable(sku: string, quantity = 1): UnavailableQuoteLine {
  return { sku, quantity, status: "unavailable" };
}

function cartOf(rows: readonly QuoteLine[]): CartLine[] {
  return rows.map((row) => ({ sku: row.sku, slug: row.status === "unavailable" ? "gone" : row.product_slug, quantity: row.quantity }));
}

function ready(lines: QuoteLine[], subtotal_sen = 0): QuoteState {
  return { status: "ready", quote: { lines, subtotal_sen }, pending: false };
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
  language?: Language;
}

function render({ lines = [], quote = { status: "loading" }, writeFailed = false, language = "en" }: RenderOptions = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/cart">
        <CartView lines={lines} quote={quote} writeFailed={writeFailed} onQuantity={() => undefined} onRemove={() => undefined} />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function renderRows(rows: QuoteLine[], options: Omit<RenderOptions, "lines" | "quote"> & { subtotal?: number } = {}): string {
  const { subtotal = 0, ...rest } = options;
  return render({ lines: cartOf(rows), quote: ready(rows, subtotal), ...rest });
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

function items(html: string): string[] {
  return [...html.matchAll(/<div class="site-cart__item">[\s\S]*?<\/button><\/div>(?:<p class="acs-field__error site-cart__note">[^<]*<\/p>)?<\/div>/g)].map((m) => m[0]);
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
  // 名称链到商品详情，规格值之间是图形分隔，件数在步进器的 output 里。
  it.each(LANGUAGES)("shows the image, name, options, quantity, line subtotal and remove button in %s", (language) => {
    const row = priced("tee-black-m", { quantity: 2, line_subtotal_sen: 7800 });
    const [item = ""] = items(renderRows([row], { language }));
    expect(item).toContain(`<span class="acs-cat__img site-cart__img"><img class="site-img" src="/img/tee.svg" alt="" loading="lazy"/></span>`);
    expect(item).toContain(`<a href="/products/crew-neck-tee">Crew Neck Tee</a>`);
    expect(item).toMatch(/<span>Black<\/span><\/span><span class="site-cart__option"><svg[^>]*aria-hidden="true">[\s\S]*?<\/svg><span>M<\/span>/);
    expect(item).toContain(`<output aria-live="polite">2</output>`);
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
    const bar = part(html, /<div class="acs site-phone-only site-cart__bar">[\s\S]*?<\/div><\/div>/);
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

  // UX P04「去向：P05；P02（继续购物）」与 SHOP-TASK-018 验收第 2 条「cart.checkout 按路由规则在结账页实现前不渲染」：只有链到商品列表的 cart.continue。
  it.each(LANGUAGES)("links cart.continue to the product list and has no checkout button in %s", (language) => {
    const html = renderRows([priced("tee-black-m")], { language });
    expect(html).toContain(`<a class="acs-btn acs-btn--quiet site-cart__continue" href="/products">${COPY["cart.continue"][language]}</a>`);
    expect(html).not.toContain(COPY["cart.checkout"][language]);
    expect(html).not.toContain("/checkout");
  });

  // UX P04 金额元素 M1「不信任浏览器保存的价格」与 SHOP-TASK-018 验收第 3 条：从购物车移除的行在新结果返回前就不再显示（用上一次的结果时只显示仍在购物车里的行）。
  it("hides a removed line before the new quote arrives", () => {
    const rows = [priced("tee-black-m"), priced("candle", { product_slug: "candle", name: text("Soy Wax Candle") })];
    const html = render({ lines: cartOf(rows).slice(1), quote: { status: "ready", quote: { lines: rows, subtotal_sen: 0 }, pending: true } });
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
  const first: CheckoutQuote = { lines: [priced("a")], subtotal_sen: 3900 };

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
  "cart.checkout",
  "cart.continue",
  "cart.demo_hint",
  "common.nav_cart",
  "common.price_myr",
  "common.error_retry",
  "common.a11y_qty_decrease",
  "common.a11y_qty_increase",
];

describe("dictionary keys", () => {
  // SHOP-TASK-018 验收第 1 条「文案以 docs/UX-COPY.md 0.5 为准」：P04 的文案键都在字典里且三列非空（与文档的逐字比较由 i18n/copy.test.ts 覆盖）。
  it.each(CART_KEYS)("has %s in all three languages", (key) => {
    for (const language of LANGUAGES) {
      expect(COPY[key][language]).not.toBe("");
    }
  });
});
