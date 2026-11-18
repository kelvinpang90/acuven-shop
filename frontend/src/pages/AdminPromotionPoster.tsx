import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";

import { POSTER_SLOTS, uploadPromotionPoster } from "../api/adminPromotionPoster";
import type { PosterSlot, PosterUpload } from "../api/adminPromotionPoster";
import type { PromotionDecoration } from "../api/adminPromotions";
import { LANGUAGE_LABEL } from "../components/AdminFrame";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy } from "../i18n/language";
import { IMAGE_TYPES, MAX_IMAGE_BYTES } from "./AdminProductImages";
import { rereadNotice } from "./AdminStoreLogo";
import type { RereadOutcome } from "./AdminStoreLogo";
import { copyLength, copyMaxLength } from "./promotionForm";
import type { CopyField, PromotionFormCopy } from "./promotionForm";

// 后台促销活动 A12 编辑区的海报区（docs/UX.md 0.20 A12 线框「+---- [admin.promo_poster] ----」一段与说明的「海报」一条，视觉稿 A12-editor-desktop 与
// A12-states；HANDOFF 0.98 落实写法（1）至（4）；DESIGN 1.23「节日促销活动」第 12 条，SHOP-TASK-275）：
// - admin.poster_template 与 admin.poster_image 两个单选改表单装饰的 poster_mode（计入未保存与提交，由编辑页经 onMode 改）。
// - 上传图模式下为桌面与手机两行（名称、缩略图、admin.upload 与该位置的规则文字），其下 admin.image_rules 与 admin.poster_alt 三语输入（表单 copy 的
//   poster_alt_<语言>，已用字数「<已用>/150」，计入未保存与提交）；模板模式下这些都不显示。
// - 上传照 A08 标志图（AdminStoreLogo.tsx）：选好文件即上传（先查格式与大小），经 api/adminPromotionPoster.ts（SHOP-TASK-267）立即生效、不随保存提交、
//   不改版本，所以成功只经 onPoster 换上缩略图，不改表单与基准（不算未保存的修改）。格式、大小、像素与存储由服务端检查；不碰金额与个人资料。
// - 新建而尚未保存（没有活动 ID）时上传按钮禁用并显示 admin.poster_save_first；已结束时上传按钮禁用、单选禁用、替代文字只读。
// 卸载（重新读取、离开页面）或活动 ID 改变时中止进行中的上传。

export const POSTER_MODES = ["template", "image"] as const;
export type PosterMode = (typeof POSTER_MODES)[number];

export const POSTER_MODE_LABEL: Readonly<Record<PosterMode, CopyKey>> = {
  template: "admin.poster_template",
  image: "admin.poster_image",
};

// 每个位置的名称与规则文字（规则文字也是 poster_invalid 时的提示，Kelvin 2026-11-15 决定）。
export const POSTER_SLOT_LABEL: Readonly<Record<PosterSlot, CopyKey>> = {
  desktop: "admin.poster_desktop",
  phone: "admin.poster_phone",
};

export const POSTER_SLOT_RULES: Readonly<Record<PosterSlot, CopyKey>> = {
  desktop: "admin.poster_desktop_rules",
  phone: "admin.poster_phone_rules",
};

// 替代文字的三个字段，顺序同 LANGUAGES。
export type PosterAltField = `poster_alt_${Language}`;

export function posterAltField(language: Language): PosterAltField {
  return `poster_alt_${language}`;
}

// 换海报模式；值不变时返回原对象。
export function pickPosterMode(decoration: PromotionDecoration, mode: PosterMode): PromotionDecoration {
  return decoration.poster_mode === mode ? decoration : { ...decoration, poster_mode: mode };
}

// 一个位置的提示：上传被拒或失败（字典文案）、poster_invalid（该位置的规则文字）、ended 与 missing（admin.promo_changed 与 admin.reload）。
export type PosterNotice =
  | { kind: "alert"; key: "admin.image_type_unsupported" | "admin.image_too_large" | "common.network_check" | "common.error_retry" }
  | { kind: "invalid" }
  | { kind: "changed" };

// 一个位置的状态：busy 为上传进行中，notice 为最近一次上传的提示。
export interface PosterSlotState {
  busy: boolean;
  notice: PosterNotice | null;
}

export type PosterState = Readonly<Record<PosterSlot, PosterSlotState>>;

export const POSTER_IDLE: PosterState = { desktop: { busy: false, notice: null }, phone: { busy: false, notice: null } };

// 开始上传：该位置进行中，收起它上一次的提示；另一位置不变。
export function startUpload(state: PosterState, slot: PosterSlot): PosterState {
  return { ...state, [slot]: { busy: true, notice: null } };
}

