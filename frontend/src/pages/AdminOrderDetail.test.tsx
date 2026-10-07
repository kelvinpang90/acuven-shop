import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ADVANCE_TARGETS, ORDER_STATUSES } from "../api/adminOrders";
import type { AdminOrderDetail, OrderStatus } from "../api/adminOrders";
import { formatSen } from "../format";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import { advanceOrder, AdminOrderDetailView, canAdvance, INITIAL_DETAIL, openDetail, readStep } from "./AdminOrderDetail";
import type { AdminOrderDetailViewProps, DetailScreen, DetailState } from "./AdminOrderDetail";
import { countryName } from "./PayPage";
import { formatDateTime } from "./TrackOrderPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDERS_PATH = "/admin/orders";
const LOGIN_PATH = "/admin/login";
const ORDER_ID = "42";
const TOKEN = "csrf-token-for-cookie";
const STATES: ReadonlyMap<string, string> = new Map([["MY-10", "Selangor"]]);

const ORDER: AdminOrderDetail = {
  id: 42,
  order_number: "K7Q2-9MXA",
  status: "demo_paid",
  created_at: "2026-09-30T06:02:00Z",
  paid_at: "2026-09-30T06:05:00Z",
  lines: [
    { name: "Crew Neck Tee", variant_label: "Black, M", quantity: 2, unit_price_sen: 4000, line_subtotal_sen: 6737, unit_cash_paid_sen: [3369, 3368] },
    { name: "Soy Wax Candle", variant_label: "", quantity: 1, unit_price_sen: 3000, line_subtotal_sen: 2763, unit_cash_paid_sen: [2763] },
  ],
  subtotal_sen: 11000,
  coupon_discount_sen: 1000,
  points_discount_sen: 500,
  shipping_fee_sen: 800,
  total_sen: 10300,
  recipient: {
    name: "Aina Rahman",
    phone: "+60123456789",
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
};

// 状态与它的名称（UX A02 [order.status_*]），按 ORDER_STATUSES 的顺序。
const STATUS_KEY: Readonly<Record<OrderStatus, CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
  demo_cancelled: "order.status_cancelled",
};

function ready(order: AdminOrderDetail = ORDER): Extract<DetailScreen, { status: "ready" }> {
  return { status: "ready", order, csrfToken: TOKEN };
}

function shown(order: AdminOrderDetail = ORDER, overrides: Partial<DetailState> = {}): DetailState {
  return { screen: ready(order), busy: false, error: null, ...overrides };
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
      <RouterProvider initialPath={`${ORDERS_PATH}/${ORDER_ID}`}>{element}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AdminOrderDetailViewProps> = {}, language: Language = "en"): string {
  const props: AdminOrderDetailViewProps = { state: shown(), states: STATES, onAdvance: noop, ...overrides };
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
  const nodes = html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
  return [...nodes, ...attributes].filter((value) => value !== "");
}

// 元素的全部文字连起来（相当于 textContent）。
function flat(html: string): string {
  return unescapeHtml(html.replace(/<[^>]*>/g, ""));
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

// 带某个 class 的全部同名元素（依出现顺序）。
function withClass(html: string, name: string, className: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].filter((m) => classes(m[0]).includes(className)).map((m) => elementAt(html, name, m.index));
}

function first(html: string, name: string, className: string): string {
  const [found] = withClass(html, name, className);
  if (found === undefined) {
    throw new Error(`not found: <${name} class="${className}">`);
  }
  return found;
}

function all(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => elementAt(html, name, m.index));
}

// 文字以某个字典文案开头的区块（桌面的 site-admin-order__wide 区块或手机的折叠区块）。
function blockTitled(blocks: string[], key: CopyKey, language: Language): string {
  const found = blocks.find((block) => visibleTexts(block)[0] === COPY[key][language]);
  if (found === undefined) {
    throw new Error(`no block titled ${key}`);
  }
  return found;
}

function price(sen: number, language: Language): string {
  return translate(language, "common.price_myr", { amount: formatSen(sen) });
}

