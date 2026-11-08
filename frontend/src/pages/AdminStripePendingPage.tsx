import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";

import { readStripePending, retryLateRefund, voidCheckout } from "../api/adminStripePending";
import type {
  CheckoutStatus,
  FailedLateRefund,
  PendingCheckout,
  RefundRetryReply,
  StripePending,
  StripePendingRead,
  VoidReply,
} from "../api/adminStripePending";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { formatDateTime, usePrice } from "./TrackOrderPage";

// 后台 Stripe 待处理 A11（docs/UX.md 0.19 A11，视觉稿 A11-desktop、A11-phone 与 A11-states；DESIGN 1.22「Stripe 测试环境支付」第 11 条）：
// 用后台框架渲染，当前导航项为 Stripe 待处理。内容区为标题 admin.nav_stripe_pending（h1）与其下的 admin.stripe_pending_note，再下为两个列表：
// admin.stripe_pending_checkouts_title（已告警而订单仍待支付的结账记录，每行 admin.stripe_void 打开作废确认框）与
// admin.stripe_pending_refunds_title（其下 admin.stripe_pending_refunds_note；失败的迟到付款退款，每行 admin.stripe_refund_retry 直接重试）。
// 桌面为表格，手机为卡片，两套都渲染，由 site.css 按宽度显隐（照 A07）。两个列表照接口给的顺序显示，各自为空时显示 admin.stripe_pending_none；
// 只显示对账字段与订单号，CSRF 令牌只在页面状态与写操作的请求头里。
// 打开时读取一次（GET /api/admin/stripe-pending）；读取失败显示提示与 admin.reload。作废与重试的结果各在自己列表的标题下方显示一条（最新一条），
// 下一次同类操作开始时清除，重新读取不清除。401 回到 A01；离开页面或再次读取时中止未完成的读取，旧读取的结果不再更新页面。

// 本页用到的三个请求；测试以替身代替。
export interface StripePendingRequests {
  read: (signal?: AbortSignal) => Promise<StripePendingRead>;
  voidCheckout: (checkoutId: number, csrfToken: string) => Promise<VoidReply>;
  retry: (checkoutId: number, csrfToken: string) => Promise<RefundRetryReply>;
}

export const STRIPE_PENDING_REQUESTS: StripePendingRequests = {
  read: readStripePending,
  voidCheckout,
  retry: retryLateRefund,
};

// 记录状态（UX A11「记录状态」）：creating、open、complete 依次为三条文案。
export const CHECKOUT_STATUS_LABEL: Readonly<Record<CheckoutStatus, CopyKey>> = {
  creating: "admin.checkout_status_creating",
  open: "admin.checkout_status_open",
  complete: "admin.checkout_status_complete",
};

// 记录 ID 与尝试 ID 都以 # 加数字显示。
export function hashId(id: number): string {
  return `#${String(id)}`;
}

// 错误类别为 Stripe 给出的类别码原文，为空时显示 —。
export function errorTypeText(errorType: string | null): string {
  return errorType === null || errorType === "" ? "—" : errorType;
}

// 列表区的状态：读取进行中（busy，标 aria-busy）、取到的两个列表与令牌（pending），与读取失败的提示（error）。
export interface PendingState {
  busy: boolean;
  pending: StripePending | null;
  error: CopyKey | null;
}

export const INITIAL_PENDING: PendingState = { busy: true, pending: null, error: null };

// 开始重新读取：标为读取中，已显示的列表与提示先留着，等结果回来再换。
export function startReading(state: PendingState): PendingState {
  return { ...state, busy: true };
}

// 按读取结果决定：取到显示两个列表；401 回到 A01（login）；网络中断显示 common.network_check，其他失败显示 common.error_retry（照 A07），
// 失败时不显示列表，只有提示与 admin.reload。
export function readStep(read: StripePendingRead): PendingState | "login" {
  switch (read.kind) {
    case "ok":
      return { busy: false, pending: read.pending, error: null };
    case "none":
      return "login";
    case "network":
      return { busy: false, pending: null, error: "common.network_check" };
    case "failed":
      return { busy: false, pending: null, error: "common.error_retry" };
  }
}

