import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";

import { removeStoreLogo, uploadStoreLogo } from "../api/adminStoreLogo";
import type { LogoRemove, LogoUpload } from "../api/adminStoreLogo";
import { AdminAlert } from "../components/AdminFrame";
import { BRAND } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { IMAGE_TYPES, MAX_IMAGE_BYTES } from "./AdminProductImages";

// A08 后台店铺装修的标志段（docs/UX.md 0.18 A08「标志图」，视觉稿 A08-logo-states、A08-desktop 与 A08-phone；HANDOFF 0.84 第（7）点）：表单里主色之后、
// 首页区块之前，标题 admin.logo，其下当前标志（没有标志或图片加载失败时为文字品牌 BRAND）、admin.upload 与 admin.logo_remove，再下为常显的
// admin.logo_hint、admin.image_rules、admin.logo_rules 与 admin.logo_immediate。上传与移除经 api/adminStoreLogo.ts（SHOP-TASK-214）各自立即生效，
// 不随 common.save 提交（DESIGN 1.20「计价、优惠、积分与库存」第 15 条）；格式、大小、像素与存放由服务端判断，页面只先查格式与大小（同 A04 图片区）。
// 同一时间只进行一项标志操作，进行中与 A08 保存进行中标志段的按钮都禁用；进行中的情况经 onBusy 告诉页面，由页面禁用 common.save。
// 标志段从页面取得当前 logo、加载失败与否与 CSRF 令牌；新的 logo 经 onLogo 交给页面，403 后经 onReread 由页面重新读取装修设置（只取新的令牌与
// logo），401 经 onLogin 回到 A01。卸载（切换语言、403 后重新读取整页或离开页面）时中止进行中的请求。

// 进行中的标志操作：上传与移除。
export type LogoOp = "upload" | "remove";

// 标志段的提示（A08-logo-states）：上传被拒三种、网络中断与其他失败。
export type LogoNotice = "admin.image_type_unsupported" | "admin.image_too_large" | "admin.logo_invalid" | "common.network_check" | "common.error_retry";

// 标志段的状态：busy 为进行中的操作，notice 为最近一次操作的提示，confirm 为移除确认框是否打开。
export interface LogoState {
  busy: LogoOp | null;
  notice: LogoNotice | null;
  confirm: boolean;
}

export const IDLE: LogoState = { busy: null, notice: null, confirm: false };

// 上传前的先查（同 A04 图片区，SHOP-TASK-207）：文件类型不是 JPEG、PNG 或 WebP 为 admin.image_type_unsupported，超过 5,242,880 字节为
// admin.image_too_large（先查类型）；通过为 null。
export function fileProblem(file: Pick<Blob, "type" | "size">): LogoNotice | null {
  if (!IMAGE_TYPES.includes(file.type)) {
    return "admin.image_type_unsupported";
  }
  return file.size > MAX_IMAGE_BYTES ? "admin.image_too_large" : null;
}

// 标志加载失败与否：失败按提供路径记下（brokenLogo），所以 logo 换成另一个路径或变为 null 时不再算失败。
export function logoBroken(logo: string | null, brokenLogo: string | null): boolean {
  return logo !== null && logo === brokenLogo;
}

// 标志段的按钮可用：没有标志操作进行中，且 A08 的保存不在进行中（locked）。
function idle(state: LogoState, locked: boolean): boolean {
  return state.busy === null && !locked;
}

// admin.upload 禁用：不可用时（有标志时上传即替换，不因已有标志禁用）。
export function uploadDisabled(state: LogoState, locked: boolean): boolean {
  return !idle(state, locked);
}

// admin.logo_remove 禁用：没有标志或不可用时。
export function removeDisabled(logo: string | null, state: LogoState, locked: boolean): boolean {
  return logo === null || !idle(state, locked);
}

// 开始一项操作：记下进行中的操作，收起上一次的提示与确认框。
export function startOp(state: LogoState, op: LogoOp): LogoState {
  return { ...state, busy: op, notice: null, confirm: false };
}

// 点 admin.logo_remove：打开移除确认框（还不提交）；有操作进行中时不变。
export function askRemove(state: LogoState): LogoState {
  return state.busy === null ? { ...state, confirm: true } : state;
}

// 确认框的 common.cancel（与 Escape）：关闭确认框，不移除。
export function cancelRemove(state: LogoState): LogoState {
  return { ...state, confirm: false };
}

// 一项操作结束：不再进行中，显示 notice（成功为 null）。
export function finished(state: LogoState, notice: LogoNotice | null): LogoState {
  return { ...state, busy: null, notice, confirm: false };
}

// 请求结果对标志段的作用：logo 为要换上的标志（移除为 null），notice 为提示（当前标志不变）；login 为回到 A01，reread 为 403 后由页面重新读取。
export type LogoChange = { logo: string | null } | { notice: LogoNotice };
export type LogoStep = LogoChange | "login" | "reread";

