import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

// 经 Vite 的 ?raw 读成字符串（与 i18n/copy.test.ts 相同），用来找出 UX-COPY 里全部 admin.sms_* 文案。
import uxCopy from "../../../docs/UX-COPY.md?raw";
import App from "../App";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import AdminSiteSettingsPage, { AdminSiteSettingsContent } from "./AdminSiteSettingsPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const SETTINGS_PATH = "/admin/settings";
const NAV_PATHS = ["/admin/orders", "/admin/refunds", "/admin/coupons", "/admin/stock-resets", "/admin/store-design", SETTINGS_PATH];
const NAV_KEYS = ["admin.nav_orders", "admin.nav_refunds", "admin.nav_coupons", "admin.nav_stock_resets", "admin.nav_store_design", "admin.nav_settings"] as const;
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

// UX-COPY 里短信验证段（admin.sms_*）的三语文字。
function smsTexts(language: Language): string[] {
  const column = { en: 2, zh: 3, ms: 4 }[language];
  return [...uxCopy.matchAll(/^\| `(admin\.sms_[a-z_]+)` \|(.*)$/gm)].map((m) => (`|${m[2] ?? ""}`.split("|")[column - 1] ?? "").trim());
}

function storage(language: Language) {
  return { getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null), setItem: () => undefined };
}

function renderApp(language: Language = "en"): string {
  return renderToStaticMarkup(<App initialPath={SETTINGS_PATH} storage={storage(language)} />);
}

// 路由替身：RouterProvider 给定 /admin/settings，不读地址栏。
function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={SETTINGS_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

// 已登录（会话 200）时的后台框架与本页内容；服务端渲染不运行 effect，用框架的视图代替会话请求。
function renderSignedIn(language: Language, menuOpen = false): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="settings"
        state={SIGNED_IN}
        busy={false}
        menuOpen={menuOpen}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        <AdminSiteSettingsContent />
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

// 页面上访客能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributeTexts = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...attributeTexts].filter((value) => value !== "");
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

// 某个元素（按 class 找到的第一个）从开始标签到与之配对的结束标签的内容（数同名元素的嵌套）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) =>
    (attributes(m[0]).get("class") ?? "").split(/\s+/).includes(className),
  )?.index;
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
  throw new Error(`unclosed: <${name} class="${className}">`);
}

describe("route", () => {
  // UX A09「入口：后台导航 [admin.nav_settings]」与页面地图「A09 后台站点设置 /admin/settings」；SHOP-TASK-137 验收第 2 条
  // 「新文件 AdminSiteSettingsPage.tsx 用后台框架渲染」：经路由打开 /admin/settings，渲染后台框架（admin.demo_banner，会话请求返回前
  // 内容区为空并标 aria-busy），不套前台框架（没有页头、页脚与 common.demo_banner）。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    const html = renderApp(language);
    expect((attributes(html.slice(0, html.indexOf(">") + 1)).get("class") ?? "").split(/\s+/)).toContain("acs-admin");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(html).not.toMatch(/<(header|footer)\b/);
  });
});

describe("navigation", () => {
  // UX「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、[admin.nav_products]、[admin.nav_coupons]、
  // [admin.nav_shipping]、[admin.nav_stock_resets]、[admin.nav_store_design]（0.4）、[admin.nav_settings]（0.7）」与 Kelvin 2026-10-06
  // （docs/HANDOFF.md 0.35）「后台导航只显示已上线页面的导航项」：经路由打开本页，桌面导航与 ☰ 菜单都依次为订单、退款、优惠券、
  // 库存重置、店铺装修与站点设置六项，站点设置排在店铺装修之后并标 aria-current="page"，没有其他导航项。
  it.each(LANGUAGES)("marks site settings current, after store design, in both navigation menus in %s", (language) => {
    for (const html of [renderApp(language), renderToStaticMarkup(wrap(<AdminSiteSettingsPage />, language))]) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual([undefined, undefined, undefined, undefined, undefined, "page"]);
        expect(textNodes(nav).slice(0, 6)).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
      }
    }
  });

  // UX A09 手机线框「☰  [admin.nav_settings]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」；
  // SHOP-TASK-137 验收第 2 条「手机顶栏标题为 admin.nav_settings」。
  it.each(LANGUAGES)("names site settings in the phone bar in %s", (language) => {
    expect(element(renderApp(language), "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_settings"][language])}</span>`,
    );
  });
});

describe("content", () => {
  // UX-COPY「admin.nav_settings：A09 导航与页标题」与 SHOP-TASK-137 验收第 2 条「内容区只有 h1 admin.nav_settings」：
  // 已登录时内容区只有页标题，没有表单、按钮或其他文字（支付方式表单留给 SHOP-TASK-139）。
  it.each(LANGUAGES)("shows only the page title in the content area in %s", (language) => {
    const h1 = `<h1 class="acs-admin__h site-admin-settings__title">${escapeHtml(COPY["admin.nav_settings"][language])}</h1>`;
    expect(renderToStaticMarkup(wrap(<AdminSiteSettingsContent />, language))).toBe(`<div class="site-admin-settings">${h1}</div>`);
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(main).toBe(`<main class="acs-admin__main" aria-busy="false"><div class="site-admin-settings">${h1}</div></main>`);
    expect(textNodes(main)).toEqual([COPY["admin.nav_settings"][language]]);
  });

  // Kelvin 2026-10-07（docs/HANDOFF.md）「后台站点设置 A09（短信验证开关）的保存接口与页面等短信验证服务与会员结账路径上线后再做…
  // 在那之前不提供任何打开它的入口」，与 2026-10-15 认可的 A09 支付方式（本页先只放支付方式开关）：页面任何地方（含 ☰ 菜单展开时）
  // 都没有 UX-COPY 里 admin.sms_* 的文字，也没有单选或表单。
  it.each(LANGUAGES)("shows no SMS verification text in %s", (language) => {
    const texts = smsTexts(language);
    expect(texts.length).toBeGreaterThan(0);
    for (const html of [renderApp(language), renderSignedIn(language), renderSignedIn(language, true)]) {
      const shown = visibleTexts(html);
      for (const text of texts) {
        expect(text).not.toBe("");
        expect(shown).not.toContain(text);
      }
      expect(html).not.toMatch(/<(form|fieldset|input)\b/);
    }
  });

  // SHOP-TASK-137 验收第 2 条「页面不发起自己的请求（读取与保存由 SHOP-TASK-139 接上）」：渲染本页内容不调用 fetch
  // （后台框架自己的会话请求在挂载后才发，由 components/AdminFrame.test.tsx 测试）。
  it("makes no request of its own", () => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    renderToStaticMarkup(wrap(<AdminSiteSettingsContent />, "en"));
    renderSignedIn("en");
    expect(request).not.toHaveBeenCalled();
  });
});

describe("dictionary", () => {
  // SHOP-TASK-137 验收第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」与第 4 条「页面文字全部来自字典」：
  // 会话返回前、已登录（☰ 菜单收起与展开），每段文字（含 aria-label）都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const html of [renderApp(language), renderSignedIn(language), renderSignedIn(language, true)]) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
