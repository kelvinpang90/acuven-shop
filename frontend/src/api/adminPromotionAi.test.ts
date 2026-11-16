import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ADMIN_PROMOTION_AI_PROPOSAL_URL,
  ADMIN_PROMOTION_AI_STATUS_URL,
  type AiProposalRequest,
  readAiStatus,
  requestAiProposal,
} from "./adminPromotionAi";
import type { AdminPromotionRule } from "./adminPromotions";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const STATUS_URL = "/api/admin/promotion-ai/status";
const PROPOSAL_URL = "/api/admin/promotion-ai/proposal";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不是给定的接口时拒绝。
// 参数带类型（地址只收字符串或 URL，String(…) 即得地址），请求从替身的 mock.calls 取。
function stubFetch(url: string, ...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined || String(input) !== url) {
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
  return typeof body === "string" ? JSON.parse(body) : undefined;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// 一份合格的提案（app/api/admin_promotion_ai.py 的 DecorationProposalOut：「八个装饰 ID 与四种文案各三语」）。
const PROPOSAL = {
  pack: "lunar_new_year",
  palette: "theme",
  ambient: "none",
  home_poster: "lny_poster_1",
  list_strip: "lny_strip_1",
  card_badge: "lny_badge_1",
  detail_ribbon: "lny_ribbon_1",
  cart_progress: "lny_progress_1",
  title_en: "Lunar New Year Sale",
  title_zh: "新春特卖",
  title_ms: null,
  subtitle_en: "Up to 20% off",
  subtitle_zh: null,
  subtitle_ms: null,
  badge_en: "-20%",
  badge_zh: null,
  badge_ms: null,
  strip_en: null,
  strip_zh: null,
  strip_ms: null,
};

function request(): AiProposalRequest {
  return {
    description: "Lunar New Year sale with red and gold",
    rules: [
      {
        kind: "item_discount",
        discount_mode: "percent",
        percent: 20,
        amount_sen: null,
        buy_quantity: null,
        get_quantity: null,
        get_percent: null,
        threshold_sen: null,
        tiers: [],
      },
    ],
  };
}

function propose(signal?: AbortSignal) {
  return requestAiProposal(request(), "csrf-abc", signal);
}

describe("reading the AI status", () => {
  // 接口文档「状态（GET /status）：require_admin（401）→ {"configured": 布尔值}」与「全部响应（含错误）都带 Cache-Control: no-store」：
  // 地址为固定路径、不带查询参数，GET、凭同源 cookie、不缓存，请求头只有 Accept，没有请求体，并带上调用方的 signal。
  it("gets the status endpoint without a body or CSRF token", async () => {
    const fetchMock = stubFetch(STATUS_URL, json(200, { configured: true }));
    const controller = new AbortController();
    await readAiStatus(controller.signal);
    expect(ADMIN_PROMOTION_AI_STATUS_URL).toBe(STATUS_URL);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe(STATUS_URL);
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.has("X-CSRF-Token")).toBe(false);
    expect(init.signal).toBe(controller.signal);
  });

  // UX 0.20 A12「未配置：( [admin.ai_generate] ) 禁用，[admin.ai_not_configured]」与接口文档「{"configured": 布尔值}」：两种取值都原样带回，
  // 多出的字段不带进结果。
  it.each([true, false])("returns configured %s", async (configured) => {
    stubFetch(STATUS_URL, json(200, { configured, model: "x" }));
    await expect(readAiStatus()).resolves.toEqual({ kind: "ok", configured });
  });

  // 接口文档「{"configured": 布尔值}」：configured 缺少、为 null、字符串或数字，整体不是对象或不是 JSON 都为 failed；回 204 也不是成功。
  it.each<[string, Response]>([
    ["configured missing", json(200, {})],
    ["configured null", json(200, { configured: null })],
    ["configured a string", json(200, { configured: "true" })],
    ["configured a number", json(200, { configured: 1 })],
    ["body an array", json(200, [true])],
    ["body null", json(200, null)],
    ["not JSON", new Response("<html>", { status: 200 })],
    ["204 instead of 200", new Response(null, { status: 204 })],
  ])("treats a malformed reply as a failure: %s", async (_name, reply) => {
    stubFetch(STATUS_URL, reply);
    await expect(readAiStatus()).resolves.toEqual({ kind: "failed" });
  });

  // 接口文档「两个接口都经 require_admin：没有有效后台会话时 401 admin_session_required」：401 为 none，其他状态都归 failed，网络中断为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(404, { detail: "Not Found" }), "failed"],
    [json(503, { detail: "ai_not_configured" }), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(STATUS_URL, reply);
    await expect(readAiStatus()).resolves.toEqual({ kind });
  });

  // 接口文档与 UX 0.20 A12 都没有关于中止的原句；守住的是本任务验收「调用方中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(STATUS_URL, json(200, { configured: true }));
    controller.abort();
    await expect(readAiStatus(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("requesting an AI proposal", () => {
  // 接口文档「POST /api/admin/promotion-ai/proposal」「3. X-CSRF-Token（403 csrf_failed）」「不是 JSON 415」与「全部响应（含错误）都带
  // Cache-Control: no-store」：地址为固定路径、不带查询参数，POST、凭同源 cookie、不缓存，Accept 与 Content-Type 为 JSON，X-CSRF-Token 为
  // 调用方给的令牌，并带上调用方的 signal。
  it("posts JSON to the proposal endpoint with the CSRF token", async () => {
    const fetchMock = stubFetch(PROPOSAL_URL, json(200, { proposal: PROPOSAL }));
    const controller = new AbortController();
    await propose(controller.signal);
    expect(ADMIN_PROMOTION_AI_PROPOSAL_URL).toBe(PROPOSAL_URL);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe(PROPOSAL_URL);
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // DESIGN 1.23「节日促销活动」第 13 条（UX 0.20 A12「AI 只收到描述、规则参数与预置清单」）与接口文档「4. 请求体有必填的 description 与 rules
  // （每项为 kind 与 RuleInput 的参数字段，没有商品与分类 ID）；多出字段……422」：调用方直接给读回的活动规则（带 rule_id、products、product_ids、
  // category_ids）与多出的属性时，请求体只有 description 与 rules，每条规则恰为 kind 与八个参数键，tiers 每项恰为三个键。
  it("sends only the description and the rule parameters", async () => {
    const fetchMock = stubFetch(PROPOSAL_URL, json(200, { proposal: PROPOSAL }));
    const rule: AdminPromotionRule = {
      rule_id: 9,
      kind: "tiers",
      discount_mode: "amount",
      percent: null,
      amount_sen: null,
      buy_quantity: null,
      get_quantity: null,
      get_percent: null,
      threshold_sen: null,
      product_ids: [3, 4],
      category_ids: [5],
      products: [{ id: 3, slug: "red-packet", name_en: "Red packet" }],
      tiers: [{ threshold_sen: 10000, amount_sen: 1000, percent: null }],
    };
    const input = { description: "Raya sale", rules: [rule], promotion_id: 7, email: "owner@example.com" };
    await requestAiProposal(input, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(body).toStrictEqual({
      description: "Raya sale",
      rules: [
        {
          kind: "tiers",
          discount_mode: "amount",
          percent: null,
          amount_sen: null,
          buy_quantity: null,
          get_quantity: null,
          get_percent: null,
          threshold_sen: null,
          tiers: [{ threshold_sen: 10000, amount_sen: 1000, percent: null }],
        },
      ],
    });
    const text = JSON.stringify(body);
    expect(text).not.toContain("product_ids");
    expect(text).not.toContain("category_ids");
    expect(text).not.toContain("owner@example.com");
  });

  // 落实写法「参数没有值时提交 null；tiers 每项恰为 threshold_sen、amount_sen 与 percent 三个键，没有值的为 null，因为后端 TierIn 不许多出字段、
  // 三个键都必须给出」：调用方对象上缺少（undefined）的参数与档位键都提交 null，档位多出的属性不带进；规则的顺序与档位的顺序照给出的顺序。
  it("fills missing parameters and tier keys with null", async () => {
    const fetchMock = stubFetch(PROPOSAL_URL, json(200, { proposal: PROPOSAL }));
    const sparse = {
      description: "Deepavali",
      rules: [
        { kind: "free_shipping", threshold_sen: 5000, tiers: [] },
        { kind: "tiers", discount_mode: "percent", tiers: [{ threshold_sen: 20000, percent: 10, label: "x" }, { threshold_sen: 40000 }] },
      ],
    } as unknown as AiProposalRequest;
    await requestAiProposal(sparse, "csrf-abc");
    expect(sentBody(fetchMock, 0)).toStrictEqual({
      description: "Deepavali",
      rules: [
        {
          kind: "free_shipping",
          discount_mode: null,
          percent: null,
          amount_sen: null,
          buy_quantity: null,
          get_quantity: null,
          get_percent: null,
          threshold_sen: 5000,
          tiers: [],
        },
        {
          kind: "tiers",
          discount_mode: "percent",
          percent: null,
          amount_sen: null,
          buy_quantity: null,
          get_quantity: null,
          get_percent: null,
          threshold_sen: null,
          tiers: [
            { threshold_sen: 20000, amount_sen: null, percent: 10 },
            { threshold_sen: 40000, amount_sen: null, percent: null },
          ],
        },
      ],
    });
  });

  // 接口文档「成功 200 {"proposal": DecorationProposal 的同名字段}」：结果带回二十个字段，proposal 与外层多出的字段都不带进结果。
  it("returns the proposal fields only", async () => {
    stubFetch(PROPOSAL_URL, json(200, { proposal: { ...PROPOSAL, poster_mode: "template", note: "x" }, saved: false }));
    await expect(propose()).resolves.toEqual({ kind: "ok", proposal: PROPOSAL });
  });

  // 接口文档 DecorationProposalOut 的字段类型（八个装饰 ID 与 title_en 为 str，其余文案为 str | None）：逐字段校验，任一项缺少或类型不对整份为 failed。
  it.each<[string, unknown]>([
    ["proposal missing", {}],
    ["proposal null", { proposal: null }],
    ["proposal an array", { proposal: [PROPOSAL] }],
    ["body the proposal itself", PROPOSAL],
    ["body null", null],
    ["pack missing", { proposal: { ...PROPOSAL, pack: undefined } }],
    ["palette null", { proposal: { ...PROPOSAL, palette: null } }],
    ["cart_progress a number", { proposal: { ...PROPOSAL, cart_progress: 1 } }],
    ["title_en null", { proposal: { ...PROPOSAL, title_en: null } }],
    ["title_zh missing", { proposal: { ...PROPOSAL, title_zh: undefined } }],
    ["strip_ms missing", { proposal: { ...PROPOSAL, strip_ms: undefined } }],
    ["badge_en a number", { proposal: { ...PROPOSAL, badge_en: 20 } }],
    ["subtitle_en an object", { proposal: { ...PROPOSAL, subtitle_en: { en: "x" } } }],
  ])("treats a malformed proposal as a failure: %s", async (_name, body) => {
    stubFetch(PROPOSAL_URL, json(200, body));
    await expect(propose()).resolves.toEqual({ kind: "failed" });
  });

  // 接口文档「成功 200」：200 的响应体不是 JSON 算 failed；回 201 也不是成功。
  it.each<[string, Response]>([
    ["not JSON", new Response("<html>", { status: 200 })],
    ["201 instead of 200", json(201, { proposal: PROPOSAL })],
  ])("treats an unexpected success as a failure: %s", async (_name, reply) => {
    stubFetch(PROPOSAL_URL, reply);
    await expect(propose()).resolves.toEqual({ kind: "failed" });
  });

  // 接口文档「PromotionInvalid：422 {"code": "promotion_invalid", "problems": [...]}，每项 code、field、product_id、product_name」与落实写法
  // 「描述不合格时 field 为 description，规则参数不合格时为 rules.N 开头」：problems 按给出的顺序带回，每项只取 code 与 field。
  it("returns the problems of an invalid request", async () => {
    stubFetch(
      PROPOSAL_URL,
      json(422, {
        detail: {
          code: "promotion_invalid",
          problems: [
            { code: "description", field: "description", product_id: null, product_name: null },
            { code: "percent_range", field: "rules.0.percent", product_id: null, product_name: null },
            { code: "tier_order", field: "rules.12.tiers.1.threshold_sen", product_id: null, product_name: null },
          ],
        },
      }),
    );
    await expect(propose()).resolves.toEqual({
      kind: "invalid",
      problems: [
        { code: "description", field: "description" },
        { code: "percent_range", field: "rules.0.percent" },
        { code: "tier_order", field: "rules.12.tiers.1.threshold_sen" },
      ],
    });
  });

  // 同一句：没有问题的空列表照样为 invalid（页面显示总的不合格提示）。
  it("accepts an empty problem list", async () => {
    stubFetch(PROPOSAL_URL, json(422, { detail: { code: "promotion_invalid", problems: [] } }));
    await expect(propose()).resolves.toEqual({ kind: "invalid", problems: [] });
  });

  // 同一句：detail.code 不是 promotion_invalid、problems 缺少或不是数组、任一项缺 code 或 field、类型不对、field 不是 description 也不以 rules.<序号>
  // 开头，以及接口文档「多出字段、缺字段或类型不对 422」的 FastAPI 错误列表，都为 failed。
  it.each<[string, unknown]>([
    ["FastAPI errors", { detail: [{ loc: ["body", "rules", 0, "product_ids"], type: "extra_forbidden", msg: "Extra inputs are not permitted" }] }],
    ["detail a string", { detail: "promotion_invalid" }],
    ["another code", { detail: { code: "ai_invalid", problems: [] } }],
    ["problems missing", { detail: { code: "promotion_invalid" } }],
    ["problems an object", { detail: { code: "promotion_invalid", problems: { code: "description", field: "description" } } }],
    ["problem without code", { detail: { code: "promotion_invalid", problems: [{ field: "description" }] } }],
    ["problem without field", { detail: { code: "promotion_invalid", problems: [{ code: "description" }] } }],
    ["problem code a number", { detail: { code: "promotion_invalid", problems: [{ code: 1, field: "description" }] } }],
    ["problem field null", { detail: { code: "promotion_invalid", problems: [{ code: "description", field: null }] } }],
    ["problem field a name", { detail: { code: "promotion_invalid", problems: [{ code: "x", field: "name" }] } }],
    ["problem field rules without a number", { detail: { code: "promotion_invalid", problems: [{ code: "x", field: "rules.percent" }] } }],
    ["problem field with a prefix", { detail: { code: "promotion_invalid", problems: [{ code: "x", field: "body.description" }] } }],
    ["problem null", { detail: { code: "promotion_invalid", problems: [null] } }],
    ["empty body", {}],
  ])("treats a malformed 422 as a failure: %s", async (_name, body) => {
    stubFetch(PROPOSAL_URL, json(422, body));
    await expect(propose()).resolves.toEqual({ kind: "failed" });
  });

  // 接口文档「AiDailyLimit：429 {"code": "ai_daily_limit", "limit": 配置的 ai_daily_call_limit}（SHOP-TASK-265，供 A12 的 admin.ai_daily_limit
  // 填 {count}）」：limit 原样带回，多出的字段不带进结果。
  it.each([1, 50, 0])("reads the daily limit %s", async (limit) => {
    stubFetch(PROPOSAL_URL, json(429, { detail: { code: "ai_daily_limit", limit, used: 50 } }));
    await expect(propose()).resolves.toEqual({ kind: "daily_limit", limit });
  });

  // 落实写法「daily_limit（429，带 limit 整数；detail 不合格时为 failed）」：limit 缺少、为 null、字符串、小数、负数，code 不对、detail 为旧的
  // 字符串写法、不是 JSON（反向代理的 429）都为 failed。
  it.each<[string, Response]>([
    ["limit missing", json(429, { detail: { code: "ai_daily_limit" } })],
    ["limit null", json(429, { detail: { code: "ai_daily_limit", limit: null } })],
    ["limit a string", json(429, { detail: { code: "ai_daily_limit", limit: "50" } })],
    ["limit a fraction", json(429, { detail: { code: "ai_daily_limit", limit: 1.5 } })],
    ["limit negative", json(429, { detail: { code: "ai_daily_limit", limit: -1 } })],
    ["another code", json(429, { detail: { code: "rate_limited", limit: 50 } })],
    ["detail a string", json(429, { detail: "ai_daily_limit" })],
    ["limit outside detail", json(429, { code: "ai_daily_limit", limit: 50 })],
    ["not JSON", new Response("Too Many Requests", { status: 429 })],
  ])("treats a malformed 429 as a failure: %s", async (_name, reply) => {
    stubFetch(PROPOSAL_URL, reply);
    await expect(propose()).resolves.toEqual({ kind: "failed" });
  });

  // 接口文档「2. require_admin（401）」「3. X-CSRF-Token（403 csrf_failed）」「AiNotConfigured：503 ai_not_configured」「AiUnavailable：503
  // ai_unavailable；AiResultInvalid：502 ai_result_invalid」「1. ……超过 8 KB 即 413……不是 JSON 415」，UX 0.20 A12「失败：[admin.ai_invalid] /
  // [admin.ai_unavailable] / [admin.ai_daily_limit]」：只有 detail 恰为对应键时归为对应的结果；detail 对调、大小写不同、不是 JSON、413、415 与其他
  // 状态都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(503, { detail: "ai_not_configured" }), "not_configured"],
    [json(503, { detail: "ai_unavailable" }), "unavailable"],
    [json(502, { detail: "ai_result_invalid" }), "result_invalid"],
    [json(503, { detail: "ai_result_invalid" }), "failed"],
    [json(503, { detail: "AI_UNAVAILABLE" }), "failed"],
    [json(503, {}), "failed"],
    [new Response("Service Unavailable", { status: 503 }), "failed"],
    [json(502, { detail: "ai_unavailable" }), "failed"],
    [new Response("<html>Bad Gateway</html>", { status: 502 }), "failed"],
    [json(413, { detail: "request_too_large" }), "failed"],
    [json(415, { detail: "unsupported_media_type" }), "failed"],
    [json(404, { detail: "Not Found" }), "failed"],
    [json(500, { detail: "ai_unavailable" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(PROPOSAL_URL, reply);
    await expect(propose()).resolves.toEqual({ kind });
  });

  // 接口文档与 UX 0.20 A12 都没有关于中止的原句；守住的是本任务验收「调用方中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(PROPOSAL_URL, json(200, { proposal: PROPOSAL }));
    controller.abort();
    await expect(propose(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
