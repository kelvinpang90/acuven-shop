import type { Language } from "../i18n/copy";

// 后台退款申请列表 A03 的接口（app/api/admin_refunds.py，SHOP-TASK-041 与 SHOP-TASK-046）：POST /api/admin/refunds/query?lang=。
// 后台会话凭服务端发的 HttpOnly cookie；列表只读，不要求 CSRF。唯一的查询参数是语言 lang；状态、订单内部 ID 与页码只在请求体里。
// 列表不含收货资料与理由；订单号只在响应体与页面内存里，不进路径、查询参数、localStorage、sessionStorage、cookie 或日志。
// 金额为接口给的申请金额快照（整数仙），页面只格式化、不计算。错误体为 {"detail": ...}；页面只按状态码区分，不显示错误体。

export const ADMIN_REFUNDS_QUERY_URL = "/api/admin/refunds/query";

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
