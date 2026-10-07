import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { REFUND_STATUSES } from "../api/adminRefunds";
import type { AdminRefundList, AdminRefundRow, RefundsQuery } from "../api/adminRefunds";
import { formatSen } from "../format";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  AdminRefundsContent,
  AdminRefundsRoute,
  AdminRefundsView,
  contentKey,
  filterBy,
  filterOrderNumber,
  firstQuery,
  INITIAL_LIST,
  INVALID_ORDER_LIST,
  listPathOf,
  listStep,
  loadRefunds,
  openingOf,
  pagerOf,
  pageTo,
  rememberedListQuery,
  rememberListQuery,
  scopeOf,
} from "./AdminRefundsPage";
import type { AdminRefundsViewProps, ListState } from "./AdminRefundsPage";
import { formatDateTime } from "./TrackOrderPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // 模块内存里记下的查询放回全部申请的第 1 页，各条测试互不影响。
  rememberListQuery(firstQuery(null));
});

const REFUNDS_PATH = "/admin/refunds";
const ORDERS_PATH = "/admin/orders";
const LOGIN_PATH = "/admin/login";

// 申请状态与它的名称（UX A03 状态筛选「[order.refund_requested] / [order.refund_approved] / [order.refund_rejected]」），按 REFUND_STATUSES 的顺序。
const STATUS_KEYS: readonly CopyKey[] = ["order.refund_requested", "order.refund_approved", "order.refund_rejected"];

// UX A03 桌面线框的六个列头。
const HEADER_KEYS: readonly CopyKey[] = [
  "admin.col_requested_at",
  "pay.order_no",
  "order.items",
  "detail.quantity",
  "admin.col_amount",
  "admin.filter_status",
];

const REQUESTED: AdminRefundRow = {
  id: 9,
  created_at: "2026-10-02T02:15:00Z",
  order_id: 42,
  order_number: "K7Q2-9MXA",
  status: "requested",
  amount_sen: 2763,
  lines: [{ name: "Soy Wax Candle", quantity: 1 }],
};
const APPROVED: AdminRefundRow = {
  id: 8,
  created_at: "2026-09-28T01:02:00Z",
  order_id: 41,
  order_number: "C2WF-8NTD",
  status: "approved",
  amount_sen: 12300,
  lines: [
    { name: "Woven Storage Basket", quantity: 1 },
    { name: "Crew Neck Tee", quantity: 3 },
  ],
};
const REJECTED: AdminRefundRow = {
  id: 7,
  created_at: "2026-09-27T08:20:00Z",
  order_id: 40,
  order_number: "R5JK-3PLV",
  status: "rejected",
  amount_sen: 8900,
  lines: [{ name: "Pullover Hoodie", quantity: 2 }],
};
const ROWS = [REQUESTED, APPROVED, REJECTED];

function list(overrides: Partial<AdminRefundList> = {}): AdminRefundList {
  return { total: ROWS.length, page: 1, page_size: 20, refunds: ROWS, ...overrides };
}

// 只含 H3PZ-4W8C 一张订单的申请（A03-desktop-order）。
const ORDER_ROWS: AdminRefundRow[] = [
  { ...REQUESTED, id: 12, order_id: 42, order_number: "H3PZ-4W8C" },
  { ...REJECTED, id: 11, order_id: 42, order_number: "H3PZ-4W8C" },
];

function shown(value: AdminRefundList): ListState {
  return { busy: false, list: value, error: null };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function props(overrides: Partial<AdminRefundsViewProps> = {}): AdminRefundsViewProps {
  return {
    scope: "all",
    status: null,
    state: shown(list()),
    onStatus: noop,
    onPage: noop,
    ...overrides,
  };
}

function wrap(element: ReactNode, language: Language, path = REFUNDS_PATH) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={path}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AdminRefundsViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AdminRefundsView {...props(overrides)} />, language));
}

