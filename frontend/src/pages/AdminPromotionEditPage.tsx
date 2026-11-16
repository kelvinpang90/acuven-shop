import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ChangeEvent, Dispatch, FormEvent, ReactNode, SetStateAction, SyntheticEvent } from "react";

import { createAdminPromotion, MAX_PROMOTION_ID, readAdminPromotion, readAdminPromotions, saveAdminPromotion } from "../api/adminPromotions";
import type { AdminPromotion, PromotionProblem, PromotionRead, PromotionsRead, PromotionWrite } from "../api/adminPromotions";
import AdminFrame, { AdminAlert, LANGUAGE_LABEL, useUnsavedConfirm } from "../components/AdminFrame";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_PROMOTION_NEW_PATH, ADMIN_PROMOTIONS_PATH, adminPromotionPath, Link, useRouter } from "../router";
import type { RouteParams, RoutePath, RoutePattern } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import { PROMOTION_STATE_CLASS, PROMOTION_STATE_LABEL } from "./AdminPromotionsPage";
import { copyLength, copyMaxLength, formFromPromotion, formProblems, isDirty, NAME_MAX_LENGTH, newPromotionForm, promotionInput } from "./promotionForm";
import type { CopyField, PromotionForm, PromotionFormProblem } from "./promotionForm";

// 后台促销活动编辑区 A12（docs/UX.md 0.20 A12「编辑区」「状态与可用操作」与说明的「时间」「文案」两条，视觉稿 A12-editor-desktop、A12-phone、A12-states；
// HANDOFF 0.98 落实写法；DESIGN 1.23「节日促销活动」第 1–2、12 条）：新建 /admin/promotions/new 与编辑 /admin/promotions/:id（段值为活动的内部 ID）共用本页，
// 用后台框架渲染，当前导航项为促销活动。本任务（SHOP-TASK-270）是编辑区的框架：读取、标题行、基本信息、同用开关、文案、操作栏的保存与结果提示、
// 提交前的字段检查与离开确认；规则区、状态操作、装饰、编辑区预览、海报与 AI 由之后的任务接上。
// - 新建先以 readAdminPromotions 取 CSRF 令牌，保存为 createAdminPromotion，成功后以 replace 换到新 ID 的页面并在那里显示 admin.promo_saved（本模块里存的
//   一次性提示）；已有活动以 readAdminPromotion 读取，保存为 saveAdminPromotion（带读取到的 version），成功后以返回的活动为新基准。段值不是不超过
//   MAX_PROMOTION_ID 的正整数即不存在，不发请求。
// - 表单的类型、换算与字段检查用 promotionForm.ts（SHOP-TASK-281）；不合格的字段旁显示 admin.promo_field_invalid，整次不提交。取值、核对、版本与审计在服务端。
// - 401 回到 A01；离开页面时中止进行中的请求，之后不再更新页面。有未保存修改时以 useUnsavedConfirm 拦截离开（照 A10）。内部名称与文案按纯文本显示。

// 编辑页的段值：不超过 MAX_PROMOTION_ID 的正整数（十进制、没有前导零与符号）。
const PROMOTION_ID_SEGMENT = /^[1-9][0-9]*$/;

// 打开的是哪一个：新建、某个活动（内部 ID），或段值不合法（按不存在处理，不发请求）。
export type EditTarget = { kind: "new" } | { kind: "existing"; id: number } | { kind: "invalid" };

export function editTarget(pattern: RoutePattern, params: RouteParams): EditTarget {
  if (pattern === ADMIN_PROMOTION_NEW_PATH) {
    return { kind: "new" };
  }
  const segment = params.id ?? "";
  if (!PROMOTION_ID_SEGMENT.test(segment)) {
    return { kind: "invalid" };
  }
  const id = Number(segment);
  return id <= MAX_PROMOTION_ID ? { kind: "existing", id } : { kind: "invalid" };
}

// 一次性的成功提示（新建成功后 admin.promo_saved，SHOP-TASK-272 复制后的 admin.promo_copied 同此写法）：换页前存下，新页面挂载时对同一 ID 取用一次。
let pendingNotice: { id: number; key: CopyKey } | null = null;

