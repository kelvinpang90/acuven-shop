import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CategoryListItem, ProductPage, ProductSummary, Remote } from "../api/catalog";
import { COPY } from "../i18n/copy";
import type { CopyKey } from "../i18n/copy";
import { LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { HOME_BLOCKS, storeDesign } from "../storeDesign";
import type { HomeBlock, HomeBlockSetting } from "../storeDesign";
import { HomeView } from "./HomePage";

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

interface RenderOptions {
  blocks?: readonly HomeBlockSetting[];
  categories?: Remote<CategoryListItem[]>;
  featured?: Remote<ProductPage>;
}

function render({ blocks = storeDesign.homeBlocks, categories = loading, featured = loading }: RenderOptions = {}): string {
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
  // 验收：默认设置为主视觉、演示怎么玩、按分类浏览、精选商品，全部显示（UX P01 A08 的默认顺序）。
  it("shows the four blocks in the UX order by default", () => {
    expect(storeDesign.homeBlocks).toEqual(HOME_BLOCKS.map((block) => ({ block, visible: true })));
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
  it("keeps the demo hint before every block whatever the order", () => {
    for (const blocks of [storeDesign.homeBlocks, shown("featured"), [...storeDesign.homeBlocks].reverse()]) {
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

  // UX P01：三个装饰图块是示例商品图，不是链接，读屏忽略；取精选商品的前三张图片。
  it("shows the first three featured images as decoration", () => {
    const hero = sectionOf(render({ featured: { status: "ready", data: featuredPage } }), "home.hero_title");
    const tiles = /<div class="site-hero__tiles" aria-hidden="true">([\s\S]*)<\/div>/.exec(hero)?.[1] ?? "";
    expect(tiles).not.toMatch(/<a\b/);
    expect([...tiles.matchAll(/<img[^>]*src="([^"]*)"[^>]*alt=""/g)].map((m) => m[1])).toEqual([
      "/img/a.svg",
      "/img/c.svg",
      "/img/d.svg",
    ]);
  });

  // 精选商品还没取到或取不到时，图块显示视觉稿的占位形状，主视觉照常显示。
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

  // 验收：分类图片为空时显示视觉稿的占位形状。
  it("shows the placeholder shape when a category has no image", () => {
    const block = sectionOf(render({ categories: { status: "ready", data: categoryList } }), "home.categories");
    const bags = /<a class="acs-cat" href="\/products\?category=bags">([\s\S]*?)<\/a>/.exec(block)?.[1] ?? "";
    expect(bags).toMatch(/^<span class="acs-cat__img"><svg viewBox="0 0 100 100" aria-hidden="true">/);
    expect(bags).not.toContain("<img");
    expect(textNodes(bags)).toEqual(["Bags", "Bags"]);
  });

  // 验收：请求失败时在该区块显示 common.error_retry，不影响其他区块。
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

  // 验收：请求失败时在该区块显示 common.error_retry，其他区块照常。
  it("shows the retry message in place when featured products fail", () => {
    const html = render({ featured: failed, categories: { status: "ready", data: categoryList } });
    expect(sectionOf(html, "home.featured")).toContain(COPY["common.error_retry"].en);
    expect(sectionOf(html, "home.categories")).not.toContain(COPY["common.error_retry"].en);
    expect(html.match(new RegExp(COPY["common.error_retry"].en.replace(/\./g, "\\."), "g"))).toHaveLength(1);
  });
});
