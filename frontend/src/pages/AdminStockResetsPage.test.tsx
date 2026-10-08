import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { StockResetLine, StockResetList, StockResetRow } from "../api/adminStockResets";
import { AdminFrameView } from "../components/AdminFrame";
import type { AdminFrameViewProps, FrameState } from "../components/AdminFrame";
import { BRAND, COPY, LANGUAGES, translate } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { htmlLang, LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
// 经 Vite 的 ?raw 读成字符串（与 i18n/copy.test.ts 读 UX-COPY 相同），用来确认本页只调用库存重置的两个接口。
import pageSource from "./AdminStockResetsPage.tsx?raw";
import {
  AdminStockResetsContent,
  AdminStockResetsView,
  DETAIL_BUSY,
  detailStep,
  formatBusinessDate,
  INITIAL_LIST,
  listStep,
  loadDetail,
  loadResets,
  needsDetail,
  pagerOf,
} from "./AdminStockResetsPage";
import type { AdminStockResetsViewProps, DetailMap, DetailState, ListState } from "./AdminStockResetsPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const STOCK_RESETS_PATH = "/admin/stock-resets";
const ORDERS_PATH = "/admin/orders";
const REFUNDS_PATH = "/admin/refunds";
const LOGIN_PATH = "/admin/login";
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

// A07-desktop 的四天：5 日成功、4 日失败（失败时整笔回滚，SKU 数为 0）、3 日与 2 日成功。
const OCT5: StockResetRow = { id: 12, business_date: "2026-10-05", result: "succeeded", sku_count: 42, completed_at: "2026-10-04T16:00:04Z" };
const OCT4: StockResetRow = { id: 11, business_date: "2026-10-04", result: "failed", sku_count: 0, completed_at: null };
const OCT3: StockResetRow = { id: 10, business_date: "2026-10-03", result: "succeeded", sku_count: 42, completed_at: "2026-10-02T16:00:03Z" };
const JAN1: StockResetRow = { id: 2, business_date: "2026-01-01", result: "succeeded", sku_count: 41, completed_at: "2025-12-31T16:00:02Z" };
const ROWS = [OCT5, OCT4, OCT3, JAN1];

const LINES: StockResetLine[] = [
  { sku: "crew-neck-tee-black-m", initial_stock: 20, held_quantity: 2, available_stock: 18 },
  { sku: "crew-neck-tee-black-xl", initial_stock: 8, held_quantity: 0, available_stock: 8 },
  { sku: "soy-wax-candle", initial_stock: 30, held_quantity: 1, available_stock: 29 },
];

function list(overrides: Partial<StockResetList> = {}): StockResetList {
  return { total: ROWS.length, page: 1, page_size: 30, resets: ROWS, ...overrides };
}

function shown(value: StockResetList): ListState {
  return { busy: false, list: value, error: null };
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
      <RouterProvider initialPath={STOCK_RESETS_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

// 已登录时的本页：后台框架（会话请求以 state 替身代替）里放本页的内容区。
function renderSignedIn(language: Language = "en", overrides: Partial<AdminFrameViewProps> = {}): string {
  const props: AdminFrameViewProps = {
    mode: "light",
    current: "stockResets",
    state: SIGNED_IN,
    busy: false,
    menuOpen: false,
    onToggleMenu: noop,
    onCloseMenu: noop,
    onLogOut: noop,
    children: <AdminStockResetsContent />,
    ...overrides,
  };
  return renderToStaticMarkup(wrap(<AdminFrameView {...props} />, language));
}

function props(overrides: Partial<AdminStockResetsViewProps> = {}): AdminStockResetsViewProps {
  return { state: shown(list()), details: {}, onOpen: noop, onPage: noop, ...overrides };
}

function render(overrides: Partial<AdminStockResetsViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AdminStockResetsView {...props(overrides)} />, language));
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

// 某种元素的全部实例（同名元素嵌套时外层在前）。
function all(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => elementAt(html, name, m.index));
}

// 直接属于这张表（不含展开处里的明细表）的各行。
function bodyRows(html: string): string[] {
  const table = element(html, "div", "site-admin-stock-resets__table");
  const tbody = all(table, "tbody")[0] ?? "";
  const inner = tbody.slice(tbody.indexOf(">") + 1);
  const rows: string[] = [];
  for (let at = inner.search(/<tr\b/); at !== -1; ) {
    const row = elementAt(inner, "tr", at);
    rows.push(row);
    const rest = inner.slice(at + row.length);
    const next = rest.search(/<tr\b/);
    at = next === -1 ? -1 : at + row.length + next;
  }
  return rows;
}

function breakdown(line: StockResetLine, language: Language): string {
  return translate(language, "admin.stock_reset_breakdown", { initial: line.initial_stock, held: line.held_quantity, available: line.available_stock });
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

// 路由替身：记录页面显示的状态与经 replace 去的地址。
function fakeMoves<T>() {
  const events: string[] = [];
  const states: T[] = [];
  const target = {
    show: (state: T) => {
      states.push(state);
      events.push("show");
    },
    replace: (path: RoutePath) => {
      events.push(`replace ${path}`);
    },
  };
  return { target, events, states };
}

describe("route", () => {
  // UX 页面地图「A07 后台库存重置结果 /admin/stock-resets」与「管理后台总体」「每页顶部常驻 [admin.demo_banner]」；
  // SHOP-TASK-059 验收第 2 条「新文件 AdminStockResetsPage.tsx 用后台框架渲染」：经路由打开 /admin/stock-resets（服务端渲染，会话未返回），
  // 根元素为 acs-admin 并有后台演示横幅，内容区为空并标 aria-busy；不套前台框架（没有前台页头、页脚、品牌名与前台演示横幅）。
  it.each(LANGUAGES)("renders the admin frame without the storefront frame in %s", (language) => {
    const html = renderApp(STOCK_RESETS_PATH, language);
    expect(classes(html.slice(0, html.indexOf(">") + 1))).toContain("acs-admin");
    expect(element(html, "div", "acs-admin__banner")).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).toContain(`<main class="acs-admin__main" aria-busy="true"></main>`);
    expect(html).not.toMatch(/<(header|footer)\b/);
    expect(html).not.toContain(BRAND);
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
  });
});

describe("navigation", () => {
  // UX「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、…、[admin.nav_stock_resets]、…」与
  // Kelvin 2026-10-06（docs/HANDOFF.md 0.35）「后台导航只显示已上线页面的导航项」；SHOP-TASK-059 验收第 2、3 条「当前导航项为库存重置」
  // 「按 UX 顺序只含已上线的订单、退款与库存重置三项（桌面导航与 ☰ 菜单同样）」：经路由打开本页，桌面导航与 ☰ 菜单都依次为
  // 订单、退款与库存重置三项，库存重置标 aria-current="page"，没有其他导航项。
  // SHOP-TASK-062 改动：按 SHOP-TASK-062 验收第 3 条把「依次为订单、退款与库存重置三项」改为依次为订单、退款、库存重置与
  // 店铺装修（/admin/store-design）四项；当前项仍为库存重置，手机顶栏名称不变（下一条）。
  it.each(LANGUAGES)("marks the stock resets item as current, after orders and refunds, in %s", (language) => {
    const html = renderApp(STOCK_RESETS_PATH, language);
    for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
      const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
      expect(items.map((tag) => [attributes(tag).get("href"), attributes(tag).get("aria-current")])).toEqual([
        [ORDERS_PATH, undefined],
        [REFUNDS_PATH, undefined],
        [STOCK_RESETS_PATH, "page"],
        ["/admin/store-design", undefined],
      ]);
      const labels = textNodes(nav).slice(0, 4);
      expect(labels).toEqual([
        COPY["admin.nav_orders"][language],
        COPY["admin.nav_refunds"][language],
        COPY["admin.nav_stock_resets"][language],
        COPY["admin.nav_store_design"][language],
      ]);
    }
  });

  // UX A07 手机线框「☰  [admin.nav_stock_resets]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」：
  // 手机顶栏名称为 admin.nav_stock_resets。
  it.each(LANGUAGES)("names the stock resets item in the phone bar in %s", (language) => {
    const html = renderApp(STOCK_RESETS_PATH, language);
    expect(element(html, "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_stock_resets"][language])}</span>`,
    );
  });
});

describe("content", () => {
  // UX A07 桌面与手机线框「[admin.stock_reset_title]」其下「[admin.stock_reset_note]」，再下为每日结果；SHOP-TASK-060 验收第 3 条
  // 「SHOP-TASK-059 的 h1 admin.stock_reset_title 与 admin.stock_reset_note 之下」与第 4 条「请求进行中标 aria-busy」：已登录时内容区
  // 只有这一个 h1，其后依次为说明与列表区；刚打开时列表区标 aria-busy、还没有表格或卡片。
  // SHOP-TASK-060 改动：原「shows only the title and the note in the content area」（SHOP-TASK-059 守住「内容区只有标题与说明、没有列表」，
  // 列表留给 SHOP-TASK-060）改为标题与说明之后为列表区；标题与说明的写法不变。
  it.each(LANGUAGES)("shows the title and the note above the list in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(attributes(tags(main, "main")[0] ?? "").get("aria-busy")).toBe("false");
    expect(tags(main, "h1")).toHaveLength(1);
    expect(element(main, "div", "site-admin-stock-resets")).toBe(
      `<div class="site-admin-stock-resets"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.stock_reset_title"][language])}</h1>` +
        `<p class="site-admin-stock-resets__note">${escapeHtml(COPY["admin.stock_reset_note"][language])}</p>` +
        `<div class="site-admin-stock-resets__list" aria-busy="true"></div></div>`,
    );
    expect(textNodes(main)).toEqual([COPY["admin.stock_reset_title"][language], COPY["admin.stock_reset_note"][language]]);
    expect(INITIAL_LIST).toEqual({ busy: true, list: null, error: null });
  });

  // SHOP-TASK-060 验收第 2 条「调用 SHOP-TASK-052 的列表与明细接口」与目的「不改库存重置本身」：本页只经 api/adminStockResets 读取，
  // 不引入其他接口模块，也不直接调用 fetch。
  // SHOP-TASK-060 改动：原「makes no requests of its own」（SHOP-TASK-059 守住「页面不发起自己的请求（列表由 SHOP-TASK-060 接上）」）
  // 改为只调用库存重置的两个只读接口，因为本任务就是接上这两个请求。
  it("reads only through the stock reset API module", () => {
    expect([...pageSource.matchAll(/from "\.\.\/api\/([^"]+)"/g)].map((m) => m[1])).toEqual(["adminStockResets", "adminStockResets"]);
    expect(pageSource).not.toMatch(/\bfetch\s*\(/);
    expect(pageSource).not.toMatch(/method:\s*"(POST|PUT|PATCH|DELETE)"/);
  });
});