// 上传的结果：ok 改显示返回的 logo；type_unsupported、too_large 与 invalid 依次为 admin.image_type_unsupported、admin.image_too_large 与
// admin.logo_invalid；网络中断 common.network_check，其他失败（含图片存储不可用）common.error_retry。
export function uploadStep(upload: LogoUpload): LogoStep {
  switch (upload.kind) {
    case "ok":
      return { logo: upload.logo };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "type_unsupported":
      return { notice: "admin.image_type_unsupported" };
    case "too_large":
      return { notice: "admin.image_too_large" };
    case "invalid":
      return { notice: "admin.logo_invalid" };
    case "network":
      return { notice: "common.network_check" };
    case "failed":
      return { notice: "common.error_retry" };
  }
}

// 移除的结果：ok 改显示文字品牌；网络中断 common.network_check，其他失败 common.error_retry。
export function removeStep(remove: LogoRemove): LogoStep {
  switch (remove.kind) {
    case "ok":
      return { logo: null };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "network":
      return { notice: "common.network_check" };
    case "failed":
      return { notice: "common.error_retry" };
  }
}

// 页面在 403 后重新读取装修设置的结果：取到（换了新令牌与当前 logo）、401、网络中断、其他失败。
export type RereadOutcome = "ok" | "login" | "network" | "failed";

// 403 之后的提示：重新读取取到时为 common.error_retry（请再操作一次），网络中断 common.network_check，其他失败 common.error_retry（标志保留当前显示）；
// 401 回到 A01。
export function rereadNotice(outcome: RereadOutcome): LogoNotice | "login" {
  switch (outcome) {
    case "login":
      return "login";
    case "network":
      return "common.network_check";
    case "ok":
    case "failed":
      return "common.error_retry";
  }
}

// 标志段对结果的处理：logo 交给页面换上标志，finish 结束进行中的操作并显示提示，reread 由页面重新读取，login 回到 A01。
export interface LogoMoves {
  logo: (logo: string | null) => void;
  finish: (notice: LogoNotice | null) => void;
  reread: (signal: AbortSignal) => Promise<RereadOutcome>;
  login: () => void;
}

// 写请求之后：401 回到 A01；403 由页面重新读取后照 rereadNotice 提示；其余先换上新的标志再结束。signal 中止之后不再更新。
async function settle(step: LogoStep, signal: AbortSignal, moves: LogoMoves): Promise<void> {
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
      moves.finish(after);
    }
    return;
  }
  if ("logo" in step) {
    moves.logo(step.logo);
    moves.finish(null);
  } else {
    moves.finish(step.notice);
  }
}

// 上传（选好文件之后，调用前标志段已经 startOp）：先照 fileProblem 查，不通过时不发请求、显示对应提示；通过时以文件本身与它的类型上传。
export async function uploadLogo(file: Blob, csrfToken: string, signal: AbortSignal, moves: LogoMoves): Promise<void> {
  const problem = fileProblem(file);
  if (problem !== null) {
    moves.finish(problem);
    return;
  }
  const upload = await uploadStoreLogo(file, file.type, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  await settle(uploadStep(upload), signal, moves);
}

// 确认框的 admin.logo_remove：移除标志。
export async function removeLogo(csrfToken: string, signal: AbortSignal, moves: LogoMoves): Promise<void> {
  const remove = await removeStoreLogo(csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  await settle(removeStep(remove), signal, moves);
}

export interface LogoMarkProps {
  logo: string | null;
  broken: boolean;
  onBroken: (logo: string) => void;
  // 文字品牌的 class（标志段不设，A08 预览缩样为 acs-brand）。
  className?: string;
}

// 当前标志：有标志且未加载失败时为 img（src 为 logo，alt 为 BRAND，加载失败经 onBroken 记下该路径）；否则为文字品牌 BRAND。
// 标志段与 A08 预览缩样共用。
export function LogoMark({ logo, broken, onBroken, className }: LogoMarkProps) {
  if (logo === null || broken) {
    return <span className={className}>{BRAND}</span>;
  }
  return (
    <img
      className="site-admin-logo__img"
      src={logo}
      alt={BRAND}
      onError={() => {
        onBroken(logo);
      }}
    />
  );
}

export interface LogoHandlers {
  onFile: (file: File) => void;
  onAskRemove: () => void;
  onCancelRemove: () => void;
  onConfirmRemove: () => void;
  onBroken: (logo: string) => void;
}

// 移除确认框（照 SHOP-TASK-197 的确认框）：admin.logo_remove_confirm，按钮依次为 common.cancel（次要，打开时取得焦点）与 admin.logo_remove（主要）；
// Escape 等同取消。
function RemoveConfirm({ onCancel, onRemove }: { onCancel: () => void; onRemove: () => void }) {
  const t = useCopy();
  const labelId = useId();
  return (
    <div
      className="site-admin__overlay"
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key === "Escape") {
          onCancel();
        }
      }}
    >
      <div className="acs-admin__panel site-admin__confirm" role="dialog" aria-modal="true" aria-labelledby={labelId}>
        <p id={labelId}>{t("admin.logo_remove_confirm")}</p>
        <div className="site-admin__confirm-actions">
          <button className="acs-admin__btn acs-admin__btn--secondary" type="button" autoFocus onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button className="acs-admin__btn" type="button" onClick={onRemove}>
            {t("admin.logo_remove")}
          </button>
        </div>
      </div>
    </div>
  );
}

