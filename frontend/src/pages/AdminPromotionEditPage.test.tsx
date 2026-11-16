import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_PROMOTION_NEW_PATH, adminPromotionPath, matchRoute, resolvePath, RouterProvider } from "../router";
import AdminPromotionEditPage, { AdminPromotionEditContent } from "./AdminPromotionEditPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PROMOTIONS_PATH = "/admin/promotions";
const NEW_PATH = "/admin/promotions/new";
const EDIT_PATH = "/admin/promotions/7";
// 编辑页的两条路由：新建与某个活动（段值为活动的内部 ID）。
const EDIT_PATHS = [NEW_PATH, EDIT_PATH] as const;
const NAV_PATHS = [
  "/admin/orders",
  "/admin/refunds",
  "/admin/stripe-pending",
  "/admin/products",
  "/admin/categories",
  "/admin/coupons",
  PROMOTIONS_PATH,
  "/admin/shipping",
  "/admin/stock-resets",
  "/admin/store-design",
  "/admin/settings",
];
const NAV_KEYS = [
  "admin.nav_orders",
  "admin.nav_refunds",
  "admin.nav_stripe_pending",
  "admin.nav_products",
  "admin.nav_categories",
  "admin.nav_coupons",
  "admin.nav_promotions",
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

function wrap(children: ReactNode, language: Language, path: string): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={path}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(path: string, language: Language): string {
  return renderToStaticMarkup(<App initialPath={path} storage={storage(language)} />);
}

// 已登录（会话 200）时的后台框架：内容区为本页的内容。
function renderSignedIn(language: Language, menuOpen = false): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="promotions"
        state={SIGNED_IN}
        busy={false}
        menuOpen={menuOpen}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        <AdminPromotionEditContent />
      </AdminFrameView>,
      language,
      EDIT_PATH,
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
  throw new Error(`unclosed <${name}> with class ${className}`);
}

describe("AdminPromotionEditPage routes", () => {
  // UX 0.20 页面地图「A12 | 后台促销活动 | /admin/promotions、/admin/promotions/<活动 ID>、…」与 A12「编辑区（桌面，/admin/promotions/<活动 ID>；
  // 新建时同一版式）」：新建为 /admin/promotions/new（排在 :id 之前，new 不当作活动 ID），编辑的段值为活动的内部 ID；两者都不带查询参数。
  it("matches the new page before the promotion ID", () => {
    expect(ADMIN_PROMOTION_NEW_PATH).toBe(NEW_PATH);
    expect(matchRoute(NEW_PATH)).toEqual({ pattern: NEW_PATH, params: {} });
    expect(adminPromotionPath(7)).toBe(EDIT_PATH);
    expect(matchRoute(adminPromotionPath(7))).toEqual({ pattern: "/admin/promotions/:id", params: { id: "7" } });
  });

  // 任务验收「不合法的段值也渲染编辑页（由 SHOP-TASK-270 处理），未知路径仍落到首页」：段值不是活动 ID 时仍进编辑页（后台框架、当前项为促销活动）；
  // 多一段的地址不在路由表里，落到首页。
  it.each(["/admin/promotions/abc", "/admin/promotions/0", "/admin/promotions/-1"])("renders the edit page for %s", (path) => {
    expect(matchRoute(path)?.pattern).toBe("/admin/promotions/:id");
    const html = renderApp(path, "en");
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(element(html, "span", "site-admin__bar-title")).toBe(`<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_promotions"].en)}</span>`);
    expect(matchRoute("/admin/promotions/7/edit")).toBeNull();
    expect(resolvePath("/admin/promotions/7/edit")).toBe("/");
  });
});

describe("AdminPromotionEditPage", () => {
  // UX「管理后台总体」「每页顶部常驻 [admin.demo_banner]」：新建与编辑经路由打开都渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），
  // 没有前台页头、页脚与前台演示横幅。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    for (const path of EDIT_PATHS) {
      const html = renderApp(path, language);
      expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
      expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
      expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
      expect(html).not.toMatch(/<(header|footer)\b/);
    }
  });

  // UX 0.20「管理后台总体」「导航项依次为 …、[admin.nav_coupons]、[admin.nav_promotions]（0.20）、[admin.nav_shipping]、…」
  // 与 A12「入口：后台导航 [admin.nav_promotions]（排在 [admin.nav_coupons] 之后）」：新建与编辑页上，桌面导航与 ☰ 菜单都依次为这十一项，
  // 促销活动（指向列表 /admin/promotions）排在优惠券之后、运费之前并标 aria-current="page"，其他项不标。
  it.each(LANGUAGES)("marks promotions current, after coupons and before shipping, in both navigation menus in %s", (language) => {
    const pages = [
      ...EDIT_PATHS.map((path) => renderApp(path, language)),
      ...EDIT_PATHS.map((path) => renderToStaticMarkup(wrap(<AdminPromotionEditPage />, language, path))),
      renderSignedIn(language, true),
    ];
    for (const html of pages) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual(
          NAV_PATHS.map((path) => (path === PROMOTIONS_PATH ? "page" : undefined)),
        );
        const labels = textNodes(nav).slice(0, 11);
        expect(labels).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
        expect(labels.indexOf(COPY["admin.nav_coupons"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_promotions"][language]));
        expect(labels.indexOf(COPY["admin.nav_promotions"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_shipping"][language]));
      }
    }
  });

  // UX「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」：新建与编辑页的手机顶栏名称都为 admin.nav_promotions。
  it.each(LANGUAGES)("names the promotions item in the phone bar in %s", (language) => {
    for (const path of EDIT_PATHS) {
      expect(element(renderApp(path, language), "span", "site-admin__bar-title")).toBe(
        `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_promotions"][language])}</span>`,
      );
    }
  });

  // 任务目的「内容区只有标题 admin.nav_promotions」（编辑区由 SHOP-TASK-270 起加入）：已登录时内容区只有 h1 admin.nav_promotions
  // （class acs-admin__h）；会话返回前内容区为空。
  it.each(LANGUAGES)("shows only the title in the content area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(main).toBe(`${tags(main, "main")[0] ?? ""}<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_promotions"][language])}</h1></main>`);
    for (const path of EDIT_PATHS) {
      expect(element(renderApp(path, language), "main", "acs-admin__main")).not.toMatch(/<h1\b/);
    }
  });

  // 任务目的「不调用接口」：渲染本页内容（会话返回前与已登录）不发任何请求（后台框架自己的会话读取在挂载后才发，服务端渲染不跑）。
  it("renders without any request", () => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    for (const language of LANGUAGES) {
      for (const path of EDIT_PATHS) {
        renderApp(path, language);
      }
      renderSignedIn(language);
    }
    expect(request).not.toHaveBeenCalled();
  });

  // UX「管理后台总体」「表格列头、状态值、按钮与区块标题同样都用 `[key]`」：会话返回前、已登录（菜单收起与展开）时，
  // 每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const html of [...EDIT_PATHS.map((path) => renderApp(path, language)), renderSignedIn(language), renderSignedIn(language, true)]) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
