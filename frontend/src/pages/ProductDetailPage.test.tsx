import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CardPromotion, ProductDetail, VariantDetail } from "../api/catalog";
import type { PromotionDisplay, PromotionResponse, RuleDisplay } from "../api/promotion";
import App from "../App";
import { CART_STORAGE_KEY, readCart } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { saleLinkPath } from "../components/PromoParts";
import { BRAND, COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PromotionProvider } from "../promotion";
import { badgeText, countdownText } from "../promoText";
import { RouterProvider } from "../router";
import { ProductDetailView, addSelection, detailRedirect } from "./ProductDetailPage";
import type { AddNotice } from "./ProductDetailPage";
import { quantityInputResult } from "./productOptions";
import type { Selection } from "./productOptions";

function text(value: string, english_fallback = false) {
  return { text: value, english_fallback };
}

function variant(sku: string, options: Record<string, string>, price_sen: number, available_stock = 20): VariantDetail {
  return { sku, options, price_sen, available_stock, sale_price_sen: null };
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
    promotion: null,
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
  draft?: string | null;
  capped?: boolean;
  lines?: readonly CartLine[];
  notice?: AddNotice;
  image?: number;
  language?: Language;
  // 一次活动读取的结果（PromotionProvider 的 initial）；不给时不包 PromotionProvider（没有活动）。
  promotion?: PromotionResponse;
}

