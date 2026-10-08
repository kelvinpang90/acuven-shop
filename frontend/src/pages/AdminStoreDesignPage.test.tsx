import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "../App";
import { AdminFrameView } from "../components/AdminFrame";
import type { AdminFrameViewProps, FrameState } from "../components/AdminFrame";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
// 经 Vite 的 ?raw 读成字符串（与 i18n/copy.test.ts 读 UX-COPY 相同），用来确认本页不发起自己的请求。
import pageSource from "./AdminStoreDesignPage.tsx?raw";
import { AdminStoreDesignContent } from "./AdminStoreDesignPage";

const STORE_DESIGN_PATH = "/admin/store-design";
const ORDERS_PATH = "/admin/orders";
const REFUNDS_PATH = "/admin/refunds";
const STOCK_RESETS_PATH = "/admin/stock-resets";
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function renderApp(path: string, language: Language = "en"): string {
  return renderToStaticMarkup(<App initialPath={path} storage={languageStorage(language)} />);
}

function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={STORE_DESIGN_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

// 已登录时的本页：后台框架（会话请求以 state 替身代替）里放本页的内容区。
function renderSignedIn(language: Language = "en", overrides: Partial<AdminFrameViewProps> = {}): string {
  const props: AdminFrameViewProps = {
    mode: "light",
    current: "storeDesign",
    state: SIGNED_IN,
    busy: false,
    menuOpen: false,
    onToggleMenu: noop,
    onCloseMenu: noop,
    onLogOut: noop,
    children: <AdminStoreDesignContent />,
    ...overrides,
  };
  return renderToStaticMarkup(wrap(<AdminFrameView {...props} />, language));
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...attributes].filter((value) => value !== "");
}

// 文本节点（不含属性），依出现顺序。
function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
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
  return (attributes(tag).get("class") ?? "").split(/\s+/).filter((name) => name !== "");
}

// 某个元素（按 class 找到的第一个）从开始标签到与之配对的结束标签的内容（数同名元素的嵌套）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) => classes(m[0]).includes(className))?.index;
  if (start === undefined) {
    throw new Error(`not found: <${name} class="${className}">`);
  }
  const pattern = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  pattern.lastIndex = start;
  let depth = 0;
  for (let m = pattern.exec(html); m !== null; m = pattern.exec(html)) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, m.index + m[0].length);
    }
  }
  throw new Error(`unclosed: <${name}>`);
}

describe("route", () => {
  // UX 页面地图「A08 后台店铺装修（0.4） /admin/store-design」与「管理后台总体」「每页顶部常驻 [admin.demo_banner]」；
  // SHOP-TASK-062 验收第 2 条「新文件 AdminStoreDesignPage.tsx 用后台框架渲染」：经路由打开 /admin/store-design（服务端渲染，会话未返回），
  // 根元素为 acs-admin 并有后台演示横幅，内容区为空并标 aria-busy；不套前台框架（没有前台页头、页脚、品牌名与前台演示横幅）。
  it.each(LANGUAGES)("renders the admin frame without the storefront frame in %s", (language) => {
    const html = renderApp(STORE_DESIGN_PATH, language);
    expect(classes(html.slice(0, html.indexOf(">") + 1))).toContain("acs-admin");
    expect(element(html, "div", "acs-admin__banner")).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).not.toMatch(/<(header|footer)\b/);
    expect(html).not.toContain(BRAND);
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
  });
});

