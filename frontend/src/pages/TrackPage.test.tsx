import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { canLookup, EMPTY_FIELDS, LOOKUP_ERROR, lookupAndGo, TRACK_ORDER_PATH, TrackView } from "./TrackPage";
import type { TrackViewProps } from "./TrackPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ORDER_NUMBER = "B6TN2RJD8K4M0QXZ";
const PHONE = "+447700900124";
const FILLED = { orderNumber: ORDER_NUMBER, phone: PHONE };

function languageStorage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

const noop = () => undefined;

function props(overrides: Partial<TrackViewProps> = {}): TrackViewProps {
  return { fields: EMPTY_FIELDS, busy: false, error: null, onChange: noop, onSubmit: noop, ...overrides };
}

function wrap(children: ReactNode, language: Language): ReactElement {
  return (
    <LanguageProvider storage={languageStorage(language)}>
      <RouterProvider initialPath="/track">{children}</RouterProvider>
    </LanguageProvider>
  );
}

function render(overrides: Partial<TrackViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<TrackView {...props(overrides)} />, language));
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

function inputs(html: string): string[] {
  return [...html.matchAll(/<input\b[^>]*>/g)].map((m) => m[0]);
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：给出一个回答（"network" 则抛出网络错误），记录请求地址。
function stubFetch(reply: Response | "network") {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      urls.push(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      return reply === "network" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
    }),
  );
  return urls;
}

describe("form", () => {
  // SHOP-TASK-028 验收第 4 条「P08 按线框显示 lookup.title、★ lookup.demo_hint、pay.order_no 与订单号输入、lookup.phone 与电话输入、lookup.phone_hint、
  // lookup.submit、lookup.access_note、lookup.privacy_warning」与 UX P08 线框的顺序。
  it.each(LANGUAGES)("shows every element of the wireframe in order in %s", (language) => {
    const html = render({}, language);
    const keys: CopyKey[] = [
      "lookup.title",
      "lookup.demo_hint",
      "pay.order_no",
      "lookup.phone",
      "lookup.phone_hint",
      "lookup.submit",
      "lookup.access_note",
      "lookup.privacy_warning",
    ];
    const positions = keys.map((key) => html.indexOf(`>${escapeHtml(COPY[key][language])}<`));
    for (const [index, position] of positions.entries()) {
      expect(position, keys[index]).toBeGreaterThanOrEqual(0);
    }
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    const [orderInput, phoneInput] = inputs(html);
    expect(html.indexOf(orderInput ?? "")).toBeGreaterThan(positions[2] ?? 0);
    expect(html.indexOf(orderInput ?? "")).toBeLessThan(positions[3] ?? 0);
    expect(html.indexOf(phoneInput ?? "")).toBeGreaterThan(positions[3] ?? 0);
    expect(html.indexOf(phoneInput ?? "")).toBeLessThan(positions[4] ?? 0);
    expect(html).toContain(`<h1 class="acs-display-l">${escapeHtml(COPY["lookup.title"][language])}</h1>`);
  });

  // 同一条：两个输入框各有标签，电话输入框关联 lookup.phone_hint；只有这两个输入框。
  it("labels both inputs", () => {
    const html = render();
    const fields = inputs(html);
    expect(fields).toHaveLength(2);
    for (const field of fields) {
      const id = /\sid="([^"]+)"/.exec(field)?.[1] ?? "";
      expect(html).toContain(`for="${id}"`);
    }
    const hintId = /aria-describedby="([^"]+)"/.exec(fields[1] ?? "")?.[1] ?? "";
    expect(html).toContain(`<span class="acs-field__hint" id="${hintId}">${COPY["lookup.phone_hint"].en}</span>`);
  });

  // SHOP-TASK-028 验收第 2 条「P08 每次打开输入框为空，不预填上一单」与 UX P08 说明：经路由打开 /track 时两个输入框都为空。
  it("opens with empty inputs", () => {
    const html = renderToStaticMarkup(<App initialPath="/track" storage={null} />);
    const fields = inputs(html.slice(html.indexOf("<main"), html.indexOf("</main>")));
    expect(fields).toHaveLength(2);
    for (const field of fields) {
      expect(field).toContain(`value=""`);
    }
    expect(EMPTY_FIELDS).toEqual({ orderNumber: "", phone: "" });
  });

  // SHOP-TASK-028 验收第 2 条「订单号、电话…不进任何路径、查询参数」：输入框不设 name、不让浏览器记住，不用脚本时表单提交也不会把二者带进网址。
  it("never puts the inputs into an address", () => {
    const html = render({ fields: FILLED });
    expect(html).toMatch(/<form class="acs-card site-track__form" method="post">/);
    for (const field of inputs(html)) {
      expect(field).not.toContain("name=");
      expect(field).toMatch(/\sautocomplete="off"/i);
    }
    for (const href of html.matchAll(/(?:href|action)="([^"]*)"/g)) {
      expect(href[1]).not.toContain(ORDER_NUMBER);
      expect(href[1]).not.toContain("7700900124");
    }
  });

  // SHOP-TASK-028 验收第 4 条「提交期间按钮禁用」与「浏览器只要求两项非空，格式由服务端判定」：进行中提交按钮禁用；
  // 两项都非空（不论格式）才提交，任一为空或只有空格时不提交，输入框标 required。
  it("disables the button while busy and only asks for two non-empty values", () => {
    expect(render({ fields: FILLED, busy: true })).toMatch(/<button class="acs-btn acs-btn--primary site-track__submit" type="submit" disabled="">/);
    expect(render({ fields: FILLED })).not.toContain(`disabled=""`);
    for (const field of inputs(render())) {
      expect(field).toContain(`required=""`);
      expect(field).not.toMatch(/pattern=|maxlength=|minlength=|type="(number|email)"/i);
    }
    expect(canLookup(FILLED, false)).toBe(true);
    expect(canLookup({ orderNumber: "x", phone: "1" }, false)).toBe(true);
    expect(canLookup(FILLED, true)).toBe(false);
    expect(canLookup({ orderNumber: "", phone: PHONE }, false)).toBe(false);
    expect(canLookup({ orderNumber: ORDER_NUMBER, phone: "  " }, false)).toBe(false);
  });
});

