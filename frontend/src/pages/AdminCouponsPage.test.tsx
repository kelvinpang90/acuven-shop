import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

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
  disableCoupon,
  formatBusinessDate,
  INITIAL_DISABLE,
  listStep,
  loadCoupons,
  pagerOf,
  replaceCoupon,
} from "./AdminCouponsPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

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

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html.replace(/<!--[\s\S]*?-->/g, "\n").replace(/<[^>]*>/g, "\n").split("\n").map((value) => unescapeHtml(value).trim()).filter((value) => value !== "");
}

function visibleTexts(html: string): string[] {
  const labels = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((match) => unescapeHtml(match[1] ?? ""));
  return [...textNodes(html), ...labels].filter((value) => value !== "");
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call { url: string; init: RequestInit; }

function stubFetch(...replies: (Response | (() => never))[]): Call[] {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init: init ?? {} });
    const reply = queue.shift();
    if (reply === undefined) return Promise.reject(new Error("unexpected request"));
    return typeof reply === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
  }));
  return calls;
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

  // SHOP-TASK-102 验收：列表加入后，SHOP-TASK-100 原来逐字断言整个内容区会失效，故只守住标题与说明仍为原样开头。
  it.each(LANGUAGES)("starts the content with the dictionary title and listed note in %s", (language) => {
    const prefix = `<div class="site-admin-coupons"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p>`;
    expect(renderContent(language).startsWith(prefix)).toBe(true);
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

  // UX A05 的类型、面额和上限显示：固定金额带两位小数，百分比及有上限的用量分别有明确表示。
  it("renders both coupon types and a finite usage limit", () => {
    const percent: AdminCoupon = { ...ACTIVE, id: 9, type: "percent", amount_off_sen: null, percent_off: 15 };
    const html = renderView("en", { ...PAGE, coupons: [ACTIVE, percent] });
    expect(html).toContain(COPY["admin.coupon_fixed"].en);
    expect(html).toContain(COPY["admin.coupon_percent"].en);
    expect(html).toContain("RM 10.00");
    expect(html).toContain("15%");
    expect(html).toContain("12 / 100");
  });

  // 本任务验收的翻页规则：不足一页不显示翻页；第二页有上一页而最后一页没有下一页。
  it("calculates both pagination boundaries", () => {
    expect(pagerOf({ ...PAGE, total: 30 })).toBeNull();
    expect(pagerOf({ ...PAGE, page: 2, total: 31 })).toEqual({ prev: 1, next: null });
  });

  // 本任务验收的读取规则：按请求页读取，ok、失败和中止分别只作规定的状态变更。
  it("loads the requested page and ignores an aborted reply", async () => {
    const calls = stubFetch(json(200, { ...PAGE, page: 2 }), json(500, {}), json(200, PAGE));
    const states: unknown[] = [];
    const moves = { show: (state: unknown) => states.push(state), replace: (_path: string) => undefined };
    await loadCoupons(2, new AbortController().signal, moves);
    await loadCoupons(3, new AbortController().signal, moves);
    const aborted = new AbortController();
    const pending = loadCoupons(4, aborted.signal, moves);
    aborted.abort();
    await pending;
    expect(calls.map((call) => call.url)).toEqual(["/api/admin/coupons?page=2", "/api/admin/coupons?page=3", "/api/admin/coupons?page=4"]);
    expect(states).toEqual([{ busy: false, list: { ...PAGE, page: 2 }, error: null }, { busy: false, list: null, error: "common.error_retry" }]);
  });

  // 本任务验收的停用进行中状态：所有可停用按钮禁用，且只有被点的券向读屏器标明忙碌。
  it("marks only the disabled coupon busy while disabling", () => {
    const html = renderView("en", { ...PAGE, coupons: [ACTIVE, { ...ACTIVE, id: 10, code: "NEXT" }] }, ACTIVE.id);
    const buttons = [...html.matchAll(/<button[^>]*>Disable<\/button>/g)].map((match) => match[0]);
    expect(buttons).toHaveLength(4);
    expect(buttons.filter((tag) => attributes(tag).has("disabled"))).toHaveLength(4);
    expect(buttons.filter((tag) => attributes(tag).get("aria-busy") === "true")).toHaveLength(2);
  });

  // 本任务验收的停用结果：成功更新该行；csrf、not_found 会重新读取并保留 retry；其余结果按规定交给回调。
  it("dispatches every disable outcome without updating an aborted request", async () => {
    const updated = { ...ACTIVE, active: false };
    const calls = stubFetch(json(200, updated), json(404, {}), json(403, {}), json(401, {}), (() => { throw new Error("network"); }), json(500, {}), json(200, updated));
    const events: string[] = [];
    let retryAfterReload: string | null = null;
    const moves = {
      show: (coupon: AdminCoupon) => events.push(`show ${coupon.id}`),
      reload: () => { events.push("reload"); retryAfterReload = "common.error_retry"; },
      replace: (path: string) => events.push(`replace ${path}`),
      error: (error: string) => events.push(`error ${error}`),
    };
    for (let id = 1; id <= 6; id += 1) await disableCoupon(id, "csrf", new AbortController().signal, moves);
    const aborted = new AbortController();
    const pending = disableCoupon(7, "csrf", aborted.signal, moves);
    aborted.abort();
    await pending;
    expect(calls).toHaveLength(7);
    expect(events).toEqual(["show 7", "reload", "reload", "replace /admin/login", "error common.network_check", "error common.error_retry"]);
    expect(retryAfterReload).toBe("common.error_retry");
  });

  // 本任务验收「页面文字全部来自字典」：可见文本（包括还原的 &quot;）仅为字典、接口数据、格式化日期或视觉稿符号。
  it.each(LANGUAGES)("uses only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const coupon of [ACTIVE, DISABLED]) {
      allowed.add(coupon.code);
      allowed.add(formatBusinessDate(coupon.starts_on, language));
      allowed.add(formatBusinessDate(coupon.ends_on, language));
      allowed.add(String(coupon.used_count));
      allowed.add(String(coupon.total_use_limit));
      allowed.add("RM 10.00");
      allowed.add("12 / 100");
      allowed.add(`${COPY["admin.coupon_usage"][language]} 12 / 100 · ${formatBusinessDate(coupon.starts_on, language)} – ${formatBusinessDate(coupon.ends_on, language)}`);
    }
    allowed.add("‹"); allowed.add("›");
    for (const value of visibleTexts(renderView(language))) expect(allowed.has(value), value).toBe(true);
  });
});