// 上传结束：该位置不再进行中，显示 notice（成功为 null）。
export function finishUpload(state: PosterState, slot: PosterSlot, notice: PosterNotice | null): PosterState {
  return { ...state, [slot]: { busy: false, notice } };
}

// 该位置的 admin.upload 禁用：新建尚未保存（没有活动 ID）、已结束，或该位置上传进行中。
export function uploadDisabled(promotionId: number | null, readOnly: boolean, slot: PosterSlotState): boolean {
  return promotionId === null || readOnly || slot.busy;
}

// 上传前的先查（同 A08 标志图的 fileProblem）：类型不是 JPEG、PNG 或 WebP 为 admin.image_type_unsupported，超过 5,242,880 字节为 admin.image_too_large
// （先查类型）；通过为 null。
export function posterFileProblem(file: Pick<Blob, "type" | "size">): PosterNotice | null {
  if (!IMAGE_TYPES.includes(file.type)) {
    return { kind: "alert", key: "admin.image_type_unsupported" };
  }
  return file.size > MAX_IMAGE_BYTES ? { kind: "alert", key: "admin.image_too_large" } : null;
}

// 上传结果之后要做的：换上新的海报图、显示提示、回到 A01，或 403 后重新读取 CSRF 令牌。
export type PosterStep = { poster: string } | { notice: PosterNotice } | "login" | "reread";

// 落实写法（3）：ok 换上返回的海报图；415 admin.image_type_unsupported，413 admin.image_too_large；poster_invalid 为该位置的规则文字；ended 与 missing
// 为 admin.promo_changed 与 admin.reload；csrf 重新读取令牌；401 回到 A01；网络中断 common.network_check，其他失败 common.error_retry。
export function posterUploadStep(upload: PosterUpload): PosterStep {
  switch (upload.kind) {
    case "ok":
      return { poster: upload.poster };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "type_unsupported":
      return { notice: { kind: "alert", key: "admin.image_type_unsupported" } };
    case "too_large":
      return { notice: { kind: "alert", key: "admin.image_too_large" } };
    case "invalid":
      return { notice: { kind: "invalid" } };
    case "ended":
    case "missing":
      return { notice: { kind: "changed" } };
    case "network":
      return { notice: { kind: "alert", key: "common.network_check" } };
    case "failed":
      return { notice: { kind: "alert", key: "common.error_retry" } };
  }
}

// 海报区对上传结果的处理：poster 交给页面换上缩略图，finish 结束该位置的上传并显示提示，reread 由页面重新读取 CSRF 令牌，login 回到 A01。
export interface PosterMoves {
  poster: (poster: string) => void;
  finish: (notice: PosterNotice | null) => void;
  reread: (signal: AbortSignal) => Promise<RereadOutcome>;
  login: () => void;
}

