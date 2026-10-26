import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CheckoutRegions, PricedQuoteLine, QuoteLine } from "../api/checkout";
import { MEMBER_ORDER_URL, MEMBER_PHONE_URL, memberOrderBody, memberQuoteUrl } from "../api/memberCheckout";
import type { MemberQuote } from "../api/memberCheckout";
import { clearMemberPayOrder, MEMBER_PAY_ORDER_KEY, stashedMemberPayOrder } from "../api/memberPay";
import { createMemberSessionReader, MEMBER_SESSION_URL } from "../api/memberSession";
import { CSRF_HEADER, IDEMPOTENCY_HEADER } from "../api/pay";
import { CART_STORAGE_KEY, readCart } from "../cart";
import type { CartLine, CartStorage } from "../cart";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { RoutePath } from "../router";
import {
  afterMemberQuote,
  applyCoupon,
  changePoints,
  completeMemberOrder,
  couponCode,
  couponInvalid,
  editPhone,
  enterAsMember,
  fillPhone,
  formAfterMemberReply,
  identityOf,
  lapseToGuest,
  memberAfterReply,
  memberForm,
  memberOrderChoice,
  memberPlaceable,
  memberQuoteKey,
  memberQuoteOutcome,
  memberReplyRequotes,
  memberSummary,
  openCheckout,
  pointsCount,
  pointsField,
  prefillPhone,
  renewMember,
  retryNotice,
  settleCsrf,
  settleMemberQuote,
  settleSignedOut,
  submitMemberCheckout,
} from "./CheckoutMember";
import type { CheckoutIdentity, MemberActions, MemberForm, MemberOrderAnswer, MemberSummary, OrderMoves, PhoneMoves } from "./CheckoutMember";
import { canPlaceOrder, CheckoutView, guestRecipient, INITIAL_FORM } from "./CheckoutPage";
import type { CheckoutActions, CheckoutForm, Reference } from "./CheckoutPage";
import { countryNames } from "./checkoutPhone";

afterEach(() => {
  vi.unstubAllGlobals();
  clearMemberPayOrder();
});

// ---- 数据 ----

const TOKEN = "csrf-member";
const NEW_TOKEN = "csrf-member-new";
const OWN_PHONE = "+60123456789";
const ORDER_NUMBER = "AS-20261026-7Q2K9D";

const REGIONS: CheckoutRegions = {
  regions: [
    { code: "GB", calling_code: 44 },
    { code: "MY", calling_code: 60 },
    { code: "SG", calling_code: 65 },
  ],
  my_states: [
    { code: "MY-10", name: "Selangor" },
    { code: "MY-14", name: "Wilayah Persekutuan Kuala Lumpur" },
  ],
  default_phone_region: "MY",
};

const REFERENCE: Reference = { status: "ready", smsEnabled: true, regions: REGIONS };

function text(value: string, english_fallback = false) {
  return { text: value, english_fallback };
}

function priced(sku: string, overrides: Partial<PricedQuoteLine> = {}): PricedQuoteLine {
  return {
    sku,
    quantity: 1,
    status: "ok",
    available_stock: null,
    max_per_order: 10,
    product_slug: "crew-neck-tee",
    name: text("Crew Neck Tee"),
    options: [
      { code: "color", name: text("Colour"), value: { code: "black", name: text("Black") } },
      { code: "size", name: text("Size"), value: { code: "m", name: text("M") } },
    ],
    image: null,
    unit_price_sen: 3900,
    line_subtotal_sen: 3900,
    ...overrides,
  };
}

const CART: CartLine[] = [
  { sku: "tee-black-m", slug: "crew-neck-tee", quantity: 2 },
  { sku: "candle", slug: "candle", quantity: 1 },
];

const ROWS: QuoteLine[] = [
  priced("tee-black-m", { quantity: 2, line_subtotal_sen: 7800 }),
  priced("candle", { product_slug: "candle", name: text("Soy Wax Candle", true), options: [], line_subtotal_sen: 3200 }),
];

// 视觉稿 P05-desktop-3-member 的数：小计 RM 110.00、券 RM 10.00、积分 500（RM 5.00）、运费 RM 8.00、合计 RM 103.00，可用 1250 积分。
function memberQuoteOf(overrides: Partial<MemberQuote> = {}): MemberQuote {
  return {
    lines: ROWS,
    subtotal_sen: 11000,
    shipping: { fee_sen: 800, zone_code: "MY-10", version: 1 },
    total_sen: 10300,
    fx_reference: null,
    can_place_order: true,
    coupon_applied: true,
    coupon_discount_sen: 1000,
    points_available: 1250,
    points_max: 1250,
    points_accepted: true,
    points_redeemed: 500,
    points_earned: 103,
    ...overrides,
  };
}

const NO_DESTINATION = memberQuoteOf({ shipping: null, total_sen: null, can_place_order: false, coupon_applied: null, coupon_discount_sen: 0, points_redeemed: 0 });

function readyMember(quote: MemberQuote = memberQuoteOf(), pending = false, coupon: string | null = "DEMO10", points = 500): MemberSummary {
  return { status: "ready", quote, pending, coupon, points };
}

const MEMBER: MemberForm = { ...memberForm(TOKEN), phone: OWN_PHONE, coupon: "DEMO10", appliedCoupon: "DEMO10", points: "500", pointsMax: 1250, round: 1 };

const RECIPIENT = { name: "Aina Rahman", country: "MY", state: "MY-10", region: "", address: "12 Jalan Contoh 3", postcode: "47000" };

const MEMBER_STEP: CheckoutForm = { ...INITIAL_FORM, step: 3, phoneRegion: "MY", recipient: RECIPIENT };

const noActions: CheckoutActions = {
  onPhoneRegion: () => undefined,
  onPhoneInput: () => undefined,
  onContinue: () => undefined,
  onChangePhone: () => undefined,
  onRecipient: () => undefined,
  onPlaceOrder: () => undefined,
};

const noMemberActions: MemberActions = {
  onPhone: () => undefined,
  onCoupon: () => undefined,
  onApplyCoupon: () => undefined,
  onPoints: () => undefined,
};

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

interface RenderOptions {
  form?: CheckoutForm;
  member?: MemberForm | null;
  quote?: MemberSummary;
  language?: Language;
}

// 会员第 3 步经 CheckoutView 渲染（quote 与 member.quote 为同一个会员计价，同页面的接法）；member 为 null 时为游客。
function render({ form = MEMBER_STEP, member = MEMBER, quote = readyMember(), language = "en" }: RenderOptions = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/checkout">
        <CheckoutView
          lines={CART}
          reference={REFERENCE}
          quote={quote}
          form={form}
          actions={noActions}
          member={member === null ? undefined : { form: member, quote, actions: noMemberActions }}
        />
      </RouterProvider>
    </LanguageProvider>,
  );
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

