import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

// 经 Vite 的 ?raw 读成字符串（与 i18n/copy.test.ts 相同），用来找出 UX-COPY 里全部 admin.sms_* 文案。
import uxCopy from "../../../docs/UX-COPY.md?raw";
import App from "../App";
import type { AdminSiteSettings, PaymentMethod } from "../api/adminSiteSettings";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
// 经 Vite 的 ?raw 读成字符串（与 AdminStoreDesignPage.test.tsx 相同），用来确认读取的 effect 与中止的写法、以及没有确认框。
import pageSource from "./AdminSiteSettingsPage.tsx?raw";
import AdminSiteSettingsPage, {
  AdminSiteSettingsContent,
  AdminSiteSettingsView,
  chooseMethod,
  LOADING,
  openSettings,
  readStep,
  saveStep,
  shownState,
  startSaving,
  STRIPE_NOTE_ID,
  submitSettings,
} from "./AdminSiteSettingsPage";
import type { SettingsChange, SettingsMoves, SettingsState } from "./AdminSiteSettingsPage";

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
  // SHOP-TASK-139 改动：SHOP-TASK-139 在标题之下加入支付方式表单（UX A09），「内容区只有标题」的逐字断言因此失效；按 SHOP-TASK-139
  // 验收第 6 条只改为断言内容区以原样的 h1 开头（表单本身由下面 SHOP-TASK-139 的各条测试）。
  it.each(LANGUAGES)("shows only the page title in the content area in %s", (language) => {
    const h1 = `<h1 class="acs-admin__h site-admin-settings__title">${escapeHtml(COPY["admin.nav_settings"][language])}</h1>`;
    expect(renderToStaticMarkup(wrap(<AdminSiteSettingsContent />, language)).startsWith(`<div class="site-admin-settings">${h1}`)).toBe(true);
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(main.startsWith(`<main class="acs-admin__main" aria-busy="false"><div class="site-admin-settings">${h1}`)).toBe(true);
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

// 以下为 SHOP-TASK-139（支付方式表单）的测试。

const LOGIN_PATH = "/admin/login";
const SETTINGS_URL = "/api/admin/site-settings";
const METHODS: PaymentMethod[] = ["simulated", "stripe_test"];

// 读取到的设置：当前为模拟，Stripe 测试凭据可用。
const SETTINGS: AdminSiteSettings = { payment_method: "simulated", stripe_available: true, csrf_token: "settings-csrf-1" };
// 当前已是 Stripe 测试而凭据后来失效（UX A09「当前已是「Stripe 测试」而凭据后来失效时」）。
const STALE_STRIPE: AdminSiteSettings = { payment_method: "stripe_test", stripe_available: false, csrf_token: "settings-csrf-1" };
// 凭据不可用、当前为模拟。
const UNAVAILABLE: AdminSiteSettings = { ...SETTINGS, stripe_available: false };

type ReadyState = Extract<SettingsState, { status: "ready" }>;

// 已取到的状态：选中项默认为 base 的当前值。
function readyState(overrides: Partial<ReadyState> = {}): SettingsState {
  const base = overrides.base ?? SETTINGS;
  return { status: "ready", base, choice: base.payment_method, saving: false, saved: false, rejected: false, alert: null, saveError: null, ...overrides };
}

// 本页内容区的视图（只依赖 props）。
function render(state: SettingsState = readyState(), language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AdminSiteSettingsView state={state} onChoose={() => undefined} onSave={() => undefined} />, language));
}

// 表单里的各单选（依出现顺序）的属性。
function radios(html: string): Map<string, string>[] {
  return tags(html, "input")
    .map((tag) => attributes(tag))
    .filter((input) => input.get("type") === "radio");
}

function group(html: string): string {
  return element(html, "fieldset", "site-admin-settings__group");
}

function saveArea(html: string): string {
  return element(html, "div", "site-admin-settings__save");
}

function saveButton(html: string): Map<string, string> {
  return attributes(tags(saveArea(html), "button")[0] ?? "");
}

