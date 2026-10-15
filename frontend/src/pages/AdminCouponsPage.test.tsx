import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminCoupon, AdminCouponPage } from "../api/adminCoupons";
import App from "../App";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import {
  default as AdminCouponsPage,
  AdminCouponsView,
  INITIAL_LIST,
  disableCoupon,
  disableState,
  disableStep,
  formatCouponDate,
  listStep,
  loadCoupons,
  pagerOf,
  reloadedDisableState,
} from "./AdminCouponsPage";
import type { DisableStep, ListState, Moves } from "./AdminCouponsPage";

const COUPON: AdminCoupon = {
  id: 7,
  code: "SAVE10",
  type: "fixed",
  amount_off_sen: 1000,
  percent_off: null,
  min_subtotal_sen: 0,
  starts_on: "2026-10-01",
  ends_on: "2026-12-31",
  total_use_limit: null,
  per_member_use_limit: null,
  used_count: 12,
  active: true,
};
const DISABLED: AdminCoupon = {
  ...COUPON,
  id: 8,
  code: "TEN",
  type: "percent",
  amount_off_sen: null,
  percent_off: 10,
  active: false,
  total_use_limit: 100,
};
const PAGE: AdminCouponPage = {
  total: 31,
  page: 1,
  page_size: 30,
  coupons: [COUPON, DISABLED],
  csrf_token: "csrf",
};
const COUPONS_PATH = "/admin/coupons";
const NAV_PATHS = ["/admin/orders", "/admin/refunds", COUPONS_PATH, "/admin/stock-resets", "/admin/store-design"];

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={COUPONS_PATH} storage={storage(language)} />);
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
    throw new Error(`not found: ${className}`);
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
  throw new Error(`unclosed: ${className}`);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

function render(state: ListState, disabling: number | null = null, language: Language = "en"): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={COUPONS_PATH}>
        <AdminCouponsView state={state} disabling={disabling} onDisable={() => undefined} onPage={() => undefined} />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function moves<T>(): { target: Moves<T>; shown: T[]; paths: string[] } {
  const shown: T[] = [];
  const paths: string[] = [];
  return {
    target: {
      show: (value) => {
        shown.push(value);
      },
      replace: (path) => {
        paths.push(path);
      },
    },
    shown,
    paths,
  };
}

function disableButtons(html: string): string[] {
  return [...html.matchAll(/<button\b[^>]*>Disable<\/button>/g)].map((match) => match[0]);
}

