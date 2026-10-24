import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MEMBER_LOGOUT_URL,
  MEMBER_SESSION_URL,
  MemberSessionProvider,
  createMemberSessionReader,
  memberLogout,
  parseMemberSession,
  readMemberSession,
  useMemberSession,
  useMemberSessionReader,
} from "./memberSession";
import type { MemberLogoutReply, MemberSessionReader, MemberSessionReply, MemberSessionState } from "./memberSession";
import { CSRF_HEADER } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
});

// 测试里的号码与令牌都是虚构值。
const MASKED = "+60 12-*** 6789";
const TOKEN = "csrf-token-example";
const SESSION_BODY = { phone_masked: MASKED, has_password: true, expires_at: "2026-11-24T00:00:00Z", csrf_token: TOKEN };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function empty(status: number): Response {
  return new Response(null, { status });
}

type Reply = () => Response | "network";

// fetch 替身：每次请求调用 reply 取回答（"network" 时抛出网络错误）。
function stubFetch(reply: Reply) {
  const request = vi.fn((input: string | URL, init?: RequestInit) => {
    const next = reply();
    return next === "network" ? Promise.reject(new TypeError(`Failed to fetch: ${String(init?.method)} ${String(input)}`)) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", request);
  return request;
}

function requests(request: ReturnType<typeof stubFetch>) {
  return request.mock.calls.map(([input, init]) => ({ url: String(input), method: String(init?.method), init }));
}

// 由测试决定何时回答的 fetch 替身：每次请求排进 pending，测试按任意顺序调用 respond。
function stubPendingFetch() {
  const pending: { request: string; resolve: (response: Response) => void }[] = [];
  const request = vi.fn(
    (input: string | URL, init?: RequestInit) =>
      new Promise<Response>((resolve) => {
        pending.push({ request: `${String(init?.method)} ${String(input)}`, resolve });
      }),
  );
  vi.stubGlobal("fetch", request);
  return {
    sent: () => pending.map((item) => item.request),
    respond(index: number, response: Response) {
      const item = pending[index];
      if (item === undefined) {
        throw new Error(`no pending request ${String(index)}`);
      }
      item.resolve(response);
    },
  };
}

function member(phoneMasked: string, hasPassword: boolean, csrfToken: string): MemberSessionState {
  return { kind: "member", phoneMasked, hasPassword, csrfToken };
}

describe("member session requests", () => {
  // SHOP-TASK-076 接口记录（app/api/member_auth.py）：GET /api/member/session 没有请求体、POST /api/member/logout 不读请求体，
  // 以及本任务验收「同源 cookie，cache 为 no-store，Accept 为 application/json，不带查询参数」「退出带 X-CSRF-Token、请求体为空」：
  // 地址恰好是接口路径，请求头只有 Accept（退出另有 X-CSRF-Token），两个请求都没有请求体并接收 AbortSignal。
  const cases: [string, (signal: AbortSignal) => Promise<unknown>, string, string, Record<string, string>][] = [
    ["read session", (signal) => readMemberSession(signal), "GET", MEMBER_SESSION_URL, {}],
    ["logout", (signal) => memberLogout(TOKEN, signal), "POST", MEMBER_LOGOUT_URL, { [CSRF_HEADER]: TOKEN }],
  ];
  it.each(cases)("%s sends a fixed request without body or query", async (name, call, method, url, extraHeaders) => {
    const request = stubFetch(() => empty(500));
    const controller = new AbortController();
    await call(controller.signal);
    const calls = requests(request);
    expect(calls.map((item) => [item.method, item.url]), name).toEqual([[method, url]]);
    expect(calls[0]?.url, name).not.toContain("?");
    expect(calls[0]?.init?.credentials, name).toBe("same-origin");
    expect(calls[0]?.init?.cache, name).toBe("no-store");
    expect(calls[0]?.init?.signal, name).toBe(controller.signal);
    expect(calls[0]?.init?.headers, name).toEqual({ Accept: "application/json", ...extraHeaders });
    expect(calls[0]?.init?.body, name).toBeUndefined();
  });

  // pay.ts 的 CSRF_HEADER 与本任务验收「请求头带调用方给出的 CSRF 令牌」「CSRF 令牌不进地址」：令牌只在 X-CSRF-Token 请求头里。
  it("puts the CSRF token only in the X-CSRF-Token header", async () => {
    const request = stubFetch(() => empty(204));
    await memberLogout(TOKEN);
    const calls = requests(request);
    expect(CSRF_HEADER).toBe("X-CSRF-Token");
    expect(calls[0]?.url).not.toContain(TOKEN);
  });
});

describe("parse member session body", () => {
  // SHOP-TASK-076 接口记录的 MemberSessionOut（phone_masked、has_password、expires_at、csrf_token）与本任务验收
  // 「phone_masked 为非空字符串、has_password 为布尔、csrf_token 为非空字符串……不取 expires_at」：只取三项，其余一律不合格。
  it("takes the three fields and leaves expires_at out", () => {
    expect(parseMemberSession(SESSION_BODY)).toEqual({ phoneMasked: MASKED, hasPassword: true, csrfToken: TOKEN });
    expect(parseMemberSession({ ...SESSION_BODY, has_password: false })).toEqual({ phoneMasked: MASKED, hasPassword: false, csrfToken: TOKEN });
  });

  // 同上验收「响应体不合格（缺字段、空串、has_password 不是布尔）归 failed」。
  const invalid: [string, unknown][] = [
    ["null", null],
    ["array", [SESSION_BODY]],
    ["string", "ok"],
    ["missing phone_masked", { has_password: true, csrf_token: TOKEN }],
    ["missing has_password", { phone_masked: MASKED, csrf_token: TOKEN }],
    ["missing csrf_token", { phone_masked: MASKED, has_password: true }],
    ["empty phone_masked", { ...SESSION_BODY, phone_masked: "" }],
    ["empty csrf_token", { ...SESSION_BODY, csrf_token: "" }],
    ["has_password string", { ...SESSION_BODY, has_password: "true" }],
    ["has_password number", { ...SESSION_BODY, has_password: 1 }],
    ["phone_masked number", { ...SESSION_BODY, phone_masked: 60123 }],
    ["csrf_token null", { ...SESSION_BODY, csrf_token: null }],
  ];
  it.each(invalid)("%s is rejected", (name, body) => {
    expect(parseMemberSession(body), name).toBeNull();
  });
});

describe("read member session", () => {
  // SHOP-TASK-076 接口记录：200 MemberSessionOut；会话不通过一律 401 member_session_required（不区分不存在、撤销、到期或注销）。
  // 本任务验收：ok、none（401 member_session_required）、failed（其他，含 200 而响应体不合格）、network。
  const replies: [string, Reply, MemberSessionReply][] = [
    ["200", () => json(200, SESSION_BODY), { kind: "ok", phoneMasked: MASKED, hasPassword: true, csrfToken: TOKEN }],
    ["200 without password", () => json(200, { ...SESSION_BODY, has_password: false }), { kind: "ok", phoneMasked: MASKED, hasPassword: false, csrfToken: TOKEN }],
    ["200 missing csrf_token", () => json(200, { phone_masked: MASKED, has_password: true }), { kind: "failed" }],
    ["200 empty phone_masked", () => json(200, { ...SESSION_BODY, phone_masked: "" }), { kind: "failed" }],
    ["200 has_password not boolean", () => json(200, { ...SESSION_BODY, has_password: "yes" }), { kind: "failed" }],
    ["200 not JSON", () => new Response("<html>", { status: 200 }), { kind: "failed" }],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), { kind: "none" }],
    ["401 other code", () => json(401, { detail: "admin_session_required" }), { kind: "failed" }],
    ["401 not JSON", () => empty(401), { kind: "failed" }],
    ["204", () => empty(204), { kind: "failed" }],
    ["403", () => json(403, { detail: "csrf_failed" }), { kind: "failed" }],
    ["503", () => json(503, { detail: "service_unavailable" }), { kind: "failed" }],
    ["network", () => "network", { kind: "network" }],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(readMemberSession(), name).resolves.toEqual(expected);
  });
});

