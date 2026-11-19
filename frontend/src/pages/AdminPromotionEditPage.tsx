import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ChangeEvent, Dispatch, FormEvent, KeyboardEvent, ReactNode, SetStateAction, SyntheticEvent } from "react";

import type { AiProposal } from "../api/adminPromotionAi";
import type { PosterSlot } from "../api/adminPromotionPoster";
import {
  copyAdminPromotion,
  createAdminPromotion,
  deleteAdminPromotion,
  endAdminPromotion,
  MAX_PROMOTION_ID,
  publishAdminPromotion,
  readAdminPromotion,
  readAdminPromotions,
  saveAdminPromotion,
  withdrawAdminPromotion,
} from "../api/adminPromotions";
import type { AdminPromotion, PromotionDelete, PromotionProblem, PromotionRead, PromotionsRead, PromotionState, PromotionWrite } from "../api/adminPromotions";
import AdminFrame, { AdminAlert, LANGUAGE_LABEL, useUnsavedConfirm } from "../components/AdminFrame";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_PROMOTION_NEW_PATH, ADMIN_PROMOTIONS_PATH, adminPromotionPath, adminPromotionPreviewPath, Link, useRouter } from "../router";
import type { RouteParams, RoutePath, RoutePattern } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import AdminPromotionAi, { aiRuleProblems, applyProposal } from "./AdminPromotionAi";
import AdminPromotionDecoration from "./AdminPromotionDecoration";
import type { DecorationChange } from "./AdminPromotionDecoration";
import AdminPromotionEditorPreview from "./AdminPromotionEditorPreview";
import AdminPromotionPoster, { pickPosterMode } from "./AdminPromotionPoster";
import AdminPromotionRules,{ loadScopeLists, SCOPE_LOADING } from "./AdminPromotionRules";
import type { RulesChange, ScopeLists } from "./AdminPromotionRules";
import { PROMOTION_STATE_CLASS, PROMOTION_STATE_LABEL } from "./AdminPromotionsPage";
import type { RereadOutcome } from "./AdminStoreLogo";
import { copyLength, copyMaxLength, formFromPromotion, formProblems, isDirty, NAME_MAX_LENGTH, newPromotionForm, promotionInput } from "./promotionForm";
import type { CopyField, PromotionForm, PromotionFormProblem } from "./promotionForm";

// 后台促销活动编辑区 A12（docs/UX.md 0.20 A12「编辑区」「状态与可用操作」与说明的「时间」「文案」两条，视觉稿 A12-editor-desktop、A12-phone、A12-states；
// HANDOFF 0.98 落实写法；DESIGN 1.23「节日促销活动」第 1–2、12 条）：新建 /admin/promotions/new 与编辑 /admin/promotions/:id（段值为活动的内部 ID）共用本页，
// 用后台框架渲染，当前导航项为促销活动。本任务（SHOP-TASK-270）是编辑区的框架：读取、标题行、基本信息、同用开关、文案、操作栏的保存与结果提示、
// 提交前的字段检查与离开确认；SHOP-TASK-271 在基本信息之后、同用开关与文案之前接上规则区（AdminPromotionRules.tsx，规则的修改计入未保存与提交）；
// SHOP-TASK-273 在文案之后接上装饰区（AdminPromotionDecoration.tsx，装饰的修改计入未保存与提交；已结束只读）；
// SHOP-TASK-275 在装饰区之后接上海报区（AdminPromotionPoster.tsx，海报模式与替代文字计入未保存与提交；海报图上传即替换、立即生效、不随保存提交、
// 不改版本，所以只换 promotion 上的海报图，不改表单、基准与版本号）；SHOP-TASK-274 在装饰区末尾接上编辑区预览（AdminPromotionEditorPreview.tsx，
// 传入当前的表单与已保存的桌面海报图，随未保存的输入即时更新，任何状态都显示）；SHOP-TASK-276 在海报区之后接上 AI 区（AdminPromotionAi.tsx，描述只在该区
// 内存里、不进表单与提交；点 admin.ai_apply 才把提案的装饰与 12 个文案字段填回表单，计入未保存、经普通保存提交；已结束只读）；SHOP-TASK-272 在操作栏
// 保存之外接上状态操作（发布、撤回、提前结束、复制、删除与前台预览，按已保存的状态显示，手机收在 admin.promo_more 里；见下方 STATE_ACTIONS 起的各段）。
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
// 与其他已发布活动时间重叠（admin.promo_overlap）、网络中断与其他失败；有未保存修改时点状态操作或预览为 admin.promo_save_first（SHOP-TASK-272）。
export type EditNotice =
  | { kind: "done"; key: CopyKey }
  | { kind: "saveFirst" }
  | { kind: "changed" }
  | { kind: "invalid"; products: string[]; publishMissing: boolean; other: boolean }
  | { kind: "overlap"; name: string }
  | { kind: "alert"; key: "common.network_check" | "common.error_retry" };