function count(html: string, value: string): number {
  return html.split(value).length - 1;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// 字典某一条在页面里的样子（HTML 转义后）。
function copy(key: CopyKey, language: Language = "en"): string {
  return escapeHtml(COPY[key][language]);
}

// 带变量的字典条目：变量换成给定的值。
function filled(key: CopyKey, values: Record<string, string>, language: Language = "en"): string {
  let template = COPY[key][language];
  for (const [name, value] of Object.entries(values)) {
    template = template.replace(`{${name}}`, value);
  }
  return escapeHtml(template);
}

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

// 某个 input 的全部属性（react-dom/server 的属性顺序不保证，逐个比较）。
function inputAfterLabel(html: string, key: CopyKey, language: Language = "en"): string {
  const label = new RegExp(`<label class="acs-field__label" for="([^"]+)">${escapeRegExp(copy(key, language))}</label>`).exec(html);
  if (label === null) {
    throw new Error(`label ${key} not found`);
  }
  const input = new RegExp(`<input ([^>]*id="${escapeRegExp(label[1] ?? "")}"[^>]*)/>`).exec(html);
  if (input === null) {
    throw new Error(`input for ${key} not found`);
  }
  return input[1] ?? "";
}

// 摘要里的金额行（标签与金额，按页面顺序）；商品行带 site-checkout__item，不在其中。
function amountRows(html: string): [string, string][] {
  return [...html.matchAll(/<div class="acs-row(?: [^"]*)?"><span>([^<]*)<\/span><span>([^<]*)<\/span><\/div>/g)].map((m) => [m[1] ?? "", m[2] ?? ""]);
}

// ---- fetch 与会话替身 ----

type Reply = Response | "network" | (() => Response);

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
      if (next === "network") {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      return Promise.resolve(typeof next === "function" ? next() : next);
    }),
  );
  return calls;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const memberSession = (token = TOKEN) => json(200, { phone_masked: "+60 12-*** 6789", has_password: false, expires_at: "2026-11-25T00:00:00Z", csrf_token: token });
const guestSession = () => json(401, { detail: "member_session_required" });
const signedOut = () => json(401, { detail: "member_session_required" });
const serverError = () => new Response(null, { status: 500 });

function urls(calls: readonly Call[]): string[] {
  return calls.map((call) => call.url);
}

function headerOf(init: RequestInit, name: string): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.[name];
}

function bodyOf(init: RequestInit): unknown {
  return JSON.parse(typeof init.body === "string" ? init.body : "null") as unknown;
}

// 页面处理替身：按顺序记下每个去向。
function recordMoves() {
  const events: string[] = [];
  const answers: MemberOrderAnswer[] = [];
  const moves: OrderMoves & PhoneMoves = {
    renew: (csrfToken) => {
      events.push(`renew ${csrfToken ?? "kept"}`);
    },
    lapse: () => {
      events.push("lapse");
    },
    fill: (phone) => {
      events.push(`fill ${phone}`);
    },
    placed: (orderNumber) => {
      events.push(`placed ${orderNumber}`);
    },
    answer: (reply) => {
      answers.push(reply);
      events.push(`answer ${reply.kind}`);
    },
  };
  return { moves, events, answers };
}

const GUEST_ONLY_KEYS: readonly CopyKey[] = [
  "checkout.guest_other_country",
  "checkout.guest_sms_off",
  "checkout.guest_notice",
  "checkout.phone_lookup_hint",
  "checkout.phone_change",
  "checkout.coupon_members_only",
  "checkout.points_guest",
];

// ---- 打开与身份 ----

describe("opening and identity", () => {
  // 验收「打开时经 SHOP-TASK-151 的读取对象重读一次会话（不直接用之前读到的状态）」「member 时跳过第 1、2 步，以会员身份进入第 3 步」与 UX P05「已登录会员 | 跳过 | 跳过（不再验证）| 会员表单」：
  // 读取对象之前已读到 guest，打开时仍发一次会话请求（不带页面的 signal），member 时以会话里的令牌进入第 3 步，没有游客提示，已填的收货资料保留。
  it("re-reads the session on opening and enters step 3 as a member", async () => {
    const reader = createMemberSessionReader();
    const calls = stubFetch({ [MEMBER_SESSION_URL]: [guestSession(), memberSession()] });
    await reader.load();
    const opening = await openCheckout(reader, new AbortController().signal);
    expect(opening).toEqual<CheckoutIdentity>({ kind: "member", csrfToken: TOKEN });
    expect(urls(calls)).toEqual([MEMBER_SESSION_URL, MEMBER_SESSION_URL]);
    expect(calls[1]?.init.signal).toBeNull();
    const entered = enterAsMember({ ...INITIAL_FORM, phoneRegion: "MY", guestNotice: "checkout.guest_other_country", recipient: RECIPIENT });
    expect(entered.step).toBe(3);
    expect(entered.guestNotice).toBeNull();
    expect(entered.recipient).toEqual(RECIPIENT);
  });

  // 验收「guest 与 failed 时照现在的游客路径（failed 不另加提示）」与 HANDOFF 0.63 第（3）项「读取失败按游客」：未登录、500、网络中断都为 guest。
  it.each<[string, Reply]>([
    ["no session", guestSession()],
    ["a failed read", serverError()],
    ["a network failure", "network"],
  ])("opens as a guest after %s", async (_name, reply) => {
    stubFetch({ [MEMBER_SESSION_URL]: [reply] });
    await expect(openCheckout(createMemberSessionReader(), new AbortController().signal)).resolves.toEqual<CheckoutIdentity>({ kind: "guest" });
    expect(identityOf({ kind: "unknown" })).toEqual({ kind: "guest" });
  });

  // 同一条：游客照现在的第 1 步（手机号标题与继续按钮），没有会员的任何字段。
  it.each(LANGUAGES)("shows the guest step 1 for a guest in %s", (language) => {
    const html = render({ form: { ...INITIAL_FORM, phoneRegion: "MY" }, member: null, quote: readyMember(NO_DESTINATION), language });
    expect(html).toContain(copy("checkout.phone_step_title", language));
    expect(html).toContain(copy("checkout.phone_continue", language));
    for (const key of ["checkout.member_phone_hint", "checkout.coupon", "checkout.points"] as const) {
      expect(html).not.toContain(copy(key, language));
    }
  });

  // 验收「离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新」：重读期间离开页面时返回 null，会话状态照常更新。
  it("gives no identity once the page has been left", async () => {
    const page = new AbortController();
    const reader = createMemberSessionReader();
    stubFetch({
      [MEMBER_SESSION_URL]: [
        () => {
          page.abort();
          return memberSession();
        },
      ],
    });
    await expect(openCheckout(reader, page.signal)).resolves.toBeNull();
    expect(reader.current().kind).toBe("member");
  });

  // 验收「会员第 3 步不显示 checkout.guest_other_country、checkout.guest_sms_off、checkout.guest_notice、checkout.phone_lookup_hint、checkout.phone_change、checkout.coupon_members_only 与 checkout.points_guest」
  // 与 HANDOFF 0.63「已登录直接进入的第 3 步照 P05-desktop-3-member 与 P05-phone-3-member 去掉已验证横幅」：即使表单里残留了游客提示也不显示，也没有已验证横幅。
  it.each(LANGUAGES)("shows none of the guest-only text on the member step 3 in %s", (language) => {
    const html = render({ form: { ...MEMBER_STEP, guestNotice: "checkout.guest_sms_off" }, language });
    for (const key of GUEST_ONLY_KEYS) {
      expect(html, key).not.toContain(copy(key, language));
    }
    expect(html).not.toContain("acs-alert--success");
    expect(html).not.toContain("site-checkout__guest");
    expect(html).toContain(`<h2 class="acs-display-s">${copy("checkout.recipient_title", language)}</h2>`);
  });
});

// ---- 收货电话 ----

