import { CSRF_HEADER } from "./pay";

// 会员短信与登录的前台接口（DESIGN 1.12「权限与资料保护」第 1、3、5 条，HANDOFF 0.58）：
// 发送短信验证码 POST /api/sms/send（app/api/sms.py，SHOP-TASK-070）、短信登录或注册 POST /api/member/sms-login
// （app/api/member_auth.py，SHOP-TASK-076）、密码登录 POST /api/member/password-login（app/api/member_pw.py，SHOP-TASK-077）、
// 重设密码的验证与提交 POST /api/member/password-reset/verify 与 POST /api/member/password-reset（app/api/member_pw_reset.py，SHOP-TASK-079）。
// 会员会话与重设凭据由服务端经 HttpOnly cookie 发出，页面脚本不读也不写。五个请求都不带查询参数；
// 手机号原文、地区代码、验证码、密码与人机挑战令牌只在请求体里，重设提交的 CSRF 令牌只在 X-CSRF-Token 请求头里，
// 都只在页面内存中，不进地址、日志、localStorage、sessionStorage 或 cookie。号码规范化、白名单、限流与判定都在服务端，
// 页面只按状态码与错误码归类。错误体为 {"detail": ...}：错误码为字符串，请求体不合格的 422 为出错列表。

export const SMS_SEND_URL = "/api/sms/send";
export const SMS_LOGIN_URL = "/api/member/sms-login";
export const PASSWORD_LOGIN_URL = "/api/member/password-login";
export const PASSWORD_RESET_VERIFY_URL = "/api/member/password-reset/verify";
export const PASSWORD_RESET_URL = "/api/member/password-reset";

// 手机号：输入原文与国家码下拉所选的地区代码（以加号开头时服务端以输入为准），与游客下单相同。
export interface MemberPhone {
  input: string;
  region: string;
}

// 发送验证码的用途（与服务端 SmsSendIn 相同）；短信登录或注册只接受前三种。
export type SmsSendPurpose = "register" | "login" | "reset_password" | "checkout";
export type SmsLoginPurpose = "register" | "login" | "checkout";

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

// 错误码：detail 为字符串时取它，否则（出错列表、缺少或不是 JSON）为 null。
function errorCode(detail: unknown): string | null {
  return typeof detail === "string" ? detail : null;
}

// 200 的响应体：不是 JSON 或不是对象时为 null。
async function replyObject(response: Response): Promise<Record<string, unknown> | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return body !== null && typeof body === "object" && !Array.isArray(body) ? (body as Record<string, unknown>) : null;
}

// 同源 cookie、不缓存的 JSON POST，不带查询参数；网络中断与中止都返回 null。
async function postJson(url: string, headers: Record<string, string>, body: unknown, signal?: AbortSignal): Promise<Response | null> {
  try {
    return await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
      signal: signal ?? null,
    });
  } catch {
    return null;
  }
}

// 发送验证码的回答：sent 为 200；disabled 为 403 sms_disabled；captcha_failed 为 403 captcha_failed；
// not_whitelisted 为 422 not_whitelisted；undeliverable 为 422 sms_undeliverable；phone_invalid 为 422 且出错类型含 phone_invalid；
// rate_limited 为 429；suspended 为 503 sms_suspended；failed 为其他；network 为网络中断。
export type SmsSendReply =
  | "sent"
  | "disabled"
  | "captcha_failed"
  | "not_whitelisted"
  | "undeliverable"
  | "phone_invalid"
  | "rate_limited"
  | "suspended"
  | "failed"
  | "network";

const SEND_FORBIDDEN: ReadonlyMap<string, SmsSendReply> = new Map<string, SmsSendReply>([
  ["sms_disabled", "disabled"],
  ["captcha_failed", "captcha_failed"],
]);

const SEND_INVALID: ReadonlyMap<string, SmsSendReply> = new Map<string, SmsSendReply>([
  ["not_whitelisted", "not_whitelisted"],
  ["sms_undeliverable", "undeliverable"],
]);

// POST /api/sms/send：请求体只有 phone、phone_region、purpose 与 captcha_token。200 的响应体不读。
export async function sendSmsCode(phone: MemberPhone, purpose: SmsSendPurpose, captchaToken: string, signal?: AbortSignal): Promise<SmsSendReply> {
  const response = await postJson(SMS_SEND_URL, {}, { phone: phone.input, phone_region: phone.region, purpose, captcha_token: captchaToken }, signal);
  if (response === null) {
    return "network";
  }
  switch (response.status) {
    case 200:
      return "sent";
    case 429:
      return "rate_limited";
    case 403:
      return SEND_FORBIDDEN.get(errorCode(await errorDetail(response)) ?? "") ?? "failed";
    case 503:
      return errorCode(await errorDetail(response)) === "sms_suspended" ? "suspended" : "failed";
    case 422: {
      const detail = await errorDetail(response);
      if (hasPhoneInvalid(detail)) {
        return "phone_invalid";
      }
      return SEND_INVALID.get(errorCode(detail) ?? "") ?? "failed";
    }
    default:
      return "failed";
  }
}

// 验证码不对或已失效（422 code_wrong 或 code_expired，二者页面同样处理）。
const CODE_REJECTED: ReadonlySet<string> = new Set(["code_wrong", "code_expired"]);

// 短信登录或注册的回答：ok 为 200（created 为是否新建了会员；响应体不合格算 failed）；wrong 为 422 code_wrong 或 code_expired；
// disabled 为 403 sms_disabled；unavailable 为 503（不论错误码）；phone_invalid 为 422 且出错类型含 phone_invalid；
// failed 为其他；network 为网络中断。
export type SmsLoginReply =
  | { kind: "ok"; created: boolean }
  | { kind: "wrong" }
  | { kind: "disabled" }
  | { kind: "unavailable" }
  | { kind: "phone_invalid" }
  | { kind: "failed" }
  | { kind: "network" };