function visibleText(html: string): string[] {
  return html
    .replace(/<[^>]+>/g, "\n")
    .split("\n")
    .map((value) => value.trim())
    .filter(Boolean);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

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

  // SHOP-TASK-100 原测试断言内容区只有标题和说明；SHOP-TASK-102 在其后加入列表，故只守住原样的开头。
  it.each(LANGUAGES)("starts content with the dictionary title and listed note in %s", (language) => {
    const content = element(
      render({ busy: false, list: { ...PAGE, coupons: [] }, error: null }, null, language),
      "div",
      "site-admin-coupons",
    );
    expect(content.startsWith(`<div class="site-admin-coupons"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p>`)).toBe(true);
  });

  // UX A05：桌面列头和手机卡片显示代码、面额、状态、已用/上限、有效期；手机不另显示类型。
  it("renders the A05 rows, cards, limits, dates, and active-only disable button", () => {
    const html = render({ busy: false, list: PAGE, error: null });
    for (const key of ["admin.coupon_code", "admin.coupon_type", "admin.coupon_value", "admin.coupon_valid", "admin.coupon_usage", "admin.filter_status"] as const) {
      expect(html).toContain(COPY[key].en);
    }
    expect(html).toContain("RM 10.00");
    expect(html).toContain("10%");
    expect(html).toContain("12 / —");
    expect(html).toContain("12 / 100");
    expect(html).toContain(formatCouponDate("2026-10-01", "en"));
    expect(html).toContain(COPY["admin.coupon_enabled"].en);
    expect(html).toContain(COPY["admin.coupon_disabled"].en);
    expect(disableButtons(html)).toHaveLength(2);
  });

  // 本任务验收：营业日期按 UTC 日期格式化，不随访客时区偏移。
  it("formats both complete coupon dates without a browser timezone shift", () => {
    expect(formatCouponDate("2026-01-01", "en")).toBe("Jan 1, 2026");
    expect(formatCouponDate("2026-10-01", "zh")).toContain("2026");
  });

  // UX A02 做法：空列表保留桌面表头，不渲染手机卡片或额外空状态文字。
  it("shows headers only when no coupons exist", () => {
    const html = render({ busy: false, list: { ...PAGE, total: 0, coupons: [] }, error: null });
    expect(html).toContain("<tbody></tbody>");
    expect(html).not.toContain("site-admin-coupons__card");
    expect(html).not.toContain('role="alert"');
  });

  // 本任务验收：读取进行中列表区 aria-busy；错误文案只能来自字典。
  it("marks reads busy and renders the prescribed failures", () => {
    expect(render(INITIAL_LIST)).toContain('aria-busy="true"');
    expect(render({ busy: false, list: null, error: "common.network_check" })).toContain(COPY["common.network_check"].en);
    expect(render({ busy: false, list: null, error: "common.error_retry" })).toContain(COPY["common.error_retry"].en);
  });

  // UX A02 翻页规则：超过 30 条才有 pager，首页和末页分别禁用上一页、下一页。
  it("calculates the previous and next coupon pages", () => {
    expect(pagerOf({ ...PAGE, total: 30 })).toBeNull();
    expect(pagerOf(PAGE)).toEqual({ prev: null, next: 2 });
    expect(pagerOf({ ...PAGE, page: 2 })).toEqual({ prev: 1, next: null });
  });

  // 本任务验收：读取的 ok、none、network、其他失败分别映射为列表、登录与两种字典提示。
  it("maps list replies to the required states", () => {
    expect(listStep({ kind: "ok", page: PAGE })).toEqual({ busy: false, list: PAGE, error: null });
    expect(listStep({ kind: "none" })).toBe("login");
    expect(listStep({ kind: "network" })).toEqual({ busy: false, list: null, error: "common.network_check" });
    expect(listStep({ kind: "failed" })).toEqual({ busy: false, list: null, error: "common.error_retry" });
  });

  // 本任务验收：停用 ok 直接更新行，csrf/not_found 重读；其他结果保留规定的错误或进入登录。
  it("maps every disable reply to its required transition", () => {
    expect(disableStep({ kind: "ok", coupon: DISABLED })).toEqual({ kind: "update", coupon: DISABLED });
    expect(disableStep({ kind: "csrf" })).toEqual({ kind: "reload" });
    expect(disableStep({ kind: "not_found" })).toEqual({ kind: "reload" });
    expect(disableStep({ kind: "none" })).toBe("login");
    expect(disableStep({ kind: "network" })).toEqual({ kind: "error", error: "common.network_check" });
    expect(disableStep({ kind: "failed" })).toEqual({ kind: "error", error: "common.error_retry" });
  });

  // 本任务验收「ok 时以返回的券更新该行」「not_found 与 csrf 重新读取当前页，读取完成后仍显示 common.error_retry」。
  it("updates a disabled row and preserves retry after a required reload", () => {
    const activePage = { busy: false, list: PAGE, error: null } satisfies ListState;
    const returned = { ...COUPON, active: false };
    const updated = disableState(activePage, { kind: "update", coupon: returned });
    expect(updated.list?.coupons).toEqual([returned, DISABLED]);
    expect(disableButtons(render(updated))).toHaveLength(0);
    expect(disableState(activePage, { kind: "reload" })).toEqual({ busy: true, list: PAGE, error: "common.error_retry" });
    expect(reloadedDisableState({ kind: "ok", page: PAGE })).toEqual({ busy: false, list: PAGE, error: "common.error_retry" });
    expect(reloadedDisableState({ kind: "network" })).toEqual({ busy: false, list: null, error: "common.error_retry" });
    expect(render(disableState(activePage, { kind: "error", error: "common.network_check" }))).toContain(COPY["common.network_check"].en);
    expect(render(disableState(activePage, { kind: "error", error: "common.error_retry" }))).toContain(COPY["common.error_retry"].en);
  });

  // 本任务验收：异步读取携带页码与 AbortSignal，并在取消后不再更新页面。
  it("loads the requested page and ignores an aborted result", async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, PAGE));
    vi.stubGlobal("fetch", fetch);
    const target = moves<ListState>();
    await loadCoupons(1, new AbortController().signal, target.target);
    await loadCoupons(2, new AbortController().signal, target.target);
    expect(fetch.mock.calls.map((call) => call[0])).toEqual(["/api/admin/coupons?page=1", "/api/admin/coupons?page=2"]);
    const cancelled = moves<ListState>();
    const signal = new AbortController();
    const pending = loadCoupons(2, signal.signal, cancelled.target);
    signal.abort();
    await pending;
    expect(cancelled.shown).toEqual([]);
  });

  // 本任务验收「none 用路由的 replace 进入 /admin/login」：列表读取把 401 的结果交给路由替身。
  it("replaces the route when a list read has no session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(401, {})));
    const target = moves<ListState>();
    await loadCoupons(1, new AbortController().signal, target.target);
    expect(target.paths).toEqual(["/admin/login"]);
  });

  // 本任务验收：停用进行中禁用该券按钮并标 aria-busy，其他启用券按钮仍可用。
  it("marks only the pending active coupon disabled and busy", () => {
    const secondActive = { ...COUPON, id: 9, code: "SAVE20" };
    const html = render({ busy: false, list: { ...PAGE, coupons: [COUPON, secondActive] }, error: null }, COUPON.id);
    const buttons = disableButtons(html).map(attributes);
    expect(buttons).toHaveLength(4);
    expect(buttons.filter((button) => button.get("aria-busy") === "true" && button.has("disabled"))).toHaveLength(2);
    expect(buttons.filter((button) => button.get("aria-busy") === "false" && !button.has("disabled"))).toHaveLength(2);
  });

  // 本任务验收：停用使用内部 ID；none replace 到登录，取消后的结果不更新页面。
  it("disables by internal id and observes login and abort transitions", async () => {
    const fetch = vi.fn().mockResolvedValue(response(401, {}));
    vi.stubGlobal("fetch", fetch);
    const target = moves<DisableStep>();
    const controller = new AbortController();
    await disableCoupon(COUPON.id, "csrf", controller.signal, target.target);
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/admin/coupons/7/disable");
    expect(target.paths).toEqual(["/admin/login"]);
    const stopped = moves<DisableStep>();
    const signal = new AbortController();
    const pending = disableCoupon(COUPON.id, "csrf", signal.signal, stopped.target);
    signal.abort();
    await pending;
    expect(stopped.shown).toEqual([]);
  });

  // 本任务验收「页面文字全部来自字典」：券数据、格式化金额日期与视觉稿给定符号以外，没有自写界面文字。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const coupon of [COUPON, DISABLED]) {
      const value = coupon.type === "fixed" ? "RM 10.00" : "10%";
      const limit = coupon.total_use_limit === null ? "—" : String(coupon.total_use_limit);
      const valid = `${formatCouponDate(coupon.starts_on, language)} – ${formatCouponDate(coupon.ends_on, language)}`;
      allowed.add(coupon.code);
      allowed.add(String(coupon.used_count));
      allowed.add(limit);
      allowed.add(`${coupon.used_count} / ${limit}`);
      allowed.add(formatCouponDate(coupon.starts_on, language));
      allowed.add(formatCouponDate(coupon.ends_on, language));
      allowed.add(`${coupon.code} · ${value}`);
      allowed.add(valid);
      allowed.add(`${COPY["admin.coupon_usage"][language]} ${coupon.used_count} / ${limit} · ${valid}`);
    }
    allowed.add("RM 10.00");
    allowed.add("10%");
    for (const value of visibleText(render({ busy: false, list: PAGE, error: null }, null, language))) {
      expect(allowed.has(value) || ["·", "–", "/", "‹", "›"].includes(value), value).toBe(true);
    }
  });
});
