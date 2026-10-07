import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";

import { readAdminRefund, reviewAdminRefund, reviewAttempt } from "../api/adminRefunds";
import type { AdminRefundDetail, AdminRefundDetailLine, RefundDetailRead, RefundStatus, ReviewAction, ReviewAttempt, ReviewReply } from "../api/adminRefunds";
import { AdminAlert } from "../components/AdminFrame";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, adminOrderPath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import { formatDateTime } from "./TrackOrderPage";

// 后台退款审核 A03 的申请详情与审核（docs/UX.md 0.10 A03 与「状态与补充（0.9）」，视觉稿 A03-desktop、A03-phone-detail、
// A03-desktop-order、A03-desktop-reviewed 与 A03-phone-detail-rejected）：路由 /admin/refunds/:id，桌面在列表右侧、
// 手机只显示详情（由 site.css 按宽度显隐）。
// 打开时以当前界面语言调用一次 GET /api/admin/refunds/{内部 ID}：推迟到下一轮事件循环再发（沿用 A02 详情的 startDeferred），
// 开发模式下 StrictMode 让 effect 执行两次时也只发一次；之后切换界面语言不重新读取（与 A02 详情相同）。
// 标题 admin.refund_detail · 订单号（链接到 A02 该单详情）与状态标签；各行名称 / 规格与 admin.refund_qty；
// admin.refund_amount、order.refunded_total、order.refundable_left 与 refund.shipping_not_refunded。
// 首版不显示 admin.refund_points 一行（Kelvin 2026-10-06 决定，由会员与积分账本任务加回）。
// 审核中：admin.refund_reason 输入框（最多 500 个字符）、admin.refund_approve、admin.refund_reject 与 admin.refund_hint；
// 理由去掉首尾空白后为空时拒绝禁用，批准不要求理由（Kelvin 2026-10-06 决定）。已批准或已拒绝：只读，金额之后为 admin.reviewed_by，
// 有理由时其下为 admin.refund_reason 与理由原文。申请不存在（404）或路径 ID 不合法时只显示 common.error_retry 与返回
// （Kelvin 2026-10-07）；401 换成登录页 A01。理由、审核人邮箱、CSRF 令牌与幂等键只在 React 状态、ref 与请求里；
// 离开页面或换申请时中止请求，旧请求的结果不再更新页面。

// 退款申请状态与它的名称、标签样式（视觉稿 A03-desktop：审核中为演示标签、已批准为成功标签、已拒绝为描边标签）；列表与详情共用。
export const REFUND_STATUS_LABEL: Readonly<Record<RefundStatus, CopyKey>> = {
  requested: "order.refund_requested",
  approved: "order.refund_approved",
  rejected: "order.refund_rejected",
};

const REFUND_TAG_CLASS: Readonly<Record<RefundStatus, string>> = {
  requested: "acs-tag acs-tag--demo",
  approved: "acs-tag acs-tag--success",
  rejected: "acs-tag acs-tag--outline",
};

export function RefundStatusTag({ status }: { status: RefundStatus }) {
  const t = useCopy();
  return <span className={REFUND_TAG_CLASS[status]}>{t(REFUND_STATUS_LABEL[status])}</span>;
}

// 理由输入框的上限（UX A03「最多 500 字」，与服务端相同）。
export const REASON_MAX_LENGTH = 500;

// 按钮可点：申请仍在审核中、理由不超过上限；拒绝另要求理由去掉首尾空白后不为空，批准不要求理由。
export function canReview(refund: AdminRefundDetail, action: ReviewAction, reason: string): boolean {
  if (refund.status !== "requested" || reason.length > REASON_MAX_LENGTH) {
    return false;
  }
  return action === "approve" || reason.trim() !== "";
}

// 详情区的状态：读取中、申请不存在（含路径 ID 不合法）、读取失败（提示）、已取到
// （busy 为审核进行中，notice 为审核后的提示）。
export type DetailState =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "failed"; error: CopyKey }
  | { status: "ready"; refund: AdminRefundDetail; busy: boolean; notice: CopyKey | null };

export const INITIAL_DETAIL: DetailState = { status: "loading" };

// 一步的结果：显示某个状态，或回到登录页 A01（login）。
export type DetailStep = DetailState | "login";

