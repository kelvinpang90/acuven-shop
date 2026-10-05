import type { CartLine } from "../cart";
import { quoteRequestBody } from "./checkout";
import type { QuoteLineBody } from "./checkout";
import { RECHECK_DELAY_MS, wait } from "./pay";

// 游客下单接口 POST /api/orders/guest（app/api/orders.py，SHOP-TASK-020）。
// 请求体字段以该接口的请求模型 GuestOrderIn 为准：购物车行（只有 SKU 与件数）、第 1 步手机号原文与所选地区代码、
// 收货人姓名、地址、邮编、国家代码，马来西亚另有州属代码、其他国家可有地区自由文本。不带任何价格、金额、运费、会员或短信标记。
// 幂等键只在请求头 Idempotency-Key；成功后服务端经 HttpOnly cookie 发订单访问凭据，页面脚本不读也不写。
// 电话与收货资料只在页面内存与本请求的请求体里，不进地址、localStorage、sessionStorage 或 cookie。

export const GUEST_ORDER_URL = "/api/orders/guest";
export const IDEMPOTENCY_HEADER = "Idempotency-Key";

// 第 3 步的收货资料：州属代码只有马来西亚有；地区只有其他国家有（可为空）。
export interface GuestRecipient {
  name: string;
  address: string;
  postal_code: string;
  country_code: string;
  state_code: string | null;
  region: string | null;
}

// 第 1 步的手机号：输入原文与国家码下拉所选的地区代码（以加号开头时服务端以输入为准）。
export interface GuestPhone {
  input: string;
  region: string;
}

export interface GuestOrderBody {
  lines: QuoteLineBody[];
  phone: string;
  phone_region: string;
  name: string;
  address: string;
  postal_code: string;
  country_code: string;
  state_code?: string;
  region?: string;
}

// 请求体：马来西亚带 state_code、不带 region；其他国家不带 state_code，地区去掉首尾空白后为空时不带。
export function guestOrderBody(lines: readonly CartLine[], phone: GuestPhone, recipient: GuestRecipient): GuestOrderBody {
  const body: GuestOrderBody = {
    lines: quoteRequestBody(lines).lines,
    phone: phone.input,
    phone_region: phone.region,
    name: recipient.name,
    address: recipient.address,
    postal_code: recipient.postal_code,
    country_code: recipient.country_code,
  };
  if (recipient.state_code !== null) {
    return { ...body, state_code: recipient.state_code };
  }
  if (recipient.region !== null && recipient.region.trim() !== "") {
    return { ...body, region: recipient.region };
  }
  return body;
}

// 一次下单的幂等键与它对应的请求体。请求体（表单或购物车）不变时沿用同一个键，变了就在提交时新生成。
export interface OrderAttempt {
  key: string;
  request: string;
}

export function orderAttemptFor(previous: OrderAttempt | null, body: GuestOrderBody, newKey: () => string = () => crypto.randomUUID()): OrderAttempt {
  const request = JSON.stringify(body);
  return previous !== null && previous.request === request ? previous : { key: newKey(), request };
}

// 接口的回答：placed 为 201（新订单）或 200（重放）；not_placeable 为 409 order_not_placeable；
// sms_required 为 403 sms_verification_required；phone_invalid 为 422 且出错类型含 phone_invalid；
// conflict 为 409 idempotency_conflict；unavailable 为 503；failed 为其他；network 为网络中断。
export type GuestOrderReply = "placed" | "not_placeable" | "sms_required" | "phone_invalid" | "conflict" | "unavailable" | "failed" | "network";

async function errorDetail(response: Response): Promise<unknown> {
  try {
    return ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    return undefined;
  }
}

function hasPhoneInvalid(detail: unknown): boolean {
  return Array.isArray(detail) && detail.some((item: unknown) => typeof item === "object" && item !== null && (item as { type?: unknown }).type === "phone_invalid");
}

export async function submitGuestOrder(body: GuestOrderBody, key: string, signal?: AbortSignal): Promise<GuestOrderReply> {
  let response: Response;
  try {
    response = await fetch(GUEST_ORDER_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [IDEMPOTENCY_HEADER]: key },
      body: JSON.stringify(body),
      signal: signal ?? null,
    });
  } catch {
    return "network";
  }
  if (response.status === 201 || response.status === 200) {
    return "placed";
  }
  if (response.status === 503) {
    return "unavailable";
  }
  if (response.status === 403) {
    return (await errorDetail(response)) === "sms_verification_required" ? "sms_required" : "failed";
  }
  if (response.status === 409) {
    const detail = await errorDetail(response);
    if (detail === "order_not_placeable") {
      return "not_placeable";
    }
    return detail === "idempotency_conflict" ? "conflict" : "failed";
  }
  if (response.status === 422) {
    return hasPhoneInvalid(await errorDetail(response)) ? "phone_invalid" : "failed";
  }
  return "failed";
}

export interface GuestOrderOptions {
  signal?: AbortSignal | undefined;
  delay?: (ms: number, signal?: AbortSignal) => Promise<void>;
  // 网络中断、等待以同一幂等键重试时调用（页面显示 common.network_check）。
  onChecking?: () => void;
}

// 提交游客订单，直到得到接口的回答：网络中断时隔一段时间以同一幂等键与同一请求体重试——
// 上一次若已建单，服务端按重放返回原订单（200），不会重复下单。页面已离开（中止）时返回 null。
export async function runGuestOrder(
  body: GuestOrderBody,
  key: string,
  { signal, delay = wait, onChecking }: GuestOrderOptions = {},
): Promise<Exclude<GuestOrderReply, "network"> | null> {
  for (;;) {
    const reply = await submitGuestOrder(body, key, signal);
    if (signal?.aborted) {
      return null;
    }
    if (reply !== "network") {
      return reply;
    }
    onChecking?.();
    await delay(RECHECK_DELAY_MS, signal);
    if (signal?.aborted) {
      return null;
    }
  }
}
