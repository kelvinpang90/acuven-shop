import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MEMBER_COUPONS_URL, MEMBER_POINTS_URL } from "../api/memberAccount";
import type { MemberCoupons, MemberPoints } from "../api/memberAccount";
import { clearMemberOrder, handOffMemberOrder, MEMBER_ORDERS_URL, memberOrderInView } from "../api/memberOrders";
import type { MemberOrderSummary, MemberOrdersRead } from "../api/memberOrders";
import { clearMemberPayOrder, MEMBER_PAY_ORDER_KEY, stashedMemberPayOrder, stashMemberPayOrder } from "../api/memberPay";
import { createMemberSessionReader, MEMBER_LOGOUT_URL, MEMBER_SESSION_URL } from "../api/memberSession";
import type { MemberLogoutReply, MemberSessionReader, MemberSessionState } from "../api/memberSession";
import { CSRF_HEADER } from "../api/pay";
import App from "../App";
import { formatSen } from "../format";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, isSourcePath, Link, nextSource, RouterProvider, verifiedPath } from "../router";
import type { RoutePath } from "../router";
import {
  ACCOUNT_SECTIONS,
  AccountView,
  canLogout,
  loadOrders,
  LOADING,
  logOut,
  logoutStep,
  openAccount,
  orderPath,
  ordersStep,
  sessionStep,
  startLogout,
  statusTag,
  viewOrder,
} from "./AccountPage";
import type { AccountMoves, AccountScreen, AccountViewProps, OrdersState } from "./AccountPage";
import { formatDate } from "./TrackOrderPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearMemberOrder();
  // SHOP-TASK-163 的退出测试会写入 P06 的当前订单暂存（模块变量），每条之后复原。
  clearMemberPayOrder();
});

// 测试里的手机号、令牌与订单号都是虚构值。
const MASKED = "+60 12•••• 678";
const TOKEN = "csrf-token-1";
const NEW_TOKEN = "csrf-token-2";

// 三张订单故意不按下单时间排列（第二张最新），用来确认页面按服务端给的顺序显示、不自行排序。
const ORDERS: MemberOrderSummary[] = [
  { order_number: "K7Q29MXAB6TN2RJD", created_at: "2026-09-30T04:00:00Z", status: "demo_shipped", total_sen: 10300 },
  { order_number: "H3PZ4W8CD8K4M0QX", created_at: "2026-10-12T04:00:00Z", status: "demo_completed", total_sen: 4200 },
  { order_number: "M5RT8Q2NZ1Y7V3PL", created_at: "2026-09-12T04:00:00Z", status: "awaiting_demo_payment", total_sen: 12345 },
];

const STATUSES = ["awaiting_demo_payment", "demo_paid", "demo_packed", "demo_shipped", "demo_completed", "demo_cancelled"] as const;

const STATUS_KEY: Readonly<Record<(typeof STATUSES)[number], CopyKey>> = {
  awaiting_demo_payment: "order.status_awaiting",
  demo_paid: "order.status_paid",
  demo_packed: "order.status_packed",
  demo_shipped: "order.status_shipped",
  demo_completed: "order.status_completed",
  demo_cancelled: "order.status_cancelled",
};

// 视觉稿 P13 的标签样式：已完成为 success，待支付与已取消为描边，其余为主色。
const STATUS_TAG: Readonly<Record<(typeof STATUSES)[number], string>> = {
  awaiting_demo_payment: "acs-tag acs-tag--outline",
  demo_paid: "acs-tag acs-tag--accent",
  demo_packed: "acs-tag acs-tag--accent",
  demo_shipped: "acs-tag acs-tag--accent",
  demo_completed: "acs-tag acs-tag--success",
  demo_cancelled: "acs-tag acs-tag--outline",
};

// 订单详情会员模式的路径（SHOP-TASK-154 才加进路由表）：测试只把它当作 View 的去向，不断言它在不在路由表里。
const ORDER_TO_PATH: string = "/account/order";
const ORDER_TO = ORDER_TO_PATH as RoutePath;

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

// SHOP-TASK-165 加入积分与优惠券两段后，已登录的状态多了这两段。这里默认为已取到的空积分与空优惠券（页面上只有字典文字），
// 我的订单、标题行与退出的断言照旧；两段的内容由 AccountPointsPanel.test.tsx 与 AccountCouponsPanel.test.tsx 守住。
const EMPTY_POINTS: MemberPoints = { balance: 0, pending: 0, next_expiry: null, history: [] };
const EMPTY_COUPONS: MemberCoupons = { available: [], used: [] };

function memberScreen(overrides: Partial<Extract<AccountScreen, { status: "member" }>> = {}): AccountScreen {
  return {
    status: "member",
    phoneMasked: MASKED,
    csrfToken: TOKEN,
    orders: { status: "ready", orders: ORDERS },
    points: { status: "ready", points: EMPTY_POINTS },
    coupons: { status: "ready", coupons: EMPTY_COUPONS },
    loggingOut: false,
    logoutError: null,
    ...overrides,
  };
}

function withOrders(orders: OrdersState): AccountScreen {
  return memberScreen({ orders });
}

const noop = () => undefined;

function props(overrides: Partial<AccountViewProps> = {}): AccountViewProps {
  return { screen: memberScreen(), section: "orders", orderTo: ORDER_TO, onSection: noop, onView: noop, onLogout: noop, ...overrides };
}

function wrap(children: ReactNode, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/account">{children}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<AccountViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<AccountView {...props(overrides)} />, language));
}

type AnyElement = ReactElement<Record<string, unknown>>;

// 元素树：展开 View 里的子组件（直接调用，仍在语言与路由的上下文里），在 Link 处停下，用来取链接与按钮的点击处理。
function expand(node: ReactNode): AnyElement[] {
  if (Array.isArray(node)) {
    return node.flatMap((child: ReactNode) => expand(child));
  }
  if (!isValidElement(node)) {
    return [];
  }
  const element = node as AnyElement;
  if (typeof element.type === "function" && element.type !== Link) {
    const component = element.type as (props: Record<string, unknown>) => ReactNode;
    return [element, ...expand(component(element.props))];
  }
  return [element, ...expand(element.props.children as ReactNode)];
}

