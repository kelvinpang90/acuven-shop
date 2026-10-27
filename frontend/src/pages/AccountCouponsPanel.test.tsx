import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MEMBER_COUPONS_URL } from "../api/memberAccount";
import type { AvailableCoupon, MemberCoupons, MemberCouponsRead, UsedCoupon } from "../api/memberAccount";
import { formatSen } from "../format";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { COUPON_TIME_ZONE, couponsStep, CouponsPanel, couponValue, loadCoupons } from "./AccountCouponsPanel";
import type { CouponsState } from "./AccountCouponsPanel";
import { formatDate } from "./TrackOrderPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 测试里的券代码、金额与订单号都是虚构值。SAVE5 的有效期是马来西亚时间 2027-01-01 00:30（UTC 仍是 2026-12-31），
// 用来确认有效期按马来西亚日期显示；它的最低消费为 0。
const DEMO10: AvailableCoupon = { code: "DEMO10", type: "fixed", amount_sen: 1000, percent: null, min_spend_sen: 5000, valid_until: "2026-12-31T15:59:59Z" };
const SAVE5: AvailableCoupon = { code: "SAVE5", type: "percent", amount_sen: null, percent: 5, min_spend_sen: 0, valid_until: "2026-12-31T16:30:00Z" };

const USED: UsedCoupon[] = [
  { code: "DEMO10", order_number: "K7Q29MXAB6TN2RJD", discount_sen: 1000 },
  { code: "SAVE5", order_number: "R5MC3VTEQ8LN2XWA", discount_sen: 300 },
];

const COUPONS: MemberCoupons = { available: [DEMO10, SAVE5], used: USED };

function ready(overrides: Partial<MemberCoupons> = {}): CouponsState {
  return { status: "ready", coupons: { ...COUPONS, ...overrides } };
}

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactElement, language: Language): ReactElement {
  return <LanguageProvider storage={languageStorage(language)}>{children}</LanguageProvider>;
}

function render(coupons: CouponsState = ready(), language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<CouponsPanel coupons={coupons} />, language));
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

function text(key: CopyKey, language: Language, vars: Record<string, string | number>): string {
  return escapeHtml(formatCopy(COPY[key][language], vars));
}

function part(html: string, pattern: RegExp): string {
  const found = pattern.exec(html)?.[0];
  if (found === undefined) {
    throw new Error(`not found: ${String(pattern)}`);
  }
  return found;
}

