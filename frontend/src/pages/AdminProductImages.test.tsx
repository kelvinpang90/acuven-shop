import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImagesWrite, ImageUpload } from "../api/adminProductImages";
import type { AdminProduct, AdminProductImage } from "../api/adminProducts";
import { CSRF_HEADER } from "../api/pay";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import {
  askRemove,
  cancelRemove,
  fileProblem,
  finished,
  IDLE,
  IMAGE_TYPES,
  MAX_IMAGE_BYTES,
  MAX_IMAGES,
  moveDisabled,
  movedIds,
  moveImage,
  ProductImagesView,
  reloadDisabled,
  reloadImages,
  reloadStep,
  removeDisabled,
  removeImage,
  rereadNotice,
  startOp,
  uploadDisabled,
  uploadImage,
  uploadStep,
  writeStep,
} from "./AdminProductImages";
import type { ImageNotice, ImageOp, ImagesMoves, ImagesState, ProductImagesViewProps, RereadOutcome } from "./AdminProductImages";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 接口顺序的三张图（不按 ID）与重新读取后的两张。
const FIRST: AdminProductImage = { id: 73, storage_ref: "/media/products/73.webp" };
const SECOND: AdminProductImage = { id: 71, storage_ref: "/media/products/71.webp" };
const THIRD: AdminProductImage = { id: 72, storage_ref: "/media/products/72.webp" };
const IMAGES = [FIRST, SECOND, THIRD];
const RELOADED: AdminProductImage[] = [THIRD, { id: 74, storage_ref: "/media/products/74.webp" }];
const EIGHT: AdminProductImage[] = Array.from({ length: 8 }, (_, index) => ({ id: 80 + index, storage_ref: `/media/products/${String(80 + index)}.webp` }));

// 重新读取时接口给的商品（图片为 RELOADED）。
const PRODUCT: AdminProduct = {
  id: 3,
  slug: "crew-neck-tee",
  version: 4,
  category_id: 1,
  name_en: "Crew Neck Tee",
  name_zh: null,
  name_ms: null,
  description_en: "A soft cotton tee.",
  description_zh: null,
  description_ms: null,
  is_active: true,
  max_per_order: 10,
  options: [],
  variants: [{ id: 11, sku: "crew-neck-tee", price_sen: 3900, daily_initial_stock: 20, available_stock: 18, is_active: true, option_value_ids: [] }],
  images: RELOADED,
};

const IMAGES_URL = "/api/admin/products/3/images";

const NOTICES: readonly ImageNotice[] = [
  "admin.image_type_unsupported",
  "admin.image_too_large",
  "admin.images_full",
  "admin.image_invalid",
  "admin.images_changed",
  "common.network_check",
  "common.error_retry",
];
const OPS: readonly ImageOp[] = ["upload", "move", "remove", "reload"];

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
  onMove: () => undefined,
  onAskRemove: () => undefined,
  onCancelRemove: () => undefined,
  onConfirmRemove: () => undefined,
  onReload: () => undefined,
};

function props(change: Partial<ProductImagesViewProps> = {}): ProductImagesViewProps {
  return { productId: 3, images: IMAGES, state: IDLE, locked: false, ...HANDLERS, ...change };
}