// 作废或重试之后的一条提示：状态提示（role="status"）或警示（acs-admin__alert，role="alert"），照 A11-states。
export interface Notice {
  key: CopyKey;
  tone: "status" | "alert";
}

// 一次写操作的下一步：显示提示并按 reread 决定是否重新读取两个列表，或回到 A01（login）。
export type WriteStep = { notice: Notice; reread: boolean } | "login";

function statusNotice(key: CopyKey): Notice {
  return { key, tone: "status" };
}

function alertNotice(key: CopyKey): Notice {
  return { key, tone: "alert" };
}

// 按作废的回答决定（UX A11 作废结果）：confirmed、cancelled、voided 与 changed（含 404）为状态提示并重新读取；unavailable 为警示、不重新读取
// （行不变）；403 为 common.error_retry 并重新读取（取得新的 CSRF 令牌）；网络中断 common.network_check、其他失败 common.error_retry，
// 都不重新读取；401 回到 A01。
export function voidStep(reply: VoidReply): WriteStep {
  switch (reply) {
    case "confirmed":
      return { notice: statusNotice("admin.stripe_void_confirmed"), reread: true };
    case "cancelled":
      return { notice: statusNotice("admin.stripe_void_cancelled"), reread: true };
    case "voided":
      return { notice: statusNotice("admin.stripe_void_voided"), reread: true };
    case "changed":
      return { notice: statusNotice("admin.stripe_pending_changed"), reread: true };
    case "unavailable":
      return { notice: alertNotice("admin.stripe_void_unavailable"), reread: false };
    case "csrf":
      return { notice: alertNotice("common.error_retry"), reread: true };
    case "none":
      return "login";
    case "network":
      return { notice: alertNotice("common.network_check"), reread: false };
    case "failed":
      return { notice: alertNotice("common.error_retry"), reread: false };
  }
}

// 按重试的回答决定（UX A11 重试结果）：done 为 admin.stripe_late_refund_retried、changed（含 404）为 admin.stripe_pending_changed，
// 都是状态提示并重新读取；cap 为 A03 已有的 admin.stripe_refund_blocked（警示，不重新读取）；403 为 common.error_retry 并重新读取；
// 网络中断 common.network_check、其他失败 common.error_retry，都不重新读取；401 回到 A01。
export function retryStep(reply: RefundRetryReply): WriteStep {
  switch (reply) {
    case "done":
      return { notice: statusNotice("admin.stripe_late_refund_retried"), reread: true };
    case "changed":
      return { notice: statusNotice("admin.stripe_pending_changed"), reread: true };
    case "cap":
      return { notice: alertNotice("admin.stripe_refund_blocked"), reread: false };
    case "csrf":
      return { notice: alertNotice("common.error_retry"), reread: true };
    case "none":
      return "login";
    case "network":
      return { notice: alertNotice("common.network_check"), reread: false };
    case "failed":
      return { notice: alertNotice("common.error_retry"), reread: false };
  }
}

// 作废确认框：关闭，或打开并记着该行、勾选框是否勾选与是否提交中。
export type VoidDialog = { open: false } | { open: true; checkout: PendingCheckout; verified: boolean; submitting: boolean };

export const CLOSED_VOID: VoidDialog = { open: false };

// 每次打开时勾选框为未勾选。
export function openVoid(checkout: PendingCheckout): VoidDialog {
  return { open: true, checkout, verified: false, submitting: false };
}

// 勾选或取消勾选；提交中不变。
export function setVerified(dialog: VoidDialog, verified: boolean): VoidDialog {
  return dialog.open && !dialog.submitting ? { ...dialog, verified } : dialog;
}

// admin.stripe_void_submit 可点：已勾选核对且不在提交中。
export function canSubmitVoid(dialog: VoidDialog): boolean {
  return dialog.open && dialog.verified && !dialog.submitting;
}

// 开始提交：标为提交中（两个按钮都禁用）。
export function startVoid(dialog: VoidDialog): VoidDialog {
  return dialog.open ? { ...dialog, submitting: true } : dialog;
}

// 页面对一次读取结果的处理：显示，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface ReadMoves {
  show: (state: PendingState) => void;
  replace: (path: RoutePath) => void;
}

