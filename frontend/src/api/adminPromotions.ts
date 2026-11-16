import { CSRF_HEADER } from "./pay";

// 后台促销活动 A12 的接口（app/api/admin_promotions.py，SHOP-TASK-242）：列表 GET 与新建 POST /api/admin/promotions，读取 GET、保存 PUT 与删除
// DELETE /api/admin/promotions/{promotion_id}，发布、撤回、提前结束与复制 POST …/{promotion_id}/publish、/withdraw、/end 与 /copy，都不带查询参数。
// 后台会话凭服务端发的 HttpOnly cookie；读取不要求 CSRF，写操作带读取返回的 CSRF 令牌。只取 PromotionOut、PromotionPageOut、PromotionListOut 列出的
// 字段，多出的字段不带进结果；响应体不合格算失败。规则、核对与审计在服务端（DESIGN 1.23 第 1–3、12 条），这里只传递与校验字段；时间为马来西亚
// 本地「YYYY-MM-DDTHH:MM」字符串或 null，原样收发，不换算。

export const ADMIN_PROMOTIONS_URL = "/api/admin/promotions";
// 活动 ID 的上限，与 app/api/admin_promotions.py 的 MAX_PROMOTION_ID 相同。
export const MAX_PROMOTION_ID = 2147483647;

export const PROMOTION_STATES = ["draft", "scheduled", "running", "ended"] as const;
export type PromotionState = (typeof PROMOTION_STATES)[number];

// 列表的一项（与 PromotionSummaryOut 一致）：ends_at 为实际结束（提前结束的时刻或结束时间），updated_at 为不带时区的 UTC，原样带回。
export interface AdminPromotionSummary {
  id: number;
  name: string;
  state: PromotionState;
  starts_at: string | null;
  ends_at: string | null;
  rule_count: number;
  updated_at: string;
}

// 满额档（与 TierIn 一致）：提交时恰为这三个键，没有值的为 null（后端不许多出字段、三个键都必须给出）。
export interface PromotionTier {
  threshold_sen: number;
  amount_sen: number | null;
  percent: number | null;
}

// 规则显式选的商品（与 ScopeProductOut 一致）。
export interface PromotionScopeProduct {
  id: number;
  slug: string;
  name_en: string | null;
}

// 提交的一条规则（与 RuleIn 一致）：参数没有值时为 null。
export interface PromotionRuleInput {
  kind: string;
  discount_mode: string | null;
  percent: number | null;
  amount_sen: number | null;
  buy_quantity: number | null;
  get_quantity: number | null;
  get_percent: number | null;
  threshold_sen: number | null;
  product_ids: number[];
  category_ids: number[];
  tiers: PromotionTier[];
}

// 读回的一条规则（与 RuleOut 一致）：提交的各项另加 rule_id 与 products（与 product_ids 一一对应）；提交时这两项不带进请求体。
export interface AdminPromotionRule extends PromotionRuleInput {
  rule_id: number;
  products: PromotionScopeProduct[];
}

// 装饰（与 DecorationIn 一致）。
export interface PromotionDecoration {
  pack: string;
  palette: string;
  ambient: string;
  home_poster: string;
  list_strip: string;
  card_badge: string;
  detail_ribbon: string;
  cart_progress: string;
  poster_mode: string;
}

// 文案（与 CopyIn 一致）：五项各三语，字符串或 null。
export interface PromotionCopy {
  title_en: string | null;
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
  poster_alt_en: string | null;
  poster_alt_zh: string | null;
  poster_alt_ms: string | null;
}

const COPY_KEYS = [
  "title_en",
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
  "poster_alt_en",
  "poster_alt_zh",
  "poster_alt_ms",
] as const satisfies readonly (keyof PromotionCopy)[];

const DECORATION_KEYS = [
  "pack",
  "palette",
  "ambient",
  "home_poster",
  "list_strip",
  "card_badge",
  "detail_ribbon",
  "cart_progress",
  "poster_mode",
] as const satisfies readonly (keyof PromotionDecoration)[];

// 一个活动（与 PromotionOut 一致）。
export interface AdminPromotion {
  id: number;
  version: number;
  state: PromotionState;
  ended_early_at: string | null;
  name: string;
  starts_at: string | null;
  ends_at: string | null;
  coupon_combinable: boolean;
  points_combinable: boolean;
  rules: AdminPromotionRule[];
  decoration: PromotionDecoration;
  copy: PromotionCopy;
  poster_desktop_url: string | null;
  poster_phone_url: string | null;
}