function render({
  item = product(),
  selection = {},
  quantity = 1,
  draft = null,
  capped = false,
  lines = [],
  notice = null,
  image = 0,
  language = "en",
  promotion,
}: RenderOptions = {}): string {
  const view = (
    <ProductDetailView
      product={item}
      selection={selection}
      quantity={quantity}
      draft={draft}
      capped={capped}
      lines={lines}
      notice={notice}
      image={image}
      onToggle={() => undefined}
      onQuantity={() => undefined}
      onDraft={() => undefined}
      onQuantityInput={() => undefined}
      onAdd={() => undefined}
      onImage={() => undefined}
    />
  );
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={`/products/${item.slug}`}>
        {promotion === undefined ? (
          view
        ) : (
          <PromotionProvider initial={promotion} fetcher={() => new Promise<PromotionResponse>(() => undefined)} clock={() => 0}>
            {view}
          </PromotionProvider>
        )}
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
const quantityInput = (html: string) => part(html, /<input class="acs-input site-detail__qty-input"[^>]*\/>/);
const shownValue = (html: string) => part(quantityInput(html), /value="[^"]*"/);

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
      { language, quantity: 5, capped: true },
      { language, lines: [{ sku: "bag-std", slug: "zip-pouch", quantity: 1000 }, { sku: "mug-std", slug: "mug", quantity: 1000 }] },
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
  // SHOP-TASK-017 验收第 2 条「数量 (+)(-) 用图形而不是文字节点（读屏标签用 common.a11y_qty_increase、common.a11y_qty_decrease）」，最小值时 (-) 禁用；
  // 值原放在 output 里，SHOP-TASK-193 按 docs/UX.md P03「(-) 与 (+) 之间是数字输入框（读屏标签 [detail.quantity]）」改为输入框
  // （视觉稿 P03-P04-caps 的 acs-input，inputmode 为 numeric）。
  it.each(LANGUAGES)("uses labelled graphic buttons around a numeric quantity box in %s", (language) => {
    const box = stepper(render({ language }));
    expect(box).toMatch(
      new RegExp(`<button type="button" aria-label="${COPY["common.a11y_qty_decrease"][language]}" disabled=""><svg[^>]*aria-hidden="true"`),
    );
    expect(box).toMatch(new RegExp(`<button type="button" aria-label="${COPY["common.a11y_qty_increase"][language]}"><svg[^>]*aria-hidden="true"`));
    const input = quantityInput(box);
    expect(input).toMatch(/\sinputmode="numeric"/i);
    expect(input).toContain(`aria-label="${COPY["detail.quantity"][language]}"`);
    expect(input).toContain(`value="1"`);
    expect(box).not.toContain("<output");
    expect(visibleTexts(box)).toEqual([COPY["common.a11y_qty_decrease"][language], COPY["detail.quantity"][language], COPY["common.a11y_qty_increase"][language]]);
  });

  // 任务契约「输入中的文字只存在页面状态里」：未生效的文字原样显示在输入框里，不改件数，(+) 仍按显示的件数判断。
  it("shows the text being typed until it takes effect", () => {
    const html = render({ quantity: 2, draft: "4x" });
    expect(shownValue(html)).toBe(`value="4x"`);
    expect(stepper(html)).not.toMatch(new RegExp(`aria-label="${COPY["common.a11y_qty_increase"].en}" disabled=""`));
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
    // 件数原以 output 显示，SHOP-TASK-193 改为输入框的 value。
    const atTwo = stepper(render({ selection: { color: "red", size: "s" }, quantity: 2, lines }));
    expect(shownValue(atTwo)).toBe(`value="2"`);
    expect(atTwo).toMatch(new RegExp(`aria-label="${COPY["common.a11y_qty_increase"].en}" disabled=""`));
    const clamped = stepper(render({ quantity: 4, lines }));
    expect(shownValue(clamped)).toBe(`value="2"`);
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

  // docs/UX.md P03「购物车全部行合计已达 2,000 件：( [detail.add_to_cart] ) 禁用，旁边显示 [detail.cart_units_full]」（{count} 为 2,000，
  // HANDOFF 0.80「{count} 直接传数字」）；与 detail.limit_reached 同一位置、同样的标签与类名，(+) 同样禁用（视觉稿 P03-P04-caps）。
  it.each(LANGUAGES)("disables adding and explains why once the whole cart holds 2,000 items in %s", (language) => {
    const lines = [
      { sku: "bag-std", slug: "zip-pouch", quantity: 1000 },
      { sku: "mug-std", slug: "mug", quantity: 1000 },
    ];
    const html = render({ selection: { color: "red", size: "s" }, lines, language });
    const area = buy(html);
    expect(area).toContain(`<p class="acs-field__hint" role="status">${escapeHtml(COPY["detail.cart_units_full"][language].replace("{count}", "2000"))}</p>`);
    expect(addButton(area)).toContain(`disabled=""`);
    expect(area).not.toContain(escapeHtml(COPY["detail.limit_reached"][language].replace("{count}", "5")));
    expect(stepper(html)).toMatch(new RegExp(`aria-label="${escapeHtml(COPY["common.a11y_qty_increase"][language])}" disabled=""`));
    // 少 1 件时不禁用。
    const oneLess = [
      { sku: "bag-std", slug: "zip-pouch", quantity: 1000 },
      { sku: "mug-std", slug: "mug", quantity: 999 },
    ];
    const roomForOne = buy(render({ selection: { color: "red", size: "s" }, lines: oneLess, language }));
    expect(roomForOne).not.toContain(escapeHtml(COPY["detail.cart_units_full"][language].replace("{count}", "2000")));
    expect(addButton(roomForOne)).not.toContain(`disabled=""`);
  });

  // docs/UX.md P03「与另两种禁用原因同时满足时只显示一条，依次为 [detail.limit_reached]、[detail.cart_units_full]、[detail.cart_full]」：
  // 本商品已达限购、整单 2,000 件且 20 行已满时只显示 detail.limit_reached。
  it.each(LANGUAGES)("shows only the limit reached message when the whole cart is also full in %s", (language) => {
    const lines = [
      { sku: "tee-red-s", slug: "crew-neck-tee", quantity: 5 },
      { sku: "bag-std", slug: "zip-pouch", quantity: 1000 },
      { sku: "mug-std", slug: "mug", quantity: 978 },
      ...Array.from({ length: 17 }, (_, index) => ({ sku: `x-${String(index)}`, slug: `x-${String(index)}`, quantity: 1 })),
    ];
    const area = buy(render({ selection: { color: "blue", size: "m" }, lines, language }));
    expect(area.match(/<p class="acs-field__hint" role="status">/g)).toHaveLength(1);
    expect(area).toContain(`<p class="acs-field__hint" role="status">${COPY["detail.limit_reached"][language].replace("{count}", "5")}</p>`);
    expect(area).not.toContain(escapeHtml(COPY["detail.cart_units_full"][language].replace("{count}", "2000")));
    expect(area).not.toContain(escapeHtml(COPY["detail.cart_full"][language].replace("{count}", "20")));
    expect(addButton(area)).toContain(`disabled=""`);
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

describe("typing a quantity", () => {
  // 生效时页面以 quantityInputResult 的结果重新渲染（与页面的 onQuantityInput 相同），这里直接调用它再渲染 View，不模拟输入。
  const lines = [{ sku: "tee-blue-m", slug: "crew-neck-tee", quantity: 1 }];

  // docs/UX.md P03「数量可直接输入」「为空、小于 1 或不是整数时恢复为原来的件数」：件数仍为原来的 3，不显示 common.qty_capped。
  it.each(["", "  ", "0", "-1", "2.5", "1e1", "３"])("restores the previous quantity for %j", (typed) => {
    const result = quantityInputResult(typed, 3, 4);
    const html = render({ quantity: result.quantity, capped: result.capped, lines });
    expect(shownValue(html)).toBe(`value="3"`);
    expect(html).not.toContain("the most you can have in this order");
  });

  // 同上「超过上限时改为上限，并在数量下方显示 [common.qty_capped]（{count} 为上限）」：限购 5、购物车已有本商品 1 件，上限 4；
  // 提示用 acs-field__hint 与 role status，位于数量之后、detail.max_per_order 之前（视觉稿 P03-P04-caps）。
  it.each(LANGUAGES)("caps a typed quantity above the limit and says so in %s", (language) => {
    const result = quantityInputResult("25", 1, 4);
    expect(result).toEqual({ quantity: 4, capped: true });
    const html = render({ quantity: result.quantity, capped: result.capped, lines, language });
    expect(shownValue(html)).toBe(`value="4"`);
    const hint = `<p class="acs-field__hint" role="status">${COPY["common.qty_capped"][language].replace("{count}", "4")}</p>`;
    expect(html).toContain(hint);
    const position = html.indexOf(hint);
    expect(position).toBeGreaterThan(html.indexOf(`<div class="acs-stepper"`));
    expect(position).toBeLessThan(html.indexOf(COPY["detail.max_per_order"][language].replace("{count}", "5")));
    expect(stepper(html)).toMatch(new RegExp(`aria-label="${escapeHtml(COPY["common.a11y_qty_increase"][language])}" disabled=""`));
  });

  // 同上「超过上限时改为上限」的反面：不超过时照收，不显示提示；任务契约「(+)(-)、改选规格、加入或再次输入后收起 common.qty_capped」——
  // 收起后（capped 为 false）不再显示。
  it("takes a quantity within the limit without a message", () => {
    const result = quantityInputResult("4", 1, 4);
    expect(result).toEqual({ quantity: 4, capped: false });
    const html = render({ quantity: result.quantity, capped: result.capped, lines });
    expect(shownValue(html)).toBe(`value="4"`);
    expect(html).not.toContain(COPY["common.qty_capped"].en.replace("{count}", "4"));
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

  // docs/UX.md P03「购物车全部行合计已达 2,000 件：( [detail.add_to_cart] ) 禁用」：即使被调用也不加入；
  // 差 3 件时加入的件数不超过整单剩余（「数量的上限取…较小的」）。
  it("does not add past 2,000 items in the whole cart", () => {
    const full = [
      { sku: "bag-std", slug: "zip-pouch", quantity: 1000 },
      { sku: "mug-std", slug: "mug", quantity: 1000 },
    ];
    const storage = memoryStorage(full);
    expect(addSelection(storage, item, { color: "red", size: "s" }, 1, full)).toBeNull();
    expect(readCart(storage)).toEqual(full);
    const almost = [
      { sku: "bag-std", slug: "zip-pouch", quantity: 1000 },
      { sku: "mug-std", slug: "mug", quantity: 997 },
    ];
    const almostStorage = memoryStorage(almost);
    expect(addSelection(almostStorage, item, { color: "red", size: "s" }, 5, almost)).toBe("added");
    expect(readCart(almostStorage)).toEqual([...almost, { sku: "tee-red-s", slug: "crew-neck-tee", quantity: 3 }]);
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

// 活动（UX 0.20 P03）：红/S RM 39.00 → 31.20、蓝/M RM 42.00 → 33.60，命中打折 20%（规则 11）。
const SERVER_TIME = "2026-11-14T04:00:00Z";
// 服务端时间之后一小时结束：时钟替身固定为 0，倒计时文字为 countdownText(language, HOUR_MS)。
const ENDS_AT = "2026-11-14T05:00:00Z";
const HOUR_MS = 3_600_000;

function cardRule(rule_id: number, kind: string): CardPromotion {
  return { rule_id, kind, discount_mode: "percent", percent: 20, amount_sen: null, buy_quantity: 2, get_quantity: 1, get_percent: 100 };
}

const ITEM_RULE = cardRule(11, "item_discount");
const BXGY_RULE = cardRule(14, "buy_x_get_y");

function saleVariant(sku: string, options: Record<string, string>, price_sen: number, sale_price_sen: number): VariantDetail {
  return { ...variant(sku, options, price_sen), sale_price_sen };
}

const onSale = product({
  variants: [
    saleVariant("tee-red-s", { color: "red", size: "s" }, 3900, 3120),
    saleVariant("tee-blue-m", { color: "blue", size: "m" }, 4200, 3360),
  ],
  promotion: ITEM_RULE,
});

function ruleDisplay(rule_id: number, position: number, overrides: Partial<RuleDisplay>): RuleDisplay {
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

const ITEM_DISPLAY = ruleDisplay(11, 1, { discount_mode: "percent", percent: 20 });
const BXGY_DISPLAY = ruleDisplay(14, 2, { kind: "buy_x_get_y", buy_quantity: 2, get_quantity: 1, get_percent: 100 });
const SPEND_DISPLAY = ruleDisplay(12, 3, {
  kind: "spend_discount",
  tiers: [
    { threshold_sen: 15000, amount_sen: 1500, percent: null },
    { threshold_sen: 30000, amount_sen: 4000, percent: null },
  ],
});
const SHIPPING_DISPLAY = ruleDisplay(13, 4, { kind: "free_shipping", threshold_sen: 12000 });

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
      ends_at: ENDS_AT,
      coupon_combinable: true,
      points_combinable: true,
      rules: [ITEM_DISPLAY, BXGY_DISPLAY, SPEND_DISPLAY, SHIPPING_DISPLAY],
      ...overrides,
    },
    server_time: SERVER_TIME,
  };
}

const NO_SALE: PromotionResponse = { promotion: null, server_time: SERVER_TIME };

const ribbon = (html: string) => part(html, /<div class="acs-promo-ribbon [\s\S]*?(?=<div class="site-detail__options">)/);
const rulesArea = (html: string) => part(html, /<section class="acs-promo-rules">[\s\S]*?<\/section>/);
const tag = (text: string) => `<span class="acs-promo-badge acs-promo-badge--tag"><span>${escapeHtml(text)}</span></span>`;

function meta(language: Language, rule: CardPromotion): string {
  return `<div class="acs-promo-ribbon__meta"><span class="acs-promo-badges">${tag("Lunar deal")}${tag(badgeText(language, rule) ?? "")}</span></div>`;
}

function salePrices(language: Language, original: string, saleText: string): string {
  return (
    `<div class="acs-promo-ribbon__prices">` +
    `<span class="acs-price-was acs-price--l"><span class="acs-vh">${COPY["promo.a11y_original"][language]}</span>${escapeHtml(original)}</span>` +
    `<span class="acs-price-sale acs-promo-ribbon__sale"><span class="acs-vh">${COPY["promo.a11y_sale"][language]}</span>${escapeHtml(saleText)}</span>` +
    `</div>`
  );
}

const CLOCK_ICON =
  `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">` +
  `<circle cx="12" cy="13" r="8"></circle><path d="M12 9v4l3 2M9 2h6"></path></svg>`;

// promo.view_all 链接：P15 在路由表里时才有（按 saleLinkPath() 的返回值，不写死）。
function viewAllLink(language: Language): string {
  const path = saleLinkPath();
  return path === null ? "" : `<a href="${path}">${escapeHtml(COPY["promo.view_all"][language])}</a>`;
}

function promoEnd(language: Language): string {
  return `<div class="site-detail__promo-end"><span class="acs-promo-count">${CLOCK_ICON}<span>${escapeHtml(countdownText(language, HOUR_MS))}</span></span>${viewAllLink(language)}</div>`;
}

describe("price ribbon", () => {
  // UX 0.20 P03「价格区按 detail_ribbon 的变体显示所选规格的划线原价与活动单价（命中打折规则时）以及角标（规则同「1. 全局框架」的商品卡）」：
  // 选全前划线价为最低单价（不加「起」）、活动价为最低活动单价加 list.price_from，角标为角标短语与规则角标。
  it.each(LANGUAGES)("shows the lowest prices with a from-price sale before every option is chosen in %s", (language) => {
    const html = render({ item: onSale, selection: { color: "blue" }, promotion: sale(), language });
    expect(ribbon(html)).toBe(
      `<div class="acs-promo-ribbon acs-promo-ribbon--ribbon">` +
        salePrices(language, translate(language, "common.price_myr", { amount: "39.00" }), translate(language, "list.price_from", { amount: "31.20" })) +
        meta(language, ITEM_RULE) +
        `</div>`,
    );
    expect(html).not.toContain(`<span class="acs-price acs-price--l">`);
  });

  // 同上「所选规格的划线原价与活动单价」与「切换规格时随服务端数据刷新」：选全后为所选 SKU 的原价与活动单价，都不加「起」。
  it.each(LANGUAGES)("shows the chosen SKU's original and sale price in %s", (language) => {
    const blue = ribbon(render({ item: onSale, selection: { color: "blue", size: "m" }, promotion: sale(), language }));
    expect(blue).toContain(salePrices(language, translate(language, "common.price_myr", { amount: "42.00" }), translate(language, "common.price_myr", { amount: "33.60" })));
    const red = ribbon(render({ item: onSale, selection: { color: "red", size: "s" }, promotion: sale(), language }));
    expect(red).toContain(salePrices(language, translate(language, "common.price_myr", { amount: "39.00" }), translate(language, "common.price_myr", { amount: "31.20" })));
  });

  // UX 0.20「商品卡价格与角标」（P03 规则同它）：「只有一个规格时用 [common.price_myr]」——单规格商品的活动价不加「起」。
  it.each(LANGUAGES)("shows a plain sale price for a single SKU in %s", (language) => {
    const single = product({ variants: [saleVariant("tee-red-s", { color: "red", size: "s" }, 3900, 3120)], promotion: ITEM_RULE });
    const html = ribbon(render({ item: single, promotion: sale(), language }));
    expect(html).toContain(salePrices(language, translate(language, "common.price_myr", { amount: "39.00" }), translate(language, "common.price_myr", { amount: "31.20" })));
  });

  // UX 0.20「1. 全局框架」：「detail_ribbon 价格飘带与规则区（P03）……每位的变体由视觉稿给出」——panel 变体用 --panel，其他取值按 ribbon。
  it.each(["panel", "ribbon", "unknown"])("uses the %s variant of the ribbon", (variant) => {
    const html = render({ item: onSale, promotion: sale({ detail_ribbon: variant }) });
    expect(ribbon(html)).toMatch(new RegExp(`^<div class="acs-promo-ribbon acs-promo-ribbon--${variant === "panel" ? "panel" : "ribbon"}">`));
  });

  // UX 0.20「商品卡价格与角标」：「买 X 享 Y 为 [promo.badge_bxgy_free]……价格不变」——飘带里是现有的价格文字（不划线）与角标。
  it.each(LANGUAGES)("keeps the plain price and adds the badges for buy X get Y in %s", (language) => {
    const html = render({ item: product({ promotion: BXGY_RULE }), promotion: sale(), language });
    expect(ribbon(html)).toBe(
      `<div class="acs-promo-ribbon acs-promo-ribbon--ribbon">` +
        `<div class="acs-promo-ribbon__prices"><span class="acs-price acs-price--l">${escapeHtml(COPY["list.price_from"][language].replace("{amount}", "39.00"))}</span></div>` +
        meta(language, BXGY_RULE) +
        `</div>`,
    );
    expect(html).not.toContain("acs-price-was");
  });

  // UX 0.20「商品卡价格与角标」：「活动的角标短语（……可为空）显示在规则角标之前」——短语为空时只放规则角标。
  it("leaves out an empty badge phrase", () => {
    const html = ribbon(render({ item: onSale, promotion: sale({ badge: null }) }));
    expect(html).toContain(`<div class="acs-promo-ribbon__meta"><span class="acs-promo-badges">${tag("−20%")}</span></div>`);
  });

  // UX 0.20「节日促销活动」：「没有生效活动时各页与 0.19 完全相同」，以及 HANDOFF 0.97「只在……商品的 promotion.rule_id 在该活动的 rules 里时显示」：
  // 没有活动、没有命中或规则不在当前活动里时，整页与不带活动的渲染逐字相同（活动没有整单级规则，规则区也不出现）。
  it.each(LANGUAGES)("is exactly the page without a sale when nothing applies in %s", (language) => {
    for (const selection of [{}, { color: "blue", size: "m" }]) {
      const plain = render({ item: onSale, selection, language });
      expect(render({ item: onSale, selection, promotion: NO_SALE, language })).toBe(plain);
      expect(render({ item: onSale, selection, promotion: sale({ rules: [BXGY_DISPLAY] }), language })).toBe(plain);
      expect(render({ item: product({ ...onSale, promotion: null }), selection, promotion: sale({ rules: [ITEM_DISPLAY] }), language })).toBe(
        render({ item: product({ ...onSale, promotion: null }), selection, language }),
      );
      expect(plain).not.toContain("acs-promo");
    }
  });

  // 同上：只命中整单级规则（商品没有命中）时价格区照旧——去掉规则区后与不带活动的渲染逐字相同。
  it("keeps the price area unchanged when only order-level rules apply", () => {
    const item = product({ ...onSale, promotion: null });
    const html = render({ item, promotion: sale() });
    expect(html).not.toContain("acs-promo-ribbon");
    expect(html.replace(rulesArea(html), "")).toBe(render({ item }));
  });
});

describe("sale rules area", () => {
  // UX 0.20 P03 线框「★ [detail.demo_hint]」之后「活动规则区：[promo.detail_title] <活动标题> · <本商品适用的商品级规则说明> · <整单级规则说明>（满额减各档、免运费）
  // · [promo.coupon_not_combinable]? · [promo.points_not_combinable]? [promo.ends_in] ⏱ ([promo.view_all]) → P15」与说明「先列本商品命中的商品级规则说明……再列满额减或满额折的各档与免运费」。
  it.each(LANGUAGES)("lists the product's rule, then the order-level rules, then the notes and the countdown in %s", (language) => {
    const html = render({ item: onSale, promotion: sale({ coupon_combinable: false, points_combinable: false }), language });
    const items = [
      translate(language, "promo.rule_item_percent", { percent: 20 }),
      translate(language, "promo.rule_tier_amount", { threshold: "150.00", amount: "15.00" }),
      translate(language, "promo.rule_tier_amount", { threshold: "300.00", amount: "40.00" }),
      translate(language, "promo.rule_free_shipping", { threshold: "120.00" }),
    ];
    expect(rulesArea(html)).toBe(
      `<section class="acs-promo-rules">` +
        `<h2 class="acs-display-s site-detail__promo-title">${escapeHtml(COPY["promo.detail_title"][language])}` +
        `<svg width="4" height="4" viewBox="0 0 4 4" aria-hidden="true"><circle cx="2" cy="2" r="2" fill="currentColor"></circle></svg>` +
        `<span>Lunar New Year Sale</span></h2>` +
        `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` +
        `<p class="acs-body-s">${escapeHtml(COPY["promo.coupon_not_combinable"][language])}</p>` +
        `<p class="acs-body-s">${escapeHtml(COPY["promo.points_not_combinable"][language])}</p>` +
        promoEnd(language) +
        `</section>`,
    );
    const hint = html.indexOf(escapeHtml(COPY["detail.demo_hint"][language]));
    expect(hint).toBeGreaterThanOrEqual(0);
    expect(html.indexOf(`<section class="acs-promo-rules">`)).toBeGreaterThan(hint);
    expect(html.indexOf(`<section class="acs-promo-rules">`)).toBeLessThan(html.indexOf(`site-detail__description`));
  });

  // UX 0.20 P03「活动关闭了与优惠券或积分同用时，分别显示」——只关闭其中一个时只显示那一条，都可同用时都不显示。
  it("shows only the note for what cannot be combined", () => {
    const coupon = escapeHtml(COPY["promo.coupon_not_combinable"].en);
    const points = escapeHtml(COPY["promo.points_not_combinable"].en);
    const pointsOnly = rulesArea(render({ item: onSale, promotion: sale({ points_combinable: false }) }));
    expect(pointsOnly).toContain(points);
    expect(pointsOnly).not.toContain(coupon);
    const couponOnly = rulesArea(render({ item: onSale, promotion: sale({ coupon_combinable: false }) }));
    expect(couponOnly).toContain(coupon);
    expect(couponOnly).not.toContain(points);
    const both = rulesArea(render({ item: onSale, promotion: sale() }));
    expect(both).not.toContain(coupon);
    expect(both).not.toContain(points);
  });

  // UX 0.20 P03「其下的活动规则区在活动有整单级规则或本商品命中商品级规则时显示……先列本商品命中的商品级规则说明（没有命中时不列）」：
  // 没有命中的商品只列满额各档与免运费。
  it.each(LANGUAGES)("shows the area with only the order-level rules for a product without a hit in %s", (language) => {
    const area = rulesArea(render({ item: product(), promotion: sale(), language }));
    expect([...area.matchAll(/<li>([^<]*)<\/li>/g)].map((m) => m[1])).toEqual(
      [
        translate(language, "promo.rule_tier_amount", { threshold: "150.00", amount: "15.00" }),
        translate(language, "promo.rule_tier_amount", { threshold: "300.00", amount: "40.00" }),
        translate(language, "promo.rule_free_shipping", { threshold: "120.00" }),
      ].map(escapeHtml),
    );
  });

  // 同上：只有本商品命中的商品级规则、没有整单级规则时只列它。
  it("shows the area with only the product's rule", () => {
    const area = rulesArea(render({ item: onSale, promotion: sale({ rules: [ITEM_DISPLAY] }) }));
    expect([...area.matchAll(/<li>([^<]*)<\/li>/g)].map((m) => m[1])).toEqual([translate("en", "promo.rule_item_percent", { percent: 20 })]);
  });

  // UX 0.20 P03「（两者都没有时整区不显示）」：只有别的商品的规则、本商品没有命中时不渲染规则区。
  it("has no area when neither kind of rule applies", () => {
    expect(render({ item: product(), promotion: sale({ rules: [BXGY_DISPLAY] }) })).not.toContain("acs-promo-rules");
  });

  // UX 0.20 P03 线框「[promo.detail_title]  <活动标题>」：活动标题为空时只放 promo.detail_title；不计时（ends_at 为 null）时只剩 promo.view_all，
  // 也没有 P15 时不放末行。
  it("leaves out an empty title and an absent countdown", () => {
    const area = rulesArea(render({ item: onSale, promotion: sale({ title: null, ends_at: null }) }));
    expect(area).toContain(`<h2 class="acs-display-s site-detail__promo-title">${COPY["promo.detail_title"].en}</h2>`);
    expect(area).not.toContain("acs-promo-count");
    const link = viewAllLink("en");
    if (link === "") {
      expect(area).not.toContain("site-detail__promo-end");
    } else {
      expect(area).toContain(`<div class="site-detail__promo-end">${link}</div>`);
    }
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
  "detail.cart_units_full",
  "detail.description",
  "detail.english_only",
  "detail.a11y_image",
  "detail.demo_hint",
  "common.a11y_qty_decrease",
  "common.a11y_qty_increase",
  "common.qty_capped",
];

describe("dictionary", () => {
  // SHOP-TASK-017 验收第 1 条「页面内容…文案以 docs/UX-COPY.md 0.5 为准」：P03 的文案键都在字典里且三列非空（与文档的逐字比较由 i18n/copy.test.ts 覆盖）。
  it.each(DETAIL_KEYS)("has %s in all three languages", (key) => {
    for (const language of LANGUAGES) {
      expect(COPY[key][language]).not.toBe("");
    }
  });
});
