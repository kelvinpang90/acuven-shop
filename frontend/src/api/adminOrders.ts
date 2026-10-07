// 后台订单 A02 的订单列表接口（app/api/admin_orders.py，SHOP-TASK-037）：POST /api/admin/orders/query。
// 只读，不要求 CSRF；后台会话凭服务端发的 HttpOnly cookie。JSON 请求体只有 order_number、status 与 page；
// 订单号只放在请求体里，不进路径、查询参数、localStorage、sessionStorage 或 cookie。
// 列表不含收货资料；金额为接口给的整数仙快照，页面只格式化、不计算。
// 错误体为 {"detail": "<错误码>"}；页面只按状态码区分，不显示错误体。

export const ADMIN_ORDERS_QUERY_URL = "/api/admin/orders/query";

// docs/DESIGN.md「订单与退款状态」的六种订单状态（与服务端 OrderStatus 相同），依履约顺序，已取消在最后。
export const ORDER_STATUSES = [
  "awaiting_demo_payment",
  "demo_paid",
  "demo_packed",
  "demo_shipped",
  "demo_completed",
  "demo_cancelled",
] as const;
export type OrderStatus = (typeof ORDER_STATUSES)[number];

export function isOrderStatus(value: unknown): value is OrderStatus {
  return typeof value === "string" && (ORDER_STATUSES as readonly string[]).includes(value);
}

// 一次查询：订单号（null 为不按订单号筛选）、状态（null 为全部）与页码（从 1 起）。
export interface OrdersQuery {
  order_number: string | null;
  status: OrderStatus | null;
  page: number;
}

// 列表的一行：内部 ID、订单号、下单时间（带时区的 ISO 字符串）、状态、应付金额（整数仙）与审核中的退款申请数。
export interface AdminOrderRow {
  id: number;
  order_number: string;
  created_at: string;
  status: OrderStatus;
  total_sen: number;
  refunds_pending: number;
}

// 一页：符合条件的总数、页码、每页条数与本页各单。
export interface AdminOrderList {
  total: number;
  page: number;
  page_size: number;
  orders: AdminOrderRow[];
}

// 查询的结果：取到（200）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断。
export type OrdersRead = { kind: "ok"; list: AdminOrderList } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isPositive(value: unknown): value is number {
  return isCount(value) && value > 0;
}

// 每行只取约定的六个字段；金额与待审数为非负整数，下单时间能解析成日期，状态是六种之一。
function readRow(value: unknown): AdminOrderRow | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { id, order_number, created_at, status, total_sen, refunds_pending } = value as Record<string, unknown>;
  if (
    !isPositive(id) ||
    typeof order_number !== "string" ||
    typeof created_at !== "string" ||
    Number.isNaN(Date.parse(created_at)) ||
    !isOrderStatus(status) ||
    !isCount(total_sen) ||
    !isCount(refunds_pending)
  ) {
    return null;
  }
  return { id, order_number, created_at, status, total_sen, refunds_pending };
}

function readList(value: unknown): AdminOrderList | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { total, page, page_size, orders } = value as Record<string, unknown>;
  if (!isCount(total) || !isPositive(page) || !isPositive(page_size) || !Array.isArray(orders)) {
    return null;
  }
  const rows: AdminOrderRow[] = [];
  for (const item of orders as unknown[]) {
    const row = readRow(item);
    if (row === null) {
      return null;
    }
    rows.push(row);
  }
  return { total, page, page_size, orders: rows };
}

// POST /api/admin/orders/query：同源 cookie、不缓存；请求体只放这三个字段。
export async function queryAdminOrders(query: OrdersQuery, signal?: AbortSignal): Promise<OrdersRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_ORDERS_QUERY_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ order_number: query.order_number, status: query.status, page: query.page }),
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
