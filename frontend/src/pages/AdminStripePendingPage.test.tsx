import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { PendingCheckout, RefundRetryReply, StripePending, StripePendingRead, VoidReply } from "../api/adminStripePending";
import { AdminFrameView } from "../components/AdminFrame";
import type { FrameState } from "../components/AdminFrame";
import { formatSen } from "../format";
import { COPY, LANGUAGES, formatCopy, translate } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_STRIPE_PENDING_PATH, matchRoute, RouterProvider } from "../router";
import type { RoutePath } from "../router";
import AdminStripePendingPage, {
  AdminStripePendingContent,
  AdminStripePendingView,
  CLOSED_VOID,
  INITIAL_PENDING,
  canSubmitVoid,
  loadPending,
  openVoid,
  readStep,
  retryStep,
  setVerified,
  startReading,
  startVoid,
  submitRetry,
  submitVoid,
  voidStep,
} from "./AdminStripePendingPage";
import type { AdminStripePendingViewProps, Notice, PendingState, StripePendingRequests, WriteMoves } from "./AdminStripePendingPage";
import { formatDateTime } from "./TrackOrderPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const STRIPE_PENDING_PATH = "/admin/stripe-pending";
const NAV_PATHS = [
  "/admin/orders",
  "/admin/refunds",
  STRIPE_PENDING_PATH,
  "/admin/products",
  "/admin/categories",
  "/admin/coupons",
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
  "admin.nav_shipping",
  "admin.nav_stock_resets",
  "admin.nav_store_design",
  "admin.nav_settings",
] as const;
const SIGNED_IN: FrameState = { status: "in", csrfToken: "admin-csrf-1", error: null };

// 接口给的两个列表（视觉稿 A11-desktop 的几行）：结账记录按告警时间从早到晚，退款按尝试 ID 从小到大，第二条退款没有错误类别。
const CHECKOUT_41: PendingCheckout = {
  checkout_id: 41,
  status: "creating",
  order_number: "7KQ2-M9XR",
  created_at: "2026-11-03T06:02:00+00:00",
  alerted_at: "2026-11-04T06:05:00+00:00",
};
const PENDING: StripePending = {
  checkouts: [
    CHECKOUT_41,
    { checkout_id: 57, status: "complete", order_number: "D9F4-ZP2W", created_at: "2026-11-05T01:31:00+00:00", alerted_at: "2026-11-06T01:40:00+00:00" },
    { checkout_id: 63, status: "open", order_number: "M3H8-R5C1", created_at: "2026-11-06T10:12:00+00:00", alerted_at: "2026-11-07T11:15:00+00:00" },
  ],
  refunds: [
    {
      checkout_id: 38,
      attempt_id: 102,
      order_number: "B6T1-N9K4",
      amount_sen: 12900,
      attempted_at: "2026-11-02T03:20:00+00:00",
      error_type: "invalid_request_error",
    },
    { checkout_id: 52, attempt_id: 117, order_number: "H2V7-C4M9", amount_sen: 5850, attempted_at: "2026-11-06T08:48:00+00:00", error_type: null },
  ],
  csrf_token: "stripe-csrf-1",
};
const LOADED: PendingState = { busy: false, pending: PENDING, error: null };
const EMPTY: PendingState = { busy: false, pending: { checkouts: [], refunds: [], csrf_token: "stripe-csrf-1" }, error: null };

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactNode, language: Language): ReactNode {
  return (
    <LanguageProvider storage={storage(language)}>
      <RouterProvider initialPath={STRIPE_PENDING_PATH}>{children}</RouterProvider>
    </LanguageProvider>
  );
}

function renderApp(language: Language): string {
  return renderToStaticMarkup(<App initialPath={STRIPE_PENDING_PATH} storage={storage(language)} />);
}

// 已登录（会话 200）时的后台框架：内容区为本页的内容（服务端渲染不跑 effect，内容区停在读取中）。
function renderSignedIn(language: Language, menuOpen = false): string {
  return renderToStaticMarkup(
    wrap(
      <AdminFrameView
        mode="light"
        current="stripePending"
        state={SIGNED_IN}
        busy={false}
        menuOpen={menuOpen}
        onToggleMenu={() => undefined}
        onCloseMenu={() => undefined}
        onLogOut={() => undefined}
      >
        <AdminStripePendingContent />
      </AdminFrameView>,
      language,
    ),
  );
}

const NOOP = () => undefined;

function viewProps(over: Partial<AdminStripePendingViewProps>): AdminStripePendingViewProps {
  return {
    state: LOADED,
    dialog: CLOSED_VOID,
    voidNotice: null,
    retryNotice: null,
    retrying: null,
    onReload: NOOP,
    onVoid: NOOP,
    onVerified: NOOP,
    onCancelVoid: NOOP,
    onSubmitVoid: NOOP,
    onRetry: NOOP,
    ...over,
  };
}

// 内容区本身（列表、提示与确认框）。
function renderView(language: Language, over: Partial<AdminStripePendingViewProps> = {}): string {
  return renderToStaticMarkup(wrap(<AdminStripePendingView {...viewProps(over)} />, language));
}

function text(key: CopyKey, language: Language): string {
  return COPY[key][language];
}