export function stashPromotionNotice(id: number, key: CopyKey): void {
  pendingNotice = { id, key };
}

// 取出并清掉；存的不是这个 ID 时为 null（也一并清掉，不留给之后的页面）。
export function takePromotionNotice(id: number): CopyKey | null {
  const found = pendingNotice;
  pendingNotice = null;
  return found !== null && found.id === id ? found.key : null;
}

// 操作栏下方的结果提示：成功（role 为 status）、版本不符等（admin.promo_changed 与 admin.reload）、取值不合格（admin.promo_invalid 下逐条列出）、
// 与其他已发布活动时间重叠（admin.promo_overlap）、网络中断与其他失败。
export type EditNotice =
  | { kind: "done"; key: CopyKey }
  | { kind: "changed" }
  | { kind: "invalid"; products: string[]; publishMissing: boolean; other: boolean }
  | { kind: "overlap"; name: string }
  | { kind: "alert"; key: "common.network_check" | "common.error_retry" };

// 编辑区：promotion 为最近一次读取或保存给的活动（新建尚未保存时为 null），base 为比较基准，form 为已填的值；locked 为保存时服务端答 rules_locked 后
// 时间与同用开关（及规则）只读，直到离开本页或 admin.reload；checked 为点过保存而字段检查不合格，此后按当前的值在字段旁提示；saving 为保存进行中。
export interface Editor {
  promotion: AdminPromotion | null;
  csrfToken: string;
  base: PromotionForm;
  form: PromotionForm;
  locked: boolean;
  checked: boolean;
  saving: boolean;
  notice: EditNotice | null;
}

// 页面的状态：读取中（标 aria-busy）、只有提示与 admin.promo_back（不存在、读取失败），或编辑区。
export type EditState = { status: "loading" } | { status: "gone"; error: CopyKey } | { status: "ready"; editor: Editor };

export const LOADING: EditState = { status: "loading" };

export function openEditor(promotion: AdminPromotion | null, csrfToken: string, notice: EditNotice | null = null): Editor {
  const base = promotion === null ? newPromotionForm() : formFromPromotion(promotion);
  return { promotion, csrfToken, base, form: base, locked: false, checked: false, saving: false, notice };
}

// 按状态的可改范围（UX A12「状态与可用操作」）：已结束全部只读、不显示保存；进行中（或保存时被答 rules_locked）开始与结束时间、同用开关只读。
export function isEnded(editor: Editor): boolean {
  return editor.promotion?.state === "ended";
}

export function timesLocked(editor: Editor): boolean {
  return editor.locked || editor.promotion?.state === "running" || isEnded(editor);
}

// admin.promo_rules_locked：进行中，或保存时被答 rules_locked；已结束时改为 admin.promo_ended_readonly。
export function showsRulesLocked(editor: Editor): boolean {
  return !isEnded(editor) && (editor.locked || editor.promotion?.state === "running");
}

export function hasUnsavedChanges(state: EditState): boolean {
  return state.status === "ready" && isDirty(state.editor.form, state.editor.base);
}

// 字段旁要提示的问题：点过保存而不合格之后，按当前的值重新检查。
export function shownProblems(editor: Editor): PromotionFormProblem[] {
  return editor.checked ? formProblems(editor.form) : [];
}

// 新建的读取（只为取 CSRF 令牌）：取到时为空表单；401 回到 A01；网络中断 common.network_check、其他失败 common.error_retry，都只带 admin.promo_back。
export function newReadStep(read: PromotionsRead): EditState | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", editor: openEditor(null, read.csrf_token) };
    case "none":
      return "login";
    case "network":
      return { status: "gone", error: "common.network_check" };
    case "failed":
      return { status: "gone", error: "common.error_retry" };
  }
}

