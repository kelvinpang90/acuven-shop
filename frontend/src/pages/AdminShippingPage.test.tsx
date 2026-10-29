import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminRates } from "../api/adminShippingRates";
import { CSRF_HEADER } from "../api/pay";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_SHIPPING_PATH, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminShippingPage, {
  AdminShippingContent,
  AdminShippingView,
  changeInput,
  feeSen,
  feeText,
  initialInputs,
  isRateText,
  LOADING,
  openRates,
  readStep,
  reloadRates,
  saveContent,
  saveStep,
  startSaving,
  submitRates,
} from "./AdminShippingPage";
import type { RatesInputs, SaveNotice, ShippingChange, ShippingMoves, ShippingState } from "./AdminShippingPage";
import { countryNames } from "./checkoutPhone";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const SHIPPING_PATH = "/admin/shipping";
const NAV_PATHS = ["/admin/orders", "/admin/refunds", "/admin/coupons", SHIPPING_PATH, "/admin/stock-resets", "/admin/store-design", "/admin/settings"];
const NAV_KEYS = [
  "admin.nav_orders",
  "admin.nav_refunds",
  "admin.nav_coupons",
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

function wrap(children: ReactNode, language: Language): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={SHIPPING_PATH}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={SHIPPING_PATH} storage={storage(language)} />);
}

// 已登录（会话 200）时的后台框架：内容区为本页的内容。
function renderSignedIn(language: Language): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="shipping"
        state={SIGNED_IN}
        busy={false}
        menuOpen={false}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        <AdminShippingContent />
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
  throw new Error(`unclosed <${name}>`);
}

