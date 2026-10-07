import { afterEach, describe, expect, it, vi } from "vitest";

import { LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import {
  ADMIN_REFUNDS_QUERY_URL,
  adminRefundReviewUrl,
  adminRefundsQueryUrl,
  adminRefundUrl,
  parseRefundId,
  queryAdminRefunds,
  readAdminRefund,
  REFUND_STATUSES,
  reviewAdminRefund,
  reviewAttempt,
} from "./adminRefunds";
import type { AdminRefundDetail, RefundsQuery, ReviewAction } from "./adminRefunds";
import { CSRF_HEADER, IDEMPOTENCY_HEADER } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDER_NUMBER = "H3PZ-4W8C";
const ROW = {
  id: 7,
  created_at: "2026-10-01T10:40:00Z",
  order_id: 42,
  order_number: ORDER_NUMBER,
  status: "requested",
  amount_sen: 3400,
  lines: [
    { name: "Crew Neck Tee", quantity: 1 },
    { name: "Soy Wax Candle", quantity: 2 },
  ],
};
const LIST = { total: 1, page: 1, page_size: 20, refunds: [ROW] };
const ALL: RefundsQuery = { status: null, order_id: null, page: 1 };

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

function body(call: Call | undefined): unknown {
  return JSON.parse(call?.init.body as string);
}

describe("querying the admin refund list", () => {
  // SHOP-TASK-056 验收第 3 条「调用 POST /api/admin/refunds/query（lang 为当前界面语言，JSON 请求体只有 status、order_id 与 page；
  // 同源 cookie，cache 为 no-store）」：地址为固定路径加 lang（唯一的查询参数，SHOP-TASK-041），凭同源 cookie、不缓存；
  // 请求体恰好是这三个字段（不筛选时为 null），并带上中止用的 signal。
  it.each<[RefundsQuery, Language]>([
    [ALL, "en"],
    [{ status: "approved", order_id: null, page: 3 }, "zh"],
    [{ status: "rejected", order_id: 42, page: 2 }, "ms"],
    [{ status: null, order_id: 2147483647, page: 1 }, "en"],
  ])("posts only the status, the order ID and the page (%#)", async (query, language) => {
    const calls = stubFetch(json(200, LIST));
    const controller = new AbortController();
    await queryAdminRefunds(query, language, controller.signal);
    expect(ADMIN_REFUNDS_QUERY_URL).toBe("/api/admin/refunds/query");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/refunds/query?lang=${language}`);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.signal).toBe(controller.signal);
    expect(body(calls[0])).toEqual({ status: query.status, order_id: query.order_id, page: query.page });
    expect(Object.keys(body(calls[0]) as object).sort()).toEqual(["order_id", "page", "status"]);
  });

  // 同一条「lang 为当前界面语言」：三种界面语言各自成为 lang 的值，地址里没有别的查询参数。
  it.each(LANGUAGES)("asks for the names in %s", (language) => {
    const url = new URL(adminRefundsQueryUrl(language), "http://localhost");
    expect(url.pathname).toBe(ADMIN_REFUNDS_QUERY_URL);
    expect([...url.searchParams.entries()]).toEqual([["lang", language]]);
  });

  // SHOP-TASK-056 验收第 5 条「订单内部 ID 只在路径里，订单号只在页面内存里，不进查询参数或浏览器存储」与 UX A03 [K]
  // （DESIGN「权限与资料保护」）：查询的地址只有 lang，订单内部 ID 只在请求体里；查询过程不写 localStorage、sessionStorage、cookie 或历史记录。
  it("keeps the order ID and the order number out of the address and browser storage", async () => {
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

    const calls = stubFetch(json(200, LIST));
    await queryAdminRefunds({ status: null, order_id: 42, page: 1 }, "en");
    expect(calls[0]?.url).toBe("/api/admin/refunds/query?lang=en");
    expect(calls[0]?.url).not.toContain("42");
    expect(calls[0]?.url).not.toContain(ORDER_NUMBER);
    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
  });

  // SHOP-TASK-056 验收第 3 条「只取 SHOP-TASK-041 记录段列出的列表字段」：total、page、page_size 与每行的 id、created_at、order_id、
  // order_number、status、amount_sen 与 lines（每行 name、quantity）；响应里多出的字段（含行与商品行里的）不带进结果。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, {
        ...LIST,
        extra: "ignored",
        refunds: [{ ...ROW, review_reason: "secret", recipient: { name: "Aminah" }, lines: ROW.lines.map((line) => ({ ...line, variant_label: "White, L" })) }],
      }),
    );
    await expect(queryAdminRefunds(ALL, "en")).resolves.toEqual({ kind: "ok", list: LIST });
  });

  // 同一条「响应体不合格算失败；结果归为 ok、none（401）、failed 与 network」：401 为没有会话；其他状态、不合格的 200 响应体与网络中断分开报告。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "forbidden" }), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [new Response(null, { status: 204 }), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [json(200, null), "failed"],
    [json(200, { page: 1, page_size: 20, refunds: [] }), "failed"],
    [json(200, { ...LIST, total: -1 }), "failed"],
    [json(200, { ...LIST, total: 1.5 }), "failed"],
    [json(200, { ...LIST, page: 0 }), "failed"],
    [json(200, { ...LIST, page_size: 0 }), "failed"],
    [json(200, { ...LIST, refunds: null }), "failed"],
    [json(200, { total: 1, page: 1, page_size: 20, orders: [ROW] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, id: "7" }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, id: 0 }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, created_at: "yesterday" }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, created_at: null }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, order_id: null }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, order_id: "42" }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, order_number: 7 }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, status: "pending" }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, amount_sen: 34.5 }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, amount_sen: -1 }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, lines: null }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, lines: ["Crew Neck Tee"] }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, lines: [{ name: 7, quantity: 1 }] }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, lines: [{ name: "Crew Neck Tee", quantity: 0 }] }] }), "failed"],
    [json(200, { ...LIST, refunds: [{ ...ROW, lines: [{ name: "Crew Neck Tee" }] }] }), "failed"],
    [json(200, { ...LIST, refunds: [ROW, "row"] }), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(queryAdminRefunds(ALL, "en")).resolves.toEqual({ kind });
  });

  // docs/UX.md A03 状态筛选「[order.refund_requested] / [order.refund_approved] / [order.refund_rejected]」与 DESIGN「订单与退款状态」
  // （requested → approved 或 rejected）：三种申请状态，每种都是合格的行状态。
  it.each(REFUND_STATUSES)("accepts the refund status %s", async (status) => {
    stubFetch(json(200, { ...LIST, refunds: [{ ...ROW, status }] }));
    await expect(queryAdminRefunds({ status, order_id: null, page: 1 }, "en")).resolves.toEqual({
      kind: "ok",
      list: { ...LIST, refunds: [{ ...ROW, status }] },
    });
    expect(REFUND_STATUSES).toEqual(["requested", "approved", "rejected"]);
  });

  // SHOP-TASK-046 记录段「订单不存在或没有申请时返回 total 为 0 的空列表，不是 404」：空的一页是合格的结果（页面按「没有结果」显示）。
  it("accepts an empty page", async () => {
    stubFetch(json(200, { total: 0, page: 1, page_size: 20, refunds: [] }));
    await expect(queryAdminRefunds({ status: null, order_id: 99, page: 1 }, "en")).resolves.toEqual({
      kind: "ok",
      list: { total: 0, page: 1, page_size: 20, refunds: [] },
    });
  });
});

const TOKEN = "csrf-token-for-admin";
const KEY = "0f8e7d6c-5b4a-4321-8fed-cba987654321";
const REASON = "The returned hoodie shows signs of use.";
const REVIEWER = "kelvin@example.com";

// 审核中的申请详情（SHOP-TASK-041 记录段的全部字段）。
const DETAIL: AdminRefundDetail = {
  id: 9,
  status: "requested",
  created_at: "2026-10-02T02:15:00Z",
  reviewed_at: null,
  reviewer_username: null,
  review_reason: null,
  order_id: 42,
  order_number: ORDER_NUMBER,
  order_status: "demo_shipped",
  amount_sen: 2763,
  lines: [
    { name: "Crew Neck Tee", variant_label: "White, L", quantity: 1, purchased_quantity: 2, approved_quantity: 0 },
    { name: "Soy Wax Candle", variant_label: "", quantity: 1, purchased_quantity: 1, approved_quantity: 1 },
  ],
  refunded_sen: 3000,
  refundable_left_sen: 9500,
  csrf_token: TOKEN,
};

// 已拒绝的申请：有审核时间、审核人邮箱与理由。
const REJECTED_DETAIL: AdminRefundDetail = {
  ...DETAIL,
  status: "rejected",
  reviewed_at: "2026-10-03T10:02:00Z",
  reviewer_username: REVIEWER,
  review_reason: REASON,
};

function headers(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}

// 把 localStorage、sessionStorage、document.cookie、history 与 console 换成替身，返回检查用的记录。
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

describe("reading a refund request", () => {
  // SHOP-TASK-057 验收第 3 条「读取详情（GET /api/admin/refunds/{内部 ID}，lang 为当前界面语言）」：地址为申请的内部 ID 加 lang
  // （唯一的查询参数），GET、同源 cookie、不缓存、没有请求体，并带上中止用的 signal。
  it.each(LANGUAGES)("gets the detail by internal ID in %s", async (language) => {
    const calls = stubFetch(json(200, DETAIL));
    const controller = new AbortController();
    await readAdminRefund("9", language, controller.signal);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/refunds/9?lang=${language}`);
    expect(adminRefundUrl(9, language)).toBe(`/api/admin/refunds/9?lang=${language}`);
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.body).toBeUndefined();
    expect(calls[0]?.init.signal).toBe(controller.signal);
  });

  // 同一条「路径 ID 不是不带符号与前导零的正整数时不发请求」（SHOP-TASK-041 记录段「只接受不带符号与前导零、不超过 2147483647 的十进制整数」）：
  // 这些段值都不合法，详情归为不存在（页面显示 common.error_retry 与 common.back），审核归为失败，都不发请求。
  it.each(["0", "00", "09", "+9", "-1", "4.2", "1e3", " 9", "9 ", "order", "", "2147483648", "99999999999999999999", "９"])(
    "sends nothing for the refund ID %j",
    async (value) => {
      const calls = stubFetch(json(200, DETAIL));
      expect(parseRefundId(value)).toBeNull();
      await expect(readAdminRefund(value, "en")).resolves.toEqual({ kind: "missing" });
      await expect(reviewAdminRefund(value, "approve", "", TOKEN, KEY)).resolves.toBe("failed");
      expect(calls).toEqual([]);
    },
  );

  it("accepts plain positive refund IDs", () => {
    expect(parseRefundId("1")).toBe(1);
    expect(parseRefundId("9")).toBe(9);
    expect(parseRefundId("2147483647")).toBe(2147483647);
  });

  // 同一条「详情只取 SHOP-TASK-041 记录段列出的字段」：响应里多出的字段（含商品行里的）不带进结果；审核中、已批准（没写理由）与已拒绝都合格。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, {
        ...DETAIL,
        recipient: { name: "Aminah", phone: "+60123456789" },
        points_returned: 146,
        lines: DETAIL.lines.map((line) => ({ ...line, unit_cash_paid_sen: [2763] })),
      }),
    );
    await expect(readAdminRefund("9", "en")).resolves.toEqual({ kind: "ok", refund: DETAIL });
    for (const refund of [REJECTED_DETAIL, { ...REJECTED_DETAIL, status: "approved", review_reason: null } as AdminRefundDetail]) {
      stubFetch(json(200, refund));
      await expect(readAdminRefund("9", "en")).resolves.toEqual({ kind: "ok", refund });
    }
  });

  // 同一条「响应体不合格算失败」与验收第 5 条「申请不存在（404）…读取详情 401…网络中断…其他失败」：401 为没有会话，404 为不存在；
  // 其他状态、各种不合格的 200 响应体与网络中断分开报告。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(404, { detail: "not_found" }), "missing"],
    [json(403, { detail: "forbidden" }), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [new Response(null, { status: 204 }), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [json(200, null), "failed"],
    [json(200, [DETAIL]), "failed"],
    [json(200, { ...DETAIL, id: "9" }), "failed"],
    [json(200, { ...DETAIL, id: 0 }), "failed"],
    [json(200, { ...DETAIL, status: "pending" }), "failed"],
    [json(200, { ...DETAIL, created_at: "yesterday" }), "failed"],
    [json(200, { ...DETAIL, reviewed_at: "2026-10-03T10:02:00Z" }), "failed"],
    [json(200, { ...DETAIL, reviewer_username: REVIEWER }), "failed"],
    [json(200, { ...REJECTED_DETAIL, reviewed_at: null }), "failed"],
    [json(200, { ...REJECTED_DETAIL, reviewed_at: "later" }), "failed"],
    [json(200, { ...REJECTED_DETAIL, reviewer_username: null }), "failed"],
    [json(200, { ...REJECTED_DETAIL, reviewer_username: 7 }), "failed"],
    [json(200, { ...DETAIL, review_reason: 7 }), "failed"],
    [json(200, { ...DETAIL, order_id: null }), "failed"],
    [json(200, { ...DETAIL, order_id: "42" }), "failed"],
    [json(200, { ...DETAIL, order_number: 42 }), "failed"],
    [json(200, { ...DETAIL, order_status: "shipped" }), "failed"],
    [json(200, { ...DETAIL, amount_sen: 27.63 }), "failed"],
    [json(200, { ...DETAIL, amount_sen: -1 }), "failed"],
    [json(200, { ...DETAIL, refunded_sen: null }), "failed"],
    [json(200, { ...DETAIL, refundable_left_sen: -5 }), "failed"],
    [json(200, { ...DETAIL, csrf_token: null }), "failed"],
    [json(200, { ...DETAIL, lines: null }), "failed"],
    [json(200, { ...DETAIL, lines: ["Crew Neck Tee"] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], name: 7 }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], variant_label: null }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], quantity: 0 }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], purchased_quantity: 0 }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], approved_quantity: -1 }] }), "failed"],
    [json(200, { ...DETAIL, lines: [{ ...DETAIL.lines[0], approved_quantity: undefined }] }), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminRefund("9", "en")).resolves.toEqual({ kind });
  });
});

