import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ORDER_STATUSES } from "../api/adminOrders";
import type { AdminOrderDetail, AdvanceTarget, OrderStatus } from "../api/adminOrders";
import { formatSen } from "../format";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  AdminOrderDetailView,
  advanceOrder,
  canMark,
  detailStep,
  INITIAL_DETAIL,
  openDetail,
  startDeferred,
} from "./AdminOrderDetail";
import type { AdminOrderDetailViewProps, DetailState } from "./AdminOrderDetail";
import { countryName } from "./PayPage";
import { formatDateTime } from "./TrackOrderPage";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const DETAIL_PATH = "/admin/orders/42";
const ORDERS_PATH = "/admin/orders";
const LOGIN_PATH = "/admin/login";
const TOKEN = "csrf-token-for-admin";

const STATES_MAP: ReadonlyMap<string, string> = new Map([["MY-10", "Selangor"]]);

const ORDER: AdminOrderDetail = {
  id: 42,
  order_number: "K7Q2-9MXA",
  status: "demo_paid",
  created_at: "2026-09-30T06:02:00Z",
  paid_at: "2026-09-30T06:05:00Z",
  lines: [
    { name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 4000, line_subtotal_sen: 6737, unit_cash_paid_sen: [3368, 3369] },
    { name: "Soy Wax Candle", variant_label: "", quantity: 1, unit_price_sen: 3000, line_subtotal_sen: 2763, unit_cash_paid_sen: [2763] },
  ],
  subtotal_sen: 11000,
  coupon_discount_sen: 1000,
  points_discount_sen: 500,
  shipping_fee_sen: 800,
  total_sen: 10300,
  recipient: {
    name: "Aina Rahman",
    phone: "+60 12-345 6789",
    country_code: "MY",
    region: "MY-10",
    address: "12 Jalan Contoh 3, Taman Demo",
    postal_code: "47000",
  },
  events: [
    { created_at: "2026-09-30T06:02:00Z", status: "awaiting_demo_payment", actor_type: "guest" },
    { created_at: "2026-09-30T06:05:00Z", status: "demo_paid", actor_type: "member" },
    { created_at: "2026-10-01T01:30:00Z", status: "demo_packed", actor_type: "admin" },
    { created_at: "2026-10-08T01:30:00Z", status: "demo_completed", actor_type: "system" },
  ],
  refunds_pending: 0,
  fully_refunded: false,
  csrf_token: TOKEN,
};

const PACKED: AdminOrderDetail = { ...ORDER, status: "demo_packed" };

const STATUS_KEY: Readonly<Record<OrderStatus, CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
  demo_cancelled: "order.status_cancelled",
};

