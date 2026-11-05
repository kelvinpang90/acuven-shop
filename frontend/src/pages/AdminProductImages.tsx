import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent, ReactNode } from "react";

import { removeAdminProductImage, reorderAdminProductImages, uploadAdminProductImage } from "../api/adminProductImages";
import type { ImagesWrite, ImageUpload } from "../api/adminProductImages";
import { readAdminProduct } from "../api/adminProducts";
import type { AdminProductImage, ProductRead } from "../api/adminProducts";
import { AdminAlert } from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// A04 后台商品的图片区（docs/UX.md 0.17 A04「图片」，视觉稿 A04-desktop、A04-desktop-new、A04-phone-edit 与 A04-images-stock；HANDOFF 0.82
// 第（6）点）：编辑区里标题 admin.product_images 与 admin.image_count，缩略图按接口顺序（第一张标 admin.image_cover），每张 (‹)(›)(✗)，
// 其下 admin.upload 与常显的 admin.image_rules、admin.product_image_rules、admin.images_immediate。上传、调序与移除经 api/adminProductImages.ts
// （SHOP-TASK-201）各自立即生效，不随 common.save 提交、不改商品的版本号（DESIGN 1.19「计价、优惠、积分与库存」第 13 条）；格式、大小、像素、
// 张数与存放由服务端判断，页面只先查格式与大小。
// 同一时间只进行一项图片操作，进行中与商品保存（或新建）进行中图片区的按钮都禁用；进行中的情况经 onBusy 告诉页面，由页面禁用 common.save。
// 图片区从页面取得商品 ID（新建表单为 null）、当前图片与 CSRF 令牌；返回的图片列表经 onImages 交给页面替换，403 后经 onReread 由页面重新读取
// 列表与分类换新令牌，401 经 onLogin 回到 A01。卸载（切换商品、收起或离开页面）时中止进行中的请求。

// 每件商品至多 8 张（UX A04「图片」与 admin.image_count 的 {max}）。
export const MAX_IMAGES = 8;
// 5 MB 指 5,242,880 字节（HANDOFF 0.82 2026-11-01 登记 SHOP-TASK-187 至 191 一条第（4）点）。
export const MAX_IMAGE_BYTES = 5242880;
// 页面先查的文件类型：JPEG、PNG 与 WebP。
export const IMAGE_TYPES: readonly string[] = ["image/jpeg", "image/png", "image/webp"];

// 进行中的图片操作：上传、调序、移除与 admin.reload 的重新读取。
export type ImageOp = "upload" | "move" | "remove" | "reload";

// 图片区的提示（A04-images-stock）：上传被拒四种、图片已被改动（带 admin.reload）、网络中断与其他失败。
export type ImageNotice =
  | "admin.image_type_unsupported"
  | "admin.image_too_large"
  | "admin.images_full"
  | "admin.image_invalid"
  | "admin.images_changed"
  | "common.network_check"
  | "common.error_retry";

// 图片区的状态：busy 为进行中的操作，notice 为最近一次操作的提示，confirm 为移除确认框所对的图片 ID（没有打开时为 null）。
export interface ImagesState {
  busy: ImageOp | null;
  notice: ImageNotice | null;
  confirm: number | null;
}

export const IDLE: ImagesState = { busy: null, notice: null, confirm: null };

// 上传前的先查：文件类型不是 JPEG、PNG 或 WebP 为 admin.image_type_unsupported，超过 5,242,880 字节为 admin.image_too_large（先查类型）；
// 通过为 null。
export function fileProblem(file: Pick<Blob, "type" | "size">): ImageNotice | null {
  if (!IMAGE_TYPES.includes(file.type)) {
    return "admin.image_type_unsupported";
  }
  return file.size > MAX_IMAGE_BYTES ? "admin.image_too_large" : null;
}