// 新建的内容（与 PromotionIn 一致）。
export interface PromotionInput {
  name: string;
  starts_at: string | null;
  ends_at: string | null;
  coupon_combinable: boolean;
  points_combinable: boolean;
  rules: PromotionRuleInput[];
  decoration: PromotionDecoration;
  copy: PromotionCopy;
}

// 保存的内容（与 PromotionSaveIn 一致）：新建的各项另加读取时的版本号。
export interface PromotionSave extends PromotionInput {
  version: number;
}

// 取值不合格的一条问题（服务层 PromotionProblem）：没有对应商品时 product_id 与 product_name 为 null。
export interface PromotionProblem {
  code: string;
  field: string;
  product_id: number | null;
  product_name: string | null;
}

// 列表的结果：取到（200，带按接口顺序的活动与只用于写请求头的 CSRF 令牌）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断（含调用方中止）。
export type PromotionsRead =
  | { kind: "ok"; promotions: AdminPromotionSummary[]; csrf_token: string }
  | { kind: "none" }
  | { kind: "failed" }
  | { kind: "network" };

// 读取一个的结果：取到（200）、没有会话（401）、不存在（404 或路径 ID 不合法，后者不发请求）、其他失败、网络中断（含调用方中止）。
export type PromotionRead =
  | { kind: "ok"; promotion: AdminPromotion; csrf_token: string }
  | { kind: "none" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "network" };

// 写操作的拒绝：没有会话（401）、CSRF 令牌不对（403）、取值不合格（422 promotion_invalid）、与其他已发布活动时间重叠（409 promotion_overlap，name 为
// 对方的内部名称）、版本不符（409 promotion_changed）、当前状态不允许（409 promotion_state_invalid，带当前状态）、已结束（409 promotion_ended）、
// 不存在（404 或路径 ID 不合法，后者不发请求）、其他失败（含 413、415、其他 422、其他 409 与响应体不合格）、网络中断（含调用方中止）。
export type PromotionRejection =
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "invalid"; problems: PromotionProblem[] }
  | { kind: "overlap"; name: string }
  | { kind: "changed" }
  | { kind: "state"; state: PromotionState }
  | { kind: "ended" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "network" };

// 新建、保存、发布、撤回、提前结束与复制的结果：成功带服务端返回的活动。
export type PromotionWrite = { kind: "ok"; promotion: AdminPromotion } | PromotionRejection;

// 删除的结果：成功为 204，没有响应体。
export type PromotionDelete = { kind: "ok" } | PromotionRejection;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isCountOrNull(value: unknown): value is number | null {
  return value === null || isNonNegativeInteger(value);
}

function isTextOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isState(value: unknown): value is PromotionState {
  return typeof value === "string" && (PROMOTION_STATES as readonly string[]).includes(value);
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

function readId(value: unknown): number | null {
  return isPositiveInteger(value) ? value : null;
}

// 列表的一项：id 为正整数、name 为字符串、state 为四个值之一、两个时间为字符串或 null、rule_count 为不小于 0 的整数、updated_at 为字符串。
function readSummary(value: unknown): AdminPromotionSummary | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, name, state, starts_at, ends_at, rule_count, updated_at } = value;
  if (
    !isPositiveInteger(id) ||
    typeof name !== "string" ||
    !isState(state) ||
    !isTextOrNull(starts_at) ||
    !isTextOrNull(ends_at) ||
    !isNonNegativeInteger(rule_count) ||
    typeof updated_at !== "string"
  ) {
    return null;
  }
  return { id, name, state, starts_at, ends_at, rule_count, updated_at };
}

function readTier(value: unknown): PromotionTier | null {
  if (!isRecord(value)) {
    return null;
  }
  const { threshold_sen, amount_sen, percent } = value;
  if (!isNonNegativeInteger(threshold_sen) || !isCountOrNull(amount_sen) || !isCountOrNull(percent)) {
    return null;
  }
  return { threshold_sen, amount_sen, percent };
}

function readScopeProduct(value: unknown): PromotionScopeProduct | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, slug, name_en } = value;
  if (!isPositiveInteger(id) || typeof slug !== "string" || !isTextOrNull(name_en)) {
    return null;
  }
  return { id, slug, name_en };
}

