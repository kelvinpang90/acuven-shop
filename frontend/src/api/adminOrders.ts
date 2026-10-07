import type { Language } from "../i18n/copy";
import { CSRF_HEADER } from "./pay";
import type { PayRecipient } from "./pay";

// 后台订单 A02 的三个接口（app/api/admin_orders.py，SHOP-TASK-037）：订单列表 POST /api/admin/orders/query、
// 订单详情 GET /api/admin/orders/{内部 ID} 与模拟发货推进 POST /api/admin/orders/{内部 ID}/status。
// 后台会话凭服务端发的 HttpOnly cookie；列表与详情只读，推进带详情返回的 CSRF 令牌。
// 订单号只放在列表的请求体里，路径只用订单的内部 ID；订单号、收货资料与令牌不进路径、查询参数、localStorage、sessionStorage、
// cookie 或日志。列表不含收货资料；金额为接口给的整数仙快照，页面只格式化、不计算。
// 错误体为 {"detail": "<错误码>"}；页面只按状态码（推进的 409 另按错误码）区分，不显示错误体。

export const ADMIN_ORDERS_QUERY_URL = "/api/admin/orders/query";
const ADMIN_ORDERS_URL = "/api/admin/orders";

// 订单内部 ID 的上限，与 app/api/admin_orders.py 的 MAX_ORDER_ID 相同。
export const MAX_ORDER_ID = 2147483647;

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

// 路径里的订单 ID：只接受不带符号与前导零、不超过 MAX_ORDER_ID 的十进制正整数（与服务端相同），否则为 null，不发请求。
export function parseOrderId(value: string): number | null {
  if (!/^[1-9][0-9]*$/.test(value)) {
    return null;
  }
  const id = Number(value);
  return id <= MAX_ORDER_ID ? id : null;
}

export function adminOrderUrl(id: number, language: Language): string {
  return `${ADMIN_ORDERS_URL}/${String(id)}?${new URLSearchParams({ lang: language }).toString()}`;
}

export function adminOrderStatusUrl(id: number): string {
  return `${ADMIN_ORDERS_URL}/${String(id)}/status`;
}

// 事件的操作者类别（服务端 OrderEvent 的 actor_type）。
export const ACTOR_TYPES = ["guest", "member", "admin", "system"] as const;
export type ActorType = (typeof ACTOR_TYPES)[number];

function isActorType(value: unknown): value is ActorType {
  return typeof value === "string" && (ACTOR_TYPES as readonly string[]).includes(value);
}

// 详情的一行商品：名称与规格（按请求的语言）、件数、单价、行小计与逐件实付（整数仙，未支付时为空）。
export interface AdminOrderLine {
  name: string;
  variant_label: string;
  quantity: number;
  unit_price_sen: number;
  line_subtotal_sen: number;
  unit_cash_paid_sen: number[];
}

// 一条事件：时间、迁移后的状态与操作者类别；不含收货资料。
export interface AdminOrderEvent {
  created_at: string;
  status: OrderStatus;
  actor_type: ActorType;
}

// 订单详情：只取 SHOP-TASK-037 记录段列出的字段。recipient 为收货资料原文（没有记录时为 null）；
// fully_refunded 为全部已退（冻结履约）；csrf_token 只用于推进的请求头。
export interface AdminOrderDetail {
  id: number;
  order_number: string;
  status: OrderStatus;
  created_at: string;
  paid_at: string | null;
  lines: AdminOrderLine[];
  subtotal_sen: number;
  coupon_discount_sen: number;
  points_discount_sen: number;
  shipping_fee_sen: number;
  total_sen: number;
  recipient: PayRecipient | null;
  events: AdminOrderEvent[];
  refunds_pending: number;
  fully_refunded: boolean;
  csrf_token: string;
}

// 读取详情的结果：取到（200）、没有会话（401）、订单不存在（404，路径 ID 不合法时不发请求也归为此类）、
// 其他失败（含 200 但响应体不合格）、网络中断。
export type DetailRead =
  | { kind: "ok"; order: AdminOrderDetail }
  | { kind: "none" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "network" };

// 时间沿用 readRow 对 created_at 的写法：字符串且能解析成日期。
function isTime(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function readLine(value: unknown): AdminOrderLine | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { name, variant_label, quantity, unit_price_sen, line_subtotal_sen, unit_cash_paid_sen } = value as Record<string, unknown>;
  if (
    typeof name !== "string" ||
    typeof variant_label !== "string" ||
    !isPositive(quantity) ||
    !isCount(unit_price_sen) ||
    !isCount(line_subtotal_sen) ||
    !Array.isArray(unit_cash_paid_sen) ||
    !(unit_cash_paid_sen as unknown[]).every(isCount)
  ) {
    return null;
  }
  return { name, variant_label, quantity, unit_price_sen, line_subtotal_sen, unit_cash_paid_sen: [...(unit_cash_paid_sen as number[])] };
}