function tree(overrides: Partial<AccountViewProps> = {}): AnyElement[] {
  let found: AnyElement[] = [];
  const probe = () => {
    found = expand(AccountView(props(overrides)));
    return null;
  };
  renderToStaticMarkup(wrap(createElement(probe), "en"));
  return found;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...attributes].filter((value) => value !== "");
}

// 文本节点（不含属性），依出现顺序。
function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

function copyPattern(template: string): RegExp {
  const parts = template.split(/\{\w+\}/).map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^${parts.join(".+")}$`);
}

function copy(key: CopyKey, language: Language = "en"): string {
  return escapeHtml(COPY[key][language]);
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const desktopList = (html: string) => part(html, /<ul class="site-desktop-only site-account__list">[\s\S]*?<\/ul>/);
const phoneList = (html: string) => part(html, /<ul class="site-phone-only site-account__list">[\s\S]*?<\/ul>/);
const desktopRows = (html: string) => [...desktopList(html).matchAll(/<li class="site-account__row">([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");
const phoneRows = (html: string) => [...phoneList(html).matchAll(/<li>([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");

function price(sen: number): string {
  return formatCopy(COPY["common.price_myr"].en, { amount: formatSen(sen) });
}

type Reply = Response | "network";

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：按地址各取一串回答，依次用掉；没有准备的请求一律失败。
function stubFetch(routes: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const next = routes[url]?.shift();
      if (next === undefined) {
        return Promise.reject(new Error(`unexpected request ${url}`));
      }
      return next === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
    }),
  );
  return calls;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const memberReply = (token = TOKEN) => json(200, { phone_masked: MASKED, has_password: false, expires_at: "2026-11-24T00:00:00Z", csrf_token: token });
const guestReply = () => json(401, { detail: "member_session_required" });
const ordersReply = (orders: MemberOrderSummary[] = ORDERS) => json(200, { total: orders.length, orders });
const pointsReply = () => json(200, EMPTY_POINTS);
const couponsReply = () => json(200, EMPTY_COUPONS);
const signedOutReply = () => json(401, { detail: "member_session_required" });

// 页面的处理替身：按顺序记下 show、update 与 replace，并按它们推演页面状态。
function fakeMoves(initial: AccountScreen = LOADING) {
  const events: string[] = [];
  let screen = initial;
  const moves: AccountMoves = {
    show: (next) => {
      screen = next;
      events.push(`show ${next.status}`);
    },
    update: (change) => {
      screen = change(screen);
      events.push("update");
    },
    replace: (path) => {
      events.push(`replace ${path}`);
    },
  };
  return { moves, events, current: () => screen };
}

function headerOf(init: RequestInit, name: string): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.[name];
}

describe("opening the page", () => {
  // 验收第 2 条「打开时经 SHOP-TASK-151 的读取对象重读一次会话（不直接用之前读到的状态：登录后来到本页时共享状态可能还是旧的）」：
  // 共享状态先读成未登录（登录之前的那次），打开页面时仍重读会话，读到已登录后显示标题行，再读我的订单。
  // SHOP-TASK-165：已登录后与我的订单一起读取积分与优惠券，只为这两个读取补了替身、请求清单与两次 update，会话与我的订单的期望不变。
  it("rereads the session instead of using the earlier shared state", async () => {
    const calls = stubFetch({
      [MEMBER_SESSION_URL]: [guestReply(), memberReply()],
      [MEMBER_ORDERS_URL]: [ordersReply()],
      [MEMBER_POINTS_URL]: [pointsReply()],
      [MEMBER_COUPONS_URL]: [couponsReply()],
    });
    const reader = createMemberSessionReader();
    expect((await reader.load()).kind).toBe("guest");
    const { moves, events, current } = fakeMoves();
    await openAccount(reader, new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL, MEMBER_SESSION_URL, MEMBER_ORDERS_URL, MEMBER_POINTS_URL, MEMBER_COUPONS_URL]);
    expect(events).toEqual(["show member", "update", "update", "update"]);
    expect(current()).toEqual(memberScreen());
    expect(reader.current().kind).toBe("member");
  });

  // 同一条「guest 时用路由的 replace 进入 /login（离开 /account 时路由记下的来源为 /account，登录成功后回到这里）」：
  // 不读我的订单、不显示任何状态，只 replace 到 /login；路由从 /account 换到 /login 时记下 /account，登录成功的去向即 /account。
  it("replaces the page with /login for a guest and comes back after logging in", async () => {
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [guestReply()] });
    const { moves, events } = fakeMoves();
    await openAccount(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["replace /login"]);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_SESSION_URL]);
    expect(sessionStep({ kind: "guest" })).toBe("login");
    const source = nextSource(null, "/account", "/login");
    expect(source).toBe("/account");
    expect(isSourcePath("/account")).toBe(true);
    expect(verifiedPath(source)).toBe("/account");
  });

  // 同一条「failed 时只显示 common.error_retry」：读取失败与网络中断都按失败，页面只有这一句提示；不读我的订单、不去 /login。
  it.each<[string, Reply]>([
    ["failed", new Response(null, { status: 500 })],
    ["network", "network"],
  ])("shows only common.error_retry when reading the session %s", async (_name, reply) => {
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [reply] });
    const { moves, events, current } = fakeMoves();
    await openAccount(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual(["show failed"]);
    expect(calls).toHaveLength(1);
    expect(visibleTexts(render({ screen: current() }))).toEqual([COPY["common.error_retry"].en]);
    expect(sessionStep({ kind: "unknown" })).toEqual({ status: "failed" });
  });

  // 同一条「读完之前只显示标 aria-busy 的空主体」与 App.test.tsx 按路由表检查初始渲染：/account 套前台框架，初始只有空主体。
  it("renders an empty busy body before the session is read", () => {
    expect(render({ screen: LOADING })).toBe(`<main class="site-account" aria-busy="true"></main>`);
    const html = renderToStaticMarkup(<App initialPath="/account" storage={languageStorage("en")} />);
    expect(html).toContain(`<main class="site-account" aria-busy="true"></main>`);
    expect(html).toContain(COPY["common.demo_banner"].en);
    expect(html.indexOf("</header>")).toBeLessThan(html.indexOf(`<main class="site-account"`));
  });

  // 同一条「这一判断只在打开页面时的那次读取后做，之后共享会话状态的变化不触发去 /login」：打开完成后共享读取再读到未登录，页面不跳转、不更新。
  // SHOP-TASK-165：只为积分与优惠券两个读取补了替身与两次 update。
  it("ignores later changes of the shared session", async () => {
    stubFetch({
      [MEMBER_SESSION_URL]: [memberReply(), guestReply()],
      [MEMBER_ORDERS_URL]: [ordersReply()],
      [MEMBER_POINTS_URL]: [pointsReply()],
      [MEMBER_COUPONS_URL]: [couponsReply()],
    });
    const reader = createMemberSessionReader();
    const { moves, events } = fakeMoves();
    await openAccount(reader, new AbortController().signal, moves);
    expect((await reader.reload()).kind).toBe("guest");
    expect(events).toEqual(["show member", "update", "update", "update"]);
  });

  // 验收第 6 条「离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：
  // 读取会话不带页面的 signal（共享状态照常更新为已登录），重读期间已离开时不显示、不读我的订单；我的订单的请求带页面的 signal。
  // SHOP-TASK-165：只为积分与优惠券两个读取补了替身，并期望这两个请求同样带页面的 signal。
  it("stops after leaving the page while the shared reread still settles", async () => {
    const calls = stubFetch({
      [MEMBER_SESSION_URL]: [memberReply(), memberReply()],
      [MEMBER_ORDERS_URL]: [ordersReply()],
      [MEMBER_POINTS_URL]: [pointsReply()],
      [MEMBER_COUPONS_URL]: [couponsReply()],
    });
    const reader = createMemberSessionReader();
    const gone = new AbortController();
    gone.abort();
    const left = fakeMoves();
    await openAccount(reader, gone.signal, left.moves);
    expect(left.events).toEqual([]);
    expect(reader.current().kind).toBe("member");
    expect(calls[0]?.init.signal ?? null).toBeNull();

    const page = new AbortController();
    const staying = fakeMoves();
    await openAccount(reader, page.signal, staying.moves);
    expect(calls[2]?.url).toBe(MEMBER_ORDERS_URL);
    expect(calls[2]?.init.signal).toBe(page.signal);
    expect(calls.slice(3).map((call) => call.url)).toEqual([MEMBER_POINTS_URL, MEMBER_COUPONS_URL]);
    expect(calls[3]?.init.signal).toBe(page.signal);
    expect(calls[4]?.init.signal).toBe(page.signal);
  });
});

describe("points and coupons sections (SHOP-TASK-165)", () => {
  // 验收第 2 条「会话为 member 后与我的订单一起经 SHOP-TASK-164 读取两段」：sessionStep 让三段都从读取中开始，打开后三个请求都发出，
  // 结果分别换上各段（积分与优惠券的取值原样来自接口）。
  it("reads both sections together with my orders", async () => {
    const points: MemberPoints = { balance: 845, pending: 0, next_expiry: { points: 120, date: "2026-11-15" }, history: [] };
    const coupons: MemberCoupons = { available: [], used: [{ code: "DEMO10", order_number: "K7Q29MXAB6TN2RJD", discount_sen: 1000 }] };
    stubFetch({
      [MEMBER_SESSION_URL]: [memberReply()],
      [MEMBER_ORDERS_URL]: [ordersReply()],
      [MEMBER_POINTS_URL]: [json(200, points)],
      [MEMBER_COUPONS_URL]: [json(200, coupons)],
    });
    const { moves, current } = fakeMoves();
    await openAccount(createMemberSessionReader(), new AbortController().signal, moves);
    expect(current()).toEqual(memberScreen({ points: { status: "ready", points }, coupons: { status: "ready", coupons } }));
  });

  // 同一条「signed_out 时照我的订单重读会话并用 replace 进入 /login」与 HANDOFF 0.61 第（5）条：积分或优惠券回 401 时先重读会话、再 replace；
  // 我的订单照常更新。三段同时读取，几段都回 401 时只重读一次会话、只跳转一次。
  it.each<[string, Record<string, Reply[]>, number]>([
    ["points", { [MEMBER_POINTS_URL]: [signedOutReply()], [MEMBER_COUPONS_URL]: [couponsReply()] }, 2],
    ["coupons", { [MEMBER_POINTS_URL]: [pointsReply()], [MEMBER_COUPONS_URL]: [signedOutReply()] }, 2],
    ["both", { [MEMBER_POINTS_URL]: [signedOutReply()], [MEMBER_COUPONS_URL]: [signedOutReply()] }, 1],
  ])("rereads the session once and goes to /login when %s says signed out", async (_name, routes, updates) => {
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [memberReply(), guestReply()], [MEMBER_ORDERS_URL]: [ordersReply()], ...routes });
    const reader = createMemberSessionReader();
    const { moves, events } = fakeMoves();
    await openAccount(reader, new AbortController().signal, moves);
    expect(calls.filter((call) => call.url === MEMBER_SESSION_URL)).toHaveLength(2);
    expect(reader.current().kind).toBe("guest");
    expect(events.filter((event) => event === "replace /login")).toHaveLength(1);
    expect(events.filter((event) => event === "update")).toHaveLength(updates);
    expect(events[0]).toBe("show member");
  });

  // 验收第 4 条「离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：
  // 积分回 401 时重读会话，重读完成前已离开时不跳转。
  it("does not leave after the page is gone while the reread settles", async () => {
    stubFetch({
      [MEMBER_SESSION_URL]: [memberReply(), guestReply()],
      [MEMBER_ORDERS_URL]: [ordersReply()],
      [MEMBER_POINTS_URL]: [signedOutReply()],
      [MEMBER_COUPONS_URL]: [couponsReply()],
    });
    const shared = createMemberSessionReader();
    const page = new AbortController();
    let reloads = 0;
    // 第二次重读（积分回 401 之后的那次）发出时页面已离开。
    const reader: MemberSessionReader = {
      ...shared,
      reload: () => {
        reloads += 1;
        if (reloads === 2) {
          page.abort();
        }
        return shared.reload();
      },
    };
    const { moves, events } = fakeMoves();
    await openAccount(reader, page.signal, moves);
    expect(reloads).toBe(2);
    expect(shared.current().kind).toBe("guest");
    expect(events).not.toContain("replace /login");
  });
});

describe("title row", () => {
  // 验收第 2 条「member 时显示标题行——h1 为 common.nav_account，其后遮掩后的手机号（acs-num）与 common.nav_logout 按钮，桌面与手机都显示」
  // 与 UX P13 桌面线框「[common.nav_account] <手机号部分遮盖 [P1]> ([common.nav_logout])」：三者依次在同一行里，不按宽度分两份。
  it.each(LANGUAGES)("shows the title, the masked number and the log-out button in %s", (language) => {
    const html = render({}, language);
    expect(html).toContain(
      `<div class="site-account__head"><h1 class="acs-display-l">${copy("common.nav_account", language)}</h1><span class="acs-num acs-muted">${MASKED}</span>` +
        `<button class="acs-btn acs-btn--secondary acs-btn--sm site-account__logout" type="button" aria-busy="false">${copy("common.nav_logout", language)}</button></div>`,
    );
    expect(html.split(MASKED)).toHaveLength(2);
    expect(html.match(/<h1/g)).toHaveLength(1);
  });
});

describe("navigation and sections", () => {
  // 验收第 3 条「段表…按表渲染…桌面为左侧导航，每项是跳到本页该段的页内锚点，当前段标 aria-current」与 SHOP-TASK-165 验收第 2 条
  // 「ACCOUNT_SECTIONS 依次为我的订单、我的积分（account.points）与我的优惠券（account.coupons），导航与标签照现有写法按表渲染」：
  // 导航依次为三项，各指向本页段内容的锚点，只有当前段标 aria-current（随段表扩展，由「只有一项」改为三项）。
  it.each(LANGUAGES)("lists my orders, my points and my coupons in the desktop navigation in %s", (language) => {
    expect(ACCOUNT_SECTIONS).toEqual([
      { section: "orders", label: "account.orders" },
      { section: "points", label: "account.points" },
      { section: "coupons", label: "account.coupons" },
    ]);
    const nav = part(render({ section: "points" }, language), /<nav class="site-desktop-only site-account__nav">[\s\S]*?<\/nav>/);
    expect(nav).toBe(
      `<nav class="site-desktop-only site-account__nav"><a href="#account-orders">${copy("account.orders", language)}</a>` +
        `<a href="#account-points" aria-current="true">${copy("account.points", language)}</a><a href="#account-coupons">${copy("account.coupons", language)}</a></nav>`,
    );
  });

  // 同一条「手机为分段标签（acs-tabs，role 为 tablist 的容器与 role 为 tab 的按钮，选中项 aria-selected，写法照 LoginPage.tsx 的页签），段内容为 tabpanel」：
  // 三个标签依次排列、只有当前段选中，各控制对应的段内容（id 为锚点），段内容以标签为名称；不是当前段的内容在手机隐藏（随段表扩展，由一个标签改为三个）。
  it.each(LANGUAGES)("shows a tab for each section on phones in %s", (language) => {
    const html = render({}, language);
    const tabs = part(html, /<div class="acs-tabs site-phone-only site-account__tabs" role="tablist">[\s\S]*?<\/div>/);
    const tab = (section: string, label: CopyKey, selected: boolean) =>
      `<button class="acs-tab" id="account-${section}-tab" role="tab" type="button" aria-selected="${String(selected)}" aria-controls="account-${section}">${copy(label, language)}</button>`;
    expect(tabs).toBe(
      `<div class="acs-tabs site-phone-only site-account__tabs" role="tablist">${tab("orders", "account.orders", true)}${tab("points", "account.points", false)}${tab("coupons", "account.coupons", false)}</div>`,
    );
    expect(html.match(/role="tabpanel"/g)).toHaveLength(3);
    expect(html).toContain(`<section class="site-account__section" id="account-orders" role="tabpanel" aria-labelledby="account-orders-tab" aria-busy="false">`);
    expect(html).toContain(`<section class="site-account__section site-account__section--off" id="account-points" role="tabpanel" aria-labelledby="account-points-tab" aria-busy="false">`);
    expect(html).toContain(`<section class="site-account__section site-account__section--off" id="account-coupons" role="tabpanel" aria-labelledby="account-coupons-tab" aria-busy="false">`);
    const panels = [...html.matchAll(/<section[^>]* id="([^"]*)"/g)].map((m) => m[1]);
    expect(panels).toEqual(["account-orders", "account-points", "account-coupons"]);
  });

  // 同一条：点导航项或标签都交给页面切换当前段（每一项都交出自己的段）。
  it("hands navigation and tab clicks to the page", () => {
    const onSection = vi.fn();
    const elements = tree({ onSection });
    const anchors = elements.filter((element) => element.type === "a" && typeof element.props.href === "string" && element.props.href.startsWith("#account-"));
    const tabs = elements.filter((element) => element.type === "button" && element.props.role === "tab");
    for (const element of [...anchors, ...tabs]) {
      (element.props.onClick as () => void)();
    }
    expect(onSection.mock.calls).toEqual([["orders"], ["points"], ["coupons"], ["orders"], ["points"], ["coupons"]]);
  });

  // SHOP-TASK-165 验收第 2 条「读取中该段标 aria-busy」：积分或优惠券读取中时只有该段标 aria-busy，只有段标题。
  it.each<["points" | "coupons", CopyKey]>([
    ["points", "account.points"],
    ["coupons", "account.coupons"],
  ])("marks only the %s section busy while it loads", (section, title) => {
    const html = render({ screen: section === "points" ? memberScreen({ points: { status: "loading" } }) : memberScreen({ coupons: { status: "loading" } }) });
    const busy = [...html.matchAll(/<section[^>]* id="account-(\w+)"[^>]* aria-busy="(true|false)">/g)].map((m) => [m[1], m[2]]);
    expect(busy).toEqual(["orders", "points", "coupons"].map((name) => [name, name === section ? "true" : "false"]));
    const panel = part(html, new RegExp(`<section[^>]* id="account-${section}"[\\s\\S]*?</section>`));
    expect(textNodes(panel)).toEqual([COPY[title].en]);
  });

  // 同一条「★ account.demo_hint 在桌面右栏顶部，手机在标题行（含暂放的手机号与退出按钮）之后」与 UX P13 两份线框：
  // 一份提示，在标题行之后、导航之后（右栏第一项），在分段标签与段内容之前；手机隐藏导航后即在标题行之后、标签之前。
  it("places the demo hint at the top of the main column", () => {
    const html = render();
    const main = part(html, /<div class="site-account__main">[\s\S]*$/);
    expect(main).toMatch(/^<div class="site-account__main"><p class="acs-hint">/);
    expect(html.match(new RegExp(copy("account.demo_hint"), "g"))).toHaveLength(1);
    const positions = [
      html.indexOf(`>${COPY["common.nav_logout"].en}<`),
      html.indexOf(`<nav class="site-desktop-only site-account__nav">`),
      html.indexOf(copy("account.demo_hint")),
      html.indexOf(`role="tablist"`),
      html.indexOf(`role="tabpanel"`),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });
});

describe("my orders", () => {
  // 验收第 4 条「每张为日期（照 TrackOrderPage.tsx 导出的 formatDate，按浏览器时区）、订单号（acs-num）、状态名、合计（common.price_myr，原样格式化 total_sen）
  // 与 account.order_view…按服务端顺序显示」与 UX P13 桌面线框「<日期> [K]<订单号> [order.status_*] [M1] ([account.order_view])」：桌面每行依次为这五项，
  // 顺序与接口相同（第二张比第一张新也不重排）。
  it.each(LANGUAGES)("lists each order on desktop in the server's order in %s", (language) => {
    const rows = desktopRows(render({}, language));
    expect(rows).toHaveLength(ORDERS.length);
    for (const [index, order] of ORDERS.entries()) {
      const status = order.status as (typeof STATUSES)[number];
      expect(rows[index]).toBe(
        `<span class="acs-body-s acs-muted">${escapeHtml(formatDate(order.created_at, language))}</span><span class="acs-num">${order.order_number}</span>` +
          `<span><span class="${STATUS_TAG[status]}">${copy(STATUS_KEY[status], language)}</span></span><span class="acs-price site-account__total">${price(order.total_sen)}</span>` +
          `<a class="acs-btn acs-btn--secondary acs-btn--sm" href="/account/order">${copy("account.order_view", language)}</a>`,
      );
    }
  });

  // 同一条「手机整行是链接、读屏标签为 account.order_view，行尾的 › 用 aria-hidden 的图标而不是文字」与 UX P13 手机线框
  // 「<日期> [order.status_*] / [K]<订单号> [M1] >（> 读屏标签 [account.order_view]）」：手机每行为一个链接，依次为日期、状态、订单号与合计加图标。
  it.each(LANGUAGES)("makes each phone row a link labelled account.order_view in %s", (language) => {
    const rows = phoneRows(render({}, language));
    expect(rows).toHaveLength(ORDERS.length);
    for (const [index, order] of ORDERS.entries()) {
      const status = order.status as (typeof STATUSES)[number];
      const row = rows[index] ?? "";
      expect(row).toMatch(new RegExp(`^<a class="site-account__item" aria-label="${copy("account.order_view", language)}" href="/account/order">`));
      expect(row).toContain(
        `<span class="acs-body-s acs-muted">${escapeHtml(formatDate(order.created_at, language))}</span><span><span class="${STATUS_TAG[status]}">${copy(STATUS_KEY[status], language)}</span></span>` +
          `<span class="acs-num">${order.order_number}</span><span class="acs-price site-account__amount">${price(order.total_sen)}<svg`,
      );
      expect(row).toMatch(/<svg[^>]*aria-hidden="true"[^>]*>[\s\S]*?<\/svg><\/span><\/a>$/);
      expect(textNodes(row)).not.toContain("›");
    }
  });

  // 同一条「状态标签样式照视觉稿——已完成为 acs-tag--success，待支付与已取消为 acs-tag--outline，其余为 acs-tag--accent」：每种状态桌面与手机都用对应的标签；
  // 未知状态不显示标签（派生实现约束，同 TrackOrderPage）。
  it.each(STATUSES)("shows the status tag for %s", (status) => {
    expect(statusTag(status)).toEqual({ label: STATUS_KEY[status], className: STATUS_TAG[status] });
    const html = render({ screen: withOrders({ status: "ready", orders: [{ ...ORDERS[0], status } as MemberOrderSummary] }) });
    const tag = `<span class="${STATUS_TAG[status]}">${copy(STATUS_KEY[status])}</span>`;
    expect(desktopList(html)).toContain(tag);
    expect(phoneList(html)).toContain(tag);
  });

  it("shows no tag for an unknown status", () => {
    expect(statusTag("unknown")).toBeNull();
    const html = render({ screen: withOrders({ status: "ready", orders: [{ ...ORDERS[0], status: "unknown" } as MemberOrderSummary] }) });
    expect(desktopList(html)).not.toContain("acs-tag");
    expect(desktopList(html)).toContain(`<span class="acs-num">${ORDERS[0]?.order_number ?? ""}</span>`);
  });

  // 同一条「account.orders_hint 桌面在列表之后、手机在列表之前」与 UX P13 两份线框：桌面提示在桌面列表之后，手机提示在手机列表之前，各一份；
  // 段标题 account.orders（h2）在桌面显示，手机由标签代替。
  it.each(LANGUAGES)("places account.orders_hint after the desktop list and before the phone list in %s", (language) => {
    const html = render({}, language);
    const hint = copy("account.orders_hint", language);
    const phoneHint = html.indexOf(`<p class="acs-body-s acs-muted site-phone-only site-account__hint">${hint}</p>`);
    const desktopHint = html.indexOf(`<p class="acs-body-s acs-muted site-desktop-only site-account__text">${hint}</p>`);
    expect(phoneHint).toBeGreaterThanOrEqual(0);
    expect(desktopHint).toBeGreaterThan(html.indexOf(`<ul class="site-desktop-only site-account__list">`));
    expect(phoneHint).toBeLessThan(html.indexOf(`<ul class="site-phone-only site-account__list">`));
    expect(html.match(new RegExp(hint, "g"))).toHaveLength(2);
    expect(html).toContain(`<h2 class="acs-display-m site-desktop-only">${copy("account.orders", language)}</h2>`);
  });

  // 同一条「没有订单时段标题照常，列表与 account.orders_hint 换成 account.orders_empty」与 HANDOFF 0.61 第（2）条「没有订单时只显示 account.orders_empty」。
  it.each(LANGUAGES)("shows account.orders_empty instead of the list and the hint in %s", (language) => {
    const html = render({ screen: withOrders({ status: "ready", orders: [] }) }, language);
    const section = part(html, /<section[\s\S]*?<\/section>/);
    expect(textNodes(section)).toEqual([COPY["account.orders"][language], COPY["account.orders_empty"][language]]);
    expect(section).not.toContain("<ul");
  });

  // 同一条「会话为 member 后经 SHOP-TASK-150 读取列表（只取第一页，按服务端顺序显示，不翻页）」与 HANDOFF 0.61 第（1）条：
  // 请求不带页码或任何查询参数，只请求一次；total 比第一页多也不翻页，原样显示第一页。
  it("reads only the first page and keeps the server's order", async () => {
    const calls = stubFetch({ [MEMBER_ORDERS_URL]: [json(200, { total: 45, orders: ORDERS })] });
    const { moves, events, current } = fakeMoves(withOrders({ status: "loading" }));
    await loadOrders(createMemberSessionReader(), new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_ORDERS_URL]);
    expect(events).toEqual(["update"]);
    expect(current()).toEqual(withOrders({ status: "ready", orders: ORDERS }));
    expect(render({ screen: current() })).not.toContain("‹");
  });

  // 同一条「读取中该段标 aria-busy」：读取中段内容标 aria-busy，只有段标题。
  it("marks the section busy while the orders load", () => {
    const html = render({ screen: withOrders({ status: "loading" }) });
    const section = part(html, /<section[\s\S]*?<\/section>/);
    expect(section).toContain(`aria-busy="true"`);
    expect(textNodes(section)).toEqual([COPY["account.orders"].en]);
  });

  // 同一条「列表 signed_out 时重读会话并用 replace 进入 /login，failed 显示 common.error_retry，network 显示 common.network_check」与
  // HANDOFF 0.61 第（5）条「任何会员接口回 401 时重读会话并 replace 到 /login」：401 时先重读会话（共享状态变为未登录），再 replace。
  it("rereads the session and goes to /login when the list says signed out", async () => {
    const calls = stubFetch({ [MEMBER_ORDERS_URL]: [json(401, { detail: "member_session_required" })], [MEMBER_SESSION_URL]: [guestReply()] });
    const reader = createMemberSessionReader();
    const { moves, events } = fakeMoves(withOrders({ status: "loading" }));
    await loadOrders(reader, new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_ORDERS_URL, MEMBER_SESSION_URL]);
    expect(events).toEqual(["replace /login"]);
    expect(reader.current().kind).toBe("guest");
    expect(ordersStep({ kind: "signed_out" })).toBe("login");
  });

  it.each<[CopyKey, MemberOrdersRead, Reply]>([
    ["common.error_retry", { kind: "failed" }, new Response(null, { status: 500 })],
    ["common.network_check", { kind: "network" }, "network"],
  ])("shows %s when the list cannot be read", async (key, read, reply) => {
    expect(ordersStep(read)).toEqual({ status: "failed", error: key });
    stubFetch({ [MEMBER_ORDERS_URL]: [reply] });
    const { moves, current } = fakeMoves(withOrders({ status: "loading" }));
    await loadOrders(createMemberSessionReader(), new AbortController().signal, moves);
    expect(current()).toEqual(withOrders({ status: "failed", error: key }));
    const section = part(render({ screen: current() }), /<section[\s\S]*?<\/section>/);
    expect(section).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy(key)}</p>`);
    expect(textNodes(section)).toEqual([COPY["account.orders"].en, COPY[key].en]);
  });

  // 验收第 6 条「离开页面时中止请求，之后不再更新」：列表回答到达前已离开时不更新。
  it("does not update after leaving the page", async () => {
    stubFetch({ [MEMBER_ORDERS_URL]: [ordersReply()] });
    const gone = new AbortController();
    gone.abort();
    const { moves, events } = fakeMoves(withOrders({ status: "loading" }));
    await loadOrders(createMemberSessionReader(), gone.signal, moves);
    expect(events).toEqual([]);
  });
});