function shown(order: AdminOrderDetail, notice: CopyKey | null = null, busy = false): DetailState {
  return { status: "ready", order, busy, notice };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function wrap(element: ReactNode, language: Language) {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath={DETAIL_PATH}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AdminOrderDetailViewProps> = {}, language: Language = "en"): string {
  const props: AdminOrderDetailViewProps = { state: shown(ORDER), states: STATES_MAP, onMark: noop, ...overrides };
  return renderToStaticMarkup(wrap(<AdminOrderDetailView {...props} />, language));
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

// 带某个 class 的全部元素（按出现顺序；含嵌套的同名元素时各自配对）。
function elements(html: string, name: string, className: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].filter((m) => classes(m[0]).includes(className)).map((m) => elementAt(html, name, m.index));
}

function element(html: string, name: string, className: string): string {
  const [found] = elements(html, name, className);
  if (found === undefined) {
    throw new Error(`not found: <${name} class="${className}">`);
  }
  return found;
}

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

function requests(calls: Call[]): string[] {
  return calls.map((call) => `${String(call.init.method)} ${call.url}`);
}

// 路由替身：记录详情区显示的状态与经 replace 去的地址。
function fakeMoves() {
  const events: string[] = [];
  const states: DetailState[] = [];
  const target = {
    show: (state: DetailState) => {
      states.push(state);
      events.push("show");
    },
    replace: (path: RoutePath) => {
      events.push(`replace ${path}`);
    },
  };
  return { target, events, states };
}

function sectionLabels(block: string): string[] {
  return elements(block, "div", "site-admin-order__row").map((row) => textNodes(row).join(" "));
}

describe("order detail", () => {
  // UX A02 线框「[admin.order_detail] [K]<订单号>」与验收第 4 条「标题 admin.order_detail 加订单号与状态标签」（视觉稿 A02-desktop-frozen）：
  // h2 为 admin.order_detail · 订单号（· 只是视觉，不读出），其后为状态标签。
  it.each(LANGUAGES)("shows the title, the order number and the status in %s", (language) => {
    const head = element(render({}, language), "div", "site-admin-order__head");
    const h2 = all(head, "h2")[0] ?? "";
    expect(textNodes(h2)).toEqual([COPY["admin.order_detail"][language], "·", ORDER.order_number]);
    expect(h2).toContain(`<span aria-hidden="true"> · </span>`);
    const tag = tags(head, "span").find((span) => classes(span).includes("acs-tag")) ?? "";
    expect(classes(tag)).toEqual(["acs-tag", "acs-tag--neutral"]);
    expect(head.indexOf(tag)).toBeGreaterThan(head.indexOf("</h2>"));
    expect(textNodes(head).at(-1)).toBe(COPY["order.status_paid"][language]);
  });

  // UX A02「[order.items] <名称>/<规格> x2 [M2]」与验收第 4 条「order.items 每行名称、规格、件数与行小计」：
  // 区块标题 order.items，每行为 名称 / 规格 × 件数 与行小计（common.price_myr 与 formatSen）；没有规格的行不显示 /。
  it.each(LANGUAGES)("lists the items with their line subtotals in %s", (language) => {
    const items = element(render({}, language), "div", "site-admin-order__items");
    expect(textNodes(items)[0]).toBe(COPY["order.items"][language]);
    expect(sectionLabels(items)).toEqual([
      `Crew Neck Tee / Black, M × 2 ${price(6737, language)}`,
      `Soy Wax Candle × 1 ${price(2763, language)}`,
    ]);
    expect(price(6737, "en")).toBe("RM 67.37");
  });

  // 验收第 4 条「order.amount_breakdown 依次为 cart.subtotal、checkout.summary_coupon、checkout.summary_points、checkout.summary_shipping 与
  // checkout.summary_total（金额用 common.price_myr 与 formatSen）」与 UX 0.5「后台 A02 不变」（游客订单也照常显示券与积分两行）：
  // 桌面区块与手机折叠（▸ [order.amount_breakdown] [M1]，标题行右端为合计）都是这五行、同一顺序。
  it.each(LANGUAGES)("shows the amount details in order in %s", (language) => {
    const html = render({}, language);
    const expected = (
      [
        ["cart.subtotal", 11000],
        ["checkout.summary_coupon", 1000],
        ["checkout.summary_points", 500],
        ["checkout.summary_shipping", 800],
        ["checkout.summary_total", 10300],
      ] as const
    ).map(([key, sen]) => `${COPY[key][language]} ${price(sen, language)}`);
    const desktop = elements(html, "div", "site-admin-order__desktop")[0] ?? "";
    expect(textNodes(desktop)[0]).toBe(COPY["order.amount_breakdown"][language]);
    expect(sectionLabels(desktop)).toEqual(expected);
    const fold = all(html, "details")[0] ?? "";
    expect(textNodes(all(fold, "summary")[0] ?? "")).toEqual([COPY["order.amount_breakdown"][language], price(10300, language)]);
    expect(sectionLabels(fold)).toEqual(expected);
    expect(attributes(tags(fold, "details")[0] ?? "").has("open")).toBe(false);
  });

  // UX A02「[admin.recipient_raw] [P1] <姓名>/<电话>/<地址>/<邮编>」「[admin.recipient_audited]」与验收第 4 条「admin.recipient_raw 显示收货资料原文
  // （姓名、电话、地址、邮编、州属或地区与国家，沿用 PayPage.tsx 的 recipientParts 与 useMyStates，与前台 P09 相同），其下 admin.recipient_audited」：
  // 桌面区块与手机折叠（默认展开，照 A02-phone-detail）都是 姓名 · 电话，地址, 邮编, 州属名称, 国家，其下审计提示。
  it.each(LANGUAGES)("shows the original shipping details and the audit note in %s", (language) => {
    const html = render({}, language);
    const place = `${ORDER.recipient?.address ?? ""}, 47000, Selangor, ${countryName("MY", language)}`;
    const expected = [ORDER.recipient?.name, "·", ORDER.recipient?.phone, place, COPY["admin.recipient_audited"][language]];
    const desktop = elements(html, "div", "site-admin-order__desktop")[1] ?? "";
    expect(textNodes(desktop)).toEqual([COPY["admin.recipient_raw"][language], ...expected]);
    const fold = all(html, "details")[1] ?? "";
    expect(attributes(tags(fold, "details")[0] ?? "").has("open")).toBe(true);
    expect(textNodes(fold)).toEqual([COPY["admin.recipient_raw"][language], ...expected]);
    // 州属名称表还没取到时显示州属代码（与前台相同）。
    expect(textNodes(elements(render({ states: null }, language), "div", "site-admin-order__desktop")[1] ?? "")).toContain(
      `${ORDER.recipient?.address ?? ""}, 47000, MY-10, ${countryName("MY", language)}`,
    );
  });

  // 验收第 4 条「recipient 为 null 时不显示该区块」：没有收货资料记录时，桌面与手机都没有 admin.recipient_raw 与 admin.recipient_audited。
  it.each(LANGUAGES)("hides the shipping block when there is no recipient in %s", (language) => {
    const html = render({ state: shown({ ...ORDER, recipient: null }) }, language);
    expect(html).not.toContain(escapeHtml(COPY["admin.recipient_raw"][language]));
    expect(html).not.toContain(escapeHtml(COPY["admin.recipient_audited"][language]));
    expect(elements(html, "div", "site-admin-order__desktop")).toHaveLength(2);
    expect(all(html, "details")).toHaveLength(2);
  });

  // 验收第 4 条「admin.event_log 表格列为 admin.col_time（formatDateTime）、admin.col_event（变更后状态的 order.status_*）与 admin.col_actor
  // （guest 与 member 显示 admin.actor_customer，admin 显示 admin.actor_admin，system 显示 admin.actor_system）」与 UX A02 手机「▸ [admin.event_log]
  // <时间> [order.status_*] [admin.actor_*]」（A02-phone-detail：状态 · 操作者，其下为时间）。
  it.each(LANGUAGES)("shows the event log in %s", (language) => {
    const html = render({}, language);
    const actors: CopyKey[] = ["admin.actor_customer", "admin.actor_customer", "admin.actor_admin", "admin.actor_system"];
    const desktop = elements(html, "div", "site-admin-order__desktop")[2] ?? "";
    expect(textNodes(desktop)[0]).toBe(COPY["admin.event_log"][language]);
    expect(all(desktop, "th").map((th) => textNodes(th).join(""))).toEqual(
      (["admin.col_time", "admin.col_event", "admin.col_actor"] as const).map((key) => COPY[key][language]),
    );
    const rows = all(all(desktop, "tbody")[0] ?? "", "tr").map((row) => all(row, "td").map((td) => textNodes(td).join("")));
    expect(rows).toEqual(
      ORDER.events.map((event, index) => [formatDateTime(event.created_at, language), COPY[STATUS_KEY[event.status]][language], COPY[actors[index] ?? "admin.actor_system"][language]]),
    );
    const fold = all(html, "details")[2] ?? "";
    expect(textNodes(all(fold, "summary")[0] ?? "")).toEqual([COPY["admin.event_log"][language]]);
    expect(all(fold, "li").map(textNodes)).toEqual(
      ORDER.events.map((event, index) => [
        COPY[STATUS_KEY[event.status]][language],
        "·",
        COPY[actors[index] ?? "admin.actor_system"][language],
        formatDateTime(event.created_at, language),
      ]),
    );
  });

  // 验收第 4 条「手机按 A02-phone-detail 用可折叠区块」与第 1 条「由 site.css 按宽度显隐」：金额明细、收货资料与事件记录在手机为 details 折叠区块
  // （acs-admin__panel），桌面区块另有自己的 class；商品与推进按钮两边共用，不折叠。
  it("renders the desktop blocks and the phone folds", () => {
    const html = render();
    expect(elements(html, "div", "site-admin-order__desktop")).toHaveLength(3);
    const folds = tags(html, "details");
    expect(folds).toHaveLength(3);
    for (const fold of folds) {
      expect(classes(fold)).toEqual(["acs-admin__panel", "site-admin-order__fold"]);
    }
    expect(all(html, "details").join("")).not.toContain("<button");
    expect(classes(tags(html, "div").find((tag) => classes(tag).includes("site-admin-order__panel")) ?? "")).toContain("acs-admin__panel");
  });

  // 验收第 4 条「打开详情后切换界面语言不重新读取…商品名称等接口文字保持打开时的语言，界面文字随语言切换」：同一份详情在另一种界面语言下
  // 渲染时，商品名称与规格保持接口给的原文，标题、区块标题与按钮换成该语言的字典文案。
  it("keeps the API text and switches the interface text", () => {
    const html = render({}, "zh");
    expect(html).toContain("Crew Neck Tee");
    expect(html).toContain("Black, M");
    expect(html).toContain(COPY["admin.order_detail"].zh);
    expect(html).toContain(COPY["admin.mark_packed"].zh);
    expect(html).not.toContain(COPY["admin.mark_packed"].en);
  });
});

describe("advancing buttons", () => {
  // 验收第 5 条「admin.mark_packed 只在 demo_paid 时可点，admin.mark_shipped 只在 demo_packed 时可点，其余状态都禁用」与
  // DESIGN 1.11「订单与退款状态」（管理员依次推进 demo_paid → demo_packed → demo_shipped）。
  it.each(ORDER_STATUSES)("enables only the next step for %s", (status) => {
    const order = { ...ORDER, status };
    expect(canMark(order, "demo_packed")).toBe(status === "demo_paid");
    expect(canMark(order, "demo_shipped")).toBe(status === "demo_packed");
    const buttons = tags(element(render({ state: shown(order) }), "div", "site-admin-order__buttons"), "button").map(attributes);
    expect(buttons.map((button) => button.has("disabled"))).toEqual([status !== "demo_paid", status !== "demo_packed"]);
    // 视觉稿 A02-desktop：可点的为主按钮，禁用的为次要按钮。
    expect(buttons.map((button) => button.get("class"))).toEqual([
      status === "demo_paid" ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary",
      status === "demo_packed" ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary",
    ]);
    expect(buttons.map((button) => button.get("type"))).toEqual(["button", "button"]);
  });

  // UX「状态与补充（0.9）」「全部已退（履约冻结）时 [admin.mark_packed] 与 [admin.mark_shipped] 都禁用，其下显示 [admin.frozen]，[admin.ship_hint] 照常显示」
  // 与验收第 5 条「fully_refunded 为真时两者都禁用并显示 admin.frozen；admin.ship_hint 始终显示」（视觉稿 A02-desktop-frozen：两个次要按钮）。
  // 按钮之下的顺序依 UX A02 线框「( [admin.mark_packed] ) ( [admin.mark_shipped] ) / [admin.ship_hint] / 全部已退：[admin.frozen]」
  // （视觉稿把冻结提示画在 ◆ 提示之前，二者冲突时以 UX 文档为准）。
  it.each(LANGUAGES)("freezes both buttons when everything is refunded in %s", (language) => {
    for (const status of ORDER_STATUSES) {
      const order = { ...ORDER, status, fully_refunded: true };
      expect(canMark(order, "demo_packed")).toBe(false);
      expect(canMark(order, "demo_shipped")).toBe(false);
      const actions = element(render({ state: shown(order) }, language), "div", "site-admin-order__actions");
      const buttons = tags(actions, "button").map(attributes);
      expect(buttons.map((button) => [button.has("disabled"), button.get("class")])).toEqual([
        [true, "acs-admin__btn acs-admin__btn--secondary"],
        [true, "acs-admin__btn acs-admin__btn--secondary"],
      ]);
      expect(textNodes(actions)).toEqual([
        COPY["admin.mark_packed"][language],
        COPY["admin.mark_shipped"][language],
        COPY["admin.ship_hint"][language],
        COPY["admin.frozen"][language],
      ]);
      expect(actions).toContain(`<div class="site-admin-order__frozen" role="status">${escapeHtml(COPY["admin.frozen"][language])}</div>`);
    }
  });

  // 同一条「admin.ship_hint 始终显示」与 UX A02「发货按钮旁 [admin.ship_hint]」：不是全部已退时没有 admin.frozen，admin.ship_hint 照常在按钮之后。
  it.each(LANGUAGES)("always shows the shipping hint and shows the frozen note only when frozen in %s", (language) => {
    for (const status of ORDER_STATUSES) {
      const actions = element(render({ state: shown({ ...ORDER, status }) }, language), "div", "site-admin-order__actions");
      expect(textNodes(actions)).toEqual([COPY["admin.mark_packed"][language], COPY["admin.mark_shipped"][language], COPY["admin.ship_hint"][language]]);
      expect(actions).not.toContain("role=\"status\"");
    }
  });

  // 验收第 5 条「进行中按钮禁用」：推进请求进行中两个按钮都禁用。
  it("disables both buttons while advancing", () => {
    for (const order of [ORDER, PACKED]) {
      const buttons = tags(element(render({ state: shown(order, null, true) }), "div", "site-admin-order__buttons"), "button").map(attributes);
      expect(buttons.map((button) => button.has("disabled"))).toEqual([true, true]);
    }
  });

  // 验收第 5 条「409 与 403 重新取详情并显示 common.error_retry；网络中断显示 common.network_check」：提示在按钮之后（role="alert"）。
  it.each(LANGUAGES)("shows the advancing notice after the buttons in %s", (language) => {
    for (const key of ["common.error_retry", "common.network_check"] as const) {
      const actions = element(render({ state: shown(ORDER, key) }, language), "div", "site-admin-order__actions");
      const alert = `<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div>`;
      expect(actions).toContain(alert);
      expect(actions.indexOf(alert)).toBeGreaterThan(actions.indexOf("site-admin-order__buttons"));
    }
  });
});

describe("missing order and reading failures", () => {
  // Kelvin 2026-10-07 决定「网址里的内部 ID 不存在或不合法时，显示现有的 common.error_retry 与 common.back，不新增「找不到」文案」与
  // 验收第 6 条「订单不存在（404）或路径 ID 不合法时详情位置显示 common.error_retry 与 common.back（返回 /admin/orders），不显示详情」：
  // 详情区只有返回链接与提示，没有标题、按钮或折叠区块。
  it.each(LANGUAGES)("shows only the error and the way back for a missing order in %s", (language) => {
    const html = render({ state: { status: "missing" } }, language);
    const back = elements(html, "a", "site-admin-order__back")[0] ?? "";
    expect(attributes(tags(back, "a")[0] ?? "").get("href")).toBe(ORDERS_PATH);
    expect(textNodes(back)).toEqual([COPY["common.back"][language]]);
    expect(html).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY["common.error_retry"][language])}</span></div>`);
    expect(textNodes(html)).toEqual([COPY["common.back"][language], COPY["common.error_retry"][language]]);
    expect(html).not.toMatch(/<(h2|button|details|table)\b/);
  });

  // 验收第 6 条「详情读取失败或网络中断时显示 common.error_retry 或 common.network_check」；读取中详情区标 aria-busy，只有返回链接。
  it.each(LANGUAGES)("shows the reading failures and the loading state in %s", (language) => {
    for (const key of ["common.error_retry", "common.network_check"] as const) {
      const html = render({ state: { status: "failed", error: key } }, language);
      expect(textNodes(html)).toEqual([COPY["common.back"][language], COPY[key][language]]);
      expect(html).toContain(`role="alert"`);
    }
    const loading = render({ state: INITIAL_DETAIL }, language);
    expect(INITIAL_DETAIL).toEqual({ status: "loading" });
    expect(attributes(tags(loading, "section")[0] ?? "").get("aria-busy")).toBe("true");
    expect(textNodes(loading)).toEqual([COPY["common.back"][language]]);
    expect(attributes(tags(render({}, language), "section")[0] ?? "").get("aria-busy")).toBe("false");
  });

  // 验收第 2 条「手机只显示详情与 common.back（返回 /admin/orders）」：返回在详情顶部（详情各状态都有），指向列表。
  it("puts the way back at the top of the detail", () => {
    for (const state of [INITIAL_DETAIL, shown(ORDER), { status: "missing" } as const]) {
      const html = render({ state });
      const section = all(html, "section")[0] ?? "";
      expect(section.indexOf("site-admin-order__back")).toBeLessThan(section.indexOf("</a>"));
      expect(tags(section, "a")[0]).toContain(`href="${ORDERS_PATH}"`);
      expect(textNodes(section)[0]).toBe(COPY["common.back"].en);
    }
  });
});

describe("opening the detail", () => {
  // 验收第 6 条「订单不存在（404）…」「详情读取失败或网络中断…」与第 5 条「401（详情或推进）用路由的 replace 进入 /admin/login」。
  it("chooses what to show from the reply", () => {
    expect(detailStep({ kind: "ok", order: ORDER })).toEqual(shown(ORDER));
    expect(detailStep({ kind: "none" })).toBe("login");
    expect(detailStep({ kind: "missing" })).toEqual({ status: "missing" });
    expect(detailStep({ kind: "network" })).toEqual({ status: "failed", error: "common.network_check" });
    expect(detailStep({ kind: "failed" })).toEqual({ status: "failed", error: "common.error_retry" });
  });

  // 同一组：经接口读取一次（地址为内部 ID 与当前界面语言，带上中止用的 signal）；200 显示详情，401 以 replace 进入 /admin/login，
  // 404 为不存在，网络中断与其他失败显示相应提示。
  it.each<[Response | (() => never), string[], DetailState | null]>([
    [json(200, ORDER), ["show"], shown(ORDER)],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [json(404, { detail: "not_found" }), ["show"], { status: "missing" }],
    [networkDown, ["show"], { status: "failed", error: "common.network_check" }],
    [json(500, {}), ["show"], { status: "failed", error: "common.error_retry" }],
  ])("opens the detail from the reply (%#)", async (reply, events, state) => {
    const calls = stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    await openDetail("42", "ms", controller.signal, moves.target);
    expect(requests(calls)).toEqual(["GET /api/admin/orders/42?lang=ms"]);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 验收第 6 条「路径 ID 不合法时详情位置显示 common.error_retry 与 common.back」与第 3 条「…时不发请求」。
  it.each(["0", "007", "2147483648", "abc"])("shows a missing order without a request for %j", async (id) => {
    const calls = stubFetch(json(200, ORDER));
    const moves = fakeMoves();
    await openDetail(id, "en", new AbortController().signal, moves.target);
    expect(calls).toEqual([]);
    expect(moves.states).toEqual([{ status: "missing" }]);
  });

  // 验收第 6 条「离开页面或换到另一张订单时中止旧请求，旧请求的结果不再更新页面」：旧订单的 signal 中止后，
  // 不论它回答什么（详情、401 或失败）都不显示也不跳转，只显示新订单的详情。
  it.each([json(200, { ...ORDER, id: 41, order_number: "B6TN-2RJD" }), json(401, { detail: "admin_session_required" }), json(404, {})])(
    "ignores the reply for the previous order (%#)",
    async (oldReply) => {
      const fresh = { ...ORDER, id: 43 };
      const calls = stubFetch(oldReply, json(200, fresh));
      const moves = fakeMoves();
      const old = new AbortController();
      const pendingOld = openDetail("41", "en", old.signal, moves.target);
      old.abort();
      const next = new AbortController();
      const pendingNext = openDetail("43", "en", next.signal, moves.target);
      await Promise.all([pendingOld, pendingNext]);
      expect(requests(calls)).toEqual(["GET /api/admin/orders/41?lang=en", "GET /api/admin/orders/43?lang=en"]);
      expect(moves.events).toEqual(["show"]);
      expect(moves.states).toEqual([shown(fresh)]);
    },
  );

  // 验收第 4 条「每次打开详情只请求一次（服务端每次查看都写审计），main.tsx 的 StrictMode 在开发模式下让 effect 执行两次时也只发出一次 GET
  // （例如推迟到下一轮事件循环再发、清理时取消），离开页面或换订单时照样中止」：
  // 照 StrictMode 的「执行—清理—再执行」调用两次，只有第二次在下一轮执行；清理时中止它的 signal；
  // 下一轮之前就离开（清理）则一次也不执行。
  it("sends one request when StrictMode runs the effect twice", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const run = vi.fn();
    const first = new AbortController();
    const cleanFirst = startDeferred(first, run);
    cleanFirst();
    expect(first.signal.aborted).toBe(true);
    const second = new AbortController();
    const cleanSecond = startDeferred(second, run);
    expect(run).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(run).toHaveBeenCalledTimes(1);
    cleanSecond();
    expect(second.signal.aborted).toBe(true);

    const left = vi.fn();
    const leaving = new AbortController();
    startDeferred(leaving, left)();
    vi.runAllTimers();
    expect(left).not.toHaveBeenCalled();
    expect(leaving.signal.aborted).toBe(true);
  });

  // 同一条：经 startDeferred 接上 openDetail 时，StrictMode 的两次执行合起来只发出一次 GET。
  it("reads the detail once through the deferred start", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const calls = stubFetch(json(200, ORDER));
    const moves = fakeMoves();
    const pending: Promise<void>[] = [];
    for (let round = 0; round < 2; round += 1) {
      const controller = new AbortController();
      const clean = startDeferred(controller, () => {
        pending.push(openDetail("42", "en", controller.signal, moves.target));
      });
      if (round === 0) {
        clean();
      }
    }
    vi.runAllTimers();
    vi.useRealTimers();
    await Promise.all(pending);
    expect(requests(calls)).toEqual(["GET /api/admin/orders/42?lang=en"]);
    expect(moves.states).toEqual([shown(ORDER)]);
  });
});

describe("advancing the order", () => {
  // 验收第 5 条「200 后重新取详情」：先 POST 推进（请求体只有目标状态、请求头带详情给的令牌），再 GET 详情并显示新的详情，没有提示。
  it.each<[AdminOrderDetail, AdvanceTarget, AdminOrderDetail]>([
    [ORDER, "demo_packed", PACKED],
    [PACKED, "demo_shipped", { ...ORDER, status: "demo_shipped" }],
  ])("reads the detail again after 200 (%#)", async (order, target, after) => {
    const calls = stubFetch(json(200, { status: target }), json(200, after));
    const moves = fakeMoves();
    const controller = new AbortController();
    await advanceOrder("42", order, target, "zh", controller.signal, moves.target);
    expect(requests(calls)).toEqual(["POST /api/admin/orders/42/status", "GET /api/admin/orders/42?lang=zh"]);
    const sent: unknown = JSON.parse(calls[0]?.init.body as string);
    expect(sent).toEqual({ status: target });
    expect((calls[0]?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(TOKEN);
    expect(calls[1]?.init.signal).toBe(controller.signal);
    expect(moves.states).toEqual([shown(after)]);
  });

  // 验收第 5 条「409（order_not_advanceable 或 fulfilment_frozen）与 403 重新取详情并显示 common.error_retry」。
  it.each([
    json(409, { detail: "order_not_advanceable", status: "demo_packed" }),
    json(409, { detail: "fulfilment_frozen", status: "demo_paid" }),
    json(403, { detail: "csrf_failed" }),
  ])("reads the detail again and asks to retry after a refusal (%#)", async (reply) => {
    const after = { ...ORDER, fully_refunded: true, csrf_token: "fresh-token" };
    const calls = stubFetch(reply, json(200, after));
    const moves = fakeMoves();
    await advanceOrder("42", ORDER, "demo_packed", "en", new AbortController().signal, moves.target);
    expect(requests(calls)).toEqual(["POST /api/admin/orders/42/status", "GET /api/admin/orders/42?lang=en"]);
    expect(moves.states).toEqual([shown(after, "common.error_retry")]);
  });

  // 验收第 5 条「401（详情或推进）用路由的 replace 进入 /admin/login」：推进 401，或推进 200 后重新读取 401，都以 replace 进入 A01。
  it("goes to /admin/login after 401", async () => {
    const first = fakeMoves();
    const calls = stubFetch(json(401, { detail: "admin_session_required" }));
    await advanceOrder("42", ORDER, "demo_packed", "en", new AbortController().signal, first.target);
    expect(requests(calls)).toEqual(["POST /api/admin/orders/42/status"]);
    expect(first.events).toEqual([`replace ${LOGIN_PATH}`]);

    const second = fakeMoves();
    stubFetch(json(200, { status: "demo_packed" }), json(401, { detail: "admin_session_required" }));
    await advanceOrder("42", ORDER, "demo_packed", "en", new AbortController().signal, second.target);
    expect(second.events).toEqual([`replace ${LOGIN_PATH}`]);
  });

  // 验收第 5 条「网络中断显示 common.network_check（再点即重发，已是目标状态时服务端回答 200，重复安全）；其他失败 common.error_retry」：
  // 不重新读取，保留当前详情，按钮按原状态仍可点（再点即重发同一请求）。
  it.each<[Response | (() => never), CopyKey]>([
    [networkDown, "common.network_check"],
    [json(500, {}), "common.error_retry"],
    [json(422, { detail: [] }), "common.error_retry"],
    [json(409, { detail: "something_else" }), "common.error_retry"],
  ])("keeps the detail and shows a notice (%#)", async (reply, notice) => {
    const calls = stubFetch(reply);
    const moves = fakeMoves();
    await advanceOrder("42", ORDER, "demo_packed", "en", new AbortController().signal, moves.target);
    expect(requests(calls)).toEqual(["POST /api/admin/orders/42/status"]);
    expect(moves.states).toEqual([shown(ORDER, notice)]);
    expect(canMark(ORDER, "demo_packed")).toBe(true);
    const buttons = tags(element(render({ state: shown(ORDER, notice) }), "div", "site-admin-order__buttons"), "button").map(attributes);
    expect(buttons[0]?.has("disabled")).toBe(false);
  });

  // 同一条「再点即重发…重复安全」：网络中断后再点，发出同样的请求（同一目标状态与令牌）；已是目标状态时服务端回答 200，随后重新读取。
  it("sends the same request again after a network failure", async () => {
    const calls = stubFetch(networkDown, json(200, { status: "demo_packed" }), json(200, PACKED));
    const moves = fakeMoves();
    await advanceOrder("42", ORDER, "demo_packed", "en", new AbortController().signal, moves.target);
    await advanceOrder("42", ORDER, "demo_packed", "en", new AbortController().signal, moves.target);
    expect(requests(calls)).toEqual(["POST /api/admin/orders/42/status", "POST /api/admin/orders/42/status", "GET /api/admin/orders/42?lang=en"]);
    expect(calls[0]?.init.body).toBe(calls[1]?.init.body);
    expect(moves.states).toEqual([shown(ORDER, "common.network_check"), shown(PACKED)]);
  });

  // 推进 200 后重新读取失败时（派生实现约束）：保留原来的详情并显示 common.network_check 或 common.error_retry；订单已不存在时为不存在。
  it.each<[Response | (() => never), DetailState]>([
    [networkDown, shown(ORDER, "common.network_check")],
    [json(500, {}), shown(ORDER, "common.error_retry")],
    [json(404, { detail: "not_found" }), { status: "missing" }],
  ])("handles a failed re-read (%#)", async (reread, state) => {
    stubFetch(json(200, { status: "demo_packed" }), reread);
    const moves = fakeMoves();
    await advanceOrder("42", ORDER, "demo_packed", "en", new AbortController().signal, moves.target);
    expect(moves.states).toEqual([state]);
  });

  // 验收第 6 条「离开页面或换到另一张订单时中止旧请求，旧请求的结果不再更新页面」：推进进行中离开页面，之后不显示也不跳转。
  it("does nothing after leaving the page", async () => {
    stubFetch(json(200, { status: "demo_packed" }), json(200, PACKED));
    const moves = fakeMoves();
    const controller = new AbortController();
    const pending = advanceOrder("42", ORDER, "demo_packed", "en", controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("personal data", () => {
  // 验收第 3 条「订单号、收货资料与令牌只在页面内存与请求里，不进网址、浏览器存储或日志」与 DESIGN 1.11「权限与资料保护」：
  // 打开与推进的全过程中请求地址不含订单号、收货资料或令牌，不写 localStorage、sessionStorage、cookie、历史记录或控制台。
  it("keeps the order number, the recipient and the token out of addresses, storage and logs", async () => {
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
    const logs = (["log", "info", "warn", "error", "debug"] as const).map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));

    const calls = stubFetch(json(200, ORDER), json(409, { detail: "order_not_advanceable" }), json(200, PACKED), json(200, { status: "demo_shipped" }), json(200, PACKED));
    const moves = fakeMoves();
    const signal = new AbortController().signal;
    await openDetail("42", "en", signal, moves.target);
    await advanceOrder("42", ORDER, "demo_packed", "en", signal, moves.target);
    await advanceOrder("42", PACKED, "demo_shipped", "en", signal, moves.target);
    expect(calls).toHaveLength(5);
    const secrets = [ORDER.order_number, TOKEN, ORDER.recipient?.name ?? "", ORDER.recipient?.phone ?? "", ORDER.recipient?.address ?? "", ORDER.recipient?.postal_code ?? ""];
    for (const call of calls) {
      for (const secret of secrets) {
        expect(call.url).not.toContain(secret);
        expect(call.url).not.toContain(encodeURIComponent(secret));
        expect((call.init.body as string | undefined) ?? "").not.toContain(secret);
      }
    }
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    for (const log of logs) {
      expect(log).not.toHaveBeenCalled();
    }
  });

  // 同一条：页面标记里没有令牌，链接只指向列表（不含订单号或内部 ID 以外的内容）。
  it("does not put the token or the order number into the markup's links", () => {
    const html = render();
    expect(html).not.toContain(TOKEN);
    for (const tag of tags(html, "a")) {
      expect(attributes(tag).get("href")).toBe(ORDERS_PATH);
    }
  });
});

describe("dictionary", () => {
  // 验收第 7 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：详情、冻结、提示、推进中、
  // 不存在、读取失败与读取中，每段文字（含 aria-label）都是当前语言的字典文案，或接口给的订单号、商品名称与规格、收货资料、
  // 按界面语言格式化的时间与 common.price_myr 的金额；此外只有视觉稿给定的符号 ·、/ 与「× 件数」。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    allowed.add(ORDER.order_number);
    for (const line of ORDER.lines) {
      allowed.add(line.name);
      allowed.add(line.variant_label);
      allowed.add(`× ${String(line.quantity)}`);
      allowed.add(price(line.line_subtotal_sen, language));
    }
    for (const sen of [ORDER.subtotal_sen, ORDER.coupon_discount_sen, ORDER.points_discount_sen, ORDER.shipping_fee_sen, ORDER.total_sen]) {
      allowed.add(price(sen, language));
    }
    for (const event of ORDER.events) {
      allowed.add(formatDateTime(event.created_at, language));
    }
    allowed.add(ORDER.recipient?.name ?? "");
    allowed.add(ORDER.recipient?.phone ?? "");
    allowed.add(`${ORDER.recipient?.address ?? ""}, 47000, Selangor, ${countryName("MY", language)}`);
    for (const symbol of ["·", "/"]) {
      allowed.add(symbol);
    }
    const states: DetailState[] = [
      INITIAL_DETAIL,
      shown(ORDER),
      shown(PACKED, "common.error_retry"),
      shown({ ...ORDER, fully_refunded: true }, "common.network_check"),
      shown({ ...ORDER, recipient: null }, null, true),
      { status: "missing" },
      { status: "failed", error: "common.error_retry" },
      { status: "failed", error: "common.network_check" },
    ];
    for (const state of states) {
      const html = render({ state }, language);
      expect(html).not.toMatch(/\stitle=/i);
      expect(html).not.toMatch(/\sstyle=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});
