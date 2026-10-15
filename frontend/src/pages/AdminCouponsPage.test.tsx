import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import AdminCouponsPage, { AdminCouponsContent } from "./AdminCouponsPage";

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

function navLinks(html: string): string[] {
  return [...html.matchAll(/<a href="([^"]*)"(?: aria-current="[^"]*")?>/g)].map((match) => match[1] ?? "");
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
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
    const menus = html.match(/<nav class="(?:acs-admin__nav site-admin__nav|site-admin__menu-nav)">[\s\S]*?<\/nav>/g) ?? [];
    expect(menus).toHaveLength(2);
    for (const nav of menus) {
      expect(navLinks(nav)).toEqual(NAV_PATHS);
      expect(nav).toMatch(new RegExp(`href="${COUPONS_PATH}" aria-current="page">${COPY["admin.nav_coupons"][language]}</a>`));
    }
    expect(html).toContain(`<span class="site-admin__bar-title">${COPY["admin.nav_coupons"][language]}</span>`);
  });

  // UX A05「桌面」及 SHOP-TASK-100 验收：内容只有字典的 h1 与会员中心说明，不自行补写界面文字。
  it.each(LANGUAGES)("renders the dictionary title and listed note only in %s", (language) => {
    expect(renderContent(language)).toBe(
      `<div class="site-admin-coupons"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p></div>`,
    );
  });
});
