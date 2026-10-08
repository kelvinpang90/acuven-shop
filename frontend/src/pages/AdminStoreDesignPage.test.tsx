import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminStoreDesign, AdminStoreDesignDetail } from "../api/adminStoreDesign";
import { AdminFrameView } from "../components/AdminFrame";
import type { AdminFrameViewProps, FrameState } from "../components/AdminFrame";
import { BRAND, COPY, LANGUAGES, translate } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import { SHOP_THEMES, THEME_ACCENTS } from "../storeDesign";
import type { ShopTheme } from "../storeDesign";
// 经 Vite 的 ?raw 读成字符串（与 i18n/copy.test.ts 读 UX-COPY 相同），用来确认本页只经店铺装修的请求模块读取与保存。
import pageSource from "./AdminStoreDesignPage.tsx?raw";
import {
  AdminStoreDesignContent,
  AdminStoreDesignView,
  BLOCK_NAME,
  chooseAccent,
  chooseTheme,
  editForm,
  formOf,
  LOADING,
  moveBlock,
  openDesign,
  readStep,
  saveInput,
  shownState,
  showBlock,
  startSaving,
  submitDesign,
  THEME_NAME,
} from "./AdminStoreDesignPage";
import type { AdminStoreDesignViewProps, DesignChange, DesignForm, DesignMoves, DesignState } from "./AdminStoreDesignPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const STORE_DESIGN_PATH = "/admin/store-design";
const ORDERS_PATH = "/admin/orders";
const REFUNDS_PATH = "/admin/refunds";
const STOCK_RESETS_PATH = "/admin/stock-resets";
const LOGIN_PATH = "/admin/login";
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

// UX A08 与视觉稿的设置：咖啡店主题、默认主色（null），区块依次为主视觉、演示怎么玩、精选（显示）与按分类浏览（隐藏）；
// 精选两件（第二件之后已下架）。
const DETAIL: AdminStoreDesignDetail = {
  theme: "kopitiam",
  accent: null,
  home_blocks: [
    { block: "hero", visible: true },
    { block: "how", visible: true },
    { block: "featured", visible: true },
    { block: "categories", visible: false },
  ],
  featured: [
    { product_id: 7, slug: "crew-neck-tee", name: "Crew Neck Tee", published: true },
    { product_id: 3, slug: "ceramic-mug", name: "Ceramic Mug", published: false },
  ],
  choices: [
    { product_id: 3, slug: "ceramic-mug", name: "Ceramic Mug" },
    { product_id: 7, slug: "crew-neck-tee", name: "Crew Neck Tee" },
  ],
  csrf_token: "design-csrf-1",
};

// 保存的响应体（不含 choices 与 csrf_token）。
function savedBody(design: AdminStoreDesign): AdminStoreDesign {
  return { theme: design.theme, accent: design.accent, home_blocks: design.home_blocks, featured: design.featured };
}

function readyState(overrides: Partial<Extract<DesignState, { status: "ready" }>> = {}): DesignState {
  return { status: "ready", base: DETAIL, form: formOf(DETAIL), saving: false, saved: false, alert: null, saveError: null, ...overrides };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function renderApp(path: string, language: Language = "en"): string {
  return renderToStaticMarkup(<App initialPath={path} storage={languageStorage(language)} />);
}

function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={STORE_DESIGN_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

// 已登录时的本页：后台框架（会话请求以 state 替身代替）里放本页的内容区。
function renderSignedIn(language: Language = "en", overrides: Partial<AdminFrameViewProps> = {}): string {
  const props: AdminFrameViewProps = {
    mode: "light",
    current: "storeDesign",
    state: SIGNED_IN,
    busy: false,
    menuOpen: false,
    onToggleMenu: noop,
    onCloseMenu: noop,
    onLogOut: noop,
    children: <AdminStoreDesignContent />,
    ...overrides,
  };
  return renderToStaticMarkup(wrap(<AdminFrameView {...props} />, language));
}

function render(state: DesignState = readyState(), language: Language = "en"): string {
  const props: AdminStoreDesignViewProps = { state, onTheme: noop, onAccent: noop, onShow: noop, onMove: noop, onSave: noop };
  return renderToStaticMarkup(wrap(<AdminStoreDesignView {...props} />, language));
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...attributes].filter((value) => value !== "");
}

// 文本节点（不含属性），依出现顺序。
function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
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
  return (attributes(tag).get("class") ?? "").split(/\s+/).filter((name) => name !== "");
}

// 某个元素（按 class 找到的第一个）从开始标签到与之配对的结束标签的内容（数同名元素的嵌套）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) => classes(m[0]).includes(className))?.index;
  if (start === undefined) {
    throw new Error(`not found: <${name} class="${className}">`);
  }
  return elementAt(html, name, start);
}

function elementAt(html: string, name: string, start: number): string {
  const pattern = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  pattern.lastIndex = start;
  let depth = 0;
  for (let m = pattern.exec(html); m !== null; m = pattern.exec(html)) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, m.index + m[0].length);
    }
  }
  throw new Error(`unclosed: <${name}>`);
}

// 某个 class 的全部元素（同名元素嵌套时外层在前）。
function allOf(html: string, name: string, className: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].filter((m) => classes(m[0]).includes(className)).map((m) => elementAt(html, name, m.index));
}

// 第 index 个分组（主题、主色、首页区块）。
function group(html: string, index: number): string {
  return allOf(html, "fieldset", "site-admin-store-design__group")[index] ?? "";
}