describe("desktop table", () => {
  // UX A07 桌面线框「[admin.col_date_myt]  [admin.col_result]   [admin.col_sku_count]」与 SHOP-TASK-060 验收第 3 条「桌面表格列头…」：
  // 三个列头依此顺序；表格在面板里（acs-admin__panel、acs-admin__table），SKU 数列靠右（A07-desktop）。
  it.each(LANGUAGES)("has the three column headers in order in %s", (language) => {
    const table = element(render({}, language), "div", "site-admin-stock-resets__table");
    expect(classes(tags(table, "div")[0] ?? "")).toContain("acs-admin__panel");
    expect(classes(tags(table, "table")[0] ?? "")).toContain("acs-admin__table");
    const head = all(table, "thead")[0] ?? "";
    expect(all(head, "th").map((th) => textNodes(th).join(""))).toEqual(
      [COPY["admin.col_date_myt"][language], COPY["admin.col_result"][language], COPY["admin.col_sku_count"][language]],
    );
    expect(tags(head, "th").map((th) => classes(th).includes("site-admin-stock-resets__num"))).toEqual([false, false, true]);
  });

  // UX A07 桌面线框「<日期> [admin.stock_reset_ok] <n>」「<日期> [admin.stock_reset_failed] <n>」与 SHOP-TASK-060 验收第 3 条
  // 「结果用 admin.stock_reset_ok 或 admin.stock_reset_failed 标签（样式照视觉稿）」、Kelvin 2026-10-07（HANDOFF 0.39）
  // 「A07 重置失败照用 admin.stock_reset_failed」：每天一行为日期、结果标签（成功为 acs-tag--success，失败为 acs-tag--danger，照 A07-desktop）
  // 与靠右的 SKU 数；其下紧跟一行展开处。
  it.each(LANGUAGES)("shows each day in a row followed by its detail row in %s", (language) => {
    const rows = bodyRows(render({}, language));
    expect(rows).toHaveLength(ROWS.length * 2);
    ROWS.forEach((reset, index) => {
      const row = rows[index * 2] ?? "";
      const cells = all(row, "td");
      expect(cells.map((td) => textNodes(td))).toEqual([
        [formatBusinessDate(reset.business_date, language)],
        [COPY[reset.result === "succeeded" ? "admin.stock_reset_ok" : "admin.stock_reset_failed"][language]],
        [String(reset.sku_count)],
      ]);
      expect(classes(tags(cells[1] ?? "", "span")[0] ?? "")).toEqual(["acs-tag", reset.result === "succeeded" ? "acs-tag--success" : "acs-tag--danger"]);
      expect(classes(tags(cells[2] ?? "", "td")[0] ?? "")).toContain("site-admin-stock-resets__num");
      expect(classes(tags(rows[index * 2 + 1] ?? "", "tr")[0] ?? "")).toContain("site-admin-stock-resets__detail-row");
    });
    expect(COPY["admin.stock_reset_failed"].en).toBe("Failed — the operator has been alerted");
  });

  // SHOP-TASK-060 验收第 3 条「每天的明细用原生 details 与 summary 展开（summary 文字为 admin.col_sku_count，照视觉稿；
  // 桌面放在该日表格行下方另起的一行）」：展开处那一行只有一个跨三列的单元格，里面是一个 details，summary 为 admin.col_sku_count；
  // 默认收起（不带 open），尚未请求时 details 里只有 summary；表格里没有链接或按钮。
  it.each(LANGUAGES)("puts a collapsed details element in the row below each day in %s", (language) => {
    const html = render({}, language);
    for (const detailRow of bodyRows(html).filter((_row, index) => index % 2 === 1)) {
      const cells = tags(detailRow, "td");
      expect(cells).toHaveLength(1);
      expect(attributes(cells[0] ?? "").get("colspan")).toBe("3");
      const details = all(detailRow, "details");
      expect(details).toHaveLength(1);
      expect(attributes(tags(details[0] ?? "", "details")[0] ?? "").has("open")).toBe(false);
      expect(textNodes(details[0] ?? "")).toEqual([COPY["admin.col_sku_count"][language]]);
      expect(all(details[0] ?? "", "summary").map((summary) => textNodes(summary).join(""))).toEqual([COPY["admin.col_sku_count"][language]]);
    }
    expect(element(html, "div", "site-admin-stock-resets__table")).not.toMatch(/<(a|button)\b/);
  });
});

