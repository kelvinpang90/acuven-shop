import type { Language } from "../i18n/copy";
import { isOrderStatus } from "./adminOrders";
import type { OrderStatus } from "./adminOrders";
import { CSRF_HEADER, IDEMPOTENCY_HEADER } from "./pay";

// 后台退款审核 A03 的接口（app/api/admin_refunds.py，SHOP-TASK-041 与 SHOP-TASK-046）：申请列表 POST /api/admin/refunds/query?lang=、
// 申请详情 GET /api/admin/refunds/{内部 ID}?lang=、批准与拒绝 POST /api/admin/refunds/{内部 ID}/approve 与 /reject。
// 后台会话凭服务端发的 HttpOnly cookie；列表与详情只读，不要求 CSRF；批准与拒绝带详情返回的 CSRF 令牌与幂等键。
// 唯一的查询参数是语言 lang；路径只用申请的内部 ID；状态、订单内部 ID 与页码只在列表的请求体里，理由只在审核的请求体里。
// 列表不含收货资料与理由；订单号、理由、审核人邮箱与令牌只在响应体、请求与页面内存里，不进路径、查询参数、localStorage、
// sessionStorage、cookie 或日志。金额为接口给的整数仙快照，页面只格式化、不计算。错误体为 {"detail": ...}；
// 页面只按状态码（审核的 409 另按错误码）区分，不显示错误体。

export const ADMIN_REFUNDS_QUERY_URL = "/api/admin/refunds/query";
const ADMIN_REFUNDS_URL = "/api/admin/refunds";

// 退款申请内部 ID 的上限，与 app/api/admin_refunds.py 校验路径 ID 的上限（2147483647）相同。
export const MAX_REFUND_ID = 2147483647;

// docs/DESIGN.md「订单与退款状态」的三种退款申请状态（与服务端相同）：审核中、已批准、已拒绝。
export const REFUND_STATUSES = ["requested", "approved", "rejected"] as const;
export type RefundStatus = (typeof REFUND_STATUSES)[number];

export function isRefundStatus(value: unknown): value is RefundStatus {
  return typeof value === "string" && (REFUND_STATUSES as readonly string[]).includes(value);
}

// 一次查询：状态（null 为全部）、所属订单的内部 ID（null 为不按订单筛选）与页码（从 1 起）。
export interface RefundsQuery {
  status: RefundStatus | null;
  order_id: number | null;
  page: number;
}

// 申请的一行商品：按请求语言的商品名称快照与申请件数。
export interface AdminRefundLine {
  name: string;
  quantity: number;
}

// 列表的一行：申请的内部 ID、申请时间（带时区的 ISO 字符串）、所属订单的内部 ID 与订单号、状态、申请金额（整数仙）与各行商品。
export interface AdminRefundRow {
  id: number;
  created_at: string;
  order_id: number;
  order_number: string;
  status: RefundStatus;
  amount_sen: number;
  lines: AdminRefundLine[];
}

// 一页：符合条件的总数、页码、每页条数与本页各申请。
export interface AdminRefundList {
  total: number;
  page: number;
  page_size: number;
  refunds: AdminRefundRow[];
}

// 查询的结果：取到（200）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断。
export type RefundsRead = { kind: "ok"; list: AdminRefundList } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isPositive(value: unknown): value is number {
  return isCount(value) && value > 0;
}

function readLine(value: unknown): AdminRefundLine | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { name, quantity } = value as Record<string, unknown>;
  if (typeof name !== "string" || !isPositive(quantity)) {
    return null;
  }
  return { name, quantity };
}

// 每行只取 SHOP-TASK-041 记录段列出的七个字段；申请时间能解析成日期，状态是三种之一，金额为非负整数，件数为正整数。
function readRow(value: unknown): AdminRefundRow | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { id, created_at, order_id, order_number, status, amount_sen, lines } = value as Record<string, unknown>;
  if (
    !isPositive(id) ||
    typeof created_at !== "string" ||
    Number.isNaN(Date.parse(created_at)) ||
    !isPositive(order_id) ||
    typeof order_number !== "string" ||
    !isRefundStatus(status) ||
    !isCount(amount_sen) ||
    !Array.isArray(lines)
  ) {
    return null;
  }
  const items: AdminRefundLine[] = [];
  for (const item of lines as unknown[]) {
    const line = readLine(item);
    if (line === null) {
      return null;
    }
    items.push(line);
  }
  return { id, created_at, order_id, order_number, status, amount_sen, lines: items };
}