function legend(fieldset: string): string {
  return textNodes(elementAt(fieldset, "legend", fieldset.indexOf("<legend"))).join("");
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | (() => never))[]) {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
      const next = queue.shift();
      if (next === undefined) {
        return Promise.reject(new Error("unexpected request"));
      }
      return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

function bodyOf(call: Call | undefined): unknown {
  return JSON.parse(call?.init.body as string);
}

function headerOf(call: Call | undefined, name: string): string | undefined {
  return (call?.init.headers as Record<string, string> | undefined)?.[name];
}

// 路由替身：保存当前状态（update 按它改动），记录每一步与经 replace 去的地址。
function fakeMoves(initial: DesignState = LOADING) {
  const events: string[] = [];
  const states: DesignState[] = [];
  let current = initial;
  const target: DesignMoves = {
    show: (state: DesignState) => {
      current = state;
      states.push(state);
      events.push(`show ${state.status}`);
    },
    update: (change: DesignChange) => {
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

describe("route", () => {
  // UX 页面地图「A08 后台店铺装修（0.4） /admin/store-design」与「管理后台总体」「每页顶部常驻 [admin.demo_banner]」；
  // SHOP-TASK-062 验收第 2 条「新文件 AdminStoreDesignPage.tsx 用后台框架渲染」：经路由打开 /admin/store-design（服务端渲染，会话未返回），
  // 根元素为 acs-admin 并有后台演示横幅，内容区为空并标 aria-busy；不套前台框架（没有前台页头、页脚、品牌名与前台演示横幅）。
  it.each(LANGUAGES)("renders the admin frame without the storefront frame in %s", (language) => {
    const html = renderApp(STORE_DESIGN_PATH, language);
    expect(classes(html.slice(0, html.indexOf(">") + 1))).toContain("acs-admin");
    expect(element(html, "div", "acs-admin__banner")).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).not.toMatch(/<(header|footer)\b/);
    expect(html).not.toContain(BRAND);
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
  });
});

describe("navigation", () => {
  // UX「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、…、[admin.nav_stock_resets]、[admin.nav_store_design]（0.4）、…」、
  // UX A08「入口：后台导航 [admin.nav_store_design]」与 Kelvin 2026-10-06（docs/HANDOFF.md 0.35）「后台导航只显示已上线页面的导航项」；
  // SHOP-TASK-062 验收第 2、3 条「当前导航项为店铺装修」「按 UX 顺序只含已上线的订单、退款、库存重置与店铺装修四项（桌面导航与 ☰ 菜单同样）」：
  // 经路由打开本页，桌面导航与 ☰ 菜单都依次为这四项，店铺装修标 aria-current="page"，没有其他导航项。
  it.each(LANGUAGES)("marks the store design item as current, after the other three, in %s", (language) => {
    const html = renderApp(STORE_DESIGN_PATH, language);
    for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
      const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
      expect(items.map((tag) => [attributes(tag).get("href"), attributes(tag).get("aria-current")])).toEqual([
        [ORDERS_PATH, undefined],
        [REFUNDS_PATH, undefined],
        [STOCK_RESETS_PATH, undefined],
        [STORE_DESIGN_PATH, "page"],
      ]);
      expect(textNodes(nav).slice(0, 4)).toEqual([
        COPY["admin.nav_orders"][language],
        COPY["admin.nav_refunds"][language],
        COPY["admin.nav_stock_resets"][language],
        COPY["admin.nav_store_design"][language],
      ]);
    }
  });

  // UX A08 手机线框「☰  [admin.nav_store_design]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」；
  // SHOP-TASK-062 验收第 2 条「手机顶栏标题为 admin.nav_store_design」。
  it.each(LANGUAGES)("names the store design item in the phone bar in %s", (language) => {
    const html = renderApp(STORE_DESIGN_PATH, language);
    expect(element(html, "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_store_design"][language])}</span>`,
    );
  });
});

describe("content", () => {
  // UX A08 桌面线框「[admin.nav_store_design]」其下「[admin.design_demo_note]」，再下为各组；SHOP-TASK-064 验收第 4 条
  // 「打开页面读取，进行中标 aria-busy、取得前不显示表单」：已登录时内容区只有这一个 h1，其后为演示说明与表单区；
  // 刚打开时表单区标 aria-busy、还没有表单。
  // SHOP-TASK-064 改动：原「shows only the title and the demo note in the content area」（SHOP-TASK-062 守住「内容区只有标题与说明，
  // 表单由 SHOP-TASK-064 接上」）改为标题与说明之后为表单区；标题与说明的写法不变。
  it.each(LANGUAGES)("shows the title and the demo note above the form area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(attributes(tags(main, "main")[0] ?? "").get("aria-busy")).toBe("false");
    expect(tags(main, "h1")).toHaveLength(1);
    expect(element(main, "div", "site-admin-store-design")).toBe(
      `<div class="site-admin-store-design"><h1 class="acs-admin__h site-admin-store-design__title">${escapeHtml(COPY["admin.nav_store_design"][language])}</h1>` +
        `<p class="site-admin-store-design__note">${escapeHtml(COPY["admin.design_demo_note"][language])}</p>` +
        `<div class="site-admin-store-design__body" aria-busy="true"></div></div>`,
    );
    expect(textNodes(main)).toEqual([COPY["admin.nav_store_design"][language], COPY["admin.design_demo_note"][language]]);
    expect(main).not.toMatch(/<(form|fieldset|input|select|button|a)\b/);
  });

  // UX A08「演示提示：★ [admin.demo_banner]；[admin.design_demo_note]（演示横幅与演示提示始终显示，这里不能关闭）」：
  // 本页同时有后台演示横幅与演示说明，二者都不带 hidden，也没有关闭它们的控件。
  // SHOP-TASK-064 改动：原断言「内容区没有按钮」（SHOP-TASK-062 时内容区只有页头）改为表单取得后内容区的按钮只有各区块的上移、
  // 下移与 common.save，没有别的按钮；横幅与说明的断言不变。
  it.each(LANGUAGES)("keeps the demo banner and the demo note in %s", (language) => {
    const html = renderSignedIn(language);
    expect(element(html, "div", "acs-admin__banner")).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    const note = element(html, "p", "site-admin-store-design__note");
    expect(attributes(tags(note, "p")[0] ?? "").has("hidden")).toBe(false);
    expect(textNodes(note)).toEqual([COPY["admin.design_demo_note"][language]]);
    expect(tags(element(html, "main", "acs-admin__main"), "button")).toEqual([]);
    const ready = render(readyState(), language);
    expect(element(ready, "p", "site-admin-store-design__note")).toContain(escapeHtml(COPY["admin.design_demo_note"][language]));
    const buttons = tags(ready, "button").map((tag) => attributes(tag).get("aria-label") ?? "submit");
    expect(buttons).toEqual([
      ...DETAIL.home_blocks.flatMap(() => [COPY["admin.block_move_up"][language], COPY["admin.block_move_down"][language]]),
      "submit",
    ]);
  });

  // SHOP-TASK-064 验收第 4 条「经 SHOP-TASK-063 的请求模块」与第 7 条「不改 frontend/src/api/adminStoreDesign.ts」：本页只经
  // api/adminStoreDesign 读取与保存，不引入其他接口模块，也不直接调用 fetch。
  // SHOP-TASK-064 改动：原「makes no requests of its own」（SHOP-TASK-062 守住「页面不发起自己的请求（表单由 SHOP-TASK-064 接上）」）
  // 改为只经店铺装修的请求模块，因为本任务就是接上读取与保存。
  it("reads and saves only through the store design API module", () => {
    expect([...pageSource.matchAll(/from "\.\.\/api\/([^"]+)"/g)].map((m) => m[1])).toEqual(["adminStoreDesign", "adminStoreDesign"]);
    expect(pageSource).not.toMatch(/\bfetch\s*\(/);
  });

  // Kelvin 2026-10-06（docs/HANDOFF.md 0.34）「店铺装修设置的存储与公开读取首版先不含标志图」与验收第 1 条「不显示 admin.logo 一组」，
  // 以及目的「精选商品本任务只原样提交、不显示」「精选商品与预览由 SHOP-TASK-065、066 接上」：表单只有主题、主色与首页区块三组，
  // 没有标志、精选与预览的文字，也不显示精选商品的名称。
  it.each(LANGUAGES)("has only the theme, accent and home block groups in %s", (language) => {
    const html = render(readyState(), language);
    expect(allOf(html, "fieldset", "site-admin-store-design__group").map(legend)).toEqual([
      COPY["admin.theme"][language],
      COPY["admin.accent"][language],
      COPY["admin.home_blocks"][language],
    ]);
    const texts = visibleTexts(html);
    for (const key of ["admin.logo", "admin.logo_hint", "admin.logo_remove", "admin.featured_pick", "admin.featured_add", "admin.preview"] as const) {
      expect(texts).not.toContain(COPY[key][language]);
    }
    for (const product of DETAIL.featured) {
      expect(html).not.toContain(product.name ?? "");
    }
  });
});

describe("themes", () => {
  // SHOP-TASK-064 验收第 2 条「legend admin.theme，10 个单选按 THEME_ACCENTS 的顺序」「主题名 admin.theme_<主题 id>」与 UX A08
  // 「(●)<色块> [admin.theme_pandan]…共 10 款」：主题组的 legend 为 admin.theme，依次 10 个单选（同一组名、值为主题 id），
  // 每项文字为该主题的名称；顺序为 THEME_ACCENTS 的键的顺序，即班兰、早市、小票、咖啡店、蜡染、夜市、糖果、画廊、金线、电路。
  it.each(LANGUAGES)("lists the ten themes in order with their names in %s", (language) => {
    const fieldset = group(render(readyState(), language), 0);
    expect(legend(fieldset)).toBe(COPY["admin.theme"][language]);
    const choices = allOf(fieldset, "label", "acs-admin__choice");
    expect(SHOP_THEMES).toEqual(Object.keys(THEME_ACCENTS));
    expect(SHOP_THEMES).toEqual(["pandan", "pasar", "receipt", "kopitiam", "batik", "malam", "gula", "galeri", "songket", "litar"]);
    expect(choices).toHaveLength(10);
    choices.forEach((choice, index) => {
      const theme = SHOP_THEMES[index] ?? "pandan";
      const radio = attributes(tags(choice, "input")[0] ?? "");
      expect([radio.get("type"), radio.get("name"), radio.get("value")]).toEqual(["radio", "theme", theme]);
      expect(textNodes(choice)).toEqual([COPY[THEME_NAME[theme]][language]]);
      expect(THEME_NAME[theme]).toBe(`admin.theme_${theme}`);
    });
  });

  // 同一条「每项为 acs-admin__choice 里的单选、主题色条（acs-swatch，data-shop-theme 为该主题，三段依次 acs-swatch__surface、
  // acs-swatch__accent、acs-swatch__demo）与主题名」与 docs/design/COMPONENTS.md「AdminShell」A08 一条（Kelvin 2026-10-08，HANDOFF 0.40）：
  // 每项依次为单选、色条（.acs 元素）与主题名，色条恰为三段空的 span。
  it("draws each theme as a radio, a three-part strip and the name", () => {
    const choices = allOf(group(render(), 0), "label", "acs-admin__choice");
    choices.forEach((choice, index) => {
      const theme = SHOP_THEMES[index] ?? "pandan";
      const strip = element(choice, "span", "acs-swatch");
      expect(classes(tags(strip, "span")[0] ?? "")).toEqual(["acs", "acs-swatch"]);
      expect(attributes(tags(strip, "span")[0] ?? "").get("data-shop-theme")).toBe(theme);
      expect(strip).toBe(
        `<span class="acs acs-swatch" data-shop-theme="${theme}"><span class="acs-swatch__surface"></span><span class="acs-swatch__accent"></span><span class="acs-swatch__demo"></span></span>`,
      );
      expect(choice.indexOf("<input")).toBeLessThan(choice.indexOf(strip));
      expect(choice.indexOf(strip)).toBeLessThan(choice.indexOf(escapeHtml(COPY[THEME_NAME[theme]].en)));
    });
  });

  // 同一条「桌面两列、手机单列，其下 admin.theme_dark_note」：10 项在同一个容器（site.css 按宽度排两列或单列）里，
  // 其后为 admin.theme_dark_note（次要文字，照视觉稿）；读取到的主题被选中，其余不选。
  it.each(LANGUAGES)("checks the saved theme and ends with the dark note in %s", (language) => {
    const fieldset = group(render(readyState(), language), 0);
    const themes = element(fieldset, "div", "site-admin-store-design__themes");
    expect(allOf(themes, "label", "acs-admin__choice")).toHaveLength(10);
    expect(tags(fieldset, "input").map((tag) => attributes(tag).has("checked"))).toEqual(SHOP_THEMES.map((theme) => theme === "kopitiam"));
    const note = element(fieldset, "span", "acs-admin__muted");
    expect(textNodes(note)).toEqual([COPY["admin.theme_dark_note"][language]]);
    expect(fieldset.indexOf(note)).toBeGreaterThan(fieldset.indexOf(themes) + themes.length - 1);
    expect(textNodes(fieldset).at(-1)).toBe(COPY["admin.theme_dark_note"][language]);
  });
});

describe("accents", () => {
  // SHOP-TASK-064 验收第 2 条「legend admin.accent，所选主题的各主色依次为 acs-admin__ring 里的单选（可访问名称 admin.accent_option，
  // {n} 为从 1 起的序号），内放 acs-swatch--dot（data-shop-theme 为所选主题、data-accent 为该主色），其下 admin.accent_hint」与
  // UX A08「（读屏标签 [admin.accent_option]）」：每款主题下，主色组依次为该主题的各主色。
  it.each(LANGUAGES)("offers the accents of the chosen theme in %s", (language) => {
    for (const theme of SHOP_THEMES) {
      const form: DesignForm = { ...formOf(DETAIL), theme, accent: THEME_ACCENTS[theme][0] };
      const fieldset = group(render(readyState({ form }), language), 1);
      expect(legend(fieldset)).toBe(COPY["admin.accent"][language]);
      const rings = allOf(fieldset, "label", "acs-admin__ring");
      const accents: readonly string[] = THEME_ACCENTS[theme];
      expect(rings).toHaveLength(accents.length);
      rings.forEach((ring, index) => {
        const radio = attributes(tags(ring, "input")[0] ?? "");
        expect([radio.get("type"), radio.get("name"), radio.get("value")]).toEqual(["radio", "accent", accents[index]]);
        expect(radio.get("aria-label")).toBe(translate(language, "admin.accent_option", { n: index + 1 }));
        const dot = tags(ring, "span");
        expect(dot).toHaveLength(1);
        expect(classes(dot[0] ?? "")).toEqual(["acs", "acs-swatch--dot"]);
        expect(attributes(dot[0] ?? "").get("data-shop-theme")).toBe(theme);
        expect(attributes(dot[0] ?? "").get("data-accent")).toBe(accents[index]);
        expect(textNodes(ring)).toEqual([]);
      });
      expect(textNodes(fieldset)).toEqual([COPY["admin.accent"][language], COPY["admin.accent_hint"][language]]);
      expect(classes(tags(fieldset, "span").at(-1) ?? "")).toContain("acs-admin__muted");
    }
    expect(translate("en", "admin.accent_option", { n: 1 })).toBe("Colour 1");
  });

  // SHOP-TASK-064 验收第 2 条「读取到的主色为 null 时选第一项」：主色为 null 时表单选第一项（咖啡店为 kopi），页面上第一个圆环被选中；
  // 读取到具体的主色时选它。
  it("selects the first accent when the saved accent is null", () => {
    expect(formOf(DETAIL)).toEqual({ theme: "kopitiam", accent: "kopi", homeBlocks: DETAIL.home_blocks });
    const checked = (state: DesignState) => tags(group(render(state), 1), "input").map((tag) => attributes(tag).has("checked"));
    expect(checked(readyState())).toEqual([true, false, false, false]);
    const tile = { ...DETAIL, accent: "tile" };
    expect(formOf(tile).accent).toBe("tile");
    expect(checked(readyState({ base: tile, form: formOf(tile) }))).toEqual([false, false, true, false]);
  });

  // SHOP-TASK-064 验收第 2 条「换主题时改选新主题的第一项」与 UX A08「选主题后，主色选项换成该主题的一组（默认选第一项）」：
  // 换到任何别的主题时主色为该主题的第一项，主题组与主色组跟着变；再点已选的主题不改主色。
  it("switches to the first accent of a new theme", () => {
    const start = chooseAccent(formOf(DETAIL), "red");
    for (const theme of SHOP_THEMES.filter((other) => other !== "kopitiam")) {
      expect(chooseTheme(start, theme)).toEqual({ theme, accent: THEME_ACCENTS[theme][0], homeBlocks: DETAIL.home_blocks });
    }
    expect(chooseTheme(start, "kopitiam")).toBe(start);
    const html = render(readyState({ form: chooseTheme(start, "batik") }));
    expect(tags(group(html, 0), "input").map((tag) => attributes(tag).has("checked"))).toEqual(SHOP_THEMES.map((theme) => theme === "batik"));
    const rings = tags(group(html, 1), "input");
    expect(rings.map((tag) => [attributes(tag).get("value"), attributes(tag).has("checked")])).toEqual([
      ["indigo", true],
      ["sogan", false],
      ["maroon", false],
      ["teal", false],
    ]);
  });
});

describe("saving the accent", () => {
  // SHOP-TASK-064 验收第 2 条「保存时若主题与读取到的相同、且所选主色就是读取到的主色（读取到 null 时即该主题的第一项，含换走又换回的情形），
  // 原样提交读取到的 accent（含 null）…（未改动的表单再次保存不改变存储）」：读取到 null 时，未改动、换到别的主题再换回、
  // 改选别的主色再改回第一项，都提交 null。
  it("sends the saved null accent back while the choice is unchanged", () => {
    const form = formOf(DETAIL);
    expect(saveInput(DETAIL, form).accent).toBeNull();
    expect(saveInput(DETAIL, chooseTheme(chooseTheme(form, "galeri"), "kopitiam")).accent).toBeNull();
    expect(saveInput(DETAIL, chooseAccent(chooseAccent(form, "teh"), "kopi")).accent).toBeNull();
  });

  // 同一条：读取到具体的主色（含就是第一项的 id）时，未改动或换走又换回都原样提交它。
  it("sends a saved accent id back while the choice is unchanged", () => {
    const teh = { ...DETAIL, accent: "teh" };
    expect(saveInput(teh, formOf(teh)).accent).toBe("teh");
    expect(saveInput(teh, chooseAccent(chooseTheme(chooseTheme(formOf(teh), "litar"), "kopitiam"), "teh")).accent).toBe("teh");
    const kopi = { ...DETAIL, accent: "kopi" };
    expect(saveInput(kopi, formOf(kopi)).accent).toBe("kopi");
    expect(saveInput(kopi, chooseTheme(chooseTheme(formOf(kopi), "pasar"), "kopitiam")).accent).toBe("kopi");
  });

  // 同一条「否则提交所选主色 id」：改选别的主色提交它的 id；换了主题时提交新主题所选主色的 id（含新主题的第一项，不提交 null）；
  // 读取到具体主色、改回第一项时提交第一项的 id。
  it("sends the chosen accent id once the choice changed", () => {
    const form = formOf(DETAIL);
    expect(saveInput(DETAIL, chooseAccent(form, "tile"))).toMatchObject({ theme: "kopitiam", accent: "tile" });
    expect(saveInput(DETAIL, chooseTheme(form, "batik"))).toMatchObject({ theme: "batik", accent: "indigo" });
    expect(saveInput(DETAIL, chooseAccent(chooseTheme(form, "batik"), "maroon"))).toMatchObject({ theme: "batik", accent: "maroon" });
    const teh = { ...DETAIL, accent: "teh" };
    expect(saveInput(teh, chooseAccent(formOf(teh), "kopi")).accent).toBe("kopi");
  });
});

describe("home blocks", () => {
  // 一行的各部分：序号、区块名、勾选框与两个按钮。
  function rows(html: string): string[] {
    return allOf(group(html, 2), "div", "site-admin-store-design__block");
  }

  // SHOP-TASK-064 验收第 3 条「legend admin.home_blocks，按读取到的顺序四行，每行序号、区块名（hero、how、categories、featured 依次为
  // admin.block_hero、home.how_title、home.categories、home.featured）、勾选框 admin.block_show（勾选为显示）」与 UX A08
  // 「1 [admin.block_hero] ☐ [admin.block_show] (↑)(↓)」「☐ 勾选为显示」：四行按读取到的顺序（视觉稿的精选在分类之前），
  // 文字依次为序号、区块名与 admin.block_show，勾选框按 visible 勾选；隐藏的区块名为次要文字（视觉稿）。
  it.each(LANGUAGES)("lists the blocks in the saved order with their names in %s", (language) => {
    const html = render(readyState(), language);
    expect(legend(group(html, 2))).toBe(COPY["admin.home_blocks"][language]);
    expect(BLOCK_NAME).toEqual({ hero: "admin.block_hero", how: "home.how_title", categories: "home.categories", featured: "home.featured" });
    const found = rows(html);
    expect(found.map((row) => textNodes(row))).toEqual(
      DETAIL.home_blocks.map(({ block }, index) => [String(index + 1), COPY[BLOCK_NAME[block]][language], COPY["admin.block_show"][language]]),
    );
    found.forEach((row, index) => {
      const checkbox = attributes(tags(row, "input")[0] ?? "");
      expect(checkbox.get("type")).toBe("checkbox");
      expect(checkbox.has("checked")).toBe(DETAIL.home_blocks[index]?.visible);
      const label = element(row, "label", "site-admin-store-design__show");
      expect(textNodes(label)).toEqual([COPY["admin.block_show"][language]]);
      expect(label).toContain("<input");
      const name = element(row, "span", "site-admin-store-design__block-name");
      expect(classes(tags(name, "span")[2] ?? "").includes("acs-admin__muted")).toBe(DETAIL.home_blocks[index]?.visible === false);
    });
  });

  // SHOP-TASK-064 验收第 3 条「上移、下移按钮（可访问名称 admin.block_move_up、admin.block_move_down，按钮内为 aria-hidden 的图形；
  // 第一行上移与最后一行下移禁用），其下 admin.home_blocks_hint」与 UX A08「第一项的 (↑) 与最后一项的 (↓) 禁用」：每行两个按钮，
  // 只含 aria-hidden 的 svg、没有文字；只有第一行的上移与最后一行的下移禁用；最后为 admin.home_blocks_hint。
  it.each(LANGUAGES)("has move buttons with only the ends disabled in %s", (language) => {
    const html = render(readyState(), language);
    const found = rows(html);
    found.forEach((row, index) => {
      const buttons = tags(row, "button").map(attributes);
      expect(buttons.map((button) => button.get("aria-label"))).toEqual([COPY["admin.block_move_up"][language], COPY["admin.block_move_down"][language]]);
      expect(buttons.map((button) => button.get("type"))).toEqual(["button", "button"]);
      expect(buttons.map((button) => button.has("disabled"))).toEqual([index === 0, index === found.length - 1]);
      const svgs = tags(row, "svg");
      expect(svgs).toHaveLength(2);
      expect(svgs.map((svg) => attributes(svg).get("aria-hidden"))).toEqual(["true", "true"]);
    });
    expect(textNodes(group(html, 2)).at(-1)).toBe(COPY["admin.home_blocks_hint"][language]);
  });

  // 同一条「勾选为显示」与「上移、下移」：勾选框改 visible；上移与上一行互换、下移与下一行互换，其他行不动；第一行上移与最后一行下移不变。
  it("shows, hides and moves blocks", () => {
    const form = formOf(DETAIL);
    expect(showBlock(form, 3, true).homeBlocks.map((setting) => setting.visible)).toEqual([true, true, true, true]);
    expect(showBlock(form, 0, false).homeBlocks.map((setting) => setting.visible)).toEqual([false, true, true, false]);
    const order = (next: DesignForm) => next.homeBlocks.map((setting) => setting.block);
    expect(order(moveBlock(form, 3, -1))).toEqual(["hero", "how", "categories", "featured"]);
    expect(moveBlock(form, 3, -1).homeBlocks[2]).toEqual({ block: "categories", visible: false });
    expect(order(moveBlock(form, 0, 1))).toEqual(["how", "hero", "featured", "categories"]);
    expect(moveBlock(form, 0, -1)).toBe(form);
    expect(moveBlock(form, 3, 1)).toBe(form);
    expect(saveInput(DETAIL, moveBlock(showBlock(form, 3, true), 3, -1)).home_blocks).toEqual([
      { block: "hero", visible: true },
      { block: "how", visible: true },
      { block: "categories", visible: true },
      { block: "featured", visible: true },
    ]);
  });

  // 同一条：移动之后页面按新顺序编号，首末按钮的禁用跟着位置走。
  it("numbers the rows after a move", () => {
    const html = render(readyState({ form: moveBlock(formOf(DETAIL), 0, 1) }));
    expect(rows(html).map((row) => textNodes(row).slice(0, 2))).toEqual([
      ["1", COPY["home.how_title"].en],
      ["2", COPY["admin.block_hero"].en],
      ["3", COPY["home.featured"].en],
      ["4", COPY["home.categories"].en],
    ]);
  });
});

describe("featured products", () => {
  // 目的「精选商品本任务只原样提交、不显示」与验收第 4 条「featured_product_ids 原样提交读取到的精选商品 ID 顺序」：
  // 不论表单怎么改，精选 ID 都是读取到的顺序（含已下架的那件，Kelvin 2026-10-08 决定保存时放行）；没有精选时为空数组。
  it("sends the saved featured ids in their order", () => {
    const form = moveBlock(chooseTheme(formOf(DETAIL), "gula"), 1, 1);
    expect(saveInput(DETAIL, form).featured_product_ids).toEqual([7, 3]);
    expect(saveInput({ ...DETAIL, featured: [] }, formOf(DETAIL)).featured_product_ids).toEqual([]);
  });
});

describe("reading", () => {
  // SHOP-TASK-064 验收第 4 条「打开页面读取，进行中标 aria-busy、取得前不显示表单」：读取中表单区标 aria-busy 且为空。
  it("marks the form area busy and shows no form while reading", () => {
    const body = element(render(LOADING), "div", "site-admin-store-design__body");
    expect(body).toBe(`<div class="site-admin-store-design__body" aria-busy="true"></div>`);
    expect(attributes(tags(element(render(), "div", "site-admin-store-design__body"), "div")[0] ?? "").get("aria-busy")).toBe("false");
  });

  // 同一条「（经 SHOP-TASK-063 的请求模块，语言参数为界面语言）」「401 用路由的 replace 进入 /admin/login；网络中断显示 common.network_check，
  // 其他失败显示 common.error_retry，都不显示表单」：读取一次（地址带界面语言与中止用的 signal）；200 以结果重置表单，
  // 401 以 replace 进入 /admin/login，网络中断、其他状态与不合格响应体显示相应提示。
  it.each<[Response | (() => never), string[], DesignState | null]>([
    [json(200, DETAIL), ["show ready"], readyState()],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [networkDown, ["show failed"], { status: "failed", error: "common.network_check" }],
    [json(500, {}), ["show failed"], { status: "failed", error: "common.error_retry" }],
    [json(403, { detail: "csrf_invalid" }), ["show failed"], { status: "failed", error: "common.error_retry" }],
    [json(200, { ...DETAIL, csrf_token: "" }), ["show failed"], { status: "failed", error: "common.error_retry" }],
  ])("reads the design from the reply (%#)", async (reply, events, state) => {
    for (const language of LANGUAGES) {
      const calls = stubFetch(typeof reply === "function" ? reply : reply.clone());
      const controller = new AbortController();
      const moves = fakeMoves();
      await openDesign(language, controller.signal, moves.target);
      expect(calls).toHaveLength(1);
      expect(calls[0]?.url).toBe(`/api/admin/store-design?lang=${language}`);
      expect(calls[0]?.init.method).toBe("GET");
      expect(calls[0]?.init.signal).toBe(controller.signal);
      expect(moves.events).toEqual(events);
      if (state !== null) {
        expect(moves.states).toEqual([state]);
      }
    }
  });

  // 同一条的规则部分：读取结果到显示的对应。
  it("chooses what to show from the read", () => {
    expect(readStep({ kind: "ok", design: DETAIL })).toEqual(readyState());
    expect(readStep({ kind: "ok", design: DETAIL }, "common.error_retry")).toEqual(readyState({ alert: "common.error_retry" }));
    expect(readStep({ kind: "none" })).toBe("login");
    expect(readStep({ kind: "network" })).toEqual({ status: "failed", error: "common.network_check" });
    expect(readStep({ kind: "failed" })).toEqual({ status: "failed", error: "common.error_retry" });
  });

  // 同一条：失败时表单区只有提示（role="alert"），没有表单；标题与说明照常。
  it.each(LANGUAGES)("shows a read failure without the form in %s", (language) => {
    for (const key of ["common.network_check", "common.error_retry"] as const) {
      const html = render({ status: "failed", error: key }, language);
      expect(element(html, "div", "site-admin-store-design__body")).toBe(
        `<div class="site-admin-store-design__body" aria-busy="false"><div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div></div>`,
      );
      expect(html).not.toMatch(/<(form|fieldset|input|button)\b/);
    }
  });

  // 同一条「离开页面时中止请求，旧请求的结果不再更新页面」：signal 中止之后，不论回答什么（设置、401 或失败）都不显示也不跳转。
  it.each([json(200, DETAIL), json(401, { detail: "admin_session_required" }), json(500, {}), networkDown])(
    "ignores a read after it was aborted (%#)",
    async (reply) => {
      stubFetch(reply);
      const controller = new AbortController();
      const moves = fakeMoves();
      const pending = openDesign("en", controller.signal, moves.target);
      controller.abort();
      await pending;
      expect(moves.events).toEqual([]);
    },
  );
});

describe("language", () => {
  // SHOP-TASK-064 验收第 4 条「切换界面语言时重新读取，重新读取与首次读取的显示与失败处理相同，取得后以结果重置表单」：
  // 读取的 effect 以界面语言为依赖（换语言即清理并重新读取）；换了语言而新结果未到时按读取中显示（不显示旧语言的表单）。
  it("reads again when the interface language changes", async () => {
    expect(pageSource).toMatch(/void openDesign\(language, controller\.signal, pageMoves\(language, setEntry, replace\)\);\s*\}\);\s*\}, \[language, replace\]\);/);
    const entry = { language: "en" as const, state: readyState({ form: chooseTheme(formOf(DETAIL), "litar"), saved: true }) };
    expect(shownState(entry, "en")).toBe(entry.state);
    expect(shownState(entry, "zh")).toBe(LOADING);
    expect(shownState(entry, "ms")).toBe(LOADING);
    // 新语言的读取与首次读取相同：取到后表单按结果重置（改过的主题与已保存提示都不带过去）。
    stubFetch(json(200, DETAIL));
    const moves = fakeMoves();
    await openDesign("ms", new AbortController().signal, moves.target);
    expect(moves.current).toEqual(readyState());
  });

  // 同一条「离开页面时中止请求」「切换界面语言或离开页面时也中止进行中的保存」：effect 的清理（startDeferred 返回的函数）中止
  // 当前语言的 controller，保存用的正是这个 controller 的 signal。
  it("aborts the read and the save together on cleanup", () => {
    expect(pageSource).toMatch(/lifetime\.current = controller;\s*return startDeferred\(controller,/);
    expect(pageSource).toMatch(/const controller = lifetime\.current;[\s\S]*?submitDesign\(state\.base, state\.form, language, controller\.signal,/);
  });
});

describe("saving", () => {
  const ZH_SAVED: AdminStoreDesign = {
    theme: "batik",
    accent: null,
    home_blocks: [
      { block: "how", visible: true },
      { block: "hero", visible: false },
      { block: "featured", visible: true },
      { block: "categories", visible: true },
    ],
    featured: DETAIL.featured,
  };

  // SHOP-TASK-064 验收第 4 条「common.save 以 PUT 提交当前选择」「（语言参数为界面语言）」与 SHOP-TASK-063 的请求头：保存以界面语言的地址
  // PUT，带读取给的 CSRF 令牌与中止用的 signal，请求体为当前的主题、主色、区块与读取到的精选 ID。
  it.each(LANGUAGES)("saves the current choice with the interface language in %s", async (language) => {
    const form = showBlock(chooseTheme(formOf(DETAIL), "batik"), 1, false);
    const calls = stubFetch(json(200, savedBody({ ...DETAIL, ...saveInput(DETAIL, form), accent: "indigo" })));
    const controller = new AbortController();
    await submitDesign(DETAIL, form, language, controller.signal, fakeMoves(startSaving(readyState({ form }))).target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/store-design?lang=${language}`);
    expect(calls[0]?.init.method).toBe("PUT");
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(headerOf(calls[0], "X-CSRF-Token")).toBe(DETAIL.csrf_token);
    expect(bodyOf(calls[0])).toEqual({
      theme: "batik",
      accent: "indigo",
      home_blocks: [
        { block: "hero", visible: true },
        { block: "how", visible: false },
        { block: "featured", visible: true },
        { block: "categories", visible: false },
      ],
      featured_product_ids: [7, 3],
    });
  });

  // 同一条「未改动的表单再次保存不改变存储」：未改动时请求体与读取到的设置相同（主色 null 原样）。
  it("sends back exactly what was read when nothing changed", async () => {
    const calls = stubFetch(json(200, savedBody(DETAIL)));
    await submitDesign(DETAIL, formOf(DETAIL), "en", new AbortController().signal, fakeMoves(startSaving(readyState())).target);
    expect(bodyOf(calls[0])).toEqual({ theme: "kopitiam", accent: null, home_blocks: DETAIL.home_blocks, featured_product_ids: [7, 3] });
  });

  // SHOP-TASK-064 验收第 4 条「保存进行中按钮禁用并标 aria-busy」：点保存后 common.save（submit）禁用并标 aria-busy，先前的提示收起；
  // 其余时候可点、aria-busy 为 false。
  it.each(LANGUAGES)("disables the save button while saving in %s", (language) => {
    const button = (state: DesignState) => attributes(tags(element(render(state, language), "div", "site-admin-store-design__save"), "button")[0] ?? "");
    const saving = startSaving(readyState({ saved: true, alert: "common.error_retry", saveError: "common.network_check" }));
    expect(saving).toEqual(readyState({ saving: true }));
    expect([button(saving).get("type"), button(saving).has("disabled"), button(saving).get("aria-busy")]).toEqual(["submit", true, "true"]);
    expect([button(readyState()).has("disabled"), button(readyState()).get("aria-busy")]).toEqual([false, "false"]);
    expect(textNodes(element(render(saving, language), "div", "site-admin-store-design__save"))).toEqual([COPY["common.save"][language]]);
    expect(startSaving(LOADING)).toBe(LOADING);
  });

  // SHOP-TASK-064 验收第 4 条「200 后以返回的设置更新表单并显示 admin.design_saved」：返回的设置（主色 null）成为表单与之后保存的依据，
  // CSRF 令牌与可挑选商品沿用读取的；页面在保存按钮旁显示 admin.design_saved。之后原样再存提交返回的主色 null（不改变存储）。
  it.each(LANGUAGES)("updates the form from the saved design in %s", async (language) => {
    stubFetch(json(200, savedBody(ZH_SAVED)));
    const form: DesignForm = { theme: "batik", accent: "indigo", homeBlocks: ZH_SAVED.home_blocks };
    const moves = fakeMoves(startSaving(readyState({ form })));
    await submitDesign(DETAIL, form, language, new AbortController().signal, moves.target);
    expect(moves.events).toEqual(["update ready"]);
    const base = { ...ZH_SAVED, choices: DETAIL.choices, csrf_token: DETAIL.csrf_token };
    expect(moves.current).toEqual({ status: "ready", base, form: formOf(ZH_SAVED), saving: false, saved: true, alert: null, saveError: null });
    expect(saveInput(base, formOf(ZH_SAVED)).accent).toBeNull();
    const save = element(render(moves.current, language), "div", "site-admin-store-design__save");
    expect(save).toContain(`<span role="status">${escapeHtml(COPY["admin.design_saved"][language])}</span>`);
    expect(textNodes(save)).toEqual([COPY["common.save"][language], COPY["admin.design_saved"][language]]);
  });

  // 同一条「（之后再改任何设置即隐藏）」：改主题、主色、显隐或顺序之后 admin.design_saved 不再显示。
  it("hides the saved message after any change", () => {
    const saved = readyState({ saved: true });
    const changes: ((form: DesignForm) => DesignForm)[] = [
      (form) => chooseTheme(form, "malam"),
      (form) => chooseAccent(form, "tile"),
      (form) => showBlock(form, 3, true),
      (form) => moveBlock(form, 2, 1),
    ];
    for (const change of changes) {
      const next = editForm(saved, change);
      expect(next).toEqual(readyState({ form: change(formOf(DETAIL)), saved: false }));
      expect(render(next)).not.toContain(escapeHtml(COPY["admin.design_saved"].en));
    }
    expect(editForm(LOADING, (form) => chooseTheme(form, "malam"))).toBe(LOADING);
  });

  // SHOP-TASK-064 验收第 4 条「401 同上」：保存得到 401 时以 replace 进入 /admin/login，不更新表单。
  it("goes to the login page on 401", async () => {
    stubFetch(json(401, { detail: "admin_session_required" }));
    const moves = fakeMoves(startSaving(readyState()));
    await submitDesign(DETAIL, formOf(DETAIL), "en", new AbortController().signal, moves.target);
    expect(moves.events).toEqual([`replace ${LOGIN_PATH}`]);
  });

  // SHOP-TASK-064 验收第 4 条「403 时重新读取（显示同上），取得后以结果重置表单并在表单上方显示 common.error_retry」：
  // 403 后先显示读取中（不显示表单），再以界面语言读取；取得后表单按新结果重置（改过的选择不保留），表单最上方为 common.error_retry。
  it.each(LANGUAGES)("reads again after 403 and shows the error above the form in %s", async (language) => {
    const fresh: AdminStoreDesignDetail = { ...DETAIL, theme: "receipt", accent: "cobalt", csrf_token: "design-csrf-2" };
    const calls = stubFetch(json(403, { detail: "csrf_invalid" }), json(200, fresh));
    const form = chooseTheme(formOf(DETAIL), "songket");
    const moves = fakeMoves(startSaving(readyState({ form })));
    const controller = new AbortController();
    await submitDesign(DETAIL, form, language, controller.signal, moves.target);
    expect(calls.map((call) => [call.init.method, call.url])).toEqual([
      ["PUT", `/api/admin/store-design?lang=${language}`],
      ["GET", `/api/admin/store-design?lang=${language}`],
    ]);
    expect(calls[1]?.init.signal).toBe(controller.signal);
    expect(moves.events).toEqual(["show loading", "show ready"]);
    expect(moves.states[0]).toBe(LOADING);
    expect(moves.current).toEqual({ status: "ready", base: fresh, form: formOf(fresh), saving: false, saved: false, alert: "common.error_retry", saveError: null });
    const formHtml = element(render(moves.current, language), "form", "site-admin-store-design__form");
    expect(formHtml.slice(formHtml.indexOf(">") + 1).startsWith(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY["common.error_retry"][language])}</span></div>`)).toBe(true);
  });

  // 同一条「（显示同上）」：403 后的重新读取与首次读取的失败处理相同——401 进入 /admin/login，网络中断与其他失败只显示提示、不显示表单。
  it.each<[Response | (() => never), string[], DesignState | null]>([
    [json(401, { detail: "admin_session_required" }), ["show loading", `replace ${LOGIN_PATH}`], null],
    [networkDown, ["show loading", "show failed"], { status: "failed", error: "common.network_check" }],
    [json(500, {}), ["show loading", "show failed"], { status: "failed", error: "common.error_retry" }],
  ])("handles the read after 403 like the first read (%#)", async (reread, events, state) => {
    stubFetch(json(403, { detail: "csrf_invalid" }), reread);
    const moves = fakeMoves(startSaving(readyState()));
    await submitDesign(DETAIL, formOf(DETAIL), "en", new AbortController().signal, moves.target);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.current).toEqual(state);
    }
  });

  // SHOP-TASK-064 验收第 4 条「网络中断显示 common.network_check；其他失败（含 422）显示 common.error_retry 并保留表单」：
  // 保存失败后按钮恢复可点，表单保持当前选择（含保存进行中的改动），提示在保存按钮旁；不显示 admin.design_saved。
  it.each<[Response | (() => never), "common.network_check" | "common.error_retry"]>([
    [networkDown, "common.network_check"],
    [json(422, { detail: "featured_unavailable" }), "common.error_retry"],
    [json(422, { detail: "accent_invalid" }), "common.error_retry"],
    [json(500, {}), "common.error_retry"],
    [json(200, { theme: "kopitiam" }), "common.error_retry"],
  ])("keeps the form when the save fails (%#)", async (reply, key) => {
    stubFetch(reply);
    const form = chooseAccent(formOf(DETAIL), "red");
    const moves = fakeMoves(startSaving(readyState({ form })));
    // 保存进行中又改了一处（页面以当前状态为准更新，不退回保存时的选择）。
    const during = moveBlock(form, 0, 1);
    moves.target.update((current) => editForm(current, () => during));
    await submitDesign(DETAIL, form, "en", new AbortController().signal, moves.target);
    expect(moves.current).toEqual(readyState({ form: during, saveError: key }));
    for (const language of LANGUAGES) {
      const html = render(moves.current, language);
      const save = element(html, "div", "site-admin-store-design__save");
      expect(save).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div>`);
      expect(attributes(tags(save, "button")[0] ?? "").has("disabled")).toBe(false);
      expect(html).not.toContain(escapeHtml(COPY["admin.design_saved"][language]));
      expect(tags(html, "fieldset")).toHaveLength(3);
    }
  });

  // SHOP-TASK-064 验收第 4 条「切换界面语言或离开页面时也中止进行中的保存，其结果不再更新表单、不显示 admin.design_saved」：
  // 保存的 signal 中止后，不论回答什么（200、401、403、失败）都不更新、不跳转，也不在 403 后重新读取。
  it.each([json(200, savedBody(DETAIL)), json(401, { detail: "admin_session_required" }), json(403, { detail: "csrf_invalid" }), json(500, {}), networkDown])(
    "ignores a save after it was aborted (%#)",
    async (reply) => {
      const calls = stubFetch(reply, json(200, DETAIL));
      const controller = new AbortController();
      const moves = fakeMoves(startSaving(readyState()));
      const pending = submitDesign(DETAIL, formOf(DETAIL), "en", controller.signal, moves.target);
      controller.abort();
      await pending;
      expect(moves.events).toEqual([]);
      expect(calls).toHaveLength(1);
    },
  );
});