describe("dates", () => {
  // SHOP-TASK-060 验收第 3 条「日期按界面语言显示营业日期本身（不按访客时区换算）」与 UX A07 列头 admin.col_date_myt（马来西亚时间）：
  // 营业日期显示为那一天本身——与在马来西亚时区格式化当天中午得到的日期相同；英文为「Oct 5, 2026」这类格式。
  it.each(LANGUAGES)("shows the business date itself in %s", (language) => {
    for (const date of ["2026-10-05", "2026-01-01", "2026-12-31", "2028-02-29"]) {
      const [year = 0, month = 1, day = 1] = date.split("-").map(Number);
      const noonInMalaysia = new Date(Date.UTC(year, month - 1, day, 4));
      const expected = new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeZone: "Asia/Kuala_Lumpur" }).format(noonInMalaysia);
      expect(formatBusinessDate(date, language)).toBe(expected);
    }
    expect(formatBusinessDate("2026-10-05", "en")).toBe("Oct 5, 2026");
    expect(formatBusinessDate("2026-01-01", "en")).toBe("Jan 1, 2026");
  });

  // 同一条「不按访客时区换算」：把浏览器的默认时区换成 UTC−12 与 UTC+14（Intl.DateTimeFormat 的替身在没给 timeZone 时补上该时区），
  // 显示的日期都不变，不会提前或推后一天。替身确实生效：不带时区格式化 2026-01-01 的 UTC 零点，在 UTC−12 得到前一天。
  it("does not move the date with the visitor's time zone", () => {
    const dates = ["2026-10-05", "2026-01-01", "2026-12-31"];
    const expected = ["Oct 5, 2026", "Jan 1, 2026", "Dec 31, 2026"];
    const RealFormat = Intl.DateTimeFormat;
    for (const zone of ["Etc/GMT+12", "Pacific/Kiritimati"]) {
      const InZone = function (locales?: string | string[], options?: Intl.DateTimeFormatOptions) {
        return new RealFormat(locales, { timeZone: zone, ...options });
      } as unknown as typeof Intl.DateTimeFormat;
      vi.stubGlobal("Intl", Object.assign(Object.create(Intl) as typeof Intl, { DateTimeFormat: InZone }));
      if (zone === "Etc/GMT+12") {
        expect(new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(Date.UTC(2026, 0, 1)))).toBe("Dec 31, 2025");
      }
      expect(dates.map((date) => formatBusinessDate(date, "en"))).toEqual(expected);
      vi.unstubAllGlobals();
    }
    expect(dates.map((date) => formatBusinessDate(date, "en"))).toEqual(expected);
  });
});

