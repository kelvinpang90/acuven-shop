import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ORDER_STATUSES } from "../api/adminOrders";
import type { AdminOrderList, AdminOrderRow, OrdersQuery } from "../api/adminOrders";
import { formatSen } from "../format";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  AdminOrdersContent,
  AdminOrdersView,
  filterBy,
  FIRST_QUERY,
  INITIAL_LIST,
  listStep,
  loadOrders,
  pagerOf,
  rememberedListQuery,
  rememberListQuery,
  searchFor,
} from "./AdminOrdersPage";
import type { AdminOrdersViewProps, ListState } from "./AdminOrdersPage";
import { formatDate } from "./TrackOrderPage";

// 上次的查询存在页面模块的内存变量里（SHOP-TASK-055）：每条测试之后放回第 1 页，测试之间互不影响。
afterEach(() => {
  rememberListQuery(FIRST_QUERY);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDERS_PATH = "/admin/orders";
const LOGIN_PATH = "/admin/login";

// 状态与它的名称（UX A02「状态筛选选项为各 [order.status_*]」），按 ORDER_STATUSES 的顺序。
const STATUS_KEYS: readonly CopyKey[] = [
  "order.status_awaiting",
  "order.status_paid",
  "order.status_packed",
  "order.status_shipped",
  "order.status_completed",
  "order.status_cancelled",
];

const SHIPPED: AdminOrderRow = {
  id: 42,
  order_number: "T4LW-6NQB",
  created_at: "2026-09-10T03:15:00Z",
  status: "demo_shipped",
  total_sen: 7640,
  refunds_pending: 0,
};
const COMPLETED: AdminOrderRow = {
  id: 41,
  order_number: "H3PZ-4W8C",
  created_at: "2026-09-09T09:30:00Z",
  status: "demo_completed",
  total_sen: 4200,
  refunds_pending: 2,
};
const CANCELLED: AdminOrderRow = {
  id: 40,
  order_number: "M8XR-7KQE",
  created_at: "2026-09-08T12:00:00Z",
  status: "demo_cancelled",
  total_sen: 5900,
  refunds_pending: 0,
};
const ROWS = [SHIPPED, COMPLETED, CANCELLED];

function list(overrides: Partial<AdminOrderList> = {}): AdminOrderList {
  return { total: ROWS.length, page: 1, page_size: 20, orders: ROWS, ...overrides };
}

function shown(value: AdminOrderList): ListState {
  return { busy: false, list: value, error: null };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function props(overrides: Partial<AdminOrdersViewProps> = {}): AdminOrdersViewProps {
  return {
    draft: "",
    status: null,
    state: shown(list()),
    onDraft: noop,
    onSearch: noop,
    onStatus: noop,
    onPage: noop,
    ...overrides,
  };
}

function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={ORDERS_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AdminOrdersViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AdminOrdersView {...props(overrides)} />, language));
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
  const textNodes = html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
  return [...textNodes, ...attributes].filter((value) => value !== "");
}

// 文本节点（不含属性），依出现顺序。
function textNodes(html: string): string[] {
  return html
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

// 某个元素（按 class 找到的第一个）从开始标签到与之配对的结束标签的内容。
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

// 某种元素的全部实例（不嵌套同名元素时使用）。
function all(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => elementAt(html, name, m.index));
}

function price(sen: number, language: Language): string {
  return translate(language, "common.price_myr", { amount: formatSen(sen) });
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

// 路由替身：记录页面显示的列表区状态与经 replace 去的地址。
function fakeMoves() {
  const events: string[] = [];
  const states: ListState[] = [];
  const target = {
    show: (state: ListState) => {
      states.push(state);
      events.push("show");
    },
    replace: (path: RoutePath) => {
      events.push(`replace ${path}`);
    },
  };
  return { target, events, states };
}

describe("search and filter", () => {
  // UX A02 线框「[admin.search_order][K ____] [admin.filter_status] ▾」与视觉稿 A02-desktop-list（搜索框以 admin.search_order 作占位文字）；
  // 验收第 3 条「状态筛选下拉（第一项为 admin.filter_status 表示全部，其余依次为六种 order.status_*）」：
  // 标题 admin.nav_orders 之后为搜索框（type=search，可访问名称与占位文字为 admin.search_order）与状态下拉（可访问名称 admin.filter_status），
  // 下拉第一项值为空、文字为 admin.filter_status，其余依次为六种状态；当前筛选的那一项标 selected。
  it.each(LANGUAGES)("shows the search box and the status filter in %s", (language) => {
    const html = render({ draft: "T4LW", status: "demo_paid" }, language);
    expect(html.indexOf(`<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_orders"][language])}</h1>`)).toBeLessThan(html.indexOf("<form"));
    const form = all(html, "form")[0] ?? "";
    const input = attributes(tags(form, "input")[0] ?? "");
    expect(input.get("type")).toBe("search");
    expect(input.get("aria-label")).toBe(COPY["admin.search_order"][language]);
    expect(input.get("placeholder")).toBe(COPY["admin.search_order"][language]);
    expect(input.get("value")).toBe("T4LW");
    const select = attributes(tags(form, "select")[0] ?? "");
    expect(select.get("aria-label")).toBe(COPY["admin.filter_status"][language]);
    const options = all(form, "option");
    expect(options.map((option) => attributes(option).get("value"))).toEqual(["", ...ORDER_STATUSES]);
    expect(options.map((option) => textNodes(option).join(""))).toEqual(
      [COPY["admin.filter_status"][language], ...STATUS_KEYS.map((key) => COPY[key][language])],
    );
    expect(options.filter((option) => attributes(option).has("selected")).map((option) => attributes(option).get("value"))).toEqual(["demo_paid"]);
  });

  // 验收第 3 条「提交时把输入原样作为 order_number，去掉首尾空白后为空则不筛选」「提交搜索或改变筛选回到第 1 页」：
  // 提交搜索保留当前状态、回到第 1 页，订单号原样（不去空白、不改大小写，规范化由服务端负责）；全是空白时为 null。
  it("submits the search as typed and goes back to page 1", () => {
    const current: OrdersQuery = { order_number: "OLD1-0000", status: "demo_packed", page: 4 };
    expect(searchFor(" t4lw-6nqb ", current)).toEqual({ order_number: " t4lw-6nqb ", status: "demo_packed", page: 1 });
    expect(searchFor("   ", current)).toEqual({ order_number: null, status: "demo_packed", page: 1 });
    expect(searchFor("", current)).toEqual({ order_number: null, status: "demo_packed", page: 1 });
  });

  // 同一条「改变筛选回到第 1 页」：已提交的订单号不变，状态换成所选（全部为 null）；打开时为全部订单的第 1 页。
  it("changes the filter and goes back to page 1", () => {
    expect(FIRST_QUERY).toEqual({ order_number: null, status: null, page: 1 });
    const current: OrdersQuery = { order_number: "T4LW-6NQB", status: null, page: 3 };
    expect(filterBy("demo_cancelled", current)).toEqual({ order_number: "T4LW-6NQB", status: "demo_cancelled", page: 1 });
    expect(filterBy(null, { ...current, status: "demo_paid" })).toEqual({ order_number: "T4LW-6NQB", status: null, page: 1 });
  });
});

describe("desktop table", () => {
  // 验收第 4 条「桌面表格列头依次为 pay.order_no、admin.col_date、admin.filter_status、admin.col_total、admin.col_refunds」（UX A02 线框）。
  it.each(LANGUAGES)("has the five column headers in order in %s", (language) => {
    const table = element(render({}, language), "div", "site-admin-orders__table");
    expect(all(table, "th").map((th) => textNodes(th).join(""))).toEqual(
      (["pay.order_no", "admin.col_date", "admin.filter_status", "admin.col_total", "admin.col_refunds"] as const).map((key) => COPY[key][language]),
    );
  });

  // 验收第 4 条「日期用 formatDate 按界面语言显示，合计用 common.price_myr 与 formatSen（RM 加金额）；状态显示对应的 order.status_*；
  // 待审数大于 0 时显示 admin.refunds_pending，为 0 时桌面表格显示 —」与 UX「状态与补充（0.9）」「为 0 时显示 —」：
  // 每行依次为订单号、日期、状态名、合计与待审数。
  // SHOP-TASK-055 改动：原来断言「行与单元格里没有链接或按钮（行不可点）」，本任务按验收第 2 条「桌面表格行的订单号链接到 /admin/orders/<内部 ID>」
  // 加上了订单号链接，改为只有订单号单元格里有一个链接、其余单元格没有链接，表格里没有按钮（待审退款数仍为文字）。
  it.each(LANGUAGES)("shows each order in a row in %s", (language) => {
    const table = element(render({}, language), "div", "site-admin-orders__table");
    const rows = all(all(table, "tbody")[0] ?? "", "tr");
    expect(rows.map((row) => all(row, "td").map((td) => textNodes(td).join("")))).toEqual([
      [SHIPPED.order_number, formatDate(SHIPPED.created_at, language), COPY["order.status_shipped"][language], price(7640, language), "—"],
      [
        COMPLETED.order_number,
        formatDate(COMPLETED.created_at, language),
        COPY["order.status_completed"][language],
        price(4200, language),
        translate(language, "admin.refunds_pending", { count: 2 }),
      ],
      [CANCELLED.order_number, formatDate(CANCELLED.created_at, language), COPY["order.status_cancelled"][language], price(5900, language), "—"],
    ]);
    expect(price(7640, language)).toBe("RM 76.40");
    for (const row of rows) {
      const cells = all(row, "td");
      expect(tags(cells[0] ?? "", "a")).toHaveLength(1);
      expect(cells.slice(1).join("")).not.toContain("<a");
    }
    expect(table).not.toContain("<button");
    expect(table).not.toContain("tabindex");
  });

  // SHOP-TASK-055 验收第 2 条「桌面表格行的订单号链接到 /admin/orders/<内部 ID>」与 UX「状态与补充（0.9）」「订单详情有自己的网址，
  // 路径带订单的内部 ID，订单号不进网址」（Kelvin 2026-10-06）：每行订单号是指向 /admin/orders/<内部 ID> 的站内链接，网址里没有订单号。
  it("links each order number to its detail by internal ID", () => {
    const table = element(render(), "div", "site-admin-orders__table");
    const links = all(table, "a");
    expect(links.map((link) => attributes(tags(link, "a")[0] ?? "").get("href"))).toEqual(ROWS.map((row) => `/admin/orders/${String(row.id)}`));
    expect(links.map((link) => textNodes(link).join(""))).toEqual(ROWS.map((row) => row.order_number));
    for (const row of ROWS) {
      expect(table).not.toContain(`href="/admin/orders/${row.order_number}"`);
    }
  });

  // SHOP-TASK-055 验收第 2 条「桌面在列表右侧显示详情、当前订单的表格行标 aria-selected」（视觉稿 A02-desktop 的 tr aria-selected="true"）：
  // 只有当前订单那一行标 aria-selected="true"，其余行不带这个属性；只看列表（未选订单）时没有行标出。
  it("marks the row of the open order as selected", () => {
    const selectedRows = (selectedId: string | null) =>
      tags(element(render({ selectedId }), "div", "site-admin-orders__table"), "tr")
        .filter((tag) => attributes(tag).has("aria-selected"))
        .map((tag) => attributes(tag).get("aria-selected"));
    expect(selectedRows("41")).toEqual(["true"]);
    const table = element(render({ selectedId: "41" }), "div", "site-admin-orders__table");
    const selected = all(all(table, "tbody")[0] ?? "", "tr").find((row) => attributes(tags(row, "tr")[0] ?? "").get("aria-selected") === "true") ?? "";
    expect(textNodes(selected)[0]).toBe(COMPLETED.order_number);
    expect(selectedRows(null)).toEqual([]);
    expect(selectedRows("999")).toEqual([]);
    expect(tags(render(), "tr").some((tag) => attributes(tag).has("aria-selected"))).toBe(false);
  });

  // 视觉稿 A02-desktop-list：状态为标签（已取消为描边标签，其余为中性标签），— 为次要文字，合计列靠右。
  it("follows the A02-desktop-list styling", () => {
    const table = element(render(), "div", "site-admin-orders__table");
    expect(classes(tags(table, "div")[0] ?? "")).toContain("acs-admin__panel");
    expect(classes(tags(table, "table")[0] ?? "")).toContain("acs-admin__table");
    const statusTags = tags(table, "span").map(classes);
    expect(statusTags).toEqual([
      ["acs-tag", "acs-tag--neutral"],
      ["acs-tag", "acs-tag--neutral"],
      ["acs-tag", "acs-tag--outline"],
    ]);
    expect(table).toContain(`<td class="acs-admin__muted">—</td>`);
    expect(classes(tags(table, "th")[3] ?? "")).toContain("site-admin-orders__num");
  });
});

describe("phone cards", () => {
  // 验收第 4 条「手机为卡片列表（订单号与状态、日期、合计与待审数）…为 0 时手机卡片不显示（照 A02-phone-list）」与
  // UX「状态与补充（0.9）」「手机卡片…待审数只作文字，为 0 时不显示」：每张卡片第一行为订单号与状态名，第二行为日期 · 合计，
  // 待审数大于 0 时再加 · admin.refunds_pending；没有 —。
  // SHOP-TASK-055 改动：原来断言卡片不是链接、列表项本身是面板；本任务按验收第 2 条「手机卡片整张为一个链接指向同一网址
  // （照 A02-phone-list，卡片里不嵌套其他链接）」改为每个列表项里恰好一个链接（面板样式在链接上），没有按钮，卡片的文字都在链接里。
  it.each(LANGUAGES)("shows each order as a card in %s", (language) => {
    const cards = element(render({}, language), "ul", "site-admin-orders__cards");
    const items = all(cards, "li");
    expect(items.map((item) => textNodes(item))).toEqual([
      [SHIPPED.order_number, COPY["order.status_shipped"][language], formatDate(SHIPPED.created_at, language), "·", price(7640, language)],
      [
        COMPLETED.order_number,
        COPY["order.status_completed"][language],
        formatDate(COMPLETED.created_at, language),
        "·",
        price(4200, language),
        "·",
        translate(language, "admin.refunds_pending", { count: 2 }),
      ],
      [CANCELLED.order_number, COPY["order.status_cancelled"][language], formatDate(CANCELLED.created_at, language), "·", price(5900, language)],
    ]);
    expect(cards).not.toContain("—");
    expect(cards).not.toContain("<button");
    for (const [index, item] of items.entries()) {
      const links = all(item, "a");
      expect(links).toHaveLength(1);
      const link = links[0] ?? "";
      expect(classes(tags(link, "a")[0] ?? "")).toEqual(["acs-admin__panel", "site-admin-orders__card"]);
      expect(attributes(tags(link, "a")[0] ?? "").get("href")).toBe(`/admin/orders/${String(ROWS[index]?.id)}`);
      expect(textNodes(link)).toEqual(textNodes(item));
      expect(element(item, "span", "site-admin-orders__card-head")).toContain("acs-tag");
      // 分隔符只是视觉，不读出。
      const separators = all(item, "span").filter((span) => textNodes(span).join("") === "·");
      expect(separators.length).toBeGreaterThan(0);
      for (const separator of separators) {
        expect(attributes(tags(separator, "span")[0] ?? "").get("aria-hidden")).toBe("true");
      }
    }
  });

  // 验收第 4 条「由 site.css 按宽度显隐」：表格与卡片两套都在页面里，各有自己的 class（桌面隐藏卡片、手机隐藏表格）。
  it("renders both the table and the cards", () => {
    const html = render();
    expect(element(html, "div", "site-admin-orders__table")).toContain("<table");
    expect(all(element(html, "ul", "site-admin-orders__cards"), "li")).toHaveLength(ROWS.length);
  });
});

describe("no matching orders", () => {
  // UX「状态与补充（0.9）」「没有符合条件的订单时，桌面只显示列头，手机不显示卡片，不另写空状态文字」与视觉稿 A02-desktop-empty：
  // 表格只有列头、tbody 为空；没有卡片与翻页按钮；列表区里除列头外没有任何文字。
  it.each(LANGUAGES)("shows only the column headers in %s", (language) => {
    const html = render({ draft: "ZZ99-0000", state: shown(list({ total: 0, orders: [] })) }, language);
    const area = element(html, "div", "site-admin-orders__list");
    expect(area).toContain("<tbody></tbody>");
    expect(all(area, "th")).toHaveLength(5);
    expect(area).not.toContain("<li");
    expect(area).not.toContain("<button");
    expect(textNodes(area)).toEqual(
      (["pay.order_no", "admin.col_date", "admin.filter_status", "admin.col_total", "admin.col_refunds"] as const).map((key) => COPY[key][language]),
    );
    expect(area).not.toContain(`role="alert"`);
  });
});

describe("paging", () => {
  // UX「状态与补充（0.9）」「列表每页 20 条。总数超过一页时…上一页与下一页两个按钮…第一页禁用上一页，最后一页禁用下一页」：
  // 不超过一页时没有翻页；第一页只有下一页、最后一页只有上一页，中间两页都有；页码取自响应。
  it("works out the previous and next pages", () => {
    expect(pagerOf(list({ total: 0, orders: [] }))).toBeNull();
    expect(pagerOf(list({ total: 20 }))).toBeNull();
    expect(pagerOf(list({ total: 21, page: 1 }))).toEqual({ prev: null, next: 2 });
    expect(pagerOf(list({ total: 21, page: 2 }))).toEqual({ prev: 1, next: null });
    expect(pagerOf(list({ total: 45, page: 2 }))).toEqual({ prev: 1, next: 3 });
    expect(pagerOf(list({ total: 60, page: 3 }))).toEqual({ prev: 2, next: null });
  });

  // 同一条与视觉稿 A02-desktop-list、A02-phone-list-pages：列表之后两个按钮，可见文字为 ‹ 与 ›，可访问名称为
  // common.a11y_page_prev 与 common.a11y_page_next，按页码禁用；桌面与手机是同一组按钮；不显示页码。
  it.each(LANGUAGES)("shows the two page buttons in %s", (language) => {
    const cases: [number, boolean, boolean][] = [
      [1, true, false],
      [2, false, false],
      [3, false, true],
    ];
    for (const [page, prevDisabled, nextDisabled] of cases) {
      const html = render({ state: shown(list({ total: 45, page })) }, language);
      const area = element(html, "div", "site-admin-orders__list");
      const pager = element(area, "div", "site-admin-orders__pager");
      expect(area.indexOf(pager)).toBeGreaterThan(area.indexOf("site-admin-orders__cards"));
      const buttons = tags(pager, "button").map(attributes);
      expect(buttons.map((button) => button.get("aria-label"))).toEqual([COPY["common.a11y_page_prev"][language], COPY["common.a11y_page_next"][language]]);
      expect(buttons.map((button) => button.get("type"))).toEqual(["button", "button"]);
      expect(buttons.map((button) => button.has("disabled"))).toEqual([prevDisabled, nextDisabled]);
      expect(textNodes(pager)).toEqual(["‹", "›"]);
      expect(tags(html, "button")).toHaveLength(2);
      expect(textNodes(html)).not.toContain(String(page));
    }
  });
});

describe("loading the list", () => {
  // 验收第 6 条「请求进行中列表区标 aria-busy」：打开时（框架确认已登录后挂载）列表区标 aria-busy，搜索与筛选已显示，还没有表格；
  // 取到之后不再标 aria-busy；翻页或换筛选时保留上一页的列表并标 aria-busy。
  it("marks the list area busy while a request is in progress", () => {
    expect(INITIAL_LIST).toEqual({ busy: true, list: null, error: null });
    const opening = renderToStaticMarkup(wrap(<AdminOrdersContent />, "en"));
    const openingArea = tags(opening, "div").find((tag) => classes(tag).includes("site-admin-orders__list")) ?? "";
    expect(attributes(openingArea).get("aria-busy")).toBe("true");
    expect(opening).toContain("<form");
    expect(opening).not.toContain("<table");
    const busyTag = (state: ListState) => tags(render({ state }), "div").find((tag) => classes(tag).includes("site-admin-orders__list")) ?? "";
    expect(attributes(busyTag(shown(list()))).get("aria-busy")).toBe("false");
    expect(attributes(busyTag({ busy: true, list: list(), error: null })).get("aria-busy")).toBe("true");
  });

  // 验收第 6 条「401 用路由的 replace 进入 /admin/login；网络中断显示 common.network_check，其他失败显示 common.error_retry」。
  it("chooses what to show from the reply", () => {
    expect(listStep({ kind: "ok", list: list() })).toEqual(shown(list()));
    expect(listStep({ kind: "none" })).toBe("login");
    expect(listStep({ kind: "network" })).toEqual({ busy: false, list: null, error: "common.network_check" });
    expect(listStep({ kind: "failed" })).toEqual({ busy: false, list: null, error: "common.error_retry" });
  });

  // 同一条：经接口查询一次（请求体为当前查询，带上中止用的 signal）；200 显示列表，401 以 replace 进入 /admin/login，
  // 网络中断与其他失败（含响应体不合格）显示相应提示；都不改网址（搜索与筛选只在页面内存里）。
  it.each<[Response | (() => never), string[], ListState | null]>([
    [json(200, list()), ["show"], shown(list())],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [networkDown, ["show"], { busy: false, list: null, error: "common.network_check" }],
    [json(500, {}), ["show"], { busy: false, list: null, error: "common.error_retry" }],
    [json(200, { total: 1 }), ["show"], { busy: false, list: null, error: "common.error_retry" }],
  ])("loads the list from the reply (%#)", async (reply, events, state) => {
    const calls = stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    const query: OrdersQuery = { order_number: "T4LW-6NQB", status: "demo_shipped", page: 2 };
    await loadOrders(query, controller.signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/orders/query");
    expect(calls[0]?.init.signal).toBe(controller.signal);
    const sent: unknown = JSON.parse(calls[0]?.init.body as string);
    expect(sent).toEqual(query);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 同一条：提示在列表区（role="alert"），失败后不显示表格、卡片与翻页；搜索与筛选照常。
  it.each(LANGUAGES)("shows the failure messages in the list area in %s", (language) => {
    for (const key of ["common.network_check", "common.error_retry"] as const) {
      const html = render({ state: { busy: false, list: null, error: key } }, language);
      const area = element(html, "div", "site-admin-orders__list");
      expect(area).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div>`);
      expect(area).not.toMatch(/<(table|ul|button)\b/);
      expect(html).toContain("<form");
    }
  });

  // 验收第 6 条「离开页面或发出新查询时中止旧请求，旧请求的结果不再更新页面」：旧查询的 signal 中止后，
  // 不论它回答什么（列表、401 或失败）都不显示也不跳转，只显示新查询的结果。
  it.each([json(200, list({ orders: [CANCELLED], total: 1 })), json(401, { detail: "admin_session_required" }), json(500, {})])(
    "ignores the reply to a replaced query (%#)",
    async (oldReply) => {
      const fresh = list({ orders: [SHIPPED], total: 1 });
      const calls = stubFetch(oldReply, json(200, fresh));
      const moves = fakeMoves();
      const old = new AbortController();
      const pendingOld = loadOrders(FIRST_QUERY, old.signal, moves.target);
      old.abort();
      const next = new AbortController();
      const pendingNext = loadOrders(filterBy("demo_shipped", FIRST_QUERY), next.signal, moves.target);
      await Promise.all([pendingOld, pendingNext]);
      expect(calls).toHaveLength(2);
      expect(moves.events).toEqual(["show"]);
      expect(moves.states).toEqual([shown(fresh)]);
    },
  );

  // 同一条「离开页面…中止」：离开页面（signal 中止）之后不论结果如何都不再更新。
  it("does nothing after leaving the page", async () => {
    stubFetch(json(200, list()));
    const controller = new AbortController();
    const moves = fakeMoves();
    const pending = loadOrders(FIRST_QUERY, controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("list and detail", () => {
  function content(orderId: string | null, language: Language = "en"): string {
    return renderToStaticMarkup(wrap(<AdminOrdersContent orderId={orderId} />, language));
  }

  // UX「状态与补充（0.9）」「桌面未选订单时列表占满内容区，不显示详情；选中订单后详情在列表右侧」与 SHOP-TASK-055 验收第 2 条
  // 「桌面在列表右侧显示详情…手机只显示详情与 common.back…由 site.css 按宽度显隐」：只看列表时没有详情区；
  // 打开一张订单时列表与详情并排放在 site-admin-orders-split 里，列表在前、详情在后（读取中标 aria-busy，顶部为返回链接）。
  it("puts the detail to the right of the list only when an order is open", () => {
    const listOnly = content(null);
    expect(listOnly).not.toContain("site-admin-orders-split");
    expect(listOnly).not.toContain("<section");
    const split = content("42");
    const wrapper = element(split, "div", "site-admin-orders-split");
    expect(wrapper.indexOf(`class="site-admin-orders"`)).toBeGreaterThan(0);
    expect(wrapper.indexOf("<section")).toBeGreaterThan(wrapper.indexOf(`class="site-admin-orders"`));
    const section = tags(wrapper, "section")[0] ?? "";
    expect(classes(section)).toEqual(["site-admin-order"]);
    expect(attributes(section).get("aria-busy")).toBe("true");
    expect(wrapper).toContain(`<a class="site-admin-order__back" href="${ORDERS_PATH}">`);
  });

  // SHOP-TASK-055 验收第 2 条「列表的查询条件（订单号、状态、页码）保存在页面模块的内存变量里，在列表与详情之间切换时保留」与
  // docs/HANDOFF.md 记录的 Kelvin 2026-10-07 决定「列表的筛选与页码只在页面内存里，在列表与详情之间切换时保留」：
  // 第一次打开为全部订单的第 1 页、搜索框为空；记下的查询在每次挂载（列表、详情、换一张订单、回到列表）时都取回，
  // 搜索框填上已提交的订单号，状态下拉选中所记的状态。
  it("keeps the query when switching between the list and a detail", () => {
    expect(rememberedListQuery()).toEqual(FIRST_QUERY);
    const fresh = tags(all(content(null), "form")[0] ?? "", "input")[0] ?? "";
    expect(attributes(fresh).get("value")).toBe("");
    const query: OrdersQuery = { order_number: "T4LW-6NQB", status: "demo_shipped", page: 3 };
    rememberListQuery(query);
    for (const orderId of [null, "42", "41", null]) {
      const form = all(content(orderId), "form")[0] ?? "";
      expect(attributes(tags(form, "input")[0] ?? "").get("value")).toBe("T4LW-6NQB");
      const selected = all(form, "option").filter((option) => attributes(option).has("selected"));
      expect(selected.map((option) => attributes(option).get("value"))).toEqual(["demo_shipped"]);
      expect(rememberedListQuery()).toEqual(query);
    }
  });

  // 同一条「不进网址或浏览器存储」与 DESIGN 1.11「权限与资料保护」：记下与取回查询不写 localStorage、sessionStorage、cookie 或历史记录，
  // 页面上的链接只用内部 ID，不含所记的订单号。
  it("keeps the remembered query out of the address and browser storage", () => {
    const storage = () => ({ getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() });
    const local = storage();
    const session = storage();
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", session);
    vi.stubGlobal("history", history);
    rememberListQuery({ order_number: "T4LW-6NQB", status: null, page: 2 });
    for (const html of [content(null), content("42")]) {
      for (const href of [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "")) {
        expect(href).not.toContain("T4LW");
        expect(href).not.toContain("?");
      }
    }
    expect(rememberedListQuery().order_number).toBe("T4LW-6NQB");
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
  });

  // SHOP-TASK-055 目的「待审退款数仍为文字」：选中订单时表格里的待审数仍不是链接。
  it("keeps the pending refund count as text", () => {
    const table = element(render({ selectedId: "41" }), "div", "site-admin-orders__table");
    const cell = all(all(table, "tbody")[0] ?? "", "tr").map((row) => all(row, "td")[4] ?? "")[1] ?? "";
    expect(textNodes(cell)).toEqual([translate("en", "admin.refunds_pending", { count: 2 })]);
    expect(cell).not.toContain("<a");
  });
});

describe("dictionary", () => {
  // 验收第 6 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：
  // 列表、空列表、翻页、读取中与两种失败，每段文字（含 aria-label 与 placeholder）都是当前语言的字典文案，或接口给的订单号、
  // 按界面语言格式化的日期、common.price_myr 的合计与 admin.refunds_pending；此外只有 UX 与视觉稿给定的符号 —、·、‹、›。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const row of ROWS) {
      allowed.add(row.order_number);
      allowed.add(formatDate(row.created_at, language));
      allowed.add(price(row.total_sen, language));
    }
    allowed.add(translate(language, "admin.refunds_pending", { count: COMPLETED.refunds_pending }));
    for (const symbol of ["—", "·", "‹", "›"]) {
      allowed.add(symbol);
    }
    const states: ListState[] = [
      INITIAL_LIST,
      shown(list()),
      shown(list({ total: 0, orders: [] })),
      shown(list({ total: 45, page: 2 })),
      { busy: true, list: list(), error: null },
      { busy: false, list: null, error: "common.network_check" },
      { busy: false, list: null, error: "common.error_retry" },
    ];
    const pages = [renderToStaticMarkup(wrap(<AdminOrdersContent />, language))];
    for (const state of states) {
      pages.push(render({ state }, language), render({ state, status: "demo_paid" }, language));
    }
    for (const html of pages) {
      expect(html).not.toMatch(/\stitle=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