// 已有活动的读取：取到时以它为基准（notice 为挂载时取到的一次性提示）；401 回到 A01；不存在 common.error_retry；网络中断与其他失败同新建。
export function existingReadStep(read: PromotionRead, notice: CopyKey | null = null): EditState | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", editor: openEditor(read.promotion, read.csrf_token, notice === null ? null : { kind: "done", key: notice }) };
    case "none":
      return "login";
    case "missing":
    case "failed":
      return { status: "gone", error: "common.error_retry" };
    case "network":
      return { status: "gone", error: "common.network_check" };
  }
}

// 取值不合格时 admin.promo_invalid 下的各条：scope_overlap 每条一行（有商品名称时），publish_missing 只一次，其余问题码合起来只一次
// admin.promo_invalid_other（rules_locked 不在其中）。没有要列的为 null。
export function invalidNotice(problems: readonly PromotionProblem[]): EditNotice | null {
  const products: string[] = [];
  let publishMissing = false;
  let other = false;
  for (const problem of problems) {
    if (problem.code === "rules_locked") {
      continue;
    }
    if (problem.code === "scope_overlap" && problem.product_name !== null) {
      products.push(problem.product_name);
    } else if (problem.code === "publish_missing") {
      publishMissing = true;
    } else {
      other = true;
    }
  }
  return products.length === 0 && !publishMissing && !other ? null : { kind: "invalid", products, publishMissing, other };
}

// rules_locked：开始与结束时间、同用开关与规则恢复为基准的值并只读，其余修改保留。
export function lockRules(editor: Editor): Editor {
  const { base, form } = editor;
  return {
    ...editor,
    locked: true,
    form: {
      ...form,
      starts_at: base.starts_at,
      ends_at: base.ends_at,
      coupon_combinable: base.coupon_combinable,
      points_combinable: base.points_combinable,
      rules: base.rules,
    },
  };
}

// 对当前状态的一次改动：只改保存进行中的编辑区（离开或重新读取之后的结果不覆盖新的页面）。
export type EditChange = (current: EditState) => EditState;

function withSavingEditor(change: (editor: Editor) => Editor): EditChange {
  return (current) => (current.status === "ready" && current.editor.saving ? { status: "ready", editor: change(current.editor) } : current);
}

function settle(notice: EditNotice | null): EditChange {
  return withSavingEditor((editor) => ({ ...editor, saving: false, notice }));
}

// 保存的结果之后要做的：回到 A01、换到新建活动的页面、重新取 CSRF 令牌，或改动当前状态。
export type SaveStep = { kind: "login" } | { kind: "created"; id: number } | { kind: "csrf" } | { kind: "change"; change: EditChange };

// 按新建或保存的结果决定（落实写法第（3）（4）点）：
// - 成功：新建换到新 ID 的页面；保存以返回的活动为新基准（锁定不解除），显示 admin.promo_saved。
// - invalid：有 ended_readonly 按 changed 处理；有 rules_locked 时恢复并锁定时间与开关，其余问题照常列出；其余逐条列出。
// - overlap 显示 admin.promo_overlap；changed、state 与 ended 显示 admin.promo_changed 与 admin.reload；missing 只剩 common.error_retry 与 admin.promo_back；
//   401 回到 A01；csrf 重新取令牌；网络中断 common.network_check，其他失败 common.error_retry。
export function saveStep(write: PromotionWrite, created: boolean): SaveStep {
  switch (write.kind) {
    case "ok": {
      const { promotion } = write;
      if (created) {
        return { kind: "created", id: promotion.id };
      }
      return {
        kind: "change",
        change: withSavingEditor((editor) => ({
          ...openEditor(promotion, editor.csrfToken, { kind: "done", key: "admin.promo_saved" }),
          locked: editor.locked,
        })),
      };
    }
    case "none":
      return { kind: "login" };
    case "csrf":
      return { kind: "csrf" };
    case "invalid": {
      const codes = write.problems.map((problem) => problem.code);
      if (codes.includes("ended_readonly")) {
        return { kind: "change", change: settle({ kind: "changed" }) };
      }
      const notice = invalidNotice(write.problems);
      if (codes.includes("rules_locked")) {
        return { kind: "change", change: withSavingEditor((editor) => ({ ...lockRules(editor), saving: false, notice })) };
      }
      return { kind: "change", change: settle(notice) };
    }
    case "overlap":
      return { kind: "change", change: settle({ kind: "overlap", name: write.name }) };
    case "changed":
    case "state":
    case "ended":
      return { kind: "change", change: settle({ kind: "changed" }) };
    case "missing":
      return {
        kind: "change",
        change: (current) => (current.status === "ready" && current.editor.saving ? { status: "gone", error: "common.error_retry" } : current),
      };
    case "network":
      return { kind: "change", change: settle({ kind: "alert", key: "common.network_check" }) };
    case "failed":
      return { kind: "change", change: settle({ kind: "alert", key: "common.error_retry" }) };
  }
}

