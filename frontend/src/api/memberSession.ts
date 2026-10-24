import { createContext, createElement, useContext, useEffect, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

import { CSRF_HEADER } from "./pay";

// 会员会话的前台接口（DESIGN 1.13「权限与资料保护」第 3、5 条，HANDOFF 0.61；UX 0.13 P13 页头「My account」入口与退出登录）：
// 读取会话 GET /api/member/session 与退出 POST /api/member/logout（app/api/member_auth.py，SHOP-TASK-076）。
// 会话 cookie 是 HttpOnly，由服务端发出与清除，页面脚本不读也不写；两个请求都不带查询参数。
// 会员写操作所需的 CSRF 令牌只能从读取会话取得；令牌与遮掩后的手机号（遮掩由服务端完成，前端取不到完整号码）
// 只在页面内存中，不进地址、日志、localStorage、sessionStorage 或 cookie。授权一律由服务端检查，页面只按状态码与错误码归类。

export const MEMBER_SESSION_URL = "/api/member/session";
export const MEMBER_LOGOUT_URL = "/api/member/logout";

// 读取会话取到的三项；expires_at 不取（会话到期由服务端判定，到期后读取会话回 401）。
export interface MemberSessionInfo {
  phoneMasked: string;
  hasPassword: boolean;
  csrfToken: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// 读取会话 200 的响应体：phone_masked 与 csrf_token 为非空字符串、has_password 为布尔时取这三项，其余（不是对象、缺字段、空串、类型不对）为 null。
export function parseMemberSession(body: unknown): MemberSessionInfo | null {
  if (!isRecord(body)) {
    return null;
  }
  const { phone_masked: phoneMasked, has_password: hasPassword, csrf_token: csrfToken } = body;
  if (typeof phoneMasked !== "string" || phoneMasked === "" || typeof hasPassword !== "boolean" || typeof csrfToken !== "string" || csrfToken === "") {
    return null;
  }
  return { phoneMasked, hasPassword, csrfToken };
}

async function responseBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

// 错误码：detail 为字符串时取它，否则（出错列表、缺少或不是 JSON）为 null。
async function errorCode(response: Response): Promise<string | null> {
  const body = await responseBody(response);
  const detail = isRecord(body) ? body.detail : undefined;
  return typeof detail === "string" ? detail : null;
}

// 读取会话的回答：ok 为 200 且响应体合格；none 为 401 member_session_required（没有会话、已撤销、已到期或会员已注销，服务端不区分）；
// failed 为其他（含 200 而响应体不合格）；network 为网络中断与中止。
export type MemberSessionReply = ({ kind: "ok" } & MemberSessionInfo) | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// GET /api/member/session：同源 cookie、不缓存，没有请求体。
export async function readMemberSession(signal?: AbortSignal): Promise<MemberSessionReply> {
  let response: Response;
  try {
    response = await fetch(MEMBER_SESSION_URL, {
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
      const info = parseMemberSession(await responseBody(response));
      return info === null ? { kind: "failed" } : { kind: "ok", ...info };
    }
    case 401:
      return (await errorCode(response)) === "member_session_required" ? { kind: "none" } : { kind: "failed" };
    default:
      return { kind: "failed" };
  }
}

// 退出的回答：ok 为 204（服务端已撤销会话并清除 cookie）；signed_out 为 401（会话已不在）；csrf 为 403 csrf_failed（服务端此时不撤销会话）；
// failed 为其他；network 为网络中断与中止。
export type MemberLogoutReply = "ok" | "signed_out" | "csrf" | "failed" | "network";

// POST /api/member/logout：请求体为空，请求头带读取会话给的 CSRF 令牌。
export async function memberLogout(csrfToken: string, signal?: AbortSignal): Promise<MemberLogoutReply> {
  let response: Response;
  try {
    response = await fetch(MEMBER_LOGOUT_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", [CSRF_HEADER]: csrfToken },
      signal: signal ?? null,
    });
  } catch {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "ok";
    case 401:
      return "signed_out";
    case 403:
      return (await errorCode(response)) === "csrf_failed" ? "csrf" : "failed";
    default:
      return "failed";
  }
}

// 全站共享的会员会话状态：unknown 为尚未读完；member 带最近一次 ok 取到的三项；guest 为 none；failed 为 failed 或 network。
export type MemberSessionState = { kind: "unknown" } | ({ kind: "member" } & MemberSessionInfo) | { kind: "guest" } | { kind: "failed" };

const UNKNOWN: MemberSessionState = { kind: "unknown" };

function stateOf(reply: MemberSessionReply): MemberSessionState {
  switch (reply.kind) {
    case "ok":
      return { kind: "member", phoneMasked: reply.phoneMasked, hasPassword: reply.hasPassword, csrfToken: reply.csrfToken };
    case "none":
      return { kind: "guest" };
    default:
      return { kind: "failed" };
  }
}

function sameState(a: MemberSessionState, b: MemberSessionState): boolean {
  if (a.kind === "member" && b.kind === "member") {
    return a.phoneMasked === b.phoneMasked && a.hasPassword === b.hasPassword && a.csrfToken === b.csrfToken;
  }
  return a.kind === b.kind;
}

// 页头与会员页面共用的读取：load 只在第一次时请求，之后返回同一结果；reload 每次都发新请求，读完之前保持原状态。
// load 与 reload 按发出先后统一排序，先发而后到的回答不覆盖后发的；两者都返回自己的回答处理之后的当前状态。
// 状态不变（种类相同，member 时三项也都相同）时 current 返回同一个对象，订阅者只在状态改变时收到通知。
export interface MemberSessionReader {
  current: () => MemberSessionState;
  load: () => Promise<MemberSessionState>;
  reload: () => Promise<MemberSessionState>;
  subscribe: (listener: () => void) => () => void;
}

export function createMemberSessionReader(): MemberSessionReader {
  const listeners = new Set<() => void>();
  let state = UNKNOWN;
  let issued = 0;
  let applied = 0;
  let first: Promise<MemberSessionState> | null = null;

  const request = async (): Promise<MemberSessionState> => {
    issued += 1;
    const order = issued;
    const next = stateOf(await readMemberSession());
    if (order > applied) {
      applied = order;
      if (!sameState(state, next)) {
        state = next;
        for (const listener of listeners) {
          listener();
        }
      }
    }
    return state;
  };

  return {
    current: () => state,
    load: () => {
      first ??= request();
      return first;
    },
    reload: request,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

// 本次页面加载唯一的读取；在页面之间切换时不再重新读取，登录与退出之后由页面调用 reload。
const pageLoadReader = createMemberSessionReader();

const MemberSessionContext = createContext<MemberSessionReader>(pageLoadReader);

interface MemberSessionProviderProps {
  children: ReactNode;
  // 测试传入替身；不传时用本次页面加载的读取。
  reader?: MemberSessionReader | undefined;
}

export function MemberSessionProvider({ children, reader }: MemberSessionProviderProps) {
  return createElement(MemberSessionContext.Provider, { value: reader ?? pageLoadReader }, children);
}

// 取得读取对象，供页面在登录、退出之后调用 reload。
export function useMemberSessionReader(): MemberSessionReader {
  return useContext(MemberSessionContext);
}

// 当前的会员会话状态；服务端渲染与首次水合时取读取对象当前的状态（新建或默认读取为 unknown），挂载后触发首次读取。
export function useMemberSession(): MemberSessionState {
  const reader = useMemberSessionReader();
  const state = useSyncExternalStore(reader.subscribe, reader.current, reader.current);

  useEffect(() => {
    void reader.load();
  }, [reader]);

  return state;
}
