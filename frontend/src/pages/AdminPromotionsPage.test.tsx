import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminPromotionSummary, PromotionsRead, PromotionState } from "../api/adminPromotions";
import App from "../App";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_PROMOTIONS_PATH, adminPromotionPath, matchRoute, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminPromotionsPage, {
  AdminPromotionsContent,
  AdminPromotionsView,
  formatPromotionTime,
  INITIAL_LIST,
  loadPromotions,
  PROMOTION_STATE_CLASS,
  PROMOTION_STATE_LABEL,
  promotionTimeText,
} from "./AdminPromotionsPage";
import type { ListState } from "./AdminPromotionsPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PROMOTIONS_PATH = "/admin/promotions";
const NAV_PATHS = [
  "/admin/orders",
  "/admin/refunds",
  "/admin/stripe-pending",
  "/admin/products",
  "/admin/categories",
  "/admin/coupons",
  PROMOTIONS_PATH,
  "/admin/shipping",
  "/admin/stock-resets",
  "/admin/store-design",
  "/admin/settings",
];
const NAV_KEYS = [
  "admin.nav_orders",
  "admin.nav_refunds",
  "admin.nav_stripe_pending",
  "admin.nav_products",
  "admin.nav_categories",
  "admin.nav_coupons",
  "admin.nav_promotions",
  "admin.nav_shipping",
  "admin.nav_stock_resets",
  "admin.nav_store_design",
  "admin.nav_settings",
] as const;
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactNode, language: Language): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={PROMOTIONS_PATH}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={PROMOTIONS_PATH} storage={storage(language)} />);
}

// 已登录（会话 200）时的后台框架：内容区为本页的内容。
function renderSignedIn(language: Language, menuOpen = false): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="promotions"
        state={SIGNED_IN}
        busy={false}
        menuOpen={menuOpen}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        <AdminPromotionsContent />
      </AdminFrameView>,
      language,
    ),
  );
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

// 页面上能看到或听到的全部文字：每个文本节点，以及读屏标签与提示类属性。
function visibleTexts(html: string): string[] {
  const labels = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? ""));
  return [...textNodes(html), ...labels];
}

function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => m[0]);
}

function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((m[1] ?? "").toLowerCase(), unescapeHtml(m[2] ?? ""));
  }
  return found;
}

function classes(tag: string): string[] {
  return (attributes(tag).get("class") ?? "").split(/\s+/);
}

// 第一个 class 含 className 的 name 元素（含其内容）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) => classes(m[0]).includes(className))?.index;
  if (start === undefined) {
    throw new Error(`no <${name}> with class ${className}`);
  }
  const re = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  re.lastIndex = start;
  let depth = 0;
  for (let m = re.exec(html); m !== null; m = re.exec(html)) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, m.index + m[0].length);
    }
  }
  throw new Error(`unclosed <${name}> with class ${className}`);
}

