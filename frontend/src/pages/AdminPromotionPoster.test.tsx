import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { PosterSlot, PosterUpload } from "../api/adminPromotionPoster";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import AdminPromotionPoster, {
  finishUpload,
  pickPosterMode,
  POSTER_IDLE,
  posterFileProblem,
  posterUploadStep,
  PromotionPosterView,
  startUpload,
  uploadDisabled,
  uploadPoster,
} from "./AdminPromotionPoster";
import type { PosterMoves, PosterNotice, PosterState, PromotionPosterViewProps } from "./AdminPromotionPoster";
import type { RereadOutcome } from "./AdminStoreLogo";
import { newPromotionForm } from "./promotionForm";

const DESKTOP = `/api/promotion-posters/${"a".repeat(32)}.webp`;
const PHONE = `/api/promotion-posters/${"b".repeat(32)}.webp`;

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
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

// 某个位置（div.site-admin-promo-poster__slot，依次为桌面与手机）的内容：桌面到手机开始为止，手机到两个位置的外框结束（其后为 span）为止。
function slotHtml(html: string, slot: PosterSlot): string {
  const parts = html.split('<div class="site-admin-promo-poster__slot"').slice(1);
  const part = parts[slot === "desktop" ? 0 : 1];
  if (part === undefined) {
    throw new Error(`no ${slot} slot`);
  }
  return slot === "desktop" ? part : part.slice(0, part.indexOf("</div></div><span"));
}

function uploadButton(html: string, slot: PosterSlot): Map<string, string> {
  const tag = tags(html, "button").find((item) => attributes(item).get("name") === `poster_upload_${slot}`);
  if (tag === undefined) {
    throw new Error(`no upload button for ${slot}`);
  }
  return attributes(tag);
}

const COPY_VALUES = newPromotionForm().copy;

// 已有活动（ID 7）、上传图模式、只有桌面图，英文与中文替代文字（中文首尾带空白）。
const BASE = {
  promotionId: 7,
  posters: { desktop: DESKTOP, phone: null },
  mode: "image",
  copy: { ...COPY_VALUES, poster_alt_en: "Sale banner", poster_alt_zh: "  新年特卖  " },
  readOnly: false,
  invalid: () => false,
  onMode: () => undefined,
  onAlt: () => undefined,
  onReload: () => undefined,
} satisfies Omit<PromotionPosterViewProps, "state" | "onFile">;

const VIEW: PromotionPosterViewProps = { ...BASE, state: POSTER_IDLE, onFile: () => undefined };

function render(language: Language, overrides: Partial<PromotionPosterViewProps> = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <PromotionPosterView {...VIEW} {...overrides} />
    </LanguageProvider>,
  );
}

// 桌面位置带 notice 的状态。
function noticed(slot: PosterSlot, notice: PosterNotice): PosterState {
  return finishUpload(POSTER_IDLE, slot, notice);
}

// 记下上传结果交给海报区的处理。
function movesRecorder(outcome: RereadOutcome = "ok") {
  const record = {
    posters: [] as string[],
    finished: [] as (PosterNotice | null)[],
    rereads: 0,
    logins: 0,
  };
  const moves: PosterMoves = {
    poster: (poster) => {
      record.posters.push(poster);
    },
    finish: (notice) => {
      record.finished.push(notice);
    },
    reread: () => {
      record.rereads += 1;
      return Promise.resolve(outcome);
    },
    login: () => {
      record.logins += 1;
    },
  };
  return { record, moves };
}

function file(type: string, size = 1000): Blob {
  return new Blob([new Uint8Array(size)], { type });
}