describe("the delivery phone", () => {
  // 验收「会员第 3 步的 checkout.phone 为可编辑输入框，其后为 checkout.member_phone_hint 与 checkout.phone_hint」与 UX P05「会员：[P3 <会员手机号，预填> ______] [checkout.member_phone_hint] [checkout.phone_hint]」：
  // 不是只读，值为电话栏的内容，两句提示依次在其后并由读屏关联到输入框。
  it.each(LANGUAGES)("is an editable field followed by the two hints in %s", (language) => {
    const html = render({ language });
    const input = inputAfterLabel(html, "checkout.phone", language);
    expect(input).toContain(`class="acs-input site-checkout__member-phone"`);
    expect(input).toContain(`type="tel"`);
    expect(input).not.toMatch(/readonly/i);
    expect(input).toContain(`value="${OWN_PHONE}"`);
    const field = part(html, new RegExp(`<label class="acs-field__label" for="[^"]+">${escapeRegExp(copy("checkout.phone", language))}</label><input [^>]*/>(<span class="acs-field__hint" id="[^"]+">[^<]*</span>){2}</div>`));
    const hints = [...field.matchAll(/<span class="acs-field__hint" id="([^"]+)">([^<]*)<\/span>/g)];
    expect(hints.map((m) => m[2])).toEqual([copy("checkout.member_phone_hint", language), copy("checkout.phone_hint", language)]);
    expect(/aria-describedby="([^"]+)"/.exec(input)?.[1]).toBe(hints.map((m) => m[1]).join(" "));
  });

  // 验收「ok 时只在电话栏仍为空时填入（不覆盖会员已输入的内容）」与 HANDOFF 0.63 第（3）项「预填只在电话栏为空时填入」。
  it("fills only an empty field", () => {
    expect(fillPhone(memberForm(TOKEN), OWN_PHONE).phone).toBe(OWN_PHONE);
    expect(fillPhone({ ...memberForm(TOKEN), phone: "  " }, OWN_PHONE).phone).toBe(OWN_PHONE);
    expect(fillPhone({ ...memberForm(TOKEN), phone: "+65 8123 4567" }, OWN_PHONE).phone).toBe("+65 8123 4567");
  });

  // 验收「每次以会员身份进入第 3 步时经 SHOP-TASK-157 取本人号码一次：ok 时…填入」：GET /api/member/phone 带页面的 signal，号码交给 fill。
  it("reads the member's own number once and fills it", async () => {
    const calls = stubFetch({ [MEMBER_PHONE_URL]: [json(200, { phone: OWN_PHONE })] });
    const { moves, events } = recordMoves();
    const page = new AbortController();
    await prefillPhone(createMemberSessionReader(), page.signal, moves);
    expect(events).toEqual([`fill ${OWN_PHONE}`]);
    expect(urls(calls)).toEqual([MEMBER_PHONE_URL]);
    expect(calls[0]?.init.signal).toBe(page.signal);
  });

  // 验收「failed 与 network 时留空、不另加提示、不以遮掩号码预填（Kelvin 2026-10-26 决定）」与 DESIGN 1.14 第 8 条「其他错误或网络中断时收货电话留空由会员填写」：
  // 不调用任何处理，不读会话（遮掩号码只在会话里），电话栏仍为空。
  it.each<[string, Reply]>([
    ["failed", serverError()],
    ["a bad body", json(200, { phone: "60123456789" })],
    ["network", "network"],
  ])("leaves the field empty after %s", async (_name, reply) => {
    const calls = stubFetch({ [MEMBER_PHONE_URL]: [reply] });
    const { moves, events } = recordMoves();
    await prefillPhone(createMemberSessionReader(), new AbortController().signal, moves);
    expect(events).toEqual([]);
    expect(urls(calls)).toEqual([MEMBER_PHONE_URL]);
    expect(inputAfterLabel(render({ member: memberForm(TOKEN) }), "checkout.phone")).toContain(`value=""`);
  });

  // 验收「signed_out 时按上一条的会话失效处理」「会员会话失效（重读得到 guest 或 failed）时回到第 1 步按游客路径继续，已填的收货资料保留」与 DESIGN 1.14 第 8 条「取号回答 401 时按会员会话失效处理（页头回到未登录、结账回到第 1 步，已填的收货资料保留）」：
  // 重读会话得到 guest 或 failed 时 lapse；共享状态随之不再是 member（页头回到未登录）。
  it.each<[string, Reply, string]>([
    ["guest", guestSession(), "guest"],
    ["failed", serverError(), "failed"],
  ])("lapses to the guest path when the number read is signed out and the session is %s", async (_name, session, kind) => {
    const reader = createMemberSessionReader();
    stubFetch({ [MEMBER_SESSION_URL]: [memberSession(), session], [MEMBER_PHONE_URL]: [signedOut()] });
    await reader.load();
    const { moves, events } = recordMoves();
    await prefillPhone(reader, new AbortController().signal, moves);
    expect(events).toEqual(["lapse"]);
    expect(reader.current().kind).toBe(kind);
  });

  // 同一条「会员改填的收货电话不带回第 1 步，第 1 步手机号栏照游客路径为空」：回到第 1 步，收货资料原样保留，手机号栏为空，没有游客提示与进行中的提交。
  it("goes back to step 1 keeping the shipping details but not the phone", () => {
    const lapsed = lapseToGuest({ ...MEMBER_STEP, phoneInput: "+60 12-345 6789", submitting: true, checking: true, notice: "error", attempt: { key: "k", request: "{}" } });
    expect(lapsed).toEqual({ ...MEMBER_STEP, step: 1, phoneInput: "", attempt: null });
    const html = render({ form: lapsed, member: null, quote: readyMember(NO_DESTINATION) });
    expect(html).toContain(copy("checkout.phone_step_title"));
    expect(html).toMatch(/<input class="acs-input" id="[^"]+" type="tel"[^>]*value=""/);
    expect(html).not.toContain(OWN_PHONE);
  });

  // 验收「会员请求回 401 时重读会话——得到 member（例如另一标签页已重新登录）时留在会员第 3 步、换用新令牌并显示 common.error_retry」：取号回 401 而会话仍为会员时 renew 带新令牌。
  it("stays a member with the new token when the session is still a member", async () => {
    const reader = createMemberSessionReader();
    stubFetch({ [MEMBER_SESSION_URL]: [memberSession(NEW_TOKEN)], [MEMBER_PHONE_URL]: [signedOut()] });
    const { moves, events } = recordMoves();
    await prefillPhone(reader, new AbortController().signal, moves);
    expect(events).toEqual([`renew ${NEW_TOKEN}`]);
    expect(renewMember(MEMBER, NEW_TOKEN)).toEqual({ ...MEMBER, csrfToken: NEW_TOKEN });
    const retried = retryNotice({ ...MEMBER_STEP, submitting: true, checking: true });
    expect(retried).toEqual({ ...MEMBER_STEP, notice: "error" });
    expect(count(render({ form: retried }), `<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.error_retry")}</p>`)).toBe(2);
  });

  // 验收「离开页面时中止请求，之后不再更新」：取号回来时或重读会话期间已离开，不调用任何处理。
  it("does nothing once the page has been left", async () => {
    const page = new AbortController();
    stubFetch({
      [MEMBER_PHONE_URL]: [
        () => {
          page.abort();
          return json(200, { phone: OWN_PHONE });
        },
      ],
    });
    const first = recordMoves();
    await prefillPhone(createMemberSessionReader(), page.signal, first.moves);
    expect(first.events).toEqual([]);

    const later = new AbortController();
    stubFetch({
      [MEMBER_PHONE_URL]: [signedOut()],
      [MEMBER_SESSION_URL]: [
        () => {
          later.abort();
          return guestSession();
        },
      ],
    });
    const second = recordMoves();
    await prefillPhone(createMemberSessionReader(), later.signal, second.moves);
    expect(second.events).toEqual([]);
  });

  // 验收「下单回 phone_invalid 时停在第 3 步，在电话栏下显示 checkout.phone_invalid」与视觉稿 P05-states 的字段错误：字段标为错误，提示在两句说明之后并关联到输入框；改电话后提示收起。
  it.each(LANGUAGES)("stays on step 3 with checkout.phone_invalid under the phone in %s", (language) => {
    const reply: MemberOrderAnswer = { kind: "phone_invalid" };
    const form = formAfterMemberReply({ ...MEMBER_STEP, submitting: true, checking: true }, reply);
    const member = memberAfterReply(MEMBER, reply);
    expect(form.step).toBe(3);
    expect(form.notice).toBeNull();
    expect(member.phoneInvalid).toBe(true);
    const html = render({ form, member, language });
    const input = inputAfterLabel(html, "checkout.phone", language);
    expect(input).toContain(`aria-invalid="true"`);
    const errorId = /<span class="acs-field__error" id="([^"]+)"><svg[^>]*aria-hidden="true">[\s\S]*?<\/svg>([^<]*)<\/span>/.exec(html);
    expect(errorId?.[2]).toBe(copy("checkout.phone_invalid", language));
    expect(input).toContain(`aria-describedby="`);
    expect(/aria-describedby="([^"]+)"/.exec(input)?.[1]?.split(" ")).toContain(errorId?.[1]);
    expect(html).toContain(`<div class="acs-field acs-field--invalid site-checkout__wide">`);
    expect(html.indexOf(copy("checkout.phone_hint", language))).toBeLessThan(html.indexOf(copy("checkout.phone_invalid", language)));
    expect(editPhone(member, "+60 12-000 0000").phoneInvalid).toBe(false);
  });
});

