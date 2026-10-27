import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MEMBER_POINTS_URL } from "../api/memberAccount";
import type { MemberPoints, MemberPointsRead, PointsActivity } from "../api/memberAccount";
import { BRAND, COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { loadPoints, pointsStep, pointsTypeLabel, PointsPanel, signedPoints } from "./AccountPointsPanel";
import type { PointsState } from "./AccountPointsPanel";
import { formatCouponDate } from "./AdminCouponsPage";
import { formatDate } from "./TrackOrderPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 测试里的订单号与积分都是虚构值。明细故意不按时间排列（第三条比前两条早、第五条最新），用来确认页面按接口顺序显示、不自行排序。
const HISTORY: PointsActivity[] = [
  { type: "returned", points: 60, order_number: "R5MC3VTEQ8LN2XWA", created_at: "2026-10-08T04:00:00Z" },
  { type: "reversed", points: -12, order_number: "R5MC3VTEQ8LN2XWA", created_at: "2026-10-08T03:00:00Z" },
  { type: "earned", points: 95, order_number: "K7Q29MXAB6TN2RJD", created_at: "2026-09-30T04:00:00Z" },
  { type: "redeemed", points: -500, order_number: "K7Q29MXAB6TN2RJD", created_at: "2026-09-30T03:00:00Z" },
  { type: "expired", points: -40, order_number: "W2LD8QNFH3PZ4W8C", created_at: "2026-10-20T04:00:00Z" },
];

const TYPE_KEY: Readonly<Record<PointsActivity["type"], CopyKey>> = {
  earned: "account.points_type_earned",
  redeemed: "account.points_type_redeemed",
  expired: "account.points_type_expired",
  returned: "account.points_type_returned",
  reversed: "account.points_type_reversed",
};

const SIGNED = ["+60", "-12", "+95", "-500", "-40"];

const POINTS: MemberPoints = { balance: 845, pending: 0, next_expiry: { points: 120, date: "2026-11-15" }, history: HISTORY };

function ready(overrides: Partial<MemberPoints> = {}): PointsState {
  return { status: "ready", points: { ...POINTS, ...overrides } };
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

function render(points: PointsState = ready(), language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<PointsPanel points={points} />, language));
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

const balanceBlock = (html: string) => part(html, /<div class="site-account__balance">[\s\S]*?<\/div>/);
const desktopRows = (html: string) =>
  [...part(html, /<ul class="site-desktop-only site-account__list site-account__history">[\s\S]*?<\/ul>/).matchAll(/<li class="site-account__activity">([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");
const fold = (html: string) => part(html, /<details class="site-phone-only site-account__fold"[\s\S]*?<\/details>/);
const phoneRows = (html: string) => [...fold(html).matchAll(/<li class="site-account__activity">([\s\S]*?)<\/li>/g)].map((m) => m[1] ?? "");

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
  const steps: (PointsState | "login")[] = [];
  const settle = (step: PointsState | "login") => {
    steps.push(step);
    return Promise.resolve();
  };
  return { steps, settle };
}

describe("reading my points", () => {
  // SHOP-TASK-165 验收第 2 条「会话为 member 后…经 SHOP-TASK-164 读取」与第 5 条「接收 AbortSignal 与回调的异步函数」：
  // 请求 GET /api/member/points（不带查询参数），带页面的 signal；200 时把余额、尚欠、到期与明细原样交给页面。
  it("hands the points over as they come from the server", async () => {
    const calls = stubFetch({ [MEMBER_POINTS_URL]: [json(200, POINTS)] });
    const page = new AbortController();
    const { steps, settle } = fakeSettle();
    await loadPoints(page.signal, settle);
    expect(calls.map((call) => call.url)).toEqual([MEMBER_POINTS_URL]);
    expect(calls[0]?.init.signal).toBe(page.signal);
    expect(steps).toEqual([ready()]);
  });

  // 同一条「signed_out 时照我的订单重读会话并用 replace 进入 /login」：401 交给页面的是 login（重读会话与跳转由 AccountPage.test.tsx 守住）。
  it("hands over login when signed out", async () => {
    stubFetch({ [MEMBER_POINTS_URL]: [json(401, { detail: "member_session_required" })] });
    const { steps, settle } = fakeSettle();
    await loadPoints(new AbortController().signal, settle);
    expect(steps).toEqual(["login"]);
    expect(pointsStep({ kind: "signed_out" })).toBe("login");
  });

  // 同一条「failed 显示 common.error_retry，network 显示 common.network_check」：两种失败各交出对应的提示，该段只有段标题与提示。
  it.each<[CopyKey, MemberPointsRead, Reply]>([
    ["common.error_retry", { kind: "failed" }, new Response(null, { status: 500 })],
    ["common.network_check", { kind: "network" }, "network"],
  ])("shows %s when the points cannot be read", async (key, read, reply) => {
    expect(pointsStep(read)).toEqual({ status: "failed", error: key });
    stubFetch({ [MEMBER_POINTS_URL]: [reply] });
    const { steps, settle } = fakeSettle();
    await loadPoints(new AbortController().signal, settle);
    expect(steps).toEqual([{ status: "failed", error: key }]);
    const html = render({ status: "failed", error: key });
    expect(html).toContain(`<p class="acs-alert acs-alert--danger site-notice" role="alert">${copy(key)}</p>`);
    expect(textNodes(html)).toEqual([COPY["account.points"].en, COPY[key].en]);
  });

  // 同一条「读取中该段标 aria-busy」（标记在 AccountPage 的段上）：读取中该段只有段标题。
  it("shows only the section title while loading", () => {
    expect(textNodes(render({ status: "loading" }))).toEqual([COPY["account.points"].en]);
  });

  // SHOP-TASK-165 验收第 5 条「离开页面时中止请求，之后不再更新」：回答到达前已离开时不交出结果。
  it("hands nothing over after leaving the page", async () => {
    stubFetch({ [MEMBER_POINTS_URL]: [json(200, POINTS)] });
    const gone = new AbortController();
    gone.abort();
    const { steps, settle } = fakeSettle();
    await loadPoints(gone.signal, settle);
    expect(steps).toEqual([]);
  });
});

describe("balance, owed points and expiry", () => {
  // UX P13 桌面线框「[account.points] / [account.points_balance] [M2]」与视觉稿 P13-desktop-points：段标题 h2 只在桌面（手机由标签代替），
  // 余额为 account.points_balance（{points} 为 balance，原样显示接口的整数）。
  it.each(LANGUAGES)("shows the title and the balance in %s", (language) => {
    const html = render(ready(), language);
    expect(html.startsWith(`<h2 class="acs-display-m site-desktop-only">${copy("account.points", language)}</h2>`)).toBe(true);
    expect(balanceBlock(html).startsWith(`<div class="site-account__balance"><span class="acs-num">${text("account.points_balance", language, { points: 845 })}</span>`)).toBe(true);
  });

  // UX P13 线框「[account.points_pending] [M2]（>0 时显示）」与 HANDOFF 0.63 第（6）条「没有即将到期的积分不显示 account.points_expiry」：
  // pending 为 0、next_expiry 为 null 时只有余额一行。
  it.each(LANGUAGES)("shows only the balance without owed points or expiry in %s", (language) => {
    const html = render(ready({ pending: 0, next_expiry: null }), language);
    expect(balanceBlock(html)).toBe(`<div class="site-account__balance"><span class="acs-num">${text("account.points_balance", language, { points: 845 })}</span></div>`);
  });

  // 同一条「（>0 时显示）」与视觉稿 P13-desktop-points 的标注「待抵扣大于 0 时…余额为 0、没有到期批次」：pending 大于 0 时余额之后为
  // account.points_pending（{points} 为 pending），余额照常显示 0。
  it.each(LANGUAGES)("shows the owed points when pending is above zero in %s", (language) => {
    const html = render(ready({ balance: 0, pending: 27, next_expiry: null }), language);
    expect(balanceBlock(html)).toBe(
      `<div class="site-account__balance"><span class="acs-num">${text("account.points_balance", language, { points: 0 })}</span>` +
        `<span class="acs-body-s">${text("account.points_pending", language, { points: 27 })}</span></div>`,
    );
  });

  // UX P13 线框「[account.points_expiry] [M2]」与验收第 3 条「next_expiry 不为 null 时 account.points_expiry（{points} 与 {date}，日期照
  // AdminCouponsPage.tsx 的 formatCouponDate 按马来西亚日期显示）」：到期在余额（与尚欠）之后；date 为马来西亚日期，与马来西亚时区
  // 当天零点按马来西亚时区格式化的结果相同，不随浏览器时区移动。
  it.each(LANGUAGES)("shows the next expiry on its Malaysian date in %s", (language) => {
    const html = render(ready({ pending: 5 }), language);
    const date = formatCouponDate("2026-11-15", language);
    expect(date).toBe(formatDate("2026-11-15T00:00:00+08:00", language, "Asia/Kuala_Lumpur"));
    expect(balanceBlock(html)).toBe(
      `<div class="site-account__balance"><span class="acs-num">${text("account.points_balance", language, { points: 845 })}</span>` +
        `<span class="acs-body-s">${text("account.points_pending", language, { points: 5 })}</span>` +
        `<span class="acs-body-s acs-muted">${text("account.points_expiry", language, { points: 120, date })}</span></div>`,
    );
  });
});

describe("points history", () => {
  // UX P13 桌面线框「[account.points_history] / <日期> [account.points_type_*] ±<积分> [K]<订单号>」与验收第 3 条「按接口顺序，每条为日期
  // （照 TrackOrderPage.tsx 的 formatDate，按浏览器时区）、account.points_type_* 中对应的键、带正负号的积分数（正数前加加号，原样显示接口的整数）
  // 与订单号（acs-num）」：桌面在 h3 之后每行依次为这四项，顺序与接口相同。
  it.each(LANGUAGES)("lists each activity on desktop in the server's order in %s", (language) => {
    const html = render(ready(), language);
    expect(html).toContain(`<h3 class="acs-display-s site-desktop-only site-account__subhead">${copy("account.points_history", language)}</h3>`);
    const rows = desktopRows(html);
    expect(rows).toHaveLength(HISTORY.length);
    for (const [index, activity] of HISTORY.entries()) {
      expect(rows[index]).toBe(
        `<span class="acs-body-s acs-muted">${escapeHtml(formatDate(activity.created_at, language))}</span><span>${copy(TYPE_KEY[activity.type], language)}</span>` +
          `<span class="acs-num site-account__figure">${SIGNED[index] ?? ""}</span><span class="acs-num acs-muted">${activity.order_number}</span>`,
      );
    }
  });

  // UX P13 手机线框「▸ [account.points_history] / <日期> [account.points_type_*] ±<积分> [K]<订单号>」与验收第 3 条「手机明细照视觉稿折叠」：
  // 手机为默认展开的 details（视觉稿 P13-phone-points 为 open），summary 为 account.points_history；每行依次为日期、积分数、类型与订单号（视觉稿的两行）。
  it.each(LANGUAGES)("folds the history on phones in %s", (language) => {
    const html = render(ready(), language);
    expect(fold(html).startsWith(`<details class="site-phone-only site-account__fold" open=""><summary>${copy("account.points_history", language)}</summary><ul`)).toBe(true);
    const rows = phoneRows(html);
    expect(rows).toHaveLength(HISTORY.length);
    for (const [index, activity] of HISTORY.entries()) {
      expect(rows[index]).toBe(
        `<span class="acs-body-s acs-muted">${escapeHtml(formatDate(activity.created_at, language))}</span><span class="acs-num site-account__figure">${SIGNED[index] ?? ""}</span>` +
          `<span>${copy(TYPE_KEY[activity.type], language)}</span><span class="acs-num acs-muted">${activity.order_number}</span>`,
      );
    }
  });

  // 同一条「带正负号的积分数（正数前加加号，原样显示接口的整数）」：正数加加号，负数与 0 原样。类型的名称取 account.points_type_* 中对应的键，
  // 未知类型不显示名称（派生实现约束，同我的订单的未知状态）。
  it("signs the points and names each type", () => {
    expect(HISTORY.map((activity) => signedPoints(activity.points))).toEqual(SIGNED);
    expect(signedPoints(0)).toBe("0");
    for (const [type, key] of Object.entries(TYPE_KEY)) {
      expect(pointsTypeLabel(type)).toBe(key);
    }
    expect(pointsTypeLabel("unknown")).toBeNull();
  });

  // HANDOFF 0.63 第（6）条「积分明细为空只显示标题」（UX-COPY 没有空明细的键）：桌面只有 h3，手机的折叠只有 summary，没有列表与其他文字。
  it.each(LANGUAGES)("shows only the history title when there is no activity in %s", (language) => {
    const html = render(ready({ history: [] }), language);
    expect(html).not.toContain("<ul");
    expect(fold(html)).toBe(`<details class="site-phone-only site-account__fold" open=""><summary>${copy("account.points_history", language)}</summary></details>`);
    expect(textNodes(html)).toEqual([
      COPY["account.points"][language],
      formatCopy(COPY["account.points_balance"][language], { points: 845 }),
      formatCopy(COPY["account.points_expiry"][language], { points: 120, date: formatCouponDate("2026-11-15", language) }),
      COPY["account.points_history"][language],
      COPY["account.points_history"][language],
    ]);
  });
});

describe("dictionary", () => {
  // SHOP-TASK-165 验收第 1 条「copy.ts 按 UX-COPY 0.14 逐字加入」与第 6 条「页面文字全部来自字典，带变量的条目按模板匹配」：
  // 各状态下，除积分明细的数据（日期、带正负号的积分数与订单号）外，每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text besides member data in %s", (language) => {
    const states: PointsState[] = [
      ready(),
      ready({ pending: 27, balance: 0, next_expiry: null }),
      ready({ history: [] }),
      { status: "loading" },
      { status: "failed", error: "common.error_retry" },
      { status: "failed", error: "common.network_check" },
    ];
    const memberData = new Set([...SIGNED, ...HISTORY.flatMap((activity) => [activity.order_number, formatDate(activity.created_at, language)])]);
    const patterns = [BRAND, ...Object.values(COPY).map((entry) => entry[language])].map(copyPattern);
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        expect(memberData.has(value) || patterns.some((pattern) => pattern.test(value)), value).toBe(true);
      }
    }
  });

  // UX「阅读说明」不写死文字：这一段用到的键在字典里，三语都有（逐字核对由 copy.test.ts 读 UX-COPY 守住）。
  it("has every key this section uses", () => {
    const keys: CopyKey[] = ["account.points", "account.points_balance", "account.points_pending", "account.points_expiry", "account.points_history", ...Object.values(TYPE_KEY)];
    for (const key of keys) {
      for (const language of LANGUAGES) {
        expect(COPY[key][language], key).not.toBe("");
      }
    }
  });
});
