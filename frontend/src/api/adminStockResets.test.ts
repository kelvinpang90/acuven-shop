import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ADMIN_STOCK_RESETS_URL,
  adminStockResetsUrl,
  adminStockResetUrl,
  isBusinessDate,
  listStockResets,
  MAX_RESET_ID,
  readStockReset,
} from "./adminStockResets";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const SUCCEEDED = { id: 12, business_date: "2026-10-05", result: "succeeded", sku_count: 3, completed_at: "2026-10-04T16:00:04Z" };
const FAILED = { id: 11, business_date: "2026-10-04", result: "failed", sku_count: 0, completed_at: null };
const LIST = { total: 2, page: 1, page_size: 30, resets: [SUCCEEDED, FAILED] };
const LINES = [
  { sku: "CREW-NECK-TEE-BLACK-M", initial_stock: 20, held_quantity: 2, available_stock: 18 },
  { sku: "SOY-WAX-CANDLE", initial_stock: 0, held_quantity: 1, available_stock: 0 },
];
const DETAIL = { ...SUCCEEDED, lines: LINES };

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

describe("listing the stock resets", () => {
  // SHOP-TASK-060 验收第 2 条「调用 GET /api/admin/stock-resets?page=N…（同源 cookie，cache 为 no-store）」：地址为固定路径加 page
  // （唯一的查询参数），GET、凭同源 cookie、不缓存、没有请求体，并带上中止用的 signal。
  it.each([1, 2, 10000])("gets page %i with the same-origin cookie and no cache", async (page) => {
    const calls = stubFetch(json(200, LIST));
    const controller = new AbortController();
    await listStockResets(page, controller.signal);
    expect(ADMIN_STOCK_RESETS_URL).toBe("/api/admin/stock-resets");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`/api/admin/stock-resets?page=${String(page)}`);
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.body).toBeUndefined();
    expect(calls[0]?.init.signal).toBe(controller.signal);
    const url = new URL(adminStockResetsUrl(page), "http://localhost");
    expect([...url.searchParams.entries()]).toEqual([["page", String(page)]]);
  });

  // SHOP-TASK-060 验收第 2 条「只取 SHOP-TASK-052 记录段列出的字段」（total、page、page_size 与每项 id、business_date、result、
  // sku_count、completed_at）：响应里多出的字段（含每项里的尝试次数与异常类名）不带进结果。
  it("keeps only the agreed fields", async () => {
    stubFetch(json(200, { ...LIST, extra: "ignored", resets: [{ ...SUCCEEDED, attempts: 2, error_class: "OperationalError" }, FAILED] }));
    await expect(listStockResets(1)).resolves.toEqual({ kind: "ok", list: LIST });
  });

  // 同一条（SHOP-TASK-052「超出的页 resets 为空、total 照常」）：空页合格。
  it("accepts an empty page", async () => {
    const empty = { total: 0, page: 1, page_size: 30, resets: [] };
    stubFetch(json(200, empty));
    await expect(listStockResets(1)).resolves.toEqual({ kind: "ok", list: empty });
  });

  // SHOP-TASK-060 验收第 2 条「结果归为 ok、none（401）、failed 与 network」：401 为 none，其他状态为 failed，fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [json(404, { detail: "not_found" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(listStockResets(1)).resolves.toEqual({ kind });
  });

  // SHOP-TASK-060 验收第 2 条「响应体不合格算失败」：计数不是非负整数、页码或每页条数不是正整数、resets 不是数组、
  // 任一项字段缺失或类型不对、营业日期不是存在的 YYYY-MM-DD、结果不是 succeeded / failed、完成时间既不是 null 也解析不出时间，都算 failed。
  it.each<[string, unknown]>([
    ["not an object", [LIST]],
    ["null body", null],
    ["negative total", { ...LIST, total: -1 }],
    ["fractional total", { ...LIST, total: 1.5 }],
    ["page zero", { ...LIST, page: 0 }],
    ["page as string", { ...LIST, page: "1" }],
    ["page size zero", { ...LIST, page_size: 0 }],
    ["resets missing", { total: 0, page: 1, page_size: 30 }],
    ["resets not an array", { ...LIST, resets: {} }],
    ["row not an object", { ...LIST, resets: [null] }],
    ["id zero", { ...LIST, resets: [{ ...SUCCEEDED, id: 0 }] }],
    ["id as string", { ...LIST, resets: [{ ...SUCCEEDED, id: "12" }] }],
    ["date missing", { ...LIST, resets: [{ ...SUCCEEDED, business_date: undefined }] }],
    ["date with time", { ...LIST, resets: [{ ...SUCCEEDED, business_date: "2026-10-05T00:00:00Z" }] }],
    ["date in another format", { ...LIST, resets: [{ ...SUCCEEDED, business_date: "05/10/2026" }] }],
    ["date that does not exist", { ...LIST, resets: [{ ...SUCCEEDED, business_date: "2026-02-30" }] }],
    ["unknown result", { ...LIST, resets: [{ ...SUCCEEDED, result: "ok" }] }],
    ["result missing", { ...LIST, resets: [{ ...SUCCEEDED, result: undefined }] }],
    ["negative sku count", { ...LIST, resets: [{ ...SUCCEEDED, sku_count: -1 }] }],
    ["sku count as string", { ...LIST, resets: [{ ...SUCCEEDED, sku_count: "3" }] }],
    ["completed at missing", { ...LIST, resets: [{ ...SUCCEEDED, completed_at: undefined }] }],
    ["completed at unparsable", { ...LIST, resets: [{ ...SUCCEEDED, completed_at: "yesterday" }] }],
    ["completed at as number", { ...LIST, resets: [{ ...SUCCEEDED, completed_at: 0 }] }],
  ])("treats a malformed list as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(listStockResets(1)).resolves.toEqual({ kind: "failed" });
  });

  // 同一条：200 但响应体不是 JSON 也算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(listStockResets(1)).resolves.toEqual({ kind: "failed" });
  });
});

