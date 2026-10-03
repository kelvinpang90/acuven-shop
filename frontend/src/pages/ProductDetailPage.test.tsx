import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ProductDetail, VariantDetail } from "../api/catalog";
import App from "../App";
import { CART_STORAGE_KEY, readCart } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { ProductDetailView, addSelection, detailRedirect } from "./ProductDetailPage";
import type { AddNotice } from "./ProductDetailPage";
import type { Selection } from "./productOptions";

function text(value: string, english_fallback = false) {
  return { text: value, english_fallback };
}

function variant(sku: string, options: Record<string, string>, price_sen: number, available_stock = 20): VariantDetail {
  return { sku, options, price_sen, available_stock };
}

// 只有红/S（RM 39.00）与蓝/M（RM 42.00）两个启用 SKU 的商品。
function product(overrides: Partial<ProductDetail> = {}): ProductDetail {
  return {
    slug: "crew-neck-tee",
    name: text("Crew Neck Tee"),
    description: text("A soft cotton tee."),
    category: { slug: "apparel", name: text("Apparel") },
    images: ["/img/1.svg", "/img/2.svg", "/img/3.svg"],
    options: [
      { code: "color", name: text("Colour"), values: [{ code: "red", name: text("Red") }, { code: "blue", name: text("Blue") }] },
      { code: "size", name: text("Size"), values: [{ code: "s", name: text("S") }, { code: "m", name: text("M") }] },
    ],
    variants: [variant("tee-red-s", { color: "red", size: "s" }, 3900, 7), variant("tee-blue-m", { color: "blue", size: "m" }, 4200, 3)],
    max_per_order: 5,
    ...overrides,
  };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  item?: ProductDetail;
  selection?: Selection;
  quantity?: number;
  lines?: readonly CartLine[];
  notice?: AddNotice;
  image?: number;
  language?: Language;
}

