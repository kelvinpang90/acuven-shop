import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "../App";
import { BRAND, COPY, LANGUAGES, formatCopy } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY } from "../i18n/language";
import { ROUTE_PATHS } from "../router";

function render(path: string, language: Language = "en"): string {
  const storage = {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
  return renderToStaticMarkup(<App initialPath={path} storage={storage} />);
}

function section(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const banner = (html: string) => section(html, /<div class="acs-banner">[\s\S]*?<\/div>/);
const header = (html: string) => section(html, /<header[\s\S]*<\/header>/);
const footer = (html: string) => section(html, /<footer[\s\S]*<\/footer>/);

const paths = [...ROUTE_PATHS];
const languages = [...LANGUAGES];
const languageLabel = { en: "common.lang_en", zh: "common.lang_zh", ms: "common.lang_ms" } as const;

describe("demo banner", () => {
  // UX「全局框架」：演示横幅在页头上方常驻，桌面 common.demo_banner、手机 common.demo_banner_short。
  it.each(paths)("is rendered first on %s with both texts", (path) => {
    for (const language of LANGUAGES) {
      const html = render(path, language);
      const content = banner(html);
      expect(content).toContain(COPY["common.demo_badge"][language]);
      expect(content).toContain(COPY["common.demo_banner"][language]);
      expect(content).toContain(COPY["common.demo_banner_short"][language]);
      expect(html.indexOf('class="acs-banner"')).toBeLessThan(html.indexOf("<header"));
    }
  });

  // UX「全局框架」：横幅不可关闭——没有按钮、链接或任何可交互控件。
  it.each(paths)("has no close control on %s", (path) => {
    const content = banner(render(path));
    expect(content).not.toMatch(/<(button|a|input)\b/);
    expect(content).not.toMatch(/role="button"|aria-label|onclick|tabindex/i);
  });
});

describe("root element", () => {
  // 验收：根元素带 class acs、data-shop-theme 为 pandan、data-mode 为 auto，不设 data-accent。
  it("uses the default theme from storeDesign.ts without an accent", () => {
    const html = render("/");
    expect(html).toMatch(/^<div class="acs site" data-shop-theme="pandan" data-mode="auto">/);
    expect(html).not.toContain("data-accent");
    expect(html).not.toContain("acs--phone");
  });

  // 验收：页面代码里不写死颜色、字体或圆角——没有任何行内样式。
  it.each(paths)("has no inline styles on %s", (path) => {
    expect(render(path)).not.toContain("style=");
  });
});

describe("header", () => {
  // UX「全局框架」：页头有品牌字样，链到首页。
  it("links the brand to the home page", () => {
    expect(header(render("/privacy"))).toMatch(new RegExp(`<a class="acs-brand" href="/">${BRAND}</a>`));
  });

  // SHOP-TASK-014 验收的「尚未实现页面的导航项不渲染」，经 SHOP-TASK-015 验收第 5 条「common.nav_shop 按路由规则开始渲染」后剩查询订单、登录：一律不渲染。
  // 商品详情自 SHOP-TASK-017 起已存在（语言切换链接指向当前路径，在详情页上即 /products/…）；购物车页自 SHOP-TASK-018 起已存在（页头购物车入口链到 /cart）；查询订单、登录仍未实现。
  it.each(paths)("renders no navigation item for pages that do not exist yet on %s", (path) => {
    for (const language of LANGUAGES) {
      const html = render(path, language);
      for (const key of ["common.nav_track", "common.nav_login"] as const) {
        expect(html).not.toContain(`>${COPY[key][language]}<`);
      }
      expect(html).not.toMatch(/href="\/(track|login|account|register)/);
    }
  });

  // SHOP-TASK-015 验收第 5 条「页头 common.nav_shop 按路由规则开始渲染」：商品列表进了路由表，桌面导航与手机菜单都渲染，在列表页标为当前。
  it.each(languages)("links common.nav_shop to the product list in %s", (language) => {
    const home = header(render("/", language));
    expect(home.match(new RegExp(`<a href="/products">${COPY["common.nav_shop"][language]}</a>`, "g"))).toHaveLength(2);
    const list = header(render("/products", language));
    expect(list).toContain(`<a aria-current="page" href="/products">${COPY["common.nav_shop"][language]}</a>`);
  });

  // UX「全局框架」页头「购物车数量」（桌面在导航行、手机在第一行 [common.nav_cart]）与 SHOP-TASK-018 验收第 2 条「本任务起页头显示 common.nav_cart，{count} 为购物车各行件数之和」：
  // 服务端渲染时没有本浏览器购物车，件数为 0；桌面与手机各一个链到 /cart 的入口，在购物车页标为当前。各行件数之和由 cart.test.ts 的 cartItemCount 守住。
  it.each(languages)("links common.nav_cart with the item count to the cart in %s", (language) => {
    const label = formatCopy(COPY["common.nav_cart"][language], { count: 0 });
    const home = header(render("/", language));
    expect(home).toContain(`<a class="site-phone-only site-header__cart" href="/cart">${label}</a>`);
    expect(home).toContain(`<nav class="acs-nav site-header__end"><a href="/cart">${label}</a></nav>`);
    const cart = header(render("/cart", language));
    expect(cart.match(new RegExp(`aria-current="page" href="/cart">${label.replace(/[()]/g, "\\$&")}</a>`, "g"))).toHaveLength(2);
  });

  // UX「全局框架」手机线框：购物车入口在第一行，☰ 菜单里没有购物车。
  it("keeps the cart entry out of the phone menu", () => {
    const panel = section(header(render("/")), /<div id="[^"]*" class="site-phone-only site-header__panel"[\s\S]*?<\/div>/);
    expect(panel).not.toContain("/cart");
  });

  // UX「全局框架」页头含「搜索框」与 SHOP-TASK-015 验收第 5 条「页头搜索框在本任务起显示并跳到带搜索词的列表」：占位与读屏标签 list.search_placeholder，按钮 common.search，提交到商品列表的 q 参数。
  it.each(languages)("has the header search form in %s", (language) => {
    const html = header(render("/", language));
    const form = section(html, /<form[\s\S]*?<\/form>/);
    expect(form).toMatch(/^<form class="acs-search site-header__search" role="search" action="\/products" method="get">/);
    expect(form).toContain(`name="q"`);
    expect(form).toContain(`aria-label="${COPY["list.search_placeholder"][language]}"`);
    expect(form).toContain(`placeholder="${COPY["list.search_placeholder"][language]}"`);
    expect(form).toContain(`type="submit">${COPY["common.search"][language]}</button>`);
    expect(html.match(/<form\b/g)).toHaveLength(1);
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-015 验收第 5 条「页头搜索框…跳到带搜索词的列表」——在列表页预填当前搜索词，其他页面为空。
  it("prefills the search box with the current search term on the product list", () => {
    expect(header(render("/products?q=tote+bag"))).toContain(`value="tote bag"`);
    expect(header(render("/privacy"))).toContain(`value=""`);
  });

  // UX「全局框架」：语言切换 EN | 中文 | BM，当前语言标出。
  it.each(languages)("offers all three languages and marks %s as current", (language) => {
    const html = header(render("/", language));
    for (const option of LANGUAGES) {
      expect(html).toContain(`>${COPY[languageLabel[option]][language]}</a>`);
    }
    const current = [...html.matchAll(/<a href="\/" lang="([^"]+)" aria-current="true">/g)].map((m) => m[1]);
    expect(new Set(current)).toEqual(new Set([language === "zh" ? "zh-Hans" : language]));
  });

  // UX「全局框架」手机：☰ 菜单（读屏标签 common.nav_menu）与当前语言下拉；菜单含隐私说明与三种语言。
  it.each(languages)("has the phone menu button and current-language button in %s", (language) => {
    const html = header(render("/", language));
    expect(html).toMatch(new RegExp(`<button[^>]*aria-label="${COPY["common.nav_menu"][language]}"[^>]*aria-expanded="false"`));
    expect(html).toMatch(new RegExp(`aria-expanded="false"[^>]*><span>${COPY[languageLabel[language]][language]}</span>`));
    expect(html).toContain(`href="/privacy">${COPY["common.nav_privacy"][language]}</a>`);
  });
});

describe("footer", () => {
  // UX「全局框架」：页脚有 common.footer_demo 与链到 P14 的 common.nav_privacy。
  it.each(languages)("shows the demo note and the privacy link in %s", (language) => {
    const html = footer(render("/", language));
    expect(html).toContain(COPY["common.footer_demo"][language]);
    expect(html).toContain(`href="/privacy">${COPY["common.nav_privacy"][language]}</a>`);
  });

  // UX Q10：WhatsApp 联系链接未配置时隐藏所有 WhatsApp 按钮，不显示占位文字；不设站内联系表单。
  // 页头的搜索表单（role="search"）不是联系表单，先去掉再检查。
  it.each(paths)("has no WhatsApp button, placeholder or contact form on %s", (path) => {
    const html = render(path);
    expect(html.toLowerCase()).not.toContain("whatsapp");
    expect(html).not.toContain("{{");
    const withoutSearch = html.replace(/<form[^>]*role="search"[\s\S]*?<\/form>/g, "");
    expect(withoutSearch).not.toMatch(/<(form|textarea)\b/);
  });
});
