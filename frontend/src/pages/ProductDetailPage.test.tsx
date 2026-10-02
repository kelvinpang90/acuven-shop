import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ProductDetail, ProductVariant, RemoteItem } from "../api/catalog";
import App from "../App";
import type { CartLine } from "../cart";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { leaveIfMissing, ProductDetailPending, ProductDetailView } from "./ProductDetailPage";
import type { Notice } from "./ProductDetailPage";
import type { Selection } from "./productOptions";

function text(value: string, english_fallback = false) {
  return { text: value, english_fallback };
}

function variant(sku: string, colour: string, size: string, price_sen: number, available_stock = 20): ProductVariant {
  return { sku, options: { colour, size }, price_sen, available_stock };
}

// 颜色 Black/White/Navy，尺寸 S/M/L；启用 SKU：black-s、black-m、white-m、navy-l。
function tee(overrides: Partial<ProductDetail> = {}): ProductDetail {
  return {
    slug: "crew-neck-tee",
    name: text("Crew Neck Tee"),
    description: text("A soft cotton tee."),
    category: { slug: "apparel", name: text("Apparel") },
    images: ["/img/tee-1.svg", "/img/tee-2.svg", "/img/tee-3.svg"],
    options: [
      {
        code: "colour",
        name: text("Colour"),
        values: [
          { code: "black", name: text("Black") },
          { code: "white", name: text("White") },
          { code: "navy", name: text("Navy") },
        ],
      },
      {
        code: "size",
        name: text("Size"),
        values: [
          { code: "s", name: text("S") },
          { code: "m", name: text("M") },
          { code: "l", name: text("L") },
        ],
      },
    ],
    variants: [
      variant("TEE-BLK-S", "black", "s", 3900),
      variant("TEE-BLK-M", "black", "m", 4200, 7),
      variant("TEE-WHT-M", "white", "m", 3500),
      variant("TEE-NVY-L", "navy", "l", 4500),
    ],
    max_per_order: 5,
    ...overrides,
  };
}

const PRODUCT_TEXTS = ["Crew Neck Tee", "A soft cotton tee.", "Apparel", "Colour", "Black", "White", "Navy", "Size", "S", "M", "L"];

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  product?: ProductDetail;
  cart?: CartLine[];
  selection?: Selection;
  quantity?: number;
  notice?: Notice;
  language?: Language;
}

