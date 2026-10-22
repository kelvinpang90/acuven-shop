import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER, payOrdersUrl, rememberPaidNotCancelled, STRIPE_CONFIRM_URL, takePaidNotCancelled } from "../api/pay";
import type { PayOrder } from "../api/pay";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { isRoutePath, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import { countryName } from "./PayPage";
import {
  AUTO_RECHECK_MS,
  AUTO_RECHECKS,
  checkAgain,
  confirming,
  keepOnFailure,
  LOADING,
  openResult,
  PayResultView,
  startChecking,
} from "./PayResultPage";
import type { ResultMoves, ResultScreen } from "./PayResultPage";

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";

function payOrder(overrides: Partial<PayOrder> = {}): PayOrder {
  return {
    order_number: ORDER_NUMBER,
    status: "demo_paid",
    created_at: "2026-10-05T10:00:00Z",
    payment_expires_at: "2026-10-05T10:15:00Z",
    server_time: "2026-10-05T10:03:00Z",
    subtotal_sen: 12000,
    shipping_fee_sen: 1500,
    total_sen: 13500,
    recipient: {
      name: "Aina Rahman",
      phone: "+60123456789",
      country_code: "MY",
      region: "MY-10",
      address: "12 Jalan Contoh 3",
      postal_code: "47000",
    },
    last_payment: { method: "card", result: "success", created_at: "2026-10-05T10:02:00Z" },
    cancelled_by: null,
    payment_route: "simulated",
    stripe_state: null,
    stripe_refunded: false,
    ...overrides,
  };
}

const failed = payOrder({ status: "awaiting_demo_payment", last_payment: { method: "bank", result: "failure", created_at: "2026-10-05T10:02:00Z" } });
const timedOut = payOrder({ status: "demo_cancelled", last_payment: null, cancelled_by: "timeout" });
const cancelledByYou = payOrder({ status: "demo_cancelled", last_payment: null, cancelled_by: "self" });
const STATES = new Map([["MY-10", "Selangor"]]);

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function render(screen: ResultScreen, language: Language = "en", minutes = 12, paidNotCancelled = false): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/pay/result">
        <PayResultView screen={screen} minutes={minutes} states={STATES} paidNotCancelled={paidNotCancelled} onCheckAgain={() => undefined} />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function ready(order: PayOrder): ResultScreen {
  return { status: "ready", order };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// 页面上访客能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const attributes = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/g)].map((m) => unescapeHtml(m[1] ?? ""));
  const textNodes = html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
  return [...textNodes, ...attributes].filter((value) => value !== "");
}

function copyPattern(template: string): RegExp {
  const parts = template.split(/\{\w+\}/).map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^${parts.join(".+")}$`);
}

function has(html: string, key: CopyKey, language: Language = "en"): boolean {
  return html.includes(escapeHtml(COPY[key][language]));
}

describe("success", () => {
  // SHOP-TASK-024 验收第 8 条「demo_paid 显示 result.success_title、result.success_body、订单号与 common.copy、pay.save_order_no、pay.amount_due、收货资料、
  // result.guest_next、★ result.demo_hint 与 result.continue（去商品列表）」。
  it.each(LANGUAGES)("shows the paid order in %s", (language) => {
    const html = render(ready(payOrder()), language);
    const keys: CopyKey[] = [
      "result.success_title",
      "result.success_body",
      "pay.order_no",
      "common.copy",
      "pay.save_order_no",
      "pay.amount_due",
      "checkout.recipient_title",
      "result.guest_next",
      "result.demo_hint",
    ];
    for (const key of keys) {
      expect(has(html, key, language), key).toBe(true);
    }
    expect(html).toContain(`<span class="acs-num site-pay__number">${ORDER_NUMBER}</span>`);
    expect(html).toContain(">RM 135.00<");
    expect(html).toContain(`<a class="acs-btn acs-btn--secondary site-result__continue" href="/products">${COPY["result.continue"][language]}</a>`);
    expect(html).toContain("<span>Selangor</span>");
    expect(html).toContain(`<span>${countryName("MY", language)}</span>`);
  });

  // SHOP-TASK-024 验收第 8 条「result.guest_register 与 result.track 按路由规则在注册页、会员订单详情实现前不渲染，不显示获得积分」与
  // UX P07 说明「游客结果页不提供确认收货、退款或其他订单的入口，也不设直达 P09 的按钮」：成功页唯一的链接是去商品列表。
  it("has no register, order detail or points", () => {
    const html = render(ready(payOrder()));
    expect(isRoutePath("/register")).toBe(false);
    expect(isRoutePath("/account/order")).toBe(false);
    for (const key of ["result.guest_register", "result.track", "result.points_earned", "result.retry"]) {
      expect(html).not.toContain(key);
    }
    expect(html).not.toMatch(/Register|View this order|demo points/);
    expect([...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1])).toEqual(["/products"]);
  });

  // UX P07 线框「成功」的顺序：[result.success_title]、[result.success_body]、订单号、[pay.save_order_no]、[pay.amount_due]、收货资料、[result.guest_next]、
  // ★ [result.demo_hint]、[result.continue]。
  it("follows the order of the wireframe", () => {
    const html = render(ready(payOrder()));
    const positions = [
      html.indexOf(COPY["result.success_title"].en),
      html.indexOf(escapeHtml(COPY["result.success_body"].en)),
      html.indexOf(ORDER_NUMBER),
      html.indexOf(escapeHtml(COPY["pay.save_order_no"].en)),
      html.indexOf(COPY["pay.amount_due"].en),
      html.indexOf(COPY["checkout.recipient_title"].en),
      html.indexOf(escapeHtml(COPY["result.guest_next"].en)),
      html.indexOf(escapeHtml(COPY["result.demo_hint"].en)),
      html.indexOf(COPY["result.continue"].en),
    ];
    for (const position of positions) {
      expect(position).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  // SHOP-TASK-024 验收第 9 条「金额原样来自接口」与 UX P07 M1「已模拟支付金额（快照）」：合计与小计加运费不符时照样显示 total_sen；不显示参考外币。
  it("shows the total exactly as the order returns it", () => {
    const html = render(ready(payOrder({ subtotal_sen: 10000, shipping_fee_sen: 1000, total_sen: 9999 })));
    expect(html).toContain(">RM 99.99<");
    expect(html).not.toContain("RM 110.00");
    expect(html).not.toContain("≈");
  });

  // 派生实现约束（实现选择）：守住第 8 条「按重新读取的订单显示」——已模拟支付之后的状态（如管理员已模拟发货）也按成功显示。
  it("shows later statuses as success", () => {
    expect(render(ready(payOrder({ status: "demo_shipped" })))).toContain(COPY["result.success_title"].en);
  });
});

describe("failure", () => {
  // SHOP-TASK-024 验收第 8 条「最近一次支付失败且仍待支付时显示 result.failure_title、result.failure_body、pay.expires 倒计时与 result.retry（回 P06）」：
  // 没有成功页的内容与 result.continue。
  it.each(LANGUAGES)("shows the failed payment with the countdown and a retry link in %s", (language) => {
    const html = render(ready(failed), language, 7);
    expect(has(html, "result.failure_title", language)).toBe(true);
    expect(has(html, "result.failure_body", language)).toBe(true);
    expect(html).toContain(escapeHtml(COPY["pay.expires"][language].replace("{minutes}", "7")));
    expect(html).toContain(`<a class="acs-btn acs-btn--primary site-result__retry" href="/pay">${COPY["result.retry"][language]}</a>`);
    expect(has(html, "result.success_title", language)).toBe(false);
    expect(has(html, "result.continue", language)).toBe(false);
    expect(html).not.toContain(ORDER_NUMBER);
  });
});

describe("cancelled", () => {
  // SHOP-TASK-024 验收第 8 条「demo_cancelled 按取消方显示 result.cancelled（超时）或 result.cancelled_by_you，以及 result.continue」：
  // 两种文案互斥，取消方未知时按超时。
  it.each(LANGUAGES)("chooses the text by who cancelled in %s", (language) => {
    const timeout = render(ready(timedOut), language);
    expect(has(timeout, "result.cancelled", language)).toBe(true);
    expect(has(timeout, "result.cancelled_by_you", language)).toBe(false);
    const self = render(ready(cancelledByYou), language);
    expect(has(self, "result.cancelled_by_you", language)).toBe(true);
    expect(has(self, "result.cancelled", language)).toBe(false);
    const unknown = render(ready(payOrder({ status: "demo_cancelled", cancelled_by: null })), language);
    expect(has(unknown, "result.cancelled", language)).toBe(true);
    for (const html of [timeout, self]) {
      expect(html).toContain(`href="/products">${COPY["result.continue"][language]}</a>`);
      expect(has(html, "result.retry", language)).toBe(false);
      expect(has(html, "result.success_title", language)).toBe(false);
    }
  });
});

describe("session expired and loading", () => {
  // SHOP-TASK-024 验收第 3 条「接口 401 时整页显示 pay.session_expired，common.nav_track 按路由规则在订单查询页实现前不渲染」与
  // SHOP-TASK-028 验收第 3 条「加入 /track 后…P06、P07 授权过期提示里的 common.nav_track 按路由规则出现并链到 /track」、UX P07「游客凭据过期 [pay.session_expired] ( [common.nav_track] ) → P08」：
  // 结果页同样只有这一句与链到 /track 的 common.nav_track。
  it.each(LANGUAGES)("replaces the whole page with pay.session_expired in %s", (language) => {
    const html = render({ status: "expired" }, language);
    expect(isRoutePath("/track")).toBe(true);
    expect(visibleTexts(html)).toEqual([COPY["pay.session_expired"][language], COPY["common.nav_track"][language]]);
    expect([...html.matchAll(/<a [^>]*href="([^"]*)"/g)].map((m) => m[1])).toEqual(["/track"]);
  });

  // 派生实现约束（实现选择）：接口返回之前主体为空并标 aria-busy；读取失败时只显示 common.error_retry；仍待支付而最近一次不是失败（页面正换成 P06）时不显示任何结果。
  it("shows nothing before the order arrives", () => {
    expect(render({ status: "loading" })).toBe(`<main class="site-pay" aria-busy="true"></main>`);
    expect(visibleTexts(render({ status: "error" }))).toEqual([COPY["common.error_retry"].en]);
    expect(render(ready(payOrder({ status: "awaiting_demo_payment", last_payment: null })))).toBe(`<main class="site-pay" aria-busy="true"></main>`);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-024 验收第 9 条「页面文字全部来自字典」：各状态下，除订单数据（订单号、收货资料、州属与国家名、分隔符号）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const screens: ResultScreen[] = [ready(payOrder()), ready(failed), ready(timedOut), ready(cancelledByYou), { status: "expired" }, { status: "error" }];
    const orderData = new Set([ORDER_NUMBER, "Aina Rahman", "+60123456789", "12 Jalan Contoh 3", "47000", "Selangor", countryName("MY", language), "·", ","]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const screen of screens) {
      for (const value of visibleTexts(render(screen, language))) {
        const known = orderData.has(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});

// —— Stripe 途径（SHOP-TASK-133）——

const stripePending = payOrder({ status: "awaiting_demo_payment", last_payment: null, payment_route: "stripe", stripe_state: "open" });
const paidOrder = payOrder({ payment_route: null, stripe_state: "complete" });

afterEach(() => {
  vi.unstubAllGlobals();
  // 不让上一条测试记下的「取消时会话已付款」留到下一条。
  takePaidNotCancelled("");
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function ordersReply(order: PayOrder, token = "token-0"): Response {
  return json(200, { orders: [order], csrf_token: token });
}

function confirmReply(result: string): Response {
  return json(200, { result });
}

// 一次回答：响应、网络中断，或在请求发出时才给出响应的函数（可在其中中止）。
type Reply = Response | "network" | (() => Response);

// fetch 替身：按顺序给出回答；回答用完或请求已中止时按网络中断。api/pay.ts 只以字符串地址调用 fetch，所以 input 写成 string
// （对 RequestInfo | URL 或请求体直接调用 String 会触发带类型规则 @typescript-eslint/no-base-to-string）。
function stubFetch(...replies: Reply[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string, init?: RequestInit) => {
    const next = queue.shift();
    if (next === undefined) {
      return Promise.reject(new Error(`unexpected request ${input}`));
    }
    const reply = typeof next === "function" ? next() : next;
    if (reply === "network" || init?.signal?.aborted) {
      return Promise.reject(new TypeError("Failed to fetch"));
    }
    return Promise.resolve(reply);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

function urls(fetchMock: FetchMock): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input));
}

// 每次确认请求带的 CSRF 令牌与请求体。
function confirmCalls(fetchMock: FetchMock): { token: string; body: string }[] {
  return fetchMock.mock.calls
    .filter(([input]) => String(input) === STRIPE_CONFIRM_URL)
    .map(([, init]) => ({
      token: String((init?.headers as Record<string, string> | undefined)?.[CSRF_HEADER]),
      body: typeof init?.body === "string" ? init.body : "",
    }));
}

// 页面替身：保存当前状态（update 按它改动），记录每次显示、经 replace 去的地址与 takePaidNotCancelled 的结果。
function fakeMoves(initial: ResultScreen = LOADING) {
  let current = initial;
  const screens: ResultScreen[] = [];
  const replaced: RoutePath[] = [];
  const notices: boolean[] = [];
  const target: ResultMoves = {
    show: (screen) => {
      current = screen;
      screens.push(screen);
    },
    update: (change) => {
      current = change(current);
      screens.push(current);
    },
    replace: (path) => {
      replaced.push(path);
    },
    notice: (shown) => {
      notices.push(shown);
    },
  };
  return { target, screens, replaced, notices };
}

// delay 替身：立即结束，记录等待的毫秒数；onCall 收到第几次等待（可在其中中止）。
function fakeDelay(onCall: (count: number) => void = () => undefined) {
  const waits: number[] = [];
  const delay = vi.fn((ms: number, signal?: AbortSignal) => {
    waits.push(ms);
    expect(signal).toBeDefined();
    onCall(waits.length);
    return Promise.resolve();
  });
  return { delay, waits };
}

// 第一次自动重查前就中止：只看确认本身的结果。
function stopAtFirstWait(controller: AbortController) {
  return fakeDelay(() => {
    controller.abort();
  }).delay;
}

const ORDERS = payOrdersUrl("en");

describe("stripe decision", () => {
  // UX 0.11「订单仍待支付且本单有 Stripe 会话时，页面调用本站的确认接口一次（须 CSRF）」、HANDOFF 0.55 第（5）项与验收「以读取给的 CSRF 令牌调用 confirmStripe 一次，
  // 确认返回前按读取中显示」「paid 与 refunded 重新读取订单并按读到的状态显示」：先显示读取中，确认带读到的令牌与订单号，paid 后再读一次并显示成功。
  it("confirms once with the read token and shows the paid order", async () => {
    const fetchMock = stubFetch(ordersReply(stripePending, "token-a"), confirmReply("paid"), ordersReply(paidOrder, "token-b"));
    const moves = fakeMoves();
    await openResult("en", new AbortController().signal, moves.target, { delay: fakeDelay().delay });
    expect(urls(fetchMock)).toEqual([ORDERS, STRIPE_CONFIRM_URL, ORDERS]);
    expect(confirmCalls(fetchMock)).toEqual([{ token: "token-a", body: JSON.stringify({ order_number: ORDER_NUMBER }) }]);
    expect(moves.screens).toEqual([LOADING, ready(paidOrder)]);
    expect(render(LOADING)).toContain('aria-busy="true"');
    expect(has(render(ready(paidOrder)), "result.success_title")).toBe(true);
  });

  // 同一条「refunded 重新读取订单并按读到的状态显示」与 UX 0.11「已取消、已发起退款」：退款后读到已取消、有退款尝试的订单，显示 result.stripe_late_refund。
  it("rereads after refunded and shows the late refund", async () => {
    const refunded = payOrder({ status: "demo_cancelled", last_payment: null, cancelled_by: "timeout", payment_route: null, stripe_state: null, stripe_refunded: true });
    const fetchMock = stubFetch(ordersReply(stripePending), confirmReply("refunded"), ordersReply(refunded));
    const moves = fakeMoves();
    await openResult("en", new AbortController().signal, moves.target, { delay: fakeDelay().delay });
    expect(urls(fetchMock)).toEqual([ORDERS, STRIPE_CONFIRM_URL, ORDERS]);
    expect(moves.screens).toEqual([LOADING, ready(refunded)]);
    expect(has(render(ready(refunded)), "result.stripe_late_refund")).toBe(true);
  });

  // UX 0.11「尚未付款：显示 [result.stripe_not_paid] 与 ( [result.stripe_back_to_pay] ) 回到 P06」：不再重查，按钮链到 /pay。
  it.each(LANGUAGES)("shows not_paid with the way back to /pay in %s", async (language) => {
    stubFetch(ordersReply(stripePending), confirmReply("not_paid"));
    const moves = fakeMoves();
    const { delay, waits } = fakeDelay();
    await openResult(language, new AbortController().signal, moves.target, { delay });
    expect(moves.screens).toEqual([LOADING, { status: "not_paid" }]);
    expect(waits).toEqual([]);
    const html = render({ status: "not_paid" }, language);
    expect(has(html, "result.stripe_not_paid", language)).toBe(true);
    expect(html).toContain(`<a class="acs-btn acs-btn--primary" href="/pay">${COPY["result.stripe_back_to_pay"][language]}</a>`);
    expect(has(html, "result.demo_hint", language)).toBe(false);
    expect(has(html, "result.failure_title", language)).toBe(false);
  });

  // 验收「confirming、failed、csrf 与 network 都显示确认中」与 HANDOFF 0.54「Stripe 不可用与确认返回 needs_review 都按『确认中』显示」：
  // 确认回答 confirming、500、403、看不懂的结果或网络中断，都显示确认中并开始自动重查（第一次等待时中止）。
  it.each<[string, Reply]>([
    ["confirming", confirmReply("confirming")],
    ["failed", json(500, {})],
    ["unknown result", confirmReply("needs_review")],
    ["csrf", json(403, {})],
    ["network", "network"],
  ])("shows confirming after %s", async (_name, reply) => {
    stubFetch(ordersReply(stripePending), reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    await openResult("en", controller.signal, moves.target, { delay: stopAtFirstWait(controller) });
    expect(moves.screens).toEqual([LOADING, confirming(stripePending, false)]);
  });

  // 验收「expired 整页 pay.session_expired」：确认回 401 时整页凭据过期。
  it("shows the expired page when the confirmation is 401", async () => {
    stubFetch(ordersReply(stripePending), json(401, {}));
    const moves = fakeMoves();
    await openResult("en", new AbortController().signal, moves.target, { delay: fakeDelay().delay });
    expect(moves.screens).toEqual([LOADING, { status: "expired" }]);
  });

  // 验收「这次重新读取 network 或 failed 时按确认中处理、进入或继续自动重查；expired 整页 pay.session_expired」：paid 之后的读取失败不显示错误页。
  it.each<[string, Reply]>([
    ["network", "network"],
    ["failed", json(500, {})],
  ])("treats a %s reread after paid as confirming", async (_name, reply) => {
    stubFetch(ordersReply(stripePending), confirmReply("paid"), reply);
    const controller = new AbortController();
    const moves = fakeMoves();
    await openResult("en", controller.signal, moves.target, { delay: stopAtFirstWait(controller) });
    expect(moves.screens).toEqual([LOADING, confirming(stripePending, false)]);
  });

  it("shows the expired page when the reread after paid is 401", async () => {
    stubFetch(ordersReply(stripePending), confirmReply("paid"), json(401, {}));
    const moves = fakeMoves();
    await openResult("en", new AbortController().signal, moves.target, { delay: fakeDelay().delay });
    expect(moves.screens).toEqual([LOADING, { status: "expired" }]);
  });

  // 验收「仍待支付而 stripe_state 为 null 时——payment_route 为 stripe 一律用路由的 replace 换成 /pay（订单此前即使有模拟支付失败的记录也一样），
  // payment_route 为 simulated 照现有 resultKind 处理」与 UX 0.11「Stripe 支付途径没有『失败』状态」：不调用确认接口。
  it("keeps orders without a Stripe record on the existing path", async () => {
    const failedLast = { method: "bank", result: "failure", created_at: "2026-10-05T10:02:00Z" } as const;
    const stripeFailed = payOrder({ status: "awaiting_demo_payment", last_payment: failedLast, payment_route: "stripe", stripe_state: null });
    const simulatedFresh = payOrder({ status: "awaiting_demo_payment", last_payment: null, payment_route: "simulated", stripe_state: null });
    const cases: [PayOrder, ResultScreen[], RoutePath[]][] = [
      [stripeFailed, [], ["/pay"]],
      [failed, [ready(failed)], []],
      [simulatedFresh, [], ["/pay"]],
    ];
    for (const [order, screens, replaced] of cases) {
      const fetchMock = stubFetch(ordersReply(order));
      const moves = fakeMoves();
      await openResult("en", new AbortController().signal, moves.target, { delay: fakeDelay().delay });
      expect(urls(fetchMock)).toEqual([ORDERS]);
      expect(moves.screens).toEqual(screens);
      expect(moves.replaced).toEqual(replaced);
    }
    expect(has(render(ready(failed)), "result.failure_title")).toBe(true);
  });

  // 派生实现约束（实现选择）：守住现有写法「读取失败时保留已显示的结果」——切换语言时的读取失败不把确认中换成错误页，
  // 而是停止自动重查并给出 result.check_again；原本没有结果时为 common.error_retry。
  it("keeps the shown result when a later read fails", () => {
    const shown = confirming(stripePending, false);
    expect(keepOnFailure(shown)).toEqual(confirming(stripePending, true));
    expect(keepOnFailure(ready(paidOrder))).toEqual(ready(paidOrder));
    expect(keepOnFailure({ status: "not_paid" })).toEqual({ status: "not_paid" });
    expect(keepOnFailure(LOADING)).toEqual({ status: "error" });
  });
});

describe("stripe confirming", () => {
  // UX 0.11「确认中：不显示 [result.demo_hint]……显示 [result.confirming_title] 与 [result.confirming_body]」与线框（订单号、[pay.amount_due]、游客 [result.guest_next]）：
  // 也不显示 pay.expires；自动重查用完之前没有 result.confirming_slow 与 result.check_again。
  it.each(LANGUAGES)("shows the confirming area without the demo hint in %s", (language) => {
    const html = render(confirming(stripePending, false), language);
    for (const key of ["result.confirming_title", "result.confirming_body", "pay.order_no", "pay.amount_due", "result.guest_next"] as const) {
      expect(has(html, key, language), key).toBe(true);
    }
    expect(html).toContain(ORDER_NUMBER);
    expect(html).toContain(">RM 135.00<");
    for (const key of ["result.demo_hint", "result.confirming_slow", "result.check_again", "result.success_title", "result.failure_title"] as const) {
      expect(has(html, key, language), key).toBe(false);
    }
    expect(visibleTexts(html).some((value) => copyPattern(COPY["pay.expires"][language]).test(value))).toBe(false);
    expect(html).not.toContain("<button class=\"acs-btn acs-btn--primary site-result__check\"");
  });

  // UX 0.11「用完后停止自动重查，显示 [result.confirming_slow] 与 ( [result.check_again] )……提交期间按钮禁用」与验收「只在手动再查进行中按钮标 aria-busy」。
  it.each(LANGUAGES)("shows check again after the automatic checks and disables it while checking in %s", (language) => {
    const slow = render(confirming(stripePending, true), language);
    expect(has(slow, "result.confirming_slow", language)).toBe(true);
    expect(slow).toContain(`<button class="acs-btn acs-btn--primary site-result__check" type="button" aria-busy="false">${COPY["result.check_again"][language]}</button>`);
    expect(has(slow, "result.demo_hint", language)).toBe(false);
    const checking = startChecking(confirming(stripePending, true));
    expect(checking).toEqual({ status: "confirming", order: stripePending, slow: true, checking: true });
    expect(render(checking, language)).toContain(
      `<button class="acs-btn acs-btn--primary site-result__check" type="button" disabled="" aria-busy="true">${COPY["result.check_again"][language]}</button>`,
    );
    // 自动重查期间与进行中再点都不改动。
    const automatic = confirming(stripePending, false);
    expect(startChecking(automatic)).toBe(automatic);
    expect(startChecking(checking)).toBe(checking);
  });

  // UX 0.11「页面每 5 秒自动重查一次、最多 12 次……用完后停止自动重查」与 HANDOFF 0.55 第（5）项「P07 每次重查都先重新读取订单再确认」：
  // 每次重查先等 5 秒，读取取得新令牌后确认；第 12 次之后不再等待，此时才出现 result.check_again，之前一直是确认中且没有读取中。
  it("stops after the twelfth automatic check", async () => {
    const replies: Reply[] = [ordersReply(stripePending, "token-0"), confirmReply("confirming")];
    for (let count = 1; count <= AUTO_RECHECKS; count += 1) {
      replies.push(ordersReply(stripePending, `token-${String(count)}`), confirmReply("confirming"));
    }
    const fetchMock = stubFetch(...replies);
    const moves = fakeMoves();
    const { delay, waits } = fakeDelay();
    await openResult("en", new AbortController().signal, moves.target, { delay });
    expect(AUTO_RECHECKS).toBe(12);
    expect(waits).toEqual(Array.from({ length: 12 }, () => AUTO_RECHECK_MS));
    expect(AUTO_RECHECK_MS).toBe(5000);
    expect(confirmCalls(fetchMock).map((call) => call.token)).toEqual(Array.from({ length: 13 }, (_, count) => `token-${String(count)}`));
    expect(moves.screens).toEqual([
      LOADING,
      ...Array.from({ length: 12 }, () => confirming(stripePending, false)),
      confirming(stripePending, true),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(26);
  });

  // 验收「重查里的重新读取失败（network 或 failed）与确认失败一样计为一次重查、继续停在确认中画面」：读取一直失败时不再确认，12 次后同样停下。
  it("counts failed rereads as checks", async () => {
    const replies: Reply[] = [ordersReply(stripePending), confirmReply("confirming")];
    for (let count = 1; count <= AUTO_RECHECKS; count += 1) {
      replies.push(count % 2 === 0 ? "network" : json(500, {}));
    }
    const fetchMock = stubFetch(...replies);
    const moves = fakeMoves();
    const { delay, waits } = fakeDelay();
    await openResult("en", new AbortController().signal, moves.target, { delay });
    expect(waits).toHaveLength(12);
    expect(confirmCalls(fetchMock)).toHaveLength(1);
    expect(moves.screens.at(-1)).toEqual(confirming(stripePending, true));
    expect(moves.screens.slice(1).every((screen) => screen.status === "confirming")).toBe(true);
  });

  // 验收「每次重查是重新读取订单（已不是待支付就按读到的状态显示）」：第二次重查读到已付款，显示成功，不再确认也不再等待。
  it("shows success when a recheck finds the order paid", async () => {
    const fetchMock = stubFetch(ordersReply(stripePending), confirmReply("confirming"), "network", ordersReply(paidOrder));
    const moves = fakeMoves();
    const { delay, waits } = fakeDelay();
    await openResult("en", new AbortController().signal, moves.target, { delay });
    expect(waits).toHaveLength(2);
    expect(confirmCalls(fetchMock)).toHaveLength(1);
    expect(moves.screens).toEqual([LOADING, confirming(stripePending, false), confirming(stripePending, false), ready(paidOrder)]);
  });

  // 同一条：重查时仍待支付，再确认得到 paid，重新读取后显示成功。
  it("shows success when a recheck confirms the payment", async () => {
    const fetchMock = stubFetch(ordersReply(stripePending), confirmReply("confirming"), ordersReply(stripePending, "token-1"), confirmReply("paid"), ordersReply(paidOrder));
    const moves = fakeMoves();
    await openResult("en", new AbortController().signal, moves.target, { delay: fakeDelay().delay });
    expect(confirmCalls(fetchMock).map((call) => call.token)).toEqual(["token-0", "token-1"]);
    expect(moves.screens.at(-1)).toEqual(ready(paidOrder));
  });

  // 验收「expired 照判定一条整页 pay.session_expired」：重查读到 401 时整页凭据过期，停止重查。
  it("shows the expired page when a recheck is 401", async () => {
    stubFetch(ordersReply(stripePending), confirmReply("confirming"), json(401, {}));
    const moves = fakeMoves();
    const { delay, waits } = fakeDelay();
    await openResult("en", new AbortController().signal, moves.target, { delay });
    expect(waits).toHaveLength(1);
    expect(moves.screens.at(-1)).toEqual({ status: "expired" });
  });

  // 验收「离开页面或切换语言时中止，中止后的结果不再更新页面」：等待中中止后不再读取；确认请求进行中中止后，其回答不改变页面。
  it("stops updating after the abort", async () => {
    const waiting = new AbortController();
    const first = stubFetch(ordersReply(stripePending), confirmReply("confirming"), ordersReply(stripePending), confirmReply("confirming"));
    const moves = fakeMoves();
    await openResult("en", waiting.signal, moves.target, {
      delay: fakeDelay((count) => {
        if (count === 2) {
          waiting.abort();
        }
      }).delay,
    });
    expect(first).toHaveBeenCalledTimes(4);
    expect(moves.screens).toEqual([LOADING, confirming(stripePending, false), confirming(stripePending, false)]);

    const confirmingNow = new AbortController();
    const second = stubFetch(ordersReply(stripePending), () => {
      confirmingNow.abort();
      return confirmReply("paid");
    });
    const left = fakeMoves();
    await openResult("en", confirmingNow.signal, left.target, { delay: fakeDelay().delay });
    expect(second).toHaveBeenCalledTimes(2);
    expect(left.screens).toEqual([LOADING]);
  });

  // UX 0.11「每点一次重查一次」：手动再查重新读取并确认一次，仍为确认中时按钮恢复（仍显示 result.check_again），已付款时显示成功；
  // 页面在调用前已按 startChecking 标为进行中。
  it("checks once per click", async () => {
    const stillWaiting = stubFetch(ordersReply(stripePending, "token-9"), confirmReply("confirming"));
    const moves = fakeMoves(startChecking(confirming(stripePending, true)));
    await checkAgain(stripePending, "en", new AbortController().signal, moves.target);
    expect(confirmCalls(stillWaiting).map((call) => call.token)).toEqual(["token-9"]);
    expect(moves.screens).toEqual([confirming(stripePending, true)]);

    stubFetch(ordersReply(stripePending), confirmReply("paid"), ordersReply(paidOrder));
    const paid = fakeMoves();
    await checkAgain(stripePending, "en", new AbortController().signal, paid.target);
    expect(paid.screens).toEqual([ready(paidOrder)]);
  });
});

describe("stripe late refund", () => {
  // UX 0.11「服务端告知本单有来源为会话的 Stripe 退款尝试时……以 [result.stripe_late_refund] 代替 [result.cancelled] 或 [result.cancelled_by_you]」与线框
  // 「[result.stripe_late_refund] ([result.continue])」：不论取消方，其下照旧是 result.continue。
  it.each(LANGUAGES)("replaces both cancelled texts in %s", (language) => {
    for (const order of [timedOut, cancelledByYou]) {
      const html = render(ready({ ...order, stripe_refunded: true }), language);
      expect(has(html, "result.stripe_late_refund", language)).toBe(true);
      expect(has(html, "result.cancelled", language)).toBe(false);
      expect(has(html, "result.cancelled_by_you", language)).toBe(false);
      expect(html).toContain(`href="/products">${COPY["result.continue"][language]}</a>`);
    }
  });
});

describe("paid not cancelled", () => {
  // UX 0.11「从 P06 取消时会话已付款进入的，成功区顶部另显示 [result.paid_not_cancelled]」与 HANDOFF 0.55 第（5）项「只放在请求模块的内存里、读一次即失效」：
  // 首次读到订单时以其订单号取一次，第二次打开不再显示；不取的读取不消耗记录。
  it("takes the notice once with the first read", async () => {
    rememberPaidNotCancelled(ORDER_NUMBER);
    stubFetch(ordersReply(paidOrder));
    const later = fakeMoves();
    await openResult("en", new AbortController().signal, later.target, { takeNotice: false });
    expect(later.notices).toEqual([]);

    stubFetch(ordersReply(paidOrder));
    const first = fakeMoves();
    await openResult("en", new AbortController().signal, first.target, { takeNotice: true });
    expect(first.notices).toEqual([true]);

    stubFetch(ordersReply(paidOrder));
    const again = fakeMoves();
    await openResult("en", new AbortController().signal, again.target, { takeNotice: true });
    expect(again.notices).toEqual([false]);

    rememberPaidNotCancelled("OTHER0ORDER0NO00");
    stubFetch(ordersReply(paidOrder));
    const other = fakeMoves();
    await openResult("en", new AbortController().signal, other.target, { takeNotice: true });
    expect(other.notices).toEqual([false]);
  });

  // 同一条与线框「取消时会话已付款：[result.paid_not_cancelled]（其余同上成功区）」：只在成功时、位于成功区顶部；其余状态不显示。
  it.each(LANGUAGES)("shows the notice only on success in %s", (language) => {
    const html = render(ready(paidOrder), language, 12, true);
    expect(has(html, "result.paid_not_cancelled", language)).toBe(true);
    expect(html.indexOf(escapeHtml(COPY["result.paid_not_cancelled"][language]))).toBeLessThan(html.indexOf(escapeHtml(COPY["result.success_title"][language])));
    expect(has(render(ready(paidOrder), language), "result.paid_not_cancelled", language)).toBe(false);
    for (const screen of [ready(failed), ready(cancelledByYou), confirming(stripePending, true), { status: "not_paid" } as const]) {
      expect(has(render(screen, language, 12, true), "result.paid_not_cancelled", language)).toBe(false);
    }
  });
});

describe("stripe dictionary", () => {
  // 验收「页面文字全部来自字典」：Stripe 的各个画面里，除订单数据外每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides order data in %s", (language) => {
    const screens: [ResultScreen, boolean][] = [
      [confirming(stripePending, false), false],
      [startChecking(confirming(stripePending, true)), false],
      [{ status: "not_paid" }, false],
      [ready({ ...cancelledByYou, stripe_refunded: true }), false],
      [ready(paidOrder), true],
    ];
    const orderData = new Set([ORDER_NUMBER, "Aina Rahman", "+60123456789", "12 Jalan Contoh 3", "47000", "Selangor", countryName("MY", language), "·", ","]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const [screen, notice] of screens) {
      for (const value of visibleTexts(render(screen, language, 12, notice))) {
        const known = orderData.has(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });
});
