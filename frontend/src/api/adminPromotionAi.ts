import type { PromotionRuleInput, PromotionTier } from "./adminPromotions";
import { CSRF_HEADER } from "./pay";

// 后台促销活动 A12 AI 区的接口（app/api/admin_promotion_ai.py，SHOP-TASK-251）：状态 GET /api/admin/promotion-ai/status 与提案
// POST /api/admin/promotion-ai/proposal，都不带查询参数。后台会话凭服务端发的 HttpOnly cookie；状态不要求 CSRF，提案带调用方给出的 CSRF 令牌。
// 只取 StatusOut 与 ProposalOut 列出的字段，多出的字段不带进结果；响应体不合格算失败。DESIGN 1.23「节日促销活动」第 13 条：请求只带描述与规则参数，
// 不带商品、分类与任何个人资料；出站内容与计数在服务端，这里只传递与校验字段。429 的响应体按 SHOP-TASK-265（detail 带 limit）。

export const ADMIN_PROMOTION_AI_STATUS_URL = "/api/admin/promotion-ai/status";
export const ADMIN_PROMOTION_AI_PROPOSAL_URL = "/api/admin/promotion-ai/proposal";

// 提交的一条规则（与 RuleIn 一致）：活动规则去掉商品与分类 ID。调用方可直接给 PromotionRuleInput，多出的属性不带进请求体。
export type AiRuleInput = Omit<PromotionRuleInput, "product_ids" | "category_ids">;

// 提案的请求（与 ProposalIn 一致）。
export interface AiProposalRequest {
  description: string;
  rules: AiRuleInput[];
}

// 一份提案（与 DecorationProposalOut 一致）：八个装饰 ID 为字符串，英文标题为字符串，其余文案为字符串或 null。
export interface AiProposal {
  pack: string;
  palette: string;
  ambient: string;
  home_poster: string;
  list_strip: string;
  card_badge: string;
  detail_ribbon: string;
  cart_progress: string;
  title_en: string;
  title_zh: string | null;
  title_ms: string | null;
  subtitle_en: string | null;
  subtitle_zh: string | null;
  subtitle_ms: string | null;
  badge_en: string | null;
  badge_zh: string | null;
  badge_ms: string | null;
  strip_en: string | null;
  strip_zh: string | null;
  strip_ms: string | null;
}

const TEXT_KEYS = [
  "pack",
  "palette",
  "ambient",
  "home_poster",
  "list_strip",
  "card_badge",
  "detail_ribbon",
  "cart_progress",
  "title_en",
] as const satisfies readonly (keyof AiProposal)[];

const TEXT_OR_NULL_KEYS = [
  "title_zh",
  "title_ms",
  "subtitle_en",
  "subtitle_zh",
  "subtitle_ms",
  "badge_en",
  "badge_zh",
  "badge_ms",
  "strip_en",
  "strip_zh",
  "strip_ms",
] as const satisfies readonly (keyof AiProposal)[];

// 描述或规则参数不合格的一条问题：描述不合格时 field 为 description，规则参数不合格时以 rules.<序号> 开头。
export interface AiProposalProblem {
  code: string;
  field: string;
}

// 状态的结果：取到（200，带是否已配置）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断（含调用方中止）。
export type AiStatusRead = { kind: "ok"; configured: boolean } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 提案的结果：取到（200，带提案）、没有会话（401）、CSRF 令牌不对（403）、描述或规则参数不合格（422 promotion_invalid，带 problems）、AI 未配置
// （503 ai_not_configured）、当天次数已达上限（429，带 limit）、AI 暂不可用（503 ai_unavailable）、AI 结果不合格（502 ai_result_invalid）、
// 其他失败（含 413、415、FastAPI 的 422、detail 不合格的 429、其他 502 与 503、200 但响应体不合格）、网络中断（含调用方中止）。
export type AiProposalResult =
  | { kind: "ok"; proposal: AiProposal }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "invalid"; problems: AiProposalProblem[] }
  | { kind: "not_configured" }
  | { kind: "daily_limit"; limit: number }
  | { kind: "unavailable" }
  | { kind: "result_invalid" }
  | { kind: "failed" }
  | { kind: "network" };

// problems 的 field：description，或 rules.<序号> 加「.参数名」（服务层 _checked_rules 的写法）。
const PROBLEM_FIELD = /^(description|rules\.(0|[1-9][0-9]*)(\..+)?)$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isTextOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

// 数组的每一项按 read 读取；不是数组或任一项不合格为 null。
function readEach<T>(value: unknown, read: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const items: T[] = [];
  for (const item of value) {
    const parsed = read(item);
    if (parsed === null) {
      return null;
    }
    items.push(parsed);
  }
  return items;
}

// 状态的响应体：configured 为布尔值。
function readStatus(value: unknown): boolean | null {
  return isRecord(value) && typeof value.configured === "boolean" ? value.configured : null;
}

// 提案：九项为字符串、十一项为字符串或 null，都须在；多出的字段不带进结果。
function readProposal(value: unknown): AiProposal | null {
  if (!isRecord(value)) {
    return null;
  }
  const proposal: Partial<AiProposal> = {};
  for (const key of TEXT_KEYS) {
    const text = value[key];
    if (typeof text !== "string") {
      return null;
    }
    proposal[key] = text;
  }
  for (const key of TEXT_OR_NULL_KEYS) {
    const text = value[key];
    if (!isTextOrNull(text)) {
      return null;
    }
    proposal[key] = text;
  }
  return proposal as AiProposal;
}