function ready(refund: AdminRefundDetail, notice: CopyKey | null): DetailState {
  return { status: "ready", refund, busy: false, notice };
}

// 打开时按读取结果决定：取到显示详情；401 回到 A01；404 与不合法 ID 为不存在；网络中断 common.network_check，其他失败 common.error_retry。
export function detailStep(read: RefundDetailRead): DetailStep {
  switch (read.kind) {
    case "ok":
      return ready(read.refund, null);
    case "none":
      return "login";
    case "missing":
      return { status: "missing" };
    case "network":
      return { status: "failed", error: "common.network_check" };
    case "failed":
      return { status: "failed", error: "common.error_retry" };
  }
}

// 审核之后重新取详情：取到时显示新详情与 notice；401 回到 A01；404 为不存在；
// 读取本身失败时保留原来的详情并显示 common.network_check 或 common.error_retry。
async function reread(id: string, refund: AdminRefundDetail, language: Language, signal: AbortSignal, notice: CopyKey | null): Promise<DetailStep> {
  const read = await readAdminRefund(id, language, signal);
  switch (read.kind) {
    case "ok":
      return ready(read.refund, notice);
    case "none":
      return "login";
    case "missing":
      return { status: "missing" };
    case "network":
      return ready(refund, "common.network_check");
    case "failed":
      return ready(refund, "common.error_retry");
  }
}

// 按审核的回答决定：200 后重新取详情；409 refund_already_reviewed 与 403 重新取详情并显示 common.error_retry；
// 409 idempotency_conflict、422 与其他失败显示 common.error_retry；网络中断显示 common.network_check；401 回到 A01。
// 不重新读取的几种保留当前详情，按钮按原状态仍可点。
export async function reviewStep(reply: ReviewReply, id: string, refund: AdminRefundDetail, language: Language, signal: AbortSignal): Promise<DetailStep> {
  switch (reply) {
    case "done":
      return reread(id, refund, language, signal, null);
    case "reviewed":
    case "csrf":
      return reread(id, refund, language, signal, "common.error_retry");
    case "none":
      return "login";
    case "network":
      return ready(refund, "common.network_check");
    case "conflict":
    case "invalid":
    case "failed":
      return ready(refund, "common.error_retry");
  }
}

// 审核的幂等键去留：网络中断时保留（再点且操作与理由都没变时沿用）；得到确定回答（含 409 idempotency_conflict 与 422）后丢弃。
export function keptAttempt(reply: ReviewReply, attempt: ReviewAttempt): ReviewAttempt | null {
  return reply === "network" ? attempt : null;
}

// 页面对一步结果的处理：显示详情区，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface DetailMoves {
  show: (state: DetailState) => void;
  replace: (path: RoutePath) => void;
}

// 审核另有两件事：记下（或丢弃）幂等键；200 后让列表重新查询。
export interface ReviewMoves extends DetailMoves {
  keep: (attempt: ReviewAttempt | null) => void;
  reviewed: () => void;
}

// 离开页面或换申请（signal 已中止）之后不再更新。
export function settleDetail(step: DetailStep, signal: AbortSignal, moves: DetailMoves): void {
  if (signal.aborted) {
    return;
  }
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

export async function openDetail(id: string, language: Language, signal: AbortSignal, moves: DetailMoves): Promise<void> {
  const read = await readAdminRefund(id, language, signal);
  settleDetail(detailStep(read), signal, moves);
}

// 发出一次审核：请求体只有理由，请求头带本次的幂等键与详情给的 CSRF 令牌。审核的 POST 本身不中止（与 A02 推进相同），
// 离开页面之后其结果与重新读取都不再更新页面，列表也不再重新查询。
export async function submitReview(
  id: string,
  refund: AdminRefundDetail,
  attempt: ReviewAttempt,
  language: Language,
  signal: AbortSignal,
  moves: ReviewMoves,
): Promise<void> {
  const reply = await reviewAdminRefund(id, attempt.action, attempt.reason, refund.csrf_token, attempt.key);
  moves.keep(keptAttempt(reply, attempt));
  if (reply === "done" && !signal.aborted) {
    moves.reviewed();
  }
  settleDetail(await reviewStep(reply, id, refund, language, signal), signal, moves);
}

function BackIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}

