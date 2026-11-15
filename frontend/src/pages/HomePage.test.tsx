import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { categoriesUrl, featuredBySlugsUrl, featuredProductsUrl } from "../api/catalog";
import type { CategoryListItem, ProductPage, ProductSummary, Remote } from "../api/catalog";
import type { PromotionDisplay, PromotionResponse } from "../api/promotion";
import { saleLinkPath } from "../components/PromoParts";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PromotionProvider } from "../promotion";
import { countdownText } from "../promoText";
import { RouterProvider } from "../router";
import { DEFAULT_STORE_DESIGN, HOME_BLOCKS, STORE_DESIGN_STORAGE_KEY, StoreDesignProvider } from "../storeDesign";
import type { HomeBlock, HomeBlockSetting, StoreDesignStorage } from "../storeDesign";
import HomePage, {
  categoriesRequest,
  featuredRemote,
  featuredSlugsToShow,
  featuredSource,
  HomeView,
  newestRequest,
  orderedFeatured,
  pickedRequest,
} from "./HomePage";

const HEADINGS: Readonly<Record<HomeBlock, CopyKey>> = {
  hero: "home.hero_title",
  how: "home.how_title",
  categories: "home.categories",
  featured: "home.featured",
};

function item(slug: string, image: string | null): ProductSummary {
  return {
    slug,
    name: { text: `Name ${slug}`, english_fallback: false },
    category: { slug: "apparel", name: { text: "Apparel", english_fallback: false } },
    image,
    min_price_sen: 1500,
    has_multiple_variants: false,
    sold_out_today: false,
    promotion: null,
    min_sale_price_sen: null,
  };
}

const featuredPage: ProductPage = {
  total: 30,
  page: 1,
  page_size: 4,
  items: [item("a", "/img/a.svg"), item("b", null), item("c", "/img/c.svg"), item("d", "/img/d.svg")],
};

const categoryList: CategoryListItem[] = [
  { slug: "apparel", name: { text: "Apparel", english_fallback: false }, image: "/demo-images/apparel.svg" },
  { slug: "bags", name: { text: "Bags", english_fallback: false }, image: null },
];

const loading: Remote<never> = { status: "loading" };
const failed: Remote<never> = { status: "error" };
const notFound: Remote<never> = { status: "not_found" };

interface RenderOptions {
  blocks?: readonly HomeBlockSetting[];
  categories?: Remote<CategoryListItem[]>;
  featured?: Remote<ProductPage>;
}