// 核验验证码的两个接口（短信登录与重设验证）共有的错误归类；200 由各自处理。
type CodeRejection = { kind: "wrong" } | { kind: "disabled" } | { kind: "unavailable" } | { kind: "phone_invalid" } | { kind: "failed" };

async function codeRejection(response: Response): Promise<CodeRejection> {
  switch (response.status) {
    case 503:
      return { kind: "unavailable" };
    case 403:
      return errorCode(await errorDetail(response)) === "sms_disabled" ? { kind: "disabled" } : { kind: "failed" };
    case 422: {
      const detail = await errorDetail(response);
      if (hasPhoneInvalid(detail)) {
        return { kind: "phone_invalid" };
      }
      const code = errorCode(detail);
      return code !== null && CODE_REJECTED.has(code) ? { kind: "wrong" } : { kind: "failed" };
    }
    default:
      return { kind: "failed" };
  }
}

// POST /api/member/sms-login：请求体只有 phone、phone_region、purpose 与 code；成功时服务端设置会员会话 cookie。
export async function smsLogin(phone: MemberPhone, purpose: SmsLoginPurpose, code: string, signal?: AbortSignal): Promise<SmsLoginReply> {
  const response = await postJson(SMS_LOGIN_URL, {}, { phone: phone.input, phone_region: phone.region, purpose, code }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status !== 200) {
    return codeRejection(response);
  }
  const created = (await replyObject(response))?.created;
  return typeof created === "boolean" ? { kind: "ok", created } : { kind: "failed" };
}

// 密码登录的回答：ok 为 204；login_failed 为 401 login_failed（锁定、未注册、未设密码与密码错误不区分）；unavailable 为 503；
// phone_invalid 为 422 且出错类型含 phone_invalid；failed 为其他；network 为网络中断。
export type PasswordLoginReply = "ok" | "login_failed" | "unavailable" | "phone_invalid" | "failed" | "network";

// POST /api/member/password-login：请求体只有 phone、phone_region 与 password；成功时服务端设置会员会话 cookie。
export async function passwordLogin(phone: MemberPhone, password: string, signal?: AbortSignal): Promise<PasswordLoginReply> {
  const response = await postJson(PASSWORD_LOGIN_URL, {}, { phone: phone.input, phone_region: phone.region, password }, signal);
  if (response === null) {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "ok";
    case 503:
      return "unavailable";
    case 401:
      return errorCode(await errorDetail(response)) === "login_failed" ? "login_failed" : "failed";
    case 422:
      return hasPhoneInvalid(await errorDetail(response)) ? "phone_invalid" : "failed";
    default:
      return "failed";
  }
}

// 重设验证的回答：registered 为 200 且号码已注册（csrfToken 只用于随后提交的请求头）；not_registered 为 200 且未注册（不创建账号）；
// wrong 为 422 code_wrong 或 code_expired；disabled 为 403 sms_disabled；unavailable 为 503（不论错误码）；
// phone_invalid 为 422 且出错类型含 phone_invalid；failed 为其他（含 200 而响应体不合格）；network 为网络中断。
export type ResetVerifyReply = { kind: "registered"; csrfToken: string } | { kind: "not_registered" } | CodeRejection | { kind: "network" };

// POST /api/member/password-reset/verify：请求体只有 phone、phone_region 与 code（用途由服务端固定）；已注册时服务端设置重设 cookie。
export async function verifyPasswordReset(phone: MemberPhone, code: string, signal?: AbortSignal): Promise<ResetVerifyReply> {
  const response = await postJson(PASSWORD_RESET_VERIFY_URL, {}, { phone: phone.input, phone_region: phone.region, code }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status !== 200) {
    return codeRejection(response);
  }
  const body = await replyObject(response);
  const registered = body?.registered;
  const token = body?.csrf_token;
  if (registered === true && typeof token === "string" && token !== "") {
    return { kind: "registered", csrfToken: token };
  }
  return registered === false ? { kind: "not_registered" } : { kind: "failed" };
}

// 重设提交的回答：ok 为 204；expired 为 401 reset_expired；disabled 为 403 sms_disabled；csrf 为 403 csrf_failed；
// too_short 为 422 password_too_short；unavailable 为 503；failed 为其他；network 为网络中断。
export type ResetSubmitReply = "ok" | "expired" | "disabled" | "csrf" | "too_short" | "unavailable" | "failed" | "network";

const RESET_FORBIDDEN: ReadonlyMap<string, ResetSubmitReply> = new Map<string, ResetSubmitReply>([
  ["sms_disabled", "disabled"],
  ["csrf_failed", "csrf"],
]);

// POST /api/member/password-reset：请求体只有 password，请求头带重设验证给的 CSRF 令牌；凭据在服务端发的重设 cookie 里。
export async function submitPasswordReset(password: string, csrfToken: string, signal?: AbortSignal): Promise<ResetSubmitReply> {
  const response = await postJson(PASSWORD_RESET_URL, { [CSRF_HEADER]: csrfToken }, { password }, signal);
  if (response === null) {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "ok";
    case 503:
      return "unavailable";
    case 401:
      return errorCode(await errorDetail(response)) === "reset_expired" ? "expired" : "failed";
    case 403:
      return RESET_FORBIDDEN.get(errorCode(await errorDetail(response)) ?? "") ?? "failed";
    case 422:
      return errorCode(await errorDetail(response)) === "password_too_short" ? "too_short" : "failed";
    default:
      return "failed";
  }
}