describe("dictionary", () => {
  // SHOP-TASK-064 验收第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」与第 5 条「页面文字全部来自字典」
  // （SHOP-TASK-062 的同名测试扩展到表单的各状态）：经路由打开（会话未返回）、已登录（菜单收起与展开）、读取中、读取失败、
  // 每款主题的表单、保存中、保存成功、保存失败与 403 后，每段文字（含 aria-label）都是当前语言的字典文案，此外只有区块的序号；
  // 页面没有 title 或 placeholder。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const index of [1, 2, 3, 4, 5]) {
      allowed.add(String(index));
      allowed.add(translate(language, "admin.accent_option", { n: index }));
    }
    const themes: ShopTheme[] = [...SHOP_THEMES];
    const states: DesignState[] = [
      LOADING,
      { status: "failed", error: "common.network_check" },
      { status: "failed", error: "common.error_retry" },
      ...themes.map((theme) => readyState({ form: chooseTheme(formOf(DETAIL), theme) })),
      readyState({ saving: true }),
      readyState({ saved: true }),
      readyState({ saveError: "common.network_check" }),
      readyState({ saveError: "common.error_retry" }),
      readyState({ alert: "common.error_retry" }),
    ];
    const pages = [renderApp(STORE_DESIGN_PATH, language), renderSignedIn(language), renderSignedIn(language, { menuOpen: true })];
    for (const state of states) {
      pages.push(render(state, language));
    }
    for (const html of pages) {
      expect(html).not.toMatch(/\s(title|placeholder)=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