// 编辑区：promotion 为最近一次读取或保存给的活动（新建尚未保存时为 null），base 为比较基准，form 为已填的值；locked 为保存时服务端答 rules_locked 后
// 时间与同用开关（及规则）只读，直到离开本页或 admin.reload；checked 为点过保存而字段检查不合格，此后按当前的值在字段旁提示；aiChecked 为点过
// admin.ai_generate 而规则参数不合格，此后按当前的值只在规则参数旁提示（SHOP-TASK-276）；saving 为写请求（保存或状态操作）进行中，此时操作栏的按钮都禁用；
// acting 为进行中的状态操作（保存时为 null）；confirm 为打开着的确认框；more 为手机上 admin.promo_more 的菜单是否展开（SHOP-TASK-272）。
export interface Editor {
  promotion: AdminPromotion | null;
  csrfToken: string;
  base: PromotionForm;
  form: PromotionForm;
  locked: boolean;
  checked: boolean;
  aiChecked: boolean;
  saving: boolean;
  acting: StateAction | null;
  confirm: ConfirmAction | null;
  more: boolean;
  notice: EditNotice | null;
}

// 页面的状态：读取中（标 aria-busy）、只有提示与 admin.promo_back（不存在、读取失败），或编辑区。
export type EditState = { status: "loading" } | { status: "gone"; error: CopyKey } | { status: "ready"; editor: Editor };

export const LOADING: EditState = { status: "loading" };