describe("navigation", () => {
  // UX「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、…、[admin.nav_stock_resets]、[admin.nav_store_design]（0.4）、…」、
  // UX A08「入口：后台导航 [admin.nav_store_design]」与 Kelvin 2026-10-06（docs/HANDOFF.md 0.35）「后台导航只显示已上线页面的导航项」；
  // SHOP-TASK-062 验收第 2、3 条「当前导航项为店铺装修」「按 UX 顺序只含已上线的订单、退款、库存重置与店铺装修四项（桌面导航与 ☰ 菜单同样）」：
  // 经路由打开本页，桌面导航与 ☰ 菜单都依次为这四项，店铺装修标 aria-current="page"，没有其他导航项。
  it.each(LANGUAGES)("marks the store design item as current, after the other three, in %s", (language) => {
    const html = renderApp(STORE_DESIGN_PATH, language);
    for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
      const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
      expect(items.map((tag) => [attributes(tag).get("href"), attributes(tag).get("aria-current")])).toEqual([
        [ORDERS_PATH, undefined],
        [REFUNDS_PATH, undefined],
        [STOCK_RESETS_PATH, undefined],
        [STORE_DESIGN_PATH, "page"],
      ]);
      expect(textNodes(nav).slice(0, 4)).toEqual([
        COPY["admin.nav_orders"][language],
        COPY["admin.nav_refunds"][language],
        COPY["admin.nav_stock_resets"][language],
        COPY["admin.nav_store_design"][language],
      ]);
    }
  });

  // UX A08 手机线框「☰  [admin.nav_store_design]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」；
  // SHOP-TASK-062 验收第 2 条「手机顶栏标题为 admin.nav_store_design」。
  it.each(LANGUAGES)("names the store design item in the phone bar in %s", (language) => {
    const html = renderApp(STORE_DESIGN_PATH, language);
    expect(element(html, "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_store_design"][language])}</span>`,
    );
  });
});

describe("content", () => {
  // UX A08 桌面线框「[admin.nav_store_design]」其下「[admin.design_demo_note]」、UX-COPY「admin.nav_store_design：A08 导航与页标题」
  // 与 SHOP-TASK-062 验收第 2 条「内容区为 h1 admin.nav_store_design 与其下的 admin.design_demo_note」：已登录时内容区只有这一个 h1，
  // 其后为演示说明，没有别的文字、表单、按钮或链接（表单由 SHOP-TASK-064 接上）。
  it.each(LANGUAGES)("shows only the title and the demo note in the content area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(attributes(tags(main, "main")[0] ?? "").get("aria-busy")).toBe("false");
    expect(tags(main, "h1")).toHaveLength(1);
    expect(element(main, "div", "site-admin-store-design")).toBe(
      `<div class="site-admin-store-design"><h1 class="acs-admin__h site-admin-store-design__title">${escapeHtml(COPY["admin.nav_store_design"][language])}</h1>` +
        `<p class="site-admin-store-design__note">${escapeHtml(COPY["admin.design_demo_note"][language])}</p></div>`,
    );
    expect(textNodes(main)).toEqual([COPY["admin.nav_store_design"][language], COPY["admin.design_demo_note"][language]]);
    expect(main).not.toMatch(/<(form|fieldset|input|select|button|a)\b/);
  });

  // UX A08「演示提示：★ [admin.demo_banner]；[admin.design_demo_note]（演示横幅与演示提示始终显示，这里不能关闭）」：
  // 本页同时有后台演示横幅与演示说明，二者都不带 hidden，也没有关闭它们的控件。
  it.each(LANGUAGES)("keeps the demo banner and the demo note in %s", (language) => {
    const html = renderSignedIn(language);
    expect(element(html, "div", "acs-admin__banner")).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    const note = element(html, "p", "site-admin-store-design__note");
    expect(attributes(tags(note, "p")[0] ?? "").has("hidden")).toBe(false);
    expect(textNodes(note)).toEqual([COPY["admin.design_demo_note"][language]]);
    expect(tags(element(html, "main", "acs-admin__main"), "button")).toEqual([]);
  });

  // SHOP-TASK-062 验收第 2 条「页面不发起自己的请求（表单由 SHOP-TASK-064 接上）」：页面源码不引入接口模块、不调用 fetch、没有 effect，
  // 打开时唯一的请求是框架读取会话（由 components/AdminFrame.test.tsx 测试）。
  it("makes no requests of its own", () => {
    expect(pageSource).not.toMatch(/from "\.\.\/api\//);
    expect(pageSource).not.toMatch(/\bfetch\s*\(/);
    expect(pageSource).not.toMatch(/\buseEffect\b/);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-062 验收第 4 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：
  // 经路由打开（会话未返回）、已登录（菜单收起与展开），每段文字（含 aria-label）都是当前语言的字典文案；页面没有 title 或 placeholder。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    const pages = [renderApp(STORE_DESIGN_PATH, language), renderSignedIn(language), renderSignedIn(language, { menuOpen: true })];
    for (const html of pages) {
      expect(html).not.toMatch(/\s(title|placeholder)=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