function placeLine(language: Language): string {
  return ["12 Jalan Contoh 3, Taman Demo", "47000", "Selangor", countryName("MY", language)].join(", ");
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function detailBody(order: AdminOrderDetail = ORDER, token = TOKEN) {
  return { ...order, csrf_token: token };
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

// 路由替身：记录页面显示的详情状态与经 replace 去的地址。
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

describe("order details", () => {
  // SHOP-TASK-055 验收第 4 条「标题 admin.order_detail 加订单号与状态标签」与视觉稿 A02-desktop（「Order details · K7Q2-9MXA」与中性状态标签）。
  it.each(LANGUAGES)("shows the title with the order number and the status in %s", (language) => {
    const head = first(render({}, language), "div", "site-admin-order__head");
    const h2 = all(head, "h2")[0] ?? "";
    expect(flat(h2)).toBe(`${COPY["admin.order_detail"][language]} · ${ORDER.order_number}`);
    const tag = tags(head, "span").find((span) => classes(span).includes("acs-tag")) ?? "";
    expect(classes(tag)).toEqual(["acs-tag", "acs-tag--neutral"]);
    expect(head).toContain(`>${escapeHtml(COPY["order.status_paid"][language])}</span>`);
    const cancelled = first(render({ state: shown({ ...ORDER, status: "demo_cancelled" }) }, language), "div", "site-admin-order__head");
    expect(cancelled).toContain(`class="acs-tag acs-tag--outline"`);
  });

  // 同一条「order.items 每行名称、规格、件数与行小计」（UX A02 [order.items] <名称>/<规格> x2 [M2]）：每行左为名称、规格与 × 件数，右为行小计；
  // 没有规格的行不显示规格。商品区块是手机上的面板（A02-phone-detail）。
  it.each(LANGUAGES)("lists each item with its option, quantity and line subtotal in %s", (language) => {
    const items = first(render({}, language), "div", "site-admin-order__items");
    expect(classes(tags(items, "div")[0] ?? "")).toContain("acs-admin__panel");
    expect(visibleTexts(items)[0]).toBe(COPY["order.items"][language]);
    const rows = withClass(items, "div", "site-admin-order__row");
    expect(rows.map(visibleTexts)).toEqual([
      ["Crew Neck Tee", "Black, M", "× 2", price(6737, language)],
      ["Soy Wax Candle", "× 1", price(2763, language)],
    ]);
  });

  // 同一条「order.amount_breakdown 依次为 cart.subtotal、checkout.summary_coupon、checkout.summary_points、checkout.summary_shipping 与
  // checkout.summary_total（金额用 common.price_myr 与 formatSen）」：桌面区块与手机折叠区块里都是这五行，金额原样来自接口（游客订单也显示券与积分两行）。
  it.each(LANGUAGES)("shows the five amount rows in order in %s", (language) => {
    const html = render({}, language);
    const expected = [
      [COPY["cart.subtotal"][language], price(11000, language)],
      [COPY["checkout.summary_coupon"][language], price(1000, language)],
      [COPY["checkout.summary_points"][language], price(500, language)],
      [COPY["checkout.summary_shipping"][language], price(800, language)],
      [COPY["checkout.summary_total"][language], price(10300, language)],
    ];
    const wide = blockTitled(withClass(html, "div", "site-admin-order__wide"), "order.amount_breakdown", language);
    expect(withClass(wide, "div", "site-admin-order__row").map(visibleTexts)).toEqual(expected);
    const fold = blockTitled(withClass(html, "details", "site-admin-order__fold"), "order.amount_breakdown", language);
    expect(withClass(fold, "div", "site-admin-order__row").map(visibleTexts)).toEqual(expected);
    expect(price(10300, language)).toBe("RM 103.00");
  });

  // 同一条「admin.recipient_raw 显示收货资料原文（姓名、电话、地址、邮编、州属或地区与国家，沿用 recipientParts 与 useMyStates，与前台 P09 相同），
  // 其下 admin.recipient_audited」与 DESIGN 1.11「权限与资料保护」（仅管理员可见原始资料，查看留审计）：
  // 桌面区块与手机折叠区块（默认展开，照 A02-phone-detail）都是姓名 · 电话、地址, 邮编, 州属名称, 国家，之后为审计提示。
  it.each(LANGUAGES)("shows the original shipping details and the audit note in %s", (language) => {
    const html = render({}, language);
    const wide = blockTitled(withClass(html, "div", "site-admin-order__wide"), "admin.recipient_raw", language);
    const fold = blockTitled(withClass(html, "details", "site-admin-order__fold"), "admin.recipient_raw", language);
    expect(attributes(tags(fold, "details")[0] ?? "").has("open")).toBe(true);
    for (const block of [wide, fold]) {
      const text = flat(block);
      expect(text).toContain(`Aina Rahman · +60123456789`);
      expect(text).toContain(placeLine(language));
      expect(text.indexOf(placeLine(language))).toBeGreaterThan(text.indexOf("+60123456789"));
      expect(text.trimEnd().endsWith(COPY["admin.recipient_audited"][language])).toBe(true);
    }
    // 州属名称表还没取到时显示州属代码（与前台 P09 相同）。
    expect(flat(render({ states: null }, language))).toContain("47000, MY-10, ");
  });

  // 同一条「recipient 为 null 时不显示该区块」：没有 admin.recipient_raw，也没有审计提示与收货资料。
  it.each(LANGUAGES)("hides the shipping details when there is no recipient in %s", (language) => {
    const html = render({ state: shown({ ...ORDER, recipient: null }) }, language);
    expect(html).not.toContain(escapeHtml(COPY["admin.recipient_raw"][language]));
    expect(html).not.toContain(escapeHtml(COPY["admin.recipient_audited"][language]));
    expect(html).not.toContain("Aina Rahman");
    expect(html).toContain(escapeHtml(COPY["order.amount_breakdown"][language]));
  });

  // 同一条「admin.event_log 表格列为 admin.col_time（formatDateTime）、admin.col_event（变更后状态的 order.status_*）与 admin.col_actor
  // （admin.actor_customer、admin.actor_admin、admin.actor_system）」：访客与会员都是顾客；按接口的顺序（从早到晚）。
  it.each(LANGUAGES)("shows the event log in %s", (language) => {
    const html = render({}, language);
    const wide = blockTitled(withClass(html, "div", "site-admin-order__wide"), "admin.event_log", language);
    expect(classes(tags(wide, "table")[0] ?? "")).toContain("acs-admin__table");
    expect(all(wide, "th").map(flat)).toEqual([COPY["admin.col_time"][language], COPY["admin.col_event"][language], COPY["admin.col_actor"][language]]);
    const rows = all(all(wide, "tbody")[0] ?? "", "tr").map((row) => all(row, "td").map(flat));
    const actors: CopyKey[] = ["admin.actor_customer", "admin.actor_customer", "admin.actor_admin", "admin.actor_system"];
    expect(rows).toEqual(ORDER.events.map((event, index) => [formatDateTime(event.created_at, language), COPY[STATUS_KEY[event.status]][language], COPY[actors[index] ?? "admin.actor_admin"][language]]));
    // 手机（A02-phone-detail）：折叠区块里每条为「状态 · 操作者」，其下为时间。
    const fold = blockTitled(withClass(html, "details", "site-admin-order__fold"), "admin.event_log", language);
    expect(all(fold, "li").map(visibleTexts)).toEqual(
      ORDER.events.map((event, index) => [COPY[STATUS_KEY[event.status]][language], "·", COPY[actors[index] ?? "admin.actor_admin"][language], formatDateTime(event.created_at, language)]),
    );
  });

  // 同一条「手机按 A02-phone-detail 用可折叠区块」与验收第 1 条「由 site.css 按宽度显隐」：金额明细（摘要行带合计）、收货资料与事件记录
  // 各有一个折叠面板（acs-admin__panel 的 details），桌面的同名区块另带 site-admin-order__wide；金额明细与事件记录默认收起。
  it("renders the phone sections as collapsible panels", () => {
    const html = render();
    const folds = withClass(html, "details", "site-admin-order__fold");
    expect(folds.map((fold) => visibleTexts(all(fold, "summary")[0] ?? ""))).toEqual([
      [COPY["order.amount_breakdown"].en, price(10300, "en")],
      [COPY["admin.recipient_raw"].en],
      [COPY["admin.event_log"].en],
    ]);
    expect(folds.map((fold) => classes(tags(fold, "details")[0] ?? "").includes("acs-admin__panel"))).toEqual([true, true, true]);
    expect(folds.map((fold) => attributes(tags(fold, "details")[0] ?? "").has("open"))).toEqual([false, true, false]);
    expect(withClass(html, "div", "site-admin-order__wide").map((block) => visibleTexts(block)[0])).toEqual([
      COPY["order.amount_breakdown"].en,
      COPY["admin.recipient_raw"].en,
      COPY["admin.event_log"].en,
    ]);
  });

  // 验收第 2 条「手机只显示详情与 common.back（返回 /admin/orders）」与第 6 条「common.back 与详情顶部的返回一样只在手机显示」：
  // 详情最前面是指向 /admin/orders 的返回链接（图形 aria-hidden，文字 common.back），由 site.css 只在手机显示。
  it.each(LANGUAGES)("starts with the back link to the order list in %s", (language) => {
    const html = render({}, language);
    const section = tags(html, "section")[0] ?? "";
    expect(classes(section)).toEqual(["acs-admin__panel", "site-admin-order"]);
    const back = first(html, "a", "site-admin-order__back");
    expect(html.indexOf(back)).toBeLessThan(html.indexOf("site-admin-order__head"));
    expect(attributes(tags(back, "a")[0] ?? "").get("href")).toBe(ORDERS_PATH);
    expect(visibleTexts(back)).toEqual([COPY["common.back"][language]]);
    expect(attributes(tags(back, "svg")[0] ?? "").get("aria-hidden")).toBe("true");
  });
});

describe("advancing", () => {
  // UX A02「推进 demo_paid → demo_packed → demo_shipped」与验收第 5 条「admin.mark_packed 只在 demo_paid 时可点，admin.mark_shipped 只在 demo_packed 时可点，
  // 其余状态都禁用；fully_refunded 为真时两者都禁用」。
  it("allows each step only from its own status and never when fully refunded", () => {
    for (const status of ORDER_STATUSES) {
      expect(canAdvance({ status, fully_refunded: false }, "demo_packed"), status).toBe(status === "demo_paid");
      expect(canAdvance({ status, fully_refunded: false }, "demo_shipped"), status).toBe(status === "demo_packed");
      for (const target of ADVANCE_TARGETS) {
        expect(canAdvance({ status, fully_refunded: true }, target), status).toBe(false);
      }
    }
  });

  // 同一条与 UX「状态与补充（0.9）」「全部已退…都禁用，其下显示 [admin.frozen]，[admin.ship_hint] 照常显示」、视觉稿 A02-desktop 与 A02-desktop-frozen：
  // 每种状态、全部已退与否，两个按钮的禁用与样式（可点为主按钮、禁用为次要按钮）；admin.frozen 只在全部已退时出现在按钮之下（role="status"）；
  // admin.ship_hint 始终在最后。
  it.each(LANGUAGES)("enables the buttons by status and shows the frozen note in %s", (language) => {
    for (const status of ORDER_STATUSES) {
      for (const frozen of [false, true]) {
        const html = render({ state: shown({ ...ORDER, status, fully_refunded: frozen }) }, language);
        const actions = first(html, "div", "site-admin-order__actions");
        const buttons = tags(actions, "button").map(attributes);
        expect(all(actions, "button").map(flat)).toEqual([COPY["admin.mark_packed"][language], COPY["admin.mark_shipped"][language]]);
        const usable = [!frozen && status === "demo_paid", !frozen && status === "demo_packed"];
        expect(buttons.map((button) => button.has("disabled")), `${status} ${String(frozen)}`).toEqual(usable.map((value) => !value));
        expect(buttons.map((button) => (button.get("class") ?? "").includes("acs-admin__btn--secondary"))).toEqual(usable.map((value) => !value));
        expect(buttons.map((button) => button.get("type"))).toEqual(["button", "button"]);
        const frozenNote = `<p class="acs-admin__panel site-admin-order__frozen" role="status">${escapeHtml(COPY["admin.frozen"][language])}</p>`;
        expect(actions.includes(frozenNote)).toBe(frozen);
        const hint = first(actions, "p", "acs-hint");
        expect(visibleTexts(hint)).toEqual([COPY["admin.ship_hint"][language]]);
        expect(actions.indexOf(hint)).toBeGreaterThan(actions.lastIndexOf("</button>"));
        if (frozen) {
          expect(actions.indexOf(frozenNote)).toBeGreaterThan(actions.lastIndexOf("</button>"));
          expect(actions.indexOf(frozenNote)).toBeLessThan(actions.indexOf(hint));
        }
      }
    }
  });

  // 同一条「进行中按钮禁用」：推进或之后的重新读取进行中两个按钮都禁用，详情区标 aria-busy。
  it("disables both buttons while a request is in progress", () => {
    const html = render({ state: shown(ORDER, { busy: true }) });
    expect(tags(first(html, "div", "site-admin-order__actions"), "button").map((tag) => attributes(tag).has("disabled"))).toEqual([true, true]);
    expect(attributes(tags(html, "section")[0] ?? "").get("aria-busy")).toBe("true");
    expect(attributes(tags(render(), "section")[0] ?? "").get("aria-busy")).toBe("false");
  });

  // 同一条「409 与 403…显示 common.error_retry；网络中断显示 common.network_check」：提示在推进区里（role="alert"），按钮之后、提示之前。
  it.each(LANGUAGES)("shows the advance messages next to the buttons in %s", (language) => {
    for (const key of ["common.error_retry", "common.network_check"] as const) {
      const actions = first(render({ state: shown(ORDER, { error: key }) }, language), "div", "site-admin-order__actions");
      const alert = `<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div>`;
      expect(actions).toContain(alert);
      expect(actions.indexOf(alert)).toBeGreaterThan(actions.lastIndexOf("</button>"));
      expect(actions.indexOf(alert)).toBeLessThan(actions.indexOf("acs-hint"));
    }
  });

  // 验收第 5 条「200 后重新取详情」：推进的请求体只有目标状态、请求头带详情返回的令牌；之后以当前语言重新读取一次并显示新详情，没有提示。
  it.each(ADVANCE_TARGETS)("reads the order again after advancing to %s", async (target) => {
    const after = { ...ORDER, status: target };
    const calls = stubFetch(json(200, { status: target }), json(200, detailBody(after, "next-token")));
    const moves = fakeMoves();
    const controller = new AbortController();
    await advanceOrder(ORDER_ID, ready({ ...ORDER, status: target === "demo_packed" ? "demo_paid" : "demo_packed" }), target, "zh", controller.signal, moves.target);
    expect(calls.map((call) => `${call.init.method ?? ""} ${call.url}`)).toEqual(["POST /api/admin/orders/42/status", "GET /api/admin/orders/42?lang=zh"]);
    const sent: unknown = JSON.parse(calls[0]?.init.body as string);
    expect(sent).toEqual({ status: target });
    expect((calls[0]?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(TOKEN);
    expect(calls.every((call) => call.init.signal === controller.signal)).toBe(true);
    expect(moves.events).toEqual(["show"]);
    expect(moves.states).toEqual([{ screen: { status: "ready", order: after, csrfToken: "next-token" }, busy: false, error: null }]);
  });

  // 同一条「409（order_not_advanceable 或 fulfilment_frozen）与 403 重新取详情并显示 common.error_retry」。
  it.each([
    json(409, { detail: "order_not_advanceable", status: "demo_shipped" }),
    json(409, { detail: "fulfilment_frozen", status: "demo_paid" }),
    json(403, { detail: "csrf_failed" }),
  ])("reads the order again and asks to retry after a refusal (%#)", async (reply) => {
    const after = { ...ORDER, status: "demo_shipped" as const };
    const calls = stubFetch(reply, json(200, detailBody(after, "fresh-token")));
    const moves = fakeMoves();
    await advanceOrder(ORDER_ID, ready(), "demo_packed", "en", new AbortController().signal, moves.target);
    expect(calls).toHaveLength(2);
    expect(calls[1]?.url).toBe("/api/admin/orders/42?lang=en");
    expect(moves.states).toEqual([{ screen: { status: "ready", order: after, csrfToken: "fresh-token" }, busy: false, error: "common.error_retry" }]);
  });

  // 同一条「网络中断显示 common.network_check（再点即重发，已是目标状态时服务端回答 200，重复安全）；其他失败 common.error_retry」：
  // 不重新读取，保留原来的详情（按钮仍按原状态可点）；再点即再发同样的请求。
  it.each<[Response | (() => never), CopyKey]>([
    [networkDown, "common.network_check"],
    [json(500, {}), "common.error_retry"],
    [json(404, { detail: "not_found" }), "common.error_retry"],
    [json(409, { detail: "idempotency_conflict" }), "common.error_retry"],
  ])("keeps the order and shows a message when advancing fails (%#)", async (reply, key) => {
    const calls = stubFetch(reply, json(200, { status: "demo_packed" }), json(200, detailBody({ ...ORDER, status: "demo_packed" })));
    const moves = fakeMoves();
    await advanceOrder(ORDER_ID, ready(), "demo_packed", "en", new AbortController().signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(moves.states).toEqual([{ screen: ready(), busy: false, error: key }]);
    expect(canAdvance(ORDER, "demo_packed")).toBe(true);
    // 再点：同一请求体与令牌再发一次；已是目标状态时服务端照样 200，之后重新读取。
    await advanceOrder(ORDER_ID, ready(), "demo_packed", "en", new AbortController().signal, moves.target);
    expect(calls).toHaveLength(3);
    expect(calls[1]?.init.body).toBe(calls[0]?.init.body);
    expect(moves.states[1]?.screen).toEqual({ status: "ready", order: { ...ORDER, status: "demo_packed" }, csrfToken: TOKEN });
  });

  // 同一条「401（详情或推进）用路由的 replace 进入 /admin/login」：推进 401 不再读取；重新读取 401 同样进入登录页。
  it("goes to the login page on 401 when advancing or reading again", async () => {
    const first401 = stubFetch(json(401, { detail: "admin_session_required" }));
    const moves = fakeMoves();
    await advanceOrder(ORDER_ID, ready(), "demo_packed", "en", new AbortController().signal, moves.target);
    expect(first401).toHaveLength(1);
    expect(moves.events).toEqual([`replace ${LOGIN_PATH}`]);

    const second401 = stubFetch(json(200, { status: "demo_packed" }), json(401, { detail: "admin_session_required" }));
    const again = fakeMoves();
    await advanceOrder(ORDER_ID, ready(), "demo_packed", "en", new AbortController().signal, again.target);
    expect(second401).toHaveLength(2);
    expect(again.events).toEqual([`replace ${LOGIN_PATH}`]);
  });

  // 派生实现约束（实现选择）：推进成功或被拒后重新读取失败时保留原来的详情，显示读取的提示（网络中断 common.network_check，其他 common.error_retry）；
  // 重新读取得到 404 时换成订单不存在的显示（Kelvin 2026-10-07）。
  it.each<[Response | (() => never), DetailState]>([
    [networkDown, { screen: ready(), busy: false, error: "common.network_check" }],
    [json(500, {}), { screen: ready(), busy: false, error: "common.error_retry" }],
    [json(404, { detail: "not_found" }), { screen: { status: "missing" }, busy: false, error: null }],
  ])("handles a failed second read (%#)", async (reread, state) => {
    stubFetch(json(200, { status: "demo_packed" }), reread);
    const moves = fakeMoves();
    await advanceOrder(ORDER_ID, ready(), "demo_packed", "en", new AbortController().signal, moves.target);
    expect(moves.states).toEqual([state]);
  });

  // 验收第 6 条「离开页面或换到另一张订单时中止旧请求，旧请求的结果不再更新页面」：推进或重新读取期间中止后不再显示也不跳转。
  it("does nothing after leaving the page while advancing", async () => {
    stubFetch(json(200, { status: "demo_packed" }), json(200, detailBody()));
    const controller = new AbortController();
    const moves = fakeMoves();
    const pending = advanceOrder(ORDER_ID, ready(), "demo_packed", "en", controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("opening the details", () => {
  // 验收第 4 条「每次打开详情只请求一次（服务端每次查看都写审计）」与第 3 条「lang 为当前界面语言」：打开时只发一个 GET，地址为内部 ID 与语言。
  it.each(LANGUAGES)("reads the order once in %s", async (language) => {
    const calls = stubFetch(json(200, detailBody()));
    const moves = fakeMoves();
    const controller = new AbortController();
    await openDetail(ORDER_ID, language, controller.signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/orders/42?lang=${language}`);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(moves.states).toEqual([shown()]);
  });

  // 验收第 5、6 条与 Kelvin 2026-10-07「打开不存在的订单…显示现有的 common.error_retry 与 common.back，不新增文案」：
  // 200 显示详情；401 以 replace 进入 /admin/login；404 为订单不存在；网络中断 common.network_check；其他失败（含响应体不合格）common.error_retry。
  it.each<[Response | (() => never), string[], DetailState | null]>([
    [json(200, detailBody()), ["show"], shown()],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [json(404, { detail: "not_found" }), ["show"], { screen: { status: "missing" }, busy: false, error: null }],
    [networkDown, ["show"], { screen: { status: "failed" }, busy: false, error: "common.network_check" }],
    [json(500, {}), ["show"], { screen: { status: "failed" }, busy: false, error: "common.error_retry" }],
    [json(200, { ...detailBody(), lines: "none" }), ["show"], { screen: { status: "failed" }, busy: false, error: "common.error_retry" }],
  ])("shows what the reply says (%#)", async (reply, events, state) => {
    stubFetch(reply);
    const moves = fakeMoves();
    await openDetail(ORDER_ID, "en", new AbortController().signal, moves.target);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 验收第 3 条「路径里的 ID 不是不带符号与前导零的正整数时不发请求」与第 6 条「路径 ID 不合法时详情位置显示 common.error_retry 与 common.back」。
  it.each(["0", "042", "-3", "abc", "1.0", ":id", "2147483648"])("sends nothing and shows the missing state for %j", async (raw) => {
    const calls = stubFetch(json(200, detailBody()));
    const moves = fakeMoves();
    await openDetail(raw, "en", new AbortController().signal, moves.target);
    expect(calls).toEqual([]);
    expect(moves.states).toEqual([{ screen: { status: "missing" }, busy: false, error: null }]);
  });

  // 同一条「读取结果的归类」：readStep 与 openDetail 一致。
  it("chooses what to show from the read", () => {
    expect(INITIAL_DETAIL).toEqual({ screen: { status: "loading" }, busy: true, error: null });
    expect(readStep({ kind: "ok", order: ORDER, csrfToken: TOKEN })).toEqual(shown());
    expect(readStep({ kind: "none" })).toBe("login");
    expect(readStep({ kind: "missing" })).toEqual({ screen: { status: "missing" }, busy: false, error: null });
    expect(readStep({ kind: "network" })).toEqual({ screen: { status: "failed" }, busy: false, error: "common.network_check" });
    expect(readStep({ kind: "failed" })).toEqual({ screen: { status: "failed" }, busy: false, error: "common.error_retry" });
  });

  // 验收第 6 条「订单不存在（404）或路径 ID 不合法时详情位置显示 common.error_retry 与 common.back（返回 /admin/orders），不显示详情」：
  // 详情位置只有返回链接与 common.error_retry 提示，没有标题、商品、按钮或收货资料。
  it.each(LANGUAGES)("shows only the retry message and the back link for a missing order in %s", (language) => {
    const html = render({ state: { screen: { status: "missing" }, busy: false, error: null } }, language);
    expect(visibleTexts(html)).toEqual([COPY["common.back"][language], COPY["common.error_retry"][language]]);
    expect(html).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY["common.error_retry"][language])}</span></div>`);
    expect(attributes(tags(first(html, "a", "site-admin-order__back"), "a")[0] ?? "").get("href")).toBe(ORDERS_PATH);
    expect(html).not.toMatch(/<(button|h2|table|details)\b/);
  });

  // 验收第 6 条「详情读取失败或网络中断时显示 common.error_retry 或 common.network_check」；读取中详情区只有返回链接并标 aria-busy。
  it.each(LANGUAGES)("shows the read failures and the loading state in %s", (language) => {
    for (const key of ["common.error_retry", "common.network_check"] as const) {
      const html = render({ state: { screen: { status: "failed" }, busy: false, error: key } }, language);
      expect(visibleTexts(html)).toEqual([COPY["common.back"][language], COPY[key][language]]);
      expect(html).toContain(`role="alert"`);
    }
    const loading = render({ state: INITIAL_DETAIL }, language);
    expect(visibleTexts(loading)).toEqual([COPY["common.back"][language]]);
    expect(attributes(tags(loading, "section")[0] ?? "").get("aria-busy")).toBe("true");
  });

  // 验收第 6 条「离开页面或换到另一张订单时中止旧请求，旧请求的结果不再更新页面」：旧订单的读取中止后，不论它回答什么都不显示也不跳转，
  // 只显示新订单的结果。
  it.each([json(200, detailBody()), json(401, { detail: "admin_session_required" }), json(404, { detail: "not_found" }), json(500, {})])(
    "ignores the reply for the previous order (%#)",
    async (oldReply) => {
      const other = { ...ORDER, id: 43, order_number: "B6TN-2RJD" };
      const calls = stubFetch(oldReply, json(200, detailBody(other)));
      const moves = fakeMoves();
      const old = new AbortController();
      const pendingOld = openDetail(ORDER_ID, "en", old.signal, moves.target);
      old.abort();
      const pendingNext = openDetail("43", "en", new AbortController().signal, moves.target);
      await Promise.all([pendingOld, pendingNext]);
      expect(calls.map((call) => call.url)).toEqual(["/api/admin/orders/42?lang=en", "/api/admin/orders/43?lang=en"]);
      expect(moves.events).toEqual(["show"]);
      expect(moves.states).toEqual([shown(other)]);
    },
  );
});

describe("privacy", () => {
  // 验收第 3 条「订单号、收货资料与令牌只在页面内存与请求里，不进网址、浏览器存储或日志」与 DESIGN 1.11「权限与资料保护」：
  // 打开与推进的全过程不写 localStorage、sessionStorage、cookie 或历史记录，不写控制台；请求地址里没有订单号、收货资料或令牌；
  // 页面标记里没有令牌，链接只指向 /admin/orders。
  it("keeps the order number, the recipient and the token out of addresses, storage, logs and markup", async () => {
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
    const logs = (["log", "info", "warn", "error", "debug"] as const).map((name) => vi.spyOn(console, name).mockImplementation(() => undefined));

    const calls = stubFetch(json(200, detailBody()), json(409, { detail: "fulfilment_frozen", status: "demo_paid" }), json(200, detailBody()));
    const moves = fakeMoves();
    await openDetail(ORDER_ID, "en", new AbortController().signal, moves.target);
    await advanceOrder(ORDER_ID, ready(), "demo_packed", "en", new AbortController().signal, moves.target);
    expect(calls).toHaveLength(3);
    const secrets = [ORDER.order_number, "Aina Rahman", "+60123456789", "12 Jalan", "47000", TOKEN];
    for (const call of calls) {
      for (const secret of secrets) {
        expect(call.url).not.toContain(secret);
        expect(typeof call.init.body === "string" ? call.init.body : "").not.toContain(secret);
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
    const html = render();
    expect(html).not.toContain(TOKEN);
    expect([...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1])).toEqual([ORDERS_PATH]);
  });
});

describe("dictionary", () => {
  // 验收第 7 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：
  // 详情（含冻结、提示、读取中、不存在与失败）每段文字都是当前语言的字典文案，或接口给的订单号、商品名称与规格、收货资料、
  // 按界面语言格式化的时间与金额；此外只有视觉稿给定的符号 ·、逗号与「× 件数」。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const value of [ORDER.order_number, "Crew Neck Tee", "Black, M", "Soy Wax Candle", "× 2", "× 1", "Aina Rahman", "+60123456789", "12 Jalan Contoh 3, Taman Demo", "47000", "Selangor", "MY-10", countryName("MY", language), "·", ","]) {
      allowed.add(value);
    }
    for (const sen of [6737, 2763, 11000, 1000, 500, 800, 10300]) {
      allowed.add(price(sen, language));
    }
    for (const event of ORDER.events) {
      allowed.add(formatDateTime(event.created_at, language));
    }
    const states: DetailState[] = [
      INITIAL_DETAIL,
      shown(),
      shown({ ...ORDER, recipient: null }),
      shown({ ...ORDER, status: "demo_packed", fully_refunded: true }),
      shown(ORDER, { busy: true }),
      shown(ORDER, { error: "common.error_retry" }),
      shown(ORDER, { error: "common.network_check" }),
      { screen: { status: "missing" }, busy: false, error: null },
      { screen: { status: "failed" }, busy: false, error: "common.network_check" },
      { screen: { status: "failed" }, busy: false, error: "common.error_retry" },
    ];
    for (const state of states) {
      for (const html of [render({ state }, language), render({ state, states: null }, language)]) {
        expect(html).not.toMatch(/\stitle=/i);
        for (const value of visibleTexts(html)) {
          // 收货资料各段之间的分隔「, 」与后一段在静态标记里连成一个文本节点（如「, 47000」），拆开后分别核对。
          const parts = value.startsWith(", ") ? [",", value.slice(2)] : [value];
          for (const part of parts) {
            expect(allowed.has(part), value).toBe(true);
          }
        }
      }
    }
  });
});