// 一条规则：rule_id 为正整数、kind 为字符串、discount_mode 为字符串或 null、各数值参数为不小于 0 的整数或 null，商品与分类 ID 为正整数的数组，
// tiers 与 products 每项合格。
function readRule(value: unknown): AdminPromotionRule | null {
  if (!isRecord(value)) {
    return null;
  }
  const { rule_id, kind, discount_mode, percent, amount_sen, buy_quantity, get_quantity, get_percent, threshold_sen } = value;
  const product_ids = readEach(value.product_ids, readId);
  const category_ids = readEach(value.category_ids, readId);
  const tiers = readEach(value.tiers, readTier);
  const products = readEach(value.products, readScopeProduct);
  if (
    !isPositiveInteger(rule_id) ||
    typeof kind !== "string" ||
    !isTextOrNull(discount_mode) ||
    !isCountOrNull(percent) ||
    !isCountOrNull(amount_sen) ||
    !isCountOrNull(buy_quantity) ||
    !isCountOrNull(get_quantity) ||
    !isCountOrNull(get_percent) ||
    !isCountOrNull(threshold_sen) ||
    product_ids === null ||
    category_ids === null ||
    tiers === null ||
    products === null
  ) {
    return null;
  }
  return {
    rule_id,
    kind,
    discount_mode,
    percent,
    amount_sen,
    buy_quantity,
    get_quantity,
    get_percent,
    threshold_sen,
    product_ids,
    category_ids,
    tiers,
    products,
  };
}

// 装饰：九项都为字符串。
function readDecoration(value: unknown): PromotionDecoration | null {
  if (!isRecord(value)) {
    return null;
  }
  const decoration: Partial<PromotionDecoration> = {};
  for (const key of DECORATION_KEYS) {
    const text = value[key];
    if (typeof text !== "string") {
      return null;
    }
    decoration[key] = text;
  }
  return decoration as PromotionDecoration;
}

// 文案：十五项都在，每项为字符串或 null。
function readCopy(value: unknown): PromotionCopy | null {
  if (!isRecord(value)) {
    return null;
  }
  const copy: Partial<PromotionCopy> = {};
  for (const key of COPY_KEYS) {
    const text = value[key];
    if (!isTextOrNull(text)) {
      return null;
    }
    copy[key] = text;
  }
  return copy as PromotionCopy;
}

// 一个活动：id 为正整数、version 为不小于 0 的整数、state 为四个值之一、时间与海报地址为字符串或 null、两个同用开关为布尔值、各规则、装饰与文案合格。
function readPromotion(value: unknown): AdminPromotion | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, version, state, ended_early_at, name, starts_at, ends_at, coupon_combinable, points_combinable, poster_desktop_url, poster_phone_url } = value;
  const rules = readEach(value.rules, readRule);
  const decoration = readDecoration(value.decoration);
  const copy = readCopy(value.copy);
  if (
    !isPositiveInteger(id) ||
    !isNonNegativeInteger(version) ||
    !isState(state) ||
    !isTextOrNull(ended_early_at) ||
    typeof name !== "string" ||
    !isTextOrNull(starts_at) ||
    !isTextOrNull(ends_at) ||
    typeof coupon_combinable !== "boolean" ||
    typeof points_combinable !== "boolean" ||
    rules === null ||
    decoration === null ||
    copy === null ||
    !isTextOrNull(poster_desktop_url) ||
    !isTextOrNull(poster_phone_url)
  ) {
    return null;
  }
  return {
    id,
    version,
    state,
    ended_early_at,
    name,
    starts_at,
    ends_at,
    coupon_combinable,
    points_combinable,
    rules,
    decoration,
    copy,
    poster_desktop_url,
    poster_phone_url,
  };
}

// 列表的响应体：promotions 为数组（按接口给的顺序，任一项不合格为 null）与非空的 csrf_token。
function readList(value: unknown): { promotions: AdminPromotionSummary[]; csrf_token: string } | null {
  if (!isRecord(value)) {
    return null;
  }
  const { csrf_token } = value;
  const promotions = readEach(value.promotions, readSummary);
  if (typeof csrf_token !== "string" || csrf_token === "" || promotions === null) {
    return null;
  }
  return { promotions, csrf_token };
}

// 读取一个的响应体（PromotionPageOut）：活动的各字段与非空的 csrf_token 在同一层。
function readPage(value: unknown): { promotion: AdminPromotion; csrf_token: string } | null {
  if (!isRecord(value)) {
    return null;
  }
  const { csrf_token } = value;
  const promotion = readPromotion(value);
  if (typeof csrf_token !== "string" || csrf_token === "" || promotion === null) {
    return null;
  }
  return { promotion, csrf_token };
}