describe("viewing an order", () => {
  // 验收第 5 条「/account/order 进入路由表（SHOP-TASK-154）之前不渲染该链接（桌面没有 account.order_view，手机行不作为链接），照 CheckoutPage.tsx 的 loginPath
  // 写法以 isRoutePath 判断，并以 props 传给 View」：去向为 null 时桌面行没有 account.order_view，手机行不是链接、没有读屏标签与 ›；页面没有指向 /account/order 的链接。
  it.each(LANGUAGES)("renders no view entry without a destination in %s", (language) => {
    const html = render({ orderTo: null }, language);
    expect(html).not.toContain(copy("account.order_view", language));
    expect(html).not.toContain(`href="/account/order"`);
    for (const row of desktopRows(html)) {
      expect(row).not.toContain("<a");
    }
    for (const row of phoneRows(html)) {
      expect(row).toMatch(/^<div class="site-account__item">/);
      expect(row).not.toContain("<svg");
    }
  });

  // 同一条「照 loginPath 写法以 isRoutePath 判断」：去向随路由表而定（不断言 /account/order 现在在不在表里）。
  it("derives the destination from the route table", () => {
    expect(orderPath()).toBe(isRoutePath(ORDER_TO_PATH) ? ORDER_TO_PATH : null);
  });

  // 同一条「点 account.order_view 时先经 SHOP-TASK-150 的交接函数交出该单订单号，再前往 /account/order（订单号不进地址、历史状态与浏览器存储，HANDOFF 0.61）」：
  // 桌面按钮与手机整行都是指向 /account/order 的站内链接（Link 先调用 onClick、再前往），点击时交出的是该行的订单号；链接地址里没有订单号。
  it("hands off the order number when a view link is clicked", () => {
    const links = tree({ onView: viewOrder }).filter((element) => element.type === Link);
    expect(links).toHaveLength(ORDERS.length * 2);
    for (const [index, link] of links.entries()) {
      const order = ORDERS[index % ORDERS.length];
      expect(link.props.to).toBe(ORDER_TO_PATH);
      (link.props.onClick as () => void)();
      expect(memberOrderInView()).toBe(order?.order_number);
    }
    for (const href of [...render().matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "")) {
      for (const order of ORDERS) {
        expect(href).not.toContain(order.order_number);
      }
    }
  });
});