function render({
  product = tee(),
  cart = [],
  selection = {},
  quantity = 1,
  notice = null,
  language = "en",
}: RenderOptions = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={`/products/${product.slug}`}>
        <ProductDetailView
          product={product}
          cart={cart}
          selection={selection}
          quantity={quantity}
          notice={notice}
          onToggle={() => undefined}
          onQuantity={() => undefined}
          onAdd={() => undefined}
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

function unescapeHtml(value: string): string {
  return value
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

// 访客能看到或听到的全部文字：文本节点与读屏标签、提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/g)].map((m) =>
    unescapeHtml(m[1] ?? ""),
  );
  return [...textNodes(html), ...attributes].filter((value) => value !== "");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// 字典里某一列的模板：{变量} 处可以是任意值。
function templatePattern(template: string): RegExp {
  return new RegExp(`^${template.split(/\{\w+\}/).map(escapeRegExp).join(".+")}$`);
}

const addButton = (html: string) => part(html, /<button class="acs-btn acs-btn--primary[^"]*"[^>]*>[^<]*<\/button>/);
const buyBar = (html: string) => part(html, /<div class="acs site-detail__buy">[\s\S]*?<\/button><\/div>/);
const increase = (html: string, language: Language = "en") =>
  part(html, new RegExp(`<button type="button" aria-label="${COPY["common.a11y_qty_increase"][language]}"[^>]*>`));
const decrease = (html: string, language: Language = "en") =>
  part(html, new RegExp(`<button type="button" aria-label="${COPY["common.a11y_qty_decrease"][language]}"[^>]*>`));

function chip(html: string, label: string): string {
  return part(html, new RegExp(`<button class="acs-chip" type="button"[^>]*>${label}</button>`));
}

describe("product detail content", () => {
  // UX P03 线框「面包屑：[common.nav_shop] / <分类> / <名称>」与 SHOP-TASK-017 验收第 2 条「分类（链到按该分类筛选的列表）」。
  it.each(LANGUAGES)("shows the breadcrumb of shop, category and product name in %s", (language) => {
    const crumbs = part(render({ language }), /<nav class="[^"]*site-detail__crumbs">[\s\S]*?<\/nav>/);
    expect(crumbs).toContain(`<a href="/products">${COPY["common.nav_shop"][language]}</a>`);
    expect(crumbs).toContain(`<a href="/products?category=apparel">Apparel</a>`);
    expect(crumbs).toContain(`<span aria-current="page">Crew Neck Tee</span>`);
  });

  // UX P03 说明与 Q8：当前语言回退英文时在名称旁显示 detail.english_only；文字本身标为英文。
  it.each(LANGUAGES)("labels an English fallback name with detail.english_only in %s", (language) => {
    const html = render({ product: tee({ name: text("Crew Neck Tee", true) }), language });
    expect(html).toContain(
      `<h1 class="acs-display-l" lang="en">Crew Neck Tee</h1><span class="acs-tag acs-tag--outline">${COPY["detail.english_only"][language]}</span>`,
    );
  });

  // 同一条：商品描述回退英文也是「当前语言缺少商品文案」。
  it("labels an English fallback description too", () => {
    const html = render({ product: tee({ description: text("A soft cotton tee.", true) }), language: "ms" });
    expect(html).toContain(COPY["detail.english_only"].ms);
    expect(html).toContain(`<p lang="en">A soft cotton tee.</p>`);
  });

  // 同一条的反面：商品文案都有当前语言时不显示该标签。
  it("has no English-only label when the copy is in the current language", () => {
    expect(render({ language: "zh" })).not.toContain(COPY["detail.english_only"].zh);
  });

  // UX P03 说明：桌面缩略图可点选切换主图，每张图的读屏标签用 detail.a11y_image。
  it.each(LANGUAGES)("labels the main image and every thumbnail with detail.a11y_image in %s", (language) => {
    const html = render({ language });
    const gallery = part(html, /<div class="site-desktop-only site-detail__gallery">[\s\S]*?<\/div><\/div>/);
    const label = (n: number) => COPY["detail.a11y_image"][language].replace("{n}", String(n)).replace("{count}", "3");
    expect(gallery).toContain(`<img class="site-img" src="/img/tee-1.svg" alt="${label(1)}"/>`);
    const thumbs = [...gallery.matchAll(/<button class="acs-chip site-detail__thumb" type="button" aria-pressed="(true|false)" aria-label="([^"]*)">/g)];
    expect(thumbs.map((m) => [m[1], m[2]])).toEqual([
      ["true", label(1)],
      ["false", label(2)],
      ["false", label(3)],
    ]);
  });

  // UX P03 手机「主图，可左右滑动」：每张图一块，读屏标签同为 detail.a11y_image。
  it("lays every image out in the phone slider with its label", () => {
    const slides = part(render(), /<div class="site-phone-only site-detail__slides">[\s\S]*?<\/div>/);
    expect([...slides.matchAll(/src="([^"]*)" alt="([^"]*)"/g)].map((m) => [m[1], m[2]])).toEqual([
      ["/img/tee-1.svg", "Image 1 of 3"],
      ["/img/tee-2.svg", "Image 2 of 3"],
      ["/img/tee-3.svg", "Image 3 of 3"],
    ]);
  });

  // 派生实现约束（实现选择）：守住 UX P03 的 [主图] 区块——没有图片时沿用商品卡的占位形状，不渲染空 img，也没有缩略图。
  it("shows the placeholder shape without images", () => {
    const html = render({ product: tee({ images: [] }) });
    expect(html).not.toContain("<img");
    expect(html).not.toContain("site-detail__thumb");
  });

  // UX P03 手机「[detail.description] ▾」可折叠；桌面是标题加描述。
  it.each(LANGUAGES)("shows the description on desktop and in a collapsible block on phones in %s", (language) => {
    const html = render({ language });
    expect(html).toContain(
      `<section class="site-desktop-only site-detail__description"><h2 class="acs-display-s">${COPY["detail.description"][language]}</h2><p>A soft cotton tee.</p></section>`,
    );
    expect(html).toContain(
      `<details class="site-phone-only site-detail__more"><summary class="acs-field__label">${COPY["detail.description"][language]}</summary><p>A soft cotton tee.</p></details>`,
    );
  });

  // UX P03「演示提示」：★ detail.demo_hint，位于加入购物车之后。
  it.each(LANGUAGES)("shows the demo hint after the add button in %s", (language) => {
    const html = render({ language });
    expect(html).toContain(COPY["detail.demo_hint"][language]);
    expect(html.indexOf(COPY["detail.demo_hint"][language])).toBeGreaterThan(html.indexOf(COPY["detail.add_to_cart"][language]));
  });

  // UX P03 手机「底部固定：( [detail.add_to_cart] )」：按钮与其提示在单独一栏里，由 site.css 在 767px 以下固定在底部。
  it("puts the add button and its notices in the buy bar", () => {
    const bar = buyBar(render({ notice: "added" }));
    expect(bar).toContain(COPY["detail.added"].en);
    expect(bar).toContain(`>${COPY["detail.add_to_cart"].en}</button>`);
  });

  // SHOP-TASK-017 验收第 6 条「页面文字全部来自字典」：除商品数据外，所有文字都是字典当前语言那一列（含变量替换），没有行内样式。
  it.each(LANGUAGES)("shows only dictionary text besides the product data in %s", (language) => {
    const patterns = Object.values(COPY).map((entry) => templatePattern(entry[language]));
    const variants: RenderOptions[] = [
      { language },
      { language, selection: { colour: "black", size: "m" }, notice: "added" },
      { language, notice: "select_all_options" },
      { language, cart: [{ sku: "TEE-BLK-S", slug: "crew-neck-tee", quantity: 5 }] },
      { language, product: tee({ name: text("Crew Neck Tee", true) }) },
    ];
    for (const options of variants) {
      const html = render(options);
      expect(html).not.toContain("style=");
      for (const value of visibleTexts(html)) {
        const allowed = PRODUCT_TEXTS.includes(value) || /^\d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(allowed, value).toBe(true);
      }
    }
  });
});

describe("options and price", () => {
  // SHOP-TASK-017 验收第 4 条「初始不预选」与 COMPONENTS「Options」：没有按下的值；选全前价格按起价规则（多 SKU 一律 list.price_from）。
  it.each(LANGUAGES)("starts with nothing chosen and the from-price in %s", (language) => {
    const html = render({ language });
    expect(html).not.toContain(`aria-pressed="true">`);
    expect(html).toContain(
      `<span class="acs-price acs-price--l">${COPY["list.price_from"][language].replace("{amount}", "35.00")}</span>`,
    );
    expect(html).not.toContain(COPY["detail.stock_left"][language].split("{count}")[1] ?? "");
  });

  // UX P03 M1 与 SHOP-TASK-017 验收第 4 条「选全后显示所选 SKU 的单价与 detail.stock_left（当日可用库存）」。
  it.each(LANGUAGES)("shows the chosen SKU's price and stock left today in %s", (language) => {
    const html = render({ language, selection: { colour: "black", size: "m" } });
    expect(html).toContain(`<span class="acs-price acs-price--l">RM 42.00</span>`);
    expect(html).toContain(`<span class="acs-caption">${COPY["detail.stock_left"][language].replace("{count}", "7")}</span>`);
    expect(chip(html, "Black")).toContain(`aria-pressed="true"`);
    expect(chip(html, "M")).toContain(`aria-pressed="true"`);
  });

  // SHOP-TASK-017 验收第 4 条「只有一个启用 SKU 的商品直接选定」由页面给出初始选择；视图按选择显示单价，不带「起」。
  it("shows a plain price for a single SKU", () => {
    const html = render({ product: tee({ variants: [variant("TEE-BLK-S", "black", "s", 3900)] }), selection: { colour: "black", size: "s" } });
    expect(html).toContain(`<span class="acs-price acs-price--l">RM 39.00</span>`);
  });

  // COMPONENTS「Options」与 SHOP-TASK-017 验收第 4 条：与已选的其他规格值组不成任何启用 SKU 的值禁用。
  it("disables values that make no active SKU with the other chosen values", () => {
    const html = render({ selection: { colour: "navy" } });
    expect(chip(html, "S")).toContain("disabled");
    expect(chip(html, "M")).toContain("disabled");
    expect(chip(html, "L")).not.toContain("disabled");
    expect(chip(html, "Black")).not.toContain("disabled");
    expect(chip(html, "Navy")).toContain(`aria-pressed="true"`);
  });

  // UX P03 线框「[detail.options]」下每个规格名一组，规格名与值来自接口。
  it.each(LANGUAGES)("groups the values under each option name in %s", (language) => {
    const html = render({ language });
    expect(html).toContain(`<span class="acs-field__label">${COPY["detail.options"][language]}</span>`);
    expect(html.match(/role="group" aria-labelledby=/g)).toHaveLength(3);
    expect(html).toMatch(/<span id="[^"]+" class="acs-body-s acs-muted">Colour<\/span>/);
  });

  // UX P03「未选全规格点加入：[detail.select_all_options]」。
  it.each(LANGUAGES)("asks to choose every option in %s", (language) => {
    const html = render({ language, notice: "select_all_options" });
    expect(buyBar(html)).toContain(`role="alert">${COPY["detail.select_all_options"][language]}</p>`);
    expect(addButton(html)).not.toContain("disabled");
  });
});

describe("quantity, per-order limit and cart size", () => {
  // UX P03 0.6：显示 detail.max_per_order（该商品的限购件数）；数量从 1 起，(-) 在最小值时禁用（COMPONENTS「Options」）。
  it.each(LANGUAGES)("shows the per-order limit and starts the quantity at 1 in %s", (language) => {
    const html = render({ language });
    expect(html).toContain(`<span class="acs-caption">${COPY["detail.max_per_order"][language].replace("{count}", "5")}</span>`);
    expect(html).toContain(`<output>1</output>`);
    expect(html).toContain(`aria-label="${COPY["common.a11y_qty_decrease"][language]}" disabled=""`);
    expect(html).toContain(`aria-label="${COPY["common.a11y_qty_increase"][language]}"`);
  });

  // UX P03 0.6「数量 (+) 的上限是限购件数减去购物车中该商品已有的件数」：同一商品不同 SKU 合计，其他商品不算。
  it("stops the quantity at the limit minus the product's other SKUs in the cart", () => {
    const cart = [
      { sku: "TEE-BLK-S", slug: "crew-neck-tee", quantity: 2 },
      { sku: "TEE-WHT-M", slug: "crew-neck-tee", quantity: 1 },
      { sku: "MUG", slug: "mug", quantity: 9 },
    ];
    const atCap = render({ cart, quantity: 2 });
    expect(atCap).toContain(`<output>2</output>`);
    expect(increase(atCap)).toContain("disabled");
    const belowCap = render({ cart, quantity: 1 });
    expect(increase(belowCap)).not.toContain("disabled");
    // 购物车变化后，先前选的件数按新的剩余件数收窄。
    expect(render({ cart, quantity: 4 })).toContain(`<output>2</output>`);
  });

  // UX P03 0.6「该商品已达限购时加入按钮禁用并显示 [detail.limit_reached]」：同一商品各 SKU 合计达到限购。
  it.each(LANGUAGES)("disables adding and says so when the limit is reached in %s", (language) => {
    const cart = [
      { sku: "TEE-BLK-S", slug: "crew-neck-tee", quantity: 3 },
      { sku: "TEE-NVY-L", slug: "crew-neck-tee", quantity: 2 },
    ];
    const html = render({ cart, language, selection: { colour: "black", size: "m" } });
    expect(addButton(html)).toMatch(/disabled="" aria-describedby="[^"]+"/);
    expect(buyBar(html)).toContain(`${COPY["detail.limit_reached"][language].replace("{count}", "5")}</p>`);
    expect(increase(html, language)).toContain("disabled");
    expect(decrease(html, language)).toContain("disabled");
    expect(html).not.toContain(COPY["detail.cart_full"][language].split("{count}")[0] ?? "");
  });

  // 同一条的反面：差一件到限购时仍可加入，不显示提示。
  it("keeps adding enabled one short of the limit", () => {
    const html = render({ cart: [{ sku: "TEE-BLK-S", slug: "crew-neck-tee", quantity: 4 }] });
    expect(addButton(html)).not.toContain("disabled");
    expect(html).not.toContain(COPY["detail.limit_reached"].en.split("{count}")[0] ?? "");
  });

  // UX P03 0.6「购物车已有 20 行且所选规格不在其中时加入按钮禁用并显示 [detail.cart_full]」（{count} 为 20）。
  it.each(LANGUAGES)("disables adding a new SKU to a full cart and says so in %s", (language) => {
    const full = Array.from({ length: 20 }, (_, index) => ({ sku: `OTHER-${String(index)}`, slug: "other", quantity: 1 }));
    const html = render({ cart: full, language, selection: { colour: "black", size: "m" } });
    expect(addButton(html)).toContain(`disabled=""`);
    expect(buyBar(html)).toContain(`${COPY["detail.cart_full"][language].replace("{count}", "20")}</p>`);
  });

  // 同一条「且所选规格不在其中」：所选 SKU 已在 20 行里时仍可加入（合并件数）。
  it("keeps adding enabled when the chosen SKU is already one of the 20 lines", () => {
    const full = [
      ...Array.from({ length: 19 }, (_, index) => ({ sku: `OTHER-${String(index)}`, slug: "other", quantity: 1 })),
      { sku: "TEE-BLK-M", slug: "crew-neck-tee", quantity: 1 },
    ];
    const html = render({ cart: full, selection: { colour: "black", size: "m" } });
    expect(addButton(html)).not.toContain("disabled");
    expect(html).not.toContain(COPY["detail.cart_full"].en.split("{count}")[0] ?? "");
  });

  // UX P03「加入后提示条：[detail.added] ( [detail.view_cart] )」与 SHOP-TASK-017 验收第 6 条：
  // 购物车页 P04 还不在路由表里，detail.view_cart 按路由规则不渲染，也没有指向 /cart 的链接。
  it.each(LANGUAGES)("confirms the item was added without a link to the missing cart page in %s", (language) => {
    const html = render({ language, notice: "added", selection: { colour: "black", size: "m" } });
    expect(buyBar(html)).toContain(`role="status">`);
    expect(buyBar(html)).toContain(COPY["detail.added"][language]);
    expect(html).not.toContain(COPY["detail.view_cart"][language]);
    expect(html).not.toContain(`href="/cart`);
  });
});

describe("loading, failure and missing products", () => {
  // SHOP-TASK-017 验收第 2 条「商品不存在或未发布（接口 404）时替换为商品列表页」：只在 404 时换成 /products。
  it("replaces the page with the product list on a 404", () => {
    const replace = vi.fn();
    leaveIfMissing({ status: "not_found" }, replace);
    expect(replace).toHaveBeenCalledWith("/products");
    const others: RemoteItem<ProductDetail>[] = [{ status: "loading" }, { status: "error" }, { status: "ready", data: tee() }];
    for (const remote of others) {
      const other = vi.fn();
      leaveIfMissing(remote, other);
      expect(other).not.toHaveBeenCalled();
    }
  });

  // 同一条「不另显示文字」：等待数据与即将换页时页面主体是空的。
  it("shows no text while loading or leaving", () => {
    const html = renderToStaticMarkup(
      <LanguageProvider storage={null}>
        <ProductDetailPending failed={false} />
      </LanguageProvider>,
    );
    expect(html).toBe(`<main class="site-detail" aria-busy="true"></main>`);
  });

  // SHOP-TASK-015 验收第 7 条沿用：其他请求失败时在页面位置显示 common.error_retry。
  it.each(LANGUAGES)("shows the retry message when the request fails in %s", (language) => {
    const html = renderToStaticMarkup(
      <LanguageProvider storage={storage(language)}>
        <ProductDetailPending failed />
      </LanguageProvider>,
    );
    expect(textNodes(html)).toEqual([COPY["common.error_retry"][language]]);
  });

  // SHOP-TASK-017 验收第 2 条「路由 /products/<slug> 用商品详情接口取数」：路由给出详情页（服务端渲染不发请求，主体为空），不是首页或列表。
  it("renders the detail page for /products/<slug> inside the frame", () => {
    const html = renderToStaticMarkup(<App initialPath="/products/crew-neck-tee" storage={storage("en")} />);
    expect(html).toContain(`<main class="site-detail" aria-busy="true"></main>`);
    expect(html).not.toContain(COPY["home.demo_hint"].en);
    expect(html).not.toContain(COPY["list.title"].en);
    expect(html).toContain(COPY["common.demo_banner"].en);
  });
});