describe("AdminPromotionsPage", () => {
  // UX 0.20 页面地图「A12 | 后台促销活动（0.20，含前台预览） | /admin/promotions、…」与「管理后台总体」「每页顶部常驻 [admin.demo_banner]」：
  // 列表路径不带段值，经路由打开渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），没有前台页头、页脚与前台演示横幅。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    expect(ADMIN_PROMOTIONS_PATH).toBe(PROMOTIONS_PATH);
    expect(matchRoute(PROMOTIONS_PATH)).toEqual({ pattern: PROMOTIONS_PATH, params: {} });
    const html = renderApp(language);
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX 0.20「管理后台总体」「导航项依次为 …、[admin.nav_coupons]、[admin.nav_promotions]（0.20）、[admin.nav_shipping]、…」
  // 与 A12「入口：后台导航 [admin.nav_promotions]（排在 [admin.nav_coupons] 之后）」：经路由打开本页，桌面导航与 ☰ 菜单都依次为这十一项，
  // 促销活动（指向 /admin/promotions）排在优惠券之后、运费之前并标 aria-current="page"，其他项不标；各项文字为字典里当前语言的文案。
  it.each(LANGUAGES)("marks promotions current, after coupons and before shipping, in both navigation menus in %s", (language) => {
    for (const html of [renderApp(language), renderToStaticMarkup(wrap(<AdminPromotionsPage />, language)), renderSignedIn(language, true)]) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual(
          NAV_PATHS.map((path) => (path === PROMOTIONS_PATH ? "page" : undefined)),
        );
        const labels = textNodes(nav).slice(0, 11);
        expect(labels).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
        expect(labels.indexOf(COPY["admin.nav_coupons"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_promotions"][language]));
        expect(labels.indexOf(COPY["admin.nav_promotions"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_shipping"][language]));
      }
    }
  });

  // UX「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」：手机顶栏名称为 admin.nav_promotions。
  it.each(LANGUAGES)("names the promotions item in the phone bar in %s", (language) => {
    expect(element(renderApp(language), "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_promotions"][language])}</span>`,
    );
  });

  // UX A12 列表线框「☰ 导航 | [admin.nav_promotions]  ( [common.create] )」的标题（SHOP-TASK-269 改动：内容区加入列表，
  // 原「内容区只有标题」改为含标题）：已登录时内容区含原样的 h1 admin.nav_promotions（class acs-admin__h）；会话返回前内容区没有 h1。
  it.each(LANGUAGES)("shows the title in the content area in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(main).toContain(`<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_promotions"][language])}</h1>`);
    expect(element(renderApp(language), "main", "acs-admin__main")).not.toMatch(/<h1\b/);
  });

  // 任务目的「不调用接口」：渲染本页内容（会话返回前与已登录）不发任何请求（后台框架自己的会话读取在挂载后才发，服务端渲染不跑）。
  it("renders without any request", () => {
    const request = vi.fn(() => Promise.reject(new Error("unexpected request")));
    vi.stubGlobal("fetch", request);
    for (const language of LANGUAGES) {
      renderApp(language);
      renderSignedIn(language);
    }
    expect(request).not.toHaveBeenCalled();
  });

  // UX「管理后台总体」「表格列头、状态值、按钮与区块标题同样都用 `[key]`」：会话返回前、已登录（菜单收起与展开）时，
  // 每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const html of [renderApp(language), renderSignedIn(language), renderSignedIn(language, true)]) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});

// 列表的活动（SHOP-TASK-269）：接口顺序为进行中、未开始、草稿、已结束；草稿两端时间都为 null，内部名称带尖括号与 &。
const RUNNING: AdminPromotionSummary = {
  id: 12,
  name: "Lunar New Year 2027",
  state: "running",
  starts_at: "2027-01-20T00:00",
  ends_at: "2027-02-12T23:59",
  rule_count: 4,
  updated_at: "2026-11-01T02:00:00",
};
const SCHEDULED: AdminPromotionSummary = {
  id: 7,
  name: "Hari Raya 2027",
  state: "scheduled",
  starts_at: "2027-03-05T09:05",
  ends_at: "2027-03-25T23:59",
  rule_count: 3,
  updated_at: "2026-11-02T02:00:00",
};
const DRAFT: AdminPromotionSummary = {
  id: 31,
  name: "<b>12.12</b> & test",
  state: "draft",
  starts_at: null,
  ends_at: null,
  rule_count: 1,
  updated_at: "2026-11-03T02:00:00",
};
const ENDED: AdminPromotionSummary = {
  id: 3,
  name: "Merdeka 2026",
  state: "ended",
  starts_at: "2026-08-25T00:00",
  ends_at: "2026-09-01T23:59",
  rule_count: 2,
  updated_at: "2026-09-02T02:00:00",
};
const ROWS = [RUNNING, SCHEDULED, DRAFT, ENDED];
const STATE_TAGS: [PromotionState, CopyKey, string][] = [
  ["running", "admin.promo_running", "acs-tag acs-tag--success"],
  ["scheduled", "admin.promo_scheduled", "acs-tag acs-tag--outline"],
  ["draft", "admin.promo_draft", "acs-tag acs-tag--neutral"],
  ["ended", "admin.promo_ended", "acs-tag acs-tag--muted"],
];

function loaded(promotions: AdminPromotionSummary[]): ListState {
  return { busy: false, promotions, error: null };
}

function renderView(state: ListState, language: Language): string {
  return renderToStaticMarkup(wrap(<AdminPromotionsView state={state} />, language));
}

// 桌面表格 tbody 的各行，每行为各 td 的内部 HTML。
function tableRows(html: string): string[][] {
  const table = element(html, "div", "site-admin-promotions__table");
  const body = /<tbody>([\s\S]*)<\/tbody>/.exec(table)?.[1] ?? "";
  return body
    .split("</tr>")
    .filter((row) => row !== "")
    .map((row) => [...row.matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)].map((m) => m[1] ?? ""));
}

// 手机卡片的各项（含其内容）。
function cards(html: string): string[] {
  return element(html, "ul", "site-admin-promotions__cards")
    .split("</li>")
    .filter((card) => card.includes("<li"));
}

function stateTag(state: PromotionState, language: Language): string {
  return `<span class="${PROMOTION_STATE_CLASS[state]}">${escapeHtml(COPY[PROMOTION_STATE_LABEL[state]][language])}</span>`;
}

// 读取结果的替身：记下收到的 signal，按给定结果回答。
function readDouble(result: PromotionsRead) {
  const signals: (AbortSignal | undefined)[] = [];
  const read = (signal?: AbortSignal) => {
    signals.push(signal);
    return Promise.resolve(result);
  };
  return { signals, read };
}

function recorder() {
  const shown: ListState[] = [];
  const replaced: RoutePath[] = [];
  return {
    shown,
    replaced,
    moves: {
      show: (state: ListState) => {
        shown.push(state);
      },
      replace: (path: RoutePath) => {
        replaced.push(path);
      },
    },
  };
}

describe("AdminPromotionsPage list", () => {
  // UX A12 列表线框「[admin.nav_promotions]  ( [common.create] )」「[admin.promo_demo_note]」与「演示提示：★ [admin.demo_banner]；
  // [admin.rules_apply_new]；[admin.promo_demo_note]」：标题行为 h1 与指向 /admin/promotions/new 的 common.create，其下为 admin.promo_demo_note
  // 与 admin.rules_apply_new，再下为列表区；读取中与取到之后都一样。
  it.each(LANGUAGES)("shows the title row with the new link and the two notes in %s", (language) => {
    for (const state of [INITIAL_LIST, loaded(ROWS)]) {
      const html = renderView(state, language);
      const title = element(html, "div", "site-admin-promotions__title");
      expect(title.startsWith(`<div class="site-admin-promotions__title"><h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_promotions"][language])}</h1>`)).toBe(true);
      const create = tags(title, "a");
      expect(create).toHaveLength(1);
      expect(attributes(create[0] ?? "").get("href")).toBe("/admin/promotions/new");
      expect(classes(create[0] ?? "")).toEqual(["acs-admin__btn"]);
      expect(textNodes(title)).toEqual([COPY["admin.nav_promotions"][language], COPY["common.create"][language]]);
      const note = element(html, "p", "site-admin-promotions__note");
      expect(textNodes(note)).toEqual([COPY["admin.promo_demo_note"][language], COPY["admin.rules_apply_new"][language]]);
      expect(html.indexOf(title)).toBeLessThan(html.indexOf(note));
      expect(html.indexOf(note)).toBeLessThan(html.indexOf('class="site-admin-promotions__list"'));
    }
  });

  // UX A12 列表线框「[admin.promo_name] [admin.filter_status] [admin.promo_time] [admin.promo_rule_count]」与各行
  // 「<内部名称> [admin.promo_running]   <开始>–<结束> <n> ( [admin.promo_open] )」：桌面表格的列头依次为这四项与空的操作列，
  // 每行依次为内部名称、状态标签、时间、规则条数与 admin.promo_open。
  it.each(LANGUAGES)("lists name, status, time, rule count and open in the table in %s", (language) => {
    const html = renderView(loaded(ROWS), language);
    const head = /<thead>([\s\S]*)<\/thead>/.exec(element(html, "div", "site-admin-promotions__table"))?.[1] ?? "";
    expect([...head.matchAll(/<th\b[^>]*>([\s\S]*?)<\/th>/g)].map((m) => unescapeHtml(m[1] ?? ""))).toEqual([
      COPY["admin.promo_name"][language],
      COPY["admin.filter_status"][language],
      COPY["admin.promo_time"][language],
      COPY["admin.promo_rule_count"][language],
      "",
    ]);
    const rows = tableRows(html);
    expect(rows).toHaveLength(ROWS.length);
    ROWS.forEach((promotion, index) => {
      const cells = rows[index] ?? [];
      expect(cells).toHaveLength(5);
      expect(cells[0]).toBe(escapeHtml(promotion.name));
      expect(cells[1]).toBe(stateTag(promotion.state, language));
      expect(unescapeHtml(cells[2] ?? "")).toBe(promotionTimeText(promotion.starts_at, promotion.ends_at, language));
      expect(cells[3]).toBe(String(promotion.rule_count));
      expect(textNodes(cells[4] ?? "")).toEqual([COPY["admin.promo_open"][language]]);
    });
  });

  // UX A12「状态值 `[admin.promo_draft]` 草稿、`[admin.promo_scheduled]` 已发布未开始、`[admin.promo_running]` 进行中、`[admin.promo_ended]` 已结束」：
  // 四种状态的标签文字为当前语言的那条文案，类名照视觉稿 A12-desktop（success、outline、neutral、muted）；表格与卡片相同。导出的映射即为这四组。
  it.each(LANGUAGES)("labels the four states with their copy and tag classes in %s", (language) => {
    for (const [state, key, className] of STATE_TAGS) {
      expect(PROMOTION_STATE_LABEL[state]).toBe(key);
      expect(PROMOTION_STATE_CLASS[state]).toBe(className);
    }
    const html = renderView(loaded(ROWS), language);
    const rows = tableRows(html);
    const items = cards(html);
    STATE_TAGS.forEach(([state, key, className], index) => {
      const expected = `<span class="${className}">${escapeHtml(COPY[key][language])}</span>`;
      expect(ROWS[index]?.state).toBe(state);
      expect(rows[index]?.[1]).toBe(expected);
      expect(items[index]).toContain(expected);
    });
  });

  // UX A12「列表按进行中、未开始（开始时间从早到晚）、草稿（最近修改在前）、已结束（结束时间从晚到早）排列」：排序由服务端做好，
  // 页面照接口给的顺序显示，不重排——接口给的顺序打乱时，表格与卡片仍照接口的顺序。
  it("keeps the order the server gave", () => {
    const given = [ENDED, DRAFT, RUNNING, SCHEDULED];
    const html = renderView(loaded(given), "en");
    expect(tableRows(html).map((cells) => unescapeHtml(cells[0] ?? ""))).toEqual(given.map((promotion) => promotion.name));
    expect(cards(html).map((card) => textNodes(card)[0])).toEqual(given.map((promotion) => promotion.name));
  });

  // UX A12「去向：活动编辑区」与各行「( [admin.promo_open] )」：每行（表格与卡片）的 admin.promo_open 链接到 adminPromotionPath(活动 ID)；
  // 标题行的 common.create 链接到新建。
  it.each(LANGUAGES)("links open to the promotion's edit page in %s", (language) => {
    const html = renderView(loaded(ROWS), language);
    const expected = ROWS.map((promotion) => adminPromotionPath(promotion.id));
    expect(expected).toEqual(["/admin/promotions/12", "/admin/promotions/7", "/admin/promotions/31", "/admin/promotions/3"]);
    const tableLinks = tableRows(html).map((cells) => tags(cells[4] ?? "", "a"));
    const cardLinks = cards(html).map((card) => tags(card, "a"));
    for (const links of [tableLinks, cardLinks]) {
      expect(links.map((found) => found.length)).toEqual([1, 1, 1, 1]);
      expect(links.map((found) => attributes(found[0] ?? "").get("href"))).toEqual(expected);
    }
    for (const card of cards(html)) {
      const texts = textNodes(card);
      expect(texts[texts.length - 1]).toBe(COPY["admin.promo_open"][language]);
    }
  });

  // UX A12「手机：…列表改为卡片：<内部名称>、<状态>、<开始>–<结束>、( [admin.promo_open] )」：表格与卡片两套都渲染；每张卡片第一行为内部名称
  // 与状态标签，其下为时间与 admin.promo_open，没有规则条数；两端时间都为 null 时不显示时间。
  it.each(LANGUAGES)("renders a card per promotion with name, status, time and open in %s", (language) => {
    const html = renderView(loaded(ROWS), language);
    const items = cards(html);
    expect(items).toHaveLength(ROWS.length);
    ROWS.forEach((promotion, index) => {
      const card = items[index] ?? "";
      const head = element(card, "div", "site-admin-promotions__card-head");
      expect(head).toBe(`<div class="site-admin-promotions__card-head"><span>${escapeHtml(promotion.name)}</span>${stateTag(promotion.state, language)}</div>`);
      const time = promotionTimeText(promotion.starts_at, promotion.ends_at, language);
      expect(textNodes(card)).toEqual([
        promotion.name,
        COPY[PROMOTION_STATE_LABEL[promotion.state]][language],
        ...(time === "" ? [] : [time]),
        COPY["admin.promo_open"][language],
      ]);
    });
  });

  // UX A12「开始与结束按马来西亚时间输入到分钟」与各行「<开始>–<结束>」：接口给的马来西亚本地时间只按当前语言格式化（日期 medium、时间 short），
  // 年月日时分照原样，不按浏览器时区换算。
  it.each(LANGUAGES)("formats Malaysia local times without converting them in %s", (language) => {
    const expected = new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(
      new Date(Date.UTC(2027, 2, 5, 9, 5)),
    );
    expect(formatPromotionTime("2027-03-05T09:05", language)).toBe(expected);
    const shifted = new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kuala_Lumpur" }).format(
      new Date(Date.UTC(2027, 2, 5, 9, 5)),
    );
    expect(formatPromotionTime("2027-03-05T09:05", language)).not.toBe(shifted);
    if (language === "en") {
      expect(formatPromotionTime("2027-03-05T09:05", language)).toContain("Mar 5, 2027");
    }
  });

  // UX A12 草稿行「<内部名称> [admin.promo_draft]     <开始>–<结束>? <n>」：两端都有时为「开始 – 结束」；只有一端时只显示那一端、没有分隔符；
  // 两端都为 null 时表格的时间格为空。
  it.each(LANGUAGES)("shows one end, both ends or nothing for the time in %s", (language) => {
    const start = formatPromotionTime("2027-03-05T09:05", language);
    const end = formatPromotionTime("2027-03-25T23:59", language);
    expect(promotionTimeText("2027-03-05T09:05", "2027-03-25T23:59", language)).toBe(`${start} – ${end}`);
    expect(promotionTimeText("2027-03-05T09:05", null, language)).toBe(start);
    expect(promotionTimeText(null, "2027-03-25T23:59", language)).toBe(end);
    expect(promotionTimeText(null, null, language)).toBe("");
    const rows = [
      { ...DRAFT, id: 41, starts_at: "2027-03-05T09:05", ends_at: null },
      { ...DRAFT, id: 42, starts_at: null, ends_at: "2027-03-25T23:59" },
      DRAFT,
    ];
    const html = renderView(loaded(rows), language);
    expect(tableRows(html).map((cells) => unescapeHtml(cells[2] ?? ""))).toEqual([start, end, ""]);
    expect(html).toContain(`</span></td><td></td><td>1</td>`);
    for (const value of [start, end]) {
      expect(value).not.toContain("–");
    }
  });

  // UX A12 列表线框「<内部名称>」：内部名称按纯文本显示，尖括号与 & 转义，不成为标签。
  it("shows the internal name as plain text", () => {
    const html = renderView(loaded([DRAFT]), "en");
    expect(html).not.toMatch(/<b>/);
    expect(tableRows(html)[0]?.[0]).toBe("&lt;b&gt;12.12&lt;/b&gt; &amp; test");
  });

  // UX A12 列表线框「空：[admin.promo_empty]」：没有活动时列表区只有 admin.promo_empty，没有表格与卡片。
  it.each(LANGUAGES)("shows only the empty note when there are no promotions in %s", (language) => {
    const html = renderView(loaded([]), language);
    const list = element(html, "div", "site-admin-promotions__list");
    expect(list).toBe(
      `<div class="site-admin-promotions__list" aria-busy="false"><p class="acs-admin__panel acs-admin__muted site-admin-promotions__empty">${escapeHtml(COPY["admin.promo_empty"][language])}</p></div>`,
    );
  });

  // UX A12「目的：建立、修改、…节日促销活动」的列表（读取中的写法照 HANDOFF 0.98 落实写法与 A07）：读取中列表区标 aria-busy，
  // 没有表格、卡片、空提示与错误提示。
  it.each(LANGUAGES)("marks the list busy while reading in %s", (language) => {
    expect(INITIAL_LIST).toEqual({ busy: true, promotions: null, error: null });
    const list = element(renderView(INITIAL_LIST, language), "div", "site-admin-promotions__list");
    expect(list).toBe(`<div class="site-admin-promotions__list" aria-busy="true"></div>`);
  });

  // UX A12「目的：…」的列表读取（列表的读取结果照 HANDOFF 0.98 落实写法与 A07）：读取取到时照接口给的列表显示，替身收到的是本次的 signal。
  it("shows the promotions the read returned", async () => {
    const { signals, read } = readDouble({ kind: "ok", promotions: ROWS, csrf_token: "csrf-1" });
    const record = recorder();
    const controller = new AbortController();
    await loadPromotions(controller.signal, record.moves, read);
    expect(signals).toEqual([controller.signal]);
    expect(record.shown).toEqual([loaded(ROWS)]);
    expect(record.replaced).toEqual([]);
  });

  // UX A12「入口：后台导航 [admin.nav_promotions]」的列表在会话失效时（照 HANDOFF 0.98 落实写法与 A07）：读取得到 401 换成 A01，不显示列表。
  it("goes to the admin login when the read has no session", async () => {
    const { read } = readDouble({ kind: "none" });
    const record = recorder();
    await loadPromotions(new AbortController().signal, record.moves, read);
    expect(record.replaced).toEqual([ADMIN_LOGIN_PATH]);
    expect(record.shown).toEqual([]);
  });

  // UX A12「目的：…」的列表读取失败（照 HANDOFF 0.98 落实写法与 A07）：网络中断显示 common.network_check，其他失败显示 common.error_retry，
  // 都只有提示（role="alert"），没有表格、卡片与空提示。
  it.each(LANGUAGES)("shows the network and other failure alerts in %s", async (language) => {
    const cases: [PromotionsRead, CopyKey][] = [
      [{ kind: "network" }, "common.network_check"],
      [{ kind: "failed" }, "common.error_retry"],
    ];
    for (const [result, key] of cases) {
      const { read } = readDouble(result);
      const record = recorder();
      await loadPromotions(new AbortController().signal, record.moves, read);
      expect(record.replaced).toEqual([]);
      expect(record.shown).toEqual([{ busy: false, promotions: null, error: key }]);
      const list = element(renderView(record.shown[0] ?? INITIAL_LIST, language), "div", "site-admin-promotions__list");
      expect(list).toBe(
        `<div class="site-admin-promotions__list" aria-busy="false"><div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[key][language])}</span></div></div>`,
      );
    }
  });

  // UX A12「目的：…」的列表读取在离开页面之后（照 HANDOFF 0.98 落实写法与 A07）：signal 已中止之后的读取结果不再更新页面，也不换页。
  it("ignores a read that finishes after leaving the page", async () => {
    for (const result of [{ kind: "ok", promotions: ROWS, csrf_token: "csrf-1" }, { kind: "none" }, { kind: "failed" }] as PromotionsRead[]) {
      const { read } = readDouble(result);
      const record = recorder();
      const controller = new AbortController();
      controller.abort();
      await loadPromotions(controller.signal, record.moves, read);
      expect(record.shown).toEqual([]);
      expect(record.replaced).toEqual([]);
    }
  });
});
