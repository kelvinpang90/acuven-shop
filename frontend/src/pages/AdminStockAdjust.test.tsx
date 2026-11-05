import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { StockAdjustResult } from "../api/adminStockAdjust";
import { CSRF_HEADER } from "../api/pay";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import type { RereadOutcome } from "./AdminProductImages";
import { adjustStep, adjustValue, changeText, finishAdjust, IDLE, rereadNotice, startAdjust, StockAdjustView, submitAdjust } from "./AdminStockAdjust";
import type { AdjustMoves, AdjustNotice, AdjustState, StockAdjustViewProps } from "./AdminStockAdjust";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const SKU = "crew-neck-tee-black-m";
const ADJUST_URL = "/api/admin/variants/41/available-stock";

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function wrap(children: ReactNode, language: Language): ReactNode {
  return <LanguageProvider storage={storage(language)}>{children}</LanguageProvider>;
}

const HANDLERS = {
  onText: () => undefined,
  onSave: () => undefined,
  onCancel: () => undefined,
};

function props(change: Partial<StockAdjustViewProps> = {}): StockAdjustViewProps {
  return { sku: SKU, shown: 18, state: IDLE, ...HANDLERS, ...change };
}

function render(change: Partial<StockAdjustViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<StockAdjustView {...props(change)} />, language));
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

// 看得见或读得出的文字：文本节点与读屏标签、提示类属性。
function visibleTexts(html: string): string[] {
  const labels = [...html.matchAll(/\s(?:aria-label|title|alt|placeholder)="([^"]*)"/gi)].map((m) => unescapeHtml(m[1] ?? "")).filter((value) => value !== "");
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
  throw new Error(`unclosed <${name}>`);
}

// 调整区里的提示（role 为 status 或 alert）的文字。
function notices(html: string): [string, string][] {
  return [...html.matchAll(/<(span|div)\b([^>]*)\brole="(status|alert)"[^>]*>([\s\S]*?)<\/\1>/g)].map((m): [string, string] => [m[3] ?? "", textNodes(m[4] ?? "").join(" ")]);
}

function dictionary(language: Language): Set<string> {
  return new Set<string>(Object.values(COPY).map((entry) => entry[language]));
}

type AnyElement = ReactElement<Record<string, unknown>>;

// 元素树：展开子组件（直接调用，仍在语言的上下文里），用来取按钮与输入框的处理函数（不模拟点击）。
function expand(node: ReactNode): AnyElement[] {
  if (Array.isArray(node)) {
    return node.flatMap((child: ReactNode) => expand(child));
  }
  if (!isValidElement(node)) {
    return [];
  }
  const found = node as AnyElement;
  if (typeof found.type === "function") {
    const component = found.type as (props: Record<string, unknown>) => ReactNode;
    return [found, ...expand(component(found.props))];
  }
  return [found, ...expand(found.props.children as ReactNode)];
}

function viewTree(viewProps: StockAdjustViewProps): AnyElement[] {
  let found: AnyElement[] = [];
  const probe = () => {
    found = expand(createElement(StockAdjustView, viewProps));
    return null;
  };
  renderToStaticMarkup(wrap(createElement(probe), "en"));
  return found;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call {
  url: string;
  init: RequestInit;
}

type Reply = Response | "network";

// fetch 替身：按顺序给出回答；"network" 表示网络中断。多出的请求以错误拒绝。
function stubFetch(replies: Reply[]): Call[] {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const reply = queue.shift();
      if (reply === undefined) {
        return Promise.reject(new Error(`unexpected request ${url}`));
      }
      return reply === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
    }),
  );
  return calls;
}

// 请求体（JSON 字符串）读成值。
function sentBody(call: Call | undefined): unknown {
  const body = call?.init.body;
  if (typeof body !== "string") {
    throw new Error("request body is not a JSON string");
  }
  return JSON.parse(body) as unknown;
}