describe("reading one day", () => {
  // SHOP-TASK-060 验收第 2 条「调用 GET /api/admin/stock-resets/{内部 ID}（同源 cookie，cache 为 no-store）」：路径只带内部 ID，没有查询参数。
  it("gets the detail by internal ID", async () => {
    const calls = stubFetch(json(200, DETAIL));
    const controller = new AbortController();
    await readStockReset(12, controller.signal);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/admin/stock-resets/12");
    expect(adminStockResetUrl(MAX_RESET_ID)).toBe("/api/admin/stock-resets/2147483647");
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(calls[0]?.init.body).toBeUndefined();
    expect(calls[0]?.init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-060 验收第 2 条「只取 SHOP-TASK-052 记录段列出的字段」（列表的五个字段与 lines 的 sku、initial_stock、held_quantity、
  // available_stock）：多出的字段（含规格 ID 与异常类名）不带进结果；失败那天的空明细合格。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, { ...DETAIL, error_class: "OperationalError", lines: LINES.map((line) => ({ ...line, variant_id: 7 })) }),
      json(200, { ...FAILED, lines: [] }),
    );
    await expect(readStockReset(12)).resolves.toEqual({ kind: "ok", reset: DETAIL });
    await expect(readStockReset(11)).resolves.toEqual({ kind: "ok", reset: { ...FAILED, lines: [] } });
  });

  // SHOP-TASK-060 验收第 2 条「结果归为 ok、none（401）、failed 与 network」：明细的 404 也只是 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(404, { detail: "not_found" }), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readStockReset(12)).resolves.toEqual({ kind });
  });

  // SHOP-TASK-060 验收第 2 条「响应体不合格算失败」：列表那几个字段不合格、不是所请求的那一天、lines 缺失或不是数组、
  // 某行 SKU 不是字符串、三个数不是非负整数，都算 failed。
  it.each<[string, unknown]>([
    ["another day", { ...DETAIL, id: 13 }],
    ["bad date", { ...DETAIL, business_date: "2026-13-01" }],
    ["unknown result", { ...DETAIL, result: "partial" }],
    ["lines missing", SUCCEEDED],
    ["lines not an array", { ...DETAIL, lines: "none" }],
    ["line not an object", { ...DETAIL, lines: [1] }],
    ["sku missing", { ...DETAIL, lines: [{ ...LINES[0], sku: undefined }] }],
    ["sku as number", { ...DETAIL, lines: [{ ...LINES[0], sku: 7 }] }],
    ["negative initial", { ...DETAIL, lines: [{ ...LINES[0], initial_stock: -1 }] }],
    ["fractional held", { ...DETAIL, lines: [{ ...LINES[0], held_quantity: 0.5 }] }],
    ["available as string", { ...DETAIL, lines: [{ ...LINES[0], available_stock: "18" }] }],
    ["available missing", { ...DETAIL, lines: [{ ...LINES[0], available_stock: undefined }] }],
  ])("treats a malformed detail as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readStockReset(12)).resolves.toEqual({ kind: "failed" });
  });

  // 派生实现约束：不是不超过上限的正整数的 ID 不发请求，算失败。
  it.each([0, -1, 1.5, MAX_RESET_ID + 1, Number.NaN])("does not ask for the ID %d", async (id) => {
    const calls = stubFetch();
    await expect(readStockReset(id)).resolves.toEqual({ kind: "failed" });
    expect(calls).toEqual([]);
  });
});

describe("business dates", () => {
  // SHOP-TASK-052「business_date（YYYY-MM-DD，马来西亚营业日期）」：只有存在的 YYYY-MM-DD 合格（含闰日）。
  it("accepts only existing YYYY-MM-DD dates", () => {
    for (const value of ["2026-10-05", "2028-02-29", "2026-12-31", "2026-01-01"]) {
      expect(isBusinessDate(value), value).toBe(true);
    }
    for (const value of ["2026-02-29", "2026-04-31", "2026-00-10", "2026-1-5", "20261005", " 2026-10-05", "2026-10-05Z", "", 20261005, null]) {
      expect(isBusinessDate(value), String(value)).toBe(false);
    }
  });
});