// 选好文件之后（调用前该位置已 startUpload）：先照 posterFileProblem 查，不通过时不发请求；通过时以文件本身与它的类型上传。403 后重新读取令牌，
// 取到与其他失败都为 common.error_retry、网络中断 common.network_check（同 A08 的 rereadNotice）。signal 中止之后不再更新。
export async function uploadPoster(
  promotionId: number,
  slot: PosterSlot,
  file: Blob,
  csrfToken: string,
  signal: AbortSignal,
  moves: PosterMoves,
  upload: typeof uploadPromotionPoster = uploadPromotionPoster,
): Promise<void> {
  const problem = posterFileProblem(file);
  if (problem !== null) {
    moves.finish(problem);
    return;
  }
  const result = await upload(promotionId, slot, file, file.type, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = posterUploadStep(result);
  if (step === "login") {
    moves.login();
    return;
  }
  if (step === "reread") {
    const outcome = await moves.reread(signal);
    if (signal.aborted) {
      return;
    }
    const after = rereadNotice(outcome);
    if (after === "login") {
      moves.login();
    } else {
      moves.finish({ kind: "alert", key: after === "common.network_check" ? "common.network_check" : "common.error_retry" });
    }
    return;
  }
  if ("poster" in step) {
    moves.poster(step.poster);
    moves.finish(null);
  } else {
    moves.finish(step.notice);
  }
}

// 一个字段与其旁的 admin.promo_field_invalid（不合格时），写法同编辑页的字段；输入框以 aria-describedby 指向提示。
function Field({ invalid, children }: { invalid: boolean; children: (describedBy: string | undefined) => ReactNode }) {
  const t = useCopy();
  const id = useId();
  return (
    <div className="site-admin-promo-edit__field">
      {children(invalid ? id : undefined)}
      {invalid && (
        <span id={id} className="site-admin-promo-edit__field-invalid">
          {t("admin.promo_field_invalid")}
        </span>
      )}
    </div>
  );
}

// 位置下方的提示：上传被拒与失败为 AdminAlert 的写法；poster_invalid 以同样的提示样式显示该位置的规则文字；changed 带次要按钮 admin.reload。
function SlotNotice({ slot, notice, onReload }: { slot: PosterSlot; notice: PosterNotice; onReload: () => void }) {
  const t = useCopy();
  switch (notice.kind) {
    case "alert":
    case "invalid":
      return (
        <div className="acs-admin__alert" role="alert">
          <span>{t(notice.kind === "alert" ? notice.key : POSTER_SLOT_RULES[slot])}</span>
        </div>
      );
    case "changed":
      return (
        <div className="acs-admin__alert site-admin-promo-edit__changed" role="alert">
          <span>{t("admin.promo_changed")}</span>
          <button className="acs-admin__btn acs-admin__btn--secondary" type="button" onClick={onReload}>
            {t("admin.reload")}
          </button>
        </div>
      );
  }
}

interface SlotRowProps {
  slot: PosterSlot;
  poster: string | null;
  state: PosterSlotState;
  disabled: boolean;
  // 新建尚未保存时 admin.poster_save_first 的 id（按钮以 aria-describedby 指向它）。
  saveFirstId: string | undefined;
  onFile: (slot: PosterSlot, file: File) => void;
  onReload: () => void;
}

// 一个位置（A12-editor-desktop 的 Desktop image、Phone image）：名称；缩略图（已保存或刚上传的海报图，没有时为同样大小的占位）、admin.upload（点它打开隐藏的
// 文件输入框，只收 JPEG、PNG 与 WebP）与上传中的 admin.image_uploading；该位置的规则文字；最后为该位置的提示。
function SlotRow({ slot, poster, state, disabled, saveFirstId, onFile, onReload }: SlotRowProps) {
  const t = useCopy();
  const labelId = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <div className="site-admin-promo-poster__slot" role="group" aria-labelledby={labelId}>
      <span id={labelId}>{t(POSTER_SLOT_LABEL[slot])}</span>
      <div className="site-admin-promo-poster__row">
        <span className={`site-admin-promo-poster__thumb site-admin-promo-poster__thumb--${slot}`}>
          {poster !== null && <img className="site-admin-promo-poster__img" src={poster} alt="" />}
        </span>
        <button
          className="acs-admin__btn acs-admin__btn--secondary"
          type="button"
          name={`poster_upload_${slot}`}
          disabled={disabled}
          aria-busy={state.busy}
          aria-describedby={saveFirstId}
          onClick={() => {
            fileInput.current?.click();
          }}
        >
          {t("admin.upload")}
        </button>
        <input
          ref={fileInput}
          type="file"
          name={`poster_file_${slot}`}
          accept={IMAGE_TYPES.join(",")}
          hidden
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file !== undefined) {
              onFile(slot, file);
            }
          }}
        />
        {state.busy && (
          <span className="acs-admin__muted" role="status">
            {t("admin.image_uploading")}
          </span>
        )}
      </div>
      <span className="acs-admin__muted">{t(POSTER_SLOT_RULES[slot])}</span>
      {state.notice !== null && <SlotNotice slot={slot} notice={state.notice} onReload={onReload} />}
    </div>
  );
}

export interface PromotionPosterViewProps {
  // 活动的内部 ID；新建尚未保存时为 null。
  promotionId: number | null;
  // 已保存或刚上传的海报图（提供路径），没有时为 null。
  posters: Readonly<Record<PosterSlot, string | null>>;
  // 表单装饰的 poster_mode 与表单 copy（只用替代文字三项）。
  mode: string;
  copy: PromotionFormCopy;
  // 已结束。
  readOnly: boolean;
  // 字段旁要提示的问题（copy.poster_alt_<语言>）。
  invalid: (field: string) => boolean;
  state: PosterState;
  onMode: (mode: PosterMode) => void;
  onAlt: (field: CopyField, value: string) => void;
  onFile: (slot: PosterSlot, file: File) => void;
  onReload: () => void;
}