function alertHtml(text: string): string {
  return `<div class="acs-admin__alert" role="alert"><span>${escapeHtml(text)}</span></div>`;
}

function stripeNoteHtml(language: Language): string {
  return `<div id="${STRIPE_NOTE_ID}" class="acs-admin__alert"><span>${escapeHtml(COPY["admin.stripe_not_configured"][language])}</span></div>`;
}

function occurrences(html: string, text: string): number {
  return html.split(escapeHtml(text)).length - 1;
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
function fakeMoves(initial: SettingsState = LOADING) {
  const events: string[] = [];
  const states: SettingsState[] = [];
  let current = initial;
  const target: SettingsMoves = {
    show: (state: SettingsState) => {
      current = state;
      states.push(state);
      events.push(`show ${state.status}`);
    },
    update: (change: SettingsChange) => {
      current = change(current);
      states.push(current);
      events.push(`update ${current.status}`);
    },
    replace: (path: RoutePath) => {
      events.push(`replace ${path}`);
    },
  };
  return {
    target,
    events,
    states,
    get current() {
      return current;
    },
  };
}

// 本页可能出现的各状态（读取中、读取失败、各种设置的表单、保存中、保存成功、保存被拒、保存失败与 403 之后）。
function allStates(): SettingsState[] {
  return [
    LOADING,
    { status: "failed", error: "common.network_check" },
    { status: "failed", error: "common.error_retry" },
    readyState(),
    readyState({ base: { ...SETTINGS, payment_method: "stripe_test" } }),
    readyState({ base: UNAVAILABLE }),
    readyState({ base: STALE_STRIPE }),
    readyState({ base: STALE_STRIPE, choice: "simulated" }),
    readyState({ saving: true }),
    readyState({ saved: true }),
    readyState({ choice: "stripe_test", rejected: true }),
    readyState({ base: STALE_STRIPE, rejected: true }),
    readyState({ saveError: "common.network_check" }),
    readyState({ saveError: "common.error_retry" }),
    readyState({ alert: "common.error_retry" }),
  ];
}

describe("payment method form", () => {
  // UX A09 桌面与手机线框「[admin.nav_settings] / [admin.payment_method] / (●) [admin.payment_simulated] ( ) [admin.payment_stripe] /
  // [admin.payment_method_hint] / ( [common.save] )」（短信验证段按 Kelvin 2026-10-07 与 2026-10-15 的决定不做）与 SHOP-TASK-139 验收第 2 条
  // 「标题之下为 admin.payment_method 一组两个单选项…其下 admin.payment_method_hint，再下为 common.save 按钮」：表单在 h1 之下，
  // 依次为一组（legend admin.payment_method、两个同名单选与提示）与唯一的提交按钮。
  it.each(LANGUAGES)("shows the two options, the hint and save under the title in %s", (language) => {
    const html = render(readyState(), language);
    const h1 = `<h1 class="acs-admin__h site-admin-settings__title">${escapeHtml(COPY["admin.nav_settings"][language])}</h1>`;
    expect(html.startsWith(`<div class="site-admin-settings">${h1}<div class="site-admin-settings__body" aria-busy="false"><form`)).toBe(true);
    const form = element(html, "form", "site-admin-settings__form");
    const keys: CopyKey[] = ["admin.payment_method", "admin.payment_simulated", "admin.payment_stripe", "admin.payment_method_hint", "common.save"];
    expect(textNodes(form)).toEqual(keys.map((key) => COPY[key][language]));
    expect(tags(form, "legend")).toHaveLength(1);
    expect(textNodes(group(form))[0]).toBe(COPY["admin.payment_method"][language]);
    expect(radios(form).map((input) => [input.get("name"), input.get("value"), input.has("disabled")])).toEqual([
      ["payment_method", "simulated", false],
      ["payment_method", "stripe_test", false],
    ]);
    expect(tags(form, "button")).toHaveLength(1);
    expect(saveButton(form).get("type")).toBe("submit");
    expect(textNodes(saveArea(form))).toEqual([COPY["common.save"][language]]);
  });

  // UX A09「默认 [admin.payment_simulated]」「两项照常显示当前值」与 SHOP-TASK-139 验收第 2 条「选中项为读取（或保存后）的当前值」：
  // 读取到哪一项，表单里就只有那一项选中。
  it("checks the current value from the read", () => {
    for (const method of METHODS) {
      const state = readStep({ kind: "ok", settings: { ...SETTINGS, payment_method: method } });
      if (state === "login") {
        throw new Error("unexpected login");
      }
      expect(radios(render(state)).map((input) => [input.get("value"), input.has("checked")])).toEqual([
        ["simulated", method === "simulated"],
        ["stripe_test", method === "stripe_test"],
      ]);
    }
  });

  // UX A09「服务端告知 Stripe 测试凭据…未配置或密钥不是测试密钥时，[admin.payment_stripe] 禁用、其下显示 [admin.stripe_not_configured]」、
  // DESIGN 1.12「Stripe 测试环境支付」第 2 条与视觉稿 A09-stripe-phone（禁用的选项以 aria-describedby 指向说明）：stripe_available 为假时
  // Stripe 单选禁用，说明紧接在它之后、提示之前；模拟照常可选。凭据可用时没有这段说明。
  it.each(LANGUAGES)("disables Stripe and explains why when the credentials are unavailable in %s", (language) => {
    const fieldset = group(render(readyState({ base: UNAVAILABLE }), language));
    expect(radios(fieldset).map((input) => [input.get("value"), input.has("disabled"), input.get("aria-describedby")])).toEqual([
      ["simulated", false, undefined],
      ["stripe_test", true, STRIPE_NOTE_ID],
    ]);
    expect(fieldset).toContain(stripeNoteHtml(language));
    expect(textNodes(fieldset)).toEqual(
      (["admin.payment_method", "admin.payment_simulated", "admin.payment_stripe", "admin.stripe_not_configured", "admin.payment_method_hint"] as const).map(
        (key) => COPY[key][language],
      ),
    );
    const available = render(readyState(), language);
    expect(available).not.toContain(escapeHtml(COPY["admin.stripe_not_configured"][language]));
    expect(available).not.toContain(STRIPE_NOTE_ID);
  });

  // UX A09「当前已是「Stripe 测试」而凭据后来失效时，两项照常显示当前值，仍可切回模拟」与 SHOP-TASK-139 验收第 2 条「当前值为 stripe_test 时
  // 照常显示为选中、仍可改选模拟并保存」：Stripe 选项禁用但仍选中，模拟可选；改选模拟后保存提交 simulated，成功后选中模拟。
  it.each(LANGUAGES)("keeps a stale Stripe choice checked and lets it switch back to simulated in %s", async (language) => {
    const state = readyState({ base: STALE_STRIPE });
    const html = render(state, language);
    expect(radios(html).map((input) => [input.get("value"), input.has("checked"), input.has("disabled")])).toEqual([
      ["simulated", false, false],
      ["stripe_test", true, true],
    ]);
    expect(group(html)).toContain(stripeNoteHtml(language));
    const switched = chooseMethod(state, "simulated");
    expect(radios(render(switched, language)).map((input) => input.has("checked"))).toEqual([true, false]);
    const request = stubFetch(json(200, { payment_method: "simulated" }));
    const moves = fakeMoves(startSaving(switched));
    await submitSettings(STALE_STRIPE, "simulated", new AbortController().signal, moves.target);
    expect(bodyOf(requests(request)[0]?.init)).toEqual({ payment_method: "simulated" });
    expect(moves.current).toEqual(readyState({ base: { ...STALE_STRIPE, payment_method: "simulated" }, saved: true }));
  });

  // UX A09「页面不另加确认框」与 SHOP-TASK-139 验收第 2 条「不加确认框」：点保存直接调用保存（先进入保存中），页面源码与各状态都没有
  // 确认框（confirm、dialog）。
  it("saves without a confirmation dialog", () => {
    expect(pageSource).toMatch(/setEntry\(\{ language, state: startSaving\(state\) \}\);\s*void submitSettings\(/);
    expect(pageSource).not.toMatch(/\bconfirm\(|<dialog|"dialog"|alertdialog/);
    for (const state of allStates()) {
      expect(render(state)).not.toMatch(/<dialog\b|role="(alert)?dialog"/);
    }
  });
});

describe("no SMS verification", () => {
  // Kelvin 2026-10-07（docs/HANDOFF.md）「后台站点设置 A09（短信验证开关）的保存接口与页面等短信验证服务与会员结账路径上线后再做…在那之前
  // 不提供任何打开它的入口」与 2026-10-15「后台站点设置 A09 先只放支付方式开关」，SHOP-TASK-139 验收第 2 条「不出现任何 admin.sms_* 文字
  // 或短信验证开关」：表单的各状态都没有 UX-COPY 里 admin.sms_* 的文字，单选只有支付方式一组、没有勾选框；字典也没有 admin.sms_* 条目。
  it.each(LANGUAGES)("shows no SMS text or switch in any state in %s", (language) => {
    const texts = smsTexts(language);
    expect(texts.length).toBeGreaterThan(0);
    for (const state of allStates()) {
      const html = render(state, language);
      const shown = visibleTexts(html);
      for (const text of texts) {
        expect(shown).not.toContain(text);
      }
      expect(radios(html).every((input) => input.get("name") === "payment_method")).toBe(true);
      expect(tags(html, "input").filter((tag) => attributes(tag).get("type") !== "radio")).toEqual([]);
    }
    expect(Object.keys(COPY).filter((key) => key.startsWith("admin.sms_"))).toEqual([]);
  });
});

describe("reading", () => {
  // SHOP-TASK-139 验收第 3 条「读取…进行中标 aria-busy、不显示表单」：读取中表单区标 aria-busy 且为空；取到后为 false。
  it("marks the form area busy and shows no form while reading", () => {
    expect(element(render(LOADING), "div", "site-admin-settings__body")).toBe(`<div class="site-admin-settings__body" aria-busy="true"></div>`);
    expect(attributes(tags(element(render(), "div", "site-admin-settings__body"), "div")[0] ?? "").get("aria-busy")).toBe("false");
  });

  // 同一条「none 用路由的 replace 进入 /admin/login；network 显示 common.network_check，其他失败显示 common.error_retry，都不显示表单」：
  // 经 SHOP-TASK-138 的请求模块 GET 一次（带中止用的 signal）；200 以结果为选中项，401 以 replace 进入 /admin/login，
  // 网络中断、其他状态与不合格响应体显示相应提示。
  it.each<[Response | (() => never), string[], SettingsState | null]>([
    [json(200, SETTINGS), ["show ready"], readyState()],
    [json(200, STALE_STRIPE), ["show ready"], readyState({ base: STALE_STRIPE })],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [networkDown, ["show failed"], { status: "failed", error: "common.network_check" }],
    [json(500, {}), ["show failed"], { status: "failed", error: "common.error_retry" }],
    [json(200, { ...SETTINGS, csrf_token: "" }), ["show failed"], { status: "failed", error: "common.error_retry" }],
  ])("reads the settings from the reply (%#)", async (reply, events, state) => {
    const request = stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    await openSettings(controller.signal, moves.target);
    const calls = requests(request);
    expect(calls.map((call) => [call.method, call.url])).toEqual([["GET", SETTINGS_URL]]);
    expect(calls[0]?.init?.signal).toBe(controller.signal);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 同一条的规则部分：读取结果到显示的对应。
  it("chooses what to show from the read", () => {
    expect(readStep({ kind: "ok", settings: SETTINGS })).toEqual(readyState());
    expect(readStep({ kind: "ok", settings: SETTINGS }, "common.error_retry")).toEqual(readyState({ alert: "common.error_retry" }));
    expect(readStep({ kind: "none" })).toBe("login");
    expect(readStep({ kind: "network" })).toEqual({ status: "failed", error: "common.network_check" });
    expect(readStep({ kind: "failed" })).toEqual({ status: "failed", error: "common.error_retry" });
  });

  // 同一条「都不显示表单」：失败时表单区只有提示（role="alert"），没有表单、单选或按钮；标题照常。
  it.each(LANGUAGES)("shows a read failure without the form in %s", (language) => {
    for (const key of ["common.network_check", "common.error_retry"] as const) {
      const html = render({ status: "failed", error: key }, language);
      expect(element(html, "div", "site-admin-settings__body")).toBe(`<div class="site-admin-settings__body" aria-busy="false">${alertHtml(COPY[key][language])}</div>`);
      expect(html).not.toMatch(/<(form|fieldset|input|button)\b/);
    }
  });

  // 同一条「离开页面时中止，中止后的结果不再更新页面」：signal 中止之后，不论回答什么（设置、401 或失败）都不显示也不跳转。
  it.each([json(200, SETTINGS), json(401, { detail: "admin_session_required" }), json(500, {}), networkDown])("ignores a read after it was aborted (%#)", async (reply) => {
    stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    const pending = openSettings(controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("language", () => {
  // SHOP-TASK-139 验收第 3 条「打开页面与切换语言时读取」：读取的 effect 以界面语言为依赖（换语言即清理并重新读取）；换了语言而新结果
  // 未到时按读取中显示（不显示旧语言的表单）。
  it("reads again when the interface language changes", () => {
    expect(pageSource).toMatch(/void openSettings\(controller\.signal, pageMoves\(language, setEntry, replace\)\);\s*\}\);\s*\}, \[language, replace\]\);/);
    expect(pageSource).toMatch(/if \(entry\.language !== language\) \{\s*setEntry\(\{ language, state: LOADING \}\);\s*\}/);
    const entry = { language: "en" as const, state: readyState({ choice: "stripe_test", saved: true }) };
    expect(shownState(entry, "en")).toBe(entry.state);
    expect(shownState(entry, "zh")).toBe(LOADING);
    expect(shownState(entry, "ms")).toBe(LOADING);
  });

  // 同一条「离开页面时中止」：effect 的清理（startDeferred 返回的函数）中止当前语言的 controller，保存用的正是这个 controller 的 signal。
  it("aborts the read and the save together on cleanup", () => {
    expect(pageSource).toMatch(/lifetime\.current = controller;\s*return startDeferred\(controller,/);
    expect(pageSource).toMatch(/const controller = lifetime\.current;[\s\S]*?submitSettings\(state\.base, state\.choice, controller\.signal,/);
  });
});

describe("saving", () => {
  // SHOP-TASK-139 验收第 3 条「点 common.save 以当前选择与读取给的 csrf_token 调用保存」：PUT 一次，请求头带读取给的 CSRF 令牌，
  // 请求体为当前选择，带中止用的 signal。
  it.each(METHODS)("saves the current choice with the read CSRF token (%s)", async (method) => {
    const request = stubFetch(json(200, { payment_method: method }));
    const controller = new AbortController();
    await submitSettings(SETTINGS, method, controller.signal, fakeMoves(startSaving(readyState({ choice: method }))).target);
    const calls = requests(request);
    expect(calls.map((call) => [call.method, call.url])).toEqual([["PUT", SETTINGS_URL]]);
    expect(calls[0]?.init?.signal).toBe(controller.signal);
    expect(new Headers(calls[0]?.init?.headers).get("X-CSRF-Token")).toBe(SETTINGS.csrf_token);
    expect(bodyOf(calls[0]?.init)).toEqual({ payment_method: method });
  });

  // 同一条「进行中按钮禁用并标 aria-busy」：点保存后 common.save（submit）禁用并标 aria-busy，先前的提示都收起；其余时候可点、aria-busy 为 false。
  it.each(LANGUAGES)("disables the save button while saving in %s", (language) => {
    const saving = startSaving(readyState({ saved: true, rejected: true, alert: "common.error_retry", saveError: "common.network_check" }));
    expect(saving).toEqual(readyState({ saving: true }));
    const html = render(saving, language);
    expect([saveButton(html).has("disabled"), saveButton(html).get("aria-busy")]).toEqual([true, "true"]);
    expect(textNodes(saveArea(html))).toEqual([COPY["common.save"][language]]);
    const idle = render(readyState(), language);
    expect([saveButton(idle).has("disabled"), saveButton(idle).get("aria-busy")]).toEqual([false, "false"]);
    expect(startSaving(LOADING)).toBe(LOADING);
  });

  // UX A09「保存成功：[admin.settings_saved]」与 SHOP-TASK-139 验收第 3 条「ok 以返回的值更新选中项并显示 admin.settings_saved」：
  // 保存成功后选中项与之后保存的依据为返回的支付方式（stripe_available 与 CSRF 令牌沿用读取的），保存按钮下方显示 admin.settings_saved。
  it.each(LANGUAGES)("updates the choice from the reply and shows the saved message in %s", async (language) => {
    stubFetch(json(200, { payment_method: "stripe_test" }));
    const moves = fakeMoves(startSaving(readyState({ choice: "stripe_test" })));
    await submitSettings(SETTINGS, "stripe_test", new AbortController().signal, moves.target);
    expect(moves.events).toEqual(["update ready"]);
    expect(moves.current).toEqual(readyState({ base: { ...SETTINGS, payment_method: "stripe_test" }, saved: true }));
    const html = render(moves.current, language);
    expect(radios(html).map((input) => input.has("checked"))).toEqual([false, true]);
    expect(saveArea(html)).toContain(`<span role="status">${escapeHtml(COPY["admin.settings_saved"][language])}</span>`);
    expect(textNodes(saveArea(html))).toEqual([COPY["common.save"][language], COPY["admin.settings_saved"][language]]);
    // 返回的值与提交的不同时，选中项以返回的为准。
    const step = saveStep({ kind: "ok", payment_method: "simulated" }, SETTINGS);
    if (typeof step !== "function") {
      throw new Error("expected a change");
    }
    expect(step(readyState({ choice: "stripe_test", saving: true }))).toEqual(readyState({ saved: true }));
  });

  // 同一条「改选任何一项后收起」：保存成功后改选任一项，admin.settings_saved 不再显示。
  it("hides the saved message after choosing either option", () => {
    for (const method of METHODS) {
      const next = chooseMethod(readyState({ saved: true }), method);
      expect(next).toEqual(readyState({ choice: method }));
      expect(render(next)).not.toContain(escapeHtml(COPY["admin.settings_saved"].en));
    }
    expect(chooseMethod(LOADING, "stripe_test")).toBe(LOADING);
  });

  // UX A09「保存时服务端再次检查，被拒则显示 [admin.stripe_not_configured]、本次保存不生效」与 SHOP-TASK-139 验收第 3 条「not_configured
  // 在保存按钮下方（与 admin.settings_saved 同一位置）显示 admin.stripe_not_configured、保留表单、选中项不变…改选任何一项后收起」。
  it.each(LANGUAGES)("shows the rejection under the save button and keeps the choice in %s", async (language) => {
    stubFetch(json(422, { detail: "stripe_not_configured" }));
    const moves = fakeMoves(startSaving(readyState({ choice: "stripe_test" })));
    await submitSettings(SETTINGS, "stripe_test", new AbortController().signal, moves.target);
    expect(moves.current).toEqual(readyState({ choice: "stripe_test", rejected: true }));
    const html = render(moves.current, language);
    expect(saveArea(html)).toContain(alertHtml(COPY["admin.stripe_not_configured"][language]));
    expect(textNodes(saveArea(html))).toEqual([COPY["common.save"][language], COPY["admin.stripe_not_configured"][language]]);
    expect(saveButton(html).has("disabled")).toBe(false);
    expect(radios(html).map((input) => input.has("checked"))).toEqual([false, true]);
    expect(html).not.toContain(escapeHtml(COPY["admin.settings_saved"][language]));
    for (const method of METHODS) {
      const next = chooseMethod(moves.current, method);
      expect(next).toEqual(readyState({ choice: method }));
      expect(textNodes(saveArea(render(next, language)))).toEqual([COPY["common.save"][language]]);
    }
  });

  // 同一条「它与 Stripe 选项下方的禁用说明各自独立，两者可同时出现，改选任何一项后收起」：凭据失效而当前为 Stripe 测试时保存被拒，
  // 说明同时出现在 Stripe 选项下方与保存按钮下方；改选后只收起保存按钮下方的一处。
  it.each(LANGUAGES)("shows the rejection and the disabled note independently in %s", (language) => {
    const text = COPY["admin.stripe_not_configured"][language];
    const both = render(readyState({ base: STALE_STRIPE, rejected: true }), language);
    expect(group(both)).toContain(stripeNoteHtml(language));
    expect(saveArea(both)).toContain(alertHtml(text));
    expect(occurrences(both, text)).toBe(2);
    for (const method of METHODS) {
      const after = render(chooseMethod(readyState({ base: STALE_STRIPE, rejected: true }), method), language);
      expect(group(after)).toContain(stripeNoteHtml(language));
      expect(saveArea(after)).not.toContain(escapeHtml(text));
      expect(occurrences(after, text)).toBe(1);
    }
  });

  // SHOP-TASK-139 验收第 3 条「none 进入 /admin/login」：保存得到 401 时以 replace 进入 /admin/login，不更新表单。
  it("goes to the login page on 401", async () => {
    stubFetch(json(401, { detail: "admin_session_required" }));
    const moves = fakeMoves(startSaving(readyState()));
    await submitSettings(SETTINGS, "simulated", new AbortController().signal, moves.target);
    expect(moves.events).toEqual([`replace ${LOGIN_PATH}`]);
  });

  // 同一条「csrf 照 AdminStoreDesignPage.tsx 的写法重新读取后在表单上方显示 common.error_retry」：403 后先显示读取中（不显示表单），
  // 再以同一 signal 读取；取得后选中项按新结果重置，表单最上方为 common.error_retry。
  it.each(LANGUAGES)("reads again after 403 and shows the error above the form in %s", async (language) => {
    const fresh: AdminSiteSettings = { payment_method: "stripe_test", stripe_available: true, csrf_token: "settings-csrf-2" };
    const request = stubFetch(json(403, { detail: "csrf_invalid" }), json(200, fresh));
    const moves = fakeMoves(startSaving(readyState()));
    const controller = new AbortController();
    await submitSettings(SETTINGS, "simulated", controller.signal, moves.target);
    const calls = requests(request);
    expect(calls.map((call) => [call.method, call.url])).toEqual([
      ["PUT", SETTINGS_URL],
      ["GET", SETTINGS_URL],
    ]);
    expect(calls[1]?.init?.signal).toBe(controller.signal);
    expect(moves.events).toEqual(["show loading", "show ready"]);
    expect(moves.states[0]).toBe(LOADING);
    expect(moves.current).toEqual(readyState({ base: fresh, alert: "common.error_retry" }));
    const form = element(render(moves.current, language), "form", "site-admin-settings__form");
    expect(form.slice(form.indexOf(">") + 1).startsWith(alertHtml(COPY["common.error_retry"][language]))).toBe(true);
  });

  // 同一条（照 AdminStoreDesignPage.tsx）：403 后的重新读取与首次读取的处理相同——401 进入 /admin/login，网络中断与其他失败只显示提示。
  it.each<[Response | (() => never), string[], SettingsState | null]>([
    [json(401, { detail: "admin_session_required" }), ["show loading", `replace ${LOGIN_PATH}`], null],
    [networkDown, ["show loading", "show failed"], { status: "failed", error: "common.network_check" }],
    [json(500, {}), ["show loading", "show failed"], { status: "failed", error: "common.error_retry" }],
  ])("handles the read after 403 like the first read (%#)", async (reread, events, state) => {
    stubFetch(json(403, { detail: "csrf_invalid" }), reread);
    const moves = fakeMoves(startSaving(readyState()));
    await submitSettings(SETTINGS, "simulated", new AbortController().signal, moves.target);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.current).toEqual(state);
    }
  });

  // SHOP-TASK-139 验收第 3 条「network 显示 common.network_check；failed 显示 common.error_retry」：保存失败后按钮恢复可点，表单与选中项
  // 保留（含保存进行中的改选），提示在保存按钮下方；不显示 admin.settings_saved，也不按保存被拒显示 admin.stripe_not_configured。
  it.each<[Response | (() => never), CopyKey]>([
    [networkDown, "common.network_check"],
    [json(500, {}), "common.error_retry"],
    [json(422, { detail: [{ loc: ["body", "payment_method"] }] }), "common.error_retry"],
    [json(200, { payment_method: "stripe" }), "common.error_retry"],
  ])("keeps the form when the save fails (%#)", async (reply, key) => {
    stubFetch(reply);
    const moves = fakeMoves(startSaving(readyState({ choice: "stripe_test" })));
    moves.target.update((current) => chooseMethod(current, "simulated"));
    await submitSettings(SETTINGS, "stripe_test", new AbortController().signal, moves.target);
    expect(moves.current).toEqual(readyState({ saveError: key }));
    for (const language of LANGUAGES) {
      const html = render(moves.current, language);
      expect(textNodes(saveArea(html))).toEqual([COPY["common.save"][language], COPY[key][language]]);
      expect(saveArea(html)).toContain(alertHtml(COPY[key][language]));
      expect(saveButton(html).has("disabled")).toBe(false);
      expect(tags(html, "fieldset")).toHaveLength(1);
      expect(html).not.toContain(escapeHtml(COPY["admin.settings_saved"][language]));
      expect(html).not.toContain(escapeHtml(COPY["admin.stripe_not_configured"][language]));
    }
  });

  // SHOP-TASK-139 验收第 3 条「离开页面时中止，中止后的结果不再更新页面」：保存的 signal 中止后，不论回答什么都不更新、不跳转，
  // 也不在 403 后重新读取。
  it.each([
    json(200, { payment_method: "simulated" }),
    json(401, { detail: "admin_session_required" }),
    json(403, { detail: "csrf_invalid" }),
    json(422, { detail: "stripe_not_configured" }),
    json(500, {}),
    networkDown,
  ])("ignores a save after it was aborted (%#)", async (reply) => {
    const request = stubFetch(reply, json(200, SETTINGS));
    const controller = new AbortController();
    const moves = fakeMoves(startSaving(readyState()));
    const pending = submitSettings(SETTINGS, "simulated", controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
    expect(request).toHaveBeenCalledTimes(1);
  });
});

describe("dictionary for the form", () => {
  // SHOP-TASK-139 验收第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」与第 5 条「页面文字全部来自字典」：读取中、读取失败、
  // 各种设置的表单、保存中、保存成功、保存被拒、保存失败与 403 之后，每段文字（含 aria-label）都是当前语言的字典文案；
  // 本任务加入的六条文案与 UX-COPY 0.12 逐字相同（i18n/copy.test.ts 另行核对全部条目）。
  it.each(LANGUAGES)("shows only dictionary text in every state in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const state of allStates()) {
      for (const value of visibleTexts(render(state, language))) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
    const column = { en: 2, zh: 3, ms: 4 }[language];
    for (const key of ["admin.payment_method", "admin.payment_simulated", "admin.payment_stripe", "admin.payment_method_hint", "admin.stripe_not_configured", "admin.settings_saved"] as const) {
      const row = uxCopy.split("\n").find((line) => line.startsWith(`| \`${key}\` |`)) ?? "";
      expect(row.split("|")[column]?.trim()).toBe(COPY[key][language]);
    }
  });
});
