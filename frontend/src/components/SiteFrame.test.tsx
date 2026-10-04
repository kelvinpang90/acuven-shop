import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "../App";
import { BRAND, COPY, LANGUAGES, formatCopy } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY } from "../i18n/language";
import { isRoutePath, ROUTE_PATHS } from "../router";

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

// 尚未实现的页面：导航文字（有的话）与链接路径。是否已实现由传入的判断决定，测试里用路由表的 isRoutePath。
const PENDING_PAGES: readonly { path: string; label?: CopyKey }[] = [
  { path: "/track", label: "common.nav_track" },
  { path: "/login", label: "common.nav_login" },
  { path: "/account" },
  { path: "/register" },
];

// 线框里页面主体有填写或选择后提交的表单的页面；这些页面主体的表单由各自页面的测试负责。
// 商品列表的筛选控件不在表单里，不在此表。
const PAGE_FORM_PATHS: readonly string[] = [
  "/checkout",
  "/pay",
  "/track",
  "/track/order/refund",
  "/account/order/refund",
  "/login",
  "/forgot-password",
  "/register",
  "/account",
];

// 只对判为尚未实现的页面检查：页面里出现的导航文字与 href 以该路径开头的链接。
function pendingNavigationFindings(html: string, language: Language, isImplemented: (path: string) => boolean): string[] {
  const findings: string[] = [];
  for (const page of PENDING_PAGES) {
    if (isImplemented(page.path)) {
      continue;
    }
    if (page.label !== undefined && html.includes(`>${COPY[page.label][language]}<`)) {
      findings.push(page.label);
    }
    if (html.includes(`href="${page.path}`)) {
      findings.push(`href="${page.path}`);
    }
  }
  return findings;
}

const SEARCH_FORM = /<form[^>]*role="search"[\s\S]*?<\/form>/g;
const FORM_OR_TEXTAREA = /<(form|textarea)\b/;

// 框架（根元素、演示横幅、去掉搜索表单的页头、页脚）对每页检查；页面主体只对不在 PAGE_FORM_PATHS 的页面检查。
function contactFormFindings(html: string, path: string): string[] {
  const headerStart = html.indexOf("<header");
  const headerEnd = html.lastIndexOf("</header>") + "</header>".length;
  const footerStart = html.lastIndexOf("<footer");
  if (headerStart < 0 || headerEnd < headerStart || footerStart < headerEnd) {
    throw new Error("frame not found");
  }
  const frame =
    html.slice(0, headerStart) + html.slice(headerStart, headerEnd).replace(SEARCH_FORM, "") + html.slice(footerStart);
  const body = html.slice(headerEnd, footerStart);
  const findings: string[] = [];
  if (html.toLowerCase().includes("whatsapp")) {
    findings.push("whatsapp");
  }
  if (html.includes("{{")) {
    findings.push("{{");
  }
  if (FORM_OR_TEXTAREA.test(frame)) {
    findings.push("form in frame");
  }
  if (!PAGE_FORM_PATHS.includes(path) && FORM_OR_TEXTAREA.test(body)) {
    findings.push("form in body");
  }
  return findings;
}

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

  // SHOP-TASK-014 验收的「尚未实现页面的导航项不渲染」，按路由表推算（SHOP-TASK-033）：PENDING_PAGES 里每一项只在 isRoutePath 对其路径为假时，
  // 断言每个页面、每种语言都不出现它的导航文字（有的话），也没有 href 以该路径开头的链接；该路径进了路由表即自动不再断言。
  // 改为推算是为了之后每个新页面任务（查询订单、登录、会员中心、注册等）不必为这条测试改这个文件。
  it.each(paths)("renders no navigation item for pages that do not exist yet on %s", (path) => {
    for (const language of LANGUAGES) {
      expect(pendingNavigationFindings(render(path, language), language, isRoutePath)).toEqual([]);
    }
  });

  // 推算规则本身：用替身判断把 /track 视为已实现，它不再被断言，其余项照常断言。
  it("skips pending pages the given check treats as implemented", () => {
    const html = `<nav><a href="/track">${COPY["common.nav_track"].en}</a><a href="/login">${COPY["common.nav_login"].en}</a><a href="/account">x</a><a href="/register">x</a></nav>`;
    expect(pendingNavigationFindings(html, "en", () => false)).toEqual([
      "common.nav_track",
      'href="/track',
      "common.nav_login",
      'href="/login',
      'href="/account',
      'href="/register',
    ]);
    expect(pendingNavigationFindings(html, "en", (candidate) => candidate === "/track")).toEqual([
      "common.nav_login",
      'href="/login',
      'href="/account',
      'href="/register',
    ]);
    expect(pendingNavigationFindings(html, "en", () => true)).toEqual([]);
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

  // UX Q10：WhatsApp 联系链接未配置时隐藏所有 WhatsApp 按钮，不显示占位文字——不含 whatsapp（不区分大小写）与 {{ 对所有页面断言。
  // UX「全局框架」页脚一条的「不设站内联系表单」，按页面推算（SHOP-TASK-033）：每页的演示横幅、页头（先去掉 role="search" 的搜索表单）与页脚都没有 form 或 textarea；
  // 页面主体只对不在 PAGE_FORM_PATHS 的页面断言，表里页面主体的表单由各自页面的测试负责。商品列表的筛选控件不在表单里，照常断言。
  // 改为推算是为了之后每个有表单的页面任务（结账、模拟支付、订单查询、退款申请等）不必为这条测试改这个文件。
  it.each(paths)("has no WhatsApp button, placeholder or contact form on %s", (path) => {
    expect(contactFormFindings(render(path), path)).toEqual([]);
  });

  // 推算规则本身：表里页面的主体表单不断言，表外页面照常断言；框架里的表单与 whatsapp、{{ 对所有页面断言。
  it("checks page bodies for forms only outside the form page list", () => {
    const page = (footerContent: string, body: string) =>
      `<div class="acs site"><div class="acs-banner"></div><header><form class="acs-search" role="search"><input/></form></header>${body}<footer>${footerContent}</footer></div>`;
    const bodyForm = page("", "<main><form><textarea></textarea></form></main>");
    expect(PAGE_FORM_PATHS).not.toContain("/products");
    for (const path of PAGE_FORM_PATHS) {
      expect(contactFormFindings(bodyForm, path)).toEqual([]);
    }
    for (const path of ["/", "/products", "/privacy"]) {
      expect(contactFormFindings(bodyForm, path)).toEqual(["form in body"]);
    }
    expect(contactFormFindings(page("<form></form>", "<main></main>"), "/checkout")).toEqual(["form in frame"]);
    expect(contactFormFindings(page("WhatsApp {{phone}}", "<main></main>"), "/checkout")).toEqual(["whatsapp", "{{"]);
    expect(contactFormFindings(page("", "<main></main>"), "/privacy")).toEqual([]);
  });
});