// 403 之后重新取 CSRF 令牌的结果：取到时只换令牌（表单、基准与版本号都不变），显示 common.error_retry，管理员再点保存；401 回到 A01；
// 其他失败只保留 common.error_retry、不再重试。
export function csrfStep(read: PromotionRead | PromotionsRead): EditChange | "login" {
  if (read.kind === "none") {
    return "login";
  }
  const notice: EditNotice = { kind: "alert", key: "common.error_retry" };
  if (read.kind === "ok") {
    const csrfToken = read.csrf_token;
    return withSavingEditor((editor) => ({ ...editor, csrfToken, saving: false, notice }));
  }
  return settle(notice);
}

// 点保存：字段检查不合格时不提交，只在字段旁提示；合格时开始提交（按钮禁用并标 aria-busy，先前的提示收起）。已结束不提交。
export function saveAction(editor: Editor): "invalid" | "submit" | null {
  if (editor.saving || isEnded(editor)) {
    return null;
  }
  return formProblems(editor.form).length > 0 ? "invalid" : "submit";
}

export function rejectSave(editor: Editor): Editor {
  return { ...editor, checked: true, notice: null };
}

export function startSaving(editor: Editor): Editor {
  return { ...editor, checked: false, saving: true, notice: null };
}

// 点保存后的编辑区：字段不合格时只标出（不调用 submit、不发请求），合格时以 submit 提交并进入保存中；保存中与已结束时不变。
export function pressSave(editor: Editor, submit: (editor: Editor) => void): Editor {
  const action = saveAction(editor);
  if (action === "invalid") {
    return rejectSave(editor);
  }
  if (action === "submit") {
    submit(editor);
    return startSaving(editor);
  }
  return editor;
}

// 改值：收起操作栏下方的提示。时间与同用开关只读时不改，已结束时都不改。
export type EditableField = "name" | "starts_at" | "ends_at" | "coupon_combinable" | "points_combinable";

export function changeField<K extends EditableField>(editor: Editor, field: K, value: PromotionForm[K]): Editor {
  if (isEnded(editor) || (field !== "name" && timesLocked(editor))) {
    return editor;
  }
  return { ...editor, form: { ...editor.form, [field]: value }, notice: null };
}

export function changeCopy(editor: Editor, field: CopyField, value: string): Editor {
  if (isEnded(editor)) {
    return editor;
  }
  return { ...editor, form: { ...editor.form, copy: { ...editor.form.copy, [field]: value } }, notice: null };
}

// 本页用到的请求，测试以替身代替。
export interface PromotionApi {
  readList: typeof readAdminPromotions;
  readOne: typeof readAdminPromotion;
  create: typeof createAdminPromotion;
  save: typeof saveAdminPromotion;
}

export const PROMOTION_API: PromotionApi = {
  readList: readAdminPromotions,
  readOne: readAdminPromotion,
  create: createAdminPromotion,
  save: saveAdminPromotion,
};

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 换页（A01 与新建后的新 ID，不留本页的历史记录）。
export interface EditMoves {
  show: (state: EditState) => void;
  update: (change: EditChange) => void;
  replace: (path: RoutePath) => void;
}