function priceIn(language: Language, sen: number): string {
  return translate(language, "common.price_myr", { amount: formatSen(sen) });
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

// 所有 class 含 className 的 name 元素（含其内容），按出现顺序。
function elements(html: string, name: string, className: string): string[] {
  const found: string[] = [];
  const starts = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].filter((m) => classes(m[0]).includes(className)).map((m) => m.index ?? 0);
  for (const start of starts) {
    const re = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
    re.lastIndex = start;
    let depth = 0;
    for (let m = re.exec(html); m !== null; m = re.exec(html)) {
      depth += m[0].startsWith("</") ? -1 : 1;
      if (depth === 0) {
        found.push(html.slice(start, m.index + m[0].length));
        break;
      }
    }
  }
  return found;
}

// 第一个 class 含 className 的 name 元素（含其内容）。
function element(html: string, name: string, className: string): string {
  const [first] = elements(html, name, className);
  if (first === undefined) {
    throw new Error(`no <${name}> with class ${className}`);
  }
  return first;
}

// 第 index 个（从 0 起）列表段。
function section(html: string, index: number): string {
  return elements(html, "section", "site-admin-stripe-pending__section")[index] ?? "";
}

// 一段里的结果提示：状态提示与警示（不含其他内容）。
function noticesIn(html: string): { role: string; text: string }[] {
  return [...elements(html, "div", "site-admin-stripe-pending__status"), ...elements(html, "div", "acs-admin__alert")].map((tag) => ({
    role: attributes(tags(tag, "div")[0] ?? "").get("role") ?? "",
    text: textNodes(tag).join(" "),
  }));
}

function buttonsIn(html: string): Map<string, string>[] {
  return tags(html, "button").map(attributes);
}

// 请求模块的替身：按脚本依次给出读取、作废与重试的回答，并记下每次调用的参数。
function requestDoubles(script: { reads?: StripePendingRead[]; voids?: VoidReply[]; retries?: RefundRetryReply[] }) {
  const reads = [...(script.reads ?? [])];
  const voids = [...(script.voids ?? [])];
  const retries = [...(script.retries ?? [])];
  const calls = { reads: 0, voids: [] as [number, string][], retries: [] as [number, string][] };
  const requests: StripePendingRequests = {
    read: () => {
      calls.reads += 1;
      return Promise.resolve(reads.shift() ?? { kind: "failed" });
    },
    voidCheckout: (checkoutId, csrfToken) => {
      calls.voids.push([checkoutId, csrfToken]);
      return Promise.resolve(voids.shift() ?? "failed");
    },
    retry: (checkoutId, csrfToken) => {
      calls.retries.push([checkoutId, csrfToken]);
      return Promise.resolve(retries.shift() ?? "failed");
    },
  };
  return { calls, requests };
}

// 页面对结果的处理的记录：显示的列表状态、换成的路径、给出的提示与重新读取的次数。
function recorder() {
  const shown: PendingState[] = [];
  const replaced: RoutePath[] = [];
  const notices: Notice[] = [];
  const counts = { reloads: 0 };
  const read = {
    show: (state: PendingState) => {
      shown.push(state);
    },
    replace: (path: RoutePath) => {
      replaced.push(path);
    },
  };
  const write: WriteMoves = {
    done: (notice) => {
      notices.push(notice);
    },
    reload: () => {
      counts.reloads += 1;
    },
    replace: read.replace,
  };
  return { shown, replaced, notices, counts, read, write };
}

const VOID_STATUS: [VoidReply, CopyKey][] = [
  ["confirmed", "admin.stripe_void_confirmed"],
  ["cancelled", "admin.stripe_void_cancelled"],
  ["voided", "admin.stripe_void_voided"],
  ["changed", "admin.stripe_pending_changed"],
];

const RETRY_STATUS: [RefundRetryReply, CopyKey][] = [
  ["done", "admin.stripe_late_refund_retried"],
  ["changed", "admin.stripe_pending_changed"],
];