function render({
  item = product(),
  selection = {},
  quantity = 1,
  lines = [],
  notice = null,
  image = 0,
  language = "en",
}: RenderOptions = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={`/products/${item.slug}`}>
        <ProductDetailView
          product={item}
          selection={selection}
          quantity={quantity}
          lines={lines}
          notice={notice}
          image={image}
          onToggle={() => undefined}
          onQuantity={() => undefined}
          onAdd={() => undefined}
          onImage={() => undefined}
        />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const crumbs = (html: string) => part(html, /<nav class="acs-body-s site-detail__crumbs">[\s\S]*?<\/nav>/);
const buy = (html: string) => part(html, /<div class="acs site-detail__buy">[\s\S]*?<\/button><\/div>/);
const stepper = (html: string) => part(html, /<div class="acs-stepper"[\s\S]*?<\/div>/);
const addButton = (html: string) => part(html, /<button class="acs-btn acs-btn--primary[^"]*"[^>]*>[^<]*<\/button>/);

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

describe("before the product arrives", () => {
  // SHOP-TASK-017 验收第 2 条「详情页在接口返回之前页面主体不出现字典以外的文字（含读屏标签）、行内样式、表单或指向未实现页面的链接，面包屑与购买区等数据返回后才渲染」：
  // 服务端渲染不发请求，/products/:slug 本身的主体是空的。
  it.each(["/products/:slug", "/products/crew-neck-tee"])("renders an empty main for %s", (path) => {
    const html = renderToStaticMarkup(<App initialPath={path} storage={languageStorage("en")} />);
    const main = part(html, /<main[\s\S]*?<\/main>/);
    expect(main).toBe(`<main class="site-detail" aria-busy="true"></main>`);
    expect(html).not.toContain("style=");
    expect(html).not.toContain(COPY["detail.add_to_cart"].en);
  });
});

describe("product detail page", () => {
  // UX P03 桌面线框「面包屑：[common.nav_shop] / <分类> / <名称>」与 SHOP-TASK-017 验收第 2 条「分类（链到按该分类筛选的列表）」「面包屑分隔符…用图形而不是文字节点」「面包屑不加字典里没有的读屏标签」。
  it.each(LANGUAGES)("shows the breadcrumb with graphic separators in %s", (language) => {
    const nav = crumbs(render({ language }));
    expect(nav).toContain(`<a class="acs-muted" href="/products">${COPY["common.nav_shop"][language]}</a>`);
    expect(nav).toContain(`<a class="acs-muted" href="/products?category=apparel">Apparel</a>`);
    expect(nav).toContain(`<span aria-current="page">Crew Neck Tee</span>`);
    expect(nav.match(/<svg[^>]*aria-hidden="true"/g)).toHaveLength(2);
    expect(visibleTexts(nav)).toEqual([COPY["common.nav_shop"][language], "Apparel", "Crew Neck Tee"]);
    expect(nav).not.toContain("aria-label");
  });

  // UX P03 说明「当前语言缺少商品文案、回退英文时，在商品名称旁显示「仅英文」标签 [detail.english_only]」（Q8 已决），回退的文字按英文读。
  it.each(LANGUAGES)("labels an English fallback name in %s", (language) => {
    const html = render({ item: product({ name: text("Crew Neck Tee", true) }), language });
    expect(html).toContain(
      `<h1 class="acs-display-l" lang="en">Crew Neck Tee</h1><span class="acs-tag acs-tag--outline">${COPY["detail.english_only"][language]}</span>`,
    );
  });

  // UX P03 说明的反面：有当前语言的文案时不显示「仅英文」。
  it("has no English-only label when the text is translated", () => {
    expect(render({ language: "zh" })).not.toContain(COPY["detail.english_only"].zh);
  });

  // UX P03 说明「桌面缩略图可点选切换主图，手机主图可左右滑动；每张图的读屏标签用 [detail.a11y_image]」。
  it.each(LANGUAGES)("labels every image with its position in %s", (language) => {
    const html = render({ image: 1, language });
    const label = (n: number) => escapeHtml(COPY["detail.a11y_image"][language].replace("{n}", String(n)).replace("{count}", "3"));
    const main = part(html, /<div class="acs-cat__img site-desktop-only site-detail__main">[\s\S]*?<\/div>/);
    expect(main).toContain(`<img class="site-img" src="/img/2.svg" alt="${label(2)}"/>`);
    const thumbs = part(html, /<div class="site-desktop-only site-detail__thumbs">[\s\S]*?<\/div>/);
    expect([...thumbs.matchAll(/<button[^>]*aria-label="([^"]*)" aria-pressed="(true|false)"/g)].map((m) => [m[1], m[2]])).toEqual([
      [label(1), "false"],
      [label(2), "true"],
      [label(3), "false"],
    ]);
    const slides = part(html, /<div class="site-phone-only site-detail__slides">[\s\S]*?<\/div>/);
    expect([...slides.matchAll(/alt="([^"]*)"/g)].map((m) => m[1])).toEqual([label(1), label(2), label(3)]);
  });

  // UX P03 主图：没有图片时显示占位形状，不渲染空的 img 与缩略图。
  it("shows a placeholder when there are no images", () => {
    const html = render({ item: product({ images: [] }) });
    expect(html).not.toContain("<img");
    expect(html).not.toContain("site-detail__thumbs");
  });

  // UX P03 线框的顺序：名称、价格、detail.options、规格、数量、detail.max_per_order、detail.stock_left、加入购物车、★ detail.demo_hint、描述。
  it("follows the order of the wireframe", () => {
    const html = render({ selection: { color: "red", size: "s" } });
    const positions = [
      html.indexOf("<h1"),
      html.indexOf("RM 39.00"),
      html.indexOf(COPY["detail.options"].en),
      html.indexOf("Colour"),
      html.indexOf(COPY["detail.quantity"].en),
      html.indexOf(COPY["detail.max_per_order"].en.replace("{count}", "5")),
      html.indexOf(COPY["detail.stock_left"].en.replace("{count}", "7")),
      html.indexOf(COPY["detail.add_to_cart"].en),
      html.indexOf(escapeHtml(COPY["detail.demo_hint"].en)),
      html.indexOf(COPY["detail.description"].en),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // UX P03「演示提示」：★ [detail.demo_hint]。
  it.each(LANGUAGES)("shows the demo hint in %s", (language) => {
    expect(render({ language })).toContain(`<p class="acs-hint">`);
    expect(render({ language })).toContain(escapeHtml(COPY["detail.demo_hint"][language]));
  });

  // UX P03 手机线框「[detail.description] ▾」与 SHOP-TASK-017 验收第 3 条「描述在手机上可折叠」：桌面为标题加正文，手机为默认收起的折叠块。
  it("has a desktop description and a collapsible one for phones", () => {
    const html = render();
    expect(html).toContain(
      `<section class="site-desktop-only site-detail__description"><h2 class="acs-display-s">${COPY["detail.description"].en}</h2><p>A soft cotton tee.</p></section>`,
    );
    expect(html).toContain(
      `<details class="site-phone-only site-detail__toggle"><summary class="acs-field__label">${COPY["detail.description"].en}</summary><p>A soft cotton tee.</p></details>`,
    );
  });

  // SHOP-TASK-017 验收第 6 条「页面文字全部来自字典」：除商品数据（名称、分类、规格、描述）与数量数字外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides product data in %s", (language) => {
    const states: RenderOptions[] = [
      { language },
      { language, selection: { color: "red", size: "s" }, notice: "added" },
      { language, notice: "select_all_options" },
      { language, notice: "error" },
      { language, lines: [{ sku: "tee-red-s", slug: "crew-neck-tee", quantity: 5 }] },
      { language, item: product({ name: text("Crew Neck Tee", true) }) },
    ];
    const productData = new Set(["Crew Neck Tee", "Apparel", "Colour", "Size", "Red", "Blue", "S", "M", "A soft cotton tee."]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state))) {
        const known = productData.has(value) || /^\d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

describe("options and price", () => {
  // SHOP-TASK-017 验收第 3 条「与已选的其他规格值组不成任何启用 SKU 的值禁用」：只有红/S 与蓝/M 时选红与 S 后蓝和 M 都禁用（按钮为 aria-pressed 的规格芯片）。
  it("disables chips that make no enabled SKU with the chosen ones", () => {
    const html = render({ selection: { color: "red", size: "s" } });
    const chips = [...html.matchAll(/<button class="acs-chip" type="button" aria-pressed="(true|false)"( disabled="")?>([^<]*)<\/button>/g)].map(
      (m) => [m[3], m[1] === "true", m[2] !== undefined],
    );
    expect(chips).toEqual([
      ["Red", true, false],
      ["Blue", false, true],
      ["S", true, false],
      ["M", false, true],
    ]);
  });

  // SHOP-TASK-017 验收第 3 条「初始不预选」：什么都没选时没有按下的芯片，也没有禁用的芯片。
  it("starts with no chip pressed", () => {
    const html = render();
    expect(html.match(/class="acs-chip"/g)).toHaveLength(4);
    expect(html).not.toMatch(/class="acs-chip"[^>]*aria-pressed="true"/);
    expect(html).not.toMatch(/class="acs-chip"[^>]*disabled/);
  });

  // SHOP-TASK-017 验收第 3 条「选全前价格按 SHOP-TASK-015 的起价规则显示」：多个启用 SKU 时为 list.price_from，且不显示 detail.stock_left。
  it.each(LANGUAGES)("shows the from-price before every option is chosen in %s", (language) => {
    const html = render({ selection: { color: "blue" }, language });
    expect(html).toContain(`<span class="acs-price acs-price--l">${COPY["list.price_from"][language].replace("{amount}", "39.00")}</span>`);
    expect(html).not.toContain(COPY["detail.stock_left"][language].replace("{count}", "3"));
  });

  // SHOP-TASK-017 验收第 3 条「选全后显示所选 SKU 的单价与 detail.stock_left（当日可用库存）」。
  it.each(LANGUAGES)("shows the chosen SKU's price and stock once every option is chosen in %s", (language) => {
    const html = render({ selection: { color: "blue", size: "m" }, language });
    expect(html).toContain(`<span class="acs-price acs-price--l">RM 42.00</span>`);
    expect(html).toContain(`<span class="acs-caption">${COPY["detail.stock_left"][language].replace("{count}", "3")}</span>`);
  });

  // UX P03 的 M1「不显示参考外币」（Q9）。
  it("shows no reference currency", () => {
    const html = render({ selection: { color: "blue", size: "m" } });
    expect(html).not.toContain("≈");
    expect(html).not.toMatch(/SGD|USD|CNY/);
  });
});

describe("quantity and purchase limit", () => {
  // SHOP-TASK-017 验收第 2 条「数量 (+)(-) 用图形而不是文字节点（读屏标签用 common.a11y_qty_increase、common.a11y_qty_decrease）」与 COMPONENTS「Options」：值放在 output 里，最小值时 (-) 禁用。
  it.each(LANGUAGES)("uses labelled graphic buttons around the quantity in %s", (language) => {
    const box = stepper(render({ language }));
    expect(box).toMatch(
      new RegExp(`<button type="button" aria-label="${COPY["common.a11y_qty_decrease"][language]}" disabled=""><svg[^>]*aria-hidden="true"`),
    );
    expect(box).toMatch(new RegExp(`<button type="button" aria-label="${COPY["common.a11y_qty_increase"][language]}"><svg[^>]*aria-hidden="true"`));
    expect(box).toContain(`<output aria-live="polite">1</output>`);
    expect(visibleTexts(box)).toEqual(["1", COPY["common.a11y_qty_decrease"][language], COPY["common.a11y_qty_increase"][language]]);
  });

  // UX P03 线框 [detail.max_per_order]：显示该商品的限购件数。
  it.each(LANGUAGES)("shows the limit per order in %s", (language) => {
    expect(render({ language })).toContain(`<span class="acs-caption">${COPY["detail.max_per_order"][language].replace("{count}", "5")}</span>`);
  });

  // UX P03「数量 (+) 的上限是限购件数减去购物车中该商品已有的件数」：同一商品其他 SKU 的件数也计入（限购 5，购物车已有蓝/M 3 件，上限 2）。
  it("stops the plus button at the limit minus the product's quantity in the cart", () => {
    const lines = [
      { sku: "tee-blue-m", slug: "crew-neck-tee", quantity: 3 },
      { sku: "bag-std", slug: "zip-pouch", quantity: 9 },
    ];
    const atTwo = stepper(render({ selection: { color: "red", size: "s" }, quantity: 2, lines }));
    expect(atTwo).toContain(`<output aria-live="polite">2</output>`);
    expect(atTwo).toMatch(new RegExp(`aria-label="${COPY["common.a11y_qty_increase"].en}" disabled=""`));
    const clamped = stepper(render({ quantity: 4, lines }));
    expect(clamped).toContain(`<output aria-live="polite">2</output>`);
    const atOne = stepper(render({ quantity: 1, lines }));
    expect(atOne).not.toMatch(new RegExp(`aria-label="${COPY["common.a11y_qty_increase"].en}" disabled=""`));
  });

  // UX P03「该商品已达限购时加入按钮禁用并显示 [detail.limit_reached]」：各 SKU 合计达到限购（红/S 2 件 + 蓝/M 3 件 = 5）。
  it.each(LANGUAGES)("disables adding and explains why once the limit is reached in %s", (language) => {
    const lines = [
      { sku: "tee-red-s", slug: "crew-neck-tee", quantity: 2 },
      { sku: "tee-blue-m", slug: "crew-neck-tee", quantity: 3 },
    ];
    const html = buy(render({ lines, language }));
    expect(html).toContain(`<p class="acs-field__hint" role="status">${COPY["detail.limit_reached"][language].replace("{count}", "5")}</p>`);
    expect(addButton(html)).toContain(`disabled=""`);
    expect(stepper(render({ lines, language }))).toMatch(new RegExp(`aria-label="${escapeHtml(COPY["common.a11y_qty_increase"][language])}" disabled=""`));
  });

  // UX P03「购物车已有 20 行且所选规格不在其中时加入按钮禁用并显示 [detail.cart_full]」（{count} 为 20）；所选规格已在其中时可以加入。
  it.each(LANGUAGES)("disables adding a new SKU to a full cart in %s", (language) => {
    const others = Array.from({ length: 19 }, (_, index) => ({ sku: `x-${String(index)}`, slug: `x-${String(index)}`, quantity: 1 }));
    const lines = [...others, { sku: "tee-red-s", slug: "crew-neck-tee", quantity: 1 }];
    const blue = buy(render({ selection: { color: "blue", size: "m" }, lines, language }));
    expect(blue).toContain(`<p class="acs-field__hint" role="status">${escapeHtml(COPY["detail.cart_full"][language].replace("{count}", "20"))}</p>`);
    expect(addButton(blue)).toContain(`disabled=""`);
    const red = buy(render({ selection: { color: "red", size: "s" }, lines, language }));
    expect(red).not.toContain(escapeHtml(COPY["detail.cart_full"][language].replace("{count}", "20")));
    expect(addButton(red)).not.toContain(`disabled=""`);
  });

  // SHOP-TASK-017 验收第 5 条「未选全就点加入时显示 detail.select_all_options」：按钮在未选全时仍可点，点后显示提示。
  it.each(LANGUAGES)("keeps the button enabled and asks to choose all options in %s", (language) => {
    expect(addButton(buy(render({ language })))).not.toContain("disabled");
    const html = buy(render({ notice: "select_all_options", language }));
    expect(html).toContain(`<p class="acs-field__error" role="alert">${COPY["detail.select_all_options"][language]}</p>`);
  });

  // UX P03「加入后提示条：[detail.added] ( [detail.view_cart] )」与 SHOP-TASK-018 验收第 2 条「P03 的 detail.view_cart 按路由规则开始渲染」：购物车页进了路由表，提示条含链到 /cart 的 detail.view_cart。
  it.each(LANGUAGES)("confirms the item was added with a link to the cart in %s", (language) => {
    const html = buy(render({ selection: { color: "red", size: "s" }, notice: "added", language }));
    const alert = part(html, /<div class="acs-alert acs-alert--success site-detail__added" role="status">[\s\S]*?<\/div>/);
    expect(alert).toMatch(new RegExp(`<span class="site-detail__added-text"><svg[^>]*aria-hidden="true">[\\s\\S]*</svg>${COPY["detail.added"][language]}</span>`));
    expect(alert).toContain(`<a href="/cart">${COPY["detail.view_cart"][language]}</a></div>`);
    expect(visibleTexts(alert)).toEqual([COPY["detail.added"][language], COPY["detail.view_cart"][language]]);
  });
});

describe("adding to the browser cart", () => {
  const item = product();

  function memoryStorage(initial: readonly CartLine[] = []): CartStorage {
    const data: Record<string, string> = { [CART_STORAGE_KEY]: JSON.stringify(initial) };
    return {
      getItem: (key) => data[key] ?? null,
      setItem: (key, value) => {
        data[key] = value;
      },
    };
  }

  // SHOP-TASK-017 验收第 3 条「未选全就点加入时显示 detail.select_all_options」：购物车不变。
  it("asks for every option before adding", () => {
    const storage = memoryStorage();
    expect(addSelection(storage, item, { color: "red" }, 1, [])).toBe("select_all_options");
    expect(readCart(storage)).toEqual([]);
  });

  // SHOP-TASK-017 验收第 4 条「同一 SKU 再次加入时合并件数」与第 5 条「加入成功后显示 detail.added」：只存 SKU、slug 与件数。
  it("adds the chosen SKU and merges it with the same SKU", () => {
    const storage = memoryStorage([{ sku: "tee-red-s", slug: "crew-neck-tee", quantity: 1 }]);
    const lines = readCart(storage);
    expect(addSelection(storage, item, { color: "red", size: "s" }, 2, lines)).toBe("added");
    expect(readCart(storage)).toEqual([{ sku: "tee-red-s", slug: "crew-neck-tee", quantity: 3 }]);
  });

  // UX P03「数量 (+) 的上限是限购件数减去购物车中该商品已有的件数」：加入的件数不超过剩余件数。
  it("never adds more than the remaining quantity", () => {
    const storage = memoryStorage([{ sku: "tee-blue-m", slug: "crew-neck-tee", quantity: 3 }]);
    expect(addSelection(storage, item, { color: "red", size: "s" }, 4, readCart(storage))).toBe("added");
    expect(readCart(storage)).toEqual([
      { sku: "tee-blue-m", slug: "crew-neck-tee", quantity: 3 },
      { sku: "tee-red-s", slug: "crew-neck-tee", quantity: 2 },
    ]);
  });

  // UX P03「该商品已达限购时加入按钮禁用」与「购物车已有 20 行…禁用」：即使被调用也不加入。
  it("does not add when the button is disabled", () => {
    const limit = [{ sku: "tee-blue-m", slug: "crew-neck-tee", quantity: 5 }];
    const storage = memoryStorage(limit);
    expect(addSelection(storage, item, { color: "red", size: "s" }, 1, limit)).toBeNull();
    expect(readCart(storage)).toEqual(limit);
    const full = Array.from({ length: 20 }, (_, index) => ({ sku: `x-${String(index)}`, slug: `x-${String(index)}`, quantity: 1 }));
    const fullStorage = memoryStorage(full);
    expect(addSelection(fullStorage, item, { color: "red", size: "s" }, 1, full)).toBeNull();
    expect(readCart(fullStorage)).toEqual(full);
  });

  // SHOP-TASK-017 验收第 4 条「读写失败时页面照常可用」：写入失败时不抛错，显示 common.error_retry（字典里的一般失败提示）。
  it("reports a failed write instead of throwing", () => {
    const failing: CartStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(addSelection(failing, item, { color: "red", size: "s" }, 1, [])).toBe("error");
    expect(addSelection(null, item, { color: "red", size: "s" }, 1, [])).toBe("error");
    const html = buy(render({ notice: "error" }));
    expect(html).toContain(COPY["common.error_retry"].en);
  });
});

describe("missing product", () => {
  // SHOP-TASK-017 验收第 2 条「商品不存在或未发布（接口 404）时替换为商品列表页，不另显示文字」：只有 404 换成列表，其他状态留在本页。
  it("goes back to the product list only when the product is not found", () => {
    expect(detailRedirect({ status: "not_found" })).toBe("/products");
    expect(detailRedirect({ status: "error" })).toBeNull();
    expect(detailRedirect({ status: "loading" })).toBeNull();
    expect(detailRedirect({ status: "ready", data: product() })).toBeNull();
  });
});

// 键名用于类型检查：本页用到的字典键都存在。
const DETAIL_KEYS: readonly CopyKey[] = [
  "detail.options",
  "detail.quantity",
  "detail.stock_left",
  "detail.add_to_cart",
  "detail.added",
  "detail.view_cart",
  "detail.select_all_options",
  "detail.max_per_order",
  "detail.limit_reached",
  "detail.cart_full",
  "detail.description",
  "detail.english_only",
  "detail.a11y_image",
  "detail.demo_hint",
  "common.a11y_qty_decrease",
  "common.a11y_qty_increase",
];

describe("dictionary", () => {
  // SHOP-TASK-017 验收第 1 条「页面内容…文案以 docs/UX-COPY.md 0.5 为准」：P03 的文案键都在字典里且三列非空（与文档的逐字比较由 i18n/copy.test.ts 覆盖）。
  it.each(DETAIL_KEYS)("has %s in all three languages", (key) => {
    for (const language of LANGUAGES) {
      expect(COPY[key][language]).not.toBe("");
    }
  });
});
