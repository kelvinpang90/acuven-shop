import { CSRF_HEADER } from "./pay";

// 会员中心 P13 的我的积分、我的优惠券与设置段（docs/UX.md 0.13；DESIGN 1.14「权限与资料保护」第 3、5 条与「边界与原则」第 4 条，HANDOFF 0.63）：
// 积分 GET /api/member/points 与优惠券 GET /api/member/coupons（app/api/member_account.py）、首次设置密码 POST /api/member/password
// （app/api/member_pw.py，SHOP-TASK-077），以及注销的发送验证码 POST /api/member/delete/send-code、核验 POST /api/member/delete/verify
// 与确认注销 POST /api/member/delete（app/api/member_deletion.py，SHOP-TASK-081、082）。更改已有密码走 P12 忘记密码（memberAuth.ts），不在这里。
// 授权凭服务端发的会员会话 HttpOnly cookie（页面脚本不读也不写），号码一律由服务端取本账号的；六个请求都不带查询参数。
// 密码、验证码与人机挑战令牌只在请求体里，CSRF 令牌只在 X-CSRF-Token 请求头里，都只在页面内存中，不进地址、日志、localStorage、
// sessionStorage 或 cookie。注销按当时读取的短信验证开关选择确认方式，判定都在服务端，页面只按状态码与错误码归类。

export const MEMBER_POINTS_URL = "/api/member/points";
export const MEMBER_COUPONS_URL = "/api/member/coupons";
export const MEMBER_PASSWORD_URL = "/api/member/password";
export const MEMBER_DELETE_SEND_URL = "/api/member/delete/send-code";
export const MEMBER_DELETE_VERIFY_URL = "/api/member/delete/verify";
export const MEMBER_DELETE_URL = "/api/member/delete";

// 积分流水的类型：获得、抵扣、过期、退款退回与退款扣回。
export type PointsActivityType = "earned" | "redeemed" | "expired" | "returned" | "reversed";

// 积分流水的一条：points 带正负号，created_at 为带时区的 ISO 字符串。
export interface PointsActivity {
  type: PointsActivityType;
  points: number;
  order_number: string;
  created_at: string;
}

// 最近一批将过期的积分：date 为马来西亚日期（YYYY-MM-DD）。
export interface PointsExpiry {
  points: number;
  date: string;
}

// 我的积分：可用余额、待抵扣的欠额、最近过期（没有为 null）与流水（服务端给出，最多 100 条）。
export interface MemberPoints {
  balance: number;
  pending: number;
  next_expiry: PointsExpiry | null;
  history: PointsActivity[];
}

// 可用的优惠券：fixed 时 amount_sen 为减免的整数仙，percent 时 percent 为折扣百分比，另一项为 null；
// valid_until 为带时区的 ISO 字符串。
export interface AvailableCoupon {
  code: string;
  type: "fixed" | "percent";
  amount_sen: number | null;
  percent: number | null;
  min_spend_sen: number;
  valid_until: string;
}

// 用过的优惠券：用在哪张订单与当时减免的整数仙。
export interface UsedCoupon {
  code: string;
  order_number: string;
  discount_sen: number;
}

export interface MemberCoupons {
  available: AvailableCoupon[];
  used: UsedCoupon[];
}

// 错误体为 {"detail": ...}：detail 为字符串时取它，否则（出错列表、缺少或不是 JSON）为 null。
async function errorCode(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// 错误码对应的状态码与回答；状态码不符的同一错误码不算。
type Codes<T> = ReadonlyMap<string, readonly [number, T]>;

// 六个接口共有：401 member_session_required 为 signed_out；四个写接口另有 403 csrf_failed 为 csrf。
const SIGNED_OUT = ["member_session_required", [401, "signed_out"]] as const;
const CSRF_FAILED = ["csrf_failed", [403, "csrf"]] as const;

// 按错误码与状态码归类，对不上的为 failed。
async function classify<T>(response: Response, codes: Codes<T>): Promise<T | "failed"> {
  const code = await errorCode(response);
  const known = code === null ? undefined : codes.get(code);
  return known !== undefined && known[0] === response.status ? known[1] : "failed";
}

// 同源 cookie、不缓存的 JSON 请求，不带查询参数；网络中断与中止都返回 null。
async function send(url: string, method: "GET" | "POST", headers: Record<string, string>, body: unknown, signal?: AbortSignal): Promise<Response | null> {
  try {
    return await fetch(url, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: method === "GET" ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json", ...headers },
      ...(method === "GET" ? {} : { body: JSON.stringify(body) }),
      signal: signal ?? null,
    });
  } catch {
    return null;
  }
}