// 读取（打开页面与 admin.reload）：段值不合法时不发请求；signal 中止（离开页面）之后不再更新。notice 为挂载时取到的一次性提示。
export async function loadEditor(
  target: EditTarget,
  signal: AbortSignal,
  moves: EditMoves,
  notice: CopyKey | null = null,
  api: PromotionApi = PROMOTION_API,
): Promise<void> {
  if (target.kind === "invalid") {
    moves.show({ status: "gone", error: "common.error_retry" });
    return;
  }
  const step = target.kind === "new" ? newReadStep(await api.readList(signal)) : existingReadStep(await api.readOne(target.id, signal), notice);
  if (signal.aborted) {
    return;
  }
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// admin.reload：先显示读取中、不再确认，本页的修改与锁定都丢掉，不自动合并。
export async function reloadEditor(target: EditTarget, signal: AbortSignal, moves: EditMoves, api: PromotionApi = PROMOTION_API): Promise<void> {
  moves.show(LOADING);
  await loadEditor(target, signal, moves, null, api);
}

// 提交（字段检查在调用之前，见 saveAction）：新建为 createAdminPromotion，已有活动为 saveAdminPromotion（带读取到的 version），都带 CSRF 令牌。
// 新建成功时先存一次性提示再换到新 ID 的页面。403 时重新取令牌（已有活动用 readAdminPromotion、新建用 readAdminPromotions，只取 csrf_token）。
export async function submitPromotion(editor: Editor, signal: AbortSignal, moves: EditMoves, api: PromotionApi = PROMOTION_API): Promise<void> {
  const { promotion, form, csrfToken } = editor;
  const write =
    promotion === null
      ? await api.create(promotionInput(form), csrfToken, signal)
      : await api.save(promotion.id, { ...promotionInput(form), version: promotion.version }, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = saveStep(write, promotion === null);
  switch (step.kind) {
    case "login":
      moves.replace(ADMIN_LOGIN_PATH);
      return;
    case "created":
      stashPromotionNotice(step.id, "admin.promo_saved");
      moves.replace(adminPromotionPath(step.id));
      return;
    case "change":
      moves.update(step.change);
      return;
    case "csrf":
      break;
  }
  const read = promotion === null ? await api.readList(signal) : await api.readOne(promotion.id, signal);
  if (signal.aborted) {
    return;
  }
  const reread = csrfStep(read);
  if (reread === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.update(reread);
  }
}

// 各区块的展开状态：首次渲染（含服务端渲染）全部展开；挂载后在手机宽度上收起基本信息之外的区块（规则区由 SHOP-TASK-271 起照此保持展开）。
export type Panel = "basic" | "combine" | "texts";
export type PanelsOpen = Readonly<Record<Panel, boolean>>;

export const ALL_OPEN: PanelsOpen = { basic: true, combine: true, texts: true };

export const PHONE_QUERY = "(max-width: 767px)";

export function phonePanels(current: PanelsOpen, isPhone: boolean): PanelsOpen {
  return isPhone ? { ...current, combine: false, texts: false } : current;
}

export type MatchesQuery = (query: string) => boolean;

function browserMatches(): MatchesQuery | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" ? (query) => window.matchMedia(query).matches : null;
}

// 挂载后按一次宽度收起（之后不随宽度变化，由管理员折叠或展开）；没有 matchMedia 时保持全部展开。
export function collapseOnPhone(matches: MatchesQuery | null, setPanels: Dispatch<SetStateAction<PanelsOpen>>): void {
  if (matches === null) {
    return;
  }
  const isPhone = matches(PHONE_QUERY);
  setPanels((current) => phonePanels(current, isPhone));
}

// 文案区的四项（每项三语，字段名为「<项>_<语言>」）及其标签。
const COPY_GROUPS = [
  { group: "title", label: "admin.copy_title" },
  { group: "subtitle", label: "admin.copy_subtitle" },
  { group: "badge", label: "admin.copy_badge" },
  { group: "strip", label: "admin.copy_strip" },
] as const satisfies readonly { group: string; label: CopyKey }[];

export function copyField(group: (typeof COPY_GROUPS)[number]["group"], language: Language): CopyField {
  return `${group}_${language}`;
}

// 已用字数「<已用>/<上限>」（copyLength 与该项上限）。
export function copyCount(field: CopyField, value: string): string {
  return `${String(copyLength(value))}/${String(copyMaxLength(field))}`;
}

export interface EditHandlers {
  onField: <K extends EditableField>(field: K, value: PromotionForm[K]) => void;
  onCopy: (field: CopyField, value: string) => void;
  onSave: () => void;
  onReload: () => void;
  onPanel: (panel: Panel, open: boolean) => void;
}

// 一个字段与其旁的 admin.promo_field_invalid（不合格时）；输入框以 aria-describedby 指向提示。
function Field({ className, invalid, children }: { className?: string; invalid: boolean; children: (describedBy: string | undefined) => ReactNode }) {
  const t = useCopy();
  const id = useId();
  return (
    <div className={className === undefined ? "site-admin-promo-edit__field" : `site-admin-promo-edit__field ${className}`}>
      {children(invalid ? id : undefined)}
      {invalid && (
        <span id={id} className="site-admin-promo-edit__field-invalid">
          {t("admin.promo_field_invalid")}
        </span>
      )}
    </div>
  );
}

// 区块：details（summary 为区块标题），展开状态由页面保存，之后由管理员折叠或展开。
function PanelBox({ panel, title, open, onPanel, children }: { panel: Panel; title: CopyKey; open: boolean; onPanel: EditHandlers["onPanel"]; children: ReactNode }) {
  const t = useCopy();
  return (
    <details
      className="acs-admin__panel site-admin-promo-edit__panel"
      open={open}
      onToggle={(event: SyntheticEvent<HTMLDetailsElement>) => {
        onPanel(panel, event.currentTarget.open);
      }}
    >
      <summary>{t(title)}</summary>
      <div className="site-admin-promo-edit__body">{children}</div>
    </details>
  );
}

// 结果提示（A12-states 的保存结果）：成功为 role="status"；admin.promo_changed 带次要按钮 admin.reload；取值不合格在 admin.promo_invalid 下逐条列出。
function NoticeView({ notice, onReload }: { notice: EditNotice; onReload: () => void }) {
  const t = useCopy();
  switch (notice.kind) {
    case "done":
      return (
        <p className="site-admin-promo-edit__done" role="status">
          {t(notice.key)}
        </p>
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
    case "invalid":
      return (
        <div className="acs-admin__alert site-admin-promo-edit__invalid" role="alert">
          <p>{t("admin.promo_invalid")}</p>
          <ul>
            {notice.products.map((product, index) => (
              <li key={index}>{t("admin.promo_scope_overlap", { product })}</li>
            ))}
            {notice.publishMissing && <li>{t("admin.promo_publish_missing")}</li>}
            {notice.other && <li>{t("admin.promo_invalid_other")}</li>}
          </ul>
        </div>
      );
    case "overlap":
      return (
        <div className="acs-admin__alert" role="alert">
          <span>{t("admin.promo_overlap", { name: notice.name })}</span>
        </div>
      );
    case "alert":
      return <AdminAlert error={notice.key} />;
  }
}

function BackLink() {
  const t = useCopy();
  return (
    <Link className="site-admin-promo-edit__back" to={ADMIN_PROMOTIONS_PATH}>
      {t("admin.promo_back")}
    </Link>
  );
}

// 编辑区：标题行（admin.promo_back、已保存的内部名称与状态标签；新建尚未保存时只有 admin.promo_back）、操作栏（common.save，已结束时不显示）、
// 结果提示、演示说明，再下为基本信息、同用开关与文案三个区块。
function EditorView({ editor, panels, handlers }: { editor: Editor; panels: PanelsOpen; handlers: EditHandlers }) {
  const t = useCopy();
  const { promotion, form } = editor;
  const ended = isEnded(editor);
  const locked = timesLocked(editor);
  const problems = shownProblems(editor);
  const invalid = (field: string) => problems.some((problem) => problem.field === field);
  return (
    <form
      className="site-admin-promo-edit"
      noValidate
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        handlers.onSave();
      }}
    >
      <div className="site-admin-promo-edit__title">
        <BackLink />
        {promotion !== null && (
          <div className="site-admin-promo-edit__heading">
            <h1 className="acs-admin__h">{promotion.name}</h1>
            <span className={PROMOTION_STATE_CLASS[promotion.state]}>{t(PROMOTION_STATE_LABEL[promotion.state])}</span>
          </div>
        )}
      </div>
      {!ended && (
        <div className="acs-admin__panel site-admin-promo-edit__actions">
          <button className="acs-admin__btn" type="submit" disabled={editor.saving} aria-busy={editor.saving}>
            {t("common.save")}
          </button>
        </div>
      )}
      {editor.notice !== null && <NoticeView notice={editor.notice} onReload={handlers.onReload} />}
      {ended && <p className="site-admin-promo-edit__state">{t("admin.promo_ended_readonly")}</p>}
      <p className="site-admin-promo-edit__note">
        <span>{t("admin.promo_demo_note")}</span> <span>{t("admin.rules_apply_new")}</span>
      </p>
      <PanelBox panel="basic" title="admin.promo_basic" open={panels.basic} onPanel={handlers.onPanel}>
        <Field className="site-admin-promo-edit__name" invalid={invalid("name")}>
          {(describedBy) => (
            <label className="acs-admin__field">
              {t("admin.promo_name")}
              <input
                className="acs-admin__input"
                name="name"
                value={form.name}
                maxLength={NAME_MAX_LENGTH}
                readOnly={ended}
                aria-invalid={describedBy !== undefined}
                aria-describedby={describedBy}
                onChange={(event: ChangeEvent<HTMLInputElement>) => {
                  handlers.onField("name", event.target.value);
                }}
              />
            </label>
          )}
        </Field>
        <div className="site-admin-promo-edit__times">
          {(["starts_at", "ends_at"] as const).map((field) => (
            <Field key={field} invalid={invalid(field)}>
              {(describedBy) => (
                <label className="acs-admin__field">
                  {t(field === "starts_at" ? "admin.promo_start" : "admin.promo_end")}
                  <input
                    className="acs-admin__input"
                    type="datetime-local"
                    name={field}
                    value={form[field]}
                    readOnly={locked}
                    aria-invalid={describedBy !== undefined}
                    aria-describedby={describedBy}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => {
                      handlers.onField(field, event.target.value);
                    }}
                  />
                </label>
              )}
            </Field>
          ))}
        </div>
        <span className="acs-admin__muted">{t("admin.promo_time_note")}</span>
        {showsRulesLocked(editor) && <p className="site-admin-promo-edit__state">{t("admin.promo_rules_locked")}</p>}
      </PanelBox>
      <PanelBox panel="combine" title="admin.promo_combine" open={panels.combine} onPanel={handlers.onPanel}>
        <div className="site-admin-promo-edit__checks">
          {(
            [
              ["coupon_combinable", "admin.promo_allow_coupon"],
              ["points_combinable", "admin.promo_allow_points"],
            ] as const
          ).map(([field, label]) => (
            <label key={field} className="site-admin-promo-edit__check">
              <input
                type="checkbox"
                name={field}
                checked={form[field]}
                disabled={locked}
                onChange={(event: ChangeEvent<HTMLInputElement>) => {
                  handlers.onField(field, event.target.checked);
                }}
              />
              {t(label)}
            </label>
          ))}
        </div>
        <span className="acs-admin__muted">{t("admin.promo_combine_hint")}</span>
      </PanelBox>
      <PanelBox panel="texts" title="admin.promo_texts" open={panels.texts} onPanel={handlers.onPanel}>
        <div className="site-admin-promo-edit__langs">
          {LANGUAGES.map((language) => (
            <fieldset key={language} className="site-admin-promo-edit__lang" lang={htmlLang(language)}>
              <legend>{t(LANGUAGE_LABEL[language])}</legend>
              {COPY_GROUPS.map(({ group, label }) => {
                const field = copyField(group, language);
                return (
                  <Field key={field} invalid={invalid(`copy.${field}`)}>
                    {(describedBy) => (
                      <label className="acs-admin__field">
                        <span className="site-admin-promo-edit__copy-label">
                          <span>{t(label)}</span>
                          <span className="acs-admin__muted">{copyCount(field, form.copy[field])}</span>
                        </span>
                        <input
                          className="acs-admin__input"
                          name={field}
                          value={form.copy[field]}
                          readOnly={ended}
                          aria-invalid={describedBy !== undefined}
                          aria-describedby={describedBy}
                          onChange={(event: ChangeEvent<HTMLInputElement>) => {
                            handlers.onCopy(field, event.target.value);
                          }}
                        />
                      </label>
                    )}
                  </Field>
                );
              })}
            </fieldset>
          ))}
        </div>
        <span className="acs-admin__muted">{t("admin.copy_fallback")}</span>
      </PanelBox>
    </form>
  );
}

