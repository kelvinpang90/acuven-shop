import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { reviewAttempt } from "../api/adminRefunds";
import type { AdminRefundDetail, RefundStatus, ReviewAction, ReviewAttempt } from "../api/adminRefunds";
import { formatSen } from "../format";
import { COPY, LANGUAGES, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  AdminRefundDetailView,
  canReview,
  detailStep,
  INITIAL_DETAIL,
  keptAttempt,
  openDetail,
  REASON_MAX_LENGTH,
  submitReview,
} from "./AdminRefundDetail";
import type { AdminRefundDetailViewProps, DetailState, ReviewMoves } from "./AdminRefundDetail";
import { formatDateTime } from "./TrackOrderPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const DETAIL_PATH = "/admin/refunds/9";
const REFUNDS_PATH = "/admin/refunds";
const ORDER_LIST_PATH = "/admin/refunds/order/42";
const LOGIN_PATH = "/admin/login";
const TOKEN = "csrf-token-for-admin";
const NEW_TOKEN = "csrf-token-after-reread";
const REASON = "The returned hoodie shows signs of use.";
const REVIEWER = "kelvin@example.com";

type DetailLine = AdminRefundDetail["lines"][number];

const TEE: DetailLine = { name: "Crew Neck Tee", variant_label: "White, L", quantity: 1, purchased_quantity: 2, approved_quantity: 0 };
const CANDLE: DetailLine = { name: "Soy Wax Candle", variant_label: "", quantity: 2, purchased_quantity: 3, approved_quantity: 1 };

// 审核中的申请（视觉稿 A03-desktop-order：一行有规格，一行没有）。
const REQUESTED: AdminRefundDetail = {
  id: 9,
  status: "requested",
  created_at: "2026-10-01T10:40:00Z",
  reviewed_at: null,
  reviewer_username: null,
  review_reason: null,
  order_id: 42,
  order_number: "H3PZ-4W8C",
  order_status: "demo_shipped",
  amount_sen: 3400,
  lines: [TEE, CANDLE],
  refunded_sen: 2763,
  refundable_left_sen: 9500,
  csrf_token: TOKEN,
};

// 已批准、没写理由（A03-desktop-reviewed）。
const APPROVED: AdminRefundDetail = {
  ...REQUESTED,
  status: "approved",
  reviewed_at: "2026-10-02T07:10:00Z",
  reviewer_username: REVIEWER,
  lines: REQUESTED.lines.map((line) => ({ ...line, approved_quantity: line.approved_quantity + line.quantity })),
  refunded_sen: 6163,
  refundable_left_sen: 6100,
  csrf_token: NEW_TOKEN,
};

// 已拒绝、有理由（A03-phone-detail-rejected）。
const REJECTED: AdminRefundDetail = {
  ...REQUESTED,
  status: "rejected",
  reviewed_at: "2026-10-02T10:02:00Z",
  reviewer_username: REVIEWER,
  review_reason: REASON,
  csrf_token: NEW_TOKEN,
};

const STATUS_KEY: Readonly<Record<RefundStatus, CopyKey>> = {
  requested: "order.refund_requested",
  approved: "order.refund_approved",
  rejected: "order.refund_rejected",
};

const TAG_CLASSES: Readonly<Record<RefundStatus, string[]>> = {
  requested: ["acs-tag", "acs-tag--demo"],
  approved: ["acs-tag", "acs-tag--success"],
  rejected: ["acs-tag", "acs-tag--outline"],
};