// 调整区对结果的处理：记下交给页面的当日可用库存、结束时的提示、重新读取（回答 reread 给的结果）与回到 A01。
function recorder(reread: RereadOutcome = "ok") {
  const events: string[] = [];
  const available: number[] = [];
  const finished: AdjustNotice[] = [];
  const moves: AdjustMoves = {
    available: (count) => {
      events.push("available");
      available.push(count);
    },
    finish: (notice) => {
      events.push("finish");
      finished.push(notice);
    },
    reread: () => {
      events.push("reread");
      return Promise.resolve(reread);
    },
    login: () => {
      events.push("login");
    },
  };
  return { events, available, finished, moves };
}

// 提交中的状态里已填 text。
function filled(text: string): AdjustState {
  return startAdjust(changeText(IDLE, text));
}

const NOTICES: readonly AdjustNotice[] = [
  { key: "admin.adjust_saved" },
  { key: "admin.stock_changed", count: 17 },
  { key: "admin.adjust_invalid" },
  { key: "common.network_check" },
  { key: "common.error_retry" },
];

describe("AdminStockAdjust", () => {
  // UX A04 线框「[admin.adjust_title] <sku>   [admin.adjust_shown] <n>   [admin.adjust_new] [M4 ___] ( [admin.adjust_save] ) ( [common.cancel] )」
  // 「[admin.adjust_note]」、UX「当日库存调整」「显示该 SKU 的代码、页面当前显示的当日可用库存 [admin.adjust_shown] 与新值输入框 M4」「[admin.adjust_note]
  // 常显」与本任务验收「展开区——admin.adjust_title（{sku} 为该 SKU 的代码）、admin.adjust_shown（{count} 为页面当前显示的当日可用库存）、带可见标签
  // admin.adjust_new 的新值输入框、admin.adjust_save、common.cancel 与常显的 admin.adjust_note」：文字按这个顺序；输入框在 admin.adjust_new 的 label 里、
  // 初值为空串（purpose「新值输入框初值为空串」）、inputmode 为 numeric；两个按钮都是 type="button"（不提交编辑区），admin.adjust_save 为主要按钮、
  // common.cancel 为次要；没有提示时没有 status 与 alert。
  it.each(LANGUAGES)("shows the title, the shown value, the new value input, the buttons and the note in %s", (language) => {
    const html = render({}, language);
    expect(textNodes(html)).toEqual([
      formatCopy(COPY["admin.adjust_title"][language], { sku: SKU }),
      formatCopy(COPY["admin.adjust_shown"][language], { count: 18 }),
      COPY["admin.adjust_new"][language],
      COPY["admin.adjust_save"][language],
      COPY["common.cancel"][language],
      COPY["admin.adjust_note"][language],
    ]);
    expect(textNodes(html)[0]).toContain(SKU);
    expect(textNodes(html)[1]).toContain("18");
    const label = /<label\b[^>]*>([\s\S]*?)<\/label>/.exec(html)?.[1] ?? "";
    expect(textNodes(label)).toEqual([COPY["admin.adjust_new"][language]]);
    const inputs = tags(label, "input");
    expect(inputs).toHaveLength(1);
    expect(tags(html, "input")).toEqual(inputs);
    expect(attributes(inputs[0] ?? "").get("value")).toBe("");
    expect(attributes(inputs[0] ?? "").get("inputmode")).toBe("numeric");
    const buttons = tags(html, "button");
    expect(buttons.map((tag) => [attributes(tag).get("type"), classes(tag).includes("acs-admin__btn--secondary")])).toEqual([
      ["button", false],
      ["button", true],
    ]);
    expect(notices(html)).toEqual([]);
    const root = tags(html, "div")[0] ?? "";
    expect(attributes(root).get("role")).toBe("group");
    const titleId = attributes(tags(html, "span")[0] ?? "").get("id");
    expect(titleId).toBeTruthy();
    expect(attributes(root).get("aria-labelledby")).toBe(titleId);
  });

  // 同上：{count} 为页面当前显示的值（0 与 9,999 的端点照样原样显示），{sku} 为传入的 SKU 代码。
  it("fills {sku} and {count} from the props", () => {
    expect(textNodes(render({ sku: "tote-natural", shown: 0 })).slice(0, 2)).toEqual(["Adjust today's stock for tote-natural", "Shown now: 0"]);
    expect(textNodes(render({ shown: 9999 }, "zh"))[1]).toBe(formatCopy(COPY["admin.adjust_shown"].zh, { count: 9999 }));
  });

  // 本任务验收「新值须为 1–4 位数字（0–9,999），否则不提交、显示 admin.adjust_invalid」、UX「输入不是 0–9,999 的整数时不提交」与 DESIGN 1.18 第 12 条
  // 「0–9,999 的整数」：0、7、0007、9999 合格（按十进制换成整数），10000、-1、1.5、空串、带首尾空白、中间空白、+5、1e3 与全角数字不合格。
  it("checks the new value before submitting", () => {
    expect(["0", "7", "0007", "9999"].map(adjustValue)).toEqual([0, 7, 7, 9999]);
    for (const text of ["10000", "-1", "1.5", "", " ", " 5", "5 ", "1 0", "+5", "1e3", "１２", "abc"]) {
      expect(adjustValue(text)).toBeNull();
    }
  });

  // 本任务验收「新值…否则不提交、显示 admin.adjust_invalid」：不合格的新值不发请求，直接以 admin.adjust_invalid 结束，不改页面显示的值。
  it("does not submit an invalid value", async () => {
    const calls = stubFetch([]);
    for (const text of ["10000", "-1", "1.5", "", " 25"]) {
      const run = recorder();
      await submitAdjust(41, 18, text, "csrf-1", new AbortController().signal, run.moves);
      expect(run.events).toEqual(["finish"]);
      expect(run.finished).toEqual([{ key: "admin.adjust_invalid" }]);
    }
    expect(calls).toEqual([]);
  });

  // DESIGN 1.18 第 12 条「请求带调整前页面显示的当日可用库存」、UX「[admin.adjust_save] 立即提交（请求带页面显示的那个值）」与本任务验收「提交时 expected 为
  // admin.adjust_shown 的值、available_stock 为新值」：POST 该 SKU 的路径，请求体恰为 expected（页面显示的 18）与 available_stock（新值 25），请求头带
  // CSRF 令牌，并带调整区的 signal；0 与 9999 两端照样提交。
  it("sends the shown value as expected and the new value", async () => {
    const calls = stubFetch([
      json(200, { variant_id: 41, sku: SKU, available_stock: 25 }),
      json(200, { variant_id: 41, sku: SKU, available_stock: 0 }),
      json(200, { variant_id: 41, sku: SKU, available_stock: 9999 }),
    ]);
    const controller = new AbortController();
    await submitAdjust(41, 18, "25", "csrf-1", controller.signal, recorder().moves);
    await submitAdjust(41, 25, "0", "csrf-1", controller.signal, recorder().moves);
    await submitAdjust(41, 0, "9999", "csrf-2", controller.signal, recorder().moves);
    expect(calls.map((call) => [call.url, call.init.method, sentBody(call), new Headers(call.init.headers).get(CSRF_HEADER), call.init.signal])).toEqual([
      [ADJUST_URL, "POST", { expected: 18, available_stock: 25 }, "csrf-1", controller.signal],
      [ADJUST_URL, "POST", { expected: 25, available_stock: 0 }, "csrf-1", controller.signal],
      [ADJUST_URL, "POST", { expected: 0, available_stock: 9999 }, "csrf-2", controller.signal],
    ]);
  });

  // UX「成功后该行的 [admin.available_today] 改为新值、显示 [admin.adjust_saved]」与本任务 purpose「成功后展开区保持打开、admin.adjust_shown 与该行的
  // admin.available_today 改为返回的值」：200 时先把返回的值交给页面再以 admin.adjust_saved 结束（交给页面的是服务端返回的值，不是所填的原文）；
  // 结束后所填的新值保留，渲染为 role="status" 的 admin.adjust_saved，admin.adjust_shown 为页面给的新值。
  it("hands the returned value to the page on success", async () => {
    stubFetch([json(200, { variant_id: 41, sku: SKU, available_stock: 25 })]);
    const run = recorder();
    await submitAdjust(41, 18, "025", "csrf-1", new AbortController().signal, run.moves);
    expect(run.events).toEqual(["available", "finish"]);
    expect(run.available).toEqual([25]);
    expect(run.finished).toEqual([{ key: "admin.adjust_saved" }]);
    const state = finishAdjust(filled("025"), { key: "admin.adjust_saved" });
    expect(state).toEqual({ text: "025", busy: false, notice: { key: "admin.adjust_saved" } });
    for (const language of LANGUAGES) {
      const html = render({ shown: 25, state }, language);
      expect(notices(html)).toEqual([["status", COPY["admin.adjust_saved"][language]]]);
      expect(textNodes(html)[1]).toBe(formatCopy(COPY["admin.adjust_shown"][language], { count: 25 }));
      expect(attributes(tags(html, "input")[0] ?? "").get("value")).toBe("025");
    }
  });

  // DESIGN 1.18 第 12 条「不等即回答「库存已变动」并返回当前的当日可用库存、不写入」、UX「服务端回答「库存已变动」时显示 [admin.stock_changed]（{count} 为
  // 服务端返回的当前值），该行改显示当前值、输入框保留，管理员核对后可再次提交」与本任务验收「changed 显示 admin.stock_changed（{count} 为返回的当前值），
  // admin.adjust_shown 与该行改为当前值，输入框保留」：409 时把当前值 17 交给页面，提示带 17；渲染为 alert，admin.adjust_shown 为 17、输入框仍为 25；
  // 再次提交时 expected 为 17。
  it("shows the current value when the stock changed and keeps the input", async () => {
    const calls = stubFetch([json(409, { detail: "stock_changed", available_stock: 17 }), json(200, { variant_id: 41, sku: SKU, available_stock: 25 })]);
    const run = recorder();
    await submitAdjust(41, 18, "25", "csrf-1", new AbortController().signal, run.moves);
    expect(run.events).toEqual(["available", "finish"]);
    expect(run.available).toEqual([17]);
    expect(run.finished).toEqual([{ key: "admin.stock_changed", count: 17 }]);
    const state = finishAdjust(filled("25"), { key: "admin.stock_changed", count: 17 });
    expect(state.text).toBe("25");
    for (const language of LANGUAGES) {
      const html = render({ shown: 17, state }, language);
      expect(notices(html)).toEqual([["alert", formatCopy(COPY["admin.stock_changed"][language], { count: 17 })]]);
      expect(textNodes(html)[1]).toBe(formatCopy(COPY["admin.adjust_shown"][language], { count: 17 }));
      expect(attributes(tags(html, "input")[0] ?? "").get("value")).toBe("25");
    }
    await submitAdjust(41, 17, state.text, "csrf-1", new AbortController().signal, recorder().moves);
    expect(sentBody(calls[1])).toEqual({ expected: 17, available_stock: 25 });
  });

  // 本任务验收「invalid 显示 admin.adjust_invalid」「none 照现有写法回到 A01」与 purpose「网络中断显示 common.network_check、保留所填的新值」，其他失败
  // common.error_retry（UX 线框「结果：… / [common.error_retry]」）：422 stock_invalid、401、网络中断、404 variant_not_found 与 500 依次如此，都不改页面
  // 显示的值；401 只回到 A01、不结束提示。
  it("maps the other results", async () => {
    const cases: [Reply, string[], AdjustNotice[]][] = [
      [json(422, { detail: "stock_invalid" }), ["finish"], [{ key: "admin.adjust_invalid" }]],
      [json(401, { detail: "not_authenticated" }), ["login"], []],
      ["network", ["finish"], [{ key: "common.network_check" }]],
      [json(404, { detail: "variant_not_found" }), ["finish"], [{ key: "common.error_retry" }]],
      [json(500, {}), ["finish"], [{ key: "common.error_retry" }]],
    ];
    for (const [reply, events, finished] of cases) {
      stubFetch([reply]);
      const run = recorder();
      await submitAdjust(41, 18, "25", "csrf-1", new AbortController().signal, run.moves);
      expect(run.events).toEqual(events);
      expect(run.finished).toEqual(finished);
      expect(run.available).toEqual([]);
    }
    const results: [StockAdjustResult, ReturnType<typeof adjustStep>][] = [
      [{ kind: "ok", available_stock: 3 }, { available: 3, notice: { key: "admin.adjust_saved" } }],
      [{ kind: "changed", available_stock: 0 }, { available: 0, notice: { key: "admin.stock_changed", count: 0 } }],
      [{ kind: "invalid" }, { available: null, notice: { key: "admin.adjust_invalid" } }],
      [{ kind: "none" }, "login"],
      [{ kind: "csrf" }, "reread"],
      [{ kind: "network" }, { available: null, notice: { key: "common.network_check" } }],
      [{ kind: "failed" }, { available: null, notice: { key: "common.error_retry" } }],
    ];
    for (const [result, step] of results) {
      expect(adjustStep(result)).toEqual(step);
    }
    const kept = finishAdjust(filled("25"), { key: "common.network_check" });
    expect(kept.text).toBe("25");
    expect(notices(render({ state: kept }))).toEqual([["alert", COPY["common.network_check"].en]]);
  });

  // 本任务 purpose「调整的 403 由页面重新读取列表与分类取得新的 CSRF 令牌（编辑区与已填的值、展开区所填的新值保留），显示 common.error_retry」：403 时交给
  // 页面重新读取（带调整区的 signal），取到后以 common.error_retry 结束；该次重新读取网络中断为 common.network_check、其他失败 common.error_retry、401
  // 回到 A01；都不改页面显示的值，所填的新值不变（结束只改提示与进行中）。
  it("lets the page reread after a 403", async () => {
    const outcomes: [RereadOutcome, string[], AdjustNotice[]][] = [
      ["ok", ["reread", "finish"], [{ key: "common.error_retry" }]],
      ["network", ["reread", "finish"], [{ key: "common.network_check" }]],
      ["failed", ["reread", "finish"], [{ key: "common.error_retry" }]],
      ["login", ["reread", "login"], []],
    ];
    for (const [outcome, events, finished] of outcomes) {
      stubFetch([json(403, { detail: "csrf_failed" })]);
      const run = recorder(outcome);
      await submitAdjust(41, 18, "25", "csrf-1", new AbortController().signal, run.moves);
      expect(run.events).toEqual(events);
      expect(run.finished).toEqual(finished);
      expect(run.available).toEqual([]);
    }
    expect(rereadNotice("ok")).toEqual({ key: "common.error_retry" });
    expect(rereadNotice("network")).toEqual({ key: "common.network_check" });
    expect(rereadNotice("failed")).toEqual({ key: "common.error_retry" });
    expect(rereadNotice("login")).toBe("login");
    expect(finishAdjust(filled("25"), { key: "common.error_retry" }).text).toBe("25");
  });

  // 调整区卸载（收起、切换商品或离开页面）时中止请求：signal 中止之后到达的结果不再交给页面、不再结束。
  it("stops updating after the signal is aborted", async () => {
    stubFetch([json(200, { variant_id: 41, sku: SKU, available_stock: 25 })]);
    const controller = new AbortController();
    const run = recorder();
    const pending = submitAdjust(41, 18, "25", "csrf-1", controller.signal, run.moves);
    controller.abort();
    await pending;
    expect(run.events).toEqual([]);
  });

  // 本任务验收「admin.adjust_save 提交中禁用并标 aria-busy」：提交中 admin.adjust_save 禁用、aria-busy 为 true；不在提交中时可用、aria-busy 为 false；
  // common.cancel 一直可用（收起即中止）。
  it.each(LANGUAGES)("disables admin.adjust_save while submitting in %s", (language) => {
    const busy = tags(render({ state: filled("25") }, language), "button");
    expect(busy.map((tag) => [attributes(tag).has("disabled"), attributes(tag).get("aria-busy")])).toEqual([
      [true, "true"],
      [false, undefined],
    ]);
    const idle = tags(render({ state: finishAdjust(filled("25"), { key: "admin.adjust_saved" }) }, language), "button");
    expect(idle.map((tag) => [attributes(tag).has("disabled"), attributes(tag).get("aria-busy")])).toEqual([
      [false, "false"],
      [false, undefined],
    ]);
  });

  // 本任务验收「改输入后收起提示」：changeText 换上原文并收起任一提示；startAdjust 收起上一次的提示、所填的值不变；finishAdjust 只改进行中与提示。
  it("hides the notice when the input changes", () => {
    for (const notice of NOTICES) {
      const shown = finishAdjust(filled("25"), notice);
      expect(changeText(shown, "26")).toEqual({ text: "26", busy: false, notice: null });
      expect(startAdjust(shown)).toEqual({ text: "25", busy: true, notice: null });
      expect(notices(render({ state: changeText(shown, "26") }))).toEqual([]);
    }
    expect(IDLE).toEqual({ text: "", busy: false, notice: null });
  });

  // 本任务验收「页面文字全部来自字典」：各种提示与提交中，看得见或读得出的文字都是字典里当前语言的文案（{sku} 与 {count} 填好后的标题与当前显示、
  // admin.stock_changed 也算）。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = dictionary(language);
    allowed.add(formatCopy(COPY["admin.adjust_title"][language], { sku: SKU }));
    allowed.add(formatCopy(COPY["admin.adjust_shown"][language], { count: 18 }));
    allowed.add(formatCopy(COPY["admin.stock_changed"][language], { count: 17 }));
    const states = [IDLE, filled("25"), ...NOTICES.map((notice) => finishAdjust(filled("25"), notice))];
    for (const state of states) {
      for (const text of visibleTexts(render({ state }, language))) {
        expect(allowed.has(text)).toBe(true);
      }
    }
  });

  // 本任务验收「传给事件属性的处理函数不返回 Promise」与 UX「[admin.adjust_save] 立即提交…不需要 [common.save]」：输入框的 onChange 交出原文，在输入框里按
  // Enter 阻止表单的默认提交（不触发编辑区的 common.save）并等同 admin.adjust_save，其他键不提交；两个按钮的 onClick 依次为 onSave 与 onCancel；
  // 这些处理函数都返回 undefined。
  it("wires the input and buttons without returning promises", () => {
    const texts: string[] = [];
    const events: string[] = [];
    const tree = viewTree(
      props({
        onText: (text) => {
          texts.push(text);
        },
        onSave: () => {
          events.push("save");
        },
        onCancel: () => {
          events.push("cancel");
        },
      }),
    );
    const input = tree.find((node) => node.type === "input");
    const onChange = input?.props.onChange as (event: { target: { value: string } }) => unknown;
    expect(onChange({ target: { value: "12" } })).toBeUndefined();
    expect(texts).toEqual(["12"]);
    const onKeyDown = input?.props.onKeyDown as (event: { key: string; preventDefault: () => void }) => unknown;
    const prevented: string[] = [];
    expect(onKeyDown({ key: "Enter", preventDefault: () => prevented.push("Enter") })).toBeUndefined();
    expect(onKeyDown({ key: "5", preventDefault: () => prevented.push("5") })).toBeUndefined();
    expect(prevented).toEqual(["Enter"]);
    expect(events).toEqual(["save"]);
    const buttons = tree.filter((node) => node.type === "button");
    expect(buttons).toHaveLength(2);
    for (const button of buttons) {
      expect((button.props.onClick as () => unknown)()).toBeUndefined();
    }
    expect(events).toEqual(["save", "save", "cancel"]);
  });

  // 同 UX 线框的位置：提示在 admin.adjust_note 之后（A04-images-stock 画在展开区末尾）。
  it("puts the notice after the note", () => {
    const html = render({ state: finishAdjust(filled("25"), { key: "admin.adjust_invalid" }) });
    const nodes = textNodes(html);
    expect(nodes.slice(-2)).toEqual([COPY["admin.adjust_note"].en, COPY["admin.adjust_invalid"].en]);
    expect(element(html, "div", "site-admin-adjust__inputs")).toContain(COPY["admin.adjust_new"].en);
  });
});