describe("member logout", () => {
  // SHOP-TASK-076 接口记录：退出成功 204 并撤销会话；会话不通过 401 member_session_required；CSRF 不通过 403 csrf_failed，此时不撤销会话。
  // 本任务验收：ok（204）、signed_out（401，会话已不在）、csrf（403 csrf_failed）、failed、network。
  const replies: [string, Reply, MemberLogoutReply][] = [
    ["204", () => empty(204), "ok"],
    ["401 member_session_required", () => json(401, { detail: "member_session_required" }), "signed_out"],
    ["401 not JSON", () => empty(401), "signed_out"],
    ["403 csrf_failed", () => json(403, { detail: "csrf_failed" }), "csrf"],
    ["403 other code", () => json(403, { detail: "forbidden" }), "failed"],
    ["403 not JSON", () => empty(403), "failed"],
    ["200", () => json(200, {}), "failed"],
    ["503", () => json(503, { detail: "service_unavailable" }), "failed"],
    ["network", () => "network", "network"],
  ];
  it.each(replies)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    await expect(memberLogout(TOKEN), name).resolves.toBe(expected);
  });
});

describe("member session reader", () => {
  // 本任务验收「状态为 unknown（尚未读完）、member、guest（none）与 failed（failed 或 network）之一」：新建的读取为 unknown，
  // 读完后按读取会话的回答归为四种之一；member 带取到的三项。
  const states: [string, Reply, MemberSessionState][] = [
    ["ok", () => json(200, SESSION_BODY), member(MASKED, true, TOKEN)],
    ["none", () => json(401, { detail: "member_session_required" }), { kind: "guest" }],
    ["failed", () => json(500, { detail: "internal" }), { kind: "failed" }],
    ["invalid body", () => json(200, { ...SESSION_BODY, csrf_token: "" }), { kind: "failed" }],
    ["network", () => "network", { kind: "failed" }],
  ];
  it.each(states)("%s", async (name, reply, expected) => {
    stubFetch(reply);
    const reader = createMemberSessionReader();
    expect(reader.current(), name).toEqual({ kind: "unknown" });
    await expect(reader.load(), name).resolves.toEqual(expected);
    expect(reader.current(), name).toEqual(expected);
  });

  // 同上验收「首次读取（只请求一次，之后返回同一结果）」：第二次 load 不再请求，返回同一个 Promise 与同一状态。
  it("requests only once for load", async () => {
    const request = stubFetch(() => json(200, SESSION_BODY));
    const reader = createMemberSessionReader();
    const first = reader.load();
    const second = reader.load();
    expect(second).toBe(first);
    const loaded = await first;
    await expect(reader.load()).resolves.toBe(loaded);
    expect(request).toHaveBeenCalledTimes(1);
    expect(requests(request).map((item) => item.url)).toEqual([MEMBER_SESSION_URL]);
  });

  // 同上验收「重读（每次都发新请求并返回新状态）」，以及 UX 0.13 P13 退出登录后页头回到游客：登录或退出之后的重读取到新状态。
  it("requests again on every reload and returns the new state", async () => {
    let reply: Reply = () => json(200, SESSION_BODY);
    const request = stubFetch(() => reply());
    const reader = createMemberSessionReader();
    await reader.load();
    reply = () => json(401, { detail: "member_session_required" });
    await expect(reader.reload()).resolves.toEqual({ kind: "guest" });
    reply = () => json(200, { ...SESSION_BODY, has_password: false, csrf_token: "csrf-token-second" });
    await expect(reader.reload()).resolves.toEqual(member(MASKED, false, "csrf-token-second"));
    expect(reader.current()).toEqual(member(MASKED, false, "csrf-token-second"));
    expect(request).toHaveBeenCalledTimes(3);
  });

  // 同上验收「读完之前保持原状态」：重读未回答时仍为原来的 member，不回到 unknown。
  it("keeps the previous state until a reload is answered", async () => {
    const fetchStub = stubPendingFetch();
    const reader = createMemberSessionReader();
    const loading = reader.load();
    fetchStub.respond(0, json(200, SESSION_BODY));
    const loaded = await loading;
    const reloading = reader.reload();
    expect(reader.current()).toBe(loaded);
    fetchStub.respond(1, json(401, { detail: "member_session_required" }));
    await expect(reloading).resolves.toEqual({ kind: "guest" });
  });

  // 同上验收「首次读取与重读按发出先后统一排序，先发而后到的旧结果不覆盖后发的结果」：
  // 首次读取（会员）晚于退出后的重读（游客）回答时，状态仍为游客；两者都返回处理之后的当前状态。
  it("does not let an older answer overwrite a newer one", async () => {
    const fetchStub = stubPendingFetch();
    const reader = createMemberSessionReader();
    const loading = reader.load();
    const reloading = reader.reload();
    expect(fetchStub.sent()).toEqual([`GET ${MEMBER_SESSION_URL}`, `GET ${MEMBER_SESSION_URL}`]);
    fetchStub.respond(1, json(401, { detail: "member_session_required" }));
    await expect(reloading).resolves.toEqual({ kind: "guest" });
    fetchStub.respond(0, json(200, SESSION_BODY));
    await expect(loading).resolves.toEqual({ kind: "guest" });
    expect(reader.current()).toEqual({ kind: "guest" });
  });

  // 同上，两次重读之间也一样：先发的重读后到时不覆盖后发重读取到的令牌（会员写操作须用最新的 CSRF 令牌，DESIGN 1.13 第 5 条）。
  it("orders reloads among themselves", async () => {
    const fetchStub = stubPendingFetch();
    const reader = createMemberSessionReader();
    const older = reader.reload();
    const newer = reader.reload();
    fetchStub.respond(1, json(200, { ...SESSION_BODY, csrf_token: "csrf-token-newer" }));
    await newer;
    fetchStub.respond(0, json(200, { ...SESSION_BODY, csrf_token: "csrf-token-older" }));
    await expect(older).resolves.toEqual(member(MASKED, true, "csrf-token-newer"));
    expect(reader.current()).toEqual(member(MASKED, true, "csrf-token-newer"));
  });

  // 同上验收「订阅者在状态改变时收到通知」与 frontend/src/cart.ts 的 listeners 写法：改变时通知，退订后不再通知。
  it("notifies subscribers when the state changes", async () => {
    let reply: Reply = () => json(200, SESSION_BODY);
    stubFetch(() => reply());
    const reader = createMemberSessionReader();
    const listener = vi.fn(() => undefined);
    const unsubscribe = reader.subscribe(listener);
    await reader.load();
    expect(listener).toHaveBeenCalledTimes(1);
    reply = () => json(401, { detail: "member_session_required" });
    await reader.reload();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    reply = () => json(200, SESSION_BODY);
    await reader.reload();
    expect(listener).toHaveBeenCalledTimes(2);
  });

  // 同上验收「状态不变（种类相同，member 时三项值也都相同）时当前状态返回同一个对象」（useSyncExternalStore 要求快照在内容不变时不变），
  // 且不通知订阅者；member 三项任一不同时为新对象。
  it("returns the same object while the state is unchanged", async () => {
    let reply: Reply = () => json(200, SESSION_BODY);
    stubFetch(() => reply());
    const reader = createMemberSessionReader();
    const listener = vi.fn(() => undefined);
    reader.subscribe(listener);
    const loaded = await reader.load();
    await expect(reader.reload()).resolves.toBe(loaded);
    expect(reader.current()).toBe(loaded);
    expect(listener).toHaveBeenCalledTimes(1);

    reply = () => json(200, { ...SESSION_BODY, has_password: false });
    const changed = await reader.reload();
    expect(changed).not.toBe(loaded);
    expect(listener).toHaveBeenCalledTimes(2);

    reply = () => json(500, { detail: "internal" });
    const failed = await reader.reload();
    reply = () => "network";
    await expect(reader.reload()).resolves.toBe(failed);
    expect(listener).toHaveBeenCalledTimes(3);
  });
});