function shown(refund: AdminRefundDetail, notice: CopyKey | null = null, busy = false): DetailState {
  return { status: "ready", refund, busy, notice };
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

function render(overrides: Partial<AdminRefundDetailViewProps> = {}, language: Language = "en"): string {
  const props: AdminRefundDetailViewProps = { state: shown(REQUESTED), reason: "", backTo: REFUNDS_PATH, onReason: noop, onReview: noop, ...overrides };
  return renderToStaticMarkup(wrap(<AdminRefundDetailView {...props} />, language));
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

function headers(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}

function body(call: Call | undefined): unknown {
  return JSON.parse(call?.init.body as string);
}

// 路由替身：记录详情区显示的状态、经 replace 去的地址、幂等键的去留与列表的重新查询。
function fakeMoves() {
  const events: string[] = [];
  const states: DetailState[] = [];
  const kept: (ReviewAttempt | null)[] = [];
  const target: ReviewMoves = {
    show: (state) => {
      states.push(state);
      events.push("show");
    },
    replace: (path: RoutePath) => {
      events.push(`replace ${path}`);
    },
    keep: (attempt) => {
      kept.push(attempt);
      events.push(attempt === null ? "drop key" : "keep key");
    },
    reviewed: () => {
      events.push("reload list");
    },
  };
  return { target, events, states, kept };
}

// 按顺序给出的幂等键（代替 crypto.randomUUID）。
function keys() {
  let next = 0;
  return () => `review-key-${String((next += 1)).padStart(4, "0")}`;
}

function attempt(action: ReviewAction, reason: string, key = "review-key-0001"): ReviewAttempt {
  return { action, reason, key };
}

function qty(line: DetailLine, language: Language): string {
  return translate(language, "admin.refund_qty", { requested: line.quantity, bought: line.purchased_quantity, approved: line.approved_quantity });
}

function amountLines(refund: AdminRefundDetail, language: Language): string[] {
  return [
    translate(language, "admin.refund_amount", { amount: formatSen(refund.amount_sen) }),
    translate(language, "order.refunded_total", { amount: formatSen(refund.refunded_sen) }),
    translate(language, "order.refundable_left", { amount: formatSen(refund.refundable_left_sen) }),
    COPY["refund.shipping_not_refunded"][language],
  ];
}

function reviewedBy(refund: AdminRefundDetail, language: Language): string {
  return translate(language, "admin.reviewed_by", { username: refund.reviewer_username ?? "", time: formatDateTime(refund.reviewed_at ?? "", language) });
}

function buttons(html: string): Map<string, string>[] {
  return tags(element(html, "div", "site-admin-refund__buttons"), "button").map(attributes);
}

// 把 localStorage、sessionStorage、document.cookie、history 与 console 换成替身。
function stubBrowser() {
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
  const logs = (["log", "info", "warn", "error", "debug", "trace"] as const).map((method) => vi.spyOn(console, method).mockImplementation(() => undefined));
  return { local, session, cookieWrites, history, logs };
}

describe("refund detail", () => {
  // UX A03 线框「[admin.refund_detail] [K]<订单号>」、「状态与补充（0.9）」「详情标题里的订单号链接到 A02 该订单的详情」与
  // SHOP-TASK-057 验收第 4 条「标题 admin.refund_detail 加订单号（链接到 /admin/orders/<订单内部 ID>）与状态标签」：
  // h2 为 admin.refund_detail · 订单号（· 只是视觉，不读出），订单号是指向 A02 该单详情的站内链接；其后为状态标签（照视觉稿的三种样式）。
  it.each(LANGUAGES)("shows the title, the order link and the status in %s", (language) => {
    for (const refund of [REQUESTED, APPROVED, REJECTED]) {
      const head = element(render({ state: shown(refund) }, language), "div", "site-admin-refund__head");
      const h2 = all(head, "h2")[0] ?? "";
      expect(textNodes(h2)).toEqual([COPY["admin.refund_detail"][language], "·", refund.order_number]);
      expect(h2).toContain(`<span aria-hidden="true"> · </span>`);
      const links = all(h2, "a");
      expect(links).toHaveLength(1);
      expect(attributes(tags(links[0] ?? "", "a")[0] ?? "").get("href")).toBe("/admin/orders/42");
      expect(textNodes(links[0] ?? "")).toEqual([refund.order_number]);
      const tag = tags(head, "span").find((span) => classes(span).includes("acs-tag")) ?? "";
      expect(classes(tag)).toEqual(TAG_CLASSES[refund.status]);
      expect(head.indexOf(tag)).toBeGreaterThan(head.indexOf("</h2>"));
      expect(textNodes(head).at(-1)).toBe(COPY[STATUS_KEY[refund.status]][language]);
    }
  });

  // UX A03 线框「<名称>/<规格> [admin.refund_qty]」与验收第 4 条「各行名称、规格与 admin.refund_qty（申请、购买与已批准件数）」：
  // 区块标题 order.items，每行为 名称 / 规格（没有规格的行不显示 /），右侧为 admin.refund_qty 填入接口给的三个件数。
  it.each(LANGUAGES)("shows each line with the requested, bought and approved quantities in %s", (language) => {
    const items = element(render({}, language), "div", "site-admin-refund__items");
    expect(textNodes(items)[0]).toBe(COPY["order.items"][language]);
    expect(elements(items, "div", "site-admin-refund__row").map(textNodes)).toEqual([
      ["Crew Neck Tee", "/", "White, L", qty(TEE, language)],
      ["Soy Wax Candle", qty(CANDLE, language)],
    ]);
    expect(qty(CANDLE, "en")).toBe("Requested 2 / bought 3 / approved 1");
  });

  // UX A03 线框「[admin.refund_amount] [M1]」「[order.refunded_total] [M3] [order.refundable_left] [M3]」「[refund.shipping_not_refunded]」与
  // 验收第 4 条「金额用 formatSen」：金额区块依次为申请金额、累计已退、剩余可退与运费不退的说明，金额照接口的整数仙格式化。
  it.each(LANGUAGES)("shows the amounts in %s", (language) => {
    const block = elements(render({}, language), "div", "site-admin-refund__block")[1] ?? "";
    expect(textNodes(block)).toEqual(amountLines(REQUESTED, language));
    expect(amountLines(REQUESTED, "en").slice(0, 3)).toEqual(["Cash refund (demo): RM 34.00", "Refunded so far (demo): RM 27.63", "Still refundable: RM 95.00"]);
  });

  // UX A03「状态与补充（0.9）」「首版不显示 [admin.refund_points] 一行」与验收第 4 条「不显示 admin.refund_points」（Kelvin 2026-10-06 决定）：
  // 字典里没有这个键，三种状态、三种语言的详情里都没有积分一行的文字（取 UX-COPY 该键三语的开头）。
  it.each(LANGUAGES)("has no points line in %s", (language) => {
    expect(Object.keys(COPY)).not.toContain("admin.refund_points");
    for (const refund of [REQUESTED, APPROVED, REJECTED]) {
      const html = render({ state: shown(refund) }, language);
      for (const text of ["Points returned", "返还积分", "Mata dikembalikan"]) {
        expect(html).not.toContain(text);
      }
    }
  });
});

describe("reviewing a request under review", () => {
  // UX A03 线框「[admin.refund_reason] [____] ( [admin.refund_approve] ) ( [admin.refund_reject] ) [admin.refund_hint]」与
  // 验收第 4 条「审核中：admin.refund_reason 输入框（textarea，最多 500 个字符）、admin.refund_approve、admin.refund_reject 与 admin.refund_hint」：
  // 金额之后为带标签的 textarea（maxlength 500），其后批准（主按钮）与拒绝（次要按钮），按钮之下为 ◆ 与 admin.refund_hint；没有 admin.reviewed_by。
  it.each(LANGUAGES)("shows the reason box, the two buttons and the hint in %s", (language) => {
    const html = render({ reason: REASON }, language);
    const label = all(html, "label")[0] ?? "";
    expect(textNodes(label)).toEqual([COPY["admin.refund_reason"][language], REASON]);
    const textarea = attributes(tags(label, "textarea")[0] ?? "");
    expect(textarea.get("maxlength")).toBe(String(REASON_MAX_LENGTH));
    expect(REASON_MAX_LENGTH).toBe(500);
    expect(textarea.has("disabled")).toBe(false);
    expect(html.indexOf("<label")).toBeGreaterThan(html.indexOf(escapeHtml(COPY["refund.shipping_not_refunded"][language])));
    const actions = element(html, "div", "site-admin-refund__actions");
    expect(all(actions, "button").map(textNodes)).toEqual([[COPY["admin.refund_approve"][language]], [COPY["admin.refund_reject"][language]]]);
    expect(tags(actions, "button").map(classes)).toEqual([["acs-admin__btn"], ["acs-admin__btn", "acs-admin__btn--secondary"]]);
    expect(tags(actions, "button").map((tag) => attributes(tag).get("type"))).toEqual(["button", "button"]);
    const hint = element(actions, "div", "site-admin-refund__hint");
    expect(textNodes(hint)).toEqual([COPY["admin.refund_hint"][language]]);
    expect(attributes(tags(hint, "svg")[0] ?? "").get("aria-hidden")).toBe("true");
    expect(actions.indexOf(hint)).toBeGreaterThan(actions.indexOf("</button>"));
    // 内容面板里依次为商品、金额与理由输入框三个区块，没有审核记录（admin.reviewed_by）。
    const blocks = elements(html, "div", "site-admin-refund__block");
    expect(blocks).toHaveLength(3);
    expect(blocks[2]).toContain("<textarea");
    expect(html.indexOf(actions)).toBeGreaterThan(html.indexOf("</textarea>"));
  });

  // UX A03「状态与补充（0.9）」「理由去掉首尾空白后为空时 [admin.refund_reject] 禁用，批准不要求理由」（Kelvin 2026-10-06 决定）：
  // 理由为空或只有空白时拒绝禁用、批准可点；有理由时两者都可点；超过 500 个字符时两者都不可点；已审核的申请两者都不可点。
  it("disables reject until the trimmed reason is not empty", () => {
    for (const reason of ["", "   ", "\n\t "]) {
      expect(canReview(REQUESTED, "approve", reason)).toBe(true);
      expect(canReview(REQUESTED, "reject", reason)).toBe(false);
      expect(buttons(render({ reason })).map((button) => button.has("disabled"))).toEqual([false, true]);
    }
    for (const reason of [REASON, " x ", "x".repeat(500)]) {
      expect(canReview(REQUESTED, "approve", reason)).toBe(true);
      expect(canReview(REQUESTED, "reject", reason)).toBe(true);
      expect(buttons(render({ reason })).map((button) => button.has("disabled"))).toEqual([false, false]);
    }
    expect(canReview(REQUESTED, "approve", "x".repeat(501))).toBe(false);
    expect(canReview(REQUESTED, "reject", "x".repeat(501))).toBe(false);
    for (const refund of [APPROVED, REJECTED]) {
      expect(canReview(refund, "approve", REASON)).toBe(false);
      expect(canReview(refund, "reject", REASON)).toBe(false);
    }
  });

  // 验收第 4 条「进行中两个按钮都禁用」：审核请求进行中两个按钮与理由输入框都禁用（理由有内容时也是）。
  it("disables both buttons while a review is in progress", () => {
    const html = render({ state: shown(REQUESTED, null, true), reason: REASON });
    expect(buttons(html).map((button) => button.has("disabled"))).toEqual([true, true]);
    expect(attributes(tags(html, "textarea")[0] ?? "").has("disabled")).toBe(true);
  });

  // 验收第 4 条「200 后重新取详情与列表」：先以本次的键与详情的令牌 POST，200 后丢弃该键、让列表重新查询，再 GET 详情并显示新详情
  // （已批准、没有提示）；请求体只有 reason。
  it("reloads the detail and the list after 200", async () => {
    const calls = stubFetch(json(200, { refund_request_id: 9, status: "approved" }), json(200, APPROVED));
    const moves = fakeMoves();
    const review = attempt("approve", "");
    await submitReview("9", REQUESTED, review, "zh", new AbortController().signal, moves.target);
    expect(requests(calls)).toEqual(["POST /api/admin/refunds/9/approve", "GET /api/admin/refunds/9?lang=zh"]);
    expect(body(calls[0])).toEqual({ reason: "" });
    expect(headers(calls[0])["Idempotency-Key"]).toBe(review.key);
    expect(headers(calls[0])["X-CSRF-Token"]).toBe(TOKEN);
    expect(moves.events).toEqual(["drop key", "reload list", "show"]);
    expect(moves.states).toEqual([shown(APPROVED)]);
  });

  // 验收第 4 条「409 refund_already_reviewed 与 403 重新取详情并显示 common.error_retry」：都丢弃该键、不让列表重新查询，
  // 重新取到的详情（已审核、只读）与 common.error_retry 一起显示。
  it.each([json(409, { detail: "refund_already_reviewed", status: "rejected" }), json(403, { detail: "csrf_failed" })])(
    "reloads the detail and shows common.error_retry (%#)",
    async (reply) => {
      const calls = stubFetch(reply, json(200, REJECTED));
      const moves = fakeMoves();
      await submitReview("9", REQUESTED, attempt("reject", REASON), "en", new AbortController().signal, moves.target);
      expect(requests(calls)).toEqual(["POST /api/admin/refunds/9/reject", "GET /api/admin/refunds/9?lang=en"]);
      expect(moves.events).toEqual(["drop key", "show"]);
      expect(moves.states).toEqual([shown(REJECTED, "common.error_retry")]);
    },
  );

  // 验收第 4 条「409 idempotency_conflict 与 422 显示 common.error_retry 并丢弃该键」：不重新读取，保留当前详情；其他失败同样处理。
  it.each([json(409, { detail: "idempotency_conflict" }), json(422, { detail: "reason_invalid" }), json(500, {}), json(409, { detail: "other" })])(
    "shows common.error_retry and drops the key (%#)",
    async (reply) => {
      const calls = stubFetch(reply);
      const moves = fakeMoves();
      await submitReview("9", REQUESTED, attempt("reject", REASON), "en", new AbortController().signal, moves.target);
      expect(calls).toHaveLength(1);
      expect(moves.events).toEqual(["drop key", "show"]);
      expect(moves.kept).toEqual([null]);
      expect(moves.states).toEqual([shown(REQUESTED, "common.error_retry")]);
    },
  );

  // 验收第 4 条「网络中断显示 common.network_check」与「网络中断后再点…沿用同一个键」：不重新读取，保留当前详情与该键。
  it("shows common.network_check and keeps the key when the network is down", async () => {
    const calls = stubFetch(networkDown);
    const moves = fakeMoves();
    const review = attempt("reject", REASON);
    await submitReview("9", REQUESTED, review, "en", new AbortController().signal, moves.target);
    expect(calls).toHaveLength(1);
    expect(moves.events).toEqual(["keep key", "show"]);
    expect(moves.kept).toEqual([review]);
    expect(moves.states).toEqual([shown(REQUESTED, "common.network_check")]);
    expect(keptAttempt("network", review)).toBe(review);
    for (const reply of ["done", "reviewed", "conflict", "csrf", "invalid", "none", "failed"] as const) {
      expect(keptAttempt(reply, review)).toBeNull();
    }
  });

  // 验收第 4 条「401 用路由的 replace 进入 /admin/login」：审核与审核之后的重新读取遇到 401 都进入登录页，不显示详情。
  it.each<[Response[], string[]]>([
    [[json(401, { detail: "admin_session_required" })], ["drop key", `replace ${LOGIN_PATH}`]],
    [[json(200, {}), json(401, { detail: "admin_session_required" })], ["drop key", "reload list", `replace ${LOGIN_PATH}`]],
    [[json(403, { detail: "csrf_failed" }), json(401, { detail: "admin_session_required" })], ["drop key", `replace ${LOGIN_PATH}`]],
  ])("goes to the login page on 401 (%#)", async (replies, events) => {
    stubFetch(...replies);
    const moves = fakeMoves();
    await submitReview("9", REQUESTED, attempt("approve", ""), "en", new AbortController().signal, moves.target);
    expect(moves.events).toEqual(events);
    expect(moves.states).toEqual([]);
  });

  // 派生实现约束（重新读取失败时）：审核 200 后重新读取遇到网络中断或其他失败时保留原来的详情并提示；申请已不存在时显示不存在。
  it.each<[Response | (() => never), DetailState]>([
    [networkDown, shown(REQUESTED, "common.network_check")],
    [json(500, {}), shown(REQUESTED, "common.error_retry")],
    [json(404, { detail: "not_found" }), { status: "missing" }],
  ])("handles a failed reload (%#)", async (reread, state) => {
    stubFetch(json(200, {}), reread);
    const moves = fakeMoves();
    await submitReview("9", REQUESTED, attempt("approve", ""), "en", new AbortController().signal, moves.target);
    expect(moves.states).toEqual([state]);
  });

  // 验收第 4 条「每次新的审核生成新的幂等键（crypto.randomUUID），网络中断后再点且理由与操作都没变时沿用同一个键…得到确定回答后丢弃该键」：
  // 按页面的做法串起几次点击——网络中断后以同一理由再点沿用同一个键；得到 200 后丢弃，之后的审核换新；409 idempotency_conflict
  // 之后丢弃，再点换新；网络中断后改了理由或换了操作也换新，再以同样的内容重试时沿用新键。
  it("reuses the key only for a retry after a network failure", async () => {
    const newKey = keys();
    let kept: ReviewAttempt | null = null;
    const click = async (action: ReviewAction, reason: string) => {
      const review = reviewAttempt(kept, action, reason, newKey);
      kept = review;
      const moves = fakeMoves();
      moves.target.keep = (next) => {
        kept = next;
      };
      await submitReview("9", REQUESTED, review, "en", new AbortController().signal, moves.target);
    };
    const calls = stubFetch(
      networkDown,
      networkDown,
      json(200, {}),
      json(200, REJECTED),
      json(409, { detail: "idempotency_conflict" }),
      networkDown,
      networkDown,
      networkDown,
      json(200, {}),
      json(200, REJECTED),
    );
    await click("reject", REASON);
    await click("reject", REASON);
    await click("reject", REASON);
    await click("reject", REASON);
    await click("reject", REASON);
    await click("reject", `${REASON} More detail.`);
    await click("approve", `${REASON} More detail.`);
    await click("approve", `${REASON} More detail.`);
    const posts = calls.filter((call) => call.init.method === "POST").map((call) => headers(call)["Idempotency-Key"]);
    expect(posts).toEqual([
      "review-key-0001",
      "review-key-0001",
      "review-key-0001",
      "review-key-0002",
      "review-key-0003",
      "review-key-0004",
      "review-key-0005",
      "review-key-0005",
    ]);
    expect(calls.filter((call) => call.init.method === "POST").map((call) => call.url.split("/").at(-1))).toEqual([
      "reject",
      "reject",
      "reject",
      "reject",
      "reject",
      "reject",
      "approve",
      "approve",
    ]);
    expect(kept).toBeNull();
  });

  // 验收第 5 条「离开页面或换到另一笔申请时中止旧请求，旧请求的结果不再更新页面」：signal 中止之后，审核与重新读取的结果都不显示、
  // 不跳转，列表也不重新查询。
  it.each([json(200, {}), json(401, {}), json(409, { detail: "refund_already_reviewed" })])("does nothing after leaving the page (%#)", async (reply) => {
    stubFetch(reply, json(200, APPROVED));
    const moves = fakeMoves();
    const controller = new AbortController();
    const pending = submitReview("9", REQUESTED, attempt("approve", ""), "en", controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events.filter((event) => event !== "drop key")).toEqual([]);
  });
});

describe("reviewed requests", () => {
  // UX A03「状态与补充（0.9）」「已批准或已拒绝的申请只读：不显示理由输入框、审核按钮与 [admin.refund_hint]，显示状态标签，
  // 金额之后显示 [admin.reviewed_by]…批准时没写理由则不显示」与验收第 5 条「{username} 为 reviewer_username…{time} 为 reviewed_at 经 formatDateTime」：
  // 已批准、没写理由时金额之后只有 admin.reviewed_by 一行。
  it.each(LANGUAGES)("shows an approved request read-only in %s", (language) => {
    const html = render({ state: shown(APPROVED), reason: "draft" }, language);
    expect(html).not.toMatch(/<(textarea|button|label)\b/);
    expect(html).not.toContain("site-admin-refund__actions");
    expect(html).not.toContain(escapeHtml(COPY["admin.refund_hint"][language]));
    expect(html).not.toContain(escapeHtml(COPY["admin.refund_reason"][language]));
    const blocks = elements(html, "div", "site-admin-refund__block");
    expect(textNodes(blocks[1] ?? "")).toEqual(amountLines(APPROVED, language));
    expect(textNodes(blocks[2] ?? "")).toEqual([reviewedBy(APPROVED, language)]);
    expect(reviewedBy(APPROVED, "en")).toBe(`Reviewed by ${REVIEWER} on ${formatDateTime(APPROVED.reviewed_at ?? "", "en")}`);
  });

  // 同一条「有理由时其下显示 [admin.refund_reason] 与理由原文」（视觉稿 A03-phone-detail-rejected）：已拒绝时 admin.reviewed_by 之下为
  // admin.refund_reason 与理由原文；同样没有输入框、按钮与提示。
  it.each(LANGUAGES)("shows a rejected request with its reason in %s", (language) => {
    const html = render({ state: shown(REJECTED) }, language);
    expect(html).not.toMatch(/<(textarea|button)\b/);
    expect(html).not.toContain(escapeHtml(COPY["admin.refund_hint"][language]));
    const blocks = elements(html, "div", "site-admin-refund__block");
    expect(textNodes(blocks[2] ?? "")).toEqual([reviewedBy(REJECTED, language), COPY["admin.refund_reason"][language], REASON]);
    // 已批准而写了理由时同样显示。
    const approvedWithReason = render({ state: shown({ ...APPROVED, review_reason: "Item returned unused." }) }, language);
    expect(textNodes(elements(approvedWithReason, "div", "site-admin-refund__block")[2] ?? "")).toEqual([
      reviewedBy(APPROVED, language),
      COPY["admin.refund_reason"][language],
      "Item returned unused.",
    ]);
  });
});

describe("opening the detail", () => {
  // 验收第 5 条「读取详情 401 用路由的 replace 进入 /admin/login，网络中断显示 common.network_check，其他失败 common.error_retry」与
  // 「申请不存在（404）…详情位置显示 common.error_retry 与 common.back」（Kelvin 2026-10-07 决定）。
  it("chooses what to show from the reply", () => {
    expect(INITIAL_DETAIL).toEqual({ status: "loading" });
    expect(detailStep({ kind: "ok", refund: REQUESTED })).toEqual(shown(REQUESTED));
    expect(detailStep({ kind: "none" })).toBe("login");
    expect(detailStep({ kind: "missing" })).toEqual({ status: "missing" });
    expect(detailStep({ kind: "network" })).toEqual({ status: "failed", error: "common.network_check" });
    expect(detailStep({ kind: "failed" })).toEqual({ status: "failed", error: "common.error_retry" });
  });

  // 同一条：经接口读取一次（地址带内部 ID 与界面语言，带中止用的 signal），按回答显示或进入登录页。
  it.each<[Response | (() => never), string[], DetailState | null]>([
    [json(200, REQUESTED), ["show"], shown(REQUESTED)],
    [json(404, { detail: "not_found" }), ["show"], { status: "missing" }],
    [json(401, { detail: "admin_session_required" }), [`replace ${LOGIN_PATH}`], null],
    [networkDown, ["show"], { status: "failed", error: "common.network_check" }],
    [json(500, {}), ["show"], { status: "failed", error: "common.error_retry" }],
    [json(200, { ...REQUESTED, status: "pending" }), ["show"], { status: "failed", error: "common.error_retry" }],
  ])("opens the detail from the reply (%#)", async (reply, events, state) => {
    const calls = stubFetch(reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    await openDetail("9", "ms", controller.signal, moves.target);
    expect(requests(calls)).toEqual(["GET /api/admin/refunds/9?lang=ms"]);
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(moves.events).toEqual(events);
    if (state !== null) {
      expect(moves.states).toEqual([state]);
    }
  });

  // 验收第 3 条「路径 ID 不是不带符号与前导零的正整数时不发请求」与第 5 条「路径 ID 不合法时详情位置显示 common.error_retry 与 common.back」：
  // /admin/refunds/order（段值 order）等不合法 ID 不发请求，按不存在显示。
  it.each(["order", "0", "09", "-9", "9.0", "2147483648"])("shows the missing state for the refund ID %j without a request", async (id) => {
    const calls = stubFetch();
    const moves = fakeMoves();
    await openDetail(id, "en", new AbortController().signal, moves.target);
    expect(calls).toEqual([]);
    expect(moves.states).toEqual([{ status: "missing" }]);
  });

  // 同一条（Kelvin 2026-10-07「不新增文案」）：不存在与读取失败时详情区只有返回链接（common.back，指向打开前的列表网址）与提示，
  // 没有标题、商品、金额或按钮；读取中标 aria-busy，只有返回链接。
  it.each(LANGUAGES)("shows only the back link and the message when there is no detail in %s", (language) => {
    const cases: [DetailState, CopyKey | null][] = [
      [{ status: "missing" }, "common.error_retry"],
      [{ status: "failed", error: "common.network_check" }, "common.network_check"],
      [{ status: "failed", error: "common.error_retry" }, "common.error_retry"],
      [INITIAL_DETAIL, null],
    ];
    for (const [state, message] of cases) {
      const html = render({ state, backTo: ORDER_LIST_PATH }, language);
      const section = tags(html, "section")[0] ?? "";
      expect(attributes(section).get("aria-busy")).toBe(state.status === "loading" ? "true" : "false");
      const links = all(html, "a");
      expect(links).toHaveLength(1);
      expect(attributes(tags(links[0] ?? "", "a")[0] ?? "").get("href")).toBe(ORDER_LIST_PATH);
      expect(classes(tags(links[0] ?? "", "a")[0] ?? "")).toEqual(["site-admin-refund__back"]);
      expect(textNodes(links[0] ?? "")).toEqual([COPY["common.back"][language]]);
      expect(html).not.toMatch(/<(h2|button|textarea)\b/);
      expect(textNodes(html)).toEqual(message === null ? [COPY["common.back"][language]] : [COPY["common.back"][language], COPY[message][language]]);
      if (message !== null) {
        expect(html).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[message][language])}</span></div>`);
      }
    }
  });

  // 验收第 2 条「返回链接回到当时的列表网址（/admin/refunds 或 /admin/refunds/order/<内部 ID>）」：已取到详情时返回链接同样在顶部，指向给定的列表网址。
  it("points the back link to the list the detail was opened from", () => {
    for (const backTo of [REFUNDS_PATH, ORDER_LIST_PATH] as const) {
      const back = tags(render({ backTo }), "a").find((tag) => classes(tag).includes("site-admin-refund__back")) ?? "";
      expect(attributes(back).get("href")).toBe(backTo);
    }
  });

  // 验收第 5 条「离开页面或换到另一笔申请时中止旧请求，旧请求的结果不再更新页面」：signal 中止后不显示也不跳转。
  it.each([json(200, REQUESTED), json(401, {}), json(404, {})])("ignores the reply after leaving the page (%#)", async (reply) => {
    stubFetch(reply);
    const moves = fakeMoves();
    const controller = new AbortController();
    const pending = openDetail("9", "en", controller.signal, moves.target);
    controller.abort();
    await pending;
    expect(moves.events).toEqual([]);
  });
});

describe("privacy", () => {
  // 验收第 5 条「理由、审核人邮箱、令牌与幂等键只在页面内存与请求里，不进网址、浏览器存储或日志」：读取、拒绝（网络中断后重试）与重新读取的
  // 地址只有内部 ID 与 lang 或操作名；理由只在请求体，令牌与幂等键只在请求头；页面上的链接也不含它们；不写 localStorage、sessionStorage、
  // cookie 或历史记录，console 的各方法都没有被调用（所以没有收到它们）。
  it("keeps the reason, the reviewer, the token and the key out of addresses, storage and the console", async () => {
    const browser = stubBrowser();
    const calls = stubFetch(json(200, REQUESTED), networkDown, json(200, {}), json(200, REJECTED));
    const moves = fakeMoves();
    const signal = new AbortController().signal;
    await openDetail("9", "en", signal, moves.target);
    const review = reviewAttempt(null, "reject", REASON, () => "review-key-secret-0001");
    await submitReview("9", REQUESTED, review, "en", signal, moves.target);
    await submitReview("9", REQUESTED, reviewAttempt(review, "reject", REASON), "en", signal, moves.target);
    expect(requests(calls)).toEqual([
      "GET /api/admin/refunds/9?lang=en",
      "POST /api/admin/refunds/9/reject",
      "POST /api/admin/refunds/9/reject",
      "GET /api/admin/refunds/9?lang=en",
    ]);
    const secrets = [REASON, REVIEWER, TOKEN, NEW_TOKEN, review.key, encodeURIComponent(REASON), encodeURIComponent(REVIEWER)];
    for (const call of calls) {
      for (const secret of secrets) {
        expect(call.url).not.toContain(secret);
      }
    }
    expect(body(calls[1])).toEqual({ reason: REASON });
    expect(headers(calls[2])["Idempotency-Key"]).toBe(review.key);
    expect(moves.states).toEqual([shown(REQUESTED), shown(REQUESTED, "common.network_check"), shown(REJECTED)]);
    for (const target of [browser.local, browser.session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(browser.cookieWrites).toEqual([]);
    expect(browser.history.pushState).not.toHaveBeenCalled();
    expect(browser.history.replaceState).not.toHaveBeenCalled();
    for (const log of browser.logs) {
      expect(log).not.toHaveBeenCalled();
    }
  });

  // 同一条：页面标记里没有令牌；链接只有返回列表与 A02 订单详情两个，地址里没有理由、审核人邮箱或订单号。
  // 理由与审核人邮箱只作为文字显示给已登录的管理员（HANDOFF 0.37「只向已登录的管理员显示（会话接口、A03 的审核人）」）。
  it("does not put the token, the reason or the reviewer into the markup's links", () => {
    for (const state of [shown(REQUESTED), shown(REJECTED)]) {
      const html = render({ state, reason: REASON, backTo: ORDER_LIST_PATH });
      expect(html).not.toContain(TOKEN);
      expect(html).not.toContain(NEW_TOKEN);
      const hrefs = [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "");
      expect(hrefs).toEqual([ORDER_LIST_PATH, "/admin/orders/42"]);
    }
    expect(textNodes(render({ state: shown(REJECTED) }))).toContain(REASON);
  });
});

describe("dictionary", () => {
  // 验收第 6 条「页面文字全部来自字典」与第 1 条「需要 UX-COPY 里没有的界面文字时停下…不自行编写文案」：审核中（含提示、进行中）、
  // 已批准、已拒绝、不存在、读取失败与读取中，每段文字（含 aria-label）都是当前语言的字典文案（含填入变量后的 admin.refund_qty、金额与
  // admin.reviewed_by），或接口给的订单号、商品名称、规格与理由原文、输入框里的理由；此外只有视觉稿给定的符号 · 与 /。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const refund of [REQUESTED, APPROVED, REJECTED]) {
      allowed.add(refund.order_number);
      for (const line of refund.lines) {
        allowed.add(line.name);
        allowed.add(line.variant_label);
        allowed.add(qty(line, language));
      }
      for (const text of amountLines(refund, language)) {
        allowed.add(text);
      }
      if (refund.reviewer_username !== null) {
        allowed.add(reviewedBy(refund, language));
      }
    }
    for (const value of [REASON, "·", "/"]) {
      allowed.add(value);
    }
    const states: DetailState[] = [
      shown(REQUESTED),
      shown(REQUESTED, "common.error_retry"),
      shown(REQUESTED, "common.network_check", true),
      shown(APPROVED),
      shown(REJECTED),
      { status: "missing" },
      { status: "failed", error: "common.network_check" },
      INITIAL_DETAIL,
    ];
    for (const state of states) {
      for (const reason of ["", REASON]) {
        const html = render({ state, reason }, language);
        expect(html).not.toMatch(/\s(title|placeholder)=/i);
        for (const value of visibleTexts(html)) {
          expect(allowed.has(value), value).toBe(true);
        }
      }
    }
  });
});
