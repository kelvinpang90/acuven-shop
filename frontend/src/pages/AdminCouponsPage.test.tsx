import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminCoupon, AdminCouponPage } from "../api/adminCoupons";
import AdminFrame from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminCouponsPage, { AdminCouponsContent, AdminCouponsView, couponInputOf, createCoupon, createStep, DEFAULT_COUPON_FORM, disableCoupon, disableStep, formatCouponDate, INITIAL_LIST, listStep, loadCoupons, pagerOf, reloadedState, updatedState } from "./AdminCouponsPage";
import type { DisableMoves, ListState, Moves } from "./AdminCouponsPage";

const COUPONS_PATH = "/admin/coupons";
const PATH = COUPONS_PATH;
const LOGIN = "/admin/login";
const NAV_PATHS = ["/admin/orders", "/admin/refunds", COUPONS_PATH, "/admin/stock-resets", "/admin/store-design"];
const FIXED: AdminCoupon = { id: 1, code: "WELCOME", type: "fixed", amount_off_sen: 1000, percent_off: null, min_subtotal_sen: 0, starts_on: "2026-01-01", ends_on: "2026-12-31", total_use_limit: 100, per_member_use_limit: null, used_count: 12, active: true };
const PERCENT: AdminCoupon = { id: 2, code: "SAVE20", type: "percent", amount_off_sen: null, percent_off: 20, min_subtotal_sen: 0, starts_on: "2026-10-05", ends_on: "2026-10-05", total_use_limit: null, per_member_use_limit: null, used_count: 0, active: false };

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function storage(language: Language) { return { getItem: (key: string) => key === LANGUAGE_STORAGE_KEY ? language : null, setItem: () => undefined }; }
function renderApp(language: Language) { return renderToStaticMarkup(<App initialPath={COUPONS_PATH} storage={storage(language)} />); }
function page(overrides: Partial<AdminCouponPage> = {}): AdminCouponPage { return { total: 2, page: 1, page_size: 30, coupons: [FIXED, PERCENT], csrf_token: "csrf", ...overrides }; }
function shown(list: AdminCouponPage): ListState { return { busy: false, list, error: null }; }
function wrap(element: ReactNode, language: Language = "en") { return <LanguageProvider storage={storage(language)}><RouterProvider initialPath={PATH}>{element}</RouterProvider></LanguageProvider>; }
function render(state: ListState = shown(page()), disabling: number | null = null, language: Language = "en", showingForm = false, form = DEFAULT_COUPON_FORM, creating = false, saveError: CopyKey | null = null) { return renderToStaticMarkup(wrap(<AdminCouponsView state={state} disabling={disabling} form={form} creating={creating} showingForm={showingForm} saveError={saveError} onDisable={() => undefined} onPage={() => undefined} onCreate={() => undefined} onBack={() => undefined} onForm={() => undefined} onSave={() => undefined} />, language)); }
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
  // Existing SHOP-TASK-100 assertion changes only because A05 adds common.create beside the original title.
  it.each(LANGUAGES)("starts content with the original title and note in %s", (language) => { const html = renderToStaticMarkup(wrap(<AdminCouponsContent />, language)); expect(html.startsWith(`<div class="site-admin-coupons"><div class="site-admin-coupons__list-content"><div class="site-admin-coupons__title"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><button class="acs-admin__btn" type="button" disabled="">${escapeHtml(COPY["common.create"][language])}</button></div><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p>`)).toBe(true); });
  // UX A05: table/cards show all fields, full dates, limits including —, statuses, and the active-only disable button.
  it.each(LANGUAGES)("renders A05 rows and cards in %s", (language) => { const html = render(undefined, null, language); for (const key of ["admin.coupon_code", "admin.coupon_type", "admin.coupon_value", "admin.coupon_valid", "admin.coupon_usage", "admin.filter_status", "admin.coupon_fixed", "admin.coupon_percent", "admin.coupon_enabled", "admin.coupon_disabled"] as const) expect(html).toContain(escapeHtml(COPY[key][language])); expect(html).toContain("RM 10.00"); expect(html).toContain("20%"); expect(html).toContain("12 / 100"); expect(html).toContain("0 / —"); expect(html.match(new RegExp(`<button[^>]*>${escapeHtml(COPY["admin.coupon_disable"][language])}</button>`, "g"))?.length).toBe(2); });
  // Kelvin 2026-10-12 决定「有效期只录日期」与本任务验收「有效期按界面语言显示起始日与截止日本身（不按访客时区换算，写法同 A07 的营业日期）」：
  // 显示的就是那一天——与在马来西亚时区格式化当天中午得到的日期相同；英文为「Oct 5, 2026」。
  it.each(LANGUAGES)("shows the valid dates themselves in %s", (language) => {
    for (const date of ["2026-10-05", "2026-01-01", "2026-12-31"]) {
      const [year = 0, month = 1, day = 1] = date.split("-").map(Number);
      const expected = new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeZone: "Asia/Kuala_Lumpur" }).format(new Date(Date.UTC(year, month - 1, day, 4)));
      expect(formatCouponDate(date, language)).toBe(expected);
    }
    expect(formatCouponDate("2026-10-05", "en")).toBe("Oct 5, 2026");
    expect(render(shown(page({ coupons: [PERCENT] })), null, language)).toContain(escapeHtml(`${formatCouponDate("2026-10-05", language)} – ${formatCouponDate("2026-10-05", language)}`));
  });
  // 同一句「不按访客时区换算」：浏览器默认时区换成 UTC−12 与 UTC+14（替身在没给 timeZone 时补上该时区），日期都不提前或推后一天；
  // 替身确实生效：不带时区格式化 2026-01-01 的 UTC 零点，在 UTC−12 得到前一天。
  it("does not move the dates with the visitor's time zone", () => {
    const dates = ["2026-10-05", "2026-01-01", "2026-12-31"];
    const expected = ["Oct 5, 2026", "Jan 1, 2026", "Dec 31, 2026"];
    const RealFormat = Intl.DateTimeFormat;
    for (const zone of ["Etc/GMT+12", "Pacific/Kiritimati"]) {
      const InZone = function (locales?: string | string[], options?: Intl.DateTimeFormatOptions) { return new RealFormat(locales, { timeZone: zone, ...options }); } as unknown as typeof Intl.DateTimeFormat;
      vi.stubGlobal("Intl", Object.assign(Object.create(Intl) as typeof Intl, { DateTimeFormat: InZone }));
      if (zone === "Etc/GMT+12") expect(new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(Date.UTC(2026, 0, 1)))).toBe("Dec 31, 2025");
      expect(dates.map((date) => formatCouponDate(date, "en"))).toEqual(expected);
      vi.unstubAllGlobals();
    }
  });
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
  // Existing SHOP-TASK-102 assertion changes only because the new title-row create button means it can no longer say no buttons.
  it.each<[Response | (() => never), string[], AdminCoupon[]]>([[json(200, { ...FIXED, active: false }), ["update", "finish"], [{ ...FIXED, active: false }]], [json(404, {}), ["reload", "finish"], []], [json(403, {}), ["reload", "finish"], []], [json(401, {}), [`replace ${LOGIN}`], []], [networkDown, ["error common.network_check", "finish"], []], [json(500, {}), ["error common.error_retry", "finish"], []]])("handles disable outcome (%#)", async (reply, events, updates) => { const calls = stubFetch(reply); const controller = new AbortController(); const target = disableMoves(); await disableCoupon(FIXED.id, "csrf", controller.signal, target.target); expect(String(calls[0]?.url)).toBe("/api/admin/coupons/1/disable"); expect(target.events).toEqual(events); expect(target.updates).toEqual(updates); if (updates.length > 0) expect(render(shown(page({ coupons: updates })))).not.toMatch(new RegExp(`<button[^>]*>${escapeHtml(COPY["admin.coupon_disable"].en)}</button>`)); });
  // 本任务验收「同一时间只停用一张——进行中所有停用按钮都禁用，只有被点的那张标 aria-busy」：两张启用券，点的是 WELCOME，
  // 桌面行与手机卡片里只有 WELCOME 的那一项为 aria-busy="true"，SAVE20 的行与卡片不是，四个停用按钮都带 disabled。
  // Existing SHOP-TASK-102 assertion selects only disable buttons because A05 adds the independent create button.
  it("disables every disable button and marks only the clicked coupon busy", () => {
    const secondActive = { ...PERCENT, active: true };
    const html = render(shown(page({ coupons: [FIXED, secondActive] })), FIXED.id);
    const buttons = [...html.matchAll(new RegExp(`<button[^>]*>${escapeHtml(COPY["admin.coupon_disable"].en)}</button>`, "g"))].map((match) => match[0]);
    expect(buttons).toHaveLength(4);
    expect(buttons.every((button) => button.includes('disabled=""'))).toBe(true);
    const items = [...html.matchAll(/<(tr|li)\b[^>]*>[\s\S]*?<\/\1>/g)].map((match) => match[0]).filter((item) => item.includes("WELCOME") || item.includes("SAVE20"));
    expect(items).toHaveLength(4);
    for (const item of items) expect(/^<(tr|li)\b[^>]*aria-busy="true"/.test(item)).toBe(item.includes("WELCOME"));
  });
  // 本任务验收「not_found 与 csrf 时重新读取当前页，读取完成后仍显示 common.error_retry（重新读取不清掉这条提示）」：
  // 停用把状态标成 error_retry 后重新读取，读取成功也保留这条提示；重新读取本身失败时显示那次失败；没有提示时读取结果原样采用。
  it("keeps error_retry after a reload completes", () => {
    const pending: ListState = { busy: true, list: page(), error: "common.error_retry" };
    const reloaded = page({ coupons: [{ ...FIXED, active: false }, PERCENT] });
    expect(reloadedState(pending, shown(reloaded))).toEqual({ busy: false, list: reloaded, error: "common.error_retry" });
    expect(reloadedState(pending, { busy: false, list: null, error: "common.network_check" })).toEqual({ busy: false, list: null, error: "common.network_check" });
    expect(reloadedState(pending, { busy: false, list: null, error: "common.error_retry" })).toEqual({ busy: false, list: null, error: "common.error_retry" });
    expect(reloadedState({ busy: true, list: page(), error: null }, shown(reloaded))).toEqual(shown(reloaded));
    expect(render(reloadedState(pending, shown(reloaded)))).toContain(escapeHtml(COPY["common.error_retry"].en));
  });
  // 本任务验收「ok 时以返回的券更新该行（不再显示停用按钮）」：只替换同一内部 ID 的那一行，其他行与顺序不变；还没有列表时不变。
  it("replaces only the disabled coupon's row", () => {
    const secondActive = { ...PERCENT, active: true };
    const disabled = { ...FIXED, active: false };
    const next = updatedState(shown(page({ coupons: [FIXED, secondActive] })), disabled);
    expect(next.list?.coupons).toEqual([disabled, secondActive]);
    expect(updatedState(INITIAL_LIST, disabled)).toEqual(INITIAL_LIST);
    const rows = [...render(next).matchAll(/<tr\b[^>]*>[\s\S]*?<\/tr>/g)].map((match) => match[0]).filter((row) => row.includes("<td>"));
    expect(rows.map((row) => /<button\b[^>]*>[^<]*<\/button>/.test(row))).toEqual([false, true]);
  });
  // Aborted disables cannot apply late updates, errors, reloads, or redirects.
  it("ignores an aborted disable", async () => { stubFetch(json(200, { ...FIXED, active: false })); const controller = new AbortController(); const target = disableMoves(); const pending = disableCoupon(FIXED.id, "csrf", controller.signal, target.target); controller.abort(); await pending; expect(target.events).toEqual([]); });
  // UI text is dictionary copy, API coupon data, formatted dates/values/counts, or A05's specified punctuation.
  it.each(LANGUAGES)("uses dictionary text only in %s", (language) => { const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language])); for (const coupon of [FIXED, PERCENT]) { const start = formatCouponDate(coupon.starts_on, language); const end = formatCouponDate(coupon.ends_on, language); const limit = String(coupon.total_use_limit ?? "—"); const usage = `${coupon.used_count} / ${limit}`; const valid = `${start} – ${end}`; allowed.add(coupon.code); allowed.add(coupon.type === "fixed" ? "RM 10.00" : "20%"); allowed.add(start); allowed.add(end); allowed.add(valid); allowed.add(String(coupon.used_count)); allowed.add(limit); allowed.add(usage); allowed.add(`${COPY["admin.coupon_usage"][language]} ${usage} · ${valid}`); } for (const value of ["/", "–", "·", "‹", "›"]) allowed.add(value); for (const html of [render(undefined, null, language), render({ busy: false, list: page(), error: "common.error_retry" }, null, language), render(shown(page({ total: 31 })), null, language)]) for (const value of visibleTexts(html)) expect(allowed.has(value), value).toBe(true); });
  // 本任务验收「页面文字全部来自字典」：打开新建表单时新增的标题、字段、说明与按钮均来自现有字典键。
  it.each(LANGUAGES)("uses dictionary text for the opened form in %s", (language) => { const html = render(shown(page()), null, language, true); for (const key of ["common.create", "common.back", "admin.coupon_code", "admin.coupon_type", "admin.coupon_fixed", "admin.coupon_percent", "admin.coupon_value", "admin.coupon_valid", "admin.coupon_min_spend", "admin.coupon_total_limit", "admin.coupon_member_limit", "admin.rules_apply_new", "common.save"] as const) expect(html).toContain(escapeHtml(COPY[key][language])); });
  // 本任务验收「字段为 admin.coupon_code（文本，必填）」及「固定金额时以 RM 输入、步长 0.01、0.01 到 100000」：字段本身，而非页面中其他输入，带这些约束。
  it("renders the new-coupon form and converts its values", () => { const html = render(shown(page()), null, "en", true); const inputs = tags(html, "input"); const code = inputs.find((tag) => attributes(tag).get("type") === undefined); const fixed = inputs.find((tag) => attributes(tag).get("value") === "fixed"); const value = inputs.find((tag) => attributes(tag).get("min") === "0.01"); const minimum = inputs.find((tag) => attributes(tag).get("min") === "0"); const limits = inputs.filter((tag) => attributes(tag).get("min") === "1" && attributes(tag).get("max") === "1000000"); expect(html).toContain(`<h2 class="acs-admin__h">${COPY["common.create"].en}</h2>`); expect(html).toContain(COPY["common.back"].en); expect(attributes(code ?? "").has("required")).toBe(true); expect(attributes(fixed ?? "").get("checked")).toBe(""); expect(attributes(value ?? "")).toMatchObject({ min: "0.01", max: "100000", step: "0.01" }); expect(attributes(minimum ?? "")).toMatchObject({ value: "0", min: "0", max: "100000", step: "0.01" }); expect(limits).toHaveLength(2); expect(limits.map((tag) => attributes(tag).get("value"))).toEqual(["", ""]); expect(couponInputOf({ ...DEFAULT_COUPON_FORM, code: "SAVE", value: "12.34", min_spend: "0.1", starts_on: "2026-01-01", ends_on: "2026-12-31" })).toMatchObject({ amount_off_sen: 1234, min_subtotal_sen: 10, total_use_limit: null, per_member_use_limit: null }); expect(couponInputOf({ ...DEFAULT_COUPON_FORM, type: "percent", value: "20", starts_on: "2026-01-01", ends_on: "2026-12-31" })).toMatchObject({ amount_off_sen: null, percent_off: 20 }); });
  // Acceptance: create is available only after the most recent list read supplied its CSRF token; A05 form opening hides the phone list area.
  it("gates create on a successful read and renders the opened form", () => { const unavailable: ListState = { busy: false, list: null, error: "common.error_retry" }; expect(render(INITIAL_LIST)).toMatch(/<button[^>]*disabled=""[^>]*>New<\/button>/); expect(render(unavailable)).toMatch(/<button[^>]*disabled=""[^>]*>New<\/button>/); const open = render(shown(page()), null, "en", true); expect(open).toContain('site-admin-coupons--form-open'); expect(open).toContain('site-admin-coupons__list-content'); expect(open).toContain(`<h2 class="acs-admin__h">${COPY["common.create"].en}</h2>`); expect(open).toContain(COPY["common.back"].en); expect(render(shown(page()))).not.toContain('site-admin-coupons__form-content'); });
  // 本任务验收「fixed 时以 RM 输入、步长 0.01、0.01 到 100000；percent 时为 1 到 100 的整数」及「两个都必填」：类型切换只改变优惠值字段的原生约束。
  it("uses the A05 native form constraints and busy save state", () => { const fixed = render(shown(page()), null, "en", true, DEFAULT_COUPON_FORM, true); const fixedInputs = tags(fixed, "input"); const fixedRadio = fixedInputs.find((tag) => attributes(tag).get("value") === "fixed"); const percentRadio = fixedInputs.find((tag) => attributes(tag).get("value") === "percent"); const fixedValue = fixedInputs.find((tag) => attributes(tag).get("min") === "0.01"); const fixedDates = fixedInputs.filter((tag) => attributes(tag).get("type") === "date"); expect(attributes(fixedRadio ?? "").get("checked")).toBe(""); expect(attributes(percentRadio ?? "").has("checked")).toBe(false); expect(attributes(fixedValue ?? "")).toMatchObject({ min: "0.01", max: "100000", step: "0.01" }); expect(fixedDates).toHaveLength(2); expect(fixedDates.every((tag) => attributes(tag).has("required"))).toBe(true); expect(fixed).toMatch(/<button[^>]*disabled=""[^>]*aria-busy="true"[^>]*>Save<\/button>/); const percent = render(shown(page()), null, "en", true, { ...DEFAULT_COUPON_FORM, type: "percent" }); const percentInputs = tags(percent, "input"); const percentValue = percentInputs.find((tag) => attributes(tag).get("min") === "1" && attributes(tag).get("max") === "100"); expect(attributes(percentInputs.find((tag) => attributes(tag).get("value") === "fixed") ?? "").has("checked")).toBe(false); expect(attributes(percentInputs.find((tag) => attributes(tag).get("value") === "percent") ?? "").get("checked")).toBe(""); expect(attributes(percentValue ?? "")).toMatchObject({ min: "1", max: "100", step: "1" }); });
  // 本任务验收「手机顶栏标题仍为 admin.nav_coupons，不改后台框架」：打开新建表单不改变后台框架的顶栏文案。
  it("keeps the coupon title in the phone bar while the form is open", () => { const html = renderToStaticMarkup(wrap(<AdminFrame current="coupons"><AdminCouponsView state={shown(page())} disabling={null} form={DEFAULT_COUPON_FORM} creating={false} showingForm saveError={null} onDisable={() => undefined} onPage={() => undefined} onCreate={() => undefined} onBack={() => undefined} onForm={() => undefined} onSave={() => undefined} /></AdminFrame>)); expect(html).toContain(`<span class="site-admin__bar-title">${COPY["admin.nav_coupons"].en}</span>`); });
  // Kelvin 2026-10-12 decision and acceptance: each create result retains or clears the form and selects the prescribed retry page.
  it.each<[Response | (() => never), string[]]>([[json(409, { detail: "coupon_code_taken" }), ["error admin.coupon_code_taken", "finish"]], [json(422, {}), ["error admin.coupon_invalid", "finish"]], [json(403, {}), ["error common.error_retry", "reload 3", "finish"]], [json(401, {}), [`replace ${LOGIN}`]], [networkDown, ["error common.network_check", "reload 1", "finish"]], [json(500, {}), ["error common.error_retry", "finish"]]])("handles required create outcome (%#)", async (reply, expected) => { stubFetch(reply); const events: string[] = []; await createCoupon({ ...DEFAULT_COUPON_FORM, code: "NEW", value: "1", starts_on: "2026-01-01", ends_on: "2026-12-31" }, "csrf", 3, new AbortController().signal, { clear: () => { events.push("clear"); }, error: (error) => { events.push(`error ${error}`); }, reload: (next) => { events.push(`reload ${next}`); }, finish: () => { events.push("finish"); }, replace: (path) => { events.push(`replace ${path}`); } }); expect(events).toEqual(expected); });
  // Kelvin's 2026-10-12 decision: each create result takes its specified page, form, or login action.
  it("maps every create outcome", async () => { expect(createStep({ kind: "code_taken" })).toEqual({ kind: "error", error: "admin.coupon_code_taken" }); expect(createStep({ kind: "invalid" })).toEqual({ kind: "error", error: "admin.coupon_invalid" }); const calls = stubFetch(json(201, { ...FIXED, code: "NEW" })); const events: string[] = []; await createCoupon({ ...DEFAULT_COUPON_FORM, code: "NEW", value: "1", starts_on: "2026-01-01", ends_on: "2026-12-31" }, "csrf", 3, new AbortController().signal, { clear: () => { events.push("clear"); }, error: (error) => { events.push(`error ${error}`); }, reload: (page) => { events.push(`reload ${page}`); }, finish: () => { events.push("finish"); }, replace: (path) => { events.push(`replace ${path}`); } }); expect(calls[0]?.url).toBe("/api/admin/coupons"); expect(events).toEqual(["clear", "reload 1", "finish"]); });
});
