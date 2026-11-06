import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LogoRemove, LogoUpload } from "../api/adminStoreLogo";
import type { AdminStoreDesignDetail } from "../api/adminStoreDesign";
import { CSRF_HEADER } from "../api/pay";
import { BRAND, COPY, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { changeLogo, chooseTheme, formOf, logoRereadStep, rereadForLogo } from "./AdminStoreDesignPage";
import type { DesignChange, DesignState } from "./AdminStoreDesignPage";
import {
  askRemove,
  cancelRemove,
  fileProblem,
  finished,
  IDLE,
  logoBroken,
  removeDisabled,
  removeLogo,
  removeStep,
  rereadNotice,
  startOp,
  StoreLogoView,
  uploadDisabled,
  uploadLogo,
  uploadStep,
} from "./AdminStoreLogo";
import type { LogoMoves, LogoNotice, LogoOp, LogoState, RereadOutcome, StoreLogoViewProps } from "./AdminStoreLogo";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 两个合格的提供路径（isStoreLogoPath）：当前标志与上传后的新标志。
const LOGO = "/api/store-logos/0123456789abcdef0123456789abcdef.webp";
const NEW_LOGO = "/api/store-logos/fedcba9876543210fedcba9876543210.webp";
const LOGO_URL = "/api/admin/store-design/logo";
const DESIGN_URL = "/api/admin/store-design";

const OPS: readonly LogoOp[] = ["upload", "remove"];

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
  onFile: () => undefined,
  onAskRemove: () => undefined,
  onCancelRemove: () => undefined,
  onConfirmRemove: () => undefined,
  onBroken: () => undefined,
};

function props(change: Partial<StoreLogoViewProps> = {}): StoreLogoViewProps {
  return { logo: LOGO, broken: false, state: IDLE, locked: false, ...HANDLERS, ...change };
}

function render(change: Partial<StoreLogoViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<StoreLogoView {...props(change)} />, language));
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
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
  return (attributes(tag).get("class") ?? "").split(/\s+/).filter((name) => name !== "");
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

function disabled(tag: string): boolean {
  return attributes(tag).has("disabled");
}

// 标志段一行里的两个按钮：admin.upload 与 admin.logo_remove（确认框的按钮不算）。
function buttons(html: string): string[] {
  return tags(element(html, "span", "site-admin-logo__actions"), "button");
}

// 同上两个按钮，各自的开始标签与按钮里的文字。
function buttonElements(html: string): { tag: string; text: string[] }[] {
  return [...element(html, "span", "site-admin-logo__actions").matchAll(/(<button\b[^>]*>)([\s\S]*?)<\/button>/g)].map((m) => ({ tag: m[1] ?? "", text: textNodes(m[2] ?? "") }));
}

type AnyElement = ReactElement<Record<string, unknown>>;

// 元素树：展开子组件（直接调用，仍在语言的上下文里），用来取按钮与图片的处理函数（不模拟点击）。
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