function renderApp(path: string, language: Language = "en"): string {
  return renderToStaticMarkup(<App initialPath={path} storage={languageStorage(language)} />);
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

// 某种元素的全部实例（同名元素嵌套时外层在前）。
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

describe("status filter", () => {
  // UX A03 线框「[admin.filter_status] ▾（选项：[order.refund_requested] / [order.refund_approved] / [order.refund_rejected]）」与
  // SHOP-TASK-056 验收第 4 条「状态筛选下拉第一项为 admin.filter_status 表示全部」：标题 admin.nav_refunds（h1）之后为状态下拉
  // （可访问名称 admin.filter_status），第一项值为空、文字为 admin.filter_status，其余依次为三种申请状态；当前筛选的那一项标 selected。
  it.each(LANGUAGES)("shows the title and the status filter in %s", (language) => {
    const html = render({ status: "approved" }, language);
    const h1 = `<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_refunds"][language])}</h1>`;
    expect(html).toContain(h1);
    expect(html.indexOf(h1)).toBeLessThan(html.indexOf("<select"));
    const select = all(html, "select")[0] ?? "";
    expect(attributes(tags(select, "select")[0] ?? "").get("aria-label")).toBe(COPY["admin.filter_status"][language]);
    const options = all(select, "option");
    expect(options.map((option) => attributes(option).get("value"))).toEqual(["", ...REFUND_STATUSES]);
    expect(options.map((option) => textNodes(option).join(""))).toEqual(
      [COPY["admin.filter_status"][language], ...STATUS_KEYS.map((key) => COPY[key][language])],
    );
    expect(options.filter((option) => attributes(option).has("selected")).map((option) => attributes(option).get("value"))).toEqual(["approved"]);
  });

  // SHOP-TASK-056 验收第 4 条「翻页与没有结果时的显示沿用 SHOP-TASK-048 的做法」（SHOP-TASK-048「改变筛选…回到第 1 页」）与
  // 验收第 5 条「可与状态筛选同时用」：打开时为全部状态的第 1 页；改变筛选回到第 1 页、订单筛选不变；翻页时状态与订单筛选不变。
  it("goes back to page 1 when the filter changes", () => {
    expect(firstQuery(null)).toEqual({ status: null, order_id: null, page: 1 });
    expect(firstQuery(42)).toEqual({ status: null, order_id: 42, page: 1 });
    const current: RefundsQuery = { status: "requested", order_id: 42, page: 3 };
    expect(filterBy("rejected", current)).toEqual({ status: "rejected", order_id: 42, page: 1 });
    expect(filterBy(null, current)).toEqual({ status: null, order_id: 42, page: 1 });
    expect(filterBy("approved", { status: null, order_id: null, page: 5 })).toEqual({ status: "approved", order_id: null, page: 1 });
    expect(pageTo(4, current)).toEqual({ status: "requested", order_id: 42, page: 4 });
  });
});

describe("desktop table", () => {
  // UX A03 桌面线框「[admin.col_requested_at] [pay.order_no] [order.items] [detail.quantity] [admin.col_amount] [admin.filter_status]」与
  // SHOP-TASK-056 验收第 4 条「桌面表格列头依次为…（UX A03 为六列；视觉稿把件数并在商品列里，以 UX 为准）」：六个列头依此顺序。
  it.each(LANGUAGES)("has the six column headers in order in %s", (language) => {
    const table = element(render({}, language), "div", "site-admin-refunds__table");
    expect(all(table, "th").map((th) => textNodes(th).join(""))).toEqual(HEADER_KEYS.map((key) => COPY[key][language]));
  });

  // SHOP-TASK-056 验收第 4 条「order.items 列逐行列出商品名称、detail.quantity 列逐行对应件数；时间用 formatDateTime，
  // 金额用 common.price_myr 与 formatSen」：每行依次为申请时间、订单号、商品名称（逐行）、件数（与名称逐行对应）、RM 金额与状态名。
  it.each(LANGUAGES)("shows each refund request in a row in %s", (language) => {
    const table = element(render({}, language), "div", "site-admin-refunds__table");
    const rows = all(all(table, "tbody")[0] ?? "", "tr");
    expect(rows.map((row) => all(row, "td").map((td) => textNodes(td)))).toEqual(
      ROWS.map((row, index) => [
        [formatDateTime(row.created_at, language)],
        [row.order_number],
        row.lines.map((line) => line.name),
        row.lines.map((line) => String(line.quantity)),
        [price(row.amount_sen, language)],
        [COPY[STATUS_KEYS[index] ?? "order.refund_requested"][language]],
      ]),
    );
    expect(price(2763, language)).toBe("RM 27.63");
  });

  // 同一条「商品与件数逐行对应」：两个单元格里各有一个列表容器，名称与件数的各项一一对应、个数相同。
  it("pairs each item name with its quantity", () => {
    const table = element(render(), "div", "site-admin-refunds__table");
    const row = all(all(table, "tbody")[0] ?? "", "tr")[1] ?? "";
    // 容器之内的各项（all 的第一项是容器本身）。
    const [names, quantities] = all(row, "td").slice(2, 4).map((td) => all(element(td, "span", "site-admin-refunds__lines"), "span").slice(1));
    expect(names?.map((span) => textNodes(span).join(""))).toEqual(["Woven Storage Basket", "Crew Neck Tee"]);
    expect(quantities?.map((span) => textNodes(span).join(""))).toEqual(["1", "3"]);
  });

  // 视觉稿 A03-desktop「状态标签照视觉稿（审核中、已批准、已拒绝三种样式）」：审核中为演示标签、已批准为成功标签、已拒绝为描边标签；
  // 表格在面板里，金额列（列头与单元格）靠右。
  it("follows the A03-desktop styling", () => {
    const table = element(render(), "div", "site-admin-refunds__table");
    expect(classes(tags(table, "div")[0] ?? "")).toContain("acs-admin__panel");
    expect(classes(tags(table, "table")[0] ?? "")).toContain("acs-admin__table");
    expect(tags(all(table, "tbody")[0] ?? "", "span").map(classes).filter((names) => names.includes("acs-tag"))).toEqual([
      ["acs-tag", "acs-tag--demo"],
      ["acs-tag", "acs-tag--success"],
      ["acs-tag", "acs-tag--outline"],
    ]);
    expect(classes(tags(table, "th")[4] ?? "")).toContain("site-admin-refunds__num");
    for (const row of all(all(table, "tbody")[0] ?? "", "tr")) {
      expect(classes(tags(all(row, "td")[4] ?? "", "td")[0] ?? "")).toContain("site-admin-refunds__num");
    }
  });

  // SHOP-TASK-057 验收第 2 条「列表行链接到 /admin/refunds/<内部 ID>」（取代 SHOP-TASK-056 的「行不可点」）：每行只有订单号单元格里
  // 一个指向该申请内部 ID 的站内链接（网址里没有订单号），其余单元格没有链接；表格里没有按钮；只看列表时没有行标 aria-selected。
  it("links each row to the refund detail", () => {
    const table = element(render(), "div", "site-admin-refunds__table");
    const rows = all(all(table, "tbody")[0] ?? "", "tr");
    expect(rows.map((row) => all(row, "td").map((td) => tags(td, "a").map((a) => attributes(a).get("href"))))).toEqual(
      ROWS.map((row) => [[], [`/admin/refunds/${String(row.id)}`], [], [], [], []]),
    );
    expect(rows.map((row) => textNodes(all(row, "a")[0] ?? ""))).toEqual(ROWS.map((row) => [row.order_number]));
    expect(table).not.toMatch(/<button\b/);
    expect(table).not.toContain("aria-selected");
  });

  // SHOP-TASK-057 验收第 2 条「桌面在列表右侧显示详情、当前行标 aria-selected」（视觉稿 A03-desktop、A03-desktop-reviewed）：
  // 打开的申请（路径里的内部 ID）所在的行标 aria-selected="true"，其余行不标；ID 不在本页时没有行标。
  it("marks the open request's row as selected", () => {
    for (const row of ROWS) {
      const table = element(render({ selectedId: String(row.id) }), "div", "site-admin-refunds__table");
      const selected = tags(all(table, "tbody")[0] ?? "", "tr").map((tr) => attributes(tr).get("aria-selected"));
      expect(selected).toEqual(ROWS.map((other) => (other.id === row.id ? "true" : undefined)));
    }
    expect(render({ selectedId: "99" })).not.toContain("aria-selected");
    expect(render({ selectedId: "order" })).not.toContain("aria-selected");
  });
});

describe("phone cards", () => {
  // SHOP-TASK-056 验收第 4 条「手机卡片照 A03-phone-list 以名称乘件数显示」与 UX A03 手机线框「<时间> [K]<订单号> / <名称> x1 [M1] /
  // [order.refund_requested|approved|rejected]」：每张卡片依次为申请时间、订单号、「名称 × 件数」（多行商品逐一列出）· 金额、状态标签；
  // 分隔符 · 只是视觉，以 aria-hidden 标出；状态标签样式与表格相同。
  it.each(LANGUAGES)("shows each refund request as a card in %s", (language) => {
    const cards = element(render({}, language), "ul", "site-admin-refunds__cards");
    const items = all(cards, "li");
    expect(items.map((item) => textNodes(item))).toEqual(
      ROWS.map((row, index) => [
        formatDateTime(row.created_at, language),
        row.order_number,
        ...row.lines.flatMap((line) => [`${line.name} × ${String(line.quantity)}`, "·"]),
        price(row.amount_sen, language),
        COPY[STATUS_KEYS[index] ?? "order.refund_requested"][language],
      ]),
    );
    for (const item of items) {
      // SHOP-TASK-057 验收第 2 条「列表行链接到 /admin/refunds/<内部 ID>」：面板样式在整张卡片的链接上（A03-phone-list）。
      expect(classes(tags(item, "a")[0] ?? "")).toEqual(["acs-admin__panel", "site-admin-refunds__card"]);
      const separators = all(item, "span").filter((span) => textNodes(span).join("") === "·" && tags(span, "span").length === 1);
      expect(separators.length).toBeGreaterThan(0);
      for (const separator of separators) {
        expect(attributes(tags(separator, "span")[0] ?? "").get("aria-hidden")).toBe("true");
      }
    }
    expect(tags(cards, "span").map(classes).filter((names) => names.includes("acs-tag"))).toEqual([
      ["acs-tag", "acs-tag--demo"],
      ["acs-tag", "acs-tag--success"],
      ["acs-tag", "acs-tag--outline"],
    ]);
  });

  // SHOP-TASK-057 验收第 2 条「列表行链接到 /admin/refunds/<内部 ID>」与视觉稿 A03-phone-list（取代 SHOP-TASK-056 的「卡片不可点」）：
  // 每张卡片恰好是一个指向该申请内部 ID 的链接，卡片文字都在链接里，右侧为 › 图形（aria-hidden）；没有按钮。
  it("makes each card one link to the refund detail", () => {
    const cards = element(render(), "ul", "site-admin-refunds__cards");
    const items = all(cards, "li");
    expect(items.map((item) => tags(item, "a").map((a) => attributes(a).get("href")))).toEqual(ROWS.map((row) => [`/admin/refunds/${String(row.id)}`]));
    for (const item of items) {
      expect(textNodes(all(item, "a")[0] ?? "")).toEqual(textNodes(item));
      const svg = tags(item, "svg")[0] ?? "";
      expect(attributes(svg).get("aria-hidden")).toBe("true");
    }
    expect(cards).not.toMatch(/<button\b/);
  });

  // 验收第 1 条「布局与显隐写在 site.css」：表格与卡片两套都在页面里，各有自己的 class（桌面隐藏卡片、手机隐藏表格）。
  it("renders both the table and the cards", () => {
    const html = render();
    expect(element(html, "div", "site-admin-refunds__table")).toContain("<table");
    expect(all(element(html, "ul", "site-admin-refunds__cards"), "li")).toHaveLength(ROWS.length);
  });
});

describe("no matching requests", () => {
  // SHOP-TASK-056 验收第 4 条「没有结果时的显示沿用 SHOP-TASK-048 的做法」（UX A02「状态与补充（0.9）」「桌面只显示列头，手机不显示卡片，
  // 不另写空状态文字」）：表格只有六个列头、tbody 为空；没有卡片与翻页按钮；列表区里除列头外没有任何文字。
  it.each(LANGUAGES)("shows only the column headers in %s", (language) => {
    const html = render({ state: shown(list({ total: 0, refunds: [] })) }, language);
    const area = element(html, "div", "site-admin-refunds__list");
    expect(area).toContain("<tbody></tbody>");
    expect(area).not.toMatch(/<(li|ul|button)\b/);
    expect(textNodes(area)).toEqual(HEADER_KEYS.map((key) => COPY[key][language]));
    expect(area).not.toContain(`role="alert"`);
  });
});

describe("paging", () => {
  // SHOP-TASK-056 验收第 4 条「翻页沿用 SHOP-TASK-048 的做法」（UX A02「状态与补充（0.9）」「总数超过一页时…上一页与下一页两个按钮…
  // 第一页禁用上一页，最后一页禁用下一页」）：不超过一页时没有翻页；页码取自响应。
  it("works out the previous and next pages", () => {
    expect(pagerOf(list({ total: 0, refunds: [] }))).toBeNull();
    expect(pagerOf(list({ total: 20 }))).toBeNull();
    expect(pagerOf(list({ total: 21, page: 1 }))).toEqual({ prev: null, next: 2 });
    expect(pagerOf(list({ total: 21, page: 2 }))).toEqual({ prev: 1, next: null });
    expect(pagerOf(list({ total: 45, page: 2 }))).toEqual({ prev: 1, next: 3 });
  });

  // 同一条：列表之后两个按钮，可见文字为 ‹ 与 ›，可访问名称为 common.a11y_page_prev 与 common.a11y_page_next，按页码禁用；不显示页码。
  it.each(LANGUAGES)("shows the two page buttons in %s", (language) => {
    const cases: [number, boolean, boolean][] = [
      [1, true, false],
      [2, false, false],
      [3, false, true],
    ];
    for (const [page, prevDisabled, nextDisabled] of cases) {
      const html = render({ state: shown(list({ total: 45, page })) }, language);
      const area = element(html, "div", "site-admin-refunds__list");
      const pager = element(area, "div", "site-admin-refunds__pager");
      expect(area.indexOf(pager)).toBeGreaterThan(area.indexOf("site-admin-refunds__cards"));
      const buttons = tags(pager, "button").map(attributes);
      expect(buttons.map((button) => button.get("aria-label"))).toEqual([COPY["common.a11y_page_prev"][language], COPY["common.a11y_page_next"][language]]);
      expect(buttons.map((button) => button.get("type"))).toEqual(["button", "button"]);
      expect(buttons.map((button) => button.has("disabled"))).toEqual([prevDisabled, nextDisabled]);
      expect(textNodes(pager)).toEqual(["‹", "›"]);
      expect(tags(html, "button")).toHaveLength(2);
      // 不显示页码：翻页区（含按钮的可访问名称）里没有当前页的数字。表格与卡片里的件数也是数字，不在此列。
      expect(visibleTexts(pager).join(" ")).not.toContain(String(page));
    }
  });
});

describe("filtering by order", () => {
  // UX A03「状态与补充（0.9）」「从 A02 的待审退款数进入时只列该订单的申请」与 SHOP-TASK-056 验收第 5 条「以路径里的订单内部 ID 作为 order_id 查询，
  // 可与状态筛选同时用」：合法段值成为请求体的 order_id（与所选状态、页码一起），地址只有 lang，不含订单内部 ID 或订单号。
  it("posts the internal order ID from the path together with the status", async () => {
    const { scope, orderId } = scopeOf("42");
    expect(scope).toBe("order");
    const calls = stubFetch(json(200, list({ refunds: ORDER_ROWS, total: 2 })));
    const moves = fakeMoves();
    await loadRefunds(filterBy("requested", firstQuery(orderId)), "zh", new AbortController().signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/refunds/query?lang=zh");
    expect(JSON.parse(calls[0]?.init.body as string)).toEqual({ status: "requested", order_id: 42, page: 1 });
    expect(moves.events).toEqual(["show"]);
  });

  // 同一条「状态筛选旁显示筛选标签（pay.order_no 加该单订单号，订单号取自返回的第一行，没有行时不显示标签）与 list.filter_clear
  // （链接到 /admin/refunds）」（视觉稿 A03-desktop-order、A03-phone-list-order）：下拉之后为中性标签「pay.order_no 订单号」与
  // 次要按钮样式的清除链接；不按订单筛选时两者都没有。
  it.each(LANGUAGES)("shows the order tag and the clear link next to the status filter in %s", (language) => {
    const html = render({ scope: "order", state: shown(list({ refunds: ORDER_ROWS, total: 2 })) }, language);
    const filters = element(html, "div", "site-admin-refunds__filters");
    const order = element(filters, "div", "site-admin-refunds__order");
    expect(filters.indexOf("<select")).toBeLessThan(filters.indexOf(order));
    const tag = all(order, "span").find((span) => classes(tags(span, "span")[0] ?? "").includes("acs-tag")) ?? "";
    expect(classes(tags(tag, "span")[0] ?? "")).toEqual(["acs-tag", "acs-tag--neutral"]);
    expect(textNodes(tag)).toEqual([COPY["pay.order_no"][language], "H3PZ-4W8C"]);
    const links = all(order, "a");
    expect(links).toHaveLength(1);
    const link = tags(links[0] ?? "", "a")[0] ?? "";
    expect(attributes(link).get("href")).toBe(REFUNDS_PATH);
    expect(classes(link)).toEqual(expect.arrayContaining(["acs-admin__btn", "acs-admin__btn--secondary"]));
    expect(textNodes(links[0] ?? "")).toEqual([COPY["list.filter_clear"][language]]);
    expect(order.indexOf("acs-tag")).toBeLessThan(order.indexOf("<a"));

    const plain = render({}, language);
    expect(plain).not.toContain("site-admin-refunds__order");
    expect(plain).not.toContain(escapeHtml(COPY["list.filter_clear"][language]));
  });

  // 同一条「订单号取自返回的第一行，没有行时不显示标签」：读取中（还没有列表）、失败与空列表时只有清除链接，没有标签；
  // 不按订单筛选时即使有行也不取订单号。
  it("shows the tag only when the filtered list has rows", () => {
    expect(filterOrderNumber(list({ refunds: ORDER_ROWS }), true)).toBe("H3PZ-4W8C");
    expect(filterOrderNumber(list({ refunds: [], total: 0 }), true)).toBeNull();
    expect(filterOrderNumber(null, true)).toBeNull();
    expect(filterOrderNumber(list(), false)).toBeNull();
    for (const state of [INITIAL_LIST, shown(list({ refunds: [], total: 0 })), { busy: false, list: null, error: "common.error_retry" } as ListState]) {
      const order = element(render({ scope: "order", state }), "div", "site-admin-refunds__order");
      expect(order).not.toContain("acs-tag");
      expect(textNodes(order)).toEqual([COPY["list.filter_clear"].en]);
    }
  });

  // SHOP-TASK-056 验收第 5 条「路径 ID 不是不带符号与前导零的正整数时不发请求，显示 common.error_retry 与 list.filter_clear」：
  // 这些段值都归为 invalid（不给查询用的订单 ID）；合法的为 order，没有段值为 all。
  it.each(["0", "00", "042", "+42", "-1", "4.2", "1e3", " 42", "42 ", "abc", "2147483648", "99999999999999999999", "４２"])(
    "treats the order ID %j as invalid",
    (value) => {
      expect(scopeOf(value)).toEqual({ scope: "invalid", orderId: null });
    },
  );

  it("accepts plain positive order IDs", () => {
    expect(scopeOf(null)).toEqual({ scope: "all", orderId: null });
    expect(scopeOf("1")).toEqual({ scope: "order", orderId: 1 });
    expect(scopeOf("2147483647")).toEqual({ scope: "order", orderId: 2147483647 });
  });

  // 同一条：不合法时内容区只有标题、common.error_retry 提示（role="alert"）与 list.filter_clear 链接；没有状态下拉、标签、表格与卡片，
  // 列表区不标 aria-busy；渲染过程不发请求。
  it.each(LANGUAGES)("shows common.error_retry and the clear link for an invalid order ID in %s", (language) => {
    const calls = stubFetch();
    const html = renderToStaticMarkup(wrap(<AdminRefundsContent orderId="042" />, language, "/admin/refunds/order/042"));
    expect(INVALID_ORDER_LIST).toEqual({ busy: false, list: null, error: "common.error_retry" });
    expect(html).not.toContain("<select");
    expect(html).not.toMatch(/<(table|ul|button)\b/);
    expect(html).not.toContain("acs-tag");
    const area = element(html, "div", "site-admin-refunds__list");
    expect(attributes(tags(area, "div")[0] ?? "").get("aria-busy")).toBe("false");
    expect(area).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY["common.error_retry"][language])}</span></div>`);
    const link = tags(element(html, "div", "site-admin-refunds__order"), "a")[0] ?? "";
    expect(attributes(link).get("href")).toBe(REFUNDS_PATH);
    expect(textNodes(html)).toEqual([COPY["admin.nav_refunds"][language], COPY["list.filter_clear"][language], COPY["common.error_retry"][language]]);
    expect(calls).toEqual([]);
  });

  // 同一条「以路径里的订单内部 ID 作为 order_id 查询」与「list.filter_clear（链接到 /admin/refunds）」：路径的订单 ID 变化时（经清除链接回到
  // /admin/refunds、换成另一张订单、或合法与不合法之间切换），内容区以不同的 key 重新挂载，查询从路径重新开始，不沿用旧的 order_id、状态与页码。
  it("starts over when the order ID in the path changes", () => {
    const paths: [string | null, string][] = [
      ["42", "/admin/refunds/order/42"],
      [null, REFUNDS_PATH],
      ["43", "/admin/refunds/order/43"],
      ["x", "/admin/refunds/order/x"],
    ];
    const keys = paths.map(([orderId]) => {
      const routed = AdminRefundsRoute({ orderId }) as ReactElement<{ orderId: string | null }>;
      expect(routed.type).toBe(AdminRefundsContent);
      expect(routed.props.orderId).toBe(orderId);
      expect(routed.key).toBe(contentKey(orderId));
      return routed.key;
    });
    expect(new Set(keys).size).toBe(paths.length);
    expect(contentKey("42")).toBe(contentKey("42"));

    // 每次挂载都从该路径的第 1 页、全部状态开始：/admin/refunds 没有订单筛选与清除链接，/order/43 有，不合法 ID 只有提示与清除链接。
    const calls = stubFetch();
    const markup = ([orderId, path]: [string | null, string]) => renderToStaticMarkup(wrap(AdminRefundsRoute({ orderId }), "en", path));
    const [order42 = "", all42 = "", order43 = "", invalid = ""] = paths.map(markup);
    for (const html of [order42, order43]) {
      expect(html).toContain("site-admin-refunds__order");
      expect(attributes(tags(html, "div").find((tag) => classes(tag).includes("site-admin-refunds__list")) ?? "").get("aria-busy")).toBe("true");
      expect(tags(html, "option").filter((tag) => attributes(tag).has("selected")).map((tag) => attributes(tag).get("value"))).toEqual([""]);
    }
    expect(all42).not.toContain("site-admin-refunds__order");
    expect(all42).toContain("<select");
    expect(invalid).not.toContain("<select");
    expect(invalid).toContain(escapeHtml(COPY["common.error_retry"].en));
    expect(calls).toEqual([]);
    expect(firstQuery(scopeOf("43").orderId)).toEqual({ status: null, order_id: 43, page: 1 });
    expect(firstQuery(scopeOf(null).orderId)).toEqual({ status: null, order_id: null, page: 1 });
  });

  // 同一条「订单内部 ID 只在路径里，订单号只在页面内存里，不进查询参数或浏览器存储」：按订单查询与显示标签的整个过程不写
  // localStorage、sessionStorage、cookie 或历史记录；页面上的链接不含订单号或查询串。
  it("keeps the order number out of addresses and browser storage", async () => {
    const storage = () => ({ getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() });
    const local = storage();
    const session = storage();
    const cookieWrites: string[] = [];
    const doc = {};
    Object.defineProperty(doc, "cookie", {
      get: () => "",
      set: (value: string) => {
        cookieWrites.push(value);
      },
    });
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", session);
    vi.stubGlobal("document", doc);
    vi.stubGlobal("history", history);

    const calls = stubFetch(json(200, list({ refunds: ORDER_ROWS, total: 2 })));
    const moves = fakeMoves();
    await loadRefunds(firstQuery(42), "en", new AbortController().signal, moves.target);
    expect(calls[0]?.url).not.toContain("H3PZ");
    const html = render({ scope: "order", state: moves.states[0] ?? INITIAL_LIST });
    expect(html).toContain("H3PZ-4W8C");
    for (const href of [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "")) {
      expect(href).not.toContain("H3PZ");
      expect(href).not.toContain("?");
    }
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
  });
});

describe("loading the list", () => {
  // SHOP-TASK-056 验收第 6 条「请求进行中列表区标 aria-busy」：打开时（框架确认已登录后挂载）列表区标 aria-busy，状态下拉已显示，
  // 还没有表格；取到之后不再标 aria-busy；换筛选或翻页时保留上一页的列表并标 aria-busy。
  it("marks the list area busy while a request is in progress", () => {
    expect(INITIAL_LIST).toEqual({ busy: true, list: null, error: null });
    for (const [orderId, path] of [
      [null, REFUNDS_PATH],
      ["42", "/admin/refunds/order/42"],
    ] as const) {
      const opening = renderToStaticMarkup(wrap(<AdminRefundsContent orderId={orderId} />, "en", path));
      const openingArea = tags(opening, "div").find((tag) => classes(tag).includes("site-admin-refunds__list")) ?? "";
      expect(attributes(openingArea).get("aria-busy")).toBe("true");
      expect(opening).toContain("<select");
      expect(opening).not.toContain("<table");
    }
    const busyTag = (state: ListState) => tags(render({ state }), "div").find((tag) => classes(tag).includes("site-admin-refunds__list")) ?? "";
    expect(attributes(busyTag(shown(list()))).get("aria-busy")).toBe("false");
    expect(attributes(busyTag({ busy: true, list: list(), error: null })).get("aria-busy")).toBe("true");
  });

  // SHOP-TASK-056 验收第 6 条「401 用路由的 replace 进入 /admin/login；网络中断显示 common.network_check，其他失败显示 common.error_retry」。
  it("chooses what to show from the reply", () => {
    expect(listStep({ kind: "ok", list: list() })).toEqual(shown(list()));
    expect(listStep({ kind: "none" })).toBe("login");
    expect(listStep({ kind: "network" })).toEqual({ busy: false, list: null, error: "common.network_check" });
    expect(listStep({ kind: "failed" })).toEqual({ busy: false, list: null, error: "common.error_retry" });
  });

  // 同一条：经接口查询一次（请求体为当前查询，地址带当前界面语言，带上中止用的 signal）；200 显示列表，401 以 replace 进入 /admin/login，
  // 网络中断与其他失败（含响应体不合格）显示相应提示。
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
    const query: RefundsQuery = { status: "approved", order_id: null, page: 2 };
    await loadRefunds(query, "ms", controller.signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/refunds/query?lang=ms");
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(JSON.parse(calls[0]?.init.body as string)).toEqual(query);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 同一条：提示在列表区（role="alert"），失败后不显示表格、卡片与翻页；状态下拉照常。
  it.each(LANGUAGES)("shows the failure messages in the list area in %s", (language) => {
    for (const key of ["common.network_check", "common.error_retry"] as const) {
      const html = render({ state: { busy: false, list: null, error: key } }, language);
      const area = element(html, "div", "site-admin-refunds__list");
      expect(area).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div>`);
      expect(area).not.toMatch(/<(table|ul|button)\b/);
      expect(html).toContain("<select");
    }
  });

  // SHOP-TASK-056 验收第 6 条「改变筛选或页码时中止旧请求，旧请求的结果不再更新页面」：旧查询的 signal 中止后，
  // 不论它回答什么（列表、401 或失败）都不显示也不跳转，只显示新查询的结果。
  it.each([json(200, list({ refunds: [REJECTED], total: 1 })), json(401, { detail: "admin_session_required" }), json(500, {})])(
    "ignores the reply to a replaced query (%#)",
    async (oldReply) => {
      const fresh = list({ refunds: [APPROVED], total: 1 });
      const calls = stubFetch(oldReply, json(200, fresh));
      const moves = fakeMoves();
      const old = new AbortController();
      const pendingOld = loadRefunds(firstQuery(null), "en", old.signal, moves.target);
      old.abort();
      const next = new AbortController();
      const pendingNext = loadRefunds(filterBy("approved", firstQuery(null)), "en", next.signal, moves.target);
      await Promise.all([pendingOld, pendingNext]);
      expect(calls).toHaveLength(2);
      expect(moves.events).toEqual(["show"]);
      expect(moves.states).toEqual([shown(fresh)]);
    },
  );

  // 同一条（派生实现约束：离开页面同样中止）：signal 中止之后不论结果如何都不再更新。
  it("does nothing after leaving the page", async () => {
    stubFetch(json(200, list()));
    const controller = new AbortController();
    const moves = fakeMoves();
    const pending = loadRefunds(firstQuery(null), "en", controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("list and detail", () => {
  // SHOP-TASK-057 验收第 2 条「列表的筛选（状态、订单内部 ID、页码）保存在页面模块的内存变量里，在列表与详情之间切换时保留」
  // （Kelvin 2026-10-07「列表的筛选与页码只在页面内存里，在列表与详情之间切换时保留」）：打开详情时沿用记下的查询（范围随它的订单筛选）；
  // 回到同一范围的列表路径时取回；换到别的范围（另一张订单、清除筛选）时从该路径的第 1 页、全部状态开始；不合法的订单 ID 不取回。
  it("keeps the filters between the list and the detail", () => {
    expect(rememberedListQuery()).toEqual(firstQuery(null));
    const everything: RefundsQuery = { status: "approved", order_id: null, page: 3 };
    rememberListQuery(everything);
    expect(openingOf(null, "9")).toEqual({ scope: "all", query: everything });
    expect(openingOf(null)).toEqual({ scope: "all", query: everything });
    expect(openingOf("42")).toEqual({ scope: "order", query: firstQuery(42) });
    expect(openingOf("x")).toEqual({ scope: "invalid", query: firstQuery(null) });

    const byOrder: RefundsQuery = { status: "requested", order_id: 42, page: 2 };
    rememberListQuery(byOrder);
    expect(openingOf(null, "12")).toEqual({ scope: "order", query: byOrder });
    expect(openingOf("42")).toEqual({ scope: "order", query: byOrder });
    expect(openingOf("43")).toEqual({ scope: "order", query: firstQuery(43) });
    expect(openingOf(null)).toEqual({ scope: "all", query: firstQuery(null) });
  });

  // 同一条「返回链接回到当时的列表网址（/admin/refunds 或 /admin/refunds/order/<内部 ID>），不进查询参数或浏览器存储」：
  // 列表网址只由订单筛选决定，状态与页码不进网址。
  it("returns to the list address the detail was opened from", () => {
    expect(listPathOf({ status: "approved", order_id: null, page: 3 })).toBe(REFUNDS_PATH);
    expect(listPathOf({ status: "requested", order_id: 42, page: 2 })).toBe("/admin/refunds/order/42");
    expect(listPathOf(firstQuery(null))).toBe(REFUNDS_PATH);
  });

  // SHOP-TASK-057 验收第 2 条「桌面在列表右侧显示详情…手机只显示详情与 common.back」：打开详情时列表与详情并排放在
  // site-admin-refunds-split 里（列表在前），详情区读取中标 aria-busy、只有返回链接，返回链接指向记下的列表网址；
  // 列表沿用记下的查询（状态下拉选中记下的状态，按订单筛选时有清除链接）；渲染过程不发请求（effect 不在服务端渲染执行）。
  it.each<[RefundsQuery, string]>([
    [{ status: "rejected", order_id: null, page: 2 }, REFUNDS_PATH],
    [{ status: null, order_id: 42, page: 1 }, "/admin/refunds/order/42"],
  ])("shows the list and the detail side by side (%#)", (query, back) => {
    const calls = stubFetch();
    rememberListQuery(query);
    const html = renderToStaticMarkup(wrap(<AdminRefundsContent refundId="9" />, "en", "/admin/refunds/9"));
    const split = element(html, "div", "site-admin-refunds-split");
    expect(split.indexOf("site-admin-refunds__list")).toBeLessThan(split.indexOf("site-admin-refund\""));
    const section = all(split, "section")[0] ?? "";
    expect(attributes(tags(section, "section")[0] ?? "").get("aria-busy")).toBe("true");
    const link = tags(section, "a")[0] ?? "";
    expect(attributes(link).get("href")).toBe(back);
    expect(textNodes(section)).toEqual([COPY["common.back"].en]);
    expect(tags(html, "option").filter((tag) => attributes(tag).has("selected")).map((tag) => attributes(tag).get("value"))).toEqual([query.status ?? ""]);
    expect(html.includes("site-admin-refunds__order")).toBe(query.order_id !== null);
    expect(calls).toEqual([]);
  });

  // 同一条「手机只显示详情」的另一面：只看列表（/admin/refunds 与按订单筛选）时没有详情区与并排容器。
  it("shows no detail on the list paths", () => {
    for (const [orderId, path] of [
      [null, REFUNDS_PATH],
      ["42", "/admin/refunds/order/42"],
    ] as const) {
      const html = renderToStaticMarkup(wrap(<AdminRefundsContent orderId={orderId} />, "en", path));
      expect(html).not.toContain("site-admin-refunds-split");
      expect(html).not.toContain("<section");
    }
  });

  // SHOP-TASK-057 验收第 2 条「加入后 /admin/refunds/order 匹配这个模式（段值为 order，页面按不合法 ID 处理）」与第 5 条
  // 「路径 ID 不合法时详情位置显示 common.error_retry 与 common.back…桌面左侧列表照常显示」：段值 order 的详情与列表一样并排挂载，
  // 列表沿用记下的查询；详情是否为合法 ID 由详情判断（见 AdminRefundDetail.test.tsx），内容区按申请 ID 换 key。
  it("treats /admin/refunds/order as a detail path", () => {
    expect(contentKey(null, "order")).toBe("refund:order");
    expect(contentKey(null, "9")).not.toBe(contentKey(null, "10"));
    expect(contentKey(null, "9")).not.toBe(contentKey("9"));
    const html = renderToStaticMarkup(wrap(<AdminRefundsContent refundId="order" />, "en", "/admin/refunds/order"));
    expect(html).toContain("site-admin-refunds-split");
    expect(html).toContain("<select");
  });

  // 验收第 2 条「…不进查询参数或浏览器存储」：记下查询、打开详情与返回链接的整个过程不写 localStorage、sessionStorage、cookie 或历史记录，
  // 页面上的链接没有查询串。
  it("keeps the remembered filters out of addresses and browser storage", () => {
    const storage = () => ({ getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() });
    const local = storage();
    const session = storage();
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", session);
    vi.stubGlobal("history", history);
    rememberListQuery({ status: "approved", order_id: 42, page: 4 });
    const html = renderToStaticMarkup(wrap(<AdminRefundsContent refundId="9" />, "en", "/admin/refunds/9"));
    for (const href of [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "")) {
      expect(href).not.toContain("?");
      expect(href).not.toContain("approved");
    }
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
  });
});

describe("navigation", () => {
  // SHOP-TASK-056 验收第 2 条「当前导航项为退款」「AdminFrame.tsx 的导航按 UX 顺序只含已上线的订单与退款两项（手机顶栏标题与 ☰ 菜单同样）」与
  // UX「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、…」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」：
  // 经路由打开两条退款路径，桌面导航与 ☰ 菜单都依次为订单与退款两项，退款标 aria-current="page"；手机顶栏名称为 admin.nav_refunds；
  // 没有其他导航项。
  it.each(LANGUAGES)("marks the refunds item as current in %s", (language) => {
    for (const path of [REFUNDS_PATH, "/admin/refunds/order/42"]) {
      const html = renderApp(path, language);
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => [attributes(tag).get("href"), attributes(tag).get("aria-current")])).toEqual([
          [ORDERS_PATH, undefined],
          [REFUNDS_PATH, "page"],
        ]);
        expect(all(nav, "a").filter((a) => !attributes(tags(a, "a")[0] ?? "").has("lang")).map((a) => textNodes(a).join(""))).toEqual([
          COPY["admin.nav_orders"][language],
          COPY["admin.nav_refunds"][language],
        ]);
      }
      expect(element(html, "span", "site-admin__bar-title")).toBe(
        `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_refunds"][language])}</span>`,
      );
    }
  });
});

