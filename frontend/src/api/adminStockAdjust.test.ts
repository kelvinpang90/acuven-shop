import { afterEach, describe, expect, it, vi } from "vitest";

import { adjustAdminAvailableStock } from "./adminStockAdjust";
import type { StockAdjust } from "./adminStockAdjust";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 成功的响应体（app/api/admin_stock_adjust.py 的 StockOut）。
const ADJUSTED = { variant_id: 31, sku: "TEE-RED-M", available_stock: 12 };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不在规格接口之下时拒绝。
// 参数带类型（地址只收字符串或 URL，String(…) 即得地址），请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined || !String(input).startsWith("/api/admin/variants/")) {
      return Promise.reject(new Error("unexpected request"));
    }
    return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

function sentUrl(fetchMock: FetchMock, index: number): string {
  return String(fetchMock.mock.calls[index]?.[0]);
}

function sentInit(fetchMock: FetchMock, index: number): RequestInit {
  return fetchMock.mock.calls[index]?.[1] ?? {};
}

function sentBody(fetchMock: FetchMock, index: number): unknown {
  const body = sentInit(fetchMock, index).body;
  if (typeof body !== "string") {
    throw new Error("request body is not a JSON string");
  }
  return JSON.parse(body) as unknown;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

const INPUT: StockAdjust = { expected: 10, available_stock: 12 };

describe("adjusting today's available stock", () => {
  // 接口记录「POST /api/admin/variants/{variant_id}/available-stock」「1. ……不是 JSON 415」「3. X-CSRF-Token（403 csrf_failed……）」与
  // 「全部响应（含错误）都带 Cache-Control: no-store」：地址为 SKU 规格 ID 的十进制路径段、不带查询参数，POST、凭同源 cookie、不缓存，
  // Accept 与 Content-Type 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it.each<[number, string]>([
    [31, "/api/admin/variants/31/available-stock"],
    [2147483647, "/api/admin/variants/2147483647/available-stock"],
  ])("posts the new value for variant %i", async (variantId, url) => {
    const fetchMock = stubFetch(json(200, ADJUSTED));
    const controller = new AbortController();
    await adjustAdminAvailableStock(variantId, INPUT, "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe(url);
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // 接口记录「5. 请求体结构与 JSON 类型（expected 与 available_stock 两个必填的严格整数，多出字段 422）」、DESIGN 1.18 第 12 条「请求带调整前页面显示的
  // 当日可用库存」与 UX 0.17 A04「`[admin.adjust_save]` 立即提交（请求带页面显示的那个值）」：请求体恰为 expected 与 available_stock 两项，原样写出；
  // 调用方对象上多出的属性（如 sku、variant_id）不进请求体。
  it("sends only expected and available_stock", async () => {
    const fetchMock = stubFetch(json(200, ADJUSTED));
    const withExtras = { expected: 0, available_stock: 9999, sku: "TEE-RED-M", variant_id: 31 };
    await adjustAdminAvailableStock(31, withExtras, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(Object.keys(body as object)).toEqual(["expected", "available_stock"]);
    expect(body).toStrictEqual({ expected: 0, available_stock: 9999 });
  });

  // 接口记录「成功（含新值与当前相同、未写入）回 200 {"variant_id", "sku", "available_stock"}」与 UX 0.17 A04「成功后该行的 `[admin.available_today]`
  // 改为新值」：结果只带回服务端返回的 available_stock（以响应为准），sku、variant_id 与多出的字段不带进结果；0 合格。
  it.each<[string, unknown, number]>([
    ["the new value", { ...ADJUSTED, version: 4, audit_id: 9 }, 12],
    ["zero", { ...ADJUSTED, available_stock: 0 }, 0],
    ["the reply rather than the request", { ...ADJUSTED, available_stock: 9999 }, 9999],
  ])("returns the available stock from the reply: %s", async (_name, reply, stock) => {
    stubFetch(json(200, reply));
    await expect(adjustAdminAvailableStock(31, INPUT, "csrf-abc")).resolves.toStrictEqual({ kind: "ok", available_stock: stock });
  });

  // 接口记录 StockOut（`variant_id: int`、`sku: str`、`available_stock: int`）与 DESIGN 1.18 第 12 条「把某个 SKU（含停用的）的当日可用库存直接改为一个值
  // （0–9,999 的整数）」：variant_id 缺少、为 0、负数、小数或字符串，sku 缺少、为空串、数字或 null，available_stock 缺少、负数、小数、字符串或 null，
  // 整体不是对象、为 null 或不是 JSON，以及回 201，都算 failed。
  it.each<[string, Response]>([
    ["variant_id missing", json(200, { sku: ADJUSTED.sku, available_stock: 12 })],
    ["variant_id zero", json(200, { ...ADJUSTED, variant_id: 0 })],
    ["variant_id negative", json(200, { ...ADJUSTED, variant_id: -31 })],
    ["variant_id fractional", json(200, { ...ADJUSTED, variant_id: 31.5 })],
    ["variant_id a string", json(200, { ...ADJUSTED, variant_id: "31" })],
    ["sku missing", json(200, { variant_id: 31, available_stock: 12 })],
    ["sku empty", json(200, { ...ADJUSTED, sku: "" })],
    ["sku a number", json(200, { ...ADJUSTED, sku: 31 })],
    ["sku null", json(200, { ...ADJUSTED, sku: null })],
    ["available_stock missing", json(200, { variant_id: 31, sku: ADJUSTED.sku })],
    ["available_stock negative", json(200, { ...ADJUSTED, available_stock: -1 })],
    ["available_stock fractional", json(200, { ...ADJUSTED, available_stock: 1.5 })],
    ["available_stock a string", json(200, { ...ADJUSTED, available_stock: "12" })],
    ["available_stock null", json(200, { ...ADJUSTED, available_stock: null })],
    ["body an array", json(200, [ADJUSTED])],
    ["body null", json(200, null)],
    ["not JSON", new Response("<html>", { status: 200 })],
    ["201 instead of 200", json(201, ADJUSTED)],
  ])("treats a malformed reply as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(adjustAdminAvailableStock(31, INPUT, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「StockChanged：409 {"detail": "stock_changed", "available_stock": 当前值}」、DESIGN 1.18 第 12 条「不等即回答「库存已变动」并返回当前的
  // 当日可用库存」与 UX 0.17 A04「服务端回答「库存已变动」时显示 `[admin.stock_changed]`（`{count}` 为服务端返回的当前值），该行改显示当前值」：
  // 结果带回当前值（0 合格），多出的字段不带进结果。
  it.each<[string, unknown, number]>([
    ["the current value", { detail: "stock_changed", available_stock: 7 }, 7],
    ["zero", { detail: "stock_changed", available_stock: 0 }, 0],
    ["extra fields", { detail: "stock_changed", available_stock: 7, sku: "TEE-RED-M", expected: 10 }, 7],
  ])("returns the current value when the stock has changed: %s", async (_name, reply, stock) => {
    stubFetch(json(409, reply));
    await expect(adjustAdminAvailableStock(31, INPUT, "csrf-abc")).resolves.toStrictEqual({ kind: "changed", available_stock: stock });
  });

  // 同上一条的接口记录与 UX 原句（页面要显示「服务端返回的当前值」）：409 stock_changed 缺当前值、当前值为负数、小数、字符串或 null 时无从显示，
  // 算 failed；detail 不是 stock_changed（含大小写不同）或 409 的响应体不是 JSON 也算 failed。
  it.each<[string, Response]>([
    ["current value missing", json(409, { detail: "stock_changed" })],
    ["current value negative", json(409, { detail: "stock_changed", available_stock: -1 })],
    ["current value fractional", json(409, { detail: "stock_changed", available_stock: 7.5 })],
    ["current value a string", json(409, { detail: "stock_changed", available_stock: "7" })],
    ["current value null", json(409, { detail: "stock_changed", available_stock: null })],
    ["another detail", json(409, { detail: "stock_invalid", available_stock: 7 })],
    ["detail in upper case", json(409, { detail: "STOCK_CHANGED", available_stock: 7 })],
    ["detail missing", json(409, { available_stock: 7 })],
    ["not JSON", new Response("stock_changed", { status: 409 })],
  ])("treats a malformed 409 as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(adjustAdminAvailableStock(31, INPUT, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「1. ……超过 8 KB 即 413……不是 JSON 415」「2. require_admin（401 admin_session_required）」「3. X-CSRF-Token（403 csrf_failed）」
  // 「4. 路径里的 SKU 规格 ID……不合法 422」「5. 请求体结构……多出字段 422」「6. 服务层 StockInvalid：422 {"detail": "stock_invalid"}；VariantNotFound：
  // 404 {"detail": "variant_not_found"}」与 UX 0.17 A04「结果：[admin.adjust_saved] / [admin.stock_changed] / [admin.adjust_invalid] / [common.error_retry]」：
  // 只有 detail 恰为 stock_invalid 的 422 为 invalid；路径 ID 与请求体的错误列表、大小写不同、空对象、不是 JSON、404 variant_not_found、413、415 与其他状态都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: "stock_invalid" }), "invalid"],
    [json(422, { detail: "STOCK_INVALID" }), "failed"],
    [json(422, { detail: "stock_changed", available_stock: 7 }), "failed"],
    [json(422, { detail: [{ loc: ["path", "variant_id"], type: "variant_id_invalid", msg: "Variant ID should be a positive integer" }] }), "failed"],
    [json(422, { detail: [{ loc: ["body", "expected"], type: "int_type", msg: "invalid" }] }), "failed"],
    [json(422, {}), "failed"],
    [new Response("stock_invalid", { status: 422 }), "failed"],
    [json(404, { detail: "variant_not_found" }), "failed"],
    [json(413, { detail: "request body too large" }), "failed"],
    [json(415, { detail: "request body must be JSON" }), "failed"],
    [json(500, { detail: "stock_invalid" }), "failed"],
    [json(400, { detail: "stock_changed", available_stock: 7 }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(adjustAdminAvailableStock(31, INPUT, "csrf-abc")).resolves.toEqual({ kind });
  });

  // 接口记录、DESIGN 1.18 第 12 条与 UX 0.17 A04 都没有关于中止的原句；守住的是本任务验收「网络中断与中止为 network」（UX 0.17 A04 最接近的
  // 一句为「没有未保存的修改时直接切换」）：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, ADJUSTED));
    controller.abort();
    await expect(adjustAdminAvailableStock(31, INPUT, "csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