function render(change: Partial<ProductImagesViewProps> = {}, language: Language = "en"): string {
  return renderToStaticMarkup(wrap(<ProductImagesView {...props(change)} />, language));
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

// 看得见或读得出的文字：文本节点与读屏标签、提示类属性（缩略图 alt 为空串，不读出，不算）。
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

function hasClass(html: string, className: string): boolean {
  return [...html.matchAll(/\sclass="([^"]*)"/g)].some((m) => (m[1] ?? "").split(/\s+/).includes(className));
}

// 每张缩略图的 li（li 不嵌套）。
function items(html: string): string[] {
  return [...html.matchAll(/<li\b[^>]*>[\s\S]*?<\/li>/g)].map((m) => m[0]);
}

function disabled(tag: string): boolean {
  return attributes(tag).has("disabled");
}

function uploadButton(html: string): string {
  return tags(element(html, "div", "site-admin-images__upload"), "button")[0] ?? "";
}

function dictionary(language: Language): Set<string> {
  const counts = Array.from({ length: MAX_IMAGES + 1 }, (_, count) => formatCopy(COPY["admin.image_count"][language], { count, max: MAX_IMAGES }));
  return new Set<string>([...Object.values(COPY).map((entry) => entry[language]), ...counts]);
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

function viewTree(viewProps: ProductImagesViewProps): AnyElement[] {
  let found: AnyElement[] = [];
  const probe = () => {
    found = expand(createElement(ProductImagesView, viewProps));
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

// fetch 替身：按 URL 各自按顺序给出回答；"network" 表示网络中断。多出的请求以错误拒绝。
function stubFetch(replies: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  const queues = new Map(Object.entries(replies).map(([url, list]) => [url, [...list]]));
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const reply = queues.get(url)?.shift();
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

// 图片区对结果的处理：记下换上的图片、结束时的提示、重新读取（回答 reread 给的结果）与回到 A01。
function recorder(reread: RereadOutcome = "ok") {
  const events: string[] = [];
  const replaced: AdminProductImage[][] = [];
  const moves: ImagesMoves = {
    images: (images) => {
      replaced.push(images);
      events.push("images");
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
  return { moves, events, replaced };
}

function png(size: number): Blob {
  return new Blob([new Uint8Array(size)], { type: "image/png" });
}

describe("AdminProductImages", () => {
  // UX A04「图片」「缩略图按顺序排列，第一张标 [admin.image_cover]」与 HANDOFF 0.82 第（6）点「图片缩略图的 src 为接口给的 storage_ref」、本任务 purpose
  // 「缩略图以 img 显示，src 为接口给的 storage_ref，alt 为空串」：缩略图按接口顺序（不按 ID），admin.image_cover 只在第一张。
  it.each(LANGUAGES)("renders the thumbnails in API order with the cover on the first only in %s", (language) => {
    const list = items(render({}, language));
    expect(list).toHaveLength(3);
    expect(list.map((item) => tags(item, "img").map((tag) => [attributes(tag).get("src"), attributes(tag).get("alt")]))).toEqual([
      [[FIRST.storage_ref, ""]],
      [[SECOND.storage_ref, ""]],
      [[THIRD.storage_ref, ""]],
    ]);
    expect(list.map((item) => textNodes(item))).toEqual([[COPY["admin.image_cover"][language]], [], []]);
    expect(hasClass(list[0] ?? "", "site-admin-images__cover")).toBe(true);
  });

  // UX A04 桌面线框「[admin.product_images]  [admin.image_count]」「( [admin.upload] )  [admin.image_rules] [admin.product_image_rules]」「[admin.images_immediate]」，
  // UX-COPY admin.image_count「{max} 为 8」与本任务验收「admin.image_rules、admin.product_image_rules 与 admin.images_immediate 常显」：文字依次为标题、张数
  // （{count} 为现有张数）、封面、admin.upload 与三条说明；没有提示时没有 alert 与 status。
  it.each(LANGUAGES)("shows the title, the count, upload and the standing notes in order in %s", (language) => {
    expect(textNodes(render({}, language))).toEqual([
      COPY["admin.product_images"][language],
      formatCopy(COPY["admin.image_count"][language], { count: 3, max: 8 }),
      COPY["admin.image_cover"][language],
      COPY["admin.upload"][language],
      COPY["admin.image_rules"][language],
      COPY["admin.product_image_rules"][language],
      COPY["admin.images_immediate"][language],
    ]);
    const html = render({ images: [] }, language);
    expect(textNodes(html)).toEqual([
      COPY["admin.product_images"][language],
      formatCopy(COPY["admin.image_count"][language], { count: 0, max: 8 }),
      COPY["admin.upload"][language],
      COPY["admin.image_rules"][language],
      COPY["admin.product_image_rules"][language],
      COPY["admin.images_immediate"][language],
    ]);
    expect(html).not.toMatch(/role="(alert|status)"/);
    expect(MAX_IMAGES).toBe(8);
  });

  // UX A04「图片」「每张图的 (‹)(›) 前后移动一位（读屏标签 [admin.image_move_earlier]、[admin.image_move_later]；第一张的 (‹)、最后一张的 (›) 照常显示但禁用），
  // (✗) 移除（读屏标签 [admin.image_remove]）」：每张依次为三个读屏标签的按钮（图标不读出），只有第一张的 (‹) 与最后一张的 (›) 禁用；只有一张时两个都禁用。
  it.each(LANGUAGES)("labels the move and remove buttons and disables only the outer moves in %s", (language) => {
    const list = items(render({}, language));
    const labels = [COPY["admin.image_move_earlier"][language], COPY["admin.image_move_later"][language], COPY["admin.image_remove"][language]];
    for (const item of list) {
      expect(tags(item, "button").map((tag) => [attributes(tag).get("aria-label"), attributes(tag).get("type")])).toEqual(labels.map((label) => [label, "button"]));
      expect(tags(item, "svg").every((tag) => attributes(tag).get("aria-hidden") === "true")).toBe(true);
    }
    expect(list.map((item) => tags(item, "button").map(disabled))).toEqual([
      [true, false, false],
      [false, false, false],
      [false, true, false],
    ]);
    expect(items(render({ images: [SECOND] }, language)).map((item) => tags(item, "button").map(disabled))).toEqual([[true, true, false]]);
    expect([0, 1, 2].map((index) => [moveDisabled(index, 3, -1, IDLE, false), moveDisabled(index, 3, 1, IDLE, false)])).toEqual([
      [true, false],
      [false, false],
      [false, true],
    ]);
  });

  // UX A04「图片」「已有 8 张或新建商品尚未保存时按钮禁用，后者旁边显示 [admin.image_save_first]」、线框「新建未保存：( [admin.upload] ) 禁用，旁边
  // [admin.image_save_first]」与本任务 purpose「新建表单（还没保存）时 admin.upload 禁用，旁边显示 admin.image_save_first，张数为 0」：7 张可用；
  // 8 张禁用、不显示 admin.image_save_first；新建表单（商品 ID 为 null）禁用并在旁边显示它，张数为 0。
  it.each(LANGUAGES)("disables upload at 8 images and on the new form, with the save-first note in %s", (language) => {
    const seven = render({ images: EIGHT.slice(0, 7) }, language);
    expect(disabled(uploadButton(seven))).toBe(false);
    const full = render({ images: EIGHT }, language);
    expect(disabled(uploadButton(full))).toBe(true);
    expect(textNodes(full)).not.toContain(COPY["admin.image_save_first"][language]);
    expect(textNodes(full)).toContain(formatCopy(COPY["admin.image_count"][language], { count: 8, max: 8 }));
    const created = render({ productId: null, images: [] }, language);
    expect(disabled(uploadButton(created))).toBe(true);
    expect(textNodes(element(created, "div", "site-admin-images__upload"))).toEqual([COPY["admin.upload"][language], COPY["admin.image_save_first"][language]]);
    expect(textNodes(created)[1]).toBe(formatCopy(COPY["admin.image_count"][language], { count: 0, max: 8 }));
    expect([uploadDisabled(3, 7, IDLE, false), uploadDisabled(3, 8, IDLE, false), uploadDisabled(null, 0, IDLE, false)]).toEqual([false, true, true]);
  });

  // 本任务 purpose「同一时间只进行一项图片操作，进行中图片区的按钮都禁用；商品保存或新建进行中图片区的按钮也都禁用」与 UX A04「上传中按钮禁用并显示
  // [admin.image_uploading]」：上传、调序、移除与重新读取进行中，以及保存进行中（locked），admin.upload 与每张的 (‹)(›)(✗) 都禁用；只有上传中在
  // admin.upload 旁显示 admin.image_uploading（role="status"）并标 aria-busy。
  it.each(LANGUAGES)("disables every image button while an operation or a save is in progress in %s", (language) => {
    const states: [ImagesState, boolean][] = [...OPS.map((op): [ImagesState, boolean] => [startOp(IDLE, op), false]), [IDLE, true]];
    for (const [state, locked] of states) {
      const html = render({ state, locked }, language);
      const buttons = tags(html, "button");
      expect(buttons).toHaveLength(10);
      expect(buttons.every(disabled)).toBe(true);
      const uploading = state.busy === "upload";
      expect(attributes(uploadButton(html)).get("aria-busy")).toBe(String(uploading));
      expect(textNodes(element(html, "div", "site-admin-images__upload"))).toEqual(
        uploading ? [COPY["admin.upload"][language], COPY["admin.image_uploading"][language]] : [COPY["admin.upload"][language]],
      );
      expect(tags(html, "span").some((tag) => attributes(tag).get("role") === "status")).toBe(uploading);
      expect([removeDisabled(state, locked), reloadDisabled(state, locked), uploadDisabled(3, 3, state, locked)]).toEqual([true, true, true]);
    }
    expect([removeDisabled(IDLE, false), reloadDisabled(IDLE, false)]).toEqual([false, false]);
  });

  // UX A04「图片」「页面只做格式与大小的初步检查（不是 JPEG、PNG、WebP 或超过 5 MB 即不上传、直接显示对应提示）」、HANDOFF 0.82「5 MB 指 5,242,880 字节」与本任务
  // purpose「选好即先查类型（文件类型不是这三种显示 admin.image_type_unsupported）与大小（超过 5,242,880 字节显示 admin.image_too_large）」：三种类型
  // 5,242,880 字节都合格，多一字节为 admin.image_too_large；其他类型（含空类型）为 admin.image_type_unsupported，类型不对时先报类型；不合格时不发请求。
  it("checks the type and size before uploading", async () => {
    expect(MAX_IMAGE_BYTES).toBe(5242880);
    expect(IMAGE_TYPES).toEqual(["image/jpeg", "image/png", "image/webp"]);
    for (const type of IMAGE_TYPES) {
      expect(fileProblem({ type, size: 5242880 })).toBeNull();
      expect(fileProblem({ type, size: 0 })).toBeNull();
      expect(fileProblem({ type, size: 5242881 })).toBe("admin.image_too_large");
    }
    for (const type of ["image/gif", "image/heic", "image/svg+xml", "IMAGE/PNG", ""]) {
      expect(fileProblem({ type, size: 10 })).toBe("admin.image_type_unsupported");
      expect(fileProblem({ type, size: 5242881 })).toBe("admin.image_type_unsupported");
    }
    const calls = stubFetch({});
    for (const [file, notice] of [
      [png(5242881), "admin.image_too_large"],
      [new Blob(["GIF89a"], { type: "image/gif" }), "admin.image_type_unsupported"],
    ] as const) {
      const run = recorder();
      await uploadImage(3, file, "csrf-1", new AbortController().signal, run.moves);
      expect(run.events).toEqual([`finish ${notice}`]);
    }
    expect(calls).toEqual([]);
  });

  // 本任务 purpose「通过才以文件本身与文件类型上传」与 HANDOFF 0.82「上传的请求体为文件本身（不用多部分表单）」、UX A04「成功后图片区改显示服务端返回的图片列表」：
  // 恰为 5,242,880 字节的 PNG 以 POST 提交到该商品的图片路径，请求体就是这个文件、Content-Type 为它的类型、带 CSRF 令牌与图片区的 signal；
  // 201 时先换上返回的列表，再结束且没有提示。
  it("uploads the file itself with its type and the CSRF token", async () => {
    const file = png(MAX_IMAGE_BYTES);
    const calls = stubFetch({ [IMAGES_URL]: [json(201, { images: [...IMAGES, RELOADED[1]] })] });
    const controller = new AbortController();
    const run = recorder();
    await uploadImage(3, file, "csrf-1", controller.signal, run.moves);
    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call?.init.method).toBe("POST");
    expect(call?.init.body).toBe(file);
    expect(call?.init.signal).toBe(controller.signal);
    expect(header(call, "Content-Type")).toBe("image/png");
    expect(header(call, CSRF_HEADER)).toBe("csrf-1");
    expect(run.events).toEqual(["images", "finish none"]);
    expect(run.replaced).toEqual([[...IMAGES, RELOADED[1]]]);
  });

  // 本任务验收「上传的 type_unsupported、too_large、full 与 invalid 依次显示 admin.image_type_unsupported、admin.image_too_large、admin.images_full 与
  // admin.image_invalid」「none 照现有写法回到 A01」「csrf、network 与其他失败照 purpose」：uploadStep 的每种结果；经 fetch 替身，415、413、409、422、
  // 500 与网络中断只结束并提示、不换图片，401 回到 A01。
  it("maps every upload result", async () => {
    const steps: [ImageUpload, unknown][] = [
      [{ kind: "ok", images: IMAGES }, { images: IMAGES, notice: null }],
      [{ kind: "none" }, "login"],
      [{ kind: "csrf" }, "reread"],
      [{ kind: "type_unsupported" }, { images: null, notice: "admin.image_type_unsupported" }],
      [{ kind: "too_large" }, { images: null, notice: "admin.image_too_large" }],
      [{ kind: "full" }, { images: null, notice: "admin.images_full" }],
      [{ kind: "invalid" }, { images: null, notice: "admin.image_invalid" }],
      [{ kind: "network" }, { images: null, notice: "common.network_check" }],
      [{ kind: "failed" }, { images: null, notice: "common.error_retry" }],
    ];
    for (const [upload, step] of steps) {
      expect(uploadStep(upload), upload.kind).toEqual(step);
    }
    const replies: [Reply, string[]][] = [
      [json(415, { detail: "image_type_unsupported" }), ["finish admin.image_type_unsupported"]],
      [new Response("<html>413</html>", { status: 413 }), ["finish admin.image_too_large"]],
      [json(409, { detail: "images_full" }), ["finish admin.images_full"]],
      [json(422, { detail: "image_invalid" }), ["finish admin.image_invalid"]],
      [json(503, { detail: "image_store_unavailable" }), ["finish common.error_retry"]],
      ["network", ["finish common.network_check"]],
      [json(401, { detail: "not_authenticated" }), ["login"]],
    ];
    stubFetch({ [IMAGES_URL]: replies.map(([reply]) => reply) });
    for (const [, events] of replies) {
      const run = recorder();
      await uploadImage(3, png(10), "csrf-1", new AbortController().signal, run.moves);
      expect(run.events).toEqual(events);
      expect(run.replaced).toEqual([]);
    }
  });

  // 本任务 purpose「调序提交交换后的全部图片 ID」：(‹) 与前一张、(›) 与后一张交换，提交交换后的全部 ID（按新顺序）；第一张前移与最后一张后移为 null，
  // 不发请求；PUT 的请求体只有 image_ids、带 CSRF 令牌，200 时换上返回的列表。
  it("submits the swapped image ids", async () => {
    expect(movedIds(IMAGES, 1, -1)).toEqual([71, 73, 72]);
    expect(movedIds(IMAGES, 1, 1)).toEqual([73, 72, 71]);
    expect(movedIds(IMAGES, 0, 1)).toEqual([71, 73, 72]);
    expect(movedIds(IMAGES, 2, -1)).toEqual([73, 72, 71]);
    expect(movedIds(IMAGES, 0, -1)).toBeNull();
    expect(movedIds(IMAGES, 2, 1)).toBeNull();
    expect(movedIds(IMAGES, 5, -1)).toBeNull();
    const swapped = [FIRST, THIRD, SECOND];
    const calls = stubFetch({ [IMAGES_URL]: [json(200, { images: swapped })] });
    const run = recorder();
    await moveImage(3, IMAGES, 2, -1, "csrf-1", new AbortController().signal, run.moves);
    expect(calls.map((call) => [call.init.method, JSON.parse(typeof call.init.body === "string" ? call.init.body : "null") as unknown, header(call, CSRF_HEADER)])).toEqual([
      ["PUT", { image_ids: [73, 72, 71] }, "csrf-1"],
    ]);
    expect(run.events).toEqual(["images", "finish none"]);
    expect(run.replaced).toEqual([swapped]);
    const edge = recorder();
    await moveImage(3, IMAGES, 0, -1, "csrf-1", new AbortController().signal, edge.moves);
    expect(edge.events).toEqual(["finish none"]);
    expect(calls).toHaveLength(1);
  });

  // UX A04「图片」「移除前弹确认框 [admin.image_remove_confirm]」、线框「移除图片确认框（0.17）：[admin.image_remove_confirm]  ( [common.cancel] ) ( [admin.image_remove] )」、
  // HANDOFF 0.82 Kelvin 2026-10-30 的决定（4）「移除图片前先确认」与本任务 purpose「移除确认框照 SHOP-TASK-197 的确认框写法（role dialog、aria-modal、
  // aria-labelledby 指向 admin.image_remove_confirm，按钮依次为 common.cancel（打开时取得焦点）与 admin.image_remove），确认后才提交」：点 (✗) 只打开确认框；
  // 确认框的文字与按钮顺序；common.cancel 带 autoFocus 并关闭、Escape 等同取消；确认才 DELETE 该图片。
  it.each(LANGUAGES)("asks before removing and removes only after confirming in %s", async (language) => {
    expect(render({}, language)).not.toContain('role="dialog"');
    const asking = askRemove(IDLE, SECOND.id);
    expect(asking).toEqual({ busy: null, notice: null, confirm: SECOND.id });
    expect(askRemove(startOp(IDLE, "move"), SECOND.id)).toEqual(startOp(IDLE, "move"));
    expect(cancelRemove(asking)).toEqual(IDLE);
    const html = render({ state: asking }, language);
    const dialogTag = tags(html, "div").find((tag) => attributes(tag).get("role") === "dialog") ?? "";
    expect(classes(dialogTag)).toEqual(["acs-admin__panel", "site-admin__confirm"]);
    expect(attributes(dialogTag).get("aria-modal")).toBe("true");
    const labelId = attributes(dialogTag).get("aria-labelledby") ?? "";
    expect(labelId).not.toBe("");
    const dialog = element(html, "div", "site-admin__confirm");
    expect(dialog).toContain(`<p id="${escapeHtml(labelId)}">${escapeHtml(COPY["admin.image_remove_confirm"][language])}</p>`);
    expect(tags(dialog, "button").map((tag) => [attributes(tag).get("type"), classes(tag)])).toEqual([
      ["button", ["acs-admin__btn", "acs-admin__btn--secondary"]],
      ["button", ["acs-admin__btn"]],
    ]);
    expect(textNodes(dialog)).toEqual([COPY["admin.image_remove_confirm"][language], COPY["common.cancel"][language], COPY["admin.image_remove"][language]]);
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
    expect(keyDown({ key: "Enter" })).toBeUndefined();
    expect(onCancelRemove).not.toHaveBeenCalled();
    expect(keyDown({ key: "Escape" })).toBeUndefined();
    expect(onCancelRemove).toHaveBeenCalledTimes(1);
    expect(onConfirmRemove).not.toHaveBeenCalled();
    const calls = stubFetch({ [`${IMAGES_URL}/71`]: [json(200, { images: [FIRST, THIRD] })] });
    const run = recorder();
    await removeImage(3, SECOND.id, "csrf-1", new AbortController().signal, run.moves);
    expect(calls.map((call) => [call.url, call.init.method, call.init.body, header(call, CSRF_HEADER)])).toEqual([[`${IMAGES_URL}/71`, "DELETE", undefined, "csrf-1"]]);
    expect(run.replaced).toEqual([[FIRST, THIRD]]);
    expect(run.events).toEqual(["images", "finish none"]);
  });

  // 本任务验收「调序与移除的 changed 显示 admin.images_changed 与 admin.reload」「none 照现有写法回到 A01」「csrf、network 与其他失败照 purpose」：writeStep 的
  // 每种结果；经 fetch 替身，调序与移除的 409 images_changed、网络中断与 500 只结束并提示、不换图片，401 回到 A01。
  it("maps every reorder and remove result", async () => {
    const steps: [ImagesWrite, unknown][] = [
      [{ kind: "ok", images: IMAGES }, { images: IMAGES, notice: null }],
      [{ kind: "none" }, "login"],
      [{ kind: "csrf" }, "reread"],
      [{ kind: "changed" }, { images: null, notice: "admin.images_changed" }],
      [{ kind: "network" }, { images: null, notice: "common.network_check" }],
      [{ kind: "failed" }, { images: null, notice: "common.error_retry" }],
    ];
    for (const [write, step] of steps) {
      expect(writeStep(write), write.kind).toEqual(step);
    }
    const replies: [Reply, string[]][] = [
      [json(409, { detail: "images_changed" }), ["finish admin.images_changed"]],
      ["network", ["finish common.network_check"]],
      [json(500, {}), ["finish common.error_retry"]],
      [json(401, {}), ["login"]],
    ];
    stubFetch({ [IMAGES_URL]: replies.map(([reply]) => reply), [`${IMAGES_URL}/73`]: replies.map(([reply]) => (reply === "network" ? reply : reply.clone())) });
    for (const [, events] of replies) {
      const move = recorder();
      await moveImage(3, IMAGES, 0, 1, "csrf-1", new AbortController().signal, move.moves);
      expect(move.events).toEqual(events);
      const remove = recorder();
      await removeImage(3, FIRST.id, "csrf-1", new AbortController().signal, remove.moves);
      expect(remove.events).toEqual(events);
      expect([...move.replaced, ...remove.replaced]).toEqual([]);
    }
  });

  // 本任务 purpose「图片操作的 403 由页面重新读取列表与分类取得新的 CSRF 令牌（编辑区与已填的值保留），显示 common.error_retry」（照 204 保存的 403 写法，
  // 该次重新读取网络中断时为 common.network_check、401 回到 A01）：上传、调序与移除的 403 都交给页面重新读取，不换图片；读取后的提示照 rereadNotice。
  it("rereads through the page after a 403 and asks to try again", async () => {
    expect((["ok", "failed", "network", "login"] as const).map(rereadNotice)).toEqual(["common.error_retry", "common.error_retry", "common.network_check", "login"]);
    const forbidden = () => json(403, { detail: "csrf_failed" });
    const outcomes: [RereadOutcome, string][] = [
      ["ok", "finish common.error_retry"],
      ["failed", "finish common.error_retry"],
      ["network", "finish common.network_check"],
      ["login", "login"],
    ];
    stubFetch({ [IMAGES_URL]: outcomes.flatMap(() => [forbidden(), forbidden()]), [`${IMAGES_URL}/73`]: outcomes.map(() => forbidden()) });
    for (const [outcome, last] of outcomes) {
      for (const send of [
        (moves: ImagesMoves) => uploadImage(3, png(10), "csrf-1", new AbortController().signal, moves),
        (moves: ImagesMoves) => moveImage(3, IMAGES, 0, 1, "csrf-1", new AbortController().signal, moves),
        (moves: ImagesMoves) => removeImage(3, FIRST.id, "csrf-1", new AbortController().signal, moves),
      ]) {
        const run = recorder(outcome);
        await send(run.moves);
        expect(run.events).toEqual(["reread", last]);
        expect(run.replaced).toEqual([]);
      }
    }
  });

  // UX A04「图片」「排序被拒…显示 [admin.images_changed] 与 [admin.reload]（重新读取该商品，只替换图片区，编辑区未保存的修改保留）」与本任务 purpose「该次读取为
  // none 时回到 A01，network 显示 common.network_check，其他失败显示 common.error_retry，图片区保留当前列表」：admin.reload 只 GET 该商品，ok 时只把它的图片交给页面
  // （不交出商品的其他内容），其余结果不换图片。
  it("reloads only the images of the product", async () => {
    expect(reloadStep({ kind: "ok", product: PRODUCT })).toEqual({ images: RELOADED, notice: null });
    const calls = stubFetch({
      "/api/admin/products/3": [json(200, PRODUCT), json(401, {}), "network", json(404, { detail: "product_not_found" })],
    });
    const expected: [string[], AdminProductImage[][]][] = [
      [["images", "finish none"], [RELOADED]],
      [["login"], []],
      [["finish common.network_check"], []],
      [["finish common.error_retry"], []],
    ];
    for (const [events, replaced] of expected) {
      const controller = new AbortController();
      const run = recorder();
      await reloadImages(3, controller.signal, run.moves);
      expect(run.events).toEqual(events);
      expect(run.replaced).toEqual(replaced);
      expect(calls[calls.length - 1]?.init.signal).toBe(controller.signal);
    }
    expect(calls.map((call) => [call.url, call.init.method])).toEqual(Array.from({ length: 4 }, () => ["/api/admin/products/3", "GET"]));
  });

  // 本任务验收「各种结果的提示」与视觉稿 A04-images-stock：admin.images_changed 为带次要按钮 admin.reload 的 alert（没有操作进行中时可点），其他提示为
  // AdminAlert（role="alert"）；成功（notice 为 null）不显示提示。
  it.each(LANGUAGES)("shows images_changed with reload and other notices as alerts in %s", (language) => {
    for (const notice of NOTICES) {
      const html = render({ state: finished(IDLE, notice) }, language);
      const alerts = tags(html, "div").filter((tag) => attributes(tag).get("role") === "alert");
      expect(alerts, notice).toHaveLength(1);
      if (notice === "admin.images_changed") {
        const changed = element(html, "div", "site-admin-images__changed");
        expect(textNodes(changed)).toEqual([COPY["admin.images_changed"][language], COPY["admin.reload"][language]]);
        const reload = tags(changed, "button")[0] ?? "";
        expect(classes(reload)).toEqual(["acs-admin__btn", "acs-admin__btn--secondary"]);
        expect(disabled(reload)).toBe(false);
      } else {
        expect(textNodes(element(html, "div", "acs-admin__alert"))).toEqual([COPY[notice][language]]);
        expect(hasClass(html, "site-admin-images__changed")).toBe(false);
      }
    }
    expect(render({ state: finished(startOp(IDLE, "upload"), null) }, language)).not.toMatch(/role="(alert|status)"/);
  });

  // 本任务验收「下一次操作开始时收起上一次的提示」：startOp 收起上一次的提示（与确认框）并记下进行中的操作，页面上不再有提示；finished 结束进行中并显示新的提示。
  it("clears the previous notice when the next operation starts", () => {
    for (const notice of NOTICES) {
      for (const op of OPS) {
        const started = startOp(finished(IDLE, notice), op);
        expect(started).toEqual({ busy: op, notice: null, confirm: null });
        expect(render({ state: started })).not.toContain('role="alert"');
        expect(finished(started, "common.error_retry")).toEqual({ busy: null, notice: "common.error_retry", confirm: null });
      }
    }
  });

  // 本任务 purpose「admin.upload 是按钮，点它打开隐藏的单选文件输入框（accept 为 image/jpeg、image/png 与 image/webp），…之后清空文件输入框」与验收「传给事件属性的
  // 处理函数不返回 Promise」：admin.upload 为 type="button"；其后为隐藏、不带 multiple 的文件输入框；输入框的 change 把所选的第一个文件交给 onFile 并清空输入框，
  // 没有文件时只清空；处理函数都返回 undefined。
  it("hands the chosen file over from a hidden single-file input", () => {
    const html = render();
    const inputs = tags(element(html, "div", "site-admin-images__upload"), "input");
    expect(inputs).toHaveLength(1);
    const input = attributes(inputs[0] ?? "");
    expect([input.get("type"), input.get("accept"), input.has("hidden"), input.has("multiple")]).toEqual(["file", "image/jpeg,image/png,image/webp", true, false]);
    expect(attributes(uploadButton(html)).get("type")).toBe("button");
    const onFile = vi.fn((file: File) => {
      expect(file.name).toBe("tee.png");
    });
    const tree = viewTree(props({ onFile }));
    const fileInput = tree.find((node) => node.type === "input" && node.props.type === "file");
    const change = fileInput?.props.onChange as (event: { target: { files: File[] | null; value: string } }) => unknown;
    const file = new File([new Uint8Array(4)], "tee.png", { type: "image/png" });
    const chosen = { target: { files: [file], value: "C:\\fakepath\\tee.png" } };
    expect(change(chosen)).toBeUndefined();
    expect(onFile).toHaveBeenCalledTimes(1);
    expect(chosen.target.value).toBe("");
    const none = { target: { files: null, value: "x" } };
    expect(change(none)).toBeUndefined();
    expect(onFile).toHaveBeenCalledTimes(1);
    expect(none.target.value).toBe("");
    const upload = tree.find((node) => node.type === "button" && node.props["aria-busy"] !== undefined);
    expect((upload?.props.onClick as () => unknown)()).toBeUndefined();
  });

  // 本任务验收「页面文字全部来自字典」：有图片、8 张、新建表单、上传中、各种提示与确认框打开时，看得见或读得出的每段文字都是当前语言的字典文案
  // （admin.image_count 填好的 0–8 张也算）；缩略图的 alt 为空串。
  it.each(LANGUAGES)("shows only dictionary text in %s", (language) => {
    const allowed = dictionary(language);
    const pages = [
      render({}, language),
      render({ images: EIGHT }, language),
      render({ productId: null, images: [] }, language),
      render({ state: startOp(IDLE, "upload") }, language),
      render({ state: askRemove(IDLE, FIRST.id) }, language),
      ...NOTICES.map((notice) => render({ state: finished(IDLE, notice) }, language)),
    ];
    for (const html of pages) {
      for (const value of visibleTexts(html)) {
        expect(allowed.has(value), value).toBe(true);
      }
      expect(tags(html, "img").every((tag) => attributes(tag).get("alt") === "")).toBe(true);
    }
  });

  // 离开或切换商品（signal 中止）之后到达的结果不再更新图片区，也不交出图片（照页面「signal 中止之后不再更新」的写法）。
  it("ignores results after the signal is aborted", async () => {
    stubFetch({ [IMAGES_URL]: [json(201, { images: IMAGES }), json(200, { images: IMAGES })], "/api/admin/products/3": [json(200, PRODUCT)] });
    for (const send of [
      (signal: AbortSignal, moves: ImagesMoves) => uploadImage(3, png(10), "csrf-1", signal, moves),
      (signal: AbortSignal, moves: ImagesMoves) => moveImage(3, IMAGES, 0, 1, "csrf-1", signal, moves),
      (signal: AbortSignal, moves: ImagesMoves) => reloadImages(3, signal, moves),
    ]) {
      const controller = new AbortController();
      const run = recorder();
      const sending = send(controller.signal, run.moves);
      controller.abort();
      await sending;
      expect(run.events).toEqual([]);
    }
  });
});