function DemoMark() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 3l9 9-9 9-9-9z" />
    </svg>
  );
}

// 一行商品：名称 / 规格，右侧为 admin.refund_qty（申请、购买与已批准件数）。
function ItemRow({ line }: { line: AdminRefundDetailLine }) {
  const t = useCopy();
  return (
    <div className="site-admin-refund__row">
      <span className="site-admin-refund__line-name">
        <span>{line.name}</span>
        {line.variant_label !== "" && (
          <>
            <span>/</span>
            <span>{line.variant_label}</span>
          </>
        )}
      </span>
      <span>{t("admin.refund_qty", { requested: line.quantity, bought: line.purchased_quantity, approved: line.approved_quantity })}</span>
    </div>
  );
}

interface ReadyProps {
  refund: AdminRefundDetail;
  reason: string;
  busy: boolean;
  notice: CopyKey | null;
  onReason: (value: string) => void;
  onReview: (action: ReviewAction) => void;
}

// 审核中的理由输入框（最多 500 个字符）；审核进行中禁用。
function ReasonField({ reason, busy, onReason }: Pick<ReadyProps, "reason" | "busy" | "onReason">) {
  const t = useCopy();
  return (
    <div className="site-admin-refund__block">
      <label className="site-admin-refund__reason">
        <span>{t("admin.refund_reason")}</span>
        <textarea
          className="acs-admin__input"
          rows={2}
          maxLength={REASON_MAX_LENGTH}
          value={reason}
          disabled={busy}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => {
            onReason(event.target.value);
          }}
        />
      </label>
    </div>
  );
}

// 审核中：批准与拒绝两个按钮，其后为提示与 admin.refund_hint（手机由 site.css 把 admin.refund_hint 排在按钮之前）。
function ReviewActions({ refund, reason, busy, notice, onReview }: Omit<ReadyProps, "onReason">) {
  const t = useCopy();
  return (
    <div className="site-admin-refund__actions">
      <div className="site-admin-refund__buttons">
        <button
          className="acs-admin__btn"
          type="button"
          disabled={busy || !canReview(refund, "approve", reason)}
          onClick={() => {
            onReview("approve");
          }}
        >
          {t("admin.refund_approve")}
        </button>
        <button
          className="acs-admin__btn acs-admin__btn--secondary"
          type="button"
          disabled={busy || !canReview(refund, "reject", reason)}
          onClick={() => {
            onReview("reject");
          }}
        >
          {t("admin.refund_reject")}
        </button>
      </div>
      {notice !== null && <AdminAlert error={notice} />}
      <div className="site-admin-refund__hint">
        <DemoMark />
        <span>{t("admin.refund_hint")}</span>
      </div>
    </div>
  );
}

// 已批准或已拒绝：admin.reviewed_by（审核人邮箱与按界面语言的审核时间），有理由时其下为 admin.refund_reason 与理由原文。
function ReviewedBlock({ refund }: { refund: AdminRefundDetail }) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <div className="site-admin-refund__block">
      <span>{t("admin.reviewed_by", { username: refund.reviewer_username ?? "", time: formatDateTime(refund.reviewed_at ?? "", language) })}</span>
      {refund.review_reason !== null && (
        <>
          <span className="acs-admin__muted">{t("admin.refund_reason")}</span>
          <span className="site-admin-refund__reason-text">{refund.review_reason}</span>
        </>
      )}
    </div>
  );
}