// 取值不合格的一条问题：四项都在，code 与 field 为字符串，product_id 为正整数或 null，product_name 为字符串或 null。
function readProblem(value: unknown): PromotionProblem | null {
  if (!isRecord(value)) {
    return null;
  }
  const { code, field, product_id, product_name } = value;
  if (typeof code !== "string" || typeof field !== "string" || !(product_id === null || isPositiveInteger(product_id)) || !isTextOrNull(product_name)) {
    return null;
  }
  return { code, field, product_id, product_name };
}

// 422 的响应体：detail 为 {"code": "promotion_invalid", "problems": [...]} 且每项问题合格时为 invalid，否则为 null。
function readInvalid(body: unknown): PromotionRejection | null {
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

// 409 的响应体：detail 为 promotion_changed、promotion_ended，或 {"code": "promotion_overlap", "name": …}、{"code": "promotion_state_invalid", "state": …}；
// 其他为 null。
function readConflict(body: unknown): PromotionRejection | null {
  if (!isRecord(body)) {
    return null;
  }
  const { detail } = body;
  if (detail === "promotion_changed") {
    return { kind: "changed" };
  }
  if (detail === "promotion_ended") {
    return { kind: "ended" };
  }
  if (!isRecord(detail)) {
    return null;
  }
  if (detail.code === "promotion_overlap" && typeof detail.name === "string") {
    return { kind: "overlap", name: detail.name };
  }
  if (detail.code === "promotion_state_invalid" && isState(detail.state)) {
    return { kind: "state", state: detail.state };
  }
  return null;
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

// 写操作在成功状态之外的回答按状态码与 detail 归类。
async function rejection(response: Response): Promise<PromotionRejection> {
  switch (response.status) {
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 404:
      return { kind: "missing" };
    case 422:
      return (await readBody(response, readInvalid)) ?? { kind: "failed" };
    case 409:
      return (await readBody(response, readConflict)) ?? { kind: "failed" };
    default:
      return { kind: "failed" };
  }
}

// 路径里的活动 ID：不超过 MAX_PROMOTION_ID 的正整数（与服务端相同），否则不发请求。
function isPromotionId(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 1 && value <= MAX_PROMOTION_ID;
}

export function adminPromotionUrl(promotionId: number): string {
  return `${ADMIN_PROMOTIONS_URL}/${String(promotionId)}`;
}

export function adminPromotionActionUrl(promotionId: number, action: "publish" | "withdraw" | "end" | "copy"): string {
  return `${adminPromotionUrl(promotionId)}/${action}`;
}

// 请求体只取规定的字段，调用方对象上多出的属性（含读回规则的 rule_id 与 products）不带进请求体；参数没有值时提交 null，满额档恰为三个键。
function ruleBody(rule: PromotionRuleInput): PromotionRuleInput {
  return {
    kind: rule.kind,
    discount_mode: rule.discount_mode ?? null,
    percent: rule.percent ?? null,
    amount_sen: rule.amount_sen ?? null,
    buy_quantity: rule.buy_quantity ?? null,
    get_quantity: rule.get_quantity ?? null,
    get_percent: rule.get_percent ?? null,
    threshold_sen: rule.threshold_sen ?? null,
    product_ids: [...rule.product_ids],
    category_ids: [...rule.category_ids],
    tiers: rule.tiers.map((tier) => ({ threshold_sen: tier.threshold_sen, amount_sen: tier.amount_sen ?? null, percent: tier.percent ?? null })),
  };
}

function decorationBody(decoration: PromotionDecoration): PromotionDecoration {
  const body: Partial<PromotionDecoration> = {};
  for (const key of DECORATION_KEYS) {
    body[key] = decoration[key];
  }
  return body as PromotionDecoration;
}

function copyBody(copy: PromotionCopy): PromotionCopy {
  const body: Partial<PromotionCopy> = {};
  for (const key of COPY_KEYS) {
    body[key] = copy[key] ?? null;
  }
  return body as PromotionCopy;
}

function createBody(input: PromotionInput): PromotionInput {
  return {
    name: input.name,
    starts_at: input.starts_at,
    ends_at: input.ends_at,
    coupon_combinable: input.coupon_combinable,
    points_combinable: input.points_combinable,
    rules: input.rules.map(ruleBody),
    decoration: decorationBody(input.decoration),
    copy: copyBody(input.copy),
  };
}

function saveBody(input: PromotionSave): PromotionSave {
  return { ...createBody(input), version: input.version };
}

// 写请求：同源 cookie、不缓存，请求头带读取给的 CSRF 令牌；有请求体时为 JSON。fetch 抛错（含调用方中止）为 null。
async function send(url: string, method: "POST" | "PUT" | "DELETE", body: unknown, csrfToken: string, signal?: AbortSignal): Promise<Response | null> {
  const headers: Record<string, string> = { Accept: "application/json", [CSRF_HEADER]: csrfToken };
  const init: RequestInit = { method, credentials: "same-origin", cache: "no-store", headers, signal: signal ?? null };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  try {
    return await fetch(url, init);
  } catch {
    return null;
  }
}

// 写操作：成功状态（新建与复制 201，其余 200）带回一个合格的活动，其余按状态码与 detail 归类。
async function writePromotion(url: string, method: "POST" | "PUT", okStatus: number, body: unknown, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  const response = await send(url, method, body, csrfToken, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status === okStatus) {
    const promotion = await readBody(response, readPromotion);
    return promotion === null ? { kind: "failed" } : { kind: "ok", promotion };
  }
  return rejection(response);
}

// GET /api/admin/promotions：同源 cookie、不缓存、没有请求体。
export async function readAdminPromotions(signal?: AbortSignal): Promise<PromotionsRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_PROMOTIONS_URL, {
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
  const list = await readBody(response, readList);
  return list === null ? { kind: "failed" } : { kind: "ok", ...list };
}

// GET /api/admin/promotions/{promotion_id}：路径 ID 不合法时不发请求，为 missing。
export async function readAdminPromotion(promotionId: number, signal?: AbortSignal): Promise<PromotionRead> {
  if (!isPromotionId(promotionId)) {
    return { kind: "missing" };
  }
  let response: Response;
  try {
    response = await fetch(adminPromotionUrl(promotionId), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const page = await readBody(response, readPage);
      return page === null ? { kind: "failed" } : { kind: "ok", ...page };
    }
    case 401:
      return { kind: "none" };
    case 404:
      return { kind: "missing" };
    default:
      return { kind: "failed" };
  }
}

// POST /api/admin/promotions：JSON 请求体为 PromotionIn 的字段；成功为 201 与新草稿。
export function createAdminPromotion(input: PromotionInput, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  return writePromotion(ADMIN_PROMOTIONS_URL, "POST", 201, createBody(input), csrfToken, signal);
}

// PUT /api/admin/promotions/{promotion_id}：JSON 请求体为新建的各项另加 version；成功或未变都为 200。
export async function saveAdminPromotion(promotionId: number, input: PromotionSave, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  if (!isPromotionId(promotionId)) {
    return { kind: "missing" };
  }
  return writePromotion(adminPromotionUrl(promotionId), "PUT", 200, saveBody(input), csrfToken, signal);
}

// 发布、撤回与提前结束：POST …/{promotion_id}/{action}，JSON 请求体只有 version；成功为 200。
async function changeState(action: "publish" | "withdraw" | "end", promotionId: number, version: number, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  if (!isPromotionId(promotionId)) {
    return { kind: "missing" };
  }
  return writePromotion(adminPromotionActionUrl(promotionId, action), "POST", 200, { version }, csrfToken, signal);
}

// POST …/{promotion_id}/publish：发布草稿。
export function publishAdminPromotion(promotionId: number, version: number, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  return changeState("publish", promotionId, version, csrfToken, signal);
}

// POST …/{promotion_id}/withdraw：撤回为草稿。
export function withdrawAdminPromotion(promotionId: number, version: number, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  return changeState("withdraw", promotionId, version, csrfToken, signal);
}

// POST …/{promotion_id}/end：提前结束。
export function endAdminPromotion(promotionId: number, version: number, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  return changeState("end", promotionId, version, csrfToken, signal);
}

// POST …/{promotion_id}/copy：没有请求体（也不带 Content-Type），请求头只有 Accept 与 CSRF 令牌；成功为 201 与新草稿。
export async function copyAdminPromotion(promotionId: number, csrfToken: string, signal?: AbortSignal): Promise<PromotionWrite> {
  if (!isPromotionId(promotionId)) {
    return { kind: "missing" };
  }
  return writePromotion(adminPromotionActionUrl(promotionId, "copy"), "POST", 201, undefined, csrfToken, signal);
}

// DELETE /api/admin/promotions/{promotion_id}：JSON 请求体只有 version；成功为 204，没有响应体，不读。
export async function deleteAdminPromotion(promotionId: number, version: number, csrfToken: string, signal?: AbortSignal): Promise<PromotionDelete> {
  if (!isPromotionId(promotionId)) {
    return { kind: "missing" };
  }
  const response = await send(adminPromotionUrl(promotionId), "DELETE", { version }, csrfToken, signal);
  if (response === null) {
    return { kind: "network" };
  }
  return response.status === 204 ? { kind: "ok" } : rejection(response);
}