describe("member session hooks", () => {
  function Probe() {
    const state = useMemberSession();
    return createElement("span", null, state.kind === "member" ? `member ${state.phoneMasked}` : state.kind);
  }

  // 本任务验收「服务端渲染与首次水合时取读取对象当前的状态，新建或默认读取为 unknown」：渲染时不请求（读取在挂载后由 effect 触发）。
  it("renders the reader's current state without requesting", async () => {
    const request = stubFetch(() => json(200, SESSION_BODY));
    expect(renderToStaticMarkup(createElement(Probe))).toBe("<span>unknown</span>");
    expect(renderToStaticMarkup(createElement(MemberSessionProvider, { reader: createMemberSessionReader(), children: createElement(Probe) }))).toBe("<span>unknown</span>");
    expect(request).not.toHaveBeenCalled();

    const reader = createMemberSessionReader();
    await reader.load();
    expect(renderToStaticMarkup(createElement(MemberSessionProvider, { reader, children: createElement(Probe) }))).toBe(`<span>member ${MASKED}</span>`);
    expect(request).toHaveBeenCalledTimes(1);
  });

  // 同上验收「以 Context 提供并允许测试传入替身，没有 Provider 时用默认读取」与「另导出取得读取对象的 hook」：
  // 传入的替身已读到游客，页面取到的就是它；不传或没有 Provider 时取到默认读取（尚未读取，为 unknown）。
  it("gives pages the provided reader or the page-load default", async () => {
    stubFetch(() => json(401, { detail: "member_session_required" }));
    const reader: MemberSessionReader = createMemberSessionReader();
    await reader.load();
    function ReaderKind() {
      return createElement("span", null, useMemberSessionReader().current().kind);
    }
    expect(renderToStaticMarkup(createElement(MemberSessionProvider, { reader, children: createElement(ReaderKind) }))).toBe("<span>guest</span>");
    expect(renderToStaticMarkup(createElement(MemberSessionProvider, { children: createElement(ReaderKind) }))).toBe("<span>unknown</span>");
    expect(renderToStaticMarkup(createElement(ReaderKind))).toBe("<span>unknown</span>");
  });
});