describe("phone cards", () => {
  // UX A07 手机线框「<日期> [admin.stock_reset_ok]」「 [admin.col_sku_count] <n>」「<日期> [admin.stock_reset_failed]」与 SHOP-TASK-060
  // 验收第 3 条「手机为卡片…结果用…标签（样式照视觉稿）…（手机放在卡片里）」「二者冲突时以 UX 文档为准」：每张卡片为面板，
  // 第一行为日期与结果标签（顺序照 UX；A07-phone 把 SKU 数放在第一行），其下为「admin.col_sku_count n」，再下为展开处（summary admin.col_sku_count）。
  it.each(LANGUAGES)("shows each day as a card in %s", (language) => {
    const cards = element(render({}, language), "ul", "site-admin-stock-resets__cards");
    const items = all(cards, "li");
    const result = (reset: StockResetRow) => COPY[reset.result === "succeeded" ? "admin.stock_reset_ok" : "admin.stock_reset_failed"][language];
    expect(items.map((item) => textNodes(item))).toEqual(
      ROWS.map((reset) => [
        formatBusinessDate(reset.business_date, language),
        result(reset),
        COPY["admin.col_sku_count"][language],
        String(reset.sku_count),
        COPY["admin.col_sku_count"][language],
      ]),
    );
    items.forEach((item, index) => {
      const reset = ROWS[index] ?? OCT5;
      expect(classes(tags(item, "li")[0] ?? "")).toContain("acs-admin__panel");
      const head = element(item, "div", "site-admin-stock-resets__card-head");
      expect(textNodes(head)).toEqual([formatBusinessDate(reset.business_date, language), result(reset)]);
      expect(all(item, "details")).toHaveLength(1);
      expect(item.indexOf(head) + head.length).toBeLessThan(item.indexOf(escapeHtml(COPY["admin.col_sku_count"][language])));
    });
    expect(tags(cards, "span").map(classes).filter((names) => names.includes("acs-tag"))).toEqual([
      ["acs-tag", "acs-tag--success"],
      ["acs-tag", "acs-tag--danger"],
      ["acs-tag", "acs-tag--success"],
      ["acs-tag", "acs-tag--success"],
    ]);
    expect(cards).not.toMatch(/<(a|button)\b/);
  });

  // 验收第 1 条「布局与显隐写在 site.css（手机的差异写在 767px 及以下的媒体查询里）」：表格与卡片两套都在页面里，各有自己的 class。
  it("renders both the table and the cards", () => {
    const html = render();
    expect(element(html, "div", "site-admin-stock-resets__table")).toContain("<table");
    expect(all(element(html, "ul", "site-admin-stock-resets__cards"), "li")).toHaveLength(ROWS.length);
  });
});