describe("logging out", () => {
  // 验收第 5 条「点 common.nav_logout 经 SHOP-TASK-151 以会话里的 CSRF 令牌退出…ok 与 signed_out 时清除 SHOP-TASK-150 交接的订单号、重读会话并用 replace 前往首页 P01」
  // 与 UX P13 去向「P01（退出或注销后）」：请求头带打开时读到的令牌；之后交接的订单号已清除，共享会话重读为未登录，再 replace 到 /。
  it.each<[string, Response]>([
    ["ok", new Response(null, { status: 204 })],
    ["signed_out", json(401, { detail: "member_session_required" })],
  ])("clears the hand-off and goes home when logging out is %s", async (_name, reply) => {
    const calls = stubFetch({ [MEMBER_LOGOUT_URL]: [reply], [MEMBER_SESSION_URL]: [guestReply()] });
    handOffMemberOrder(ORDERS[0]?.order_number ?? "");
    const reader = createMemberSessionReader();
    const { moves, events } = fakeMoves(startLogout(memberScreen()));
    await logOut(reader, TOKEN, new AbortController().signal, moves);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_LOGOUT_URL, MEMBER_SESSION_URL]);
    expect(headerOf(calls[0]?.init ?? {}, CSRF_HEADER)).toBe(TOKEN);
    expect(memberOrderInView()).toBeNull();
    expect(reader.current().kind).toBe("guest");
    expect(events).toEqual(["replace /"]);
  });

  // 同一条「csrf 时重读会话并显示 common.error_retry」：重读得到新令牌后按钮恢复、显示提示，下次退出带新令牌；不跳转、不清除交接。
  it("rereads the session and asks to retry after a csrf failure", async () => {
    stubFetch({ [MEMBER_LOGOUT_URL]: [json(403, { detail: "csrf_failed" })], [MEMBER_SESSION_URL]: [memberReply(NEW_TOKEN)] });
    handOffMemberOrder("K7Q29MXAB6TN2RJD");
    const { moves, events, current } = fakeMoves(startLogout(memberScreen()));
    await logOut(createMemberSessionReader(), TOKEN, new AbortController().signal, moves);
    expect(events).toEqual(["update"]);
    expect(current()).toEqual(memberScreen({ csrfToken: NEW_TOKEN, logoutError: "common.error_retry" }));
    expect(memberOrderInView()).toBe("K7Q29MXAB6TN2RJD");
  });

  // 同一条「failed 显示 common.error_retry；network 显示 common.network_check」：不重读会话、不跳转，令牌不变。
  it.each<[MemberLogoutReply, CopyKey, Reply]>([
    ["failed", "common.error_retry", new Response(null, { status: 500 })],
    ["network", "common.network_check", "network"],
  ])("shows the notice when logging out is %s", async (reply, key, response) => {
    expect(logoutStep(reply)).toBe(key);
    const calls = stubFetch({ [MEMBER_LOGOUT_URL]: [response] });
    const { moves, events, current } = fakeMoves(startLogout(memberScreen()));
    await logOut(createMemberSessionReader(), TOKEN, new AbortController().signal, moves);
    expect(calls).toHaveLength(1);
    expect(events).toEqual(["update"]);
    expect(current()).toEqual(memberScreen({ logoutError: key }));
    const html = render({ screen: current() });
    expect(html).toContain(`</div><p class="acs-alert acs-alert--danger site-notice" role="alert">${copy(key)}</p><div class="site-account__body">`);
  });

  it("maps every log-out reply", () => {
    expect(logoutStep("ok")).toBe("home");
    expect(logoutStep("signed_out")).toBe("home");
    expect(logoutStep("csrf")).toBe("reread");
  });

  // 同一条「进行中按钮禁用」：退出进行中按钮禁用并标 aria-busy，不能再次退出；平时可点并交给页面处理。
  it("disables the button while logging out", () => {
    const onLogout = vi.fn();
    const idle = tree({ onLogout }).find((element) => element.type === "button" && element.props.className === "acs-btn acs-btn--secondary acs-btn--sm site-account__logout");
    expect(idle?.props.disabled).toBe(false);
    (idle?.props.onClick as () => void)();
    expect(onLogout).toHaveBeenCalledTimes(1);
    const busy = startLogout(memberScreen({ logoutError: "common.error_retry" }));
    expect(busy).toEqual(memberScreen({ loggingOut: true }));
    expect(canLogout(busy)).toBe(false);
    expect(canLogout(memberScreen())).toBe(true);
    expect(render({ screen: busy })).toContain(`<button class="acs-btn acs-btn--secondary acs-btn--sm site-account__logout" type="button" disabled="" aria-busy="true">`);
  });

  // 验收第 6 条「离开页面时中止请求，之后不再更新」：退出的回答到达前已离开时不更新、不跳转。
  it("does nothing after leaving the page", async () => {
    stubFetch({ [MEMBER_LOGOUT_URL]: [new Response(null, { status: 204 })], [MEMBER_SESSION_URL]: [guestReply()] });
    const gone = new AbortController();
    gone.abort();
    const { moves, events } = fakeMoves(startLogout(memberScreen()));
    await logOut(createMemberSessionReader(), TOKEN, gone.signal, moves);
    expect(events).toEqual([]);
  });
});