describe("AdminPromotionPoster", () => {
  // UX 0.20 A12 线框「( ) [admin.poster_template]   ( ) [admin.poster_image]」与「上传图时：[admin.poster_desktop] <缩略图>( [admin.upload] )  [admin.poster_desktop_rules]」
  // 「[admin.poster_phone] <缩略图>( [admin.upload] )  [admin.poster_phone_rules]」「[admin.image_rules]」「[admin.poster_alt]  [common.lang_en][____] [common.lang_zh][____]
  // [common.lang_ms][____]（各 150）」：上传图模式下依次为两个单选、桌面与手机两行（名称、缩略图、admin.upload、规则文字）、admin.image_rules 与三语替代文字（已用字数
  // 「<已用>/150」），文字按当前语言；模板模式下只有两个单选。
  it.each(LANGUAGES)("shows both modes, both images, the rules and the alt text in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const html = render(language);
    expect(textNodes(html)).toEqual([
      t("admin.poster_template"),
      t("admin.poster_image"),
      t("admin.poster_desktop"),
      t("admin.upload"),
      t("admin.poster_desktop_rules"),
      t("admin.poster_phone"),
      t("admin.upload"),
      t("admin.poster_phone_rules"),
      t("admin.image_rules"),
      t("admin.poster_alt"),
      t("common.lang_en"),
      "11/150",
      t("common.lang_zh"),
      "4/150",
      t("common.lang_ms"),
      "0/150",
    ]);
    const radios = tags(html, "input")
      .map(attributes)
      .filter((attrs) => attrs.get("type") === "radio");
    expect(radios.map((attrs) => [attrs.get("name"), attrs.get("value"), attrs.has("checked")])).toEqual([
      ["poster_mode", "template", false],
      ["poster_mode", "image", true],
    ]);
    expect(attributes(tags(html, "div")[1] ?? "").get("aria-label")).toBe(t("admin.promo_poster"));
    expect(tags(html, "input").map((tag) => attributes(tag).get("name"))).toEqual([
      "poster_mode",
      "poster_mode",
      "poster_file_desktop",
      "poster_file_phone",
      "poster_alt_en",
      "poster_alt_zh",
      "poster_alt_ms",
    ]);
    expect(attributes(tags(html, "input").find((tag) => tag.includes('name="poster_alt_zh"')) ?? "").get("value")).toBe("  新年特卖  ");

    const template = render(language, { mode: "template" });
    expect(textNodes(template)).toEqual([t("admin.poster_template"), t("admin.poster_image")]);
    expect(tags(template, "input").map((tag) => attributes(tag).get("value"))).toEqual(["template", "image"]);
    expect(tags(template, "input").map((tag) => attributes(tag).has("checked"))).toEqual([true, false]);
  });

  // UX 0.20 A12 线框「<缩略图>」与说明「海报」「桌面图建议 1,800 × 600 像素（3:1），手机图建议 960 × 1,200 像素（4:5）」，落实写法（2）「缩略图（已保存或刚上传的海报图，
  // 没有时为占位）」与「文件选择只收 image/jpeg、image/png、image/webp」：有图的位置为该图（读屏忽略，名称在旁），没有的为同样位置的空占位；文件输入框隐藏、单选，只收三种格式。
  it("shows the saved images, placeholders and the accepted types", () => {
    const html = render("en", { posters: { desktop: DESKTOP, phone: PHONE } });
    expect(slotHtml(html, "desktop")).toContain(
      `<span class="site-admin-promo-poster__thumb site-admin-promo-poster__thumb--desktop"><img class="site-admin-promo-poster__img" src="${DESKTOP}" alt=""/></span>`,
    );
    expect(slotHtml(html, "phone")).toContain(
      `<span class="site-admin-promo-poster__thumb site-admin-promo-poster__thumb--phone"><img class="site-admin-promo-poster__img" src="${PHONE}" alt=""/></span>`,
    );
    const empty = render("en", { posters: { desktop: null, phone: null } });
    expect(tags(empty, "img")).toEqual([]);
    expect(empty).toContain('<span class="site-admin-promo-poster__thumb site-admin-promo-poster__thumb--desktop"></span>');
    expect(empty).toContain('<span class="site-admin-promo-poster__thumb site-admin-promo-poster__thumb--phone"></span>');
    for (const tag of tags(html, "input").filter((item) => item.includes('type="file"'))) {
      const attrs = attributes(tag);
      expect(attrs.get("accept")).toBe("image/jpeg,image/png,image/webp");
      expect(attrs.has("hidden")).toBe(true);
      expect(attrs.has("multiple")).toBe(false);
    }
  });

  // UX 0.20 A12 说明「海报」「上传即替换该位置的图，上传结果提示沿用 A08 标志图的写法」与落实写法（3）「上传中显示 admin.image_uploading 并禁用该位置的按钮」：
  // 上传中的位置按钮禁用并标 aria-busy，旁边为 admin.image_uploading（role="status"）；另一位置照常可用。
  it.each(LANGUAGES)("shows uploading and disables only that slot in %s", (language) => {
    const html = render(language, { state: startUpload(POSTER_IDLE, "phone") });
    expect(uploadButton(html, "phone").has("disabled")).toBe(true);
    expect(uploadButton(html, "phone").get("aria-busy")).toBe("true");
    expect(uploadButton(html, "desktop").has("disabled")).toBe(false);
    expect(uploadButton(html, "desktop").get("aria-busy")).toBe("false");
    const uploading = `<span class="acs-admin__muted" role="status">${escapeHtml(COPY["admin.image_uploading"][language])}</span>`;
    expect(slotHtml(html, "phone")).toContain(uploading);
    expect(slotHtml(html, "desktop")).not.toContain(uploading);
  });

  // 落实写法（3）「415 显示 admin.image_type_unsupported，413 显示 admin.image_too_large；invalid（poster_invalid）在该位置下方以提示样式显示该位置的规则文字…；
  // ended 与 missing 显示 admin.promo_changed 与 admin.reload；csrf 显示 common.error_retry…；网络中断 common.network_check，其他失败 common.error_retry」与 UX 0.20 A12
  // 说明「海报」「上传结果提示沿用 A08 标志图的写法」：提示在该位置的规则文字之下、另一位置没有；poster_invalid 为该位置自己的规则文字。
  it.each(LANGUAGES)("shows each upload result under its slot in %s", (language) => {
    const t = (key: CopyKey) => escapeHtml(COPY[key][language]);
    const alert = (text: string) => `<div class="acs-admin__alert" role="alert"><span>${text}</span></div>`;
    for (const slot of ["desktop", "phone"] as const) {
      const other = slot === "desktop" ? "phone" : "desktop";
      for (const key of ["admin.image_type_unsupported", "admin.image_too_large", "common.network_check", "common.error_retry"] as const) {
        const html = render(language, { state: noticed(slot, { kind: "alert", key }) });
        expect(slotHtml(html, slot)).toContain(`<span class="acs-admin__muted">${t(slot === "desktop" ? "admin.poster_desktop_rules" : "admin.poster_phone_rules")}</span>${alert(t(key))}`);
        expect(slotHtml(html, other)).not.toContain('role="alert"');
      }
      const rules = slot === "desktop" ? "admin.poster_desktop_rules" : "admin.poster_phone_rules";
      const invalid = render(language, { state: noticed(slot, { kind: "invalid" }) });
      expect(slotHtml(invalid, slot)).toContain(`<span class="acs-admin__muted">${t(rules)}</span>${alert(t(rules))}`);
      expect(slotHtml(invalid, other)).not.toContain('role="alert"');
      const changed = render(language, { state: noticed(slot, { kind: "changed" }) });
      expect(slotHtml(changed, slot)).toContain(
        `<div class="acs-admin__alert site-admin-promo-edit__changed" role="alert"><span>${t("admin.promo_changed")}</span>` +
          `<button class="acs-admin__btn acs-admin__btn--secondary" type="button">${t("admin.reload")}</button></div>`,
      );
    }
  });

  // UX 0.20 A12 说明「海报」「上传即替换该位置的图，上传结果提示沿用 A08 标志图的写法」与落实写法（3）的各种结果：ok 换上返回的海报图；415、413、poster_invalid、ended 与 missing、网络中断与其他失败为对应的提示；csrf 重新读取令牌；401 回到 A01。
  it("maps every upload result to a step", () => {
    const cases: [PosterUpload, ReturnType<typeof posterUploadStep>][] = [
      [{ kind: "ok", poster: DESKTOP }, { poster: DESKTOP }],
      [{ kind: "none" }, "login"],
      [{ kind: "csrf" }, "reread"],
      [{ kind: "type_unsupported" }, { notice: { kind: "alert", key: "admin.image_type_unsupported" } }],
      [{ kind: "too_large" }, { notice: { kind: "alert", key: "admin.image_too_large" } }],
      [{ kind: "invalid" }, { notice: { kind: "invalid" } }],
      [{ kind: "ended" }, { notice: { kind: "changed" } }],
      [{ kind: "missing" }, { notice: { kind: "changed" } }],
      [{ kind: "network" }, { notice: { kind: "alert", key: "common.network_check" } }],
      [{ kind: "failed" }, { notice: { kind: "alert", key: "common.error_retry" } }],
    ];
    for (const [upload, step] of cases) {
      expect(posterUploadStep(upload), upload.kind).toEqual(step);
    }
  });

  // UX 0.20 A12 说明「海报」「上传成功即替换并立即生效…不改变活动的版本号」与落实写法（3）「上传照 A08 标志图的写法：选好文件即上传…成功即替换缩略图」：
  // 先查格式与大小（不通过不发请求），通过时以活动 ID、位置、文件本身与它的类型及 CSRF 令牌上传；
  // 成功先换上返回的海报图再结束；被拒只结束并给提示，不换图。
  it("checks the file, uploads it and settles the result", async () => {
    const signal = new AbortController().signal;
    const upload = vi.fn((): Promise<PosterUpload> => Promise.resolve({ kind: "ok", poster: PHONE }));
    const ok = movesRecorder();
    const png = file("image/png");
    await uploadPoster(7, "phone", png, "csrf-1", signal, ok.moves, upload);
    expect(upload).toHaveBeenCalledWith(7, "phone", png, "image/png", "csrf-1", signal);
    expect(ok.record.posters).toEqual([PHONE]);
    expect(ok.record.finished).toEqual([null]);

    for (const [bad, notice] of [
      [file("image/gif"), { kind: "alert", key: "admin.image_type_unsupported" }],
      [file("image/webp", 5242881), { kind: "alert", key: "admin.image_too_large" }],
    ] as const) {
      const rejected = movesRecorder();
      const never = vi.fn((): Promise<PosterUpload> => Promise.resolve({ kind: "ok", poster: PHONE }));
      await uploadPoster(7, "desktop", bad, "csrf-1", signal, rejected.moves, never);
      expect(never).not.toHaveBeenCalled();
      expect(rejected.record.finished).toEqual([notice]);
      expect(rejected.record.posters).toEqual([]);
    }
    expect(posterFileProblem({ type: "image/jpeg", size: 5242880 })).toBeNull();

    const invalid = movesRecorder();
    await uploadPoster(7, "desktop", file("image/jpeg"), "csrf-1", signal, invalid.moves, () => Promise.resolve({ kind: "invalid" }));
    expect(invalid.record.finished).toEqual([{ kind: "invalid" }]);
    expect(invalid.record.posters).toEqual([]);
  });

  // UX 0.20 A12 说明「海报」「上传结果提示沿用 A08 标志图的写法」与落实写法（3）「csrf 显示 common.error_retry 并重新读取 CSRF 令牌；401 回到 A01」：403 后由页面重新读取，取到与其他失败为 common.error_retry、网络中断为
  // common.network_check（同 A08 标志图）、重新读取答 401 时回到 A01；上传答 401 时直接回到 A01、不结束提示。
  it("rereads the token after csrf and goes to sign-in on 401", async () => {
    const signal = new AbortController().signal;
    for (const [outcome, notice] of [
      ["ok", { kind: "alert", key: "common.error_retry" }],
      ["failed", { kind: "alert", key: "common.error_retry" }],
      ["network", { kind: "alert", key: "common.network_check" }],
    ] as const) {
      const { record, moves } = movesRecorder(outcome);
      await uploadPoster(7, "desktop", file("image/png"), "csrf-1", signal, moves, () => Promise.resolve({ kind: "csrf" }));
      expect(record.rereads, outcome).toBe(1);
      expect(record.finished, outcome).toEqual([notice]);
      expect(record.logins).toBe(0);
    }
    const reread = movesRecorder("login");
    await uploadPoster(7, "desktop", file("image/png"), "csrf-1", signal, reread.moves, () => Promise.resolve({ kind: "csrf" }));
    expect(reread.record.logins).toBe(1);
    expect(reread.record.finished).toEqual([]);
    const none = movesRecorder();
    await uploadPoster(7, "desktop", file("image/png"), "csrf-1", signal, none.moves, () => Promise.resolve({ kind: "none" }));
    expect(none.record.logins).toBe(1);
    expect(none.record.rereads).toBe(0);
    expect(none.record.finished).toEqual([]);
  });

  // UX 0.20 A12 说明「海报」「上传结果提示沿用 A08 标志图的写法」与落实写法（3）照 A08 标志图「卸载时中止进行中的请求」：上传返回前已中止（离开页面或重新读取）时不再更新。
  it("ignores an upload that finishes after leaving", async () => {
    const controller = new AbortController();
    const { record, moves } = movesRecorder();
    await uploadPoster(7, "desktop", file("image/png"), "csrf-1", controller.signal, moves, () => {
      controller.abort();
      return Promise.resolve({ kind: "ok", poster: DESKTOP });
    });
    expect(record).toEqual({ posters: [], finished: [], rereads: 0, logins: 0 });
  });

  // UX 0.20 A12 说明「海报」「新建而尚未保存的活动没有 ID，上传按钮禁用并显示 `[admin.poster_save_first]`；已结束的活动上传按钮禁用」与
  // 落实写法（4）「新建而尚未保存（没有活动 ID）时上传按钮禁用并显示 admin.poster_save_first；已结束的活动上传按钮禁用、单选与替代文字只读」：新建时两个按钮禁用并以
  // aria-describedby 指向唯一一处 admin.poster_save_first；已结束时按钮与单选禁用、替代文字只读，不显示 admin.poster_save_first；其他时候都可用。
  it.each(LANGUAGES)("disables uploads before the first save and when ended in %s", (language) => {
    const saveFirst = escapeHtml(COPY["admin.poster_save_first"][language]);
    const unsaved = render(language, { promotionId: null, posters: { desktop: null, phone: null } });
    const id = uploadButton(unsaved, "desktop").get("aria-describedby") ?? "";
    expect(id).not.toBe("");
    for (const slot of ["desktop", "phone"] as const) {
      expect(uploadButton(unsaved, slot).has("disabled")).toBe(true);
      expect(uploadButton(unsaved, slot).get("aria-describedby")).toBe(id);
    }
    expect(unsaved).toContain(`<span id="${escapeHtml(id)}" class="acs-admin__muted">${saveFirst}</span>`);
    expect(unsaved.split(saveFirst)).toHaveLength(2);
    expect(unsaved.indexOf(saveFirst)).toBeLessThan(unsaved.indexOf(escapeHtml(COPY["admin.image_rules"][language])));
    expect(tags(unsaved, "input").some((tag) => attributes(tag).has("disabled") || attributes(tag).has("readonly"))).toBe(false);

    const ended = render(language, { readOnly: true });
    for (const slot of ["desktop", "phone"] as const) {
      expect(uploadButton(ended, slot).has("disabled")).toBe(true);
      expect(uploadButton(ended, slot).has("aria-describedby")).toBe(false);
    }
    expect(ended).not.toContain(saveFirst);
    const inputs = tags(ended, "input").map(attributes);
    expect(inputs.filter((attrs) => attrs.get("type") === "radio").every((attrs) => attrs.has("disabled"))).toBe(true);
    expect(inputs.filter((attrs) => (attrs.get("name") ?? "").startsWith("poster_alt_")).every((attrs) => attrs.has("readonly"))).toBe(true);
    expect(tags(render(language, { readOnly: true, mode: "template" }), "input").every((tag) => attributes(tag).has("disabled"))).toBe(true);

    const open = render(language);
    expect(uploadButton(open, "desktop").has("disabled")).toBe(false);
    expect(uploadButton(open, "phone").has("disabled")).toBe(false);
    expect(open).not.toContain(saveFirst);

    expect(uploadDisabled(null, false, POSTER_IDLE.desktop)).toBe(true);
    expect(uploadDisabled(7, true, POSTER_IDLE.desktop)).toBe(true);
    expect(uploadDisabled(7, false, startUpload(POSTER_IDLE, "desktop").desktop)).toBe(true);
    expect(uploadDisabled(7, false, startUpload(POSTER_IDLE, "desktop").phone)).toBe(false);
  });

  // UX 0.20 A12 线框「[admin.poster_alt] …（各 150）」与说明「提交前页面先按…文案长度…检查，不合格的字段旁显示 [admin.promo_field_invalid]」：页面判定不合格的替代文字旁
  // 显示 admin.promo_field_invalid，输入框以 aria-describedby 指向它。
  it.each(LANGUAGES)("marks an invalid alt text in %s", (language) => {
    const html = render(language, { invalid: (field) => field === "copy.poster_alt_ms" });
    const ms = attributes(tags(html, "input").find((tag) => tag.includes('name="poster_alt_ms"')) ?? "");
    expect(ms.get("aria-invalid")).toBe("true");
    expect(html).toContain(
      `<span id="${escapeHtml(ms.get("aria-describedby") ?? "")}" class="site-admin-promo-edit__field-invalid">${escapeHtml(COPY["admin.promo_field_invalid"][language])}</span>`,
    );
    expect(html.split(escapeHtml(COPY["admin.promo_field_invalid"][language]))).toHaveLength(2);
  });

  // UX 0.20 A12 线框「( ) [admin.poster_template]   ( ) [admin.poster_image]」与说明「海报」「上传与 A08 的标志图一样不随本页的保存提交」，
  // 落实写法（1）「两个单选改表单装饰的 poster_mode」与（3）「上传…不算未保存的修改」：换模式只改 poster_mode（值不变时返回原对象）；开始与结束上传只改该位置的状态，
  // 开始时收起该位置上一次的提示。
  it("changes the mode and the per-slot upload state", () => {
    const decoration = newPromotionForm().decoration;
    expect(pickPosterMode(decoration, "image")).toEqual({ ...decoration, poster_mode: "image" });
    expect(pickPosterMode(decoration, "template")).toBe(decoration);
    const failed = finishUpload(startUpload(POSTER_IDLE, "desktop"), "desktop", { kind: "invalid" });
    expect(failed).toEqual({ desktop: { busy: false, notice: { kind: "invalid" } }, phone: { busy: false, notice: null } });
    expect(startUpload(failed, "desktop")).toEqual({ desktop: { busy: true, notice: null }, phone: { busy: false, notice: null } });
    expect(startUpload(failed, "phone")).toEqual({ desktop: { busy: false, notice: { kind: "invalid" } }, phone: { busy: true, notice: null } });
  });

  // UX 0.20 A12 说明「海报」「上传结果提示沿用 A08 标志图的写法」与落实写法（3）「上传中…」的初始：容器首次渲染（含服务端渲染）两个位置都没有上传进行中与提示，不发请求。
  it("renders the container idle without any request", () => {
    const upload = vi.fn((): Promise<PosterUpload> => Promise.resolve({ kind: "failed" }));
    const html = renderToStaticMarkup(
      <LanguageProvider storage={storage("en")}>
        <AdminPromotionPoster
          {...BASE}
          csrfToken="csrf-1"
          onPoster={() => undefined}
          onReread={() => Promise.resolve("ok")}
          onLogin={() => undefined}
          upload={upload}
        />
      </LanguageProvider>,
    );
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain('role="status"');
    expect(uploadButton(html, "desktop").has("disabled")).toBe(false);
    expect(upload).not.toHaveBeenCalled();
  });
});