function readRecipient(value: unknown): PayRecipient | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { name, phone, country_code, address, postal_code } = value as Record<string, unknown>;
  const raw = (value as Record<string, unknown>).region;
  const region = raw === null || typeof raw === "string" ? raw : undefined;
  if (
    typeof name !== "string" ||
    typeof phone !== "string" ||
    typeof country_code !== "string" ||
    region === undefined ||
    typeof address !== "string" ||
    typeof postal_code !== "string"
  ) {
    return null;
  }
  return { name, phone, country_code, region, address, postal_code };
}

function readEvent(value: unknown): AdminOrderEvent | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { created_at, status, actor_type } = value as Record<string, unknown>;
  if (!isTime(created_at) || !isOrderStatus(status) || !isActorType(actor_type)) {
    return null;
  }
  return { created_at, status, actor_type };
}

// 逐项读取；任一项不合格整个数组不合格。
function readEach<T>(value: unknown, read: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const items: T[] = [];
  for (const item of value as unknown[]) {
    const found = read(item);
    if (found === null) {
      return null;
    }
    items.push(found);
  }
  return items;
}

function readDetail(value: unknown): AdminOrderDetail | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const body = value as Record<string, unknown>;
  const { id, order_number, status, created_at, subtotal_sen, coupon_discount_sen, points_discount_sen } = body;
  const { shipping_fee_sen, total_sen, recipient, refunds_pending, fully_refunded, csrf_token } = body;
  // 未支付时 paid_at 为 null；不为 null 时须是能解析的时间（undefined 即不合格）。
  const paid_at = body.paid_at === null || isTime(body.paid_at) ? body.paid_at : undefined;
  const lines = readEach(body.lines, readLine);
  const events = readEach(body.events, readEvent);
  const shipTo = recipient === null ? null : readRecipient(recipient);
  if (
    !isPositive(id) ||
    typeof order_number !== "string" ||
    !isOrderStatus(status) ||
    !isTime(created_at) ||
    paid_at === undefined ||
    lines === null ||
    !isCount(subtotal_sen) ||
    !isCount(coupon_discount_sen) ||
    !isCount(points_discount_sen) ||
    !isCount(shipping_fee_sen) ||
    !isCount(total_sen) ||
    (recipient !== null && shipTo === null) ||
    events === null ||
    !isCount(refunds_pending) ||
    typeof fully_refunded !== "boolean" ||
    typeof csrf_token !== "string"
  ) {
    return null;
  }
  return {
    id,
    order_number,
    status,
    created_at,
    paid_at,
    lines,
    subtotal_sen,
    coupon_discount_sen,
    points_discount_sen,
    shipping_fee_sen,
    total_sen,
    recipient: shipTo,
    events,
    refunds_pending,
    fully_refunded,
    csrf_token,
  };
}

// GET /api/admin/orders/{内部 ID}?lang=：同源 cookie、不缓存。路径 ID 不合法时不发请求。
export async function readAdminOrder(id: string, language: Language, signal?: AbortSignal): Promise<DetailRead> {
  const orderId = parseOrderId(id);
  if (orderId === null) {
    return { kind: "missing" };
  }
  let response: Response;
  try {
    response = await fetch(adminOrderUrl(orderId, language), {
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
  const order = readDetail(body);
  return order === null ? { kind: "failed" } : { kind: "ok", order };
}

// 推进的目标状态：服务端只接受这两种。
export type AdvanceTarget = "demo_packed" | "demo_shipped";

// 推进的回答：done 为 200（推进了或已是目标状态）；conflict 为 409 order_not_advanceable 或 fulfilment_frozen；
// csrf 为 403；none 为 401；failed 为其他（含别的 409）；network 为网络中断。
export type AdvanceReply = "done" | "conflict" | "csrf" | "none" | "failed" | "network";

const CONFLICT_DETAILS: ReadonlySet<string> = new Set(["order_not_advanceable", "fulfilment_frozen"]);

async function errorCode(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// POST /api/admin/orders/{内部 ID}/status：JSON 请求体只有 status，请求头带详情给的 CSRF 令牌；同源 cookie、不缓存。
// 路径 ID 不合法时不发请求。
export async function advanceAdminOrder(id: string, status: AdvanceTarget, csrfToken: string): Promise<AdvanceReply> {
  const orderId = parseOrderId(id);
  if (orderId === null) {
    return "failed";
  }
  let response: Response;
  try {
    response = await fetch(adminOrderStatusUrl(orderId), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({ status }),
    });
  } catch {
    return "network";
  }
  if (response.status === 200) {
    return "done";
  }
  if (response.status === 401) {
    return "none";
  }
  if (response.status === 403) {
    return "csrf";
  }
  if (response.status === 409) {
    const code = await errorCode(response);
    return code !== null && CONFLICT_DETAILS.has(code) ? "conflict" : "failed";
  }
  return "failed";
}