export interface AdminPromotionEditViewProps {
  state: EditState;
  panels: PanelsOpen;
  handlers: EditHandlers;
}

// 内容区：读取中只有 admin.promo_back（标 aria-busy）；不存在与读取失败只有 admin.promo_back 与提示；取到后为编辑区。
export function AdminPromotionEditView({ state, panels, handlers }: AdminPromotionEditViewProps) {
  if (state.status === "ready") {
    return <EditorView editor={state.editor} panels={panels} handlers={handlers} />;
  }
  return (
    <div className="site-admin-promo-edit" aria-busy={state.status === "loading"}>
      <div className="site-admin-promo-edit__title">
        <BackLink />
      </div>
      {state.status === "gone" && <AdminAlert error={state.error} />}
    </div>
  );
}

function pageMoves(setState: Dispatch<SetStateAction<EditState>>, replace: (path: RoutePath) => void): EditMoves {
  return {
    show: (state) => {
      setState(state);
    },
    update: (change) => {
      setState(change);
    },
    replace,
  };
}

// 框架的内容区：框架确认已登录后才挂载，挂载时取用一次性提示并读取一次；离开页面时中止。
export function AdminPromotionEditContent() {
  const { pattern, params, replace } = useRouter();
  const segment = params.id;
  const target = useMemo(() => editTarget(pattern, segment === undefined ? {} : { id: segment }), [pattern, segment]);
  const [state, setState] = useState<EditState>(LOADING);
  const [panels, setPanels] = useState<PanelsOpen>(ALL_OPEN);
  // 本页的请求（读取、保存、admin.reload 与 403 后重新取令牌）共用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    collapseOnPhone(browserMatches(), setPanels);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      const notice = target.kind === "existing" ? takePromotionNotice(target.id) : null;
      void loadEditor(target, controller.signal, pageMoves(setState, replace), notice);
    });
  }, [target, replace]);

  useUnsavedConfirm(hasUnsavedChanges(state));

  const edit = (change: (current: Editor) => Editor) => {
    setState((current) => (current.status === "ready" ? { status: "ready", editor: change(current.editor) } : current));
  };

  const handlers: EditHandlers = {
    onField: (field, value) => {
      edit((current) => changeField(current, field, value));
    },
    onCopy: (field, value) => {
      edit((current) => changeCopy(current, field, value));
    },
    onSave: () => {
      const controller = lifetime.current;
      if (state.status !== "ready" || controller === null) {
        return;
      }
      const next = pressSave(state.editor, (editor) => {
        void submitPromotion(editor, controller.signal, pageMoves(setState, replace));
      });
      if (next !== state.editor) {
        setState({ status: "ready", editor: next });
      }
    },
    onReload: () => {
      const controller = lifetime.current;
      if (state.status !== "ready" || state.editor.saving || controller === null) {
        return;
      }
      void reloadEditor(target, controller.signal, pageMoves(setState, replace));
    },
    onPanel: (panel, open) => {
      setPanels((current) => (current[panel] === open ? current : { ...current, [panel]: open }));
    },
  };

  return <AdminPromotionEditView state={state} panels={panels} handlers={handlers} />;
}

export default function AdminPromotionEditPage() {
  return (
    <AdminFrame current="promotions">
      <AdminPromotionEditContent />
    </AdminFrame>
  );
}