const cards = (html: string) =>
  [...part(html, /<ul class="site-account__coupons">[\s\S]*?<\/ul>/).matchAll(/<li class="acs-card site-account__coupon">([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");
const usedRows = (html: string, which: "desktop" | "phone") =>
  [...part(html, new RegExp(`<ul class="site-${which}-only site-account__list site-account__uses">[\\s\\S]*?</ul>`)).matchAll(/<li class="site-account__used">([\s\S]*?)<\/li>/g)].map(
    (m) => m[1] ?? "",
  );

// 有效期：valid_until 按马来西亚时区取日期。
function validUntil(coupon: AvailableCoupon, language: Language): string {
  return text("account.coupon_valid_until", language, { date: formatDate(coupon.valid_until, language, "Asia/Kuala_Lumpur") });
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

// settle 的替身：记下交出的结果。
function fakeSettle() {
  const steps: (CouponsState | "login")[] = [];
  const settle = (step: CouponsState | "login") => {
    steps.push(step);
    return Promise.resolve();
  };
  return { steps, settle };
}

describe("reading my coupons", () => {
  // SHOP-TASK-165 验收第 2 条「会话为 member 后…经 SHOP-TASK-164 读取」与第 5 条「接收 AbortSignal 与回调的异步函数」：
  // 请求 GET /api/member/coupons（不带查询参数），带页面的 signal；200 时把可用券与使用记录原样（按接口顺序）交给页面。
  it("hands the coupons over as they come from the server", async () => {
    const calls = stubFetch({ [MEMBER_COUPONS_URL]: [json(200, COUPONS)] });
    const page = new AbortController();
    const { steps, settle } = fakeSettle();
    await loadCoupons(page.signal, settle);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_COUPONS_URL]);
    expect(calls[0]?.init.signal).toBe(page.signal);
    expect(steps).toEqual([ready()]);
  });

  // 同一条「signed_out 时照我的订单重读会话并用 replace 进入 /login」：401 交给页面的是 login（重读会话与跳转由 AccountPage.test.tsx 守住）。
  it("hands over login when signed out", async () => {
    stubFetch({ [MEMBER_COUPONS_URL]: [json(401, { detail: "member_session_required" })] });
    const { steps, settle } = fakeSettle();
    await loadCoupons(new AbortController().signal, settle);
    expect(steps).toEqual(["login"]);
    expect(couponsStep({ kind: "signed_out" })).toBe("login");
  });

  // 同一条「failed 显示 common.error_retry，network 显示 common.network_check」：两种失败各交出对应的提示，该段只有段标题与提示。
  it.each<[CopyKey, MemberCouponsRead, Reply]>([
    ["common.error_retry", { kind: "failed" }, new Response(null, { status: 500 })],
    ["common.network_check", { kind: "network" }, "network"],
  ])("shows %s when the coupons cannot be read", async (key, read, reply) => {
    expect(couponsStep(read)).toEqual({ status: "failed", error: key });
    stubFetch({ [MEMBER_COUPONS_URL]: [reply] });
    const { steps, settle } = fakeSettle();
    await loadCoupons(new AbortController().signal, settle);
    expect(steps).toEqual([{ status: "failed", error: key }]);
    const html = render({ status: "failed", error: key });
    expect(html).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy(key)}</p>`);
    expect(textNodes(html)).toEqual([COPY["account.coupons"].en, COPY[key].en]);
  });

  // 同一条「读取中该段标 aria-busy」（标记在 AccountPage 的段上）：读取中该段只有段标题。
  it("shows only the section title while loading", () => {
    expect(textNodes(render({ status: "loading" }))).toEqual([COPY["account.coupons"].en]);
  });

  // SHOP-TASK-165 验收第 5 条「离开页面时中止请求，之后不再更新」：回答到达前已离开时不交出结果。
  it("hands nothing over after leaving the page", async () => {
    stubFetch({ [MEMBER_COUPONS_URL]: [json(200, COUPONS)] });
    const gone = new AbortController();
    gone.abort();
    const { steps, settle } = fakeSettle();
    await loadCoupons(gone.signal, settle);
    expect(steps).toEqual([]);
  });
});

describe("available coupons", () => {
  // UX P13 桌面线框「[account.coupons] / [account.coupons_available] / <代码> [account.coupon_value_fixed]/[account.coupon_value_percent] [M3] /
  // [account.coupon_min_spend] [M3] [account.coupon_valid_until] / [account.coupon_use_hint]」与验收第 4 条：段标题 h2 只在桌面，其后 h3
  // account.coupons_available、各张券（按接口顺序）与 account.coupon_use_hint。固定额为 account.coupon_value_fixed（formatSen(amount_sen)），
  // 最低消费大于 0 时为 account.coupon_min_spend（formatSen），有效期为 account.coupon_valid_until。
  it.each(LANGUAGES)("shows a fixed-amount coupon with its minimum spend in %s", (language) => {
    const html = render(ready(), language);
    expect(html.startsWith(`<h2 class="acs-display-m site-desktop-only">${copy("account.coupons", language)}</h2><h3 class="acs-display-s">${copy("account.coupons_available", language)}</h3>`)).toBe(true);
    const found = cards(html);
    expect(found).toHaveLength(2);
    expect(found[0]).toBe(
      `<span class="acs-num">DEMO10</span><span>${text("account.coupon_value_fixed", language, { amount: formatSen(1000) })}</span>` +
        `<span class="acs-body-s acs-muted">${text("account.coupon_min_spend", language, { amount: formatSen(5000) })}</span>` +
        `<span class="acs-body-s acs-muted">${validUntil(DEMO10, language)}</span>`,
    );
    expect(html).toContain(`</ul><p class="acs-body-s acs-muted">${copy("account.coupon_use_hint", language)}</p><h3`);
  });

  // 同一条「百分比为 account.coupon_value_percent（{percent}）」与 HANDOFF 0.63 第（6）条「最低消费为 0 的券不显示 account.coupon_min_spend」：
  // SAVE5 为 5%，没有最低消费一行。
  it.each(LANGUAGES)("shows a percent coupon without a zero minimum spend in %s", (language) => {
    const found = cards(render(ready(), language));
    expect(found[1]).toBe(
      `<span class="acs-num">SAVE5</span><span>${text("account.coupon_value_percent", language, { percent: 5 })}</span>` +
        `<span class="acs-body-s acs-muted">${validUntil(SAVE5, language)}</span>`,
    );
    expect(found[1]).not.toContain(escapeHtml(COPY["account.coupon_min_spend"][language].split("{")[0] ?? ""));
  });

  // 验收第 4 条「account.coupon_valid_until（{date} 为 valid_until 照 formatDate 按 Asia/Kuala_Lumpur 时区取日期）」：SAVE5 在马来西亚已是
  // 2027-01-01，与 UTC 的日期不同，显示的是马来西亚日期。
  it("dates the validity in Malaysia", () => {
    expect(COUPON_TIME_ZONE).toBe("Asia/Kuala_Lumpur");
    const malaysia = formatDate(SAVE5.valid_until, "en", "Asia/Kuala_Lumpur");
    expect(malaysia).toBe(formatDate("2027-01-01T12:00:00Z", "en", "UTC"));
    expect(malaysia).not.toBe(formatDate(SAVE5.valid_until, "en", "UTC"));
    expect(cards(render())[1]).toContain(escapeHtml(formatCopy(COPY["account.coupon_valid_until"].en, { date: malaysia })));
  });

  // 验收第 4 条「金额原样格式化、不在浏览器计算」：面额取接口的整数仙或百分比；类型与对应的值对不上时不显示面额（派生实现约束）。
  it("derives the value from the coupon as given", () => {
    expect(couponValue(DEMO10)).toEqual({ key: "account.coupon_value_fixed", vars: { amount: formatSen(1000) } });
    expect(couponValue(SAVE5)).toEqual({ key: "account.coupon_value_percent", vars: { percent: 5 } });
    expect(couponValue({ ...DEMO10, amount_sen: null })).toBeNull();
    expect(couponValue({ ...SAVE5, percent: null })).toBeNull();
  });

  // UX P13 线框「无可用券：[account.coupons_available_empty]」与视觉稿 P13-desktop-coupons 的空状态：没有可用券时 h3 之后只有
  // account.coupons_available_empty，没有卡片与 account.coupon_use_hint。
  it.each(LANGUAGES)("shows account.coupons_available_empty without coupons in %s", (language) => {
    const html = render(ready({ available: [] }), language);
    expect(html).toContain(`<h3 class="acs-display-s">${copy("account.coupons_available", language)}</h3><p class="acs-muted">${copy("account.coupons_available_empty", language)}</p><h3`);
    expect(html).not.toContain("site-account__coupons");
    expect(html).not.toContain(copy("account.coupon_use_hint", language));
  });
});

describe("used coupons", () => {
  // UX P13 桌面线框「[account.coupons_used] / <代码> [account.coupon_used_on] [K] [M4]」与验收第 4 条「每条为代码、account.coupon_used_on
  // （{orderNo} 为订单号）与 common.price_myr 格式化的 discount_sen」：h3 之后桌面每行依次为这三项（视觉稿 P13-desktop-coupons），按接口顺序。
  it.each(LANGUAGES)("lists each use on desktop in %s", (language) => {
    const html = render(ready(), language);
    expect(html).toContain(`<h3 class="acs-display-s site-account__subhead">${copy("account.coupons_used", language)}</h3>`);
    const rows = usedRows(html, "desktop");
    expect(rows).toHaveLength(USED.length);
    for (const [index, use] of USED.entries()) {
      expect(rows[index]).toBe(
        `<span class="acs-num">${use.code}</span><span>${text("account.coupon_used_on", language, { orderNo: use.order_number })}</span>` +
          `<span class="acs-num site-account__figure">${text("common.price_myr", language, { amount: formatSen(use.discount_sen) })}</span>`,
      );
    }
  });

  // UX P13 手机线框「<代码> [account.coupon_used_on] [K] [M4]」与视觉稿 P13-phone-coupons：手机每行为代码与金额、其下 account.coupon_used_on。
  it.each(LANGUAGES)("lists each use on phones in %s", (language) => {
    const rows = usedRows(render(ready(), language), "phone");
    expect(rows).toHaveLength(USED.length);
    for (const [index, use] of USED.entries()) {
      expect(rows[index]).toBe(
        `<span class="acs-num">${use.code}</span><span class="acs-num site-account__figure">${text("common.price_myr", language, { amount: formatSen(use.discount_sen) })}</span>` +
          `<span class="acs-body-s acs-muted">${text("account.coupon_used_on", language, { orderNo: use.order_number })}</span>`,
      );
    }
  });

  // UX P13 线框「无记录：[account.coupons_used_empty]」与视觉稿的空状态：没有记录时 h3 之后只有 account.coupons_used_empty。
  it.each(LANGUAGES)("shows account.coupons_used_empty without uses in %s", (language) => {
    const html = render(ready({ used: [] }), language);
    expect(html.endsWith(`<h3 class="acs-display-s site-account__subhead">${copy("account.coupons_used", language)}</h3><p class="acs-muted">${copy("account.coupons_used_empty", language)}</p>`)).toBe(true);
    expect(html).not.toContain("site-account__uses");
  });

  // 两种空状态同时出现时（视觉稿的标注「两种空状态」）：段里只有段标题、两个小标题与两句空状态。
  it("shows both empty states together", () => {
    expect(textNodes(render(ready({ available: [], used: [] })))).toEqual([
      COPY["account.coupons"].en,
      COPY["account.coupons_available"].en,
      COPY["account.coupons_available_empty"].en,
      COPY["account.coupons_used"].en,
      COPY["account.coupons_used_empty"].en,
    ]);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-165 验收第 1 条「copy.ts 按 UX-COPY 0.14 逐字加入」与第 6 条「页面文字全部来自字典，带变量的条目按模板匹配」：
  // 各状态下，除券代码外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides coupon codes in %s", (language) => {
    const states: CouponsState[] = [
      ready(),
      ready({ available: [] }),
      ready({ used: [] }),
      { status: "loading" },
      { status: "failed", error: "common.error_retry" },
      { status: "failed", error: "common.network_check" },
    ];
    const memberData = new Set(COUPONS.available.map((coupon) => coupon.code));
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        expect(memberData.has(value) || patterns.some((pattern) => pattern.test(value)), value).toBe(true);
      }
    }
  });

  // UX「阅读说明」不写死文字：这一段用到的键在字典里，三语都有（逐字核对由 copy.test.ts 读 UX-COPY 守住）。
  it("has every key this section uses", () => {
    const keys: CopyKey[] = [
      "account.coupons",
      "account.coupons_available",
      "account.coupons_available_empty",
      "account.coupon_value_fixed",
      "account.coupon_value_percent",
      "account.coupon_min_spend",
      "account.coupon_valid_until",
      "account.coupon_use_hint",
      "account.coupons_used",
      "account.coupons_used_empty",
      "account.coupon_used_on",
    ];
    for (const key of keys) {
      for (const language of LANGUAGES) {
        expect(COPY[key][language], key).not.toBe("");
      }
    }
  });
});
