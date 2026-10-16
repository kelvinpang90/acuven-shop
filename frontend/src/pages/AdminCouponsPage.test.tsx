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
import AdminCouponsPage, { AdminCouponsContent, AdminCouponsView, couponInput, createStep, DEFAULT_COUPON_FORM, disableCoupon, disableStep, formatCouponDate, INITIAL_LIST, listStep, loadCoupons, pagerOf, reloadedState, submitCoupon, updatedState } from "./AdminCouponsPage";
import type { CreateMoves, DisableMoves, ListState, Moves } from "./AdminCouponsPage";

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
function createMoves() { const events: string[] = []; const target: CreateMoves = { success: (page) => { events.push(`success ${page}`); }, error: (error) => { events.push(`error ${error}`); }, reload: (page, error) => { events.push(`reload ${page} ${error}`); }, replace: (path) => { events.push(`replace ${path}`); } }; return { target, events }; }

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
  // SHOP-TASK-109 加标题行按钮后，守住 UX A05：标题行依次是原 h1、新建按钮，之后仍是原说明。
  it.each(LANGUAGES)("starts content with the original title and note in %s", (language) => { const html = renderToStaticMarkup(wrap(<AdminCouponsContent />, language)); expect(html.startsWith(`<div class="site-admin-coupons"><div class="site-admin-coupons__heading"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_coupons"][language])}</h1><button class="acs-admin__btn" type="button" disabled="">${escapeHtml(COPY["common.create"][language])}</button></div><p class="site-admin-coupons__note">${escapeHtml(COPY["admin.coupon_listed_note"][language])}</p>`)).toBe(true); });
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
  // SHOP-TASK-109 标题行加新建按钮后，守住 A05：停用结果不再显示停用按钮。
  it.each<[Response | (() => never), string[], AdminCoupon[]]>([[json(200, { ...FIXED, active: false }), ["update", "finish"], [{ ...FIXED, active: false }]], [json(404, {}), ["reload", "finish"], []], [json(403, {}), ["reload", "finish"], []], [json(401, {}), [`replace ${LOGIN}`], []], [networkDown, ["error common.network_check", "finish"], []], [json(500, {}), ["error common.error_retry", "finish"], []]])("handles disable outcome (%#)", async (reply, events, updates) => { const calls = stubFetch(reply); const controller = new AbortController(); const target = disableMoves(); await disableCoupon(FIXED.id, "csrf", controller.signal, target.target); expect(String(calls[0]?.url)).toBe("/api/admin/coupons/1/disable"); expect(target.events).toEqual(events); expect(target.updates).toEqual(updates); if (updates.length > 0) expect(render(shown(page({ coupons: updates })))).not.toMatch(new RegExp(`<button[^>]*>${escapeHtml(COPY["admin.coupon_disable"].en)}</button>`)); });
  // 本任务验收「同一时间只停用一张——进行中所有停用按钮都禁用，只有被点的那张标 aria-busy」：两张启用券，点的是 WELCOME，
  // 桌面行与手机卡片里只有 WELCOME 的那一项为 aria-busy="true"，SAVE20 的行与卡片不是，四个停用按钮都带 disabled。
  // SHOP-TASK-109 added a heading-row create button, so this assertion selects only disable buttons.
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
  // SHOP-TASK-109「列表读取成功前或失败时禁用，新建打开后有返回与表单」：入口只能在最近列表带 CSRF 时可用，手机新建态用专属类隐藏列表。
  it("renders the guarded create control and new form", () => {
    expect(render(INITIAL_LIST)).toMatch(/<button[^>]*disabled=""[^>]*>New<\/button>/);
    expect(render({ busy: false, list: null, error: "common.error_retry" })).toMatch(/<button[^>]*disabled=""[^>]*>New<\/button>/);
    expect(render(shown(page()))).toMatch(/<button class="acs-admin__btn" type="button">New<\/button>/);
    const html = renderToStaticMarkup(wrap(<AdminCouponsView state={shown(page())} disabling={null} formOpen form={DEFAULT_COUPON_FORM} saving={false} error={null} onChange={() => undefined} onSubmit={() => undefined} onCreate={() => undefined} onBack={() => undefined} onDisable={() => undefined} onPage={() => undefined} />));
    expect(html).toContain("site-admin-coupons--form-open"); expect(html).toContain(`<h2 class="acs-admin__h">${COPY["common.create"].en}</h2>`); expect(html).toContain(COPY["common.back"].en);
    const closed = render(shown(page()));
    expect(closed).not.toContain(`<h2 class="acs-admin__h">${COPY["common.create"].en}</h2>`); expect(closed).not.toContain(COPY["common.back"].en); expect(closed).not.toContain("site-admin-coupons__form");
  });
  // SHOP-TASK-109 field contract: the rendered fixed and percent forms retain browser validation rather than adding copy.
  it("renders A05 form defaults and browser constraints for both coupon types", () => {
    const fixed = renderToStaticMarkup(wrap(<AdminCouponsView state={shown(page())} disabling={null} formOpen form={DEFAULT_COUPON_FORM} saving={false} error={null} onChange={() => undefined} onSubmit={() => undefined} onDisable={() => undefined} onPage={() => undefined} />));
    const percent = renderToStaticMarkup(wrap(<AdminCouponsView state={shown(page())} disabling={null} formOpen form={{ ...DEFAULT_COUPON_FORM, type: "percent" }} saving={false} error={null} onChange={() => undefined} onSubmit={() => undefined} onDisable={() => undefined} onPage={() => undefined} />));
    expect(fixed).toMatch(new RegExp(`<input[^>]*type="radio"[^>]*checked=""[^>]*> ${escapeHtml(COPY["admin.coupon_fixed"].en)}`));
    expect(fixed).toMatch(/<input[^>]*required=""[^>]*min="0\.01"[^>]*max="100000"[^>]*step="0\.01"/);
    expect(percent).toMatch(/<input[^>]*required=""[^>]*min="1"[^>]*max="100"[^>]*step="1"/);
    expect(fixed).toMatch(/<input[^>]*type="date"[^>]*required=""[^>]*value=""/); expect(fixed.match(/<input[^>]*type="date"[^>]*required=""[^>]*value=""/g)).toHaveLength(2);
    expect(fixed).toMatch(/<input[^>]*required=""[^>]*value="0"[^>]*min="0"[^>]*max="100000"[^>]*step="0\.01"/);
    expect(fixed.match(/<input[^>]*min="1"[^>]*max="1000000"[^>]*step="1"[^>]*value=""/g)).toHaveLength(2);
  });
  // A05 phone new state keeps the admin frame's coupon title while its content hides the heading and list.
  it("keeps the coupon top-bar title while the phone new form is open", () => {
    const html = renderToStaticMarkup(wrap(<AdminFrame current="coupons"><AdminCouponsView state={shown(page())} disabling={null} formOpen onDisable={() => undefined} onPage={() => undefined} /></AdminFrame>));
    expect(html).toContain(`<span class="site-admin__bar-title">${COPY["admin.nav_coupons"].en}</span>`); expect(html).toContain("site-admin-coupons--form-open");
  });
  // SHOP-TASK-109 keeps save's in-progress state local to the form.
  it("marks save busy while a coupon submission is in progress", () => {
    const html = renderToStaticMarkup(wrap(<AdminCouponsView state={shown(page())} disabling={null} formOpen saving onDisable={() => undefined} onPage={() => undefined} />));
    expect(html).toMatch(new RegExp(`<button[^>]*type="submit"[^>]*disabled=""[^>]*aria-busy="true"[^>]*>${escapeHtml(COPY["common.save"].en)}</button>`));
  });
  // SHOP-TASK-109 字段规则：fixed 默认、最低消费 0、两个上限空；fixed 与 percent 各自转换成服务端九字段。
  it("uses A05 form defaults, browser limits, and integer sen", () => {
    expect(DEFAULT_COUPON_FORM).toMatchObject({ type: "fixed", min_spend: "0", total_use_limit: "", per_member_use_limit: "" });
    expect(couponInput({ ...DEFAULT_COUPON_FORM, code: "A1", value: "0.1", min_spend: "12.34", starts_on: "2026-01-01", ends_on: "2026-01-02" })).toMatchObject({ amount_off_sen: 10, min_subtotal_sen: 1234, total_use_limit: null, per_member_use_limit: null });
    expect(couponInput({ ...DEFAULT_COUPON_FORM, type: "percent", value: "20", starts_on: "2026-01-01", ends_on: "2026-01-02" })).toMatchObject({ amount_off_sen: null, percent_off: 20 });
  });
  // SHOP-TASK-109 提交结果：ok、重复、无效、CSRF、无会话、网络和失败各自执行 Kelvin 决定与验收指定的状态变化。
  it.each<[Response | (() => never), string[]]>([[json(201, FIXED), ["success 1"]], [json(409, { detail: "coupon_code_taken" }), ["error admin.coupon_code_taken"]], [json(422, {}), ["error admin.coupon_invalid"]], [json(403, {}), ["reload 3 common.error_retry"]], [json(401, {}), [`replace ${LOGIN}`]], [networkDown, ["reload 1 common.network_check"]], [json(500, {}), ["error common.error_retry"]]])("handles each A05 create result (%#)", async (reply, events) => { const calls = stubFetch(reply); const target = createMoves(); await submitCoupon(couponInput({ ...DEFAULT_COUPON_FORM, code: "CODE", value: "12.34", starts_on: "2026-01-01", ends_on: "2026-01-02" }), "csrf", 3, new AbortController().signal, target.target); expect(calls[0]?.init.method).toBe("POST"); expect(target.events).toEqual(events); });
  // SHOP-TASK-109: successful creation closes and resets the form, then reloads page one through this pure result.
  it("maps a successful create to page one", () => { expect(createStep({ kind: "ok", coupon: FIXED }, 3)).toEqual({ kind: "ok", page: 1 }); });
  // Kelvin's rejected-create decision keeps entered values; network also rereads page one because the create may have succeeded.
  it.each(["admin.coupon_code_taken", "admin.coupon_invalid", "common.network_check"] as const)("keeps the form visible with its values for %s", (error) => {
    const form = { ...DEFAULT_COUPON_FORM, code: "KEEP", value: "12.34", starts_on: "2026-01-01", ends_on: "2026-01-02" };
    const html = renderToStaticMarkup(wrap(<AdminCouponsView state={shown(page())} disabling={null} formOpen form={form} saving={false} error={error} onDisable={() => undefined} onPage={() => undefined} />));
    expect(html).toContain('value="KEEP"'); expect(html).toContain('value="12.34"'); expect(html).toContain(COPY[error].en);
  });
  // SHOP-TASK-109：离开页面后的新建结果不得更新页面。
  it("ignores an aborted create", async () => { stubFetch(json(201, FIXED)); const controller = new AbortController(); const target = createMoves(); const pending = submitCoupon(couponInput({ ...DEFAULT_COUPON_FORM, code: "CODE", value: "1", starts_on: "2026-01-01", ends_on: "2026-01-02" }), "csrf", 1, controller.signal, target.target); controller.abort(); await pending; expect(target.events).toEqual([]); });
  // UI text is dictionary copy, API coupon data, formatted dates/values/counts, or A05's specified punctuation.
  // SHOP-TASK-109 requires every open-form label, action, and rejection message to come from the dictionary.
  it.each(LANGUAGES)("uses dictionary text for the open form in %s", (language) => { const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language])); const html = renderToStaticMarkup(wrap(<AdminCouponsView state={shown(page())} disabling={null} formOpen error="admin.coupon_code_taken" onDisable={() => undefined} onPage={() => undefined} />, language)); for (const value of visibleTexts(html)) expect(allowed.has(value), value).toBe(true); });
  it.each(LANGUAGES)("uses dictionary text only in %s", (language) => { const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language])); for (const coupon of [FIXED, PERCENT]) { const start = formatCouponDate(coupon.starts_on, language); const end = formatCouponDate(coupon.ends_on, language); const limit = String(coupon.total_use_limit ?? "—"); const usage = `${coupon.used_count} / ${limit}`; const valid = `${start} – ${end}`; allowed.add(coupon.code); allowed.add(coupon.type === "fixed" ? "RM 10.00" : "20%"); allowed.add(start); allowed.add(end); allowed.add(valid); allowed.add(String(coupon.used_count)); allowed.add(limit); allowed.add(usage); allowed.add(`${COPY["admin.coupon_usage"][language]} ${usage} · ${valid}`); } for (const value of ["/", "–", "·", "‹", "›"]) allowed.add(value); for (const html of [render(undefined, null, language), render({ busy: false, list: page(), error: "common.error_retry" }, null, language), render(shown(page({ total: 31 })), null, language)]) for (const value of visibleTexts(html)) expect(allowed.has(value), value).toBe(true); });
});