// 本标签页 sessionStorage 的替身：只记 P06 当前订单暂存这类键值。
function stubSessionStorage(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
  });
  return store;
}

describe("logging out clears the current pay order (SHOP-TASK-163)", () => {
  // DESIGN 1.14「权限与资料保护」第 7 条与 UX 0.13 P06「会员的当前订单」「…会员退出或注销时清除暂存」，SHOP-TASK-163 验收「退出在 ok 与 signed_out 时，
  // 除现有的 clearMemberOrder 外另经 SHOP-TASK-158 清除当前订单暂存」：退出之后模块变量与 sessionStorage 里都没有暂存，交接同样已清除，照常 replace 到 /。
  it.each<[string, Response]>([
    ["ok", new Response(null, { status: 204 })],
    ["signed_out", json(401, { detail: "member_session_required" })],
  ])("clears the stash when logging out is %s", async (_name, reply) => {
    const store = stubSessionStorage();
    stubFetch({ [MEMBER_LOGOUT_URL]: [reply], [MEMBER_SESSION_URL]: [guestReply()] });
    handOffMemberOrder("K7Q29MXAB6TN2RJD");
    stashMemberPayOrder("K7Q29MXAB6TN2RJD");
    expect(store.get(MEMBER_PAY_ORDER_KEY)).toBe("K7Q29MXAB6TN2RJD");
    const { moves, events } = fakeMoves(startLogout(memberScreen()));
    await logOut(createMemberSessionReader(), TOKEN, new AbortController().signal, moves);
    expect(stashedMemberPayOrder()).toBeNull();
    expect(store.has(MEMBER_PAY_ORDER_KEY)).toBe(false);
    expect(memberOrderInView()).toBeNull();
    expect(events).toEqual(["replace /"]);
  });

  // 同一条「其余去向（重读会话、replace 前往首页、csrf、failed、network）不变」：退出未成（csrf、failed）时会员仍登录，暂存照旧保留，以便回到 P06 继续支付。
  it.each<[string, Record<string, Reply[]>]>([
    ["csrf", { [MEMBER_LOGOUT_URL]: [json(403, { detail: "csrf_failed" })], [MEMBER_SESSION_URL]: [memberReply(NEW_TOKEN)] }],
    ["failed", { [MEMBER_LOGOUT_URL]: [new Response(null, { status: 500 })] }],
  ])("keeps the stash when logging out is %s", async (_name, routes) => {
    const store = stubSessionStorage();
    stubFetch(routes);
    stashMemberPayOrder("K7Q29MXAB6TN2RJD");
    const { moves, events } = fakeMoves(startLogout(memberScreen()));
    await logOut(createMemberSessionReader(), TOKEN, new AbortController().signal, moves);
    expect(events).toEqual(["update"]);
    expect(stashedMemberPayOrder()).toBe("K7Q29MXAB6TN2RJD");
    expect(store.get(MEMBER_PAY_ORDER_KEY)).toBe("K7Q29MXAB6TN2RJD");
  });
});