// 读取两个列表；signal 中止（离开页面或已再次读取）之后不再更新。
export async function loadPending(signal: AbortSignal, moves: ReadMoves, requests: StripePendingRequests = STRIPE_PENDING_REQUESTS): Promise<void> {
  const read = await requests.read(signal);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 写操作结果的处理：给出提示（作废时同时关闭确认框）、重新读取两个列表，或换成 A01。
export interface WriteMoves {
  done: (notice: Notice) => void;
  reload: () => void;
  replace: (path: RoutePath) => void;
}

// 离开页面（signal 已中止）之后不再更新。
export function settleWrite(step: WriteStep, signal: AbortSignal, moves: WriteMoves): void {
  if (signal.aborted) {
    return;
  }
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
    return;
  }
  moves.done(step.notice);
  if (step.reread) {
    moves.reload();
  }
}

// 发出作废：请求头带最近一次读取给的 CSRF 令牌。POST 本身不中止，离开页面之后其结果不再更新页面。
export async function submitVoid(
  checkoutId: number,
  csrfToken: string,
  signal: AbortSignal,
  moves: WriteMoves,
  requests: StripePendingRequests = STRIPE_PENDING_REQUESTS,
): Promise<void> {
  settleWrite(voidStep(await requests.voidCheckout(checkoutId, csrfToken)), signal, moves);
}

// 发出重试：同上。
export async function submitRetry(
  checkoutId: number,
  csrfToken: string,
  signal: AbortSignal,
  moves: WriteMoves,
  requests: StripePendingRequests = STRIPE_PENDING_REQUESTS,
): Promise<void> {
  settleWrite(retryStep(await requests.retry(checkoutId, csrfToken)), signal, moves);
}

function NoticeView({ notice }: { notice: Notice | null }) {
  const t = useCopy();
  if (notice === null) {
    return null;
  }
  if (notice.tone === "alert") {
    return <AdminAlert error={notice.key} />;
  }
  return (
    <div className="site-admin-stripe-pending__status" role="status">
      {t(notice.key)}
    </div>
  );
}

function CheckoutStatusTag({ status }: { status: CheckoutStatus }) {
  const t = useCopy();
  return <span className="acs-tag acs-tag--neutral">{t(CHECKOUT_STATUS_LABEL[status])}</span>;
}

interface CheckoutsProps {
  checkouts: PendingCheckout[];
  onVoid: (checkout: PendingCheckout) => void;
}

function VoidButton({ checkout, onVoid }: { checkout: PendingCheckout; onVoid: (checkout: PendingCheckout) => void }) {
  const t = useCopy();
  return (
    <button
      className="acs-admin__btn acs-admin__btn--secondary"
      type="button"
      onClick={() => {
        onVoid(checkout);
      }}
    >
      {t("admin.stripe_void")}
    </button>
  );
}