describe("reviewing a refund request", () => {
  // SHOP-TASK-057 验收第 3 条「批准与拒绝（POST /api/admin/refunds/{内部 ID}/approve 与 /reject，JSON 请求体只有 reason，
  // 请求头 Idempotency-Key 与 X-CSRF-Token，沿用 api/pay.ts 的请求头名，令牌取详情返回值）」：地址按操作分为两个，没有查询参数；
  // 请求体恰好是 reason（输入框原文）；两个请求头为本次的幂等键与详情给的令牌；同源 cookie、不缓存。
  it.each<[ReviewAction, string]>([
    ["approve", ""],
    ["approve", "  Item returned unused.  "],
    ["reject", REASON],
  ])("posts only the reason with both headers to %s (%#)", async (action, reason) => {
    const calls = stubFetch(json(200, { refund_request_id: 9, status: "approved", reviewed_at: "2026-10-03T10:02:00Z", review_reason: null }));
    await expect(reviewAdminRefund("9", action, reason, TOKEN, KEY)).resolves.toBe("done");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/refunds/9/${action}`);
    expect(adminRefundReviewUrl(9, action)).toBe(`/api/admin/refunds/9/${action}`);
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(JSON.parse(calls[0]?.init.body as string)).toEqual({ reason });
    expect(Object.keys(JSON.parse(calls[0]?.init.body as string) as object)).toEqual(["reason"]);
    expect(IDEMPOTENCY_HEADER).toBe("Idempotency-Key");
    expect(CSRF_HEADER).toBe("X-CSRF-Token");
    expect(headers(calls[0])[IDEMPOTENCY_HEADER]).toBe(KEY);
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
  });

  // SHOP-TASK-057 验收第 4 条「200 后重新取详情与列表；409 refund_already_reviewed 与 403 重新取详情…；409 idempotency_conflict 与 422
  // 显示 common.error_retry 并丢弃该键；网络中断显示 common.network_check；401…进入 /admin/login」：回答按状态码（409 另按错误码）归类；
  // 别的 409、404 与其他状态为 failed。
  it.each<[Response | (() => never), string]>([
    [json(200, {}), "done"],
    [json(409, { detail: "refund_already_reviewed", status: "approved" }), "reviewed"],
    [json(409, { detail: "idempotency_conflict" }), "conflict"],
    [json(409, { detail: "something_else" }), "failed"],
    [new Response("not json", { status: 409 }), "failed"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: "reason_invalid" }), "invalid"],
    [json(422, { detail: [{ type: "missing", loc: ["header", "Idempotency-Key"], msg: "Field required" }] }), "invalid"],
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(404, { detail: "not_found" }), "failed"],
    [json(413, {}), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(reviewAdminRefund("9", "reject", REASON, TOKEN, KEY)).resolves.toBe(kind);
  });

  // 同一条「每次新的审核生成新的幂等键（crypto.randomUUID），网络中断后再点且理由与操作都没变时沿用同一个键」：
  // 没有保留的审核时新生成；操作与理由都相同时沿用；改了理由（含只改空白）或换了操作都换新。
  it("keeps the key only for the same action and reason", () => {
    let next = 0;
    const newKey = () => `key-${String((next += 1))}`;
    const first = reviewAttempt(null, "reject", REASON, newKey);
    expect(first).toEqual({ action: "reject", reason: REASON, key: "key-1" });
    expect(reviewAttempt(first, "reject", REASON, newKey)).toEqual(first);
    expect(reviewAttempt(first, "reject", `${REASON} `, newKey).key).toBe("key-2");
    expect(reviewAttempt(first, "reject", "Another reason", newKey).key).toBe("key-3");
    expect(reviewAttempt(first, "approve", REASON, newKey).key).toBe("key-4");
    expect(reviewAttempt(null, "reject", REASON, newKey).key).toBe("key-5");
  });

  // 同一条「用 crypto.randomUUID 生成」：不给生成函数时用浏览器的 crypto.randomUUID，且每次新的审核各调用一次。
  it("uses crypto.randomUUID by default", () => {
    const randomUUID = vi.fn(() => KEY);
    vi.stubGlobal("crypto", { randomUUID });
    expect(reviewAttempt(null, "approve", "", undefined).key).toBe(KEY);
    expect(randomUUID).toHaveBeenCalledTimes(1);
  });

  // SHOP-TASK-057 验收第 5 条「理由、审核人邮箱、令牌与幂等键只在页面内存与请求里，不进网址、浏览器存储或日志」：
  // 读取与审核的地址只有内部 ID 与 lang（或操作名）；理由只在请求体、令牌与幂等键只在请求头；整个过程不写 localStorage、sessionStorage、
  // cookie 或历史记录，console 的各方法都没有被调用。
  it("keeps the reason, the reviewer, the token and the key out of addresses, storage and the console", async () => {
    const browser = stubBrowser();
    const calls = stubFetch(json(200, REJECTED_DETAIL), json(200, {}), networkDown, json(409, { detail: "idempotency_conflict" }));
    await readAdminRefund("9", "zh");
    await reviewAdminRefund("9", "reject", REASON, TOKEN, KEY);
    await reviewAdminRefund("9", "reject", REASON, TOKEN, KEY);
    await reviewAdminRefund("9", "approve", REASON, TOKEN, KEY);
    expect(calls).toHaveLength(4);
    for (const call of calls) {
      for (const secret of [REASON, REVIEWER, TOKEN, KEY, ORDER_NUMBER, encodeURIComponent(REASON)]) {
        expect(call.url).not.toContain(secret);
      }
    }
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
});
