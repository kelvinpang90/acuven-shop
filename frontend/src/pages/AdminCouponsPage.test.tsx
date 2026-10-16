import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { AdminCoupon, AdminCouponPage } from "../api/adminCoupons";
import AdminCouponsPage, { AdminCouponsContent, AdminCouponsView, disableStep, formatCouponDate, listStep, pagerOf } from "./AdminCouponsPage";

const COUPONS_PATH = "/admin/coupons";
const NAV_PATHS = ["/admin/orders", "/admin/refunds", COUPONS_PATH, "/admin/stock-resets", "/admin/store-design"];

function storage(language: Language) {
  return { getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null), setItem: () => undefined };
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={COUPONS_PATH} storage={storage(language)} />);
}

function renderContent(language: Language): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={COUPONS_PATH}>
        <AdminCouponsContent />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((match) => match[0]);
}

function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const match of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((match[1] ?? "").toLowerCase(), match[2] ?? "");
  }
  return found;
}

function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((match) => {
    return (attributes(match[0]).get("class") ?? "").split(/\s+/).includes(className);
  })?.index;
  if (start === undefined) {
    throw new Error(`not found: <${name} class="${className}">`);
  }
  const pattern = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  pattern.lastIndex = start;
  let depth = 0;
  for (let match = pattern.exec(html); match !== null; match = pattern.exec(html)) {
    depth += match[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, match.index + match[0].length);
    }
  }
  throw new Error(`unclosed: <${name} class="${className}">`);
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

const FIXED: AdminCoupon = { id: 1, code: "WELCOME", type: "fixed", amount_off_sen: 1000, percent_off: null, min_subtotal_sen: 0, starts_on: "2026-01-01", ends_on: "2026-12-31", total_use_limit: 100, per_member_use_limit: null, used_count: 12, active: true };
const PERCENT: AdminCoupon = { id: 2, code: "SAVE20", type: "percent", amount_off_sen: null, percent_off: 20, min_subtotal_sen: 0, starts_on: "2026-10-05", ends_on: "2026-10-05", total_use_limit: null, per_member_use_limit: null, used_count: 0, active: false };
function page(overrides: Partial<AdminCouponPage> = {}): AdminCouponPage { return { total: 2, page: 1, page_size: 30, coupons: [FIXED, PERCENT], csrf_token: "csrf", ...overrides }; }
function renderView(value = page(), disabling: number | null = null, language: Language = "en"): string {
  return renderToStaticMarkup(<LanguageProvider storage={storage(language)}><RouterProvider initialPath={COUPONS_PATH}><AdminCouponsView state={{ busy: false, list: value, error: null }} disabling={disabling} onDisable={() => undefined} onPage={() => undefined} /></RouterProvider></LanguageProvider>);
}

describe("AdminCouponsPage", () => {
  // UX A05「入口：后台导航」及 SHOP-TASK-100 验收：经路由打开页面渲染后台框架，不套前台框架。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    const html = renderApp(language);
    expect(html).toContain(COPY["admin.demo_banner"][language]);
    expect(html).not.toContain(COPY["common.demo_banner"][language]);
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX「管理后台总体」及 SHOP-TASK-100 验收：优惠券当前项位于退款之后，五项顺序在桌面与手机菜单相同。
  it.each(LANGUAGES)("marks coupons current in both navigation menus in UX order in %s", (language) => {
    const html = renderToStaticMarkup(
      <LanguageProvider storage={storage(language)}>
        <RouterProvider initialPath={COUPONS_PATH}>
          <AdminCouponsPage />
        </RouterProvider>
      </LanguageProvider>,
    );
    const menus = [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")];
    for (const nav of menus) {
      const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
      expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
      expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual([undefined, undefined, "page", undefined, undefined]);
    }
    expect(html).toContain(`<span class="site-admin__bar-title">${COPY["admin.nav_coupons"][language]}</span>`);
  });

  // SHOP-TASK-102 验收「SHOP-TASK-100 的第三条」：列表加入后仍必须以原样标题和说明开头。
  it.each(LANGUAGES)("keeps the dictionary title and note at the start of content in %s", (language) => {
    expect(renderContent(language)).toContain(`<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p>`);
  });

  // UX A05 列表：固定金额、百分比、完整起止日期、已用上限与启停状态均按服务端记录显示。
  it("renders A05 table and phone cards, including the unlimited mark", () => {
    const html = renderView();
    for (const key of ["admin.coupon_code", "admin.coupon_type", "admin.coupon_value", "admin.coupon_valid", "admin.coupon_usage", "admin.filter_status"] as const) expect(html).toContain(COPY[key].en);
    expect(html).toContain("RM 10.00"); expect(html).toContain("20%"); expect(html).toContain("12 / 100"); expect(html).toContain("0 / —");
    expect(html).toContain(COPY["admin.coupon_enabled"].en); expect(html).toContain(COPY["admin.coupon_disabled"].en);
    expect(html.match(new RegExp(`<button[^>]*>${COPY["admin.coupon_disable"].en}</button>`, "g"))?.length).toBe(2);
  });

  // A05 的日期是营业日期；用 UTC 日期构造避免访客时区把日期移到前一天或后一天。
  it.each(LANGUAGES)("formats coupon dates without a visitor time-zone shift in %s", (language) => {
    expect(formatCouponDate("2026-10-05", language)).toContain("2026");
    expect(renderView(page({ coupons: [PERCENT] }), null, language)).toContain(escapeHtml(formatCouponDate("2026-10-05", language)));
  });

  // A02 沿用的翻页规则：每页 30 条，只有总数超出页面大小才出现前后按钮。
  it("calculates the previous and next coupon pages", () => {
    expect(pagerOf(page({ total: 30 }))).toBeNull();
    expect(pagerOf(page({ total: 31, page: 1 }))).toEqual({ prev: null, next: 2 });
    expect(pagerOf(page({ total: 61, page: 2 }))).toEqual({ prev: 1, next: 3 });
  });

  // 本任务读取与停用结果分类：无会话去登录，网络与其他失败使用规定的字典提示，CSRF/不存在重新读取。
  it("maps list and disable results to the required states", () => {
    expect(listStep({ kind: "network" })).toMatchObject({ error: "common.network_check" });
    expect(listStep({ kind: "failed" })).toMatchObject({ error: "common.error_retry" });
    expect(listStep({ kind: "none" })).toBe("login");
    expect(disableStep({ kind: "csrf" })).toEqual({ kind: "reload" });
    expect(disableStep({ kind: "not_found" })).toEqual({ kind: "reload" });
    expect(disableStep({ kind: "none" })).toBe("login");
  });

  // 本任务停用中限制为一张：所有按钮禁用，但只有目标券的行和卡片为 busy。
  it("marks only the selected coupon busy while disabling every disable button", () => {
    const html = renderView(page(), FIXED.id);
    expect((html.match(/aria-busy="true"/g) ?? []).length).toBe(2);
    expect((html.match(/disabled=""/g) ?? []).length).toBe(2);
  });
});
