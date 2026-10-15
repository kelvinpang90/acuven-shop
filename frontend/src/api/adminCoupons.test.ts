import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_COUPONS_URL, createAdminCoupon, disableAdminCoupon, listAdminCoupons } from "./adminCoupons";
import type { AdminCouponInput } from "./adminCoupons";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const FIXED = { id: 7, code: "SAVE10", type: "fixed", amount_off_sen: 1000, percent_off: null, min_subtotal_sen: 5000, starts_on: "2026-10-15", ends_on: "2026-10-31", total_use_limit: null, per_member_use_limit: 2, used_count: 1, active: true };
const PERCENT = { ...FIXED, id: 8, code: "PCT20", type: "percent", amount_off_sen: null, percent_off: 20, total_use_limit: 5, per_member_use_limit: null };
const PAGE = { total: 2, page: 1, page_size: 30, coupons: [FIXED, PERCENT], csrf_token: "csrf-abc" };
const INPUT: AdminCouponInput = { code: "SAVE10", type: "fixed", amount_off_sen: 1000, percent_off: null, min_subtotal_sen: 5000, starts_on: "2026-10-15", ends_on: "2026-10-31", total_use_limit: null, per_member_use_limit: 2 };

function json(status: number, body: unknown): Response { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
interface Call { url: string; init: RequestInit; }
function stubFetch(...replies: (Response | (() => never))[]) {
  const calls: Call[] = []; const queue = [...replies];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
    const reply = queue.shift();
    return reply === undefined ? Promise.reject(new Error("unexpected request")) : typeof reply === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
  }));
  return calls;
}
const networkDown = (): never => { throw new TypeError("Failed to fetch"); };