const READ_CODES: Codes<"signed_out"> = new Map<string, readonly [number, "signed_out"]>([SIGNED_OUT]);

// 我的积分的结果：ok 为 200；signed_out 为 401；failed 为其他（含 200 以外的 2xx 与不是 JSON）；network 为网络中断。
export type MemberPointsRead = ({ kind: "ok" } & MemberPoints) | { kind: "signed_out" } | { kind: "failed" } | { kind: "network" };

// GET /api/member/points：不带任何查询参数。
export async function readMemberPoints(signal?: AbortSignal): Promise<MemberPointsRead> {
  const response = await send(MEMBER_POINTS_URL, "GET", {}, null, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status !== 200) {
    return { kind: await classify(response, READ_CODES) };
  }
  try {
    const body = (await response.json()) as MemberPoints;
    return { kind: "ok", balance: body.balance, pending: body.pending, next_expiry: body.next_expiry, history: body.history };
  } catch {
    return { kind: "failed" };
  }
}

// 我的优惠券的结果：ok 为 200；signed_out 为 401；failed 为其他（含 200 以外的 2xx 与不是 JSON）；network 为网络中断。
export type MemberCouponsRead = ({ kind: "ok" } & MemberCoupons) | { kind: "signed_out" } | { kind: "failed" } | { kind: "network" };

// GET /api/member/coupons：不带任何查询参数。
export async function readMemberCoupons(signal?: AbortSignal): Promise<MemberCouponsRead> {
  const response = await send(MEMBER_COUPONS_URL, "GET", {}, null, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status !== 200) {
    return { kind: await classify(response, READ_CODES) };
  }
  try {
    const body = (await response.json()) as MemberCoupons;
    return { kind: "ok", available: body.available, used: body.used };
  } catch {
    return { kind: "failed" };
  }
}

// 首次设置密码的回答：done 为 204；too_short 为 422 password_too_short；already_set 为 409 password_already_set；
// signed_out 为 401；csrf 为 403；unavailable 为 503；failed 为其他（含结构不合格的 422）；network 为网络中断。
export type SetPasswordReply = "done" | "too_short" | "already_set" | "signed_out" | "csrf" | "unavailable" | "failed" | "network";

const PASSWORD_CODES: Codes<SetPasswordReply> = new Map<string, readonly [number, SetPasswordReply]>([
  SIGNED_OUT,
  CSRF_FAILED,
  ["password_too_short", [422, "too_short"]],
  ["password_already_set", [409, "already_set"]],
]);

// POST /api/member/password：请求体只有 password，请求头带会员会话给的 CSRF 令牌；成功时不撤销会话。
export async function setMemberPassword(password: string, csrfToken: string, signal?: AbortSignal): Promise<SetPasswordReply> {
  const response = await send(MEMBER_PASSWORD_URL, "POST", { [CSRF_HEADER]: csrfToken }, { password }, signal);
  if (response === null) {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "done";
    case 503:
      return "unavailable";
    default:
      return classify(response, PASSWORD_CODES);
  }
}

// 发送注销验证码的回答，照 memberAuth.ts 的 sendSmsCode（号码由服务端取，没有 phone_invalid）：sent 为 200；
// disabled 为 403 sms_disabled；captcha_failed 为 403 captcha_failed；not_whitelisted 为 422 not_whitelisted；
// undeliverable 为 422 sms_undeliverable；rate_limited 为 429；suspended 为 503 sms_suspended；signed_out 为 401；csrf 为 403 csrf_failed；
// failed 为其他；network 为网络中断。
export type DeleteSendReply =
  | "sent"
  | "disabled"
  | "captcha_failed"
  | "not_whitelisted"
  | "undeliverable"
  | "rate_limited"
  | "suspended"
  | "signed_out"
  | "csrf"
  | "failed"
  | "network";