// 第一个列表：桌面表格的列依次为记录 ID、状态、订单号、创建时间、告警时间与 admin.stripe_void（靠右）；手机卡片照 UX A11 手机线框，
// 第一行记录 ID 与状态标签分列两端，其下订单号、创建时间、告警时间与按钮。
function CheckoutList({ checkouts, onVoid }: CheckoutsProps) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <>
      <div className="acs-admin__panel site-admin-stripe-pending__table">
        <table className="acs-admin__table">
          <thead>
            <tr>
              <th>{t("admin.col_checkout_record")}</th>
              <th>{t("admin.filter_status")}</th>
              <th>{t("pay.order_no")}</th>
              <th>{t("admin.col_created")}</th>
              <th>{t("admin.col_alerted")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {checkouts.map((checkout) => (
              <tr key={checkout.checkout_id}>
                <td>{hashId(checkout.checkout_id)}</td>
                <td>
                  <CheckoutStatusTag status={checkout.status} />
                </td>
                <td>{checkout.order_number}</td>
                <td>{formatDateTime(checkout.created_at, language)}</td>
                <td>{formatDateTime(checkout.alerted_at, language)}</td>
                <td className="site-admin-stripe-pending__end">
                  <VoidButton checkout={checkout} onVoid={onVoid} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="site-admin-stripe-pending__cards">
        {checkouts.map((checkout) => (
          <li key={checkout.checkout_id} className="acs-admin__panel site-admin-stripe-pending__card">
            <div className="site-admin-stripe-pending__card-head">
              <span>{hashId(checkout.checkout_id)}</span>
              <CheckoutStatusTag status={checkout.status} />
            </div>
            <span>
              <span>{t("pay.order_no")}</span> <span>{checkout.order_number}</span>
            </span>
            <span className="acs-admin__muted">
              <span>{t("admin.col_created")}</span> <span>{formatDateTime(checkout.created_at, language)}</span>
            </span>
            <span className="acs-admin__muted">
              <span>{t("admin.col_alerted")}</span> <span>{formatDateTime(checkout.alerted_at, language)}</span>
            </span>
            <div>
              <VoidButton checkout={checkout} onVoid={onVoid} />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

interface RefundsProps {
  refunds: FailedLateRefund[];
  retrying: number | null;
  onRetry: (refund: FailedLateRefund) => void;
}

// admin.stripe_refund_retry：本页同时只发一次重试，提交中各行的重试按钮都禁用，发出的那一行另标 aria-busy。
function RetryButton({ refund, retrying, onRetry }: { refund: FailedLateRefund } & Omit<RefundsProps, "refunds">) {
  const t = useCopy();
  return (
    <button
      className="acs-admin__btn acs-admin__btn--secondary"
      type="button"
      disabled={retrying !== null}
      aria-busy={retrying === refund.checkout_id}
      onClick={() => {
        onRetry(refund);
      }}
    >
      {t("admin.stripe_refund_retry")}
    </button>
  );
}

// 第二个列表：桌面表格的列依次为记录 ID、尝试 ID、订单号、金额（靠右）、尝试时间、错误类别与 admin.stripe_refund_retry（靠右）；手机卡片照
// UX A11 手机线框，第一行记录 ID 与「admin.col_refund_attempt 尝试 ID」分列两端，其下订单号、金额、尝试时间、错误类别与按钮。
function RefundList({ refunds, retrying, onRetry }: RefundsProps) {
  const t = useCopy();
  const price = usePrice();
  const { language } = useLanguage();
  return (
    <>
      <div className="acs-admin__panel site-admin-stripe-pending__table">
        <table className="acs-admin__table">
          <thead>
            <tr>
              <th>{t("admin.col_checkout_record")}</th>
              <th>{t("admin.col_refund_attempt")}</th>
              <th>{t("pay.order_no")}</th>
              <th className="site-admin-stripe-pending__end">{t("admin.col_amount")}</th>
              <th>{t("admin.col_attempted")}</th>
              <th>{t("admin.col_error_type")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {refunds.map((refund) => (
              <tr key={refund.attempt_id}>
                <td>{hashId(refund.checkout_id)}</td>
                <td>{hashId(refund.attempt_id)}</td>
                <td>{refund.order_number}</td>
                <td className="site-admin-stripe-pending__end">{price(refund.amount_sen)}</td>
                <td>{formatDateTime(refund.attempted_at, language)}</td>
                <td>{errorTypeText(refund.error_type)}</td>
                <td className="site-admin-stripe-pending__end">
                  <RetryButton refund={refund} retrying={retrying} onRetry={onRetry} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="site-admin-stripe-pending__cards">
        {refunds.map((refund) => (
          <li key={refund.attempt_id} className="acs-admin__panel site-admin-stripe-pending__card">
            <div className="site-admin-stripe-pending__card-head">
              <span>{hashId(refund.checkout_id)}</span>
              <span>
                <span>{t("admin.col_refund_attempt")}</span> <span>{hashId(refund.attempt_id)}</span>
              </span>
            </div>
            <span>
              <span>{t("pay.order_no")}</span> <span>{refund.order_number}</span>
            </span>
            <span>{price(refund.amount_sen)}</span>
            <span className="acs-admin__muted">
              <span>{t("admin.col_attempted")}</span> <span>{formatDateTime(refund.attempted_at, language)}</span>
            </span>
            <span className="acs-admin__muted">
              <span>{t("admin.col_error_type")}</span> <span>{errorTypeText(refund.error_type)}</span>
            </span>
            <div>
              <RetryButton refund={refund} retrying={retrying} onRetry={onRetry} />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function NoneText() {
  const t = useCopy();
  return <p className="acs-admin__muted">{t("admin.stripe_pending_none")}</p>;
}

export interface VoidConfirmProps {
  dialog: Extract<VoidDialog, { open: true }>;
  onVerified: (verified: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

// 作废确认框（A11-states，照 site-admin__confirm）：标题 admin.stripe_void_confirm_title（{id} 为记录 ID，{order} 为订单号）、正文、
// 勾选框 admin.stripe_void_verified，按钮依次为 common.cancel（次要，打开时取得焦点）与 admin.stripe_void_submit；未勾选时提交禁用，
// 提交中两个按钮与勾选框都禁用。common.cancel 或 Escape 关闭，不发请求（提交中不关闭）。
export function VoidConfirm({ dialog, onVerified, onCancel, onSubmit }: VoidConfirmProps) {
  const t = useCopy();
  const labelId = useId();
  return (
    <div
      className="site-admin__overlay"
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key === "Escape" && !dialog.submitting) {
          onCancel();
        }
      }}
    >
      <div
        className="acs-admin__panel site-admin__confirm site-admin-stripe-pending__confirm"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        aria-busy={dialog.submitting}
      >
        <p id={labelId}>{t("admin.stripe_void_confirm_title", { id: dialog.checkout.checkout_id, order: dialog.checkout.order_number })}</p>
        <p>{t("admin.stripe_void_confirm_body")}</p>
        <label className="acs-admin__choice site-admin-stripe-pending__verified">
          <input
            type="checkbox"
            checked={dialog.verified}
            disabled={dialog.submitting}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              onVerified(event.target.checked);
            }}
          />
          <span>{t("admin.stripe_void_verified")}</span>
        </label>
        <div className="site-admin__confirm-actions">
          <button className="acs-admin__btn acs-admin__btn--secondary" type="button" autoFocus disabled={dialog.submitting} onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button className="acs-admin__btn" type="button" disabled={!canSubmitVoid(dialog)} onClick={onSubmit}>
            {t("admin.stripe_void_submit")}
          </button>
        </div>
      </div>
    </div>
  );
}

export interface AdminStripePendingViewProps {
  state: PendingState;
  dialog: VoidDialog;
  voidNotice: Notice | null;
  retryNotice: Notice | null;
  // 进行中的重试所在行的结账记录 ID（没有为 null）。
  retrying: number | null;
  onReload: () => void;
  onVoid: (checkout: PendingCheckout) => void;
  onVerified: (verified: boolean) => void;
  onCancelVoid: () => void;
  onSubmitVoid: () => void;
  onRetry: (refund: FailedLateRefund) => void;
}

// 内容区：标题、说明，读取失败时的提示与 admin.reload，两个列表的标题（第二个之下为说明），各自标题下方为该类操作的结果提示，
// 再下为列表（取到之后；为空时为 admin.stripe_pending_none）；确认框打开时盖在页面上方。读取中标 aria-busy。
export function AdminStripePendingView({
  state,
  dialog,
  voidNotice,
  retryNotice,
  retrying,
  onReload,
  onVoid,
  onVerified,
  onCancelVoid,
  onSubmitVoid,
  onRetry,
}: AdminStripePendingViewProps) {
  const t = useCopy();
  const { pending } = state;
  return (
    <div className="site-admin-stripe-pending" aria-busy={state.busy}>
      <h1 className="acs-admin__h">{t("admin.nav_stripe_pending")}</h1>
      <p className="site-admin-stripe-pending__note">{t("admin.stripe_pending_note")}</p>
      {state.error !== null && (
        <>
          <AdminAlert error={state.error} />
          <button className="acs-admin__btn acs-admin__btn--secondary site-admin-stripe-pending__reload" type="button" disabled={state.busy} onClick={onReload}>
            {t("admin.reload")}
          </button>
        </>
      )}
      <section className="site-admin-stripe-pending__section">
        <h2 className="acs-admin__h">{t("admin.stripe_pending_checkouts_title")}</h2>
        <NoticeView notice={voidNotice} />
        {pending !== null && (pending.checkouts.length === 0 ? <NoneText /> : <CheckoutList checkouts={pending.checkouts} onVoid={onVoid} />)}
      </section>
      <section className="site-admin-stripe-pending__section site-admin-stripe-pending__refunds">
        <h2 className="acs-admin__h">{t("admin.stripe_pending_refunds_title")}</h2>
        <p className="acs-admin__muted">{t("admin.stripe_pending_refunds_note")}</p>
        <NoticeView notice={retryNotice} />
        {pending !== null &&
          (pending.refunds.length === 0 ? <NoneText /> : <RefundList refunds={pending.refunds} retrying={retrying} onRetry={onRetry} />)}
      </section>
      {dialog.open && <VoidConfirm dialog={dialog} onVerified={onVerified} onCancel={onCancelVoid} onSubmit={onSubmitVoid} />}
    </div>
  );
}

// 后台框架内容区（已登录时显示）：框架确认已登录后才挂载，挂载即读取一次。
export function AdminStripePendingContent() {
  const { replace } = useRouter();
  const [state, setState] = useState<PendingState>(INITIAL_PENDING);
  const [dialog, setDialog] = useState<VoidDialog>(CLOSED_VOID);
  const [voidNotice, setVoidNotice] = useState<Notice | null>(null);
  const [retryNotice, setRetryNotice] = useState<Notice | null>(null);
  const [retrying, setRetrying] = useState<number | null>(null);
  // ref 让提交中的再点（重新渲染之前）也不发第二次请求。
  const voiding = useRef(false);
  const retryingRef = useRef(false);
  // 页面的生命周期与进行中的读取：离开页面时中止生命周期，进行中的读取随之中止；再次读取时中止上一次读取。
  const lifetime = useRef<AbortController | null>(null);
  const reading = useRef<AbortController | null>(null);

  // 发出一次读取（中止上一次）；结果回来之前页面状态不变。
  const read = useCallback(
    (life: AbortController) => {
      reading.current?.abort();
      const controller = new AbortController();
      reading.current = controller;
      life.signal.addEventListener(
        "abort",
        () => {
          controller.abort();
        },
        { once: true },
      );
      void loadPending(controller.signal, { show: setState, replace });
    },
    [replace],
  );

  // admin.reload 与写操作之后的重新读取：标为读取中，已显示的列表与两处提示先留着。
  const reload = useCallback(() => {
    const life = lifetime.current;
    if (life === null || life.signal.aborted) {
      return;
    }
    setState(startReading);
    read(life);
  }, [read]);

  // 挂载即读取一次（初始状态已是读取中）。
  useEffect(() => {
    const life = new AbortController();
    lifetime.current = life;
    read(life);
    return () => {
      life.abort();
    };
  }, [read]);

  return (
    <AdminStripePendingView
      state={state}
      dialog={dialog}
      voidNotice={voidNotice}
      retryNotice={retryNotice}
      retrying={retrying}
      onReload={reload}
      onVoid={(checkout) => {
        if (!voiding.current) {
          setDialog(openVoid(checkout));
        }
      }}
      onVerified={(verified) => {
        setDialog((current) => setVerified(current, verified));
      }}
      onCancelVoid={() => {
        if (!voiding.current) {
          setDialog(CLOSED_VOID);
        }
      }}
      onSubmitVoid={() => {
        const life = lifetime.current;
        if (!dialog.open || !canSubmitVoid(dialog) || voiding.current || life === null || state.pending === null) {
          return;
        }
        voiding.current = true;
        setDialog(startVoid(dialog));
        setVoidNotice(null);
        void submitVoid(dialog.checkout.checkout_id, state.pending.csrf_token, life.signal, {
          done: (notice) => {
            voiding.current = false;
            setDialog(CLOSED_VOID);
            setVoidNotice(notice);
          },
          reload,
          replace,
        });
      }}
      onRetry={(refund) => {
        const life = lifetime.current;
        if (retryingRef.current || life === null || state.pending === null) {
          return;
        }
        retryingRef.current = true;
        setRetrying(refund.checkout_id);
        setRetryNotice(null);
        void submitRetry(refund.checkout_id, state.pending.csrf_token, life.signal, {
          done: (notice) => {
            retryingRef.current = false;
            setRetrying(null);
            setRetryNotice(notice);
          },
          reload,
          replace,
        });
      }}
    />
  );
}

export default function AdminStripePendingPage() {
  return (
    <AdminFrame current="stripePending">
      <AdminStripePendingContent />
    </AdminFrame>
  );
}