// 海报区：两个单选（name="poster_mode"）；上传图模式下另有桌面与手机两行、新建尚未保存时的 admin.poster_save_first、admin.image_rules 与 admin.poster_alt
// 三语输入（标签为语言名称，旁为已用字数）。
export function PromotionPosterView({ promotionId, posters, mode, copy, readOnly, invalid, state, onMode, onAlt, onFile, onReload }: PromotionPosterViewProps) {
  const t = useCopy();
  const saveFirstId = useId();
  const altId = useId();
  const unsaved = promotionId === null;
  return (
    <div className="site-admin-promo-poster">
      <div className="site-admin-promo-poster__modes" role="radiogroup" aria-label={t("admin.promo_poster")}>
        {POSTER_MODES.map((option) => (
          <label key={option} className="site-admin-promo-edit__check">
            <input
              type="radio"
              name="poster_mode"
              value={option}
              checked={mode === option}
              disabled={readOnly}
              onChange={() => {
                onMode(option);
              }}
            />
            {t(POSTER_MODE_LABEL[option])}
          </label>
        ))}
      </div>
      {mode === "image" && (
        <>
          <div className="site-admin-promo-poster__slots">
            {POSTER_SLOTS.map((slot) => (
              <SlotRow
                key={slot}
                slot={slot}
                poster={posters[slot]}
                state={state[slot]}
                disabled={uploadDisabled(promotionId, readOnly, state[slot])}
                saveFirstId={unsaved ? saveFirstId : undefined}
                onFile={onFile}
                onReload={onReload}
              />
            ))}
          </div>
          {unsaved && (
            <span id={saveFirstId} className="acs-admin__muted">
              {t("admin.poster_save_first")}
            </span>
          )}
          <span className="acs-admin__muted">{t("admin.image_rules")}</span>
          <div className="site-admin-promo-poster__alt" role="group" aria-labelledby={altId}>
            <span id={altId}>{t("admin.poster_alt")}</span>
            <div className="site-admin-promo-poster__alt-langs">
              {LANGUAGES.map((language) => {
                const field = posterAltField(language);
                const value = copy[field];
                return (
                  <Field key={field} invalid={invalid(`copy.${field}`)}>
                    {(describedBy) => (
                      <label className="acs-admin__field">
                        <span className="site-admin-promo-edit__copy-label">
                          <span>{t(LANGUAGE_LABEL[language])}</span>
                          <span className="acs-admin__muted">{`${String(copyLength(value))}/${String(copyMaxLength(field))}`}</span>
                        </span>
                        <input
                          className="acs-admin__input"
                          name={field}
                          lang={htmlLang(language)}
                          value={value}
                          readOnly={readOnly}
                          aria-invalid={describedBy !== undefined}
                          aria-describedby={describedBy}
                          onChange={(event: ChangeEvent<HTMLInputElement>) => {
                            onAlt(field, event.target.value);
                          }}
                        />
                      </label>
                    )}
                  </Field>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export interface AdminPromotionPosterProps extends Omit<PromotionPosterViewProps, "state" | "onFile"> {
  csrfToken: string;
  // 上传成功：页面换上该活动该位置的海报图（不改表单、基准与版本）。
  onPoster: (promotionId: number, slot: PosterSlot, poster: string) => void;
  // 403 之后由页面重新读取该活动的 CSRF 令牌。
  onReread: (promotionId: number, signal: AbortSignal) => Promise<RereadOutcome>;
  onLogin: () => void;
  // 上传的请求，测试以替身代替。
  upload?: typeof uploadPromotionPoster;
}

// 海报区的容器：自己保留两个位置的上传状态。传给事件属性的处理函数都不返回 Promise。
export default function AdminPromotionPoster({ csrfToken, onPoster, onReread, onLogin, upload = uploadPromotionPoster, ...view }: AdminPromotionPosterProps) {
  const [state, setState] = useState<PosterState>(POSTER_IDLE);
  // 本海报区的上传共用的中止；卸载或活动 ID 改变时中止，并回到没有进行中的上传。
  const lifetime = useRef<AbortController | null>(null);
  const { promotionId, readOnly } = view;

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => {
      controller.abort();
      setState(POSTER_IDLE);
    };
  }, [promotionId]);

  return (
    <PromotionPosterView
      {...view}
      state={state}
      onFile={(slot, file) => {
        const controller = lifetime.current;
        if (promotionId === null || controller === null || uploadDisabled(promotionId, readOnly, state[slot])) {
          return;
        }
        setState((current) => startUpload(current, slot));
        const moves: PosterMoves = {
          poster: (poster) => {
            onPoster(promotionId, slot, poster);
          },
          finish: (notice) => {
            setState((current) => finishUpload(current, slot, notice));
          },
          reread: (signal) => onReread(promotionId, signal),
          login: onLogin,
        };
        void uploadPoster(promotionId, slot, file, csrfToken, controller.signal, moves, upload);
      }}
    />
  );
}
