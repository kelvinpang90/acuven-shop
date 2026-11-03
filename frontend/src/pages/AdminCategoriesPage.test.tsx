import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_CATEGORIES_PATH, RouterProvider } from "../router";
import AdminCategoriesPage, { AdminCategoriesContent } from "./AdminCategoriesPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const CATEGORIES_PATH = "/admin/categories";
const NAV_PATHS = [
  "/admin/orders",
  "/admin/refunds",
  "/admin/products",
  CATEGORIES_PATH,
  "/admin/coupons",
  "/admin/shipping",
  "/admin/stock-resets",
  "/admin/store-design",
  "/admin/settings",
];
const NAV_KEYS = [
  "admin.nav_orders",
  "admin.nav_refunds",
  "admin.nav_products",
  "admin.nav_categories",
  "admin.nav_coupons",
  "admin.nav_shipping",
  "admin.nav_stock_resets",
  "admin.nav_store_design",
  "admin.nav_settings",
] as const;
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactNode, language: Language): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={CATEGORIES_PATH}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={CATEGORIES_PATH} storage={storage(language)} />);
}

// 已登录（会话 200）时的后台框架：内容区为本页的内容。
function renderSignedIn(language: Language): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="categories"
        state={SIGNED_IN}
        busy={false}
        menuOpen={false}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        <AdminCategoriesContent />
      </AdminFrameView>,
      language,
    ),
  );
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const labels = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...labels];
}

function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => m[0]);
}

function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((m[1] ?? "").toLowerCase(), unescapeHtml(m[2] ?? ""));
  }
  return found;
}

function classes(tag: string): string[] {
  return (attributes(tag).get("class") ?? "").split(/\s+/);
}

// 第一个 class 含 className 的 name 元素（含其内容）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) => classes(m[0]).includes(className))?.index;
  if (start === undefined) {
    throw new Error(`no <${name}> with class ${className}`);
  }
  const re = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  re.lastIndex = start;
  let depth = 0;
  for (let m = re.exec(html); m !== null; m = re.exec(html)) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, m.index + m[0].length);
    }
  }
  throw new Error(`unclosed <${name}>`);
}

describe("AdminCategoriesPage", () => {
  // UX A10「入口：后台导航 [admin.nav_categories]」、0.17「独立一页 /admin/categories」与 SHOP-TASK-196 验收「页面」一条「两页都以后台框架渲染」：
  // 经路由打开 /admin/categories 渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），不套前台框架（没有前台页头、页脚与前台演示横幅）。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    expect(ADMIN_CATEGORIES_PATH).toBe(CATEGORIES_PATH);
    const html = renderApp(language);
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX 0.17「导航项 [admin.nav_categories] 排在 [admin.nav_products] 之后」、「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、
  // [admin.nav_products]、[admin.nav_categories]（0.17）、[admin.nav_coupons]、…」与 SHOP-TASK-196 验收「导航」一条「桌面导航与手机菜单都按这个顺序列出」：
  // 经路由打开本页，桌面导航与 ☰ 菜单都依次为这九项，分类排在商品之后并标 aria-current="page"；各项文字为字典里当前语言的文案。
  it.each(LANGUAGES)("marks categories current, after products, in both navigation menus in %s", (language) => {
    for (const html of [renderApp(language), renderToStaticMarkup(wrap(<AdminCategoriesPage />, language))]) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual([
          undefined,
          undefined,
          undefined,
          "page",
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
        ]);
        expect(textNodes(nav).slice(0, 9)).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
      }
    }
  });

  // UX A10 手机线框「☰  [admin.nav_categories]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」。
  it.each(LANGUAGES)("names the categories item in the phone bar in %s", (language) => {
    expect(element(renderApp(language), "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_categories"][language])}</span>`,
    );
  });

  // UX A10 桌面线框内容区第一行「[admin.nav_categories]」、UX-COPY「admin.nav_categories：A10 导航与页标题」与 SHOP-TASK-196 验收「页面」一条
  // 「内容区只有标题 h1（类名照其他后台页的 acs-admin__h），文字为 admin.nav_categories」「桌面与手机都显示标题（视觉稿 A10-phone 的内容区有标题）」
  // 「不加其他文字」：已登录时内容区里只有这一个标题，h1 只带 acs-admin__h（没有手机隐藏用的类）。
  it.each(LANGUAGES)("shows only the categories title in the content area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    const opening = tags(main, "main")[0] ?? "";
    expect(main).toBe(`${opening}<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_categories"][language])}</h1></main>`);
  });

  // SHOP-TASK-196 验收「页面」一条「不发请求」（请求模块留给 SHOP-TASK-198）：fetch 换成替身，渲染本页的内容与整页时都没有调用它。
  it("makes no request of its own", () => {
    const fetch = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", fetch);
    renderToStaticMarkup(wrap(<AdminCategoriesContent />, "en"));
    renderToStaticMarkup(wrap(<AdminCategoriesPage />, "en"));
    renderApp("en");
    expect(fetch).not.toHaveBeenCalled();
  });

  // SHOP-TASK-196 验收「测试」一条「页面文字全部来自字典」：经路由打开与已登录时，页面上看得见或读得出的每段文字（含 aria-label）
  // 都是当前语言的字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const html of [renderApp(language), renderSignedIn(language)]) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
