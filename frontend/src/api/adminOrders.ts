import type { Language } from "../i18n/copy";
import { CSRF_HEADER } from "./pay";

// 后台订单 A02 的接口（app/api/admin_orders.py，SHOP-TASK-037）：订单列表 POST /api/admin/orders/query、
// 订单详情 GET /api/admin/orders/{内部 ID} 与模拟发货推进 POST /api/admin/orders/{内部 ID}/status。
// 后台会话凭服务端发的 HttpOnly cookie；列表与详情只读，推进带详情返回的 CSRF 令牌（请求头名沿用 api/pay.ts）。
// 路径里只有订单的内部整数 ID；订单号只放在列表的请求体里，订单号、收货资料与 CSRF 令牌只在页面内存与请求里，
// 不进路径、查询参数、localStorage、sessionStorage、cookie 或日志。
// 列表不含收货资料；金额为接口给的整数仙快照，页面只格式化、不计算。
// 错误体为 {"detail": "<错误码>"}；页面只按状态码（推进的 409 另看错误码）区分，不显示错误体。

export const ADMIN_ORDERS_QUERY_URL = "/api/admin/orders/query";
const ADMIN_ORDERS_URL = "/api/admin/orders";

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

// 路径里的订单内部 ID：不带符号与前导零的十进制正整数，不超过服务端接受的 2147483647（SHOP-TASK-037 的 order_id_invalid）。
export const MAX_ORDER_ID = 2147483647;

export function orderIdOf(raw: string): number | null {
  if (!/^[1-9][0-9]*$/.test(raw)) {
    return null;
  }
  const id = Number(raw);
  return id <= MAX_ORDER_ID ? id : null;
}

export function adminOrderUrl(id: number, language: Language): string {
  return `${ADMIN_ORDERS_URL}/${String(id)}?${new URLSearchParams({ lang: language }).toString()}`;
}

export function adminOrderStatusUrl(id: number): string {
  return `${ADMIN_ORDERS_URL}/${String(id)}/status`;
}

// 事件的操作者类别（与服务端 ACTOR_TYPES 相同）：访客与会员都显示为 admin.actor_customer。
export const ACTOR_TYPES = ["guest", "member", "admin", "system"] as const;
export type ActorType = (typeof ACTOR_TYPES)[number];

// 订单行快照：按请求语言的名称与规格说明、件数、单价、行小计（整数仙）与逐件实付（按件序）。
export interface AdminOrderLine {
  name: string;
  variant_label: string;
  quantity: number;
  unit_price_sen: number;
  line_subtotal_sen: number;
  unit_cash_paid_sen: number[];
}

// 收货资料原文（字段同 api/pay.ts 的 PayRecipient）：马来西亚的 region 为州属代码，其他国家为自由文本或空。
export interface AdminRecipient {
  name: string;
  phone: string;
  country_code: string;
  region: string | null;
  address: string;
  postal_code: string;
}

// 一条状态事件：时间、迁移后状态与操作者类别；不含收货资料。
export interface AdminOrderEvent {
  created_at: string;
  status: OrderStatus;
  actor_type: ActorType;
}

// 一张订单的后台详情（SHOP-TASK-037 记录段列出的字段，CSRF 令牌另放）。时间都是带时区的 ISO 字符串。
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
  recipient: AdminRecipient | null;
  events: AdminOrderEvent[];
  refunds_pending: number;
  fully_refunded: boolean;
}

// 读取详情的结果：取到（200，带 CSRF 令牌）、没有会话（401）、订单不存在（404，或路径 ID 不合法而没有发请求）、
// 其他失败（含 200 但响应体不合格）、网络中断。
export type DetailRead =
  | { kind: "ok"; order: AdminOrderDetail; csrfToken: string }
  | { kind: "none" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "network" };

function isTime(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function isActorType(value: unknown): value is ActorType {
  return typeof value === "string" && (ACTOR_TYPES as readonly string[]).includes(value);
}

// 读取数组：每一项都合格才算合格。
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

function readLine(value: unknown): AdminOrderLine | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { name, variant_label, quantity, unit_price_sen, line_subtotal_sen, unit_cash_paid_sen } = value as Record<string, unknown>;
  const paid = readEach(unit_cash_paid_sen, (sen) => (isCount(sen) ? sen : null));
  if (
    typeof name !== "string" ||
    typeof variant_label !== "string" ||
    !isPositive(quantity) ||
    !isCount(unit_price_sen) ||
    !isCount(line_subtotal_sen) ||
    paid === null
  ) {
    return null;
  }
  return { name, variant_label, quantity, unit_price_sen, line_subtotal_sen, unit_cash_paid_sen: paid };
}