describe("AdminShippingPage", () => {
  // UX A06「入口：后台导航」与 SHOP-TASK-173 验收第 2 条「路由 /admin/shipping……用后台框架渲染」：
  // 经路由打开 /admin/shipping 渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），不套前台框架（没有前台页头、页脚与前台演示横幅）。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    expect(ADMIN_SHIPPING_PATH).toBe(SHIPPING_PATH);
    const html = renderApp(language);
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、[admin.nav_products]、[admin.nav_coupons]、
  // [admin.nav_shipping]、[admin.nav_stock_resets]、[admin.nav_store_design]、[admin.nav_settings]」与 Kelvin 2026-10-06
  // （docs/HANDOFF.md 0.35）「后台导航只显示已上线页面的导航项」：经路由打开本页，桌面导航与 ☰ 菜单都依次为订单、退款、优惠券、
  // 运费与演示汇率、库存重置、店铺装修与站点设置七项，运费与演示汇率排在优惠券之后并标 aria-current="page"，没有其他导航项。
  it.each(LANGUAGES)("marks shipping current, after coupons, in both navigation menus in %s", (language) => {
    for (const html of [renderApp(language), renderToStaticMarkup(wrap(<AdminShippingPage />, language))]) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual([undefined, undefined, undefined, "page", undefined, undefined, undefined]);
        expect(textNodes(nav).slice(0, 7)).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
      }
    }
  });

  // UX A06 手机线框「☰  [admin.nav_shipping]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」。
  it.each(LANGUAGES)("names the shipping item in the phone bar in %s", (language) => {
    expect(element(renderApp(language), "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_shipping"][language])}</span>`,
    );
  });

  // UX A06 线框内容区第一行「[admin.shipping_title]」与 SHOP-TASK-173 验收第 2 条「内容区只有 h1 admin.shipping_title（class 含视觉稿的 acs-admin__h）」：
  // 已登录时内容区里只有这一个标题。
  // SHOP-TASK-175 改动：标题之下加入表单区（SHOP-TASK-175 验收第 2 条「读取前……不显示表单」），「内容区只有标题」的逐字断言因此失效；
  // 改为内容区以原样的 h1（class 加 site-admin-shipping__title）开头，读取返回前其下只有标 aria-busy 的空表单区。
  it.each(LANGUAGES)("shows only the shipping title in the content area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    const opening = tags(main, "main")[0] ?? "";
    expect(main).toBe(
      `${opening}<div class="site-admin-shipping"><h1 class="acs-admin__h site-admin-shipping__title">${escapeHtml(COPY["admin.shipping_title"][language])}</h1>` +
        `<div class="site-admin-shipping__body" aria-busy="true"></div></div></main>`,
    );
  });

  // SHOP-TASK-173 验收第 2 条「页面不发起自己的请求」（请求模块留给 SHOP-TASK-174）：fetch 换成替身，渲染本页的内容与整页时都没有调用它。
  // SHOP-TASK-175 改动：SHOP-TASK-175 验收第 2 条「打开页面以 SHOP-TASK-174 的读取函数取整页」，页面不再「不发起自己的请求」；改为渲染时
  // 仍不调用 fetch（读取在挂载后的 effect 里），而打开页面的读取函数 openRates 只发一个 GET /api/admin/shipping-rates。
  it("reads the whole page only after mounting", async () => {
    const fetch = stubFetch(json(200, { ...RATES, csrf_token: CSRF }));
    renderToStaticMarkup(wrap(<AdminShippingContent />, "en"));
    renderApp("en");
    expect(fetch).not.toHaveBeenCalled();
    await openRates(new AbortController().signal, fakeMoves().target);
    expect(requests(fetch).map(({ method, url }) => `${method} ${url}`)).toEqual(["GET /api/admin/shipping-rates"]);
  });

  // SHOP-TASK-173 验收第 1 条「需要 UX-COPY 里没有的界面文字时停下……不自行编写文案」：经路由打开与已登录时，
  // 页面上看得见或读得出的每段文字（含 aria-label）都是当前语言的字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const html of [renderApp(language), renderSignedIn(language)]) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});

// 以下为 SHOP-TASK-175（读取、显示、改值与整页保存）的测试。

const LOGIN_PATH = "/admin/login";
const RATES_URL = "/api/admin/shipping-rates";
const CSRF = "rates-csrf-1";

// 读取到的两行汇率（日元小数位为 0）。
const SGD_ROW: AdminRates["fx"][number] = { id: 1, version: 2, country_code: "SG", currency_code: "SGD", currency_decimals: 2, rate: "0.31" };
const JPY_ROW: AdminRates["fx"][number] = { id: 4, version: 1, country_code: "JP", currency_code: "JPY", currency_decimals: 0, rate: "34" };

// 读取到的整页：马来西亚西马在前，三个国家，两行汇率。运费表与汇率表的 id 各自编号。
const RATES: AdminRates = {
  malaysia: [
    { id: 1, version: 1, zone_code: "MY-W", fee_sen: 800 },
    { id: 2, version: 1, zone_code: "MY-E", fee_sen: 1500 },
  ],
  other: { id: 3, version: 4, zone_code: "OTHER", fee_sen: 8000 },
  countries: [
    { id: 7, version: 1, zone_code: "SG", fee_sen: 2000 },
    { id: 5, version: 2, zone_code: "TH", fee_sen: 3000 },
    { id: 9, version: 1, zone_code: "JP", fee_sen: 4550 },
  ],
  fx: [SGD_ROW, JPY_ROW],
};

// 保存返回的整页：各行的运费与汇率改过、版本号加一。
const SAVED: AdminRates = {
  malaysia: [
    { id: 1, version: 2, zone_code: "MY-W", fee_sen: 900 },
    { id: 2, version: 1, zone_code: "MY-E", fee_sen: 1500 },
  ],
  other: { id: 3, version: 5, zone_code: "OTHER", fee_sen: 7550 },
  countries: [
    { id: 7, version: 2, zone_code: "SG", fee_sen: 2150 },
    { id: 5, version: 2, zone_code: "TH", fee_sen: 3000 },
    { id: 9, version: 1, zone_code: "JP", fee_sen: 4550 },
  ],
  fx: [
    { id: 1, version: 3, country_code: "SG", currency_code: "SGD", currency_decimals: 2, rate: "0.32" },
    { id: 4, version: 1, country_code: "JP", currency_code: "JPY", currency_decimals: 0, rate: "34" },
  ],
};

// 改过几个输入框之后的文字（与 SAVED 对应）。
const EDITED: RatesInputs = {
  malaysia: ["9", "15.00"],
  other: "75.5",
  countries: ["21.50", "30.00", "45.50"],
  fx: ["0.32", "34"],
};

type ReadyState = Extract<ShippingState, { status: "ready" }>;

function ready(overrides: Partial<ReadyState> = {}): ReadyState {
  const base = overrides.base ?? RATES;
  return { status: "ready", base, csrfToken: CSRF, inputs: initialInputs(base), saving: false, notice: null, alert: null, ...overrides };
}

// 本页内容区的视图（只依赖 props）。
function render(state: ShippingState = ready(), language: Language = "en"): string {
  return renderToStaticMarkup(
    wrap(<AdminShippingView state={state} onChange={() => undefined} onSave={() => undefined} onReload={() => undefined} />, language),
  );
}

function inputs(html: string): Map<string, string>[] {
  return tags(html, "input").map((tag) => attributes(tag));
}

function saveArea(html: string): string {
  return element(html, "div", "site-admin-shipping__save");
}

function saveButton(html: string): Map<string, string> {
  return attributes(tags(saveArea(html), "button")[0] ?? "");
}

function alertHtml(text: string): string {
  return `<div class="acs-admin__alert" role="alert"><span>${escapeHtml(text)}</span></div>`;
}

// 汇率行的名称：「国家名称 → 币种代码 (小数位)」。
function fxName(language: Language, row: AdminRates["fx"][number]): string {
  return `${countryNames(language)(row.country_code)} → ${row.currency_code} (${String(row.currency_decimals)})`;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// fetch 替身：按顺序给出回答（函数则以网络错误拒绝）；请求从 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const request = vi.fn((input: string | URL, init?: RequestInit) => {
    const next = queue.shift();
    if (next === undefined) {
      return Promise.reject(new Error(`unexpected request: ${String(init?.method)} ${String(input)}`));
    }
    return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", request);
  return request;
}

function requests(request: ReturnType<typeof stubFetch>) {
  return request.mock.calls.map(([input, init]) => ({ url: String(input), method: String(init?.method), init }));
}

function bodyOf(init: RequestInit | undefined): unknown {
  return typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
}

// 路由替身：保存当前状态（update 按它改动），记录每一步与经 replace 去的地址。
function fakeMoves(initial: ShippingState = LOADING) {
  const events: string[] = [];
  let current = initial;
  const target: ShippingMoves = {
    show: (state: ShippingState) => {
      current = state;
      events.push(`show ${state.status}`);
    },
    update: (change: ShippingChange) => {
      current = change(current);
      events.push(`update ${current.status}`);
    },
    replace: (path: RoutePath) => {
      events.push(`replace ${path}`);
    },
  };
  return {
    target,
    events,
    get current() {
      return current;
    },
  };
}

const NOTICES: SaveNotice[] = ["admin.rates_saved", "admin.rates_invalid", "admin.rates_changed", "common.error_retry", "common.network_check"];

// 本页可能出现的各状态（读取中、读取失败、表单、保存中、各保存结果与 403 之后）。
function allStates(): ShippingState[] {
  return [
    LOADING,
    { status: "failed", error: "common.network_check" },
    { status: "failed", error: "common.error_retry" },
    ready(),
    ready({ inputs: EDITED }),
    ready({ saving: true }),
    ...NOTICES.map((notice) => ready({ notice })),
    ready({ alert: "common.error_retry" }),
    ready({ base: SAVED, notice: "admin.rates_saved" }),
  ];
}

describe("display", () => {
  // UX A06 桌面线框「[admin.shipping_title] / [admin.shipping_my_state] / [admin.shipping_my_west] [admin.shipping_fee] / [admin.shipping_my_east] … /
  // [admin.shipping_by_country] / <国家> … / [admin.shipping_other] / [admin.fx_title] / <国家> → <币种>（<小数位>） [admin.fx_rate] [admin.fx_version] /
  // [admin.rules_apply_new] ( [common.save] )」、视觉稿 A06-desktop 的两栏与 SHOP-TASK-175 验收第 3 条：左栏依次为马来西亚、按国家与其他国家三块，
  // 右栏为演示汇率、admin.rules_apply_new 与保存；各块的文字按读取的顺序，国家名称用 countryNames 按界面语言取得。
  it.each(LANGUAGES)("lists the three fee blocks, then the demo rates and save, in %s", (language) => {
    const html = render(ready(), language);
    const name = countryNames(language);
    const t = (key: CopyKey) => COPY[key][language];
    expect(html.startsWith(`<div class="site-admin-shipping"><h1 class="acs-admin__h site-admin-shipping__title">${escapeHtml(t("admin.shipping_title"))}</h1>`)).toBe(true);
    const form = element(html, "form", "site-admin-shipping__form");
    expect(textNodes(element(form, "div", "site-admin-shipping__fees"))).toEqual([
      t("admin.shipping_my_state"),
      t("admin.shipping_fee"),
      t("admin.shipping_my_west"),
      t("admin.shipping_my_east"),
      t("admin.shipping_by_country"),
      t("admin.shipping_fee"),
      name("SG"),
      name("TH"),
      name("JP"),
      t("admin.shipping_other"),
    ]);
    expect(textNodes(element(form, "div", "site-admin-shipping__side"))).toEqual([
      t("admin.fx_title"),
      fxName(language, SGD_ROW),
      translate(language, "admin.fx_rate", { rate: "0.31", currency: "SGD" }),
      translate(language, "admin.fx_version", { n: 2 }),
      fxName(language, JPY_ROW),
      translate(language, "admin.fx_rate", { rate: "34", currency: "JPY" }),
      translate(language, "admin.fx_version", { n: 1 }),
      t("admin.rules_apply_new"),
      t("common.save"),
    ]);
    expect(textNodes(element(form, "details", "site-admin-shipping__malaysia"))[0]).toBe(t("admin.shipping_my_state"));
    expect(textNodes(element(form, "details", "site-admin-shipping__countries"))[0]).toBe(t("admin.shipping_by_country"));
    expect(textNodes(element(form, "div", "site-admin-shipping__other"))).toEqual([t("admin.shipping_other")]);
    expect(textNodes(element(form, "details", "site-admin-shipping__fx"))[0]).toBe(t("admin.fx_title"));
    expect(form.indexOf("site-admin-shipping__fees")).toBeLessThan(form.indexOf("site-admin-shipping__side"));
    // 版本号为行内标签（A06-desktop 的 acs-tag acs-tag--neutral）。
    expect(tags(form, "span").filter((tag) => classes(tag).includes("acs-tag")).map((tag) => classes(tag))).toEqual([
      ["acs-tag", "acs-tag--neutral"],
      ["acs-tag", "acs-tag--neutral"],
    ]);
    // 本任务没有删除与新增（SHOP-TASK-176）：表单里只有保存一个按钮，没有下拉。
    expect(tags(form, "button")).toHaveLength(1);
    expect(tags(form, "select")).toEqual([]);
    expect(saveButton(form).get("type")).toBe("submit");
  });

  // SHOP-TASK-175 验收第 3 条「按读取的顺序列出两档，MY-W 的文字为 admin.shipping_my_west、MY-E 为 admin.shipping_my_east」：两档的文字按区域代码取，
  // 不按位置；读取的顺序反过来时页面也反过来。
  it.each(LANGUAGES)("names the Malaysia tiers by zone code in read order in %s", (language) => {
    const swapped: AdminRates = { ...RATES, malaysia: [...RATES.malaysia].reverse() };
    const malaysia = element(render(ready({ base: swapped }), language), "details", "site-admin-shipping__malaysia");
    expect(textNodes(malaysia).slice(2)).toEqual([COPY["admin.shipping_my_east"][language], COPY["admin.shipping_my_west"][language]]);
    expect(inputs(malaysia).map((input) => input.get("value"))).toEqual(["15.00", "8.00"]);
  });

  // SHOP-TASK-175 验收第 3 条「运费输入框的初值由 fee_sen 用整数运算写成不带 RM 前缀的两位小数文字（0 为 0.00、5 为 0.05、800 为 8.00、99999 为 999.99），
  // 汇率输入框的初值为读取的 rate」与 HANDOFF 0.72 第（4）点「初值为不带 RM 前缀的两位小数文字」：输入框的值按页面顺序为各行的运费文字与读取的汇率。
  it("fills the inputs with two-decimal fees and the read rates", () => {
    expect([0, 5, 800, 99999, 999999].map(feeText)).toEqual(["0.00", "0.05", "8.00", "999.99", "9999.99"]);
    const base: AdminRates = {
      ...RATES,
      countries: [
        { id: 7, version: 1, zone_code: "SG", fee_sen: 0 },
        { id: 5, version: 1, zone_code: "TH", fee_sen: 5 },
        { id: 9, version: 1, zone_code: "JP", fee_sen: 99999 },
      ],
    };
    const form = element(render(ready({ base })), "form", "site-admin-shipping__form");
    expect(inputs(form).map((input) => input.get("value"))).toEqual(["8.00", "15.00", "0.00", "0.05", "999.99", "80.00", "0.31", "34"]);
    expect(inputs(form).map((input) => input.get("inputmode"))).toEqual(Array<string>(8).fill("decimal"));
  });

  // HANDOFF 0.72 第（6）点「运费输入框为 admin.shipping_fee 的文字、英文逗号加空格、行名；已有汇率行的输入框为 admin.fx_rate_new 的文字、英文逗号加空格、
  // 国家名称（不用币种代码）」与 SHOP-TASK-175 验收第 3 条：马来西亚两档与其他国家的行名为各自的文案，国家行为国家名称；两行汇率同一币种时标签仍不同。
  it.each(LANGUAGES)("labels each input with the fee or rate and the row name in %s", (language) => {
    const name = countryNames(language);
    const fee = COPY["admin.shipping_fee"][language];
    const rate = COPY["admin.fx_rate_new"][language];
    const sameCurrency: AdminRates = {
      ...RATES,
      fx: [
        { id: 1, version: 1, country_code: "SG", currency_code: "USD", currency_decimals: 2, rate: "0.23" },
        { id: 4, version: 1, country_code: "JP", currency_code: "USD", currency_decimals: 2, rate: "0.23" },
      ],
    };
    expect(inputs(render(ready({ base: sameCurrency }), language)).map((input) => input.get("aria-label"))).toEqual([
      `${fee}, ${COPY["admin.shipping_my_west"][language]}`,
      `${fee}, ${COPY["admin.shipping_my_east"][language]}`,
      `${fee}, ${name("SG")}`,
      `${fee}, ${name("TH")}`,
      `${fee}, ${name("JP")}`,
      `${fee}, ${COPY["admin.shipping_other"][language]}`,
      `${rate}, ${name("SG")}`,
      `${rate}, ${name("JP")}`,
    ]);
  });

  // SHOP-TASK-175 验收第 6 条「页面文字全部来自字典（国家名称、币种代码、小数位、版本号、汇率与运费的值、输入框的值、无障碍标签中拼接用的英文逗号加空格
  // 以及 → 与括号除外）」：各状态下每段文字（含 aria-label）都是字典文案、填入汇率与版本号的字典文案，或由国家名称与币种、小数位、
  // 「 → 」与括号拼成的汇率行名称、由字典文案与行名以「, 」拼成的无障碍标签。
  it.each(LANGUAGES)("shows only dictionary text in every state in %s", (language) => {
    const name = countryNames(language);
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    const rowNames: string[] = [COPY["admin.shipping_my_west"][language], COPY["admin.shipping_my_east"][language], COPY["admin.shipping_other"][language]];
    for (const base of [RATES, SAVED]) {
      for (const row of base.countries) {
        allowed.add(name(row.zone_code));
        rowNames.push(name(row.zone_code));
      }
      for (const row of base.fx) {
        allowed.add(fxName(language, row));
        allowed.add(translate(language, "admin.fx_rate", { rate: row.rate, currency: row.currency_code }));
        allowed.add(translate(language, "admin.fx_version", { n: row.version }));
        allowed.add(`${COPY["admin.fx_rate_new"][language]}, ${name(row.country_code)}`);
      }
    }
    for (const rowName of rowNames) {
      allowed.add(`${COPY["admin.shipping_fee"][language]}, ${rowName}`);
    }
    for (const state of allStates()) {
      for (const value of visibleTexts(render(state, language))) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });

  // SHOP-TASK-175 验收第 2 条「csrf_token 只留在页面内存里，用于保存」：各状态的页面里都没有 CSRF 令牌。
  it("never renders the CSRF token", () => {
    for (const state of allStates()) {
      expect(render(state)).not.toContain(CSRF);
    }
  });
});

describe("read", () => {
  // SHOP-TASK-175 验收第 2 条「读取前、401 与失败的处理照 AdminSiteSettingsPage.tsx 现有写法（401 回到 A01，网络中断 common.network_check，
  // 其他失败 common.error_retry，都不显示表单）」：取到时填表，CSRF 令牌留在状态里；读取中标 aria-busy、没有表单。
  it("opens the form from the read and handles 401 and failures like A09", async () => {
    const moves = fakeMoves();
    stubFetch(json(200, { ...RATES, csrf_token: CSRF }));
    await openRates(new AbortController().signal, moves.target);
    expect(moves.current).toEqual(ready());

    const login = fakeMoves();
    stubFetch(json(401, { detail: "not_authenticated" }));
    await openRates(new AbortController().signal, login.target);
    expect(login.events).toEqual([`replace ${LOGIN_PATH}`]);

    expect(readStep({ kind: "network" })).toEqual({ status: "failed", error: "common.network_check" });
    expect(readStep({ kind: "failed" })).toEqual({ status: "failed", error: "common.error_retry" });
    expect(readStep({ kind: "none" })).toBe("login");

    const loading = render(LOADING);
    expect(loading).toContain(`<div class="site-admin-shipping__body" aria-busy="true"></div>`);
    for (const error of ["common.network_check", "common.error_retry"] as const) {
      const html = render({ status: "failed", error });
      expect(element(html, "div", "site-admin-shipping__body")).toBe(
        `<div class="site-admin-shipping__body" aria-busy="false">${alertHtml(COPY[error].en)}</div>`,
      );
      expect(html).not.toMatch(/<(form|input)\b/);
    }
  });

  // UX A06「点了重新读取，未保存的修改丢弃」与 SHOP-TASK-175 验收第 4 条「点 admin.reload 照打开页面重新读取」：先显示读取中，再以新读取的数据与令牌填表，
  // 先前改过的输入框回到读取的值。
  it("reloads like opening the page and drops unsaved edits", async () => {
    const request = stubFetch(json(200, { ...SAVED, csrf_token: "rates-csrf-2" }));
    const moves = fakeMoves(ready({ inputs: EDITED, notice: "admin.rates_changed" }));
    await reloadRates(new AbortController().signal, moves.target);
    expect(requests(request).map(({ method, url }) => `${method} ${url}`)).toEqual([`GET ${RATES_URL}`]);
    expect(moves.events).toEqual(["show loading", "show ready"]);
    expect(moves.current).toEqual(ready({ base: SAVED, csrfToken: "rates-csrf-2" }));
  });
});

describe("fee and rate format", () => {
  // HANDOFF 0.72 第（4）点「运费输入为一到九位数字、可再跟小数点与一到两位数字，按文字换成整仙（整数部分乘 100 加补足两位的小数部分，不经浮点数相乘）」：
  // 0.1 与 0.10 都是 10 仙，8 为 800，8.5 为 850，9999.99 为 999999（0.1 + 0.2 一类的浮点误差不出现）。
  it("turns fee text into whole sen", () => {
    expect(feeSen("0.1")).toBe(10);
    expect(feeSen("0.10")).toBe(10);
    expect(feeSen("8")).toBe(800);
    expect(feeSen("8.5")).toBe(850);
    expect(feeSen("9999.99")).toBe(999999);
    expect(feeSen("0")).toBe(0);
    expect(feeSen("0.29")).toBe(29);
    expect(feeSen("1.15")).toBe(115);
    expect(feeSen("123456789.99")).toBe(12345678999);
  });

  // 同一条：「8.」「.5」「8.123」、负号、空白、空字符串与十位以上的整数部分都不是运费格式。取值范围（RM9,999.99 上限）以服务端为准，页面不查。
  it("rejects fee text that is not the format", () => {
    for (const text of ["8.", ".5", "8.123", "-8", "-0", " 8", "8 ", " ", "", "1234567890", "8,00", "RM8", "1e3", "８"]) {
      expect(feeSen(text), JSON.stringify(text)).toBeNull();
    }
  });

  // HANDOFF 0.72 第（4）点「汇率输入为数字、可再跟小数点与数字，原样提交」：整数与任意位小数都合格（位数与范围以服务端为准）。
  it("accepts rate text of digits with an optional fraction", () => {
    for (const text of ["34", "0.31", "0.000001", "3500", "1.2345678"]) {
      expect(isRateText(text), text).toBe(true);
    }
    for (const text of ["", " ", "1.", ".5", "-1", " 0.31", "0.31 ", "1,000", "1e3", "abc"]) {
      expect(isRateText(text), JSON.stringify(text)).toBe(false);
    }
  });
});

describe("save content", () => {
  // SHOP-TASK-175 验收第 4 条「保存内容按页面的顺序列出全部现有的行，马来西亚两档、其他国家与每个国家为保留的运费行，每个汇率为保留的汇率行，
  // 都带读取时的 id 与 version」与 HANDOFF 0.72 第（4）点：运费按文字换成整仙，汇率原样提交；没有删除项与新增项。
  it("keeps every existing row in page order with its id and version", () => {
    expect(saveContent(RATES, EDITED)).toEqual({
      malaysia: [
        { id: 1, version: 1, fee_sen: 900 },
        { id: 2, version: 1, fee_sen: 1500 },
      ],
      other: { id: 3, version: 4, fee_sen: 7550 },
      countries: [
        { id: 7, version: 1, fee_sen: 2150 },
        { id: 5, version: 2, fee_sen: 3000 },
        { id: 9, version: 1, fee_sen: 4550 },
      ],
      fx: [
        { id: 1, version: 2, rate: "0.32" },
        { id: 4, version: 1, rate: "34" },
      ],
    });
    expect(saveContent(RATES, initialInputs(RATES))).toEqual({
      malaysia: RATES.malaysia.map(({ id, version, fee_sen }) => ({ id, version, fee_sen })),
      other: { id: 3, version: 4, fee_sen: 8000 },
      countries: RATES.countries.map(({ id, version, fee_sen }) => ({ id, version, fee_sen })),
      fx: RATES.fx.map(({ id, version, rate }) => ({ id, version, rate })),
    });
  });

  // SHOP-TASK-175 验收第 4 条「点 common.save 时先按 HANDOFF 0.72 的格式检查全部输入框，任一不合格即不发请求」：任一组的任一输入框格式不对，保存内容为 null。
  it("has no save content when any input is not the format", () => {
    const broken: RatesInputs[] = [
      { ...EDITED, malaysia: ["9", "15."] },
      { ...EDITED, other: "" },
      { ...EDITED, countries: ["21.50", "-30", "45.50"] },
      { ...EDITED, fx: ["0.32", "3 4"] },
    ];
    for (const edited of broken) {
      expect(saveContent(RATES, edited)).toBeNull();
    }
  });

  // UX A06「输入本身不是这两种格式时，页面不提交，直接显示 [admin.rates_invalid]」与 SHOP-TASK-175 验收第 4 条：格式不对时不调用 fetch，
  // 保存按钮下方显示 admin.rates_invalid（role 为 alert，照 AdminAlert），已填的值保留。
  it.each(LANGUAGES)("shows rates_invalid without a request when the format is wrong in %s", async (language) => {
    const request = stubFetch();
    const edited: RatesInputs = { ...EDITED, countries: ["21.50", "8.123", "45.50"] };
    const state = ready({ inputs: edited });
    const moves = fakeMoves(startSaving(state));
    await submitRates(state, new AbortController().signal, moves.target);
    expect(request).not.toHaveBeenCalled();
    expect(moves.current).toEqual(ready({ inputs: edited, notice: "admin.rates_invalid" }));
    const area = saveArea(render(moves.current, language));
    expect(area).toContain(alertHtml(COPY["admin.rates_invalid"][language]));
    expect(inputs(render(moves.current, language)).map((input) => input.get("value"))).toEqual([...edited.malaysia, ...edited.countries, edited.other, ...edited.fx]);
  });
});

describe("save", () => {
  // SHOP-TASK-175 验收第 5 条「ok 以返回的整页数据替换页面（输入框改为返回的值，版本号随之更新），csrf_token 沿用读取时的值，在保存按钮下方显示
  // admin.rates_saved（role 为 status）」与 UX A06「成功显示 [admin.rates_saved]，页面改显示服务端返回的保存后数据（新的版本号）」：PUT 带读取的
  // CSRF 令牌与整页的保存内容，成功后输入框、汇率与版本号都是返回的值。
  it.each(LANGUAGES)("replaces the page with the saved data and shows rates_saved in %s", async (language) => {
    const request = stubFetch(json(200, SAVED));
    const state = ready({ inputs: EDITED });
    const moves = fakeMoves(startSaving(state));
    await submitRates(state, new AbortController().signal, moves.target);
    const [put] = requests(request);
    expect(requests(request)).toHaveLength(1);
    expect(put?.url).toBe(RATES_URL);
    expect(put?.method).toBe("PUT");
    expect(new Headers(put?.init?.headers).get(CSRF_HEADER)).toBe(CSRF);
    expect(bodyOf(put?.init)).toEqual(saveContent(RATES, EDITED));
    expect(moves.current).toEqual(ready({ base: SAVED, notice: "admin.rates_saved" }));
    const html = render(moves.current, language);
    expect(inputs(html).map((input) => input.get("value"))).toEqual(["9.00", "15.00", "21.50", "30.00", "45.50", "75.50", "0.32", "34"]);
    expect(textNodes(element(html, "details", "site-admin-shipping__fx"))).toContain(translate(language, "admin.fx_version", { n: 3 }));
    expect(textNodes(element(html, "details", "site-admin-shipping__fx"))).toContain(translate(language, "admin.fx_rate", { rate: "0.32", currency: "SGD" }));
    expect(saveArea(html)).toContain(`<span role="status">${escapeHtml(COPY["admin.rates_saved"][language])}</span>`);
  });

  // SHOP-TASK-175 验收第 5 条「csrf_token 沿用读取时的值」「none 回到 A01，csrf 照 A09 重新读取」：保存结果到页面状态的纯函数——成功时令牌为传入的读取令牌，
  // 401 为 login，403 为 reread。
  it("maps save results to page steps", () => {
    const ok = saveStep({ kind: "ok", rates: SAVED }, CSRF);
    if (typeof ok !== "function") {
      throw new Error("expected a change");
    }
    expect(ok(LOADING)).toEqual(ready({ base: SAVED, notice: "admin.rates_saved" }));
    expect(saveStep({ kind: "none" }, CSRF)).toBe("login");
    expect(saveStep({ kind: "csrf" }, CSRF)).toBe("reread");
  });

  // UX A06「服务端回答「取值不合格」时同样显示 [admin.rates_invalid]，整次保存不生效，页面保留已填的值」「服务端告知「数据已被改动」……时显示
  // [admin.rates_changed] 与 ( [admin.reload] )」「其他失败显示 [common.error_retry]，页面保留已填的值」，与 SHOP-TASK-175 验收第 5 条
  // 「network 显示 common.network_check，failed 显示 common.error_retry（都在保存按钮下方，保留已填的值）」：各失败只在保存按钮下方显示对应提示，
  // base 与已填的值都不变；只有 admin.rates_changed 带次要按钮 admin.reload。
  it.each(LANGUAGES)("keeps the filled values and shows the failure under save in %s", async (language) => {
    const cases: [Response | (() => never), SaveNotice][] = [
      [json(422, { detail: "rates_invalid" }), "admin.rates_invalid"],
      [json(409, { detail: "rates_changed" }), "admin.rates_changed"],
      [networkDown, "common.network_check"],
      [json(500, { detail: "boom" }), "common.error_retry"],
      [json(422, { detail: "other" }), "common.error_retry"],
    ];
    for (const [reply, notice] of cases) {
      stubFetch(reply);
      const state = ready({ inputs: EDITED });
      const moves = fakeMoves(startSaving(state));
      await submitRates(state, new AbortController().signal, moves.target);
      expect(moves.current, notice).toEqual(ready({ inputs: EDITED, notice }));
      const html = render(moves.current, language);
      expect(inputs(html).map((input) => input.get("value"))).toEqual([...EDITED.malaysia, ...EDITED.countries, EDITED.other, ...EDITED.fx]);
      const area = saveArea(html);
      const buttons = tags(area, "button");
      if (notice === "admin.rates_changed") {
        expect(element(area, "div", "site-admin-shipping__changed")).toBe(
          `<div class="acs-admin__alert site-admin-shipping__changed" role="alert"><span>${escapeHtml(COPY["admin.rates_changed"][language])}</span>` +
            `<button class="acs-admin__btn acs-admin__btn--secondary" type="button">${escapeHtml(COPY["admin.reload"][language])}</button></div>`,
        );
        expect(buttons).toHaveLength(2);
      } else {
        expect(area).toContain(alertHtml(COPY[notice][language]));
        expect(buttons).toHaveLength(1);
        expect(area).not.toContain(escapeHtml(COPY["admin.reload"][language]));
      }
      expect(area).not.toContain('role="status"');
      vi.unstubAllGlobals();
    }
  });

  // SHOP-TASK-175 验收第 5 条「none 回到 A01」：保存 401 时经路由的 replace 换成 A01，不更新表单。
  it("goes back to A01 when the save has no session", async () => {
    stubFetch(json(401, { detail: "not_authenticated" }));
    const moves = fakeMoves(startSaving(ready()));
    await submitRates(ready(), new AbortController().signal, moves.target);
    expect(moves.events).toEqual([`replace ${LOGIN_PATH}`]);
  });

  // SHOP-TASK-175 验收第 5 条「csrf 照 A09 重新读取并在表单上方显示 common.error_retry（照 AdminAlert，不随改值收起）」与 HANDOFF 0.72 第（5）点：
  // 保存 403 后先显示读取中，再以新读取的数据与令牌重置表单，表单上方（左栏最上）显示 common.error_retry，保存按钮下方没有提示。
  it.each(LANGUAGES)("rereads after a CSRF refusal and shows error_retry above the form in %s", async (language) => {
    const request = stubFetch(json(403, { detail: "csrf" }), json(200, { ...SAVED, csrf_token: "rates-csrf-2" }));
    const state = ready({ inputs: EDITED });
    const moves = fakeMoves(startSaving(state));
    await submitRates(state, new AbortController().signal, moves.target);
    expect(requests(request).map(({ method }) => method)).toEqual(["PUT", "GET"]);
    expect(moves.events).toEqual(["show loading", "show ready"]);
    expect(moves.current).toEqual(ready({ base: SAVED, csrfToken: "rates-csrf-2", alert: "common.error_retry" }));
    const html = render(moves.current, language);
    const fees = element(html, "div", "site-admin-shipping__fees");
    expect(fees.startsWith(`<div class="site-admin-shipping__fees">${alertHtml(COPY["common.error_retry"][language])}<details`)).toBe(true);
    expect(textNodes(saveArea(html))).toEqual([COPY["common.save"][language]]);
  });

  // UX A06「保存期间按钮禁用，防重复提交」、视觉稿 A06-desktop-states「保存中：Save 禁用」与 SHOP-TASK-175 验收第 5 条「保存期间 common.save 禁用」：
  // 点保存后按钮禁用并标 aria-busy，先前的提示（含表单上方的提示）都收起；不在保存中时按钮可用。
  it("disables save while saving", () => {
    const saving = startSaving(ready({ notice: "common.error_retry", alert: "common.error_retry" }));
    expect(saving).toEqual(ready({ saving: true }));
    const button = saveButton(render(saving));
    expect(button.has("disabled")).toBe(true);
    expect(button.get("aria-busy")).toBe("true");
    const idle = saveButton(render(ready()));
    expect(idle.has("disabled")).toBe(false);
    expect(idle.get("aria-busy")).toBe("false");
  });

  // SHOP-TASK-175 验收第 5 条（照 A09「离开页面或切换语言时中止……旧请求的结果不再更新页面」）：signal 已中止时保存的结果不更新表单。
  it("ignores the save result after the page is left", async () => {
    stubFetch(json(200, SAVED));
    const controller = new AbortController();
    const moves = fakeMoves(startSaving(ready()));
    const pending = submitRates(ready(), controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("editing", () => {
  // SHOP-TASK-175 验收第 4 条「运费与汇率输入框可改」：改一个输入框只换它的文字，其他输入框与 base 不变。
  it("changes only the edited input", () => {
    const state = ready();
    expect(changeInput(state, { group: "malaysia", index: 1 }, "16")).toEqual(ready({ inputs: { ...initialInputs(RATES), malaysia: ["8.00", "16"] } }));
    expect(changeInput(state, { group: "countries", index: 2 }, "")).toEqual(
      ready({ inputs: { ...initialInputs(RATES), countries: ["20.00", "30.00", ""] } }),
    );
    expect(changeInput(state, { group: "other" }, "70")).toEqual(ready({ inputs: { ...initialInputs(RATES), other: "70" } }));
    expect(changeInput(state, { group: "fx", index: 0 }, "0.3")).toEqual(ready({ inputs: { ...initialInputs(RATES), fx: ["0.3", "34"] } }));
    expect(changeInput(LOADING, { group: "other" }, "70")).toBe(LOADING);
  });

  // HANDOFF 0.72 第（5）点与 SHOP-TASK-175 验收第 4 条「改任何输入框即收起保存按钮下方的全部提示（admin.rates_saved、admin.rates_invalid、admin.rates_changed
  // 连同 admin.reload、common.error_retry 与 common.network_check）」「表单上方 403 后的提示照 A09 不随改值收起」：每种提示在改值后都收起，表单上方的提示留着。
  it.each(LANGUAGES)("hides every notice under save on any edit but keeps the alert above the form in %s", (language) => {
    const fields = [{ group: "malaysia", index: 0 }, { group: "countries", index: 1 }, { group: "other" }, { group: "fx", index: 1 }] as const;
    for (const notice of NOTICES) {
      for (const field of fields) {
        const changed = changeInput(ready({ notice, alert: "common.error_retry" }), field, "1");
        expect(changed.status === "ready" && changed.notice).toBeNull();
        const html = render(changed, language);
        expect(textNodes(saveArea(html))).toEqual([COPY["common.save"][language]]);
        expect(element(html, "div", "site-admin-shipping__fees")).toContain(alertHtml(COPY["common.error_retry"][language]));
      }
    }
  });
});
