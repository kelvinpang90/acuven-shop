import { afterEach, describe, expect, it, vi } from "vitest";

import { LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { ADMIN_REFUNDS_QUERY_URL, adminRefundsQueryUrl, queryAdminRefunds, REFUND_STATUSES } from "./adminRefunds";
import type { RefundsQuery } from "./adminRefunds";

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

// fetch 替身：给出一个回答（函数则抛出网络错误），记录请求。
function stubFetch(reply: Response | (() => never)) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
      return typeof reply === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
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