const SEND_CODES: Codes<DeleteSendReply> = new Map<string, readonly [number, DeleteSendReply]>([
  SIGNED_OUT,
  CSRF_FAILED,
  ["sms_disabled", [403, "disabled"]],
  ["captcha_failed", [403, "captcha_failed"]],
  ["not_whitelisted", [422, "not_whitelisted"]],
  ["sms_undeliverable", [422, "undeliverable"]],
  ["sms_suspended", [503, "suspended"]],
]);

// POST /api/member/delete/send-code：请求体只有 captcha_token（不带号码），请求头带 CSRF 令牌。200 的响应体不读。
export async function sendDeleteCode(captchaToken: string, csrfToken: string, signal?: AbortSignal): Promise<DeleteSendReply> {
  const response = await send(MEMBER_DELETE_SEND_URL, "POST", { [CSRF_HEADER]: csrfToken }, { captcha_token: captchaToken }, signal);
  if (response === null) {
    return "network";
  }
  switch (response.status) {
    case 200:
      return "sent";
    case 429:
      return "rate_limited";
    default:
      return classify(response, SEND_CODES);
  }
}

// 核验注销验证码的回答：verified 为 204（服务端为当前会话签发 10 分钟有效的一次性批准）；wrong 为 422 code_wrong 或 code_expired
// （二者同样处理）；disabled 为 403 sms_disabled（开关已关闭，页面改用开关关闭时的确认方式）；unavailable 为 503（不论错误码）；
// signed_out 为 401；csrf 为 403 csrf_failed；failed 为其他；network 为网络中断。
export type DeleteVerifyReply = "verified" | "wrong" | "disabled" | "unavailable" | "signed_out" | "csrf" | "failed" | "network";

const VERIFY_CODES: Codes<DeleteVerifyReply> = new Map<string, readonly [number, DeleteVerifyReply]>([
  SIGNED_OUT,
  CSRF_FAILED,
  ["code_wrong", [422, "wrong"]],
  ["code_expired", [422, "wrong"]],
  ["sms_disabled", [403, "disabled"]],
]);

// POST /api/member/delete/verify：请求体只有 code，请求头带 CSRF 令牌；批准凭会话，不另发 cookie。
export async function verifyDeleteCode(code: string, csrfToken: string, signal?: AbortSignal): Promise<DeleteVerifyReply> {
  const response = await send(MEMBER_DELETE_VERIFY_URL, "POST", { [CSRF_HEADER]: csrfToken }, { code }, signal);
  if (response === null) {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "verified";
    case 503:
      return "unavailable";
    default:
      return classify(response, VERIFY_CODES);
  }
}

// 确认注销的回答：deleted 为 204（服务端清除会话 cookie）；not_verified 为 403 delete_not_verified（开关开启而没有批准）；
// password_wrong 为 403 password_wrong；password_required 为 422 password_required（开关关闭且已设密码而没给密码）；
// rate_limited 为 429；unavailable 为 503；signed_out 为 401；csrf 为 403 csrf_failed；failed 为其他；network 为网络中断。
export type DeleteConfirmReply =
  | "deleted"
  | "not_verified"
  | "password_wrong"
  | "password_required"
  | "rate_limited"
  | "unavailable"
  | "signed_out"
  | "csrf"
  | "failed"
  | "network";

const CONFIRM_CODES: Codes<DeleteConfirmReply> = new Map<string, readonly [number, DeleteConfirmReply]>([
  SIGNED_OUT,
  CSRF_FAILED,
  ["delete_not_verified", [403, "not_verified"]],
  ["password_wrong", [403, "password_wrong"]],
  ["password_required", [422, "password_required"]],
]);

// POST /api/member/delete：调用方给出当前密码时请求体只有 password，否则为空对象；请求头带 CSRF 令牌。
// 确认方式由服务端按当时读取的开关判定（开启凭批准，关闭且已设密码凭密码，关闭且未设密码凭会话与 CSRF）。
export async function confirmDeletion(password: string | null, csrfToken: string, signal?: AbortSignal): Promise<DeleteConfirmReply> {
  const response = await send(MEMBER_DELETE_URL, "POST", { [CSRF_HEADER]: csrfToken }, password === null ? {} : { password }, signal);
  if (response === null) {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "deleted";
    case 429:
      return "rate_limited";
    case 503:
      return "unavailable";
    default:
      return classify(response, CONFIRM_CODES);
  }
}