// 调序：第 index 张与前一张（offset 为 -1）或后一张（1）交换后的全部图片 ID；第一张前移、最后一张后移或位置不在列表里时为 null。
export function movedIds(images: readonly AdminProductImage[], index: number, offset: -1 | 1): number[] | null {
  const other = index + offset;
  const current = images[index];
  const neighbour = images[other];
  if (current === undefined || neighbour === undefined) {
    return null;
  }
  const ids = images.map((image) => image.id);
  ids[index] = neighbour.id;
  ids[other] = current.id;
  return ids;
}

// 图片区的按钮可用：没有图片操作进行中，且商品的保存或新建不在进行中（locked）。
function idle(state: ImagesState, locked: boolean): boolean {
  return state.busy === null && !locked;
}

// admin.upload 禁用：新建表单（还没有商品 ID）、已有 8 张或不可用（idle 为假）时。
export function uploadDisabled(productId: number | null, count: number, state: ImagesState, locked: boolean): boolean {
  return productId === null || count >= MAX_IMAGES || !idle(state, locked);
}

// (‹)(›) 禁用：第一张的 (‹)、最后一张的 (›)，以及不可用时。
export function moveDisabled(index: number, count: number, offset: -1 | 1, state: ImagesState, locked: boolean): boolean {
  const other = index + offset;
  return other < 0 || other >= count || !idle(state, locked);
}

// (✗) 与图片区的 admin.reload 禁用：不可用时。
export function removeDisabled(state: ImagesState, locked: boolean): boolean {
  return !idle(state, locked);
}

export function reloadDisabled(state: ImagesState, locked: boolean): boolean {
  return !idle(state, locked);
}

// 开始一项操作：记下进行中的操作，收起上一次的提示与确认框。
export function startOp(state: ImagesState, op: ImageOp): ImagesState {
  return { ...state, busy: op, notice: null, confirm: null };
}

// 点 (✗)：打开该图片的移除确认框（还不提交）；有操作进行中时不变。
export function askRemove(state: ImagesState, imageId: number): ImagesState {
  return state.busy === null ? { ...state, confirm: imageId } : state;
}

// 确认框的 common.cancel（与 Escape）：关闭确认框，不移除。
export function cancelRemove(state: ImagesState): ImagesState {
  return { ...state, confirm: null };
}

// 一项操作结束：不再进行中，显示 notice（成功为 null）。
export function finished(state: ImagesState, notice: ImageNotice | null): ImagesState {
  return { ...state, busy: null, notice, confirm: null };
}

// 请求结果对图片区的作用：images 为要换上的图片列表（不换为 null）、notice 为提示；login 为回到 A01，reread 为 403 后由页面重新读取。
export type ImagesStep = { images: AdminProductImage[] | null; notice: ImageNotice | null } | "login" | "reread";

function notice(value: ImageNotice): ImagesStep {
  return { images: null, notice: value };
}

// 上传的结果：ok 改显示返回的图片列表；type_unsupported、too_large、full 与 invalid 依次为 admin.image_type_unsupported、
// admin.image_too_large、admin.images_full 与 admin.image_invalid；网络中断 common.network_check，其他失败 common.error_retry。
export function uploadStep(upload: ImageUpload): ImagesStep {
  switch (upload.kind) {
    case "ok":
      return { images: upload.images, notice: null };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "type_unsupported":
      return notice("admin.image_type_unsupported");
    case "too_large":
      return notice("admin.image_too_large");
    case "full":
      return notice("admin.images_full");
    case "invalid":
      return notice("admin.image_invalid");
    case "network":
      return notice("common.network_check");
    case "failed":
      return notice("common.error_retry");
  }
}

// 调序与移除的结果：ok 改显示返回的图片列表；changed 为 admin.images_changed（带 admin.reload）；网络中断 common.network_check，
// 其他失败 common.error_retry。
export function writeStep(write: ImagesWrite): ImagesStep {
  switch (write.kind) {
    case "ok":
      return { images: write.images, notice: null };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "changed":
      return notice("admin.images_changed");
    case "network":
      return notice("common.network_check");
    case "failed":
      return notice("common.error_retry");
  }
}