// 已取到的详情：桌面为一张面板；手机外层面板不绘制（site.css），标题、内容面板（商品、金额与理由或审核记录）、提示与按钮依次排列。
function RefundDetail({ refund, reason, busy, notice, onReason, onReview }: ReadyProps) {
  const t = useCopy();
  const requested = refund.status === "requested";
  return (
    <div className="acs-admin__panel site-admin-refund__panel">
      <div className="site-admin-refund__head">
        <h2 className="acs-admin__h">
          <span>{t("admin.refund_detail")}</span>
          <span aria-hidden="true"> · </span>
          <Link to={adminOrderPath(refund.order_id)}>{refund.order_number}</Link>
        </h2>
        <RefundStatusTag status={refund.status} />
      </div>
      <div className="acs-admin__panel site-admin-refund__body">
        <div className="site-admin-refund__block site-admin-refund__items">
          <span className="acs-admin__muted">{t("order.items")}</span>
          {refund.lines.map((line, index) => (
            <ItemRow key={index} line={line} />
          ))}
        </div>
        <div className="site-admin-refund__block">
          <span>{t("admin.refund_amount", { amount: formatSen(refund.amount_sen) })}</span>
          <span>{t("order.refunded_total", { amount: formatSen(refund.refunded_sen) })}</span>
          <span>{t("order.refundable_left", { amount: formatSen(refund.refundable_left_sen) })}</span>
          <span className="acs-admin__muted">{t("refund.shipping_not_refunded")}</span>
        </div>
        {requested ? <ReasonField reason={reason} busy={busy} onReason={onReason} /> : <ReviewedBlock refund={refund} />}
      </div>
      {requested && <ReviewActions refund={refund} reason={reason} busy={busy} notice={notice} onReview={onReview} />}
    </div>
  );
}

export interface AdminRefundDetailViewProps {
  state: DetailState;
  // 理由输入框里的文字（只在页面内存里）。
  reason: string;
  // 返回链接指向的列表网址（打开详情之前的列表：/admin/refunds 或 /admin/refunds/order/<订单内部 ID>）。
  backTo: RoutePath;
  onReason: (value: string) => void;
  onReview: (action: ReviewAction) => void;
}

// 详情区：顶部为返回列表的链接（只在手机显示）；读取中标 aria-busy；不存在或读取失败时只有提示，不显示详情。
export function AdminRefundDetailView({ state, reason, backTo, onReason, onReview }: AdminRefundDetailViewProps) {
  const t = useCopy();
  return (
    <section className="site-admin-refund" aria-busy={state.status === "loading"}>
      <Link className="site-admin-refund__back" to={backTo}>
        <BackIcon />
        <span>{t("common.back")}</span>
      </Link>
      {state.status === "missing" && <AdminAlert error="common.error_retry" />}
      {state.status === "failed" && <AdminAlert error={state.error} />}
      {state.status === "ready" && (
        <RefundDetail refund={state.refund} reason={reason} busy={state.busy} notice={state.notice} onReason={onReason} onReview={onReview} />
      )}
    </section>
  );
}

export interface AdminRefundDetailContentProps {
  id: string;
  backTo: RoutePath;
  // 审核 200 之后让左侧列表重新查询。
  onReviewed: () => void;
}

// 一笔申请的详情：路径里的 ID 换了（换申请）时由调用方以 key 重新挂载。
export default function AdminRefundDetailContent({ id, backTo, onReviewed }: AdminRefundDetailContentProps) {
  const { replace } = useRouter();
  const { language } = useLanguage();
  const [state, setState] = useState<DetailState>(INITIAL_DETAIL);
  const [reason, setReason] = useState("");
  // 网络中断后保留的那次审核（操作、理由与幂等键）；得到确定回答后为 null。
  const retry = useRef<ReviewAttempt | null>(null);
  // 打开时的界面语言：之后切换语言不重新读取（与 A02 详情相同）。
  const languageRef = useRef(language);
  useEffect(() => {
    languageRef.current = language;
  });
  // 离开页面或换申请时中止读取与审核后的重新读取，之后不再更新页面。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openDetail(id, languageRef.current, controller.signal, { show: setState, replace });
    });
  }, [id, replace]);

  return (
    <AdminRefundDetailView
      state={state}
      reason={reason}
      backTo={backTo}
      onReason={setReason}
      onReview={(action) => {
        const controller = lifetime.current;
        if (state.status !== "ready" || state.busy || controller === null || !canReview(state.refund, action, reason)) {
          return;
        }
        const attempt = reviewAttempt(retry.current, action, reason);
        retry.current = attempt;
        setState({ ...state, busy: true, notice: null });
        void submitReview(id, state.refund, attempt, language, controller.signal, {
          show: setState,
          replace,
          keep: (kept) => {
            retry.current = kept;
          },
          reviewed: onReviewed,
        });
      }}
    />
  );
}