// ---- 优惠券 ----

describe("the coupon", () => {
  // 验收「点按钮时以去掉首尾空白的代码（为空即不用券）重新计价，输入框改动本身不重新计价」与 HANDOFF 0.63「点 checkout.coupon_apply 才带券重新计价」「券代码去空白后为空不用券」：
  // 改输入框不改计价标识；点按钮后标识变化、用的是去空白的代码；空白代码为不用券；同一代码再点一次也重新计价。
  it("re-quotes with the trimmed code only when Apply is pressed", () => {
    const base = "en|lines|MY|MY-10|0";
    const start = memberForm(TOKEN);
    const typed = { ...start, coupon: "  DEMO10 " };
    expect(memberQuoteKey(base, typed)).toBe(memberQuoteKey(base, start));
    const applied = applyCoupon(typed);
    expect(applied.appliedCoupon).toBe("DEMO10");
    expect(memberQuoteKey(base, applied)).not.toBe(memberQuoteKey(base, typed));
    expect(memberQuoteKey(base, applyCoupon(applied))).not.toBe(memberQuoteKey(base, applied));
    expect(applyCoupon({ ...applied, coupon: "   " }).appliedCoupon).toBeNull();
    expect(couponCode(" a b ")).toBe("a b");
    expect(couponCode("")).toBeNull();
    expect(memberQuoteKey(null, applied)).toBeNull();
  });

  // 视觉稿 P05-desktop-3-member：checkout.coupon 标签、输入框与 checkout.coupon_apply 次要按钮在同一行。
  it.each(LANGUAGES)("shows the coupon field with the Apply button in %s", (language) => {
    const html = render({ language });
    expect(inputAfterLabel(html, "checkout.coupon", language)).toContain(`value="DEMO10"`);
    expect(html).toMatch(new RegExp(`<div class="site-checkout__coupon"><input [^>]*/><button class="acs-btn acs-btn--secondary" type="button">${escapeRegExp(copy("checkout.coupon_apply", language))}</button></div>`));
  });

  // 验收「计价的 coupon_applied 为假时在输入框下显示 checkout.coupon_invalid」与视觉稿 P05-states：字段标为错误，提示在券输入框之下、积分之前并关联到输入框；
  // coupon_applied 为真或为 null（未给代码）时不显示。
  it.each(LANGUAGES)("shows checkout.coupon_invalid when the quote does not apply the coupon in %s", (language) => {
    const quote = readyMember(memberQuoteOf({ coupon_applied: false, coupon_discount_sen: 0 }));
    expect(couponInvalid(MEMBER, quote)).toBe(true);
    const html = render({ quote, language });
    const input = inputAfterLabel(html, "checkout.coupon", language);
    const error = new RegExp(`<span class="acs-field__error" id="([^"]+)"><svg[^>]*aria-hidden="true">[\\s\\S]*?</svg>${escapeRegExp(copy("checkout.coupon_invalid", language))}</span>`).exec(html);
    expect(error).not.toBeNull();
    expect(input).toContain(`aria-invalid="true"`);
    expect(input).toContain(`aria-describedby="${error?.[1] ?? ""}"`);
    expect(html.indexOf(copy("checkout.coupon_invalid", language))).toBeLessThan(html.indexOf(copy("checkout.points", language)));
    for (const applied of [true, null]) {
      const other = readyMember(memberQuoteOf({ coupon_applied: applied }));
      expect(couponInvalid(MEMBER, other)).toBe(false);
      expect(render({ quote: other, language })).not.toContain(copy("checkout.coupon_invalid", language));
    }
  });

  // 验收「下单回 coupon_invalid 时重新计价并显示 checkout.coupon_invalid（…在下一次计价返回时清除，之后是否显示只看该次计价的 coupon_applied）」与 HANDOFF 0.63 同一句：
  // 回答后停在第 3 步、会员轮次加一（重新计价），计价返回前即显示；返回后提示清除，coupon_applied 为真时不再显示。
  it("re-quotes and shows checkout.coupon_invalid after coupon_invalid", () => {
    const reply: MemberOrderAnswer = { kind: "coupon_invalid" };
    const member = memberAfterReply(MEMBER, reply);
    expect(member.round).toBe(MEMBER.round + 1);
    expect(member.orderNotice).toBe("coupon_invalid");
    expect(memberQuoteKey("base", member)).not.toBe(memberQuoteKey("base", MEMBER));
    expect(formAfterMemberReply({ ...MEMBER_STEP, submitting: true }, reply)).toEqual(MEMBER_STEP);
    expect(memberReplyRequotes(reply)).toBe(false);
    const waiting = readyMember(memberQuoteOf(), true);
    expect(render({ member, quote: waiting })).toContain(copy("checkout.coupon_invalid"));
    const cleared = afterMemberQuote(member, memberQuoteOf());
    expect(cleared.orderNotice).toBeNull();
    expect(render({ member: cleared })).not.toContain(copy("checkout.coupon_invalid"));
    expect(couponInvalid(afterMemberQuote(member, null), { status: "error" })).toBe(false);
  });
});

// ---- 积分 ----