export function openEditor(promotion: AdminPromotion | null, csrfToken: string, notice: EditNotice | null = null): Editor {
  const base = promotion === null ? newPromotionForm() : formFromPromotion(promotion);
  return {
    promotion,
    csrfToken,
    base,
    form: base,
    locked: false,
    checked: false,
    aiChecked: false,
    saving: false,
    acting: null,
    confirm: null,
    more: false,
    notice,
  };
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

// 字段旁要提示的问题：点过保存而不合格之后，按当前的值重新检查；只点过 admin.ai_generate 而规则参数不合格时，只按当前的值查规则参数（照保存时的写法标在字段旁）。
export function shownProblems(editor: Editor): PromotionFormProblem[] {
  if (editor.checked) {
    return formProblems(editor.form);
  }
  return editor.aiChecked ? aiRuleProblems(editor.form) : [];
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

// 对当前状态的一次改动：只改写请求进行中的编辑区（离开或重新读取之后的结果不覆盖新的页面）。
export type EditChange = (current: EditState) => EditState;

function withSavingEditor(change: (editor: Editor) => Editor): EditChange {
  return (current) => (current.status === "ready" && current.editor.saving ? { status: "ready", editor: change(current.editor) } : current);
}

function settle(notice: EditNotice | null): EditChange {
  return withSavingEditor((editor) => ({ ...editor, saving: false, acting: null, notice }));
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
        return { kind: "change", change: withSavingEditor((editor) => ({ ...lockRules(editor), saving: false, acting: null, notice })) };
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
    return withSavingEditor((editor) => ({ ...editor, csrfToken, saving: false, acting: null, notice }));
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
  return { ...editor, checked: false, aiChecked: false, saving: true, notice: null };
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

// 规则的增删、排序与改值（落实写法（5））：进行中、已结束与 rules_locked 锁定之后（同 timesLocked）规则只读，不改；规则不变时编辑区不变。
export function changeRules(editor: Editor, change: RulesChange): Editor {
  if (timesLocked(editor)) {
    return editor;
  }
  const rules = change(editor.form);
  return rules === editor.form.rules ? editor : { ...editor, form: { ...editor.form, rules }, notice: null };
}

// 素材包、配色与各装饰位的改动（落实写法（4））：已结束时不改，进行中与其他状态可改；装饰不变时编辑区不变。
export function changeDecoration(editor: Editor, change: DecorationChange): Editor {
  if (isEnded(editor)) {
    return editor;
  }
  const decoration = change(editor.form.decoration);
  return decoration === editor.form.decoration ? editor : { ...editor, form: { ...editor.form, decoration }, notice: null };
}

// 海报图上传成功（落实写法（3））：只换该活动该位置的海报图，表单、基准、版本号与操作栏下方的提示都不变（不算未保存的修改，之后的保存照读取到的版本）；
// 编辑区已换成别的活动时不变。
export function replacePoster(editor: Editor, promotionId: number, slot: PosterSlot, poster: string): Editor {
  const { promotion } = editor;
  if (promotion?.id !== promotionId) {
    return editor;
  }
  const key = slot === "desktop" ? "poster_desktop_url" : "poster_phone_url";
  return promotion[key] === poster ? editor : { ...editor, promotion: { ...promotion, [key]: poster } };
}

// AI 区点 admin.ai_apply（SHOP-TASK-276 落实写法（3））：提案的装饰（pack、palette 与六个装饰位）与 12 个文案字段填回表单，计入未保存的修改、不保存；
// poster_mode、替代文字、名称、规则、时间与开关不变，收起操作栏下方的提示。已结束时不改。
export function applyAiProposal(editor: Editor, proposal: AiProposal): Editor {
  if (isEnded(editor)) {
    return editor;
  }
  return { ...editor, form: applyProposal(editor.form, proposal), notice: null };
}

// AI 区点 admin.ai_generate 做过检查（落实写法（3））：规则参数不合格时此后在规则区字段旁提示，合格时收起只因 AI 而起的提示。
export function checkAiRules(editor: Editor, rulesInvalid: boolean): Editor {
  return editor.aiChecked === rulesInvalid ? editor : { ...editor, aiChecked: rulesInvalid };
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

// 海报图上传被答 403 之后重新取 CSRF 令牌（readAdminPromotion，只取 csrf_token）：取到时只换该活动编辑区的令牌（表单、基准、海报图与版本号都不变）；
// 401 为 login，网络中断 network，其余失败 failed（海报区照 A08 的 rereadNotice 提示）。signal 中止之后不再更新。
export async function rereadPosterToken(
  promotionId: number,
  signal: AbortSignal,
  update: (change: EditChange) => void,
  api: PromotionApi = PROMOTION_API,
): Promise<RereadOutcome> {
  const read = await api.readOne(promotionId, signal);
  if (signal.aborted) {
    return "failed";
  }
  switch (read.kind) {
    case "ok": {
      const csrfToken = read.csrf_token;
      update((current) =>
        current.status === "ready" && current.editor.promotion?.id === promotionId ? { status: "ready", editor: { ...current.editor, csrfToken } } : current,
      );
      return "ok";
    }
    case "none":
      return "login";
    case "network":
      return "network";
    case "missing":
    case "failed":
      return "failed";
  }
}

// AI 提案被答 403 之后重新取 CSRF 令牌（SHOP-TASK-276 落实写法（4））：已有活动同 rereadPosterToken；新建尚未保存（promotionId 为 null）时以 readAdminPromotions
// 只取 csrf_token，取到时只换新建编辑区的令牌（表单不变）。401 为 login，网络中断 network，其余失败 failed；signal 中止之后不再更新。
export async function rereadAiToken(
  promotionId: number | null,
  signal: AbortSignal,
  update: (change: EditChange) => void,
  api: PromotionApi = PROMOTION_API,
): Promise<RereadOutcome> {
  if (promotionId !== null) {
    return rereadPosterToken(promotionId, signal, update, api);
  }
  const read = await api.readList(signal);
  if (signal.aborted) {
    return "failed";
  }
  switch (read.kind) {
    case "ok": {
      const csrfToken = read.csrf_token;
      update((current) =>
        current.status === "ready" && current.editor.promotion === null ? { status: "ready", editor: { ...current.editor, csrfToken } } : current,
      );
      return "ok";
    }
    case "none":
      return "login";
    case "network":
      return "network";
    case "failed":
      return "failed";
  }
}

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

// 状态操作（SHOP-TASK-272，UX A12「状态与可用操作」与说明的「提前结束、撤回、删除」「前台预览」两条，HANDOFF 0.98 落实写法（1）–（5））。
// 保存之外的操作：发请求的五个，与只打开新标签页的预览；先弹确认框的为撤回、提前结束与删除。
export type StateAction = "publish" | "withdraw" | "end" | "copy" | "delete";
export type BarAction = StateAction | "preview";
export type ConfirmAction = "withdraw" | "end" | "delete";

// 按已保存的状态显示的操作（common.save 之外，依次照 UX 线框；不可用的不显示）：草稿为发布、复制、删除、预览；未开始为撤回、复制、预览；
// 进行中为提前结束、复制；已结束只有复制。新建尚未保存时没有。
export const STATE_ACTIONS: Readonly<Record<PromotionState, readonly BarAction[]>> = {
  draft: ["publish", "copy", "delete", "preview"],
  scheduled: ["withdraw", "copy", "preview"],
  running: ["end", "copy"],
  ended: ["copy"],
};

export function barActions(editor: Editor): readonly BarAction[] {
  return editor.promotion === null ? [] : STATE_ACTIONS[editor.promotion.state];
}

export const ACTION_LABEL: Readonly<Record<BarAction, CopyKey>> = {
  publish: "admin.promo_publish",
  withdraw: "admin.promo_withdraw",
  end: "admin.promo_end_now",
  copy: "admin.promo_copy",
  delete: "admin.promo_delete",
  preview: "admin.promo_preview",
};

export const CONFIRM_TEXT: Readonly<Record<ConfirmAction, CopyKey>> = {
  withdraw: "admin.promo_withdraw_confirm",
  end: "admin.promo_end_confirm",
  delete: "admin.promo_delete_confirm",
};

function needsConfirm(action: BarAction): action is ConfirmAction {
  return action === "withdraw" || action === "end" || action === "delete";
}

// 马来西亚当前时刻（落实写法（5））：当前 UTC 时间加 8 小时取到分钟，写成「YYYY-MM-DDTHH:MM」。
export function malaysiaMinute(nowMs: number): string {
  return new Date(nowMs + 8 * 60 * 60 * 1000).toISOString().slice(0, 16);
}

// 前台预览的禁用条件（UX A12 说明「前台预览」）：已保存的开始或结束时间为空，或已保存的结束时间不晚于 now（同为「YYYY-MM-DDTHH:MM」，按字符串比较）。
export function previewNeedsTime(promotion: AdminPromotion, now: string): boolean {
  const startsAt = promotion.starts_at ?? "";
  const endsAt = promotion.ends_at ?? "";
  return startsAt === "" || endsAt === "" || endsAt <= now;
}

// 在新标签页打开前台预览（带 noopener）。
export function openPreview(path: RoutePath, target: Pick<Window, "open"> = window): void {
  target.open(path, "_blank", "noopener");
}

// 点操作的副作用：run 发状态操作的请求，open 在新标签页打开前台预览。
export interface ActionEffects {
  run: (editor: Editor, action: StateAction) => void;
  open: (path: RoutePath) => void;
}

// 开始一次状态操作：按钮都禁用、该操作标 aria-busy，确认框与菜单收起，先前的提示收起。
export function startAction(editor: Editor, action: StateAction): Editor {
  return { ...editor, saving: true, acting: action, confirm: null, more: false, notice: null };
}

// 点操作栏的操作（落实写法（2）（3）（5））：写请求进行中、该状态不提供的操作不变。删除之外有未保存修改时只显示 admin.promo_save_first（不发请求、
// 不打开预览）；预览按钮禁用时不变，否则打开 adminPromotionPreviewPath；撤回、提前结束与删除打开确认框（删除有未保存修改时同样只弹确认框）；
// 发布与复制立即提交。now 为马来西亚当前时刻（malaysiaMinute）。点过之后手机菜单收起。
export function pressAction(editor: Editor, action: BarAction, now: string, effects: ActionEffects): Editor {
  const { promotion } = editor;
  if (editor.saving || promotion === null || !barActions(editor).includes(action)) {
    return editor;
  }
  if (action !== "delete" && isDirty(editor.form, editor.base)) {
    return { ...editor, more: false, notice: { kind: "saveFirst" } };
  }
  if (action === "preview") {
    if (previewNeedsTime(promotion, now)) {
      return editor;
    }
    effects.open(adminPromotionPreviewPath(promotion.id));
    return editor.more ? { ...editor, more: false } : editor;
  }
  if (needsConfirm(action)) {
    return { ...editor, more: false, confirm: action };
  }
  effects.run(editor, action);
  return startAction(editor, action);
}

// 确认框里点该操作本身：提交并关闭确认框；没有确认框或写请求进行中时不变。
export function confirmAction(editor: Editor, run: ActionEffects["run"]): Editor {
  const { confirm } = editor;
  if (confirm === null || editor.saving) {
    return editor;
  }
  run(editor, confirm);
  return startAction(editor, confirm);
}

// 确认框里点 common.cancel 或按 Escape：只关闭确认框。
export function cancelConfirm(editor: Editor): Editor {
  return editor.confirm === null ? editor : { ...editor, confirm: null };
}

// 手机上点 admin.promo_more：切换菜单的显隐。
export function toggleMore(editor: Editor): Editor {
  return { ...editor, more: !editor.more };
}

// 状态操作的结果之后要做的：同保存（SaveStep，状态操作不会给 created），另有复制成功换到新草稿、删除成功回到列表。
export type ActionStep = SaveStep | { kind: "copied"; id: number } | { kind: "deleted" };

// 按状态操作的结果决定（落实写法（4））：发布、撤回与提前结束成功后以返回的活动为新基准（状态标签与可改范围随之更新，保存时的锁定一并解除），
// 显示 admin.promo_saved；复制成功为 copied（新草稿的 ID），删除成功为 deleted；失败的提示与保存相同（saveStep）。
export function actionStep(action: StateAction, result: PromotionWrite | PromotionDelete): ActionStep {
  if (result.kind !== "ok") {
    return saveStep(result, false);
  }
  if (!("promotion" in result)) {
    return { kind: "deleted" };
  }
  const { promotion } = result;
  if (action === "copy") {
    return { kind: "copied", id: promotion.id };
  }
  return { kind: "change", change: withSavingEditor((editor) => openEditor(promotion, editor.csrfToken, { kind: "done", key: "admin.promo_saved" })) };
}

// 状态操作用到的请求，测试以替身代替。
export interface PromotionActionApi {
  readOne: typeof readAdminPromotion;
  publish: typeof publishAdminPromotion;
  withdraw: typeof withdrawAdminPromotion;
  end: typeof endAdminPromotion;
  copy: typeof copyAdminPromotion;
  remove: typeof deleteAdminPromotion;
}

export const PROMOTION_ACTION_API: PromotionActionApi = {
  readOne: readAdminPromotion,
  publish: publishAdminPromotion,
  withdraw: withdrawAdminPromotion,
  end: endAdminPromotion,
  copy: copyAdminPromotion,
  remove: deleteAdminPromotion,
};

// 状态操作的换页另有站内跳转（复制成功后进入新草稿，留下本页的历史记录）。
export interface ActionMoves extends EditMoves {
  navigate: (path: RoutePath) => void;
}

// 发出一次状态操作：都作用于已保存的版本（带读取到的 version，复制只带 ID），带 CSRF 令牌。
function sendAction(action: StateAction, promotion: AdminPromotion, csrfToken: string, signal: AbortSignal, api: PromotionActionApi): Promise<PromotionWrite | PromotionDelete> {
  const { id, version } = promotion;
  switch (action) {
    case "publish":
      return api.publish(id, version, csrfToken, signal);
    case "withdraw":
      return api.withdraw(id, version, csrfToken, signal);
    case "end":
      return api.end(id, version, csrfToken, signal);
    case "copy":
      return api.copy(id, csrfToken, signal);
    case "delete":
      return api.remove(id, version, csrfToken, signal);
  }
}

// 提交状态操作（是否可点、确认与未保存修改的判断在调用之前，见 pressAction 与 confirmAction）。复制成功先存一次性提示 admin.promo_copied，再以站内跳转
// 进入 adminPromotionPath(新草稿 ID)；删除成功以 replace 回到列表；401 回到 A01；403 时照保存重新取令牌（readAdminPromotion，只取 csrf_token）。
// signal 中止（离开页面）之后不再更新。
export async function runAction(
  editor: Editor,
  action: StateAction,
  signal: AbortSignal,
  moves: ActionMoves,
  api: PromotionActionApi = PROMOTION_ACTION_API,
): Promise<void> {
  const { promotion, csrfToken } = editor;
  if (promotion === null) {
    return;
  }
  const result = await sendAction(action, promotion, csrfToken, signal, api);
  if (signal.aborted) {
    return;
  }
  const step = actionStep(action, result);
  switch (step.kind) {
    case "login":
      moves.replace(ADMIN_LOGIN_PATH);
      return;
    case "copied":
      stashPromotionNotice(step.id, "admin.promo_copied");
      moves.navigate(adminPromotionPath(step.id));
      return;
    case "deleted":
      moves.replace(ADMIN_PROMOTIONS_PATH);
      return;
    case "change":
      moves.update(step.change);
      return;
    case "created":
      // 只有新建的保存会给 created，状态操作不会。
      return;
    case "csrf":
      break;
  }
  const reread = csrfStep(await api.readOne(promotion.id, signal));
  if (signal.aborted) {
    return;
  }
  if (reread === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.update(reread);
  }
}

// 各区块的展开状态：首次渲染（含服务端渲染）全部展开；挂载后在手机宽度上收起基本信息与规则区之外的区块。
export type Panel = "basic" | "rules" | "combine" | "texts" | "decoration" | "poster" | "ai";
export type PanelsOpen = Readonly<Record<Panel, boolean>>;

export const ALL_OPEN: PanelsOpen = { basic: true, rules: true, combine: true, texts: true, decoration: true, poster: true, ai: true };

export const PHONE_QUERY = "(max-width: 767px)";

export function phonePanels(current: PanelsOpen, isPhone: boolean): PanelsOpen {
  return isPhone ? { ...current, combine: false, texts: false, decoration: false, poster: false, ai: false } : current;
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
  onRules: (change: RulesChange) => void;
  onDecoration: (change: DecorationChange) => void;
  onSave: () => void;
  onReload: () => void;
  onPanel: (panel: Panel, open: boolean) => void;
  // 海报区：上传成功换上海报图、403 后重新取令牌、401 回到 A01。
  onPoster: (promotionId: number, slot: PosterSlot, poster: string) => void;
  onPosterReread: (promotionId: number, signal: AbortSignal) => Promise<RereadOutcome>;
  onLogin: () => void;
  // AI 区：点 admin.ai_apply 填回提案、点 admin.ai_generate 之后的规则参数检查、403 后重新取令牌（新建尚未保存时 promotionId 为 null）。
  onAiApply: (proposal: AiProposal) => void;
  onAiRulesChecked: (rulesInvalid: boolean) => void;
  onAiReread: (promotionId: number | null, signal: AbortSignal) => Promise<RereadOutcome>;
  // 状态操作（SHOP-TASK-272）：点操作栏的操作、确认框里点该操作或取消、手机上切换 admin.promo_more 的菜单。
  onAction: (action: BarAction) => void;
  onConfirm: () => void;
  onCancelConfirm: () => void;
  onMore: () => void;
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
    case "saveFirst":
      return (
        <p className="site-admin-promo-edit__save-first" role="status">
          {t("admin.promo_save_first")}
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

// 操作栏（A12-states「各状态的操作组合」与 A12-phone）：common.save（已结束不显示）之后为按已保存状态的操作（STATE_ACTIONS）。桌面并排；手机上它们收在
// admin.promo_more 的菜单里，admin.promo_more 切换菜单的显隐（aria-expanded 标明是否展开，桌面不显示它）。写请求进行中时都禁用，进行中的那一个标
// aria-busy。前台预览禁用时其下为 admin.promo_preview_needs_time（按钮以 aria-describedby 指向它）。已结束只有 admin.promo_copy，为主要按钮，其余为次要按钮。
export function PromotionActionBar({ editor, now, handlers }: { editor: Editor; now: string; handlers: EditHandlers }) {
  const t = useCopy();
  const menuId = useId();
  const noteId = useId();
  const { promotion } = editor;
  const ended = isEnded(editor);
  const actions = barActions(editor);
  return (
    <div className="acs-admin__panel site-admin-promo-edit__actions">
      {!ended && (
        <button className="acs-admin__btn" type="submit" disabled={editor.saving} aria-busy={editor.saving && editor.acting === null}>
          {t("common.save")}
        </button>
      )}
      {promotion !== null && actions.length > 0 && (
        <>
          <button
            className="acs-admin__btn acs-admin__btn--secondary site-admin-promo-edit__more"
            type="button"
            aria-expanded={editor.more}
            aria-controls={menuId}
            onClick={handlers.onMore}
          >
            {t("admin.promo_more")}
          </button>
          <div id={menuId} className={editor.more ? "acs-admin__panel site-admin-promo-edit__menu site-admin-promo-edit__menu--open" : "acs-admin__panel site-admin-promo-edit__menu"}>
            {actions.map((action) => {
              const press = () => {
                handlers.onAction(action);
              };
              if (action === "preview") {
                const blocked = previewNeedsTime(promotion, now);
                return (
                  <span key={action} className="site-admin-promo-edit__preview">
                    <button
                      className="acs-admin__btn acs-admin__btn--secondary"
                      type="button"
                      disabled={editor.saving || blocked}
                      aria-describedby={blocked ? noteId : undefined}
                      onClick={press}
                    >
                      {t(ACTION_LABEL[action])}
                    </button>
                    {blocked && (
                      <span id={noteId} className="acs-admin__muted">
                        {t("admin.promo_preview_needs_time")}
                      </span>
                    )}
                  </span>
                );
              }
              return (
                <button
                  key={action}
                  className={ended ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary"}
                  type="button"
                  disabled={editor.saving}
                  aria-busy={editor.acting === action}
                  onClick={press}
                >
                  {t(ACTION_LABEL[action])}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// 确认框（UX A12 说明「提前结束、撤回、删除都先弹确认框」，照 A10 的 site-admin__confirm）：admin.promo_end_confirm、admin.promo_withdraw_confirm 或
// admin.promo_delete_confirm，按钮依次为 common.cancel（次要，打开时取得焦点）与该操作本身（主要）；Escape 等同取消。
export function PromotionConfirm({ action, onCancel, onConfirm }: { action: ConfirmAction; onCancel: () => void; onConfirm: () => void }) {
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
        <p id={labelId}>{t(CONFIRM_TEXT[action])}</p>
        <div className="site-admin__confirm-actions">
          <button className="acs-admin__btn acs-admin__btn--secondary" type="button" autoFocus onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button className="acs-admin__btn" type="button" onClick={onConfirm}>
            {t(ACTION_LABEL[action])}
          </button>
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  const t = useCopy();
  return (
    <Link className="site-admin-promo-edit__back" to={ADMIN_PROMOTIONS_PATH}>
      {t("admin.promo_back")}
    </Link>
  );
}

// 编辑区：标题行（admin.promo_back、已保存的内部名称与状态标签；新建尚未保存时只有 admin.promo_back）、操作栏（common.save，已结束时不显示；
// 其后为按状态的操作）、结果提示、演示说明，再下为基本信息、规则、同用开关、文案、装饰、海报与 AI 七个区块，最后为打开着的确认框。
// lists 为规则范围用的分类与商品列表，now 为马来西亚当前时刻（前台预览的禁用条件）。
function EditorView({ editor, panels, lists, now, handlers }: { editor: Editor; panels: PanelsOpen; lists: ScopeLists; now: string; handlers: EditHandlers }) {
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
      <PromotionActionBar editor={editor} now={now} handlers={handlers} />
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
      <PanelBox panel="rules" title="admin.promo_rules" open={panels.rules} onPanel={handlers.onPanel}>
        <AdminPromotionRules form={form} readOnly={locked} lists={lists} invalid={invalid} onRules={handlers.onRules} />
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
      <PanelBox panel="decoration" title="admin.promo_decoration" open={panels.decoration} onPanel={handlers.onPanel}>
        <AdminPromotionDecoration
          decoration={form.decoration}
          readOnly={ended}
          onDecoration={handlers.onDecoration}
          preview={<AdminPromotionEditorPreview form={form} poster={promotion?.poster_desktop_url ?? null} />}
        />
      </PanelBox>
      <PanelBox panel="poster" title="admin.promo_poster" open={panels.poster} onPanel={handlers.onPanel}>
        <AdminPromotionPoster
          promotionId={promotion?.id ?? null}
          posters={{ desktop: promotion?.poster_desktop_url ?? null, phone: promotion?.poster_phone_url ?? null }}
          csrfToken={editor.csrfToken}
          mode={form.decoration.poster_mode}
          copy={form.copy}
          readOnly={ended}
          invalid={invalid}
          onMode={(mode) => {
            handlers.onDecoration((decoration) => pickPosterMode(decoration, mode));
          }}
          onAlt={handlers.onCopy}
          onPoster={handlers.onPoster}
          onReread={handlers.onPosterReread}
          onLogin={handlers.onLogin}
          onReload={handlers.onReload}
        />
      </PanelBox>
      <PanelBox panel="ai" title="admin.ai_title" open={panels.ai} onPanel={handlers.onPanel}>
        <AdminPromotionAi
          form={form}
          readOnly={ended}
          csrfToken={editor.csrfToken}
          onApply={handlers.onAiApply}
          onRulesChecked={handlers.onAiRulesChecked}
          onReread={(signal) => handlers.onAiReread(promotion?.id ?? null, signal)}
          onLogin={handlers.onLogin}
        />
      </PanelBox>
      {editor.confirm !== null && <PromotionConfirm action={editor.confirm} onCancel={handlers.onCancelConfirm} onConfirm={handlers.onConfirm} />}
    </form>
  );
}

export interface AdminPromotionEditViewProps {
  state: EditState;
  panels: PanelsOpen;
  handlers: EditHandlers;
  // 规则范围用的列表；没给时为读取中。
  lists?: ScopeLists;
  // 马来西亚当前时刻（malaysiaMinute）；没给（挂载后取得时钟之前）时为空串，前台预览只按已保存的时间是否为空判断禁用。
  now?: string;
}

// 内容区：读取中只有 admin.promo_back（标 aria-busy）；不存在与读取失败只有 admin.promo_back 与提示；取到后为编辑区。
export function AdminPromotionEditView({ state, panels, handlers, lists = SCOPE_LOADING, now = "" }: AdminPromotionEditViewProps) {
  if (state.status === "ready") {
    return <EditorView editor={state.editor} panels={panels} lists={lists} now={now} handlers={handlers} />;
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
  const { pattern, params, replace, navigate } = useRouter();
  const segment = params.id;
  const target = useMemo(() => editTarget(pattern, segment === undefined ? {} : { id: segment }), [pattern, segment]);
  const [state, setState] = useState<EditState>(LOADING);
  const [panels, setPanels] = useState<PanelsOpen>(ALL_OPEN);
  const [lists, setLists] = useState<ScopeLists>(SCOPE_LOADING);
  // 马来西亚当前时刻（前台预览的禁用条件）：编辑区打开后取一次，之后每次点操作栏的操作时更新。
  const [now, setNow] = useState("");
  // 本页的请求（读取、保存、admin.reload 与 403 后重新取令牌）共用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);
  const opened = state.status === "ready";

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

  // 编辑区打开时各读一次规则范围用的分类与商品列表（落实写法（4））；离开页面或重新读取编辑区时中止。
  useEffect(() => {
    if (!opened) {
      return undefined;
    }
    const controller = new AbortController();
    return startDeferred(controller, () => {
      void loadScopeLists(controller.signal, { show: setLists, replace });
    });
  }, [opened, replace]);

  useEffect(() => {
    if (!opened) {
      return undefined;
    }
    return startDeferred(new AbortController(), () => {
      setNow(malaysiaMinute(Date.now()));
    });
  }, [opened]);

  useUnsavedConfirm(hasUnsavedChanges(state));

  const edit = (change: (current: Editor) => Editor) => {
    setState((current) => (current.status === "ready" ? { status: "ready", editor: change(current.editor) } : current));
  };

  // 状态操作的提交：用本页的中止，复制成功以站内跳转进入新草稿。
  const actionRunner =
    (controller: AbortController): ActionEffects["run"] =>
    (editor, action) => {
      void runAction(editor, action, controller.signal, { ...pageMoves(setState, replace), navigate });
    };

  const handlers: EditHandlers = {
    onField: (field, value) => {
      edit((current) => changeField(current, field, value));
    },
    onCopy: (field, value) => {
      edit((current) => changeCopy(current, field, value));
    },
    onRules: (change) => {
      edit((current) => changeRules(current, change));
    },
    onDecoration: (change) => {
      edit((current) => changeDecoration(current, change));
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
    onPoster: (promotionId, slot, poster) => {
      edit((current) => replacePoster(current, promotionId, slot, poster));
    },
    onPosterReread: (promotionId, signal) => rereadPosterToken(promotionId, signal, setState),
    onLogin: () => {
      replace(ADMIN_LOGIN_PATH);
    },
    onAiApply: (proposal) => {
      edit((current) => applyAiProposal(current, proposal));
    },
    onAiRulesChecked: (rulesInvalid) => {
      edit((current) => checkAiRules(current, rulesInvalid));
    },
    onAiReread: (promotionId, signal) => rereadAiToken(promotionId, signal, setState),
    onAction: (action) => {
      const controller = lifetime.current;
      if (state.status !== "ready" || controller === null) {
        return;
      }
      const current = malaysiaMinute(Date.now());
      setNow(current);
      const next = pressAction(state.editor, action, current, {
        run: actionRunner(controller),
        open: (path) => {
          openPreview(path);
        },
      });
      if (next !== state.editor) {
        setState({ status: "ready", editor: next });
      }
    },
    onConfirm: () => {
      const controller = lifetime.current;
      if (state.status !== "ready" || controller === null) {
        return;
      }
      const next = confirmAction(state.editor, actionRunner(controller));
      if (next !== state.editor) {
        setState({ status: "ready", editor: next });
      }
    },
    onCancelConfirm: () => {
      edit(cancelConfirm);
    },
    onMore: () => {
      edit(toggleMore);
    },
  };

  return <AdminPromotionEditView state={state} panels={panels} handlers={handlers} lists={lists} now={now} />;
}

export default function AdminPromotionEditPage() {
  return (
    <AdminFrame current="promotions">
      <AdminPromotionEditContent />
    </AdminFrame>
  );
}