function readList(value: unknown): AdminRefundList | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { total, page, page_size, refunds } = value as Record<string, unknown>;
  if (!isCount(total) || !isPositive(page) || !isPositive(page_size) || !Array.isArray(refunds)) {
    return null;
  }
  const rows: AdminRefundRow[] = [];
  for (const item of refunds as unknown[]) {
    const row = readRow(item);
    if (row === null) {
      return null;
    }
    rows.push(row);
  }
  return { total, page, page_size, refunds: rows };
}

export function adminRefundsQueryUrl(language: Language): string {
  return `${ADMIN_REFUNDS_QUERY_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// POST /api/admin/refunds/query?lang=：同源 cookie、不缓存；请求体只放这三个字段。
export async function queryAdminRefunds(query: RefundsQuery, language: Language, signal?: AbortSignal): Promise<RefundsRead> {
  let response: Response;
  try {
    response = await fetch(adminRefundsQueryUrl(language), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ status: query.status, order_id: query.order_id, page: query.page }),
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
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: "failed" };
  }
  const list = readList(body);
  return list === null ? { kind: "failed" } : { kind: "ok", list };
}

// 路径里的申请 ID：只接受不带符号与前导零、不超过 MAX_REFUND_ID 的十进制正整数（与服务端相同），否则为 null，不发请求。
export function parseRefundId(value: string): number | null {
  if (!/^[1-9][0-9]*$/.test(value)) {
    return null;
  }
  const id = Number(value);
  return id <= MAX_REFUND_ID ? id : null;
}

export function adminRefundUrl(id: number, language: Language): string {
  return `${ADMIN_REFUNDS_URL}/${String(id)}?${new URLSearchParams({ lang: language }).toString()}`;
}

// 审核的两种操作：批准与拒绝（各是一个接口）。
export type ReviewAction = "approve" | "reject";

export function adminRefundReviewUrl(id: number, action: ReviewAction): string {
  return `${ADMIN_REFUNDS_URL}/${String(id)}/${action}`;
}

// 详情的一行商品：名称与规格（按请求的语言）、申请件数、该行购买件数与该行被已批准申请占用的件数。
export interface AdminRefundDetailLine {
  name: string;
  variant_label: string;
  quantity: number;
  purchased_quantity: number;
  approved_quantity: number;
}

// 申请详情：只取 SHOP-TASK-041 记录段列出的字段。reviewed_at、reviewer_username（管理员邮箱）未审核时为 null；
// review_reason 未审核或批准时没写理由为 null；refunded_sen 与 refundable_left_sen 为该单累计已退与剩余可退；
// csrf_token 只用于审核的请求头。
export interface AdminRefundDetail {
  id: number;
  status: RefundStatus;
  created_at: string;
  reviewed_at: string | null;
  reviewer_username: string | null;
  review_reason: string | null;
  order_id: number;
  order_number: string;
  order_status: OrderStatus;
  amount_sen: number;
  lines: AdminRefundDetailLine[];
  refunded_sen: number;
  refundable_left_sen: number;
  csrf_token: string;
}

// 读取详情的结果：取到（200）、没有会话（401）、申请不存在（404，路径 ID 不合法时不发请求也归为此类）、
// 其他失败（含 200 但响应体不合格）、网络中断。
export type RefundDetailRead =
  | { kind: "ok"; refund: AdminRefundDetail }
  | { kind: "none" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "network" };

// 时间沿用 readRow 对 created_at 的写法：字符串且能解析成日期。
function isTime(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function readDetailLine(value: unknown): AdminRefundDetailLine | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { name, variant_label, quantity, purchased_quantity, approved_quantity } = value as Record<string, unknown>;
  if (typeof name !== "string" || typeof variant_label !== "string" || !isPositive(quantity) || !isPositive(purchased_quantity) || !isCount(approved_quantity)) {
    return null;
  }
  return { name, variant_label, quantity, purchased_quantity, approved_quantity };
}

function readDetail(value: unknown): AdminRefundDetail | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const body = value as Record<string, unknown>;
  const { id, status, created_at, reviewed_at, reviewer_username, review_reason, order_id, order_number, order_status } = body;
  const { amount_sen, refunded_sen, refundable_left_sen, csrf_token } = body;
  if (
    !isPositive(id) ||
    !isRefundStatus(status) ||
    !isTime(created_at) ||
    !(review_reason === null || typeof review_reason === "string") ||
    !isPositive(order_id) ||
    typeof order_number !== "string" ||
    !isOrderStatus(order_status) ||
    !isCount(amount_sen) ||
    !Array.isArray(body.lines) ||
    !isCount(refunded_sen) ||
    !isCount(refundable_left_sen) ||
    typeof csrf_token !== "string"
  ) {
    return null;
  }
  // 审核中的申请没有审核时间与审核人；已批准或已拒绝的两者都有（页面据此显示 admin.reviewed_by）。
  const reviewed = status !== "requested";
  if (reviewed ? !isTime(reviewed_at) || typeof reviewer_username !== "string" : reviewed_at !== null || reviewer_username !== null) {
    return null;
  }
  const lines: AdminRefundDetailLine[] = [];
  for (const item of body.lines as unknown[]) {
    const line = readDetailLine(item);
    if (line === null) {
      return null;
    }
    lines.push(line);
  }
  return {
    id,
    status,
    created_at,
    reviewed_at: reviewed_at as string | null,
    reviewer_username: reviewer_username as string | null,
    review_reason,
    order_id,
    order_number,
    order_status,
    amount_sen,
    lines,
    refunded_sen,
    refundable_left_sen,
    csrf_token,
  };
}

// GET /api/admin/refunds/{内部 ID}?lang=：同源 cookie、不缓存。路径 ID 不合法时不发请求。
export async function readAdminRefund(id: string, language: Language, signal?: AbortSignal): Promise<RefundDetailRead> {
  const refundId = parseRefundId(id);
  if (refundId === null) {
    return { kind: "missing" };
  }
  let response: Response;
  try {
    response = await fetch(adminRefundUrl(refundId, language), {
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
  if (response.status === 404) {
    return { kind: "missing" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: "failed" };
  }
  const refund = readDetail(body);
  return refund === null ? { kind: "failed" } : { kind: "ok", refund };
}

// 审核的回答：done 为 200（新审核或同键重放）；reviewed 为 409 refund_already_reviewed；conflict 为 409 idempotency_conflict；
// csrf 为 403；invalid 为 422；none 为 401；failed 为其他（含别的 409 与 404）；network 为网络中断。
export type ReviewReply = "done" | "reviewed" | "conflict" | "csrf" | "invalid" | "none" | "failed" | "network";

const REVIEW_CONFLICTS: ReadonlyMap<string, ReviewReply> = new Map<string, ReviewReply>([
  ["refund_already_reviewed", "reviewed"],
  ["idempotency_conflict", "conflict"],
]);

async function errorCode(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// POST /api/admin/refunds/{内部 ID}/approve 或 /reject：JSON 请求体只有 reason（输入框原文，去掉首尾空白由服务端负责），
// 请求头带幂等键与详情给的 CSRF 令牌；同源 cookie、不缓存。路径 ID 不合法时不发请求。
export async function reviewAdminRefund(id: string, action: ReviewAction, reason: string, csrfToken: string, key: string): Promise<ReviewReply> {
  const refundId = parseRefundId(id);
  if (refundId === null) {
    return "failed";
  }
  let response: Response;
  try {
    response = await fetch(adminRefundReviewUrl(refundId, action), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        [IDEMPOTENCY_HEADER]: key,
        [CSRF_HEADER]: csrfToken,
      },
      body: JSON.stringify({ reason }),
    });
  } catch {
    return "network";
  }
  switch (response.status) {
    case 200:
      return "done";
    case 401:
      return "none";
    case 403:
      return "csrf";
    case 422:
      return "invalid";
    case 409: {
      const code = await errorCode(response);
      return (code === null ? undefined : REVIEW_CONFLICTS.get(code)) ?? "failed";
    }
    default:
      return "failed";
  }
}

// 一次审核：操作、输入框里的理由与它的幂等键。网络中断后保留，以便再点时判断能否沿用同一个键。
export interface ReviewAttempt {
  action: ReviewAction;
  reason: string;
  key: string;
}

// 审核的幂等键：每次新的审核新生成；只有网络中断后再点、且操作与理由都没变时才沿用原来的键（服务端同键重放安全）。
export function reviewAttempt(retry: ReviewAttempt | null, action: ReviewAction, reason: string, newKey: () => string = () => crypto.randomUUID()): ReviewAttempt {
  const key = retry !== null && retry.action === action && retry.reason === reason ? retry.key : newKey();
  return { action, reason, key };
}