describe("AdminStripePendingPage", () => {
  // UX 0.19「A11 后台 Stripe 待处理…独立一页 `/admin/stripe-pending`」与「管理后台总体」「每页顶部常驻 [admin.demo_banner]」：
  // 路径不带段值，经路由打开渲染后台框架（根元素 acs-admin、常驻 admin.demo_banner），没有前台页头、页脚与前台演示横幅。
  it.each(LANGUAGES)("renders the admin frame, not the storefront frame, in %s", (language) => {
    expect(ADMIN_STRIPE_PENDING_PATH).toBe(STRIPE_PENDING_PATH);
    expect(matchRoute(STRIPE_PENDING_PATH)).toEqual({ pattern: STRIPE_PENDING_PATH, params: {} });
    const html = renderApp(language);
    expect(classes(tags(html, "div")[0] ?? "")).toContain("acs-admin");
    expect(html).toContain(escapeHtml(COPY["admin.demo_banner"][language]));
    expect(html).not.toContain(escapeHtml(COPY["common.demo_banner"][language]));
    expect(html).not.toMatch(/<(header|footer)\b/);
  });

  // UX 0.19「管理后台总体」「导航项依次为 [admin.nav_orders]、[admin.nav_refunds]、[admin.nav_stripe_pending]（0.19）、[admin.nav_products]、…」
  // 与 A11「入口：后台导航 [admin.nav_stripe_pending]（排在 [admin.nav_refunds] 之后）」：经路由打开本页，桌面导航与 ☰ 菜单都依次为这十项，
  // Stripe 待处理排在退款之后、商品之前并标 aria-current="page"，其他项不标；各项文字为字典里当前语言的文案。
  it.each(LANGUAGES)("marks Stripe follow-ups current, after refunds and before products, in both navigation menus in %s", (language) => {
    for (const html of [renderApp(language), renderToStaticMarkup(wrap(<AdminStripePendingPage />, language)), renderSignedIn(language, true)]) {
      for (const nav of [element(html, "nav", "site-admin__nav"), element(html, "nav", "site-admin__menu-nav")]) {
        const items = tags(nav, "a").filter((tag) => !attributes(tag).has("lang"));
        expect(items.map((tag) => attributes(tag).get("href"))).toEqual(NAV_PATHS);
        expect(items.map((tag) => attributes(tag).get("aria-current"))).toEqual([
          undefined,
          undefined,
          "page",
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
        ]);
        const labels = textNodes(nav).slice(0, 10);
        expect(labels).toEqual(NAV_KEYS.map((key) => COPY[key][language]));
        expect(labels.indexOf(COPY["admin.nav_refunds"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_stripe_pending"][language]));
        expect(labels.indexOf(COPY["admin.nav_stripe_pending"][language])).toBeLessThan(labels.indexOf(COPY["admin.nav_products"][language]));
      }
    }
  });

  // UX A11 手机线框「☰  [admin.nav_stripe_pending]」与「管理后台总体」「手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称」。
  it.each(LANGUAGES)("names the Stripe follow-ups item in the phone bar in %s", (language) => {
    expect(element(renderApp(language), "span", "site-admin__bar-title")).toBe(
      `<span class="site-admin__bar-title">${escapeHtml(COPY["admin.nav_stripe_pending"][language])}</span>`,
    );
  });

  // UX A11 线框「☰ 导航 | [admin.nav_stripe_pending]」，其下「[admin.stripe_pending_note]」，再下「[admin.stripe_pending_checkouts_title]」与
  // 「[admin.stripe_pending_refunds_title]」「[admin.stripe_pending_refunds_note]」：已登录时内容区第一项为标题 admin.nav_stripe_pending
  // （h1，class acs-admin__h，SHOP-TASK-226 的断言保留），其下依次为说明与两个列表的标题；会话返回前内容区为空。
  it.each(LANGUAGES)("shows the title, the note and both list headings in order in %s", (language) => {
    const main = element(renderSignedIn(language), "main", "acs-admin__main");
    expect(main).toContain(`<h1 class="acs-admin__h">${escapeHtml(COPY["admin.nav_stripe_pending"][language])}</h1>`);
    expect(textNodes(main).slice(0, 5)).toEqual([
      text("admin.nav_stripe_pending", language),
      text("admin.stripe_pending_note", language),
      text("admin.stripe_pending_checkouts_title", language),
      text("admin.stripe_pending_refunds_title", language),
      text("admin.stripe_pending_refunds_note", language),
    ]);
    expect(tags(main, "h1")).toHaveLength(1);
    expect(element(renderApp(language), "main", "acs-admin__main")).not.toMatch(/<h1\b/);
  });

  // UX「管理后台总体」「表格列头、状态值、按钮与区块标题同样都用 `[key]`」：会话返回前、已登录（菜单收起与展开）时，
  // 每段文字都是当前语言的某条字典文案。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>(Object.values(COPY).map((entry) => entry[language]));
    for (const html of [renderApp(language), renderSignedIn(language), renderSignedIn(language, true), renderView(language, { state: EMPTY })]) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});

describe("A11 lists", () => {
  // UX A11「两个列表来自同一个接口，打开页面时读取一次」：挂载时的读取只调用一次列表接口，取到后显示两个列表与令牌（令牌只在状态里）。
  it("reads both lists with one request", async () => {
    const { calls, requests } = requestDoubles({ reads: [{ kind: "ok", pending: PENDING }] });
    const moves = recorder();
    expect(INITIAL_PENDING).toEqual({ busy: true, pending: null, error: null });
    await loadPending(new AbortController().signal, moves.read, requests);
    expect(calls).toEqual({ reads: 1, voids: [], retries: [] });
    expect(moves.shown).toEqual([LOADED]);
    expect(moves.replaced).toEqual([]);
  });

  // UX A11 桌面线框「[admin.col_checkout_record] [admin.filter_status] [pay.order_no] [admin.col_created] [admin.col_alerted]」
  // 「#<记录 ID> [admin.checkout_status_*] [K]<订单号> <时间> <时间> ( [admin.stripe_void] )」，「各按服务端给出的顺序显示」，
  // 「记录状态：creating 显示 [admin.checkout_status_creating]，open 显示 [admin.checkout_status_open]，complete 显示 [admin.checkout_status_complete]」
  // 与「时间的写法同 A03 的尝试时间」：第一个列表的表格列头与每行照此，行序同接口。
  it.each(LANGUAGES)("lists pending checkouts in a table in API order in %s", (language) => {
    const table = element(section(renderView(language), 0), "div", "site-admin-stripe-pending__table");
    expect(textNodes(table.slice(0, table.indexOf("</thead>")))).toEqual([
      text("admin.col_checkout_record", language),
      text("admin.filter_status", language),
      text("pay.order_no", language),
      text("admin.col_created", language),
      text("admin.col_alerted", language),
    ]);
    const rows = tags(table, "tr").length - 1;
    expect(rows).toBe(3);
    const body = textNodes(table.slice(table.indexOf("<tbody>")));
    const statusKeys: CopyKey[] = ["admin.checkout_status_creating", "admin.checkout_status_complete", "admin.checkout_status_open"];
    expect(body).toEqual(
      PENDING.checkouts.flatMap((checkout, index) => [
        `#${String(checkout.checkout_id)}`,
        text(statusKeys[index] ?? "admin.checkout_status_open", language),
        checkout.order_number,
        formatDateTime(checkout.created_at, language),
        formatDateTime(checkout.alerted_at, language),
        text("admin.stripe_void", language),
      ]),
    );
  });

  // UX A11 桌面线框「[admin.col_checkout_record] [admin.col_refund_attempt] [pay.order_no] [admin.col_amount] [admin.col_attempted] [admin.col_error_type]」
  // 「#<记录 ID> #<尝试 ID> [K]<订单号> [M1]<金额> <时间> <类别或 —> ( [admin.stripe_refund_retry] )」与「错误类别是 Stripe 给出的类别码原文
  // （如 invalid_request_error），为空时显示 —」：第二个列表的表格列头与每行照此，金额照 A03 用 common.price_myr，行序同接口。
  it.each(LANGUAGES)("lists failed late refunds in a table in API order in %s", (language) => {
    const table = element(section(renderView(language), 1), "div", "site-admin-stripe-pending__table");
    expect(textNodes(table.slice(0, table.indexOf("</thead>")))).toEqual([
      text("admin.col_checkout_record", language),
      text("admin.col_refund_attempt", language),
      text("pay.order_no", language),
      text("admin.col_amount", language),
      text("admin.col_attempted", language),
      text("admin.col_error_type", language),
    ]);
    expect(textNodes(table.slice(table.indexOf("<tbody>")))).toEqual([
      "#38",
      "#102",
      "B6T1-N9K4",
      priceIn(language, 12900),
      formatDateTime("2026-11-02T03:20:00+00:00", language),
      "invalid_request_error",
      text("admin.stripe_refund_retry", language),
      "#52",
      "#117",
      "H2V7-C4M9",
      priceIn(language, 5850),
      formatDateTime("2026-11-06T08:48:00+00:00", language),
      "—",
      text("admin.stripe_refund_retry", language),
    ]);
  });

  // UX A11 手机线框「#<记录 ID> [admin.checkout_status_*]」「[pay.order_no] [K]<订单号>」「[admin.col_created] <时间>」「[admin.col_alerted] <时间>」
  // 「( [admin.stripe_void] )」与「#<记录 ID> [admin.col_refund_attempt] #<尝试 ID>」「[pay.order_no] [K]<订单号>」「[M1]<金额>」
  // 「[admin.col_attempted] <时间>」「[admin.col_error_type] <类别或 —>」「( [admin.stripe_refund_retry] )」：两套都渲染，手机卡片照此。
  it.each(LANGUAGES)("renders phone cards with the wireframe fields in %s", (language) => {
    const html = renderView(language);
    const checkoutCards = elements(section(html, 0), "li", "site-admin-stripe-pending__card");
    expect(checkoutCards.map(textNodes)[0]).toEqual([
      "#41",
      text("admin.checkout_status_creating", language),
      text("pay.order_no", language),
      "7KQ2-M9XR",
      text("admin.col_created", language),
      formatDateTime(CHECKOUT_41.created_at, language),
      text("admin.col_alerted", language),
      formatDateTime(CHECKOUT_41.alerted_at, language),
      text("admin.stripe_void", language),
    ]);
    expect(checkoutCards.map((card) => textNodes(card)[0])).toEqual(["#41", "#57", "#63"]);
    const refundCards = elements(section(html, 1), "li", "site-admin-stripe-pending__card");
    expect(refundCards.map(textNodes)).toEqual([
      [
        "#38",
        text("admin.col_refund_attempt", language),
        "#102",
        text("pay.order_no", language),
        "B6T1-N9K4",
        priceIn(language, 12900),
        text("admin.col_attempted", language),
        formatDateTime("2026-11-02T03:20:00+00:00", language),
        text("admin.col_error_type", language),
        "invalid_request_error",
        text("admin.stripe_refund_retry", language),
      ],
      [
        "#52",
        text("admin.col_refund_attempt", language),
        "#117",
        text("pay.order_no", language),
        "H2V7-C4M9",
        priceIn(language, 5850),
        text("admin.col_attempted", language),
        formatDateTime("2026-11-06T08:48:00+00:00", language),
        text("admin.col_error_type", language),
        "—",
        text("admin.stripe_refund_retry", language),
      ],
    ]);
  });

  // UX A11「两类都没有记录时，各自标题下显示 [admin.stripe_pending_none]」与线框「（没有时只显示 [admin.stripe_pending_none]）」：
  // 为空的列表只有标题（第二个另有说明）与 admin.stripe_pending_none，没有表格与卡片；一个为空时另一个照常列出。
  it.each(LANGUAGES)("shows the empty state under each heading in %s", (language) => {
    const html = renderView(language, { state: EMPTY });
    expect(textNodes(section(html, 0))).toEqual([text("admin.stripe_pending_checkouts_title", language), text("admin.stripe_pending_none", language)]);
    expect(textNodes(section(html, 1))).toEqual([
      text("admin.stripe_pending_refunds_title", language),
      text("admin.stripe_pending_refunds_note", language),
      text("admin.stripe_pending_none", language),
    ]);
    expect(html).not.toMatch(/<(table|li)\b/);
    const onlyRefunds = renderView(language, { state: { busy: false, pending: { ...PENDING, checkouts: [] }, error: null } });
    expect(textNodes(section(onlyRefunds, 0))).toEqual([text("admin.stripe_pending_checkouts_title", language), text("admin.stripe_pending_none", language)]);
    expect(elements(section(onlyRefunds, 1), "li", "site-admin-stripe-pending__card")).toHaveLength(2);
  });

  // UX A11「读取失败显示 [common.error_retry] 与 [admin.reload]」，照 A07 网络中断为 common.network_check：失败时没有列表，
  // 只有警示与 admin.reload；admin.reload 的处理即重新读取（读取中禁用，开始读取时已显示的内容先留着）。
  it.each(LANGUAGES)("shows the read failure with a reload button in %s", (language) => {
    for (const [read, key] of [
      [{ kind: "failed" }, "common.error_retry"],
      [{ kind: "network" }, "common.network_check"],
    ] as [StripePendingRead, CopyKey][]) {
      const state = readStep(read);
      expect(state).toEqual({ busy: false, pending: null, error: key });
      if (state === "login") {
        throw new Error("unexpected login");
      }
      const html = renderView(language, { state });
      expect(noticesIn(html)).toEqual([{ role: "alert", text: text(key, language) }]);
      const reload = element(html, "button", "site-admin-stripe-pending__reload");
      expect(textNodes(reload)).toEqual([text("admin.reload", language)]);
      expect(attributes(tags(reload, "button")[0] ?? "").has("disabled")).toBe(false);
      expect(html).not.toMatch(/<(table|li)\b/);
      expect(attributes(tags(renderView(language, { state: startReading(state) }), "button")[0] ?? "").has("disabled")).toBe(true);
    }
    expect(startReading(LOADED)).toEqual({ ...LOADED, busy: true });
  });

  // UX A11「后台会话失效时同其他后台页面回到 A01」：读取 401 换成登录页，不显示列表。
  it("returns to A01 when the read has no session", async () => {
    const { requests } = requestDoubles({ reads: [{ kind: "none" }] });
    const moves = recorder();
    await loadPending(new AbortController().signal, moves.read, requests);
    expect(moves.replaced).toEqual([ADMIN_LOGIN_PATH]);
    expect(moves.shown).toEqual([]);
  });

  // 任务验收「离开页面时中止未完成的读取，旧读取的结果不再更新页面」：读取的 signal 中止之后，回来的结果（含 401）都不再显示或换页。
  it("ignores a read that finishes after the page is left", async () => {
    for (const read of [{ kind: "ok", pending: PENDING }, { kind: "none" }, { kind: "failed" }] as StripePendingRead[]) {
      let finish: (value: StripePendingRead) => void = NOOP;
      const seen: (AbortSignal | undefined)[] = [];
      const requests: StripePendingRequests = {
        ...requestDoubles({}).requests,
        read: (signal) => {
          seen.push(signal);
          return new Promise<StripePendingRead>((resolve) => {
            finish = resolve;
          });
        },
      };
      const controller = new AbortController();
      const moves = recorder();
      const pending = loadPending(controller.signal, moves.read, requests);
      expect(seen).toEqual([controller.signal]);
      controller.abort();
      finish(read);
      await pending;
      expect(moves.shown).toEqual([]);
      expect(moves.replaced).toEqual([]);
    }
  });

  // DESIGN 1.22 第 11 条与 UX A11「不显示 Stripe 的任何 ID、付款页地址、收货资料与 Stripe 返回的客户资料」：页面不显示 csrf_token
  // （令牌只在页面状态与写操作的请求头里），确认框打开时同样没有。
  it.each(LANGUAGES)("never shows the CSRF token in %s", (language) => {
    for (const html of [renderView(language), renderView(language, { dialog: openVoid(CHECKOUT_41) }), renderView(language, { state: EMPTY })]) {
      expect(html).not.toContain(PENDING.csrf_token);
      expect(html).not.toMatch(/csrf/i);
    }
  });
});

describe("A11 void confirmation", () => {
  // UX A11 作废确认框「[admin.stripe_void_confirm_title]」「[admin.stripe_void_confirm_body]」「[ ] [admin.stripe_void_verified]」
  // 「( [common.cancel] )  ( [admin.stripe_void_submit] )」与「标题里的 {id} 为记录 ID、{order} 为订单号」「[admin.stripe_void_verified] 未勾选时
  // [admin.stripe_void_submit] 禁用」：打开时为 role="dialog"、aria-modal="true"，标题、正文、未勾选的勾选框与两个按钮依次排列，提交禁用。
  it.each(LANGUAGES)("opens the dialog unchecked with submit disabled in %s", (language) => {
    const html = renderView(language, { dialog: openVoid(CHECKOUT_41) });
    const dialog = elements(html, "div", "site-admin__confirm")[0] ?? "";
    const attrs = attributes(tags(dialog, "div")[0] ?? "");
    expect(attrs.get("role")).toBe("dialog");
    expect(attrs.get("aria-modal")).toBe("true");
    const title = formatCopy(text("admin.stripe_void_confirm_title", language), { id: 41, order: "7KQ2-M9XR" });
    expect(textNodes(dialog)).toEqual([
      title,
      text("admin.stripe_void_confirm_body", language),
      text("admin.stripe_void_verified", language),
      text("common.cancel", language),
      text("admin.stripe_void_submit", language),
    ]);
    const labelled = attrs.get("aria-labelledby") ?? "";
    expect(dialog).toContain(`id="${labelled}">${escapeHtml(title)}</p>`);
    const checkbox = attributes(tags(dialog, "input")[0] ?? "");
    expect(checkbox.get("type")).toBe("checkbox");
    expect(checkbox.has("checked")).toBe(false);
    expect(buttonsIn(dialog).map((button) => button.has("disabled"))).toEqual([false, true]);
  });

  // UX A11「[admin.stripe_void_verified] 未勾选时 [admin.stripe_void_submit] 禁用」与「提交中按钮禁用」：勾选后提交可点；提交中两个按钮都禁用
  // （勾选框也不可改）；任务验收「每次打开时勾选框为未勾选」：勾选后关闭再打开，勾选框回到未勾选。
  it.each(LANGUAGES)("enables submit only when checked and disables both buttons while submitting in %s", (language) => {
    const opened = openVoid(CHECKOUT_41);
    expect(canSubmitVoid(opened)).toBe(false);
    const checked = setVerified(opened, true);
    expect(canSubmitVoid(checked)).toBe(true);
    const checkedHtml = elements(renderView(language, { dialog: checked }), "div", "site-admin__confirm")[0] ?? "";
    expect(attributes(tags(checkedHtml, "input")[0] ?? "").has("checked")).toBe(true);
    expect(buttonsIn(checkedHtml).map((button) => button.has("disabled"))).toEqual([false, false]);

    const submitting = startVoid(checked);
    expect(canSubmitVoid(submitting)).toBe(false);
    expect(setVerified(submitting, false)).toEqual(submitting);
    const submittingHtml = elements(renderView(language, { dialog: submitting }), "div", "site-admin__confirm")[0] ?? "";
    expect(buttonsIn(submittingHtml).map((button) => button.has("disabled"))).toEqual([true, true]);
    expect(attributes(tags(submittingHtml, "input")[0] ?? "").has("disabled")).toBe(true);

    expect(setVerified(CLOSED_VOID, true)).toEqual(CLOSED_VOID);
    expect(openVoid(CHECKOUT_41)).toEqual({ open: true, checkout: CHECKOUT_41, verified: false, submitting: false });
  });

  // UX A11「[common.cancel] 关闭确认框，不发请求」：关闭后的页面没有确认框，列表照旧；关闭不经请求模块（作废只由提交发出）。
  it.each(LANGUAGES)("closes the dialog on cancel without a request in %s", (language) => {
    const { calls } = requestDoubles({});
    const html = renderView(language, { dialog: CLOSED_VOID });
    expect(html).not.toContain('role="dialog"');
    expect(elements(section(html, 0), "li", "site-admin-stripe-pending__card")).toHaveLength(3);
    expect(calls).toEqual({ reads: 0, voids: [], retries: [] });
  });
});

describe("A11 void results", () => {
  // UX A11「结果在确认框关闭后显示在该列表标题下方，并重新读取两个列表」：confirmed、cancelled、voided 与 changed（「记录在此期间已变化，或记录不存在（404）」）
  // 依次为 admin.stripe_void_confirmed、admin.stripe_void_cancelled、admin.stripe_void_voided 与 admin.stripe_pending_changed，都是状态提示
  // （role="status"），给出提示（关闭确认框）后重新读取；请求带记录 ID 与读到的令牌。
  it.each(VOID_STATUS)("shows %s as a status under the first heading and reloads", async (reply, key) => {
    const { calls, requests } = requestDoubles({ voids: [reply] });
    const moves = recorder();
    await submitVoid(41, PENDING.csrf_token, new AbortController().signal, moves.write, requests);
    expect(calls.voids).toEqual([[41, "stripe-csrf-1"]]);
    expect(moves.notices).toEqual([{ key, tone: "status" }]);
    expect(moves.counts.reloads).toBe(1);
    for (const language of LANGUAGES) {
      const html = renderView(language, { voidNotice: moves.notices[0] ?? null });
      const first = section(html, 0);
      expect(noticesIn(first)).toEqual([{ role: "status", text: text(key, language) }]);
      expect(textNodes(first).slice(0, 2)).toEqual([text("admin.stripe_pending_checkouts_title", language), text(key, language)]);
      expect(noticesIn(section(html, 1))).toEqual([]);
      expect(html).not.toContain('role="dialog"');
    }
  });

  // UX A11「本站记录已显示付款而 Stripe 暂不可用：[admin.stripe_void_unavailable]（确认框关闭，行不变）」与「其他失败：[common.error_retry]」，
  // 网络中断照 A07 为 common.network_check：都是警示（acs-admin__alert，role="alert"），不重新读取。
  it.each([
    ["unavailable", "admin.stripe_void_unavailable"],
    ["failed", "common.error_retry"],
    ["network", "common.network_check"],
  ] as [VoidReply, CopyKey][])("shows %s as an alert without reloading", async (reply, key) => {
    const { requests } = requestDoubles({ voids: [reply] });
    const moves = recorder();
    await submitVoid(41, PENDING.csrf_token, new AbortController().signal, moves.write, requests);
    expect(moves.notices).toEqual([{ key, tone: "alert" }]);
    expect(moves.counts.reloads).toBe(0);
    for (const language of LANGUAGES) {
      const html = renderView(language, { voidNotice: moves.notices[0] ?? null });
      expect(noticesIn(section(html, 0))).toEqual([{ role: "alert", text: text(key, language) }]);
      expect(elements(section(html, 0), "li", "site-admin-stripe-pending__card")).toHaveLength(3);
    }
  });

  // 任务 purpose「写操作的 403 关闭确认框、显示 common.error_retry 并重新读取（取得新的 CSRF 令牌，照 A03 重试的 csrf）」：403 给出警示并重新读取，
  // 下一次作废带重新读到的令牌。
  it("closes on 403, reloads and sends the next void with the new token", async () => {
    const fresh: StripePending = { ...PENDING, csrf_token: "stripe-csrf-2" };
    const { calls, requests } = requestDoubles({ reads: [{ kind: "ok", pending: fresh }], voids: ["csrf", "voided"] });
    const moves = recorder();
    const life = new AbortController();
    await submitVoid(41, PENDING.csrf_token, life.signal, moves.write, requests);
    expect(moves.notices).toEqual([{ key: "common.error_retry", tone: "alert" }]);
    expect(moves.counts.reloads).toBe(1);
    await loadPending(new AbortController().signal, moves.read, requests);
    const token = moves.shown[0]?.pending?.csrf_token ?? "";
    expect(token).toBe("stripe-csrf-2");
    await submitVoid(41, token, life.signal, moves.write, requests);
    expect(calls.voids).toEqual([
      [41, "stripe-csrf-1"],
      [41, "stripe-csrf-2"],
    ]);
    expect(moves.notices[1]).toEqual({ key: "admin.stripe_void_voided", tone: "status" });
  });

  // UX A11「后台会话失效时同其他后台页面回到 A01」：作废 401 换成登录页，不给提示、不重新读取。
  it("returns to A01 when the void has no session", async () => {
    const { requests } = requestDoubles({ voids: ["none"] });
    const moves = recorder();
    await submitVoid(41, PENDING.csrf_token, new AbortController().signal, moves.write, requests);
    expect(voidStep("none")).toBe("login");
    expect(moves.replaced).toEqual([ADMIN_LOGIN_PATH]);
    expect(moves.notices).toEqual([]);
    expect(moves.counts.reloads).toBe(0);
  });
});

describe("A11 refund retry", () => {
  // UX A11「[admin.stripe_refund_retry] 直接发出重试（与 A03 的重试相同，不另设确认框），提交中按钮禁用」：重试进行中该按钮禁用并标 aria-busy
  // （本页同时只发一次重试，其他行的按钮也禁用），作废按钮与确认框不受影响。
  it.each(LANGUAGES)("disables the retry button while the retry is in flight in %s", (language) => {
    const idle = buttonsIn(section(renderView(language), 1));
    expect(idle.map((button) => button.has("disabled"))).toEqual([false, false, false, false]);
    const busy = renderView(language, { retrying: 38 });
    const buttons = buttonsIn(section(busy, 1));
    expect(buttons.map((button) => button.has("disabled"))).toEqual([true, true, true, true]);
    expect(buttons.map((button) => button.get("aria-busy"))).toEqual(["true", "false", "true", "false"]);
    expect(buttonsIn(section(busy, 0)).some((button) => button.has("disabled"))).toBe(false);
    expect(busy).not.toContain('role="dialog"');
  });

  // UX A11「成功显示 [admin.stripe_late_refund_retried] 并重新读取」「不能重试…或记录不存在（404）显示 [admin.stripe_pending_changed] 并重新读取」：
  // 两者都是状态提示（role="status"），在第二个列表标题（与说明）之下、列表之前；请求带记录 ID 与读到的令牌。
  it.each(RETRY_STATUS)("shows %s as a status under the second heading and reloads", async (reply, key) => {
    const { calls, requests } = requestDoubles({ retries: [reply] });
    const moves = recorder();
    await submitRetry(38, PENDING.csrf_token, new AbortController().signal, moves.write, requests);
    expect(calls.retries).toEqual([[38, "stripe-csrf-1"]]);
    expect(moves.notices).toEqual([{ key, tone: "status" }]);
    expect(moves.counts.reloads).toBe(1);
    for (const language of LANGUAGES) {
      const html = renderView(language, { retryNotice: moves.notices[0] ?? null });
      const second = section(html, 1);
      expect(noticesIn(second)).toEqual([{ role: "status", text: text(key, language) }]);
      expect(textNodes(second).slice(0, 3)).toEqual([
        text("admin.stripe_pending_refunds_title", language),
        text("admin.stripe_pending_refunds_note", language),
        text(key, language),
      ]);
      expect(noticesIn(section(html, 0))).toEqual([]);
    }
  });

  // UX A11「会超过付款金额显示 A03 已有的 [admin.stripe_refund_blocked]；其他失败显示 [common.error_retry]」，网络中断照 A07 为 common.network_check：
  // 都是警示（role="alert"），不重新读取，行不变。
  it.each([
    ["cap", "admin.stripe_refund_blocked"],
    ["failed", "common.error_retry"],
    ["network", "common.network_check"],
  ] as [RefundRetryReply, CopyKey][])("shows %s as an alert without reloading", async (reply, key) => {
    const { requests } = requestDoubles({ retries: [reply] });
    const moves = recorder();
    await submitRetry(38, PENDING.csrf_token, new AbortController().signal, moves.write, requests);
    expect(moves.notices).toEqual([{ key, tone: "alert" }]);
    expect(moves.counts.reloads).toBe(0);
    for (const language of LANGUAGES) {
      const html = renderView(language, { retryNotice: moves.notices[0] ?? null });
      expect(noticesIn(section(html, 1))).toEqual([{ role: "alert", text: text(key, language) }]);
      expect(elements(section(html, 1), "li", "site-admin-stripe-pending__card")).toHaveLength(2);
    }
  });

  // 任务 purpose「写操作的 403…显示 common.error_retry 并重新读取（取得新的 CSRF 令牌）」：重试 403 给出警示并重新读取，下一次重试带新令牌。
  it("reloads on 403 and sends the next retry with the new token", async () => {
    const fresh: StripePending = { ...PENDING, csrf_token: "stripe-csrf-3" };
    const { calls, requests } = requestDoubles({ reads: [{ kind: "ok", pending: fresh }], retries: ["csrf", "done"] });
    const moves = recorder();
    await submitRetry(38, PENDING.csrf_token, new AbortController().signal, moves.write, requests);
    expect(moves.notices).toEqual([{ key: "common.error_retry", tone: "alert" }]);
    expect(moves.counts.reloads).toBe(1);
    await loadPending(new AbortController().signal, moves.read, requests);
    await submitRetry(38, moves.shown[0]?.pending?.csrf_token ?? "", new AbortController().signal, moves.write, requests);
    expect(calls.retries).toEqual([
      [38, "stripe-csrf-1"],
      [38, "stripe-csrf-3"],
    ]);
  });

  // UX A11「后台会话失效时同其他后台页面回到 A01」：重试 401 换成登录页，不给提示、不重新读取。
  it("returns to A01 when the retry has no session", async () => {
    const { requests } = requestDoubles({ retries: ["none"] });
    const moves = recorder();
    await submitRetry(38, PENDING.csrf_token, new AbortController().signal, moves.write, requests);
    expect(retryStep("none")).toBe("login");
    expect(moves.replaced).toEqual([ADMIN_LOGIN_PATH]);
    expect(moves.notices).toEqual([]);
    expect(moves.counts.reloads).toBe(0);
  });
});

describe("A11 notices", () => {
  // 任务验收「两处提示各自只保留最新一条…重新读取不清除刚给出的结果提示」：两处各显示一条，互不影响；重新读取中（列表仍在、标 aria-busy）提示照旧。
  it.each(LANGUAGES)("keeps each notice in its own place while reloading in %s", (language) => {
    const voidNotice: Notice = { key: "admin.stripe_void_voided", tone: "status" };
    const retryNotice: Notice = { key: "admin.stripe_refund_blocked", tone: "alert" };
    const html = renderView(language, { state: startReading(LOADED), voidNotice, retryNotice });
    expect(attributes(tags(element(html, "div", "site-admin-stripe-pending"), "div")[0] ?? "").get("aria-busy")).toBe("true");
    expect(noticesIn(section(html, 0))).toEqual([{ role: "status", text: text("admin.stripe_void_voided", language) }]);
    expect(noticesIn(section(html, 1))).toEqual([{ role: "alert", text: text("admin.stripe_refund_blocked", language) }]);
    const failed = readStep({ kind: "failed" });
    if (failed === "login") {
      throw new Error("unexpected login");
    }
    expect(noticesIn(section(renderView(language, { state: failed, voidNotice }), 0))).toEqual([
      { role: "status", text: text("admin.stripe_void_voided", language) },
    ]);
  });

  // 任务验收「离开页面时…旧读取的结果不再更新页面」，写操作同样：作废或重试在离开页面之后才回来时，不给提示、不重新读取、不换页。
  it("ignores write results after the page is left", async () => {
    const { requests } = requestDoubles({ voids: ["voided", "none"], retries: ["done", "none"] });
    const moves = recorder();
    const life = new AbortController();
    life.abort();
    await submitVoid(41, PENDING.csrf_token, life.signal, moves.write, requests);
    await submitVoid(41, PENDING.csrf_token, life.signal, moves.write, requests);
    await submitRetry(38, PENDING.csrf_token, life.signal, moves.write, requests);
    await submitRetry(38, PENDING.csrf_token, life.signal, moves.write, requests);
    expect(moves.notices).toEqual([]);
    expect(moves.counts.reloads).toBe(0);
    expect(moves.replaced).toEqual([]);
  });
});