describe("submitting", () => {
  // SHOP-TASK-028 验收第 4 条「204 时转到 /track/order」：查单通过后转到 P09，网址只是 /track/order，不带订单号或电话。
  it("goes to /track/order when the lookup succeeds", async () => {
    stubFetch(new Response(null, { status: 204 }));
    const go = vi.fn();
    await expect(lookupAndGo(FILLED, go)).resolves.toBeNull();
    expect(go.mock.calls).toEqual([[TRACK_ORDER_PATH]]);
    expect(TRACK_ORDER_PATH).toBe("/track/order");
  });

  // SHOP-TASK-028 验收第 4 条「404 显示 lookup.not_found，429 显示 common.rate_limited，503 显示 common.service_unavailable，网络中断显示 common.network_check，
  // 均留在本页」：四种失败各自对应的文案，且不转页。
  it.each<[Response | "network", CopyKey]>([
    [json(404, { detail: "not_found" }), "lookup.not_found"],
    [json(429, { detail: "rate_limited" }), "common.rate_limited"],
    [json(503, { detail: "service_unavailable" }), "common.service_unavailable"],
    ["network", "common.network_check"],
  ])("stays on the page with the right message (%#)", async (reply, key) => {
    stubFetch(reply);
    const go = vi.fn();
    await expect(lookupAndGo(FILLED, go)).resolves.toBe(key);
    expect(go).not.toHaveBeenCalled();
  });

  // 派生实现约束（实现选择）：其他意外回答（如 5xx）也留在本页，显示 common.error_retry。
  it("shows common.error_retry for other failures", async () => {
    stubFetch(json(500, {}));
    await expect(lookupAndGo(FILLED, vi.fn())).resolves.toBe("common.error_retry");
    expect(LOOKUP_ERROR.failed).toBe("common.error_retry");
  });

  // 同一条「均留在本页且保留已输入内容」与 UX P08 线框「错误：…」在 lookup.privacy_warning 之后：
  // 失败后两个输入框仍是已输入的值，提示在隐私提醒之后，以 role="alert" 读出。
  it.each(LANGUAGES)("keeps the entered values next to the message in %s", (language) => {
    for (const key of Object.values(LOOKUP_ERROR)) {
      const html = render({ fields: FILLED, error: key }, language);
      const [orderInput, phoneInput] = inputs(html);
      expect(orderInput).toContain(`value="${ORDER_NUMBER}"`);
      expect(phoneInput).toContain(`value="${PHONE}"`);
      const message = html.indexOf(escapeHtml(COPY[key][language]));
      expect(message).toBeGreaterThan(html.indexOf(escapeHtml(COPY["lookup.privacy_warning"][language])));
      expect(html).toMatch(/<div class="acs-alert acs-alert--danger" role="alert"><svg[\s\S]*?<\/svg><span>/);
    }
    expect(render({}, language)).not.toContain(`role="alert"`);
  });

  // SHOP-TASK-028 验收第 2 条「订单号、电话…不进任何路径、查询参数、localStorage、sessionStorage 或 cookie，只在页面内存里」：
  // 提交查单的整个过程不写任何浏览器存储、cookie 或历史记录；转页只交给路由一个不带订单号或电话的路径。
  it("keeps the order number and phone out of browser storage", async () => {
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

    const failedUrls = stubFetch(json(404, { detail: "not_found" }));
    await lookupAndGo(FILLED, vi.fn());
    const foundUrls = stubFetch(new Response(null, { status: 204 }));
    const go = vi.fn();
    await lookupAndGo(FILLED, go);

    for (const target of [local, session]) {
      expect(target.setItem).not.toHaveBeenCalled();
    }
    expect(cookieWrites).toEqual([]);
    expect(history.pushState).not.toHaveBeenCalled();
    expect(history.replaceState).not.toHaveBeenCalled();
    expect([...failedUrls, ...foundUrls]).toEqual(["/api/orders/lookup", "/api/orders/lookup"]);
    expect(JSON.stringify(go.mock.calls)).not.toContain(ORDER_NUMBER);
    expect(JSON.stringify(go.mock.calls)).not.toContain("7700900124");
  });
});

describe("dictionary", () => {
  // SHOP-TASK-028 验收第 8 条「页面文字全部来自字典」：各状态下，除访客输入的值外，每段文字都是当前语言的某条字典文案；
  // 页面不显示任何凭据内容或授权剩余时间（第 2 条），所以也没有别的文字。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const patterns = new Set<string>([BRAND, ...Object.values(COPY).map((entry) => entry[language])]);
    const states: Partial<TrackViewProps>[] = [{}, { fields: FILLED, busy: true }, ...Object.values(LOOKUP_ERROR).map((error) => ({ fields: FILLED, error }))];
    for (const state of states) {
      for (const value of visibleTexts(render(state, language))) {
        expect(patterns.has(value), value).toBe(true);
      }
    }
  });
});
