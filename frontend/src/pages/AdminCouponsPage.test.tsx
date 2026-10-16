import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminCoupon, AdminCouponPage } from "../api/adminCoupons";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminCouponsPage, { AdminCouponsContent, AdminCouponsView, disableCoupon, disableStep, formatCouponDate, INITIAL_LIST, listStep, loadCoupons, pagerOf } from "./AdminCouponsPage";
import type { DisableMoves, ListState, Moves } from "./AdminCouponsPage";

const COUPONS_PATH = "/admin/coupons";
const PATH = COUPONS_PATH;
const LOGIN = "/admin/login";
const NAV_PATHS = ["/admin/orders", "/admin/refunds", COUPONS_PATH, "/admin/stock-resets", "/admin/store-design"];
const FIXED: AdminCoupon = { id: 1, code: "WELCOME", type: "fixed", amount_off_sen: 1000, percent_off: null, min_subtotal_sen: 0, starts_on: "2026-01-01", ends_on: "2026-12-31", total_use_limit: 100, per_member_use_limit: null, used_count: 12, active: true };
const PERCENT: AdminCoupon = { id: 2, code: "SAVE20", type: "percent", amount_off_sen: null, percent_off: 20, min_subtotal_sen: 0, starts_on: "2026-10-05", ends_on: "2026-10-05", total_use_limit: null, per_member_use_limit: null, used_count: 0, active: false };

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function storage(language: Language) { return { getItem: (key: string) => key === LANGUAGE_STORAGE_KEY ? language : null, setItem: () => undefined }; }
function page(overrides: Partial<AdminCouponPage> = {}): AdminCouponPage { return { total: 2, page: 1, page_size: 30, coupons: [FIXED, PERCENT], csrf_token: "csrf", ...overrides }; }
function shown(list: AdminCouponPage): ListState { return { busy: false, list, error: null }; }
function wrap(element: ReactNode, language: Language = "en") { return <LanguageProvider storage={storage(language)}><RouterProvider initialPath={PATH}>{element}</RouterProvider></LanguageProvider>; }
function render(state: ListState = shown(page()), disabling: number | null = null, language: Language = "en") { return renderToStaticMarkup(wrap(<AdminCouponsView state={state} disabling={disabling} onDisable={() => undefined} onPage={() => undefined} />, language)); }
function escapeHtml(value: string) { return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;"); }
function unescapeHtml(value: string) { return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"); }
function textNodes(html: string) { return html.replace(/<!--[\s\S]*?-->/g, "\n").replace(/<[^>]*>/g, "\n").split("\n").map((value) => unescapeHtml(value).trim()).filter((value) => value !== ""); }
function visibleTexts(html: string) { return [...textNodes(html), ...[...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((match) => unescapeHtml(match[1] ?? ""))].filter((value) => value !== ""); }
function tags(html: string, name: string) { return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((match) => match[0]); }
function attributes(tag: string) { const found = new Map<string, string>(); for (const match of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) found.set((match[1] ?? "").toLowerCase(), unescapeHtml(match[2] ?? "")); return found; }
function element(html: string, name: string, className: string) { const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((match) => (attributes(match[0]).get("class") ?? "").split(/\s+/).includes(className))?.index; if (start === undefined) throw new Error(`not found: ${className}`); const re = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g"); re.lastIndex = start; let depth = 0; for (let match = re.exec(html); match !== null; match = re.exec(html)) { depth += match[0].startsWith("</") ? -1 : 1; if (depth === 0) return html.slice(start, match.index + match[0].length); } throw new Error("unclosed"); }
function json(status: number, body: unknown) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
interface Call { url: string; init: RequestInit; }
function stubFetch(...replies: (Response | (() => never))[]) { const calls: Call[] = []; const queue = [...replies]; vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => { calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} }); const reply = queue.shift(); if (reply === undefined) return Promise.reject(new Error("unexpected request")); return typeof reply === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply); })); return calls; }
const networkDown = (): never => { throw new TypeError("Failed to fetch"); };
function moves<T>() { const events: string[] = []; const states: T[] = []; const target: Moves<T> = { show: (state) => { states.push(state); events.push("show"); }, replace: (path: RoutePath) => { events.push(`replace ${path}`); } }; return { target, events, states }; }
function disableMoves() { const events: string[] = []; const updates: AdminCoupon[] = []; const target: DisableMoves = { update: (coupon) => { updates.push(coupon); events.push("update"); }, error: (error: CopyKey) => { events.push(`error ${error}`); }, reload: () => { events.push("reload"); }, finish: () => { events.push("finish"); }, replace: (path: RoutePath) => { events.push(`replace ${path}`); } }; return { target, events, updates }; }