describe("day details", () => {
  function withDetails(details: DetailMap, language: Language = "en"): string {
    return render({ details }, language);
  }

  // 桌面与手机里某一天的展开处（details 元素）。
  function detailsOf(html: string, index: number): { desktop: string; phone: string } {
    const desktop = all(bodyRows(html)[index * 2 + 1] ?? "", "details")[0] ?? "";
    const phone = all(all(element(html, "ul", "site-admin-stock-resets__cards"), "li")[index] ?? "", "details")[0] ?? "";
    return { desktop, phone };
  }

  // SHOP-TASK-060 验收第 3 条「每个 SKU 一行显示 SKU 与 admin.stock_reset_breakdown（{initial}、{held}、{available} 依次为初始库存、
  // 有效预留与当日可用）」与 UX A07「▸（展开后）<sku> [admin.stock_reset_breakdown] [M1]」：取得明细后，桌面展开处为一张明细表，
  // 每个 SKU 一行两格（SKU、breakdown）；手机每个 SKU 一项（SKU 与其下的 breakdown，breakdown 为次要文字，照 A07-phone）。
  it.each(LANGUAGES)("lists each SKU with its breakdown in %s", (language) => {
    const html = withDetails({ [OCT5.id]: { kind: "ok", lines: LINES } }, language);
    const { desktop, phone } = detailsOf(html, 0);
    const summary = COPY["admin.col_sku_count"][language];
    expect(all(desktop, "tr").map((tr) => all(tr, "td").map((td) => textNodes(td).join("")))).toEqual(LINES.map((line) => [line.sku, breakdown(line, language)]));
    expect(textNodes(desktop)[0]).toBe(summary);
    expect(classes(tags(desktop, "table")[0] ?? "")).toContain("acs-admin__table");
    const items = all(phone, "div").filter((div) => classes(tags(div, "div")[0] ?? "").includes("site-admin-stock-resets__line"));
    expect(items.map((item) => textNodes(item))).toEqual(LINES.map((line) => [line.sku, breakdown(line, language)]));
    for (const item of items) {
      expect(classes(tags(item, "span")[1] ?? "")).toContain("acs-admin__muted");
    }
    expect(textNodes(phone)[0]).toBe(summary);
    // 其他几天没有明细。
    for (const index of [1, 2, 3]) {
      const others = detailsOf(html, index);
      expect(textNodes(others.desktop)).toEqual([summary]);
      expect(textNodes(others.phone)).toEqual([summary]);
    }
  });

  // 同一条：breakdown 依次填入初始库存、有效预留与当日可用（英文为「Initial 20 − active holds 2 = available today 18」，照 A07-desktop）。
  it("fills the breakdown in the order initial, held, available", () => {
    expect(breakdown({ sku: "x", initial_stock: 20, held_quantity: 2, available_stock: 18 }, "en")).toBe("Initial 20 − active holds 2 = available today 18");
    expect(textNodes(detailsOf(withDetails({ [OCT5.id]: { kind: "ok", lines: LINES } }), 0).desktop)).toContain(
      "Initial 30 − active holds 1 = available today 29",
    );
  });

  // SHOP-TASK-060 验收第 3 条「明细为空时展开后不显示行」（失败那天整笔回滚，SHOP-TASK-052「失败那天的 lines 为空数组」）：
  // 取得空明细后展开处只有 summary，没有明细表、行或提示文字。
  it.each(LANGUAGES)("shows no rows for an empty detail in %s", (language) => {
    const { desktop, phone } = detailsOf(withDetails({ [OCT4.id]: { kind: "ok", lines: [] } }, language), 1);
    for (const details of [desktop, phone]) {
      expect(textNodes(details)).toEqual([COPY["admin.col_sku_count"][language]]);
      expect(details).not.toMatch(/<(table|tr|td)\b/);
      expect(details).not.toContain("site-admin-stock-resets__line");
      expect(details).not.toContain(`role="alert"`);
      expect(attributes(tags(details, "details")[0] ?? "").get("aria-busy")).toBe("false");
    }
  });

  // SHOP-TASK-060 验收第 4 条「明细请求进行中该行展开处标 aria-busy」：只有读取中的那一天，桌面与手机的展开处标 aria-busy="true"，
  // 其余各天为 false；列表区不因明细读取而标 aria-busy。
  it("marks only the loading day as busy", () => {
    const html = withDetails({ [OCT3.id]: DETAIL_BUSY });
    ROWS.forEach((_reset, index) => {
      const { desktop, phone } = detailsOf(html, index);
      for (const details of [desktop, phone]) {
        expect(attributes(tags(details, "details")[0] ?? "").get("aria-busy")).toBe(index === 2 ? "true" : "false");
      }
    });
    expect(attributes(tags(element(html, "div", "site-admin-stock-resets__list"), "div")[0] ?? "").get("aria-busy")).toBe("false");
  });

  // SHOP-TASK-060 验收第 4 条「网络中断显示 common.network_check，其他失败显示 common.error_retry（明细读取失败只在该行展开处显示）」：
  // 提示（role="alert"）只在那一天桌面与手机的展开处里，列表区的其他位置与其他几天都没有提示；表格与卡片照常显示。
  it.each(LANGUAGES)("shows a detail failure only inside that day in %s", (language) => {
    for (const key of ["common.network_check", "common.error_retry"] as const) {
      const html = withDetails({ [OCT5.id]: { kind: "error", error: key } }, language);
      const alert = `<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div>`;
      const { desktop, phone } = detailsOf(html, 0);
      for (const details of [desktop, phone]) {
        expect(details).toContain(alert);
        expect(textNodes(details)).toEqual([COPY["admin.col_sku_count"][language], COPY[key][language]]);
      }
      expect(html.split(`role="alert"`)).toHaveLength(3);
      expect(bodyRows(html)).toHaveLength(ROWS.length * 2);
    }
  });

  // SHOP-TASK-060 验收第 3、4 条「首次展开时请求该日明细」「读取失败后收起再展开时重新请求，取得明细后不再请求」：
  // 展开时尚未请求或上次失败的要请求，读取中或已取得明细（含空明细）的不再请求。
  it("asks for a day only when it has not been read", () => {
    expect(needsDetail(undefined)).toBe(true);
    expect(needsDetail({ kind: "error", error: "common.error_retry" })).toBe(true);
    expect(needsDetail({ kind: "error", error: "common.network_check" })).toBe(true);
    expect(needsDetail(DETAIL_BUSY)).toBe(false);
    expect(needsDetail({ kind: "ok", lines: LINES })).toBe(false);
    expect(needsDetail({ kind: "ok", lines: [] })).toBe(false);
  });

  // 同一条「首次展开时请求」：打开页面（服务端渲染已登录的内容区）与读取一页列表都不请求任何一天的明细；
  // 页面源码里明细请求只在展开（details 的 toggle 且为 open）时发出。
  it("does not read any day before it is expanded", async () => {
    const calls = stubFetch(json(200, list()));
    renderSignedIn();
    const moves = fakeMoves<ListState>();
    await loadResets(1, new AbortController().signal, moves.target);
    expect(calls.map((call) => call.url)).toEqual(["/api/admin/stock-resets?page=1"]);
    expect(moves.states).toEqual([shown(list())]);
    expect(pageSource).toMatch(/onToggle=\{[\s\S]*?event\.currentTarget\.open[\s\S]*?onOpen\(reset\.id\)/);
  });

  // SHOP-TASK-060 验收第 4 条「401 用路由的 replace 进入 /admin/login；网络中断显示 common.network_check，其他失败显示 common.error_retry」
  // 用于明细：按内部 ID 读取那一天（带中止用的 signal），200 显示各行，401 以 replace 进入 /admin/login，网络中断、其他状态与不合格响应体
  // 在展开处显示相应提示。
  it.each<[Response | (() => never), string[], DetailState | null]>([
    [json(200, { ...OCT5, lines: LINES }), ["show"], { kind: "ok", lines: LINES }],
    [json(200, { ...OCT4, id: OCT5.id, lines: [] }), ["show"], { kind: "ok", lines: [] }],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [networkDown, ["show"], { kind: "error", error: "common.network_check" }],
    [json(404, { detail: "not_found" }), ["show"], { kind: "error", error: "common.error_retry" }],
    [json(500, {}), ["show"], { kind: "error", error: "common.error_retry" }],
    [json(200, { ...OCT5 }), ["show"], { kind: "error", error: "common.error_retry" }],
  ])("loads a day from the reply (%#)", async (reply, events, state) => {
    const calls = stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves<DetailState>();
    await loadDetail(OCT5.id, controller.signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/stock-resets/12");
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 同一条的规则部分：明细结果到显示的对应。
  it("chooses what to show for a day from the reply", () => {
    expect(detailStep({ kind: "ok", reset: { ...OCT5, lines: LINES } })).toEqual({ kind: "ok", lines: LINES });
    expect(detailStep({ kind: "none" })).toBe("login");
    expect(detailStep({ kind: "network" })).toEqual({ kind: "error", error: "common.network_check" });
    expect(detailStep({ kind: "failed" })).toEqual({ kind: "error", error: "common.error_retry" });
  });

  // SHOP-TASK-060 验收第 4 条「离开页面或翻页时中止旧请求，旧请求的结果不再更新页面」：明细请求的 signal 中止后，
  // 不论它回答什么（明细、401 或失败）都不显示也不跳转。
  it.each([json(200, { ...OCT5, lines: LINES }), json(401, { detail: "admin_session_required" }), json(500, {}), networkDown])(
    "ignores a day's reply after it was aborted (%#)",
    async (reply) => {
      stubFetch(reply);
      const controller = new AbortController();
      const moves = fakeMoves<DetailState>();
      const pending = loadDetail(OCT5.id, controller.signal, moves.target);
      controller.abort();
      await pending;
      expect(moves.events).toEqual([]);
    },
  );
});

describe("no results", () => {
  // SHOP-TASK-060 验收第 3 条「没有结果时的显示沿用 SHOP-TASK-048 的做法」（UX A02「状态与补充（0.9）」「桌面只显示列头，手机不显示卡片，
  // 不另写空状态文字」）：表格只有三个列头、tbody 为空；没有卡片、展开处与翻页按钮；列表区里除列头外没有任何文字。
  it.each(LANGUAGES)("shows only the column headers in %s", (language) => {
    const html = render({ state: shown(list({ total: 0, resets: [] })) }, language);
    const area = element(html, "div", "site-admin-stock-resets__list");
    expect(area).toContain("<tbody></tbody>");
    expect(area).not.toMatch(/<(li|ul|button|details)\b/);
    expect(textNodes(area)).toEqual([COPY["admin.col_date_myt"][language], COPY["admin.col_result"][language], COPY["admin.col_sku_count"][language]]);
    expect(area).not.toContain(`role="alert"`);
  });
});

describe("paging", () => {
  // SHOP-TASK-060 验收第 3 条「翻页…沿用 SHOP-TASK-048 的做法（每页 30 条）」（UX A02「状态与补充（0.9）」「总数超过一页时…上一页与
  // 下一页两个按钮…第一页禁用上一页，最后一页禁用下一页」）：不超过 30 条时没有翻页；页码取自响应。
  it("works out the previous and next pages", () => {
    expect(pagerOf(list({ total: 0, resets: [] }))).toBeNull();
    expect(pagerOf(list({ total: 30 }))).toBeNull();
    expect(pagerOf(list({ total: 31, page: 1 }))).toEqual({ prev: null, next: 2 });
    expect(pagerOf(list({ total: 31, page: 2 }))).toEqual({ prev: 1, next: null });
    expect(pagerOf(list({ total: 61, page: 2 }))).toEqual({ prev: 1, next: 3 });
    expect(pagerOf(list({ total: 90, page: 3 }))).toEqual({ prev: 2, next: null });
  });

  // 同一条：列表之后两个按钮，可见文字为 ‹ 与 ›，可访问名称为 common.a11y_page_prev 与 common.a11y_page_next，按页码禁用；不显示页码。
  it.each(LANGUAGES)("shows the two page buttons in %s", (language) => {
    const cases: [number, boolean, boolean][] = [
      [1, true, false],
      [2, false, false],
      [3, false, true],
    ];
    for (const [page, prevDisabled, nextDisabled] of cases) {
      const html = render({ state: shown(list({ total: 75, page })) }, language);
      const area = element(html, "div", "site-admin-stock-resets__list");
      const pager = element(area, "div", "site-admin-stock-resets__pager");
      expect(area.indexOf(pager)).toBeGreaterThan(area.indexOf("site-admin-stock-resets__cards"));
      const buttons = tags(pager, "button").map(attributes);
      expect(buttons.map((button) => button.get("aria-label"))).toEqual([COPY["common.a11y_page_prev"][language], COPY["common.a11y_page_next"][language]]);
      expect(buttons.map((button) => button.get("type"))).toEqual(["button", "button"]);
      expect(buttons.map((button) => button.has("disabled"))).toEqual([prevDisabled, nextDisabled]);
      expect(textNodes(pager)).toEqual(["‹", "›"]);
      expect(tags(html, "button")).toHaveLength(2);
      expect(visibleTexts(pager).join(" ")).not.toContain(String(page));
    }
  });

  // 同一条：翻页即以新页码读取列表（地址只带 page）。
  it("reads the requested page", async () => {
    const calls = stubFetch(json(200, list({ total: 75, page: 3 })));
    const moves = fakeMoves<ListState>();
    await loadResets(3, new AbortController().signal, moves.target);
    expect(calls.map((call) => call.url)).toEqual(["/api/admin/stock-resets?page=3"]);
    expect(moves.states).toEqual([shown(list({ total: 75, page: 3 }))]);
  });
});

describe("loading the list", () => {
  // SHOP-TASK-060 验收第 4 条「请求进行中标 aria-busy」：列表区读取中标 aria-busy（翻页时保留上一页的列表直到新结果到来，沿用 SHOP-TASK-048），
  // 取到之后不再标。
  it("marks the list area busy while a request is in progress", () => {
    const busyTag = (state: ListState) => tags(render({ state }), "div").find((tag) => classes(tag).includes("site-admin-stock-resets__list")) ?? "";
    expect(attributes(busyTag(INITIAL_LIST)).get("aria-busy")).toBe("true");
    expect(attributes(busyTag(shown(list()))).get("aria-busy")).toBe("false");
    expect(attributes(busyTag({ busy: true, list: list(), error: null })).get("aria-busy")).toBe("true");
  });

  // SHOP-TASK-060 验收第 4 条「401 用路由的 replace 进入 /admin/login；网络中断显示 common.network_check，其他失败显示 common.error_retry」。
  it("chooses what to show from the reply", () => {
    expect(listStep({ kind: "ok", list: list() })).toEqual(shown(list()));
    expect(listStep({ kind: "none" })).toBe("login");
    expect(listStep({ kind: "network" })).toEqual({ busy: false, list: null, error: "common.network_check" });
    expect(listStep({ kind: "failed" })).toEqual({ busy: false, list: null, error: "common.error_retry" });
  });

  // 同一条：读取一次（带上中止用的 signal）；200 显示列表，401 以 replace 进入 /admin/login，网络中断与其他失败（含响应体不合格）显示相应提示。
  it.each<[Response | (() => never), string[], ListState | null]>([
    [json(200, list()), ["show"], shown(list())],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [networkDown, ["show"], { busy: false, list: null, error: "common.network_check" }],
    [json(500, {}), ["show"], { busy: false, list: null, error: "common.error_retry" }],
    [json(200, { total: 1 }), ["show"], { busy: false, list: null, error: "common.error_retry" }],
  ])("loads the list from the reply (%#)", async (reply, events, state) => {
    const calls = stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves<ListState>();
    await loadResets(2, controller.signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/stock-resets?page=2");
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 同一条：提示在列表区（role="alert"），失败后不显示表格、卡片、展开处与翻页；标题与说明照常。
  it.each(LANGUAGES)("shows the failure messages in the list area in %s", (language) => {
    for (const key of ["common.network_check", "common.error_retry"] as const) {
      const html = render({ state: { busy: false, list: null, error: key } }, language);
      const area = element(html, "div", "site-admin-stock-resets__list");
      expect(area).toBe(
        `<div class="site-admin-stock-resets__list" aria-busy="false"><div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div></div>`,
      );
      expect(textNodes(html)).toEqual([COPY["admin.stock_reset_title"][language], COPY["admin.stock_reset_note"][language], COPY[key][language]]);
    }
  });

  // SHOP-TASK-060 验收第 4 条「离开页面或翻页时中止旧请求，旧请求的结果不再更新页面」：旧页的 signal 中止后，
  // 不论它回答什么（列表、401 或失败）都不显示也不跳转，只显示新页的结果。
  it.each([json(200, list({ resets: [OCT4], total: 31 })), json(401, { detail: "admin_session_required" }), json(500, {})])(
    "ignores the reply to a replaced page (%#)",
    async (oldReply) => {
      const fresh = list({ resets: [JAN1], total: 31, page: 2 });
      const calls = stubFetch(oldReply, json(200, fresh));
      const moves = fakeMoves<ListState>();
      const old = new AbortController();
      const pendingOld = loadResets(1, old.signal, moves.target);
      old.abort();
      const next = new AbortController();
      const pendingNext = loadResets(2, next.signal, moves.target);
      await Promise.all([pendingOld, pendingNext]);
      expect(calls).toHaveLength(2);
      expect(moves.events).toEqual(["show"]);
      expect(moves.states).toEqual([shown(fresh)]);
    },
  );

  // 同一条（离开页面同样中止）：signal 中止之后不论结果如何都不再更新。
  it("does nothing after leaving the page", async () => {
    stubFetch(json(200, list()));
    const controller = new AbortController();
    const moves = fakeMoves<ListState>();
    const pending = loadResets(1, controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });

  // 同一条「翻页时中止旧请求」也包括明细：页码变化或离开页面时，effect 的清理一并中止进行中的明细请求，换页时清空各天的明细状态。
  it("aborts the day requests together with the page", () => {
    expect(pageSource).toMatch(/return \(\) => \{\s*controller\.abort\(\);\s*for \(const request of requests\.values\(\)\) \{\s*request\.abort\(\);/);
    expect(pageSource).toMatch(/setPage\(next\);\s*setDetails\(\{\}\);/);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-060 验收第 5 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」
  // （SHOP-TASK-059 的同名测试扩展到列表与明细的各状态）：经路由打开（会话未返回）、已登录（菜单收起与展开）、列表的各状态与明细的各状态，
  // 每段文字（含 aria-label）都是当前语言的字典文案，或接口给的 SKU、SKU 数、按界面语言格式化的营业日期与填入数字的 breakdown；
  // 此外只有视觉稿给定的符号 ‹、›。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const reset of ROWS) {
      allowed.add(formatBusinessDate(reset.business_date, language));
      allowed.add(String(reset.sku_count));
    }
    for (const line of LINES) {
      allowed.add(line.sku);
      allowed.add(breakdown(line, language));
    }
    for (const symbol of ["‹", "›"]) {
      allowed.add(symbol);
    }
    const states: ListState[] = [
      INITIAL_LIST,
      shown(list()),
      shown(list({ total: 0, resets: [] })),
      shown(list({ total: 75, page: 2 })),
      { busy: true, list: list(), error: null },
      { busy: false, list: null, error: "common.network_check" },
      { busy: false, list: null, error: "common.error_retry" },
    ];
    const details: DetailMap = {
      [OCT5.id]: { kind: "ok", lines: LINES },
      [OCT4.id]: { kind: "ok", lines: [] },
      [OCT3.id]: { kind: "error", error: "common.network_check" },
      [JAN1.id]: DETAIL_BUSY,
    };
    const pages = [renderApp(STOCK_RESETS_PATH, language), renderSignedIn(language), renderSignedIn(language, { menuOpen: true })];
    for (const state of states) {
      pages.push(render({ state }, language), render({ state, details }, language));
    }
    for (const html of pages) {
      expect(html).not.toMatch(/\s(title|placeholder)=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
