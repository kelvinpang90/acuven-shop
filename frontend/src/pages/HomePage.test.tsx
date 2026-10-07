import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { categoriesUrl, featuredBySlugsUrl, featuredProductsUrl } from "../api/catalog";
import type { CategoryListItem, ProductPage, ProductSummary, Remote } from "../api/catalog";
import { COPY } from "../i18n/copy";
import type { CopyKey } from "../i18n/copy";
import { LanguageProvider } from "../i18n/language";
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