// SHOP-TASK-054：storeDesign.ts 不再导出固定的 storeDesign，默认区块改取 DEFAULT_STORE_DESIGN（同一份默认值，意图不变）。
function render({ blocks = DEFAULT_STORE_DESIGN.homeBlocks, categories = loading, featured = loading }: RenderOptions = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={null}>
      <RouterProvider initialPath="/">
        <HomeView blocks={blocks} categories={categories} featured={featured} />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function shown(...visible: HomeBlock[]): HomeBlockSetting[] {
  return HOME_BLOCKS.map((block) => ({ block, visible: visible.includes(block) }));
}

function sectionOf(html: string, heading: CopyKey): string {
  const start = html.lastIndexOf("<section", html.indexOf(COPY[heading].en));
  const end = html.indexOf("</section>", start);
  return html.slice(start, end);
}

function textNodes(html: string): string[] {
  return html
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((text) => text.trim())
    .filter((text) => text !== "");
}

describe("home block settings", () => {
  // SHOP-TASK-015 验收第 2 条「主视觉、演示怎么玩、按分类浏览、精选商品，全部显示」（UX P01 A08 的默认顺序）。
  // SHOP-TASK-054 只把取值从 storeDesign.homeBlocks 改为默认值 DEFAULT_STORE_DESIGN.homeBlocks（固定设置已改为按店铺装修读取）。
  it("shows the four blocks in the UX order by default", () => {
    expect(DEFAULT_STORE_DESIGN.homeBlocks).toEqual(HOME_BLOCKS.map((block) => ({ block, visible: true })));
  });

  // UX P01 说明：四个区块的顺序按 A08 保存的设置。
  it("renders the blocks in the order of the settings", () => {
    const order: HomeBlock[] = ["featured", "how", "hero", "categories"];
    const html = render({ blocks: order.map((block) => ({ block, visible: true })) });
    const positions = order.map((block) => html.indexOf(COPY[HEADINGS[block]].en));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // UX P01 说明：隐藏的区块整块不渲染。
  it("does not render hidden blocks at all", () => {
    const html = render({ blocks: shown("how", "featured"), categories: { status: "ready", data: categoryList } });
    expect(html).not.toContain(COPY["home.hero_title"].en);
    expect(html).not.toContain(COPY["home.hero_cta"].en);
    expect(html).not.toContain(COPY["home.categories"].en);
    expect(html).not.toContain("Apparel");
    expect(html).toContain(COPY["home.how_title"].en);
    expect(html).toContain(COPY["home.featured"].en);
    expect(html.match(/<section\b/g)).toHaveLength(2);
  });

  // UX P01 说明：四个都隐藏时只剩 ★ 提示（演示横幅、页头、页脚由框架给出）。
  it("leaves only the demo hint when every block is hidden", () => {
    const html = render({ blocks: shown() });
    expect(html).toMatch(/^<main class="site-home"><p class="acs-hint">[\s\S]*<\/p><\/main>$/);
    expect(textNodes(html)).toEqual([COPY["home.demo_hint"].en]);
  });

  // UX P01：★ home.demo_hint 在所有区块之前，不属于任何区块，不随区块隐藏。
  // SHOP-TASK-054 只把默认区块的来源改为 DEFAULT_STORE_DESIGN（原因同上），断言不变。
  it("keeps the demo hint before every block whatever the order", () => {
    const defaults = DEFAULT_STORE_DESIGN.homeBlocks;
    for (const blocks of [defaults, shown("featured"), [...defaults].reverse()]) {
      const html = render({ blocks });
      const hint = html.indexOf(COPY["home.demo_hint"].en);
      expect(hint).toBeGreaterThan(0);
      expect(html.indexOf("<section")).toBeGreaterThan(hint);
    }
  });
});

describe("hero block", () => {
  // UX P01：主视觉的 home.hero_cta 链到商品列表。
  it("links the call to action to the product list", () => {
    const hero = sectionOf(render(), "home.hero_title");
    expect(hero).toContain(`href="/products">${COPY["home.hero_cta"].en}</a>`);
    expect(hero).toContain(COPY["home.hero_body"].en);
  });

  // UX P01「三个图块是装饰性的示例商品图，不是链接」与 SHOP-TASK-015 验收第 3 条「三个装饰图块取精选商品的前三张图片，不是链接、读屏忽略」。
  // 派生实现约束（实现选择）：守住同一条的「前三张」——按前三件商品的位置取图，无图的保留位置显示占位形状，不补取第四件的图片。
  it("shows the first three featured products' images as decoration", () => {
    const hero = sectionOf(render({ featured: { status: "ready", data: featuredPage } }), "home.hero_title");
    const tiles = /<div class="site-hero__tiles" aria-hidden="true">([\s\S]*)<\/div>/.exec(hero)?.[1] ?? "";
    expect(tiles).not.toMatch(/<a\b/);
    expect([...tiles.matchAll(/<img[^>]*src="([^"]*)"[^>]*alt=""/g)].map((m) => m[1])).toEqual([
      "/img/a.svg",
      "/img/c.svg",
    ]);
    expect(tiles).not.toContain("/img/d.svg");
    expect(tiles.match(/<svg\b/g)).toHaveLength(1);
    const placeholder = tiles.indexOf("<svg");
    expect(tiles.indexOf("/img/a.svg")).toBeLessThan(placeholder);
    expect(placeholder).toBeLessThan(tiles.indexOf("/img/c.svg"));
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-015 验收第 3 条「三个装饰图块取精选商品的前三张图片」与 UX P01「装饰性的示例商品图」——还没取到或取不到时显示占位形状，不显示错误。
  it.each([loading, failed])("shows placeholder tiles while featured products are %j", (featured) => {
    const hero = sectionOf(render({ featured }), "home.hero_title");
    expect(hero).not.toContain("<img");
    expect(hero.match(/<svg\b/g)).toHaveLength(3);
    expect(hero).not.toContain(COPY["common.error_retry"].en);
  });
});

describe("how this demo works", () => {
  // UX P01：「演示怎么玩」四步，文案全部来自字典，按 ①–④ 的顺序。
  it("lists the four steps in order", () => {
    const how = sectionOf(render(), "home.how_title");
    expect(how).toContain("<ol");
    expect(textNodes(how)).toEqual([
      COPY["home.how_title"].en,
      COPY["home.how_1"].en,
      COPY["home.how_2"].en,
      COPY["home.how_3"].en.replace(/"/g, "&quot;"),
      COPY["home.how_4"].en,
    ]);
  });
});

describe("categories block", () => {
  // UX P01：每块显示分类图片与名称，链到按该分类筛选的商品列表。
  it("links each category to the filtered product list", () => {
    const block = sectionOf(render({ categories: { status: "ready", data: categoryList } }), "home.categories");
    expect(block).toContain(`<a class="acs-cat" href="/products?category=apparel">`);
    expect(block).toContain(`<a class="acs-cat" href="/products?category=bags">`);
    expect(block).toContain(`<img class="site-img" src="/demo-images/apparel.svg" alt=""`);
  });

  // SHOP-TASK-015 验收第 3 条「分类图片（为空时显示视觉稿的占位形状）」。
  it("shows the placeholder shape when a category has no image", () => {
    const block = sectionOf(render({ categories: { status: "ready", data: categoryList } }), "home.categories");
    const bags = /<a class="acs-cat" href="\/products\?category=bags">([\s\S]*?)<\/a>/.exec(block)?.[1] ?? "";
    expect(bags).toMatch(/^<span class="acs-cat__img"><svg viewBox="0 0 100 100" aria-hidden="true">/);
    expect(bags).not.toContain("<img");
    expect(textNodes(bags)).toEqual(["Bags", "Bags"]);
  });

  // SHOP-TASK-015 验收第 7 条「请求失败时在对应区块…显示 common.error_retry，不影响…其他区块」。
  it("shows the retry message in place when categories fail", () => {
    const html = render({ categories: failed, featured: { status: "ready", data: featuredPage } });
    expect(sectionOf(html, "home.categories")).toContain(COPY["common.error_retry"].en);
    expect(sectionOf(html, "home.featured")).not.toContain(COPY["common.error_retry"].en);
    expect(sectionOf(html, "home.featured").match(/class="acs-pcard"/g)).toHaveLength(4);
    expect(html).toContain(COPY["home.hero_title"].en);
  });
});

describe("featured block", () => {
  // UX P01（0.6）：未挑选时显示按最新排序的前 4 件——卡片按接口返回的顺序显示，不在页面重排。
  it("shows the products in the order returned", () => {
    const block = sectionOf(render({ featured: { status: "ready", data: featuredPage } }), "home.featured");
    expect([...block.matchAll(/<span class="acs-pcard__name">([^<]*)<\/span>/g)].map((m) => m[1])).toEqual([
      "Name a",
      "Name b",
      "Name c",
      "Name d",
    ]);
    expect(block).toContain("RM 15.00");
  });

  // UX P01「去向：P03（精选商品）」与 SHOP-TASK-017 验收第 2 条「本任务起商品卡与首页精选按路由规则链到详情页」：每件精选商品链到它的详情页。
  it("links each featured product to its detail page", () => {
    const block = sectionOf(render({ featured: { status: "ready", data: featuredPage } }), "home.featured");
    expect([...block.matchAll(/<a class="acs-pcard" href="([^"]*)">/g)].map((m) => m[1])).toEqual([
      "/products/a",
      "/products/b",
      "/products/c",
      "/products/d",
    ]);
  });

  // SHOP-TASK-015 验收第 7 条「请求失败时在对应区块…显示 common.error_retry，不影响…其他区块」：其他区块照常。
  it("shows the retry message in place when featured products fail", () => {
    const html = render({ featured: failed, categories: { status: "ready", data: categoryList } });
    expect(sectionOf(html, "home.featured")).toContain(COPY["common.error_retry"].en);
    expect(sectionOf(html, "home.categories")).not.toContain(COPY["common.error_retry"].en);
    expect(html.match(new RegExp(COPY["common.error_retry"].en.replace(/\./g, "\\."), "g"))).toHaveLength(1);
  });
});

function page(...slugs: string[]): ProductPage {
  return { total: slugs.length, page: 1, page_size: 4, items: slugs.map((slug) => item(slug, `/img/${slug}.svg`)) };
}

function slugsOf(remote: Remote<ProductPage>): string[] | null {
  return remote.status === "ready" ? remote.data.items.map((product) => product.slug) : null;
}

const allShown = DEFAULT_STORE_DESIGN.homeBlocks;

describe("featured products from the store design", () => {
  // REQUIREMENTS「店铺装修」「前台按该顺序只显示仍上架的商品」与 UX P01（0.6）「按挑选顺序只显示仍上架的」：
  // 接口按 newest 返回，页面按 featured_slugs 的顺序重排；没返回的（已下架）略去，其余顺序不变。
  it("shows the returned products in the picked order", () => {
    const source = featuredSource(["c", "gone", "a", "b"], { status: "ready", data: page("a", "b", "c") });
    const featured = featuredRemote(source, loading);
    expect(slugsOf(featured)).toEqual(["c", "a", "b"]);
    const html = render({ featured });
    const block = sectionOf(html, "home.featured");
    expect([...block.matchAll(/<a class="acs-pcard" href="([^"]*)">/g)].map((m) => m[1])).toEqual([
      "/products/c",
      "/products/a",
      "/products/b",
    ]);
  });

  // SHOP-TASK-054 验收「主视觉区块用同一组商品」：主视觉的三个图块按挑选顺序取前三件的图片。
  it("uses the same picked products for the hero tiles", () => {
    const featured = featuredRemote(featuredSource(["c", "a", "b"], { status: "ready", data: page("a", "b", "c") }), loading);
    const hero = sectionOf(render({ featured }), "home.hero_title");
    expect([...hero.matchAll(/<img[^>]*src="([^"]*)"/g)].map((m) => m[1])).toEqual(["/img/c.svg", "/img/a.svg", "/img/b.svg"]);
  });

  // 派生实现约束（实现选择）：守住 UX P01「按挑选顺序只显示」——重复的 slug 只显示一次，接口多返回的商品不显示。
  it("shows each picked product once and nothing else", () => {
    expect(orderedFeatured(page("a", "b", "x"), ["b", "a", "b"]).items.map((product) => product.slug)).toEqual(["b", "a"]);
  });

  // REQUIREMENTS「店铺装修」「未挑选或挑选的商品都已下架时显示最新的 4 件」与验收「featured_slugs 为空、设置请求失败或按 slug 的请求成功但一件也没返回时显示最新 4 件」
  // （设置请求失败时 storeDesign.ts 把 featuredSlugs 设为空，见 storeDesign.test.ts）。
  it("falls back to the four newest when nothing is picked or nothing picked is returned", () => {
    const newest: Remote<ProductPage> = { status: "ready", data: page("n1", "n2", "n3", "n4") };
    for (const source of [
      featuredSource([], loading),
      featuredSource(["gone"], { status: "ready", data: page() }),
      featuredSource(["gone"], { status: "ready", data: page("other") }),
    ]) {
      expect(source).toEqual({ kind: "newest" });
      expect(newestRequest("zh", source)).toBe(featuredProductsUrl("zh"));
      expect(slugsOf(featuredRemote(source, newest))).toEqual(["n1", "n2", "n3", "n4"]);
    }
  });

  // SHOP-TASK-054 验收「按 slug 的请求本身失败（网络中断或非 2xx）时与现在精选请求失败的处理相同（区块显示现有的错误提示），不再改取最新 4 件」。
  it.each([failed, notFound])("shows the retry message without the newest when the picked request is %j", (picked) => {
    const source = featuredSource(["a"], picked);
    expect(source).toEqual({ kind: "error" });
    expect(newestRequest("en", source)).toBeNull();
    const html = render({ featured: featuredRemote(source, loading) });
    expect(sectionOf(html, "home.featured")).toContain(COPY["common.error_retry"].en);
  });

  // SHOP-TASK-054 验收「精选商品在设置返回后才取」：设置还没返回时不取精选也不取最新 4 件，区块显示读取中。
  it("waits for the settings before asking for featured products", () => {
    const design = { homeBlocks: allShown, featuredSlugs: null };
    const slugs = featuredSlugsToShow(design);
    expect(slugs).toBeNull();
    expect(pickedRequest("en", slugs)).toBeNull();
    const source = featuredSource(slugs, loading);
    expect(newestRequest("en", source)).toBeNull();
    expect(featuredRemote(source, loading)).toEqual(loading);
  });

  // SHOP-TASK-054 验收「featured_slugs 非空时经 SHOP-TASK-053 的 slug 参数取商品卡片」：有挑选时先按 slug 取，取到之前不取最新 4 件。
  it("asks for the picked slugs first", () => {
    const slugs = featuredSlugsToShow({ homeBlocks: allShown, featuredSlugs: ["b", "a"] });
    expect(pickedRequest("ms", slugs)).toBe(featuredBySlugsUrl("ms", ["b", "a"]));
    const source = featuredSource(slugs, loading);
    expect(source).toEqual({ kind: "loading" });
    expect(newestRequest("ms", source)).toBeNull();
  });

  // UX P01「隐藏的区块整块不渲染」与验收「隐藏的区块不发对应请求」：主视觉与精选都隐藏时不取精选，分类隐藏时不取分类；
  // 主视觉与精选只要有一个显示就取同一组商品。
  it("sends no request for hidden blocks", () => {
    for (const visible of [["how", "categories"], [], ["how"]] as HomeBlock[][]) {
      const slugs = featuredSlugsToShow({ homeBlocks: shown(...visible), featuredSlugs: ["a"] });
      expect(slugs).toBeNull();
      expect(pickedRequest("en", slugs)).toBeNull();
      expect(newestRequest("en", featuredSource(slugs, loading))).toBeNull();
      expect(featuredSlugsToShow({ homeBlocks: shown(...visible), featuredSlugs: [] })).toBeNull();
    }
    expect(categoriesRequest("en", shown("hero", "how", "featured"))).toBeNull();
    expect(categoriesRequest("en", shown("categories"))).toBe(categoriesUrl("en"));
    expect(featuredSlugsToShow({ homeBlocks: shown("hero"), featuredSlugs: ["a"] })).toEqual(["a"]);
    expect(featuredSlugsToShow({ homeBlocks: shown("featured"), featuredSlugs: [] })).toEqual([]);
  });
});

function renderPage(storage: StoreDesignStorage | null): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={null}>
      <RouterProvider initialPath="/">
        <StoreDesignProvider storage={storage}>
          <HomePage />
        </StoreDesignProvider>
      </RouterProvider>
    </LanguageProvider>,
  );
}

function rememberedBlocks(blocks: HomeBlockSetting[]): StoreDesignStorage {
  const raw = JSON.stringify({ theme: "batik", accent: null, home_blocks: blocks });
  return { getItem: (key) => (key === STORE_DESIGN_STORAGE_KEY ? raw : null), setItem: () => undefined };
}

describe("home page with the store design", () => {
  // Kelvin 2026-10-07 决定「打开页面时先用它」与验收「首次渲染用记住的设置」「首页按设置的顺序与显隐渲染四个区块（★ home.demo_hint 照常始终显示）」。
  it("renders the remembered block order and visibility on first render", () => {
    const html = renderPage(
      rememberedBlocks([
        { block: "categories", visible: true },
        { block: "how", visible: false },
        { block: "featured", visible: true },
        { block: "hero", visible: true },
      ]),
    );
    const order = [COPY["home.demo_hint"].en, COPY["home.categories"].en, COPY["home.featured"].en, COPY["home.hero_title"].en];
    const positions = order.map((text) => html.indexOf(text));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    expect(html).not.toContain(COPY["home.how_title"].en);
  });

  // Kelvin 2026-10-07 决定「没有记住的值时先用默认主题」与验收「没有时用默认值（四个区块按默认顺序全部显示）」；设置返回前精选区块为读取中，不显示错误。
  it("renders the default blocks when nothing valid is remembered", () => {
    for (const storage of [null, rememberedBlocks([{ block: "hero", visible: true }])]) {
      const html = renderPage(storage);
      const positions = HOME_BLOCKS.map((block) => html.indexOf(COPY[HEADINGS[block]].en));
      expect(positions.every((position) => position >= 0)).toBe(true);
      expect([...positions].sort((a, b) => a - b)).toEqual(positions);
      expect(sectionOf(html, "home.featured")).toContain('aria-busy="true"');
      expect(html).not.toContain(COPY["common.error_retry"].en);
    }
  });
});

const HOUR_MS = 3_600_000;
const SERVER_TIME = "2026-11-14T04:00:00Z";
// 服务端时间之后一小时结束：时钟替身固定为 0，倒计时文字为 countdownText(language, HOUR_MS)。
const ENDS_AT = "2026-11-14T05:00:00Z";

function sale(overrides: Partial<PromotionDisplay> = {}): PromotionDisplay {
  return {
    promotion_id: 7,
    title: "Lunar New Year Sale",
    subtitle: "20% off apparel and more",
    badge: null,
    strip: null,
    pack: "cny",
    palette: "cny",
    ambient: "garland",
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
    rules: [],
    ...overrides,
  };
}

const IMAGE_SALE: Partial<PromotionDisplay> = {
  poster_mode: "image",
  poster_desktop: "/media/promo/desktop.webp",
  poster_phone: "/media/promo/phone.webp",
  poster_alt: "Lunar New Year Sale: 20% off apparel",
};

interface PromoRenderOptions {
  blocks?: readonly HomeBlockSetting[];
  language?: Language;
}

// 有活动的状态以 PromotionProvider 的 initial 传入（null 即接口说没有活动）；读取替身从不返回。
function renderPromo(display: PromotionDisplay | null, { blocks = DEFAULT_STORE_DESIGN.homeBlocks, language = "en" }: PromoRenderOptions = {}): string {
  const storage = { getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null), setItem: () => undefined };
  const initial: PromotionResponse = { promotion: display, server_time: SERVER_TIME };
  return renderToStaticMarkup(
    <LanguageProvider storage={storage}>
      <RouterProvider initialPath="/">
        <PromotionProvider initial={initial} fetcher={() => new Promise<PromotionResponse>(() => undefined)} clock={() => 0}>
          <HomeView blocks={blocks} categories={loading} featured={loading} />
        </PromotionProvider>
      </RouterProvider>
    </LanguageProvider>,
  );
}

const CROWN =
  `<span class="acs-promo-poster__crown" aria-hidden="true"><span class="acs-promo-motif"></span>` +
  `<span class="acs-promo-motif acs-promo-motif--art"></span><span class="acs-promo-motif"></span></span>`;

const CLOCK_ICON =
  `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">` +
  `<circle cx="12" cy="13" r="8"></circle><path d="M12 9v4l3 2M9 2h6"></path></svg>`;

// promo.shop_now 按钮：P15 在路由表里时才有（按 saleLinkPath() 的返回值，不写死）。
function shopNow(language: Language): string {
  const path = saleLinkPath();
  return path === null ? "" : `<a class="acs-btn acs-btn--lg acs-btn--promo" href="${path}">${COPY["promo.shop_now"][language]}</a>`;
}

function poster(html: string): string {
  return /<section class="acs-promo-poster[\s\S]*?<\/section>/.exec(html)?.[0] ?? "";
}

// 上传图海报：P15 在路由表里时为链接，否则为同类名的 div。
function imagePoster(html: string): string {
  return /<(a|div) class="acs-promo-poster-img"[\s\S]*?<\/\1>/.exec(html)?.[0] ?? "";
}

describe("home promotion poster", () => {
  // UX 0.20 P01：「模板模式按 `home_poster` 的变体排版，标题与副标题是活动文案（副标题为空时不显示该行），`{time}` 为倒计时」——
  // 两种版式、三种语言：标题、副标题、倒计时与 promo.shop_now（P15 不在路由表时不放），右侧为装饰图。
  it.each(LANGUAGES.flatMap((language) => ["split", "centered"].map((layout): [Language, string] => [language, layout])))(
    "shows the %s %s template poster",
    (language, layout) => {
      const html = renderPromo(sale({ home_poster: layout }), { language });
      expect(poster(html)).toBe(
        `<section class="acs-promo-poster acs-promo-poster--${layout}" aria-label="Lunar New Year Sale">` +
          `<div class="acs-promo-poster__text">${CROWN}` +
          `<h2 class="acs-display-l acs-promo-poster__title">Lunar New Year Sale</h2>` +
          `<p class="acs-promo-poster__sub">20% off apparel and more</p>` +
          `<span class="acs-promo-count">${CLOCK_ICON}<span>${countdownText(language, HOUR_MS)}</span></span>` +
          shopNow(language) +
          `</div><div class="acs-promo-poster__art" aria-hidden="true"></div></section>`,
      );
    },
  );

  // UX 0.20 P01：「副标题为空时不显示该行」。
  it.each([null, ""])("leaves out the subtitle line when it is empty (%j)", (subtitle) => {
    const html = poster(renderPromo(sale({ subtitle })));
    expect(html).toContain(`<h2 class="acs-display-l acs-promo-poster__title">Lunar New Year Sale</h2><span class="acs-promo-count">`);
    expect(html).not.toContain("acs-promo-poster__sub");
    expect(html).not.toMatch(/<p[\s>]/);
  });

  // UX 0.20 P01：「上传图模式桌面显示桌面图、手机显示手机图……整图是去 P15 的链接，读屏读活动的替代文字」——
  // picture 以 767px 切换两张图，替代文字为 poster_alt；P15 不在路由表时整图为同类名的 div。
  it.each(LANGUAGES)("shows the uploaded images with the alt text in %s", (language) => {
    const html = renderPromo(sale(IMAGE_SALE), { language });
    expect(html).not.toContain("acs-promo-poster--");
    const image = imagePoster(html);
    const path = saleLinkPath();
    expect(image.startsWith(path === null ? `<div class="acs-promo-poster-img">` : `<a class="acs-promo-poster-img" href="${path}">`)).toBe(true);
    expect(image).toMatch(
      /^<(a|div) class="acs-promo-poster-img"[^>]*><picture><source media="\(max-width: 767px\)" srcset="\/media\/promo\/phone\.webp"\/?><img src="\/media\/promo\/desktop\.webp" alt="Lunar New Year Sale: 20% off apparel"\/?><\/picture><\/\1>$/i,
    );
    expect(html).not.toContain(COPY["promo.shop_now"][language]);
  });

  // UX 0.20 P01「模板或上传图」与落实写法「上传图缺任一张时按模板模式显示」。
  it.each([
    { poster_desktop: null },
    { poster_phone: null },
    { poster_desktop: null, poster_phone: null },
  ])("falls back to the template poster when an uploaded image is missing (%j)", (missing) => {
    const html = renderPromo(sale({ ...IMAGE_SALE, ...missing }));
    expect(html).not.toContain("acs-promo-poster-img");
    expect(html).not.toContain("<picture");
    expect(poster(html)).toContain(`<section class="acs-promo-poster acs-promo-poster--split" aria-label="Lunar New Year Sale">`);
  });

  // UX 0.20 P01：「有活动生效时显示在 ★ 提示之后、所有区块之前，不属于任何区块，A08 的区块设置不影响它」——
  // 任何区块顺序与显隐下都紧接在 ★ 提示之后、第一个区块之前；区块全部隐藏时照样显示。
  it("puts the poster after the hint and before every block whatever the block settings", () => {
    const defaults = DEFAULT_STORE_DESIGN.homeBlocks;
    for (const blocks of [defaults, [...defaults].reverse(), shown("featured"), shown()]) {
      for (const display of [sale(), sale(IMAGE_SALE)]) {
        const html = renderPromo(display, { blocks });
        const hintEnd = html.indexOf("</p>", html.indexOf(COPY["home.demo_hint"].en)) + "</p>".length;
        const start = html.indexOf("acs-promo-poster");
        expect(hintEnd).toBeGreaterThan(0);
        expect(html.lastIndexOf("<", start)).toBe(hintEnd);
        for (const setting of blocks.filter((candidate) => candidate.visible)) {
          expect(html.indexOf(COPY[HEADINGS[setting.block]].en)).toBeGreaterThan(start);
        }
      }
    }
    expect(renderPromo(sale(), { blocks: shown() })).toMatch(/^<main class="site-home"><p class="acs-hint">[\s\S]*<\/p><section class="acs-promo-poster /);
  });

  // UX 0.20 P01：「没有生效活动时不占位」，UX 0.20「节日促销活动」「没有生效活动时各页与 0.19 完全相同」——
  // 接口说没有活动与读取中时，首页与没有活动功能时逐字相同。
  it("renders no promotion content without a promotion or while loading", () => {
    const withoutProvider = render();
    expect(renderPromo(null)).toBe(withoutProvider);
    const whileLoading = renderToStaticMarkup(
      <LanguageProvider storage={null}>
        <RouterProvider initialPath="/">
          <PromotionProvider fetcher={() => new Promise<PromotionResponse>(() => undefined)}>
            <HomeView blocks={DEFAULT_STORE_DESIGN.homeBlocks} categories={loading} featured={loading} />
          </PromotionProvider>
        </RouterProvider>
      </LanguageProvider>,
    );
    expect(whileLoading).toBe(withoutProvider);
    expect(withoutProvider).not.toContain("acs-promo");
  });
});