describe("dictionary", () => {
  // SHOP-TASK-056 验收第 7 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：
  // 全部申请与按订单筛选（含不合法 ID）的列表、空列表、翻页、读取中与两种失败，每段文字（含 aria-label）都是当前语言的字典文案，
  // 或接口给的订单号与「名称 × 件数」、件数、按界面语言格式化的时间与 common.price_myr 的金额；此外只有视觉稿给定的符号 ·、‹、›。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const row of [...ROWS, ...ORDER_ROWS]) {
      allowed.add(row.order_number);
      allowed.add(formatDateTime(row.created_at, language));
      allowed.add(price(row.amount_sen, language));
      for (const line of row.lines) {
        allowed.add(line.name);
        allowed.add(String(line.quantity));
        allowed.add(`${line.name} × ${String(line.quantity)}`);
      }
    }
    for (const symbol of ["·", "‹", "›"]) {
      allowed.add(symbol);
    }
    const states: ListState[] = [
      INITIAL_LIST,
      shown(list()),
      shown(list({ total: 0, refunds: [] })),
      shown(list({ total: 45, page: 2 })),
      { busy: true, list: list(), error: null },
      { busy: false, list: null, error: "common.network_check" },
      { busy: false, list: null, error: "common.error_retry" },
    ];
    const pages = [
      renderToStaticMarkup(wrap(<AdminRefundsContent />, language)),
      renderToStaticMarkup(wrap(<AdminRefundsContent orderId="42" />, language, "/admin/refunds/order/42")),
      renderToStaticMarkup(wrap(<AdminRefundsContent orderId="x" />, language, "/admin/refunds/order/x")),
    ];
    for (const state of states) {
      pages.push(
        render({ state }, language),
        render({ state, status: "rejected" }, language),
        render({ scope: "order", state: state.list === null ? state : { ...state, list: { ...state.list, refunds: ORDER_ROWS } } }, language),
      );
    }
    for (const html of pages) {
      expect(html).not.toMatch(/\s(title|placeholder)=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