function readRecipient(value: unknown): AdminRecipient | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { name, phone, country_code, region, address, postal_code } = value as Record<string, unknown>;
  if (
    typeof name !== "string" ||
    typeof phone !== "string" ||
    typeof country_code !== "string" ||
    (region !== null && typeof region !== "string") ||
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

// 只取约定的字段；任一字段类型不对、内部 ID 不是所请求的那一个或没有 CSRF 令牌都算不合格。
function readDetail(value: unknown, requested: number): { order: AdminOrderDetail; csrfToken: string } | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const body = value as Record<string, unknown>;
  const { id, order_number, status, created_at, paid_at, subtotal_sen, coupon_discount_sen, points_discount_sen, shipping_fee_sen, total_sen } = body;
  const { refunds_pending, fully_refunded, csrf_token } = body;
  const lines = readEach(body.lines, readLine);
  const events = readEach(body.events, readEvent);
  const recipient = body.recipient === null ? null : readRecipient(body.recipient);
  if (
    id !== requested ||
    typeof order_number !== "string" ||
    !isOrderStatus(status) ||
    !isTime(created_at) ||
    (paid_at !== null && !isTime(paid_at)) ||
    lines === null ||
    !isCount(subtotal_sen) ||
    !isCount(coupon_discount_sen) ||
    !isCount(points_discount_sen) ||
    !isCount(shipping_fee_sen) ||
    !isCount(total_sen) ||
    (body.recipient !== null && recipient === null) ||
    events === null ||
    !isCount(refunds_pending) ||
    typeof fully_refunded !== "boolean" ||
    typeof csrf_token !== "string" ||
    csrf_token === ""
  ) {
    return null;
  }
  return {
    order: {
      id: requested,
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
      recipient,
      events,
      refunds_pending,
      fully_refunded,
    },
    csrfToken: csrf_token,
  };
}

// GET /api/admin/orders/{内部 ID}?lang=<界面语言>：同源 cookie、不缓存。路径里的 ID 不合法时不发请求，按订单不存在报告。
// 服务端每次成功查看都写一条审计，所以调用方每次打开详情只调用一次（推进之后的重新读取除外）。
export async function readAdminOrder(rawId: string, language: Language, signal?: AbortSignal): Promise<DetailRead> {
  const id = orderIdOf(rawId);
  if (id === null) {
    return { kind: "missing" };
  }
  let response: Response;
  try {
    response = await fetch(adminOrderUrl(id, language), {
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
  const found = readDetail(body, id);
  return found === null ? { kind: "failed" } : { kind: "ok", ...found };
}

// 推进的目标状态：打包与发货（服务端只接受这两个）。
export const ADVANCE_TARGETS = ["demo_packed", "demo_shipped"] as const;
export type AdvanceTarget = (typeof ADVANCE_TARGETS)[number];

// 推进的回答：done 为 200（推进了，或已是目标状态）；conflict 为 409 order_not_advanceable 或 fulfilment_frozen；
// csrf 为 403；none 为 401；failed 为其他（含其他 409 与路径 ID 不合法而没有发请求）；network 为网络中断。
export type AdvanceReply = "done" | "conflict" | "csrf" | "none" | "failed" | "network";

const CONFLICT_DETAILS: ReadonlySet<string> = new Set(["order_not_advanceable", "fulfilment_frozen"]);

async function errorDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// POST /api/admin/orders/{内部 ID}/status：JSON 请求体只有 status，请求头带 CSRF 令牌；同源 cookie、不缓存。
// 已是目标状态时服务端照样回答 200，所以网络中断后再点即重发是安全的。
export async function advanceAdminOrder(rawId: string, status: AdvanceTarget, csrfToken: string, signal?: AbortSignal): Promise<AdvanceReply> {
  const id = orderIdOf(rawId);
  if (id === null) {
    return "failed";
  }
  let response: Response;
  try {
    response = await fetch(adminOrderStatusUrl(id), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({ status }),
      signal: signal ?? null,
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
    case 409: {
      const detail = await errorDetail(response);
      return detail !== null && CONFLICT_DETAILS.has(detail) ? "conflict" : "failed";
    }
    default:
      return "failed";
  }
}