function viewTree(viewProps: StoreLogoViewProps): AnyElement[] {
  let found: AnyElement[] = [];
  const probe = () => {
    found = expand(createElement(StoreLogoView, viewProps));
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
function stubFetch(...replies: Reply[]): Call[] {
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

function header(call: Call | undefined, name: string): string | null {
  return new Headers(call?.init.headers).get(name);
}

// 标志段对结果的处理：记下换上的标志、结束时的提示、重新读取（回答 reread 给的结果）与回到 A01。
function recorder(reread: RereadOutcome = "ok") {
  const events: string[] = [];
  const moves: LogoMoves = {
    logo: (logo) => {
      events.push(`logo ${logo ?? "null"}`);
    },
    finish: (notice) => {
      events.push(`finish ${notice ?? "none"}`);
    },
    reread: () => {
      events.push("reread");
      return Promise.resolve(reread);
    },
    login: () => {
      events.push("login");
    },
  };
  return { moves, events };
}

function png(size: number): Blob {
  return new Blob([new Uint8Array(size)], { type: "image/png" });
}

describe("showing the logo", () => {
  // UX 0.18 A08「没有标志时显示文字品牌 ACUVEN SHOP，[admin.logo_remove] 禁用」：没有标志时当前标志处为文字品牌 BRAND（没有 img），admin.upload 可点、
  // admin.logo_remove 禁用。
  it.each(LANGUAGES)("shows the text brand and disables remove without a logo in %s", (language) => {
    const html = render({ logo: null }, language);
    expect(tags(html, "img")).toEqual([]);
    expect(element(html, "span", "site-admin-logo__current")).toBe(`<span class="site-admin-logo__current"><span>${BRAND}</span></span>`);
    expect(BRAND).toBe("ACUVEN SHOP");
    expect(buttonElements(html).map(({ tag, text }) => [text, disabled(tag)])).toEqual([
      [[COPY["admin.upload"][language]], false],
      [[COPY["admin.logo_remove"][language]], true],
    ]);
    expect([removeDisabled(null, IDLE, false), uploadDisabled(IDLE, false)]).toEqual([true, false]);
  });

  // HANDOFF 0.84 第（7）点与本任务 purpose「标志以 img 显示，src 为读取或上传返回的 logo，alt 为文字品牌 ACUVEN SHOP（copy.ts 的 BRAND）」：有标志时当前标志处
  // 只有一张 img（src 为 logo、alt 为 BRAND），没有文字品牌的文本；admin.logo_remove 可点（UX 0.18 A08「有标志时 [admin.upload] 即替换，不另确认」，上传也可点）。
  it.each(LANGUAGES)("shows the logo as an image with the brand as its text in %s", (language) => {
    const html = render({}, language);
    const current = element(html, "span", "site-admin-logo__current");
    expect(tags(current, "img").map((tag) => [classes(tag), attributes(tag).get("src"), attributes(tag).get("alt")])).toEqual([[["site-admin-logo__img"], LOGO, BRAND]]);
    expect(textNodes(current)).toEqual([]);
    expect(buttons(html).map(disabled)).toEqual([false, false]);
  });

  // HANDOFF 0.84 第（7）点「没有标志或图片加载失败时显示文字品牌」与本任务验收「当前标志与预览缩样共用一个加载失败状态，logo 换成另一个路径或变为 null 时重置」：
  // 加载失败（broken）时显示文字品牌、没有 img，admin.logo_remove 仍可点（标志仍在）；失败按路径记下，换成另一个路径或 null 时不再算失败。
  it("falls back to the text brand when the image fails to load", () => {
    const html = render({ broken: true });
    expect(tags(html, "img")).toEqual([]);
    expect(textNodes(element(html, "span", "site-admin-logo__current"))).toEqual([BRAND]);
    expect(buttons(html).map(disabled)).toEqual([false, false]);
    expect(logoBroken(LOGO, LOGO)).toBe(true);
    expect(logoBroken(NEW_LOGO, LOGO)).toBe(false);
    expect(logoBroken(null, LOGO)).toBe(false);
    expect(logoBroken(LOGO, null)).toBe(false);
    // img 的 onError 把加载失败的路径交给 onBroken。
    const onBroken = vi.fn<(logo: string) => void>();
    const image = viewTree(props({ onBroken })).find((node) => node.type === "img");
    (image?.props.onError as () => void)();
    expect(onBroken).toHaveBeenCalledWith(LOGO);
  });

  // UX 0.18 A08 桌面线框「[admin.logo] / <当前标志或文字品牌> ( [admin.upload] ) ([admin.logo_remove]) / [admin.logo_hint] [admin.image_rules] /
  // [admin.logo_rules] [admin.logo_immediate]」与本任务验收「admin.logo_hint、admin.image_rules、admin.logo_rules 与 admin.logo_immediate 常显（顺序照视觉稿）」：
  // legend 为 admin.logo，文字依次为当前标志、两个按钮与四条说明；前三条说明同一段（A08-logo-states），admin.logo_immediate 另一段；没有提示时没有 alert 与 status。
  it.each(LANGUAGES)("shows the title, the buttons and the standing notes in order in %s", (language) => {
    const html = render({ logo: null }, language);
    expect(tags(html, "fieldset").map(classes)).toEqual([["acs-admin__panel", "site-admin-logo"]]);
    expect(html).toContain(`<legend>${escapeHtml(COPY["admin.logo"][language])}</legend>`);
    expect(textNodes(html)).toEqual([
      COPY["admin.logo"][language],
      BRAND,
      COPY["admin.upload"][language],
      COPY["admin.logo_remove"][language],
      COPY["admin.logo_hint"][language],
      COPY["admin.image_rules"][language],
      COPY["admin.logo_rules"][language],
      COPY["admin.logo_immediate"][language],
    ]);
    const notes = [...html.matchAll(/<span class="acs-admin__muted">([\s\S]*?)<\/span>(?=<span class="acs-admin__muted">|<\/fieldset>)/g)].map((m) => textNodes(m[1] ?? ""));
    expect(notes).toEqual([
      [COPY["admin.logo_hint"][language], COPY["admin.image_rules"][language], COPY["admin.logo_rules"][language]],
      [COPY["admin.logo_immediate"][language]],
    ]);
    expect(html).not.toMatch(/role="(alert|status|dialog)"/);
  });

  // 本任务 purpose「admin.upload 是按钮，点它打开隐藏的单选文件输入框（accept 为 image/jpeg、image/png 与 image/webp）」：admin.upload 是 type="button"
  // （不提交 A08 的表单），其后为隐藏、没有 multiple 的文件输入框；按钮的 onClick 打开它（这里只确认它是函数，不模拟点击）。
  it("opens a hidden single-file input from the upload button", () => {
    const html = render();
    const inputs = tags(html, "input").map(attributes);
    expect(inputs).toHaveLength(1);
    expect([inputs[0]?.get("type"), inputs[0]?.get("accept"), inputs[0]?.has("hidden"), inputs[0]?.has("multiple")]).toEqual([
      "file",
      "image/jpeg,image/png,image/webp",
      true,
      false,
    ]);
    expect(buttons(html).map((tag) => attributes(tag).get("type"))).toEqual(["button", "button"]);
    // 选好文件后：交给 onFile，并清空文件输入框（同一文件可再选）。
    const onFile = vi.fn<(file: File) => void>();
    const input = viewTree(props({ onFile })).find((node) => node.type === "input");
    const file = new File([new Uint8Array(4)], "logo.png", { type: "image/png" });
    const target = { files: [file], value: "C:\\fakepath\\logo.png" };
    (input?.props.onChange as (event: { target: typeof target }) => void)({ target });
    expect(onFile).toHaveBeenCalledWith(file);
    expect(target.value).toBe("");
  });
});

describe("busy and locked", () => {
  // 本任务 purpose「同一时间只进行一项标志操作，进行中 admin.upload 与 admin.logo_remove 都禁用；A08 保存进行中标志按钮也都禁用」与 UX 0.18 A08「上传中显示
  // [admin.image_uploading]、按钮禁用」：上传、移除进行中与 A08 保存进行中（locked），两个按钮都禁用；只有上传中在按钮旁显示 admin.image_uploading（role="status"）
  // 并标 aria-busy；当前标志照旧显示（A08-logo-states「上传中…当前标志照旧显示」）。
  it.each(LANGUAGES)("disables both buttons during an operation or a save in %s", (language) => {
    const states: [LogoState, boolean][] = [...OPS.map((op): [LogoState, boolean] => [startOp(IDLE, op), false]), [IDLE, true]];
    for (const [state, locked] of states) {
      const html = render({ state, locked }, language);
      expect(buttons(html).map(disabled)).toEqual([true, true]);
      const uploading = state.busy === "upload";
      expect(attributes(buttons(html)[0] ?? "").get("aria-busy")).toBe(String(uploading));
      expect(textNodes(element(html, "span", "site-admin-logo__actions"))).toEqual(
        uploading
          ? [COPY["admin.upload"][language], COPY["admin.logo_remove"][language], COPY["admin.image_uploading"][language]]
          : [COPY["admin.upload"][language], COPY["admin.logo_remove"][language]],
      );
      expect(html.includes('role="status"')).toBe(uploading);
      expect(tags(element(html, "span", "site-admin-logo__current"), "img").map((tag) => attributes(tag).get("src"))).toEqual([LOGO]);
      expect([uploadDisabled(state, locked), removeDisabled(LOGO, state, locked)]).toEqual([true, true]);
    }
    expect([uploadDisabled(IDLE, false), removeDisabled(LOGO, IDLE, false)]).toEqual([false, false]);
  });

  // 本任务验收「下一次标志操作开始时收起上一次的提示」：开始上传或移除时收起上一次的提示与确认框；结束时显示新的提示（成功为 null）。
  it("clears the last notice when the next operation starts", () => {
    const notices: LogoNotice[] = ["admin.image_type_unsupported", "admin.image_too_large", "admin.logo_invalid", "common.network_check", "common.error_retry"];
    for (const notice of notices) {
      const ended = finished(startOp(IDLE, "upload"), notice);
      expect(ended).toEqual({ busy: null, notice, confirm: false });
      for (const op of OPS) {
        expect(startOp(ended, op)).toEqual({ busy: op, notice: null, confirm: false });
      }
    }
    expect(finished(startOp(IDLE, "remove"), null)).toEqual(IDLE);
  });
});

describe("checking the file", () => {
  // 本任务 purpose「选好即先查类型与大小（同 SHOP-TASK-207：类型不是这三种显示 admin.image_type_unsupported，超过 5,242,880 字节显示 admin.image_too_large）」与
  // UX 0.18 A08「标志图沿用商品图片的格式与大小限制」：三种类型 5,242,880 字节合格，多一字节为 admin.image_too_large；其他类型（含空类型）为
  // admin.image_type_unsupported，类型不对时先报类型。
  it("checks the type and size before uploading", () => {
    for (const type of ["image/jpeg", "image/png", "image/webp"]) {
      expect(fileProblem({ type, size: 5242880 })).toBeNull();
      expect(fileProblem({ type, size: 1 })).toBeNull();
      expect(fileProblem({ type, size: 5242881 })).toBe("admin.image_too_large");
    }
    for (const type of ["image/gif", "image/svg+xml", "image/heic", "IMAGE/PNG", ""]) {
      expect(fileProblem({ type, size: 10 })).toBe("admin.image_type_unsupported");
      expect(fileProblem({ type, size: 5242881 })).toBe("admin.image_type_unsupported");
    }
  });

  // 同一条「通过才…上传」：不合格的文件不发请求，直接以对应提示结束，当前标志不变（没有 logo 一步）。
  it("does not send a file that fails the check", async () => {
    const calls = stubFetch();
    for (const [file, notice] of [
      [png(5242881), "admin.image_too_large"],
      [new Blob(["GIF89a"], { type: "image/gif" }), "admin.image_type_unsupported"],
    ] as const) {
      const run = recorder();
      await uploadLogo(file, "csrf-1", new AbortController().signal, run.moves);
      expect(run.events).toEqual([`finish ${notice}`]);
    }
    expect(calls).toEqual([]);
  });
});

describe("uploading", () => {
  // 本任务 purpose「通过才以文件本身与文件类型上传」「经 SHOP-TASK-214 的请求」与 UX 0.18 A08「成功后改显示新标志」「有标志时 [admin.upload] 即替换，不另确认」：
  // 合格的文件以 PUT 上传（请求体为该文件本身、Content-Type 为文件类型、带 CSRF 令牌与 signal），ok 时先换上返回的 logo 再结束、没有提示。
  it("uploads the file itself and shows the returned logo", async () => {
    const calls = stubFetch(json(200, { logo: NEW_LOGO }));
    const file = png(5242880);
    const controller = new AbortController();
    const run = recorder();
    await uploadLogo(file, "csrf-1", controller.signal, run.moves);
    expect(calls).toHaveLength(1);
    expect([calls[0]?.url, calls[0]?.init.method, calls[0]?.init.body, calls[0]?.init.signal]).toEqual([LOGO_URL, "PUT", file, controller.signal]);
    expect([header(calls[0], "Content-Type"), header(calls[0], CSRF_HEADER)]).toEqual(["image/png", "csrf-1"]);
    expect(run.events).toEqual([`logo ${NEW_LOGO}`, "finish none"]);
  });

  // UX 0.18 A08「被拒显示 [admin.image_type_unsupported] / [admin.image_too_large] / [admin.logo_invalid]，其他失败（含图片存储不可用）显示 [common.error_retry]，
  // 当前标志不变」与 HANDOFF 0.84 第（7）点「网络中断显示 common.network_check」：上传的各种结果对应的提示，失败时不换标志；401 回到 A01，403 由页面重新读取。
  it("maps every upload result to a notice or a new logo", () => {
    const cases: [LogoUpload, ReturnType<typeof uploadStep>][] = [
      [{ kind: "ok", logo: NEW_LOGO }, { logo: NEW_LOGO }],
      [{ kind: "type_unsupported" }, { notice: "admin.image_type_unsupported" }],
      [{ kind: "too_large" }, { notice: "admin.image_too_large" }],
      [{ kind: "invalid" }, { notice: "admin.logo_invalid" }],
      [{ kind: "failed" }, { notice: "common.error_retry" }],
      [{ kind: "network" }, { notice: "common.network_check" }],
      [{ kind: "none" }, "login"],
      [{ kind: "csrf" }, "reread"],
    ];
    for (const [upload, step] of cases) {
      expect(uploadStep(upload)).toEqual(step);
    }
  });

  // 同一条：服务端拒绝（415、413、422 logo_invalid）、图片存储不可用（503）与网络中断时只以提示结束，不换标志。
  it.each<[Reply, LogoNotice]>([
    [json(415, { detail: "image_type_unsupported" }), "admin.image_type_unsupported"],
    [json(413, { detail: "image_too_large" }), "admin.image_too_large"],
    [json(422, { detail: "logo_invalid" }), "admin.logo_invalid"],
    [json(503, { detail: "image_store_unavailable" }), "common.error_retry"],
    ["network", "common.network_check"],
  ])("keeps the current logo when the upload is refused (%#)", async (reply, notice) => {
    stubFetch(reply);
    const run = recorder();
    await uploadLogo(png(10), "csrf-1", new AbortController().signal, run.moves);
    expect(run.events).toEqual([`finish ${notice}`]);
  });

  // 本任务验收「none 照现有写法回到 A01」：上传得到 401 时回到 A01，不结束为提示、不换标志。
  it("goes to the login page on 401", async () => {
    stubFetch(json(401, { detail: "admin_session_required" }));
    const run = recorder();
    await uploadLogo(png(10), "csrf-1", new AbortController().signal, run.moves);
    expect(run.events).toEqual(["login"]);
  });

  // 本任务 purpose「同一时间只进行一项标志操作」与卸载时中止：signal 中止之后，不论回答什么都不再更新。
  it("ignores an upload after it was aborted", async () => {
    stubFetch(json(200, { logo: NEW_LOGO }));
    const controller = new AbortController();
    const run = recorder();
    const pending = uploadLogo(png(10), "csrf-1", controller.signal, run.moves);
    controller.abort();
    await pending;
    expect(run.events).toEqual([]);
  });
});

describe("removing", () => {
  // UX 0.18 A08「移除标志确认框：[admin.logo_remove_confirm] ( [common.cancel] ) ( [admin.logo_remove] )」与本任务 purpose「移除确认框照 SHOP-TASK-197 的确认框写法
  // （role dialog、aria-modal、aria-labelledby 指向 admin.logo_remove_confirm，按钮依次为 common.cancel（打开时取得焦点）与 admin.logo_remove），确认后才提交」：
  // 点 admin.logo_remove 只打开确认框；确认框的文字与按钮顺序；common.cancel 带 autoFocus，Escape 等同取消。
  it.each(LANGUAGES)("asks before removing in %s", (language) => {
    expect(render({}, language)).not.toContain('role="dialog"');
    const asking = askRemove(IDLE);
    expect(asking).toEqual({ busy: null, notice: null, confirm: true });
    expect(askRemove(startOp(IDLE, "upload"))).toEqual(startOp(IDLE, "upload"));
    expect(cancelRemove(asking)).toEqual(IDLE);
    const html = render({ state: asking }, language);
    const dialogTag = tags(html, "div").find((tag) => attributes(tag).get("role") === "dialog") ?? "";
    expect(classes(dialogTag)).toEqual(["acs-admin__panel", "site-admin__confirm"]);
    expect(attributes(dialogTag).get("aria-modal")).toBe("true");
    const labelId = attributes(dialogTag).get("aria-labelledby") ?? "";
    expect(labelId).not.toBe("");
    const dialog = element(html, "div", "site-admin__confirm");
    expect(dialog).toContain(`<p id="${escapeHtml(labelId)}">${escapeHtml(COPY["admin.logo_remove_confirm"][language])}</p>`);
    expect(tags(dialog, "button").map((tag) => [attributes(tag).get("type"), classes(tag)])).toEqual([
      ["button", ["acs-admin__btn", "acs-admin__btn--secondary"]],
      ["button", ["acs-admin__btn"]],
    ]);
    expect(textNodes(dialog)).toEqual([COPY["admin.logo_remove_confirm"][language], COPY["common.cancel"][language], COPY["admin.logo_remove"][language]]);
    const onCancelRemove = vi.fn(() => undefined);
    const onConfirmRemove = vi.fn(() => undefined);
    const tree = viewTree(props({ state: asking, onCancelRemove, onConfirmRemove }));
    const dialogButtons = tree.filter((node) => node.type === "button" && (node.props.onClick === onCancelRemove || node.props.onClick === onConfirmRemove));
    expect(dialogButtons.map((node) => [node.props.onClick, node.props.autoFocus])).toEqual([
      [onCancelRemove, true],
      [onConfirmRemove, undefined],
    ]);
    const overlay = tree.find((node) => node.props.className === "site-admin__overlay");
    const keyDown = overlay?.props.onKeyDown as (event: { key: string }) => unknown;
    keyDown({ key: "Enter" });
    expect(onCancelRemove).not.toHaveBeenCalled();
    keyDown({ key: "Escape" });
    expect(onCancelRemove).toHaveBeenCalledTimes(1);
    expect(onConfirmRemove).not.toHaveBeenCalled();
  });

  // 同一条「确认后才提交」与 UX 0.18 A08「移除即恢复文字品牌」：确认后以 DELETE 移除（带 CSRF 令牌与 signal、没有请求体），ok 时换上 null（文字品牌）再结束。
  it("removes the logo and shows the text brand", async () => {
    const calls = stubFetch(json(200, { logo: null }));
    const controller = new AbortController();
    const run = recorder();
    await removeLogo("csrf-1", controller.signal, run.moves);
    expect([calls[0]?.url, calls[0]?.init.method, calls[0]?.init.body, calls[0]?.init.signal]).toEqual([LOGO_URL, "DELETE", undefined, controller.signal]);
    expect(header(calls[0], CSRF_HEADER)).toBe("csrf-1");
    expect(run.events).toEqual(["logo null", "finish none"]);
    // 换上 null 之后当前标志为文字品牌、admin.logo_remove 禁用。
    expect(textNodes(element(render({ logo: null }), "span", "site-admin-logo__current"))).toEqual([BRAND]);
  });

  // UX 0.18 A08「移除失败显示 [common.error_retry]，当前标志不变」与 HANDOFF 0.84 第（7）点「网络中断显示 common.network_check」：移除的各种结果。
  it("maps every remove result to a notice or the text brand", async () => {
    const cases: [LogoRemove, ReturnType<typeof removeStep>][] = [
      [{ kind: "ok" }, { logo: null }],
      [{ kind: "failed" }, { notice: "common.error_retry" }],
      [{ kind: "network" }, { notice: "common.network_check" }],
      [{ kind: "none" }, "login"],
      [{ kind: "csrf" }, "reread"],
    ];
    for (const [remove, step] of cases) {
      expect(removeStep(remove)).toEqual(step);
    }
    stubFetch(json(500, {}));
    const run = recorder();
    await removeLogo("csrf-1", new AbortController().signal, run.moves);
    expect(run.events).toEqual(["finish common.error_retry"]);
  });
});

describe("notices", () => {
  // UX 0.18 A08 与 A08-logo-states 的提示：每种提示以 role="alert" 显示在标志段里，当前标志不变。
  it.each(LANGUAGES)("shows each notice as an alert in %s", (language) => {
    const notices: LogoNotice[] = ["admin.image_type_unsupported", "admin.image_too_large", "admin.logo_invalid", "common.network_check", "common.error_retry"];
    for (const notice of notices) {
      const html = render({ state: finished(startOp(IDLE, "upload"), notice) }, language);
      expect(html).toContain(`<div class="acs-admin__alert" role="alert"><span>${escapeHtml(COPY[notice][language])}</span></div>`);
      expect(tags(element(html, "span", "site-admin-logo__current"), "img").map((tag) => attributes(tag).get("src"))).toEqual([LOGO]);
    }
  });

  // 本任务验收「页面文字全部来自字典」：各状态（没有标志、有标志、加载失败、进行中、各提示、确认框）的文字都是当前语言的字典文案或 BRAND。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = new Set<string>([...Object.values(COPY).map((entry) => entry[language]), BRAND]);
    const variants: Partial<StoreLogoViewProps>[] = [
      { logo: null },
      {},
      { broken: true },
      { state: startOp(IDLE, "upload") },
      { state: askRemove(IDLE) },
      { state: finished(IDLE, "admin.logo_invalid") },
      { locked: true },
    ];
    for (const variant of variants) {
      const html = render(variant, language);
      expect(html).not.toMatch(/\s(title|placeholder)=/i);
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
    }
  });
});

describe("after 403", () => {
  // HANDOFF 0.84 第（7）点「标志操作的 403 重新读取装修设置」与本任务 purpose「显示 common.error_retry（该次读取为 none 时回到 A01，network 显示
  // common.network_check，其他失败显示 common.error_retry，标志保留当前显示）」：403 后由页面重新读取，再按读取结果提示或回到 A01；不换标志。
  it.each<[RereadOutcome, string]>([
    ["ok", "finish common.error_retry"],
    ["failed", "finish common.error_retry"],
    ["network", "finish common.network_check"],
    ["login", "login"],
  ])("reads again after 403 and then shows a notice (%s)", async (outcome, last) => {
    const calls = stubFetch(json(403, { detail: "csrf_invalid" }), json(403, { detail: "csrf_invalid" }));
    const upload = recorder(outcome);
    await uploadLogo(png(10), "csrf-1", new AbortController().signal, upload.moves);
    expect(upload.events).toEqual(["reread", last]);
    const remove = recorder(outcome);
    await removeLogo("csrf-1", new AbortController().signal, remove.moves);
    expect(remove.events).toEqual(["reread", last]);
    // 标志段自己不发读取请求（重新读取由页面做）。
    expect(calls.map((call) => call.init.method)).toEqual(["PUT", "DELETE"]);
    expect(rereadNotice(outcome)).toBe(last === "login" ? "login" : last.slice("finish ".length));
  });

  // 同一条「只取新的 CSRF 令牌与当前 logo，表单未保存的选择保留」：页面的重新读取以界面语言 GET 装修设置；取到时只换 base 的 csrf_token 与 logo，表单、
  // 其余设置（含读取里不同的主题）、保存提示都不变；401 为 login，网络中断与其他失败不改页面。
  it("takes only the new CSRF token and logo from the read", async () => {
    const base: AdminStoreDesignDetail = {
      theme: "kopitiam",
      accent: null,
      home_blocks: [
        { block: "hero", visible: true },
        { block: "how", visible: true },
        { block: "featured", visible: true },
        { block: "categories", visible: false },
      ],
      featured: [],
      choices: [],
      logo: LOGO,
      csrf_token: "design-csrf-1",
    };
    const form = chooseTheme(formOf(base), "batik");
    const before: DesignState = { status: "ready", base, form, saving: false, saved: false, alert: "common.error_retry", saveError: "common.network_check" };
    const fresh: AdminStoreDesignDetail = { ...base, theme: "litar", logo: NEW_LOGO, csrf_token: "design-csrf-2" };
    const calls = stubFetch(json(200, fresh));
    const changes: DesignChange[] = [];
    const controller = new AbortController();
    const outcome = await rereadForLogo("ms", controller.signal, (change) => {
      changes.push(change);
    });
    expect(outcome).toBe("ok");
    expect([calls[0]?.url, calls[0]?.init.method, calls[0]?.init.signal]).toEqual([`${DESIGN_URL}?lang=ms`, "GET", controller.signal]);
    expect(changes).toHaveLength(1);
    const after = changes[0]?.(before);
    expect(after).toEqual({ ...before, base: { ...base, logo: NEW_LOGO, csrf_token: "design-csrf-2" } });
    expect(after?.status === "ready" && after.form).toBe(form);
    // 读取到没有标志时同样只换这两项。
    const removed = logoRereadStep({ kind: "ok", design: { ...fresh, logo: null } });
    expect(typeof removed !== "string" && removed(before)).toEqual({ ...before, base: { ...base, logo: null, csrf_token: "design-csrf-2" } });
    expect([logoRereadStep({ kind: "none" }), logoRereadStep({ kind: "network" }), logoRereadStep({ kind: "failed" })]).toEqual(["login", "network", "failed"]);
    // 标志操作成功时只换 logo，令牌不变。
    expect(changeLogo(NEW_LOGO)(before)).toEqual({ ...before, base: { ...base, logo: NEW_LOGO } });
  });
});