// 图片区 admin.reload 重新读取该商品的结果：ok 只换上读取到的图片；none 回到 A01；网络中断 common.network_check，其他失败 common.error_retry，
// 图片区保留当前列表。
export function reloadStep(read: ProductRead): Exclude<ImagesStep, "reread"> {
  switch (read.kind) {
    case "ok":
      return { images: read.product.images, notice: null };
    case "none":
      return "login";
    case "network":
      return notice("common.network_check");
    case "failed":
      return notice("common.error_retry");
  }
}

// 页面在 403 后重新读取列表与分类的结果：都取到（换了新令牌）、任一 401、否则任一网络中断、其他失败。
export type RereadOutcome = "ok" | "login" | "network" | "failed";

// 403 之后的提示：重新读取取到时为 common.error_retry（请再操作一次），网络中断 common.network_check，其他失败 common.error_retry；401 回到 A01。
export function rereadNotice(outcome: RereadOutcome): ImageNotice | "login" {
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

// 图片区对结果的处理：images 交给页面替换图片，finish 结束进行中的操作并显示提示，reread 由页面重新读取列表与分类，login 回到 A01。
export interface ImagesMoves {
  images: (images: AdminProductImage[]) => void;
  finish: (notice: ImageNotice | null) => void;
  reread: (signal: AbortSignal) => Promise<RereadOutcome>;
  login: () => void;
}

// 写请求之后：401 回到 A01；403 由页面重新读取后照 rereadNotice 提示；其余先换上返回的图片列表再结束。signal 中止之后不再更新。
async function settle(step: ImagesStep, signal: AbortSignal, moves: ImagesMoves): Promise<void> {
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
  if (step.images !== null) {
    moves.images(step.images);
  }
  moves.finish(step.notice);
}

// 上传（选好文件之后，调用前图片区已经 startOp）：先照 fileProblem 查，不通过时不发请求、显示对应提示；通过时以文件本身与它的类型上传。
export async function uploadImage(productId: number, file: Blob, csrfToken: string, signal: AbortSignal, moves: ImagesMoves): Promise<void> {
  const problem = fileProblem(file);
  if (problem !== null) {
    moves.finish(problem);
    return;
  }
  const upload = await uploadAdminProductImage(productId, file, file.type, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  await settle(uploadStep(upload), signal, moves);
}

// (‹)(›)：提交与相邻一张交换后的全部图片 ID（movedIds）；交换不了时不发请求、直接结束。
export async function moveImage(
  productId: number,
  images: readonly AdminProductImage[],
  index: number,
  offset: -1 | 1,
  csrfToken: string,
  signal: AbortSignal,
  moves: ImagesMoves,
): Promise<void> {
  const ids = movedIds(images, index, offset);
  if (ids === null) {
    moves.finish(null);
    return;
  }
  const write = await reorderAdminProductImages(productId, ids, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  await settle(writeStep(write), signal, moves);
}

// 确认框的 admin.image_remove：移除该图片。
export async function removeImage(productId: number, imageId: number, csrfToken: string, signal: AbortSignal, moves: ImagesMoves): Promise<void> {
  const write = await removeAdminProductImage(productId, imageId, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  await settle(writeStep(write), signal, moves);
}

// admin.images_changed 旁的 admin.reload：只重新读取该商品，只换上它的图片（编辑区未保存的修改与 version 不变）。
export async function reloadImages(productId: number, signal: AbortSignal, moves: ImagesMoves): Promise<void> {
  const read = await readAdminProduct(productId, signal);
  if (signal.aborted) {
    return;
  }
  await settle(reloadStep(read), signal, moves);
}

function EarlierIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}

function LaterIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export interface ImagesHandlers {
  onFile: (file: File) => void;
  onMove: (index: number, offset: -1 | 1) => void;
  onAskRemove: (imageId: number) => void;
  onCancelRemove: () => void;
  onConfirmRemove: () => void;
  onReload: () => void;
}

// 每张图下的一个小按钮（(‹)(›)(✗)）：次要按钮，图标不读出，读屏标签为 label。
function IconButton({ label, disabled, onPress, children }: { label: string; disabled: boolean; onPress: () => void; children: ReactNode }) {
  return (
    <button
      className="acs-admin__btn acs-admin__btn--secondary site-admin-images__icon"
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={() => {
        onPress();
      }}
    >
      {children}
    </button>
  );
}

// 移除确认框（照 SHOP-TASK-197 的确认框）：admin.image_remove_confirm，按钮依次为 common.cancel（次要，打开时取得焦点）与 admin.image_remove
// （主要）；Escape 等同取消。
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
        <p id={labelId}>{t("admin.image_remove_confirm")}</p>
        <div className="site-admin__confirm-actions">
          <button className="acs-admin__btn acs-admin__btn--secondary" type="button" autoFocus onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button className="acs-admin__btn" type="button" onClick={onRemove}>
            {t("admin.image_remove")}
          </button>
        </div>
      </div>
    </div>
  );
}

export interface ProductImagesViewProps extends ImagesHandlers {
  productId: number | null;
  images: readonly AdminProductImage[];
  state: ImagesState;
  locked: boolean;
}

// 图片区（照 A04-desktop 与 A04-desktop-new 的顺序）：标题 admin.product_images 与 admin.image_count；有图片时为缩略图（img 的 src 为
// storage_ref、alt 为空串，第一张左上标 admin.image_cover），每张其下 (‹)(›)(✗)（读屏标签 admin.image_move_earlier、admin.image_move_later 与
// admin.image_remove）；之后为 admin.upload（点它打开隐藏的单选文件输入框）、新建表单时其旁的 admin.image_save_first 与上传中的
// admin.image_uploading；常显的 admin.image_rules、admin.product_image_rules 与 admin.images_immediate；最后为提示（admin.images_changed 带
// admin.reload）与移除确认框。
export function ProductImagesView({ productId, images, state, locked, ...handlers }: ProductImagesViewProps) {
  const t = useCopy();
  const fileInput = useRef<HTMLInputElement>(null);
  const count = images.length;
  const { notice } = state;
  return (
    <div className="site-admin-images">
      <div className="site-admin-images__head">
        <span className="site-admin-images__title">{t("admin.product_images")}</span>
        <span className="acs-admin__muted">{t("admin.image_count", { count, max: MAX_IMAGES })}</span>
      </div>
      {count > 0 && (
        <ul className="site-admin-images__list">
          {images.map((image, index) => (
            <li key={image.id} className="site-admin-images__item">
              <span className="site-admin-images__thumb">
                <img src={image.storage_ref} alt="" />
                {index === 0 && <span className="acs-tag acs-tag--neutral site-admin-images__cover">{t("admin.image_cover")}</span>}
              </span>
              <div className="site-admin-images__actions">
                <IconButton
                  label={t("admin.image_move_earlier")}
                  disabled={moveDisabled(index, count, -1, state, locked)}
                  onPress={() => {
                    handlers.onMove(index, -1);
                  }}
                >
                  <EarlierIcon />
                </IconButton>
                <IconButton
                  label={t("admin.image_move_later")}
                  disabled={moveDisabled(index, count, 1, state, locked)}
                  onPress={() => {
                    handlers.onMove(index, 1);
                  }}
                >
                  <LaterIcon />
                </IconButton>
                <IconButton
                  label={t("admin.image_remove")}
                  disabled={removeDisabled(state, locked)}
                  onPress={() => {
                    handlers.onAskRemove(image.id);
                  }}
                >
                  <CrossIcon />
                </IconButton>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="site-admin-images__upload">
        <button
          className="acs-admin__btn acs-admin__btn--secondary"
          type="button"
          disabled={uploadDisabled(productId, count, state, locked)}
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
        {productId === null && <span className="acs-admin__muted">{t("admin.image_save_first")}</span>}
        {state.busy === "upload" && (
          <span className="acs-admin__muted" role="status">
            {t("admin.image_uploading")}
          </span>
        )}
      </div>
      <span className="acs-admin__muted">{t("admin.image_rules")}</span>
      <span className="acs-admin__muted">{t("admin.product_image_rules")}</span>
      <span className="acs-admin__muted">{t("admin.images_immediate")}</span>
      {notice === "admin.images_changed" && (
        <div className="acs-admin__alert site-admin-images__changed" role="alert">
          <span>{t("admin.images_changed")}</span>
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            disabled={reloadDisabled(state, locked)}
            onClick={() => {
              handlers.onReload();
            }}
          >
            {t("admin.reload")}
          </button>
        </div>
      )}
      {notice !== null && notice !== "admin.images_changed" && <AdminAlert error={notice} />}
      {state.confirm !== null && <RemoveConfirm onCancel={handlers.onCancelRemove} onRemove={handlers.onConfirmRemove} />}
    </div>
  );
}

export interface AdminProductImagesProps {
  // 编辑区的商品 ID；新建表单（还没保存）为 null。
  productId: number | null;
  // 当前图片（编辑区最近一次读取、保存或图片操作给的列表）。
  images: readonly AdminProductImage[];
  csrfToken: string;
  // 商品的保存或新建进行中。
  locked: boolean;
  onImages: (images: AdminProductImage[]) => void;
  onReread: (signal: AbortSignal) => Promise<RereadOutcome>;
  onLogin: () => void;
  // 图片操作开始与结束（卸载时为 false）；须为不变的函数（如 useState 的 setter），否则每次渲染都会中止进行中的请求。
  onBusy: (busy: boolean) => void;
}

// 图片区的容器：自己保留 ImagesState；页面以商品 ID 作 key，切换商品时重新挂载。传给事件属性的处理函数都不返回 Promise。
export default function AdminProductImages({ productId, images, csrfToken, locked, onImages, onReread, onLogin, onBusy }: AdminProductImagesProps) {
  const [state, setState] = useState<ImagesState>(IDLE);
  // 本图片区的请求共用的中止；卸载时中止，并告诉页面不再进行中。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => {
      controller.abort();
      onBusy(false);
    };
  }, [onBusy]);

  const moves: ImagesMoves = {
    images: (next) => {
      onImages(next);
    },
    finish: (value) => {
      setState((current) => finished(current, value));
      onBusy(false);
    },
    reread: onReread,
    login: onLogin,
  };

  // 开始一项操作：记下进行中并告诉页面；没有挂载完成时为 null（不做任何事）。
  const start = (op: ImageOp): AbortSignal | null => {
    const controller = lifetime.current;
    if (controller === null) {
      return null;
    }
    setState((current) => startOp(current, op));
    onBusy(true);
    return controller.signal;
  };

  return (
    <ProductImagesView
      productId={productId}
      images={images}
      state={state}
      locked={locked}
      onFile={(file) => {
        if (productId === null || uploadDisabled(productId, images.length, state, locked)) {
          return;
        }
        const signal = start("upload");
        if (signal !== null) {
          void uploadImage(productId, file, csrfToken, signal, moves);
        }
      }}
      onMove={(index, offset) => {
        if (productId === null || moveDisabled(index, images.length, offset, state, locked)) {
          return;
        }
        const signal = start("move");
        if (signal !== null) {
          void moveImage(productId, images, index, offset, csrfToken, signal, moves);
        }
      }}
      onAskRemove={(imageId) => {
        if (!removeDisabled(state, locked)) {
          setState((current) => askRemove(current, imageId));
        }
      }}
      onCancelRemove={() => {
        setState(cancelRemove);
      }}
      onConfirmRemove={() => {
        const imageId = state.confirm;
        if (productId === null || imageId === null || removeDisabled(state, locked)) {
          setState(cancelRemove);
          return;
        }
        const signal = start("remove");
        if (signal !== null) {
          void removeImage(productId, imageId, csrfToken, signal, moves);
        }
      }}
      onReload={() => {
        if (productId === null || reloadDisabled(state, locked)) {
          return;
        }
        const signal = start("reload");
        if (signal !== null) {
          void reloadImages(productId, signal, moves);
        }
      }}
    />
  );
}