describe("points", () => {
  // 验收「checkout.points 输入框只收整数（非数字字符去掉），超过最近一次计价的 points_max 时改成 points_max」与 Kelvin 2026-10-26 决定（HANDOFF 0.63「只收 0 到本单上限的整数，超出的改成上限」）。
  it("keeps only digits and caps the number at points_max", () => {
    expect(pointsField("5a0-0", 1250)).toBe("500");
    expect(pointsField("-12.5", 1250)).toBe("125");
    expect(pointsField("abc", 1250)).toBe("");
    expect(pointsField("0042", 1250)).toBe("42");
    expect(pointsField("1251", 1250)).toBe("1250");
    expect(pointsField("99999999999999999999", 300)).toBe("300");
    expect(pointsField("7", 0)).toBe("0");
    expect(pointsCount("")).toBe(0);
    expect(changePoints(MEMBER, "2000").points).toBe("1250");
  });

  // 验收「每次改变后带新积分数重新计价」：积分数进计价标识，空栏按 0。
  it("re-quotes with the new number of points", () => {
    expect(memberQuoteKey("base", { ...MEMBER, points: "400" })).not.toBe(memberQuoteKey("base", MEMBER));
    expect(memberQuoteKey("base", { ...MEMBER, points: "" })).toBe(memberQuoteKey("base", { ...MEMBER, points: "0" }));
  });

  // 验收「首次计价返回前积分栏不可输入」：没有 points_max 时不接受改动、输入框禁用、不显示可用积分；计价返回后可输入，并显示 checkout.points_available（{points} 为 points_available）。
  it.each(LANGUAGES)("cannot be typed before the first quote returns in %s", (language) => {
    const fresh = { ...memberForm(TOKEN), phone: OWN_PHONE };
    expect(changePoints(fresh, "100")).toBe(fresh);
    const before = render({ member: fresh, quote: { status: "loading" }, language });
    expect(inputAfterLabel(before, "checkout.points", language)).toContain(`disabled=""`);
    expect(before).not.toContain(filled("checkout.points_available", { points: "1250" }, language));
    const after = render({ member: afterMemberQuote(fresh, memberQuoteOf()), language });
    expect(inputAfterLabel(after, "checkout.points", language)).not.toContain("disabled");
    const hint = new RegExp(`<span class="acs-field__hint" id="([^"]+)">${escapeRegExp(filled("checkout.points_available", { points: "1250" }, language))}</span>`).exec(after);
    expect(hint).not.toBeNull();
    expect(inputAfterLabel(after, "checkout.points", language)).toContain(`aria-describedby="${hint?.[1] ?? ""}"`);
  });

  // 验收「每次计价返回后积分栏大于该次 points_max 时（例如应用券后上限变小）改成该 points_max 并再计价一次」：积分栏改成上限，标识随之改变；不超过时不变。
  it("caps the field after a quote with a smaller points_max and quotes again", () => {
    const capped = afterMemberQuote(MEMBER, memberQuoteOf({ points_max: 300, points_accepted: false, points_redeemed: 0 }));
    expect(capped.points).toBe("300");
    expect(capped.pointsMax).toBe(300);
    expect(memberQuoteKey("base", capped)).not.toBe(memberQuoteKey("base", MEMBER));
    const same = afterMemberQuote(MEMBER, memberQuoteOf());
    expect(same.points).toBe("500");
    expect(memberQuoteKey("base", same)).toBe(memberQuoteKey("base", MEMBER));
  });

  // 验收「checkout.points_available…与 checkout.points_not_shipping」与 UX P05「[checkout.points] [M4 ______] [checkout.points_available] [checkout.points_not_shipping]」：积分之后是不抵运费的说明。
  it.each(LANGUAGES)("explains that coupons and points do not reduce shipping in %s", (language) => {
    const html = render({ language });
    expect(html).toContain(`<p class="acs-body-s acs-muted">${copy("checkout.points_not_shipping", language)}</p></div>`);
    expect(html.indexOf(copy("checkout.points", language))).toBeLessThan(html.indexOf(copy("checkout.points_not_shipping", language)));
  });

  // 验收「回 points_unavailable 时重新计价并显示 common.error_retry（…在下一次计价返回时清除）」与 Kelvin 2026-10-26 决定「下单回 409 points_unavailable 时重新计价并显示 common.error_retry」：
  // 回答后会员轮次加一，积分栏下显示 common.error_retry，计价返回后清除。
  it.each(LANGUAGES)("re-quotes and shows common.error_retry after points_unavailable in %s", (language) => {
    const reply: MemberOrderAnswer = { kind: "points_unavailable" };
    const member = memberAfterReply(MEMBER, reply);
    expect(member.round).toBe(MEMBER.round + 1);
    const html = render({ member, form: formAfterMemberReply(MEMBER_STEP, reply), language });
    const points = part(html, /<div class="acs-field"><label class="acs-field__label" for="[^"]+">[^<]*<\/label><input class="acs-input acs-num site-checkout__points"[\s\S]*?<\/div>/);
    expect(points).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy("common.error_retry", language)}</p>`);
    expect(count(html, copy("common.error_retry", language))).toBe(1);
    expect(render({ member: afterMemberQuote(member, memberQuoteOf()), language })).not.toContain(copy("common.error_retry", language));
  });
});

// ---- 摘要 ----

describe("the summary", () => {
  // 验收「选定收货国家之后，桌面摘要与手机折叠明细依次为 cart.subtotal、checkout.summary_coupon、checkout.summary_points、checkout.summary_shipping 与 checkout.summary_total」与 UX P05 M2–M6：
  // 桌面摘要、手机顶部折叠明细与手机第 3 步的金额各按这个顺序，金额为接口给的数（积分 500 按仙格式化为 RM 5.00），不加负号。
  it.each(LANGUAGES)("lists the five amounts in order after a country is chosen in %s", (language) => {
    const html = render({ language });
    const expected: [string, string][] = [
      [copy("cart.subtotal", language), "RM 110.00"],
      [copy("checkout.summary_coupon", language), "RM 10.00"],
      [copy("checkout.summary_points", language), "RM 5.00"],
      [copy("checkout.summary_shipping", language), "RM 8.00"],
      [copy("checkout.summary_total", language), "RM 103.00"],
    ];
    expect(amountRows(part(html, /<aside[\s\S]*?<\/aside>/))).toEqual(expected);
    expect(amountRows(part(html, /<details[\s\S]*?<\/details>/))).toEqual(expected);
    expect(amountRows(part(html, /<div class="acs-summary site-phone-only site-checkout__amounts">[\s\S]*?<\/div><\/div>/))).toEqual(expected);
    expect(html).not.toContain("-RM");
    expect(html).not.toContain("−");
  });

  // 验收「两行为 0 时也显示，金额原样格式化、不加负号，不在浏览器计算」与 HANDOFF「券与积分两行…即使为 0 也显示」：为 0 时显示 RM 0.00；彼此不符的数照接口显示。
  it("shows zero rows and the amounts exactly as returned", () => {
    const zero = render({ quote: readyMember(memberQuoteOf({ coupon_discount_sen: 0, points_redeemed: 0, total_sen: 11800 }), false, null, 0), member: { ...MEMBER, appliedCoupon: null, points: "" } });
    const rows = amountRows(part(zero, /<aside[\s\S]*?<\/aside>/));
    expect(rows).toContainEqual([copy("checkout.summary_coupon"), "RM 0.00"]);
    expect(rows).toContainEqual([copy("checkout.summary_points"), "RM 0.00"]);
    const odd = render({ quote: readyMember(memberQuoteOf({ coupon_discount_sen: 123, points_redeemed: 4567, total_sen: 99999 })) });
    expect(amountRows(part(odd, /<aside[\s\S]*?<\/aside>/))).toEqual([
      [copy("cart.subtotal"), "RM 110.00"],
      [copy("checkout.summary_coupon"), "RM 1.23"],
      [copy("checkout.summary_points"), "RM 45.67"],
      [copy("checkout.summary_shipping"), "RM 8.00"],
      [copy("checkout.summary_total"), "RM 999.99"],
    ]);
  });

  // 验收「之前与游客相同」：选定收货国家之前只有小计与 cart.shipping_later，没有券与积分两行。
  it.each(LANGUAGES)("is the same as for guests before a country is chosen in %s", (language) => {
    const html = render({ form: { ...MEMBER_STEP, recipient: { ...RECIPIENT, country: "", state: "" } }, quote: readyMember(NO_DESTINATION), language });
    const aside = part(html, /<aside[\s\S]*?<\/aside>/);
    expect(amountRows(aside)).toEqual([[copy("cart.subtotal", language), "RM 110.00"]]);
    expect(aside).toContain(copy("cart.shipping_later", language));
    expect(html).not.toContain(copy("checkout.summary_coupon", language));
    expect(html).not.toContain(copy("checkout.summary_points", language));
  });

  // 验收「游客仍不显示两行」与 UX 0.4「游客结账…不显示 [checkout.summary_coupon]、[checkout.summary_points] 两行」：同一计价交给游客的 View（不给 member）时没有这两行。
  it.each(LANGUAGES)("still shows no coupon and points rows to guests in %s", (language) => {
    const html = render({ member: null, form: { ...MEMBER_STEP, guestNotice: "checkout.guest_other_country" }, language });
    expect(html).not.toContain(copy("checkout.summary_coupon", language));
    expect(html).not.toContain(copy("checkout.summary_points", language));
    expect(html).toContain(copy("checkout.coupon_members_only", language));
  });

  // 验收「会员计价经 SHOP-TASK-157（照现有 latestRuns 写法，只认最后一次请求的结果；计价键另含所用券代码与积分数）」：结果属于当前标识时直接采用并带所用的券与积分，
  // 否则显示上一次成功的结果并标为等待中；401 时按失败显示并要求重读会话。
  it("uses only the quote for the current request", () => {
    const quote = memberQuoteOf();
    const settled = settleMemberQuote(null, "k1", "DEMO10", 500, { status: "ready", quote });
    expect(memberSummary("k1", settled)).toEqual(readyMember(quote));
    expect(memberSummary("k2", settled)).toEqual(readyMember(quote, true));
    expect(memberSummary("k1", null)).toEqual({ status: "loading" });
    const failed = settleMemberQuote(settled, "k2", null, 0, { status: "error" });
    expect(memberSummary("k2", failed)).toEqual({ status: "error" });
    expect(failed.last).toBe(quote);
    expect(memberQuoteOutcome({ status: "ready", quote: { kind: "ok", quote } })).toEqual({ outcome: { status: "ready", quote }, signedOut: false });
    expect(memberQuoteOutcome({ status: "ready", quote: { kind: "signed_out" } })).toEqual({ outcome: { status: "error" }, signedOut: true });
    for (const kind of ["failed", "network"] as const) {
      expect(memberQuoteOutcome({ status: "ready", quote: { kind } })).toEqual({ outcome: { status: "error" }, signedOut: false });
    }
    expect(memberQuoteOutcome({ status: "error" })).toEqual({ outcome: { status: "error" }, signedOut: false });
  });

  // 验收「计价键另含所用券代码与积分数」与 api/memberCheckout.ts 的请求体：会员计价的地址带语言，请求体带最近一次点应用的券与积分数（为同一模块的函数，此处只核对地址）。
  it("quotes members through the member endpoint", () => {
    expect(memberQuoteUrl("zh")).toBe("/api/member/checkout/quote?lang=zh");
  });
});

// ---- 下单 ----

describe("placing the order", () => {
  // 验收「下单只带最近一次计价里 coupon_applied 为真的代码与 points_accepted 为真的积分数（运营者落实写法，HANDOFF 0.63）」：
  // 两者都接受时带上；券不适用（false）或未给（null）时不带券；积分不接受时为 0；没有计价时都不带。
  it("takes only the coupon and points that the latest quote accepted", () => {
    expect(memberOrderChoice(readyMember())).toEqual({ coupon: "DEMO10", points: 500 });
    expect(memberOrderChoice(readyMember(memberQuoteOf({ coupon_applied: false })))).toEqual({ coupon: null, points: 500 });
    expect(memberOrderChoice(readyMember(memberQuoteOf({ coupon_applied: null }), false, null))).toEqual({ coupon: null, points: 500 });
    expect(memberOrderChoice(readyMember(memberQuoteOf({ points_accepted: false, points_redeemed: 0 })))).toEqual({ coupon: "DEMO10", points: 0 });
    expect(memberOrderChoice({ status: "error" })).toEqual({ coupon: null, points: 0 });
    expect(memberOrderChoice({ status: "loading" })).toEqual({ coupon: null, points: 0 });
  });

  // 同一条与 api/memberCheckout.ts 的 memberOrderBody：请求体是会员填写的电话原文、收货资料（马来西亚只带州属）、取舍后的券与积分，不带任何金额。
  it("builds the order body from the form and the accepted choice", () => {
    const choice = memberOrderChoice(readyMember());
    const body = memberOrderBody(CART, MEMBER.phone, guestRecipient(RECIPIENT), choice.coupon, choice.points);
    expect(body).toEqual({
      lines: [
        { sku: "tee-black-m", quantity: 2 },
        { sku: "candle", quantity: 1 },
      ],
      phone: OWN_PHONE,
      name: "Aina Rahman",
      address: "12 Jalan Contoh 3",
      postal_code: "47000",
      country_code: "MY",
      state_code: "MY-10",
      coupon_code: "DEMO10",
      points: 500,
    });
    const without = memberOrderChoice(readyMember(memberQuoteOf({ coupon_applied: false, points_accepted: false })));
    const plain = memberOrderBody(CART, MEMBER.phone, guestRecipient(RECIPIENT), without.coupon, without.points);
    expect(Object.keys(plain)).not.toContain("coupon_code");
    expect(plain.points).toBe(0);
    expect(JSON.stringify(body)).not.toMatch(/_sen|total|shipping|amount|price/);
  });

  // 验收「可下单的条件照 canPlaceOrder 另要求电话栏非空且最近一次计价对应最近一次点 checkout.coupon_apply 时的券代码与积分栏当前的积分数
  // （券输入框改了而没有点应用时，下单仍带最近一次计价接受的券，不阻止下单）」。
  it("requires a phone and a quote for the applied coupon and the current points", () => {
    expect(memberPlaceable(MEMBER, readyMember())).toBe(true);
    expect(canPlaceOrder(MEMBER_STEP, readyMember(), CART)).toBe(true);
    expect(memberPlaceable({ ...MEMBER, phone: "  " }, readyMember())).toBe(false);
    expect(memberPlaceable(MEMBER, readyMember(memberQuoteOf(), true))).toBe(false);
    expect(memberPlaceable(applyCoupon({ ...MEMBER, coupon: "OTHER" }), readyMember())).toBe(false);
    expect(memberPlaceable({ ...MEMBER, points: "400" }, readyMember())).toBe(false);
    expect(memberPlaceable(MEMBER, { status: "loading" })).toBe(false);
    const typedOnly = { ...MEMBER, coupon: "OTHER" };
    expect(memberPlaceable(typedOnly, readyMember())).toBe(true);
    expect(memberOrderChoice(readyMember()).coupon).toBe("DEMO10");
    const button = `<button class="acs-btn acs-btn--primary acs-btn--lg acs-btn--block" type="button">${copy("checkout.place_order")}</button>`;
    expect(count(render({ member: typedOnly }), button)).toBe(2);
    expect(count(render({ member: { ...MEMBER, phone: "" } }), `disabled="">${copy("checkout.place_order")}</button>`)).toBe(2);
  });

  // 验收「下单以会员会话里的 CSRF 令牌经 SHOP-TASK-157 的会员版重试提交——placed 时清空购物车、经 SHOP-TASK-158 写入当前订单暂存（订单号取自下单回答）再进入 /pay」
  // 与 DESIGN 1.14 第 7 条「写入：会员下单成功进入支付页时」、「失败、并发与重试」第 1 条：请求头带令牌与幂等键，网络中断后以同一键与同一请求体重试；
  // 回答后清空购物车、暂存里只有该订单号，再进入 /pay。
  it("writes the stash and goes to /pay after the order is placed", async () => {
    const session = { setItem: vi.fn(), getItem: vi.fn(() => null), removeItem: vi.fn() };
    vi.stubGlobal("sessionStorage", session);
    const calls = stubFetch({ [MEMBER_ORDER_URL]: ["network", json(201, { order_number: ORDER_NUMBER, points_earned: 103 })] });
    const body = memberOrderBody(CART, OWN_PHONE, guestRecipient(RECIPIENT), "DEMO10", 500);
    const { moves, events } = recordMoves();
    const checking = vi.fn();
    await submitMemberCheckout(body, "key-0001", TOKEN, createMemberSessionReader(), { signal: new AbortController().signal, delay: () => Promise.resolve(), onChecking: checking }, moves);
    expect(events).toEqual([`placed ${ORDER_NUMBER}`]);
    expect(checking).toHaveBeenCalledTimes(1);
    expect(urls(calls)).toEqual([MEMBER_ORDER_URL, MEMBER_ORDER_URL]);
    for (const call of calls) {
      expect(headerOf(call.init, CSRF_HEADER)).toBe(TOKEN);
      expect(headerOf(call.init, IDEMPOTENCY_HEADER)).toBe("key-0001");
      expect(bodyOf(call.init)).toEqual(body);
    }

    const data: Record<string, string> = { [CART_STORAGE_KEY]: JSON.stringify(CART) };
    const storage: CartStorage = { getItem: (key) => data[key] ?? null, setItem: (key, value) => void (data[key] = value) };
    const visited: RoutePath[] = [];
    completeMemberOrder(storage, ORDER_NUMBER, (path) => {
      expect(stashedMemberPayOrder()).toBe(ORDER_NUMBER);
      visited.push(path);
    });
    expect(readCart(storage)).toEqual([]);
    expect(session.setItem.mock.calls).toEqual([[MEMBER_PAY_ORDER_KEY, ORDER_NUMBER]]);
    expect(visited).toEqual(["/pay"]);
  });

  // 验收「not_placeable、conflict、unavailable 与 failed 的处理同游客」：cart.item_changed 并重新计价、换新幂等键并 common.error_retry、common.service_unavailable、common.error_retry，都停在第 3 步、保留已填内容。
  it.each<[string, Reply, MemberOrderAnswer]>([
    ["order_not_placeable", json(409, { detail: "order_not_placeable" }), { kind: "not_placeable" }],
    ["idempotency_conflict", json(409, { detail: "idempotency_conflict" }), { kind: "conflict" }],
    ["503", json(503, { detail: "service_unavailable" }), { kind: "unavailable" }],
    ["500", serverError(), { kind: "failed" }],
  ])("answers %s like the guest path", async (_name, reply, expected) => {
    stubFetch({ [MEMBER_ORDER_URL]: [reply] });
    const { moves, answers } = recordMoves();
    await submitMemberCheckout(memberOrderBody(CART, OWN_PHONE, guestRecipient(RECIPIENT), null, 0), "key", TOKEN, createMemberSessionReader(), { signal: new AbortController().signal }, moves);
    expect(answers).toEqual([expected]);
    const form = { ...MEMBER_STEP, submitting: true, checking: true, attempt: { key: "key", request: "{}" } };
    const next = formAfterMemberReply(form, expected);
    expect(next.step).toBe(3);
    expect(next.recipient).toEqual(RECIPIENT);
    expect(next.submitting).toBe(false);
    expect(memberAfterReply(MEMBER, expected)).toBe(MEMBER);
    const notice = { not_placeable: "changed", conflict: "error", unavailable: "unavailable", failed: "error" } as const;
    expect(next.notice).toBe(notice[expected.kind as keyof typeof notice]);
    expect(next.attempt).toEqual(expected.kind === "conflict" ? null : form.attempt);
    expect(memberReplyRequotes(expected)).toBe(expected.kind === "not_placeable");
  });

  // 验收「下单回 coupon_invalid…回 points_unavailable…」与「phone_invalid 时停在第 3 步」：这三种 409/422 交给表单处理，不重读会话。
  it.each<[string, Reply, MemberOrderAnswer["kind"]]>([
    ["coupon_invalid", json(409, { detail: "coupon_invalid" }), "coupon_invalid"],
    ["points_unavailable", json(409, { detail: "points_unavailable" }), "points_unavailable"],
    ["phone_invalid", json(422, { detail: [{ type: "phone_invalid", loc: ["body", "phone"] }] }), "phone_invalid"],
  ])("hands %s to the form", async (_name, reply, kind) => {
    const calls = stubFetch({ [MEMBER_ORDER_URL]: [reply] });
    const { moves, events } = recordMoves();
    await submitMemberCheckout(memberOrderBody(CART, OWN_PHONE, guestRecipient(RECIPIENT), "DEMO10", 500), "key", TOKEN, createMemberSessionReader(), { signal: new AbortController().signal }, moves);
    expect(events).toEqual([`answer ${kind}`]);
    expect(urls(calls)).toEqual([MEMBER_ORDER_URL]);
  });

  // 验收「signed_out 按会话失效处理」与「会员请求回 401 时重读会话——得到 member…换用新令牌并显示 common.error_retry，会员会话失效（重读得到 guest 或 failed）时回到第 1 步」。
  it.each<[string, Reply, string]>([
    ["member", memberSession(NEW_TOKEN), `renew ${NEW_TOKEN}`],
    ["guest", guestSession(), "lapse"],
    ["failed", serverError(), "lapse"],
    ["network", "network", "lapse"],
  ])("re-reads the session after a 401 and the session is %s", async (_name, session, expected) => {
    const calls = stubFetch({ [MEMBER_ORDER_URL]: [signedOut()], [MEMBER_SESSION_URL]: [session] });
    const { moves, events } = recordMoves();
    await submitMemberCheckout(memberOrderBody(CART, OWN_PHONE, guestRecipient(RECIPIENT), null, 0), "key", TOKEN, createMemberSessionReader(), { signal: new AbortController().signal }, moves);
    expect(events).toEqual([expected]);
    expect(urls(calls)).toEqual([MEMBER_ORDER_URL, MEMBER_SESSION_URL]);
  });

  // 验收「csrf 时重读会话——member 时换用新令牌并显示 common.error_retry，guest 时按会话失效处理，重读得到 failed 时保留原令牌并显示 common.error_retry」。
  it.each<[string, Reply, string]>([
    ["member", memberSession(NEW_TOKEN), `renew ${NEW_TOKEN}`],
    ["guest", guestSession(), "lapse"],
    ["failed", serverError(), "renew kept"],
    ["network", "network", "renew kept"],
  ])("re-reads the session after csrf_failed and the session is %s", async (_name, session, expected) => {
    stubFetch({ [MEMBER_ORDER_URL]: [json(403, { detail: "csrf_failed" })], [MEMBER_SESSION_URL]: [session] });
    const { moves, events } = recordMoves();
    await submitMemberCheckout(memberOrderBody(CART, OWN_PHONE, guestRecipient(RECIPIENT), null, 0), "key", TOKEN, createMemberSessionReader(), { signal: new AbortController().signal }, moves);
    expect(events).toEqual([expected]);
  });

  // 验收「会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转」：下单回来时或重读会话期间已离开页面，不调用任何处理；单独调用两个处理函数时同样。
  it("does nothing once the page has been left", async () => {
    const page = new AbortController();
    stubFetch({
      [MEMBER_ORDER_URL]: [
        () => {
          page.abort();
          return json(201, { order_number: ORDER_NUMBER, points_earned: 0 });
        },
      ],
    });
    const first = recordMoves();
    await submitMemberCheckout(memberOrderBody(CART, OWN_PHONE, guestRecipient(RECIPIENT), null, 0), "key", TOKEN, createMemberSessionReader(), { signal: page.signal }, first.moves);
    expect(first.events).toEqual([]);

    for (const settle of [settleSignedOut, settleCsrf]) {
      const left = new AbortController();
      const reader = createMemberSessionReader();
      stubFetch({
        [MEMBER_SESSION_URL]: [
          () => {
            left.abort();
            return guestSession();
          },
        ],
      });
      const moves = recordMoves();
      await settle(reader, left.signal, moves.moves);
      expect(moves.events).toEqual([]);
      expect(reader.current().kind).toBe("guest");
    }
  });
});

// ---- 资料只在内存 ----

describe("browser storage and addresses", () => {
  // DESIGN 1.14 第 8 条「前台只在结账页的页面内存里用它预填，不写入任何浏览器存储、地址、查询参数或历史状态」与第 7 条「只存这一个值」：
  // 从取号、下单到成功，localStorage 只写了清空的购物车，sessionStorage 只写了订单号，没有 cookie 与历史记录写入；请求地址与页面链接都不含号码。
  it("keeps the member's number in page memory only", async () => {
    const storage = () => ({ getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() });
    const local = storage();
    const session = storage();
    const cookieWrites: string[] = [];
    const doc = {};
    Object.defineProperty(doc, "cookie", {
      get: () => "",
      set: (value: string) => {
        cookieWrites.push(value);
      },
    });
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", session);
    vi.stubGlobal("document", doc);
    vi.stubGlobal("history", history);
    const calls = stubFetch({
      [MEMBER_PHONE_URL]: [json(200, { phone: OWN_PHONE })],
      [MEMBER_ORDER_URL]: [json(201, { order_number: ORDER_NUMBER, points_earned: 103 })],
    });

    let member = memberForm(TOKEN);
    const { moves } = recordMoves();
    await prefillPhone(createMemberSessionReader(), new AbortController().signal, {
      ...moves,
      fill: (phone) => {
        member = fillPhone(member, phone);
      },
    });
    expect(member.phone).toBe(OWN_PHONE);
    const html = render({ member: { ...MEMBER, phone: member.phone } });
    const placed: string[] = [];
    await submitMemberCheckout(memberOrderBody(CART, member.phone, guestRecipient(RECIPIENT), null, 0), "key", member.csrfToken, createMemberSessionReader(), { signal: new AbortController().signal }, {
      ...moves,
      placed: (orderNumber) => {
        placed.push(orderNumber);
      },
    });
    expect(placed).toEqual([ORDER_NUMBER]);
    const visited: RoutePath[] = [];
    completeMemberOrder(local, ORDER_NUMBER, (path) => visited.push(path));

    expect(local.setItem.mock.calls).toEqual([[CART_STORAGE_KEY, "[]"]]);
    expect(session.setItem.mock.calls).toEqual([[MEMBER_PAY_ORDER_KEY, ORDER_NUMBER]]);
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect(visited).toEqual(["/pay"]);
    for (const url of urls(calls)) {
      expect(url).not.toMatch(/6012|\?/);
    }
    for (const href of [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1] ?? "")) {
      expect(href).not.toMatch(/6012|Aina|Jalan|DEMO10|\?/);
    }
  });
});

// ---- 字典 ----

describe("dictionary", () => {
  // 验收「页面文字全部来自字典，带变量的条目按模板匹配」：会员第 3 步的各状态下，除商品数据、件数、地区与州属名称外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text on the member step in %s", (language) => {
    const names = countryNames(language);
    const regionNames = new Set(REGIONS.regions.flatMap((region) => [names(region.code), `${names(region.code)} +${String(region.calling_code)}`]));
    const data = new Set(["Crew Neck Tee", "Soy Wax Candle", "Black", "M", ...REGIONS.my_states.map((state) => state.name), ...regionNames]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    const states: RenderOptions[] = [
      { language },
      { language, member: { ...memberForm(TOKEN) }, quote: { status: "loading" } },
      { language, quote: { status: "error" } },
      { language, form: { ...MEMBER_STEP, recipient: { ...RECIPIENT, country: "" } }, quote: readyMember(NO_DESTINATION) },
      { language, member: { ...MEMBER, phoneInvalid: true, orderNotice: "points_unavailable" }, quote: readyMember(memberQuoteOf({ coupon_applied: false })) },
      { language, form: { ...MEMBER_STEP, submitting: true, checking: true } },
      { language, form: { ...MEMBER_STEP, notice: "unavailable" } },
      { language, form: { ...MEMBER_STEP, notice: "changed" } },
      { language, form: retryNotice(MEMBER_STEP) },
    ];
    for (const state of states) {
      for (const value of visibleTexts(render(state))) {
        const known = data.has(value) || /^× \d+$/.test(value) || patterns.some((pattern) => pattern.test(value));
        expect(known, value).toBe(true);
      }
    }
  });

  // UX-COPY 0.14 的会员第 3 步文案键：都在字典里且三列非空（与文档的逐字比较由 i18n/copy.test.ts 覆盖）。
  it.each<CopyKey>([
    "checkout.member_phone_hint",
    "checkout.phone_hint",
    "checkout.coupon",
    "checkout.coupon_apply",
    "checkout.coupon_invalid",
    "checkout.points",
    "checkout.points_available",
    "checkout.points_not_shipping",
    "checkout.summary_coupon",
    "checkout.summary_points",
  ])("has %s in all three languages", (key) => {
    for (const language of LANGUAGES) {
      expect(COPY[key][language]).not.toBe("");
    }
  });
});