export interface StoreLogoViewProps extends LogoHandlers {
  logo: string | null;
  broken: boolean;
  state: LogoState;
  locked: boolean;
}

// 标志段（照 A08-logo-states 的顺序）：legend admin.logo；当前标志（或文字品牌）、admin.upload（点它打开隐藏的单选文件输入框）、admin.logo_remove 与上传中的
// admin.image_uploading；常显的 admin.logo_hint、admin.image_rules、admin.logo_rules（同一段）与 admin.logo_immediate；最后为提示与移除确认框。
export function StoreLogoView({ logo, broken, state, locked, ...handlers }: StoreLogoViewProps) {
  const t = useCopy();
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <fieldset className="acs-admin__panel site-admin-logo">
      <legend>{t("admin.logo")}</legend>
      <div className="site-admin-logo__row">
        <span className="site-admin-logo__current">
          <LogoMark logo={logo} broken={broken} onBroken={handlers.onBroken} />
        </span>
        <span className="site-admin-logo__actions">
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            disabled={uploadDisabled(state, locked)}
            aria-busy={state.busy === "upload"}
            onClick={() => {
              fileInput.current?.click();
            }}
          >
            {t("admin.upload")}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept={IMAGE_TYPES.join(",")}
            hidden
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file !== undefined) {
                handlers.onFile(file);
              }
            }}
          />
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            disabled={removeDisabled(logo, state, locked)}
            onClick={() => {
              handlers.onAskRemove();
            }}
          >
            {t("admin.logo_remove")}
          </button>
          {state.busy === "upload" && (
            <span className="acs-admin__muted" role="status">
              {t("admin.image_uploading")}
            </span>
          )}
        </span>
      </div>
      <span className="acs-admin__muted">
        <span>{t("admin.logo_hint")}</span> <span>{t("admin.image_rules")}</span> <span>{t("admin.logo_rules")}</span>
      </span>
      <span className="acs-admin__muted">{t("admin.logo_immediate")}</span>
      {state.notice !== null && <AdminAlert error={state.notice} />}
      {state.confirm && <RemoveConfirm onCancel={handlers.onCancelRemove} onRemove={handlers.onConfirmRemove} />}
    </fieldset>
  );
}

export interface AdminStoreLogoProps {
  // 当前标志（页面最近一次读取或标志操作给的 logo）；没有标志时为 null。
  logo: string | null;
  // 当前标志加载失败（与预览缩样共用，由页面记下）。
  broken: boolean;
  csrfToken: string;
  // A08 的保存进行中。
  locked: boolean;
  onLogo: (logo: string | null) => void;
  onBroken: (logo: string) => void;
  onReread: (signal: AbortSignal) => Promise<RereadOutcome>;
  onLogin: () => void;
  // 标志操作开始与结束（卸载时为 false）；须为不变的函数，否则每次渲染都会中止进行中的请求。
  onBusy: (busy: boolean) => void;
}

// 标志段的容器：自己保留 LogoState。传给事件属性的处理函数都不返回 Promise。
export default function AdminStoreLogo({ logo, broken, csrfToken, locked, onLogo, onBroken, onReread, onLogin, onBusy }: AdminStoreLogoProps) {
  const [state, setState] = useState<LogoState>(IDLE);
  // 本标志段的请求共用的中止；卸载时中止，并告诉页面不再进行中。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => {
      controller.abort();
      onBusy(false);
    };
  }, [onBusy]);

  const moves: LogoMoves = {
    logo: (next) => {
      onLogo(next);
    },
    finish: (value) => {
      setState((current) => finished(current, value));
      onBusy(false);
    },
    reread: onReread,
    login: onLogin,
  };

  // 开始一项操作：记下进行中并告诉页面；没有挂载完成时为 null（不做任何事）。
  const start = (op: LogoOp): AbortSignal | null => {
    const controller = lifetime.current;
    if (controller === null) {
      return null;
    }
    setState((current) => startOp(current, op));
    onBusy(true);
    return controller.signal;
  };

  return (
    <StoreLogoView
      logo={logo}
      broken={broken}
      state={state}
      locked={locked}
      onFile={(file) => {
        if (uploadDisabled(state, locked)) {
          return;
        }
        const signal = start("upload");
        if (signal !== null) {
          void uploadLogo(file, csrfToken, signal, moves);
        }
      }}
      onAskRemove={() => {
        if (!removeDisabled(logo, state, locked)) {
          setState(askRemove);
        }
      }}
      onCancelRemove={() => {
        setState(cancelRemove);
      }}
      onConfirmRemove={() => {
        if (removeDisabled(logo, state, locked)) {
          setState(cancelRemove);
          return;
        }
        const signal = start("remove");
        if (signal !== null) {
          void removeLogo(csrfToken, signal, moves);
        }
      }}
      onBroken={onBroken}
    />
  );
}