describe("AdminCouponsPage", () => {
  // UX A05「入口：后台导航」及 SHOP-TASK-100 验收：经路由打开页面渲染后台框架，不套前台框架。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    const html = renderToStaticMarkup(<App initialPath={PATH} storage={storage(language)} />);
    expect(html).toContain(COPY["admin.demo_banner"][language]);
    expect(html).not.toContain(COPY["common.demo_banner"][language]);
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX「管理后台总体」及 SHOP-TASK-100 验收：优惠券当前项位于退款之后，五项顺序在桌面与手机菜单相同。
  it.each(LANGUAGES)("marks coupons current in both navigation menus in UX order in %s", (language) => {
    const html = renderToStaticMarkup(
      <LanguageProvider storage={storage(language)}>
        <RouterProvider initialPath={PATH}>
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
  // The former exact content assertion changes only because the list now follows: original h1 and note must begin content.
  it.each(LANGUAGES)("starts content with the original title and note in %s", (language) => { const html = renderToStaticMarkup(wrap(<AdminCouponsContent />, language)); expect(html).toStartWith(`<div class="site-admin-coupons"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p>`); });
  // UX A05: table/cards show all fields, full dates, limits including —, statuses, and the active-only disable button.
  it.each(LANGUAGES)("renders A05 rows and cards in %s", (language) => { const html = render(undefined, null, language); for (const key of ["admin.coupon_code", "admin.coupon_type", "admin.coupon_value", "admin.coupon_valid", "admin.coupon_usage", "admin.filter_status", "admin.coupon_fixed", "admin.coupon_percent", "admin.coupon_enabled", "admin.coupon_disabled"] as const) expect(html).toContain(escapeHtml(COPY[key][language])); expect(html).toContain("RM 10.00"); expect(html).toContain("20%"); expect(html).toContain("12 / 100"); expect(html).toContain("0 / —"); expect(html.match(new RegExp(`<button[^>]*>${escapeHtml(COPY["admin.coupon_disable"][language])}</button>`, "g"))?.length).toBe(2); });
  // Kelvin's date decision: business date October 5 remains the fifth, not a visitor-time-zone neighbor.
  it.each(LANGUAGES)("keeps October fifth in %s", (language) => { expect(formatCouponDate("2026-10-05", language)).toContain("5"); expect(render(shown(page({ coupons: [PERCENT] })), null, language)).toContain(escapeHtml(formatCouponDate("2026-10-05", language))); });
  // A02 paging applies to A05, and the selected page is sent to the list request.
  it("calculates and requests pages", async () => { expect(pagerOf(page({ total: 30 }))).toBeNull(); expect(pagerOf(page({ total: 31 }))).toEqual({ prev: null, next: 2 }); expect(pagerOf(page({ total: 61, page: 2 }))).toEqual({ prev: 1, next: 3 }); const calls = stubFetch(json(200, page({ page: 3 }))); const target = moves<ListState>(); await loadCoupons(3, new AbortController().signal, target.target); expect(calls.map((call) => call.url)).toEqual(["/api/admin/coupons?page=3"]); });
  // A05's empty state follows A02: headers only and no cards or invented copy.
  it("shows only headers for an empty list", () => { const area = element(render(shown(page({ total: 0, coupons: [] }))), "div", "site-admin-coupons__list"); expect(area).toContain("<tbody></tbody>"); expect(area).not.toMatch(/<(li|ul|button)\b/); });
  // Read states and aria-busy follow the list acceptance rule.
  it("maps reads and marks the list busy", () => { expect(listStep({ kind: "ok", page: page() })).toEqual(shown(page())); expect(listStep({ kind: "none" })).toBe("login"); expect(listStep({ kind: "network" })).toEqual({ busy: false, list: null, error: "common.network_check" }); expect(listStep({ kind: "failed" })).toEqual({ busy: false, list: null, error: "common.error_retry" }); expect(element(render(INITIAL_LIST), "div", "site-admin-coupons__list")).toContain('aria-busy="true"'); });
  // Read API outcomes: page/signal, login replacement, network, and other failure are all distinct.
  it.each<[Response | (() => never), string[], ListState | null]>([[json(200, page()), ["show"], shown(page())], [json(401, {}), [`replace ${LOGIN}`], null], [networkDown, ["show"], { busy: false, list: null, error: "common.network_check" }], [json(500, {}), ["show"], { busy: false, list: null, error: "common.error_retry" }]])("loads each read outcome (%#)", async (reply, events, state) => { const calls = stubFetch(reply); const controller = new AbortController(); const target = moves<ListState>(); await loadCoupons(2, controller.signal, target.target); expect(String(calls[0]?.url)).toBe("/api/admin/coupons?page=2"); expect(calls[0]?.init.signal).toBe(controller.signal); expect(target.events).toEqual(events); if (state !== null) expect(target.states).toEqual([state]); });
  // Aborted reads cannot update state after paging or leaving.
  it("ignores an aborted read", async () => { stubFetch(json(200, page())); const controller = new AbortController(); const target = moves<ListState>(); const pending = loadCoupons(1, controller.signal, target.target); controller.abort(); await pending; expect(target.events).toEqual([]); });
  // Disable result mapping is the A05 policy for ok, reload, login, network, and failure.
  it("maps disable results", () => { expect(disableStep({ kind: "ok", coupon: PERCENT })).toEqual({ kind: "updated", coupon: PERCENT }); expect(disableStep({ kind: "csrf" })).toEqual({ kind: "reload" }); expect(disableStep({ kind: "not_found" })).toEqual({ kind: "reload" }); expect(disableStep({ kind: "none" })).toBe("login"); expect(disableStep({ kind: "network" })).toEqual({ kind: "error", error: "common.network_check" }); expect(disableStep({ kind: "failed" })).toEqual({ kind: "error", error: "common.error_retry" }); });
  // Disable API outcomes update, reload, redirect, or show precisely the required state; updated active rows lose their button.
  it.each<[Response | (() => never), string[], AdminCoupon[]]>([[json(200, { ...FIXED, active: false }), ["update", "finish"], [{ ...FIXED, active: false }]], [json(404, {}), ["reload", "finish"], []], [json(403, {}), ["reload", "finish"], []], [json(401, {}), [`replace ${LOGIN}`], []], [networkDown, ["error common.network_check", "finish"], []], [json(500, {}), ["error common.error_retry", "finish"], []]])("handles disable outcome (%#)", async (reply, events, updates) => { const calls = stubFetch(reply); const controller = new AbortController(); const target = disableMoves(); await disableCoupon(FIXED.id, "csrf", controller.signal, target.target); expect(String(calls[0]?.url)).toBe("/api/admin/coupons/1/disable"); expect(target.events).toEqual(events); expect(target.updates).toEqual(updates); if (updates.length > 0) expect(render(shown(page({ coupons: updates }))).match(/<button\b/g)).toBeNull(); });
  // One disable at a time: every button is disabled, but only the selected row/card is busy.
  it("marks only the selected coupon busy", () => { const html = render(undefined, FIXED.id); expect((html.match(/aria-busy="true"/g) ?? []).length).toBe(2); expect((html.match(/disabled=""/g) ?? []).length).toBe(2); });
  // CSRF/not-found reloads retain error_retry even once the replacement list has completed.
  it("keeps retry after a reload", () => { expect(render({ busy: false, list: page(), error: "common.error_retry" })).toContain(COPY["common.error_retry"].en); });
  // Aborted disables cannot apply late updates, errors, reloads, or redirects.
  it("ignores an aborted disable", async () => { stubFetch(json(200, { ...FIXED, active: false })); const controller = new AbortController(); const target = disableMoves(); const pending = disableCoupon(FIXED.id, "csrf", controller.signal, target.target); controller.abort(); await pending; expect(target.events).toEqual([]); });
  // UI text is dictionary copy, API coupon data, formatted dates/values/counts, or A05's specified punctuation.
  it.each(LANGUAGES)("uses dictionary text only in %s", (language) => { const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language])); for (const coupon of [FIXED, PERCENT]) { allowed.add(coupon.code); allowed.add(coupon.type === "fixed" ? "RM 10.00" : "20%"); allowed.add(formatCouponDate(coupon.starts_on, language)); allowed.add(formatCouponDate(coupon.ends_on, language)); allowed.add(String(coupon.used_count)); allowed.add(String(coupon.total_use_limit ?? "—")); } for (const value of ["/", "–", "·", "‹", "›"]) allowed.add(value); for (const html of [render(undefined, null, language), render({ busy: false, list: page(), error: "common.error_retry" }, null, language), render(shown(page({ total: 31 })), null, language)]) for (const value of visibleTexts(html)) expect(allowed.has(value), value).toBe(true); });
});