describe("dictionary", () => {
  // 验收第 1 条「frontend/src/i18n/copy.ts 按 UX-COPY 0.14 逐字加入本页用到…的键」与第 6 条「页面文字全部来自字典，带变量的条目按模板匹配」：
  // 各状态下，除订单数据（日期、订单号）与遮掩后的手机号外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides member data in %s", (language) => {
    const screens: Partial<AccountViewProps>[] = [
      {},
      { orderTo: null },
      { screen: LOADING },
      { screen: { status: "failed" } },
      { screen: withOrders({ status: "loading" }) },
      { screen: withOrders({ status: "ready", orders: [] }) },
      { screen: withOrders({ status: "failed", error: "common.error_retry" }) },
      { screen: withOrders({ status: "failed", error: "common.network_check" }) },
      { screen: memberScreen({ loggingOut: true }) },
      { screen: memberScreen({ logoutError: "common.network_check" }) },
    ];
    const memberData = new Set([MASKED, ...ORDERS.flatMap((order) => [order.order_number, formatDate(order.created_at, language)])]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of screens) {
      for (const value of visibleTexts(render(state, language))) {
        expect(memberData.has(value) || patterns.some((pattern) => pattern.test(value)), value).toBe(true);
      }
    }
  });

  // UX「阅读说明」不写死文字：本页用到的键在字典里，三语都有（逐字核对由 copy.test.ts 读 UX-COPY 守住）。
  it("has every key this page uses", () => {
    const keys: CopyKey[] = ["common.nav_account", "common.nav_logout", "account.orders", "account.orders_hint", "account.orders_empty", "account.order_view", "account.demo_hint"];
    for (const key of keys) {
      for (const language of LANGUAGES) {
        expect(COPY[key][language], key).not.toBe("");
      }
    }
    // SHOP-TASK-165：sessionStep 的结果多了积分与优惠券两段，三段都从读取中开始（随状态扩展修改）。
    expect(sessionStep({ kind: "member", phoneMasked: MASKED, hasPassword: true, csrfToken: TOKEN } satisfies MemberSessionState)).toEqual(
      memberScreen({ orders: { status: "loading" }, points: { status: "loading" }, coupons: { status: "loading" } }),
    );
  });
});