// 提案的响应体（ProposalOut）：proposal 合格。
function readProposalOut(value: unknown): AiProposal | null {
  return isRecord(value) ? readProposal(value.proposal) : null;
}

// 一条问题：code 为字符串，field 为 description 或以 rules.<序号> 开头；其余字段不带进结果。
function readProblem(value: unknown): AiProposalProblem | null {
  if (!isRecord(value)) {
    return null;
  }
  const { code, field } = value;
  if (typeof code !== "string" || typeof field !== "string" || !PROBLEM_FIELD.test(field)) {
    return null;
  }
  return { code, field };
}

// 422 的响应体：detail 为 {"code": "promotion_invalid", "problems": [...]} 且每项问题合格时为 invalid，否则为 null。
function readInvalid(body: unknown): AiProposalResult | null {
  if (!isRecord(body)) {
    return null;
  }
  const { detail } = body;
  if (!isRecord(detail) || detail.code !== "promotion_invalid") {
    return null;
  }
  const problems = readEach(detail.problems, readProblem);
  return problems === null ? null : { kind: "invalid", problems };
}

// 429 的响应体（SHOP-TASK-265）：detail 为 {"code": "ai_daily_limit", "limit": 不小于 0 的整数} 时为 daily_limit，否则为 null。
function readDailyLimit(body: unknown): AiProposalResult | null {
  if (!isRecord(body)) {
    return null;
  }
  const { detail } = body;
  if (!isRecord(detail) || detail.code !== "ai_daily_limit") {
    return null;
  }
  const { limit } = detail;
  return typeof limit === "number" && Number.isSafeInteger(limit) && limit >= 0 ? { kind: "daily_limit", limit } : null;
}

// 503 的响应体：detail 为 ai_not_configured 或 ai_unavailable；其他为 null。
function readUnavailable(body: unknown): AiProposalResult | null {
  if (!isRecord(body)) {
    return null;
  }
  if (body.detail === "ai_not_configured") {
    return { kind: "not_configured" };
  }
  if (body.detail === "ai_unavailable") {
    return { kind: "unavailable" };
  }
  return null;
}

// 502 的响应体：detail 恰为 ai_result_invalid 时为 result_invalid，否则为 null。
function readResultInvalid(body: unknown): AiProposalResult | null {
  return isRecord(body) && body.detail === "ai_result_invalid" ? { kind: "result_invalid" } : null;
}

// 响应体按 read 读取；读不出或不合格为 null。
async function readBody<T>(response: Response, read: (body: unknown) => T | null): Promise<T | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return read(body);
}

// 请求体里的满额档与规则：满额档的三个键没有值时都提交 null。
type TierBody = { [K in keyof PromotionTier]: PromotionTier[K] | null };
type RuleBody = Omit<AiRuleInput, "tiers"> & { tiers: TierBody[] };

// 满额档恰为三个键，没有值的为 null（后端 TierIn 不许多出字段、三个键都必须给出）。
function tierBody(tier: PromotionTier): TierBody {
  return { threshold_sen: tier.threshold_sen ?? null, amount_sen: tier.amount_sen ?? null, percent: tier.percent ?? null };
}

// 规则只取 kind 与参数，不带商品与分类 ID 及调用方对象上多出的属性；参数没有值时提交 null。
function ruleBody(rule: AiRuleInput): RuleBody {
  return {
    kind: rule.kind,
    discount_mode: rule.discount_mode ?? null,
    percent: rule.percent ?? null,
    amount_sen: rule.amount_sen ?? null,
    buy_quantity: rule.buy_quantity ?? null,
    get_quantity: rule.get_quantity ?? null,
    get_percent: rule.get_percent ?? null,
    threshold_sen: rule.threshold_sen ?? null,
    tiers: rule.tiers.map(tierBody),
  };
}

// 请求体只有 description 与 rules。
function proposalBody(request: AiProposalRequest): { description: string; rules: RuleBody[] } {
  return { description: request.description, rules: request.rules.map(ruleBody) };
}

// GET /api/admin/promotion-ai/status：同源 cookie、不缓存、没有请求体，请求头只有 Accept。
export async function readAiStatus(signal?: AbortSignal): Promise<AiStatusRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_PROMOTION_AI_STATUS_URL, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "none" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  const configured = await readBody(response, readStatus);
  return configured === null ? { kind: "failed" } : { kind: "ok", configured };
}

// POST /api/admin/promotion-ai/proposal：JSON 请求体只有 description 与 rules，请求头带 CSRF 令牌；同源 cookie、不缓存、带调用方的 signal。
// 成功为 200，其余按状态码与 detail 归类。
export async function requestAiProposal(request: AiProposalRequest, csrfToken: string, signal?: AbortSignal): Promise<AiProposalResult> {
  let response: Response;
  try {
    response = await fetch(ADMIN_PROMOTION_AI_PROPOSAL_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify(proposalBody(request)),
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const proposal = await readBody(response, readProposalOut);
      return proposal === null ? { kind: "failed" } : { kind: "ok", proposal };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 422:
      return (await readBody(response, readInvalid)) ?? { kind: "failed" };
    case 429:
      return (await readBody(response, readDailyLimit)) ?? { kind: "failed" };
    case 502:
      return (await readBody(response, readResultInvalid)) ?? { kind: "failed" };
    case 503:
      return (await readBody(response, readUnavailable)) ?? { kind: "failed" };
    default:
      return { kind: "failed" };
  }
}
