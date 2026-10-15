import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { AdminCoupon, AdminCouponPage } from "../api/adminCoupons";
import AdminCouponsPage, {
  AdminCouponsContent,
  AdminCouponsView,
  disableStep,
  formatBusinessDate,
  INITIAL_DISABLE,
  listStep,
  pagerOf,
  replaceCoupon,
} from "./AdminCouponsPage";

const COUPONS_PATH = "/admin/coupons";
const NAV_PATHS = ["/admin/orders", "/admin/refunds", COUPONS_PATH, "/admin/stock-resets", "/admin/store-design"];
const ACTIVE: AdminCoupon = { id: 7, code: "WELCOME10", type: "fixed", amount_off_sen: 1000, percent_off: null, min_subtotal_sen: 0, starts_on: "2026-01-01", ends_on: "2026-12-31", total_use_limit: 100, per_member_use_limit: null, used_count: 12, active: true };
const DISABLED: AdminCoupon = { ...ACTIVE, id: 8, code: "OLD10", active: false };
const PAGE: AdminCouponPage = { total: 31, page: 1, page_size: 30, coupons: [ACTIVE, DISABLED], csrf_token: "csrf" };

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

function renderView(language: Language, page: AdminCouponPage = PAGE, busyId: number | null = null): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={COUPONS_PATH}>
        <AdminCouponsView state={{ busy: false, list: page, error: null }} disable={{ busyId, error: null }} onPage={() => undefined} onDisable={() => undefined} />
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

  // SHOP-TASK-102 验收：列表加入后，SHOP-TASK-100 的标题与说明仍是内容区的原样开头。
  it.each(LANGUAGES)("starts the content with the dictionary title and listed note in %s", (language) => {
    expect(renderContent(language)).toStartWith(
      `<div class="site-admin-coupons"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p>`,
    );
  });

  // UX A05 与本任务验收：桌面列和手机卡都显示券代码、金额、状态、使用次数及完整的营业日期。
  it("renders fixed values, unlimited usage and only active coupon disable buttons", () => {
    const html = renderView("en", { ...PAGE, coupons: [{ ...ACTIVE, total_use_limit: null }, DISABLED] });
    expect(html).toContain("RM 10.00");
    expect(html).toContain("12 / —");
    expect(html).toContain("Used / limit 12 / — · Jan 1, 2026 – Dec 31, 2026");
    expect([...html.matchAll(/<button[^>]*>Disable<\/button>/g)]).toHaveLength(2);
    expect(html).toContain("Disabled");
  });

  // 本任务验收：读取状态区可供读屏获知，空页仍保留桌面表头而手机没有卡片。
  it("keeps table headers for an empty page and marks a read in progress busy", () => {
    const html = renderToStaticMarkup(
      <LanguageProvider storage={storage("en")}>
        <RouterProvider initialPath={COUPONS_PATH}>
          <AdminCouponsView state={{ busy: true, list: { ...PAGE, total: 0, coupons: [] }, error: null }} disable={INITIAL_DISABLE} onPage={() => undefined} onDisable={() => undefined} />
        </RouterProvider>
      </LanguageProvider>,
    );
    expect(html).toContain('class="site-admin-coupons__list" aria-busy="true"');
    expect(html).toContain("<th>Coupon code</th>");
    expect(html).not.toContain("site-admin-coupons__card");
  });

  // 本任务验收：日期不按访问者时区偏移，翻页边界与所有读取结果、停用结果映射为规定状态。
  it("maps date, pagination, reads and disable outcomes without altering unrelated coupons", () => {
    expect(formatBusinessDate("2026-01-01", "en")).toBe("Jan 1, 2026");
    expect(pagerOf(PAGE)).toEqual({ prev: null, next: 2 });
    expect(listStep({ kind: "network" })).toEqual({ busy: false, list: null, error: "common.network_check" });
    expect(listStep({ kind: "none" })).toBe("login");
    expect(disableStep({ kind: "csrf" })).toEqual({ kind: "reload" });
    expect(disableStep({ kind: "network" })).toEqual({ kind: "error", error: "common.network_check" });
    expect(replaceCoupon(PAGE, { ...ACTIVE, active: false }).coupons).toEqual([{ ...ACTIVE, active: false }, DISABLED]);
  });
});