describe("admin coupons", () => {
  // SHOP-TASK-099「GET /api/admin/coupons」与 UX A05 的列表入口：页码在唯一查询参数中，同源 cookie、no-store 和调用方 signal 都被保留。
  it("gets the requested coupon page without caching", async () => {
    const calls = stubFetch(json(200, PAGE)); const controller = new AbortController();
    await listAdminCoupons(12, controller.signal);
    expect(ADMIN_COUPONS_URL).toBe("/api/admin/coupons");
    expect(calls[0]).toMatchObject({ url: "/api/admin/coupons?page=12", init: { method: "GET", credentials: "same-origin", cache: "no-store", signal: controller.signal } });
    expect(calls[0]?.init.body).toBeUndefined();
  });

  // SHOP-TASK-099「新建严格接收券字段」：JSON 只有九个请求字段，带调用方给的 CSRF 和 JSON 内容类型。
  it("posts only the nine coupon fields", async () => {
    const calls = stubFetch(json(201, FIXED));
    await createAdminCoupon({ ...INPUT, ignored: "not sent" } as AdminCouponInput, "csrf-abc");
    expect(calls[0]).toMatchObject({ url: ADMIN_COUPONS_URL, init: { method: "POST", credentials: "same-origin", cache: "no-store" } });
    expect(new Headers(calls[0]?.init.headers).get("X-CSRF-Token")).toBe("csrf-abc");
    expect(new Headers(calls[0]?.init.headers).get("Content-Type")).toBe("application/json");
    const sent = JSON.parse(calls[0]?.init.body as string) as Record<string, unknown>;
    expect(sent).toEqual(INPUT);
    expect(Object.keys(sent)).toEqual(["code", "type", "amount_off_sen", "percent_off", "min_subtotal_sen", "starts_on", "ends_on", "total_use_limit", "per_member_use_limit"]);
  });

  // SHOP-TASK-099「POST /{coupon_id}/disable」与 UX A05 的停用：只带 CSRF 请求头，不发送请求体。
  it("posts disable without a body", async () => {
    const calls = stubFetch(json(200, { ...FIXED, active: false }));
    await disableAdminCoupon(7, "csrf-abc");
    expect(calls[0]).toMatchObject({ url: "/api/admin/coupons/7/disable", init: { method: "POST", credentials: "same-origin", cache: "no-store" } });
    expect(new Headers(calls[0]?.init.headers).get("X-CSRF-Token")).toBe("csrf-abc");
    expect(new Headers(calls[0]?.init.headers).has("Content-Type")).toBe(false);
    expect(calls[0]?.init.body).toBeUndefined();
  });

  // SHOP-TASK-099 的列表记录段：只保留券、分页与 CSRF 的约定字段，固定券与百分比券的互斥值都可解码。
  it("keeps only the listed response fields", async () => {
    stubFetch(json(200, { ...PAGE, extra: 1, coupons: PAGE.coupons.map((coupon) => ({ ...coupon, created_at: "ignored" })) }));
    await expect(listAdminCoupons(1)).resolves.toEqual({ kind: "ok", page: PAGE });
  });

  // SHOP-TASK-099 记录段「字段缺少或类型不对都算不合格」：覆盖两种券的互斥字段、百分比范围、日期、上限与分页固定值。
  it.each<[string, unknown]>([
    ["fixed has percent", { ...FIXED, percent_off: 1 }], ["percent has amount", { ...PERCENT, amount_off_sen: 1 }], ["percent out of range", { ...PERCENT, percent_off: 101 }],
    ["bad date", { ...FIXED, starts_on: "2026/10/15" }], ["nonexistent date", { ...FIXED, ends_on: "2026-02-30" }], ["zero total limit", { ...FIXED, total_use_limit: 0 }],
    ["zero member limit", { ...FIXED, per_member_use_limit: 0 }], ["zero id", { ...FIXED, id: 0 }], ["empty code", { ...FIXED, code: "" }],
    ["negative minimum", { ...FIXED, min_subtotal_sen: -1 }], ["used count string", { ...FIXED, used_count: "1" }], ["active string", { ...FIXED, active: "true" }],
    ["negative total", { ...PAGE, total: -1 }], ["page zero", { ...PAGE, page: 0 }], ["wrong page size", { ...PAGE, page_size: 20 }], ["csrf empty", { ...PAGE, csrf_token: "" }],
  ])("rejects malformed response data: %s", async (_name, coupon) => {
    const body = "page" in (coupon as object) ? coupon : { ...PAGE, coupons: [coupon] };
    stubFetch(json(200, body)); await expect(listAdminCoupons(1)).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-099 的错误码记录：列表 401 为 none，其他状态和网络中断分别为 failed、network。
  it.each<[Response | (() => never), string]>([[json(401, {}), "none"], [json(403, {}), "failed"], [json(500, {}), "failed"], [networkDown, "network"]])("sorts list reply %#: %s", async (reply, kind) => {
    stubFetch(reply); await expect(listAdminCoupons(1)).resolves.toEqual({ kind });
  });

  // SHOP-TASK-099「coupon_code_taken 的 409」及其余新建错误码：仅该 detail 是 code_taken，422 为 invalid。
  it.each<[Response | (() => never), string]>([[json(201, FIXED), "ok"], [json(401, {}), "none"], [json(403, {}), "csrf"], [json(409, { detail: "coupon_code_taken" }), "code_taken"], [json(409, { detail: "other" }), "failed"], [json(422, {}), "invalid"], [json(503, {}), "failed"], [networkDown, "network"]])("sorts create reply %#: %s", async (reply, kind) => {
    stubFetch(reply); const result = await createAdminCoupon(INPUT, "csrf-abc");
    expect(result.kind).toBe(kind);
  });

  // SHOP-TASK-099 记录段「响应体不合格算失败」：创建和停用成功状态也必须给出完整、有效的券记录。
  it("rejects malformed successful write replies", async () => {
    stubFetch(json(201, { ...FIXED, active: "yes" }), json(200, { ...FIXED, active: "yes" }));
    await expect(createAdminCoupon(INPUT, "csrf-abc")).resolves.toEqual({ kind: "failed" });
    await expect(disableAdminCoupon(7, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-099 停用的错误码记录：401、403、not_found 与其他故障分别让 A05 显示相应结果。
  it.each<[Response | (() => never), string]>([[json(200, FIXED), "ok"], [json(401, {}), "none"], [json(403, {}), "csrf"], [json(404, { detail: "not_found" }), "not_found"], [json(422, {}), "failed"], [json(503, {}), "failed"], [networkDown, "network"]])("sorts disable reply %#: %s", async (reply, kind) => {
    stubFetch(reply); const result = await disableAdminCoupon(7, "csrf-abc");
    expect(result.kind).toBe(kind);
  });

  // UX A05 的离开页面行为与现有请求模块一致：中止使 fetch 抛错，归为 network。
  it("treats an aborted request as network", async () => {
    const controller = new AbortController(); controller.abort();
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new DOMException("Aborted", "AbortError"))));
    await expect(listAdminCoupons(1, controller.signal)).resolves.toEqual({ kind: "network" });
  });
});
