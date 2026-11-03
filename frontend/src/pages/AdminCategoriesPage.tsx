import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, Dispatch, FormEvent, KeyboardEvent, SetStateAction } from "react";

import { createAdminCategory, readAdminCategories, saveAdminCategory } from "../api/adminCategories";
import type { AdminCategory, CategoriesRead, CategoryCreate, CategorySave, CategoryWrite } from "../api/adminCategories";
import AdminFrame, { AdminAlert, LANGUAGE_LABEL, useUnsavedConfirm } from "../components/AdminFrame";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";

// A10 后台分类（docs/UX.md 0.17 A10，视觉稿 A10-desktop、A10-phone 与 A10-states；HANDOFF 0.81 第（3）点）：用后台框架渲染，当前导航项为分类。
// 内容区为标题 admin.nav_categories 与 common.create、admin.categories_count、全部分类（桌面表格、手机卡片，两套都渲染，由 site.css 按宽度显隐），
// 点 common.edit 或 common.create 后才显示编辑区（桌面在列表右侧；手机隐藏列表，编辑区上方为页内的 common.back）。
// 打开页面时调用一次 GET /api/admin/categories，切换界面语言不重新读取（分类数据与界面语言无关）；新建为 POST、保存为 PUT
// （经 api/adminCategories.ts；取值核对、并发核对与审计在服务端，DESIGN 1.18「计价、优惠、积分与库存」第 14 条）。名称原样提交，
// 页面只先查「勾选启用而英文名称为空（去掉首尾空白后）」一种。401 换成登录页 A01；离开页面时中止进行中的请求，之后不再更新页面。
// 有未保存的修改（编辑区的三项名称或启用状态与最近一次读取或保存的不同）时，以 useUnsavedConfirm（SHOP-TASK-197）接入离开确认。

// 分类上限（UX A10「至多 30 个」，admin.categories_count 的 {max}）。
export const MAX_CATEGORIES = 30;

// 编辑区的值：三语名称（null 当作空串）与启用状态。
export interface CategoryForm {
  names: Readonly<Record<Language, string>>;
  is_active: boolean;
}

// 新建表单的初值：三项空串、勾选启用（UX A10「启用默认勾选」）。
export const NEW_FORM: CategoryForm = { names: { en: "", zh: "", ms: "" }, is_active: true };

// 读取或保存返回的分类换成编辑区的值：名称为 null 时当作空串。
export function formOf(category: AdminCategory): CategoryForm {
  return { names: { en: category.name_en ?? "", zh: category.name_zh ?? "", ms: category.name_ms ?? "" }, is_active: category.is_active };
}

// 保存按钮下方的提示：成功（role 为 status）、取值不合格、分类已被改动（带 admin.reload）、网络中断与其他失败。
export type SaveNotice =
  | "admin.category_saved"
  | "admin.category_created"
  | "admin.category_invalid"
  | "admin.category_changed"
  | "common.network_check"
  | "common.error_retry";

// 编辑区：category 为正在编辑的分类（最近一次读取或保存给的，含 version、slug 与 visible_products；新建时为 null），base 为比较基线，
// form 为已填的值，content 为内容语言，saving 为保存进行中，notice 为保存按钮下方的提示，alert 为表单上方的提示（403 后重新读取，不随改值收起），
// confirming 为停用确认框打开。
export interface Editor {
  category: AdminCategory | null;
  base: CategoryForm;
  form: CategoryForm;
  content: Language;
  saving: boolean;
  notice: SaveNotice | null;
  alert: CopyKey | null;
  confirming: boolean;
}

// 页面的状态：读取中（标 aria-busy）、读取失败（只有提示）、已取到（按接口顺序的分类、只用于写请求头的 CSRF 令牌与编辑区；没有打开编辑区时为 null）。
export type CategoriesState =
  | { status: "loading" }
  | { status: "failed"; error: CopyKey }
  | { status: "ready"; categories: AdminCategory[]; csrfToken: string; editor: Editor | null };

export type ReadyState = Extract<CategoriesState, { status: "ready" }>;

export const LOADING: CategoriesState = { status: "loading" };

// 打开编辑区：已有分类以它的值为基线，新建以 NEW_FORM 为基线；内容语言默认英文，没有提示。
export function openEditor(category: AdminCategory | null): Editor {
  const base = category === null ? NEW_FORM : formOf(category);
  return { category, base, form: base, content: "en", saving: false, notice: null, alert: null, confirming: false };
}

// 有未保存的修改：编辑区的三项名称或启用状态与基线不同。没有编辑区时没有修改。
export function isDirty(editor: Editor | null): boolean {
  if (editor === null) {
    return false;
  }
  const { base, form } = editor;
  return form.is_active !== base.is_active || LANGUAGES.some((language) => form.names[language] !== base.names[language]);
}

// 页面先查的唯一一种：勾选启用而英文名称为空（去掉首尾空白后），不提交、显示 admin.category_invalid；其余取值由服务端判断。
export function precheck(form: CategoryForm): "admin.category_invalid" | null {
  return form.is_active && form.names.en.trim() === "" ? "admin.category_invalid" : null;
}

// 停用确认：编辑已有分类、把启用改为不启用、且该分类的 visible_products 大于 0。
export function needsDisableConfirm(editor: Editor): boolean {
  return editor.category !== null && editor.base.is_active && !editor.form.is_active && editor.category.visible_products > 0;
}

// 点 common.save 之后的去向：页面先查不合格（invalid）、先弹停用确认框（confirm）或直接提交（submit）。
export type SaveAction = "invalid" | "confirm" | "submit";

export function saveAction(editor: Editor): SaveAction {
  if (precheck(editor.form) !== null) {
    return "invalid";
  }
  return needsDisableConfirm(editor) ? "confirm" : "submit";
}

// 缺译提示：内容语言为中文或马来文，且该语言的名称为空（去掉首尾空白后）。
export function translationMissing(editor: Editor): boolean {
  return editor.content !== "en" && editor.form.names[editor.content].trim() === "";
}

// 名称列：英文名称，为空或 null 时为 —。
export function listName(category: AdminCategory): string {
  return category.name_en === null || category.name_en === "" ? "—" : category.name_en;
}

// 已满 30 个：common.create 禁用并显示 admin.categories_full。
export function isFull(categories: readonly AdminCategory[]): boolean {
  return categories.length >= MAX_CATEGORIES;
}

// common.create 只在已取到列表且未满时可点。
export function canCreate(state: CategoriesState): boolean {
  return state.status === "ready" && !isFull(state.categories);
}

// 新建的内容（三项名称原样与启用状态）与保存的内容（另加 version）。
export function createInput(form: CategoryForm): CategoryCreate {
  return { name_en: form.names.en, name_zh: form.names.zh, name_ms: form.names.ms, is_active: form.is_active };
}

export function saveInput(form: CategoryForm, version: number): CategorySave {
  return { ...createInput(form), version };
}

// 读取后要显示的编辑区：没有（打开页面）、新建表单，或某个分类（按 ID）。
export type EditTarget = number | "new" | null;

export function targetOf(editor: Editor | null): EditTarget {
  if (editor === null) {
    return null;
  }
  return editor.category === null ? "new" : editor.category.id;
}

// 读取后按 target 打开编辑区：同一分类显示读取到的最新数据，已不存在时收起编辑区。
export function editorFor(categories: readonly AdminCategory[], target: EditTarget): Editor | null {
  if (target === null) {
    return null;
  }
  if (target === "new") {
    return openEditor(null);
  }
  const category = categories.find((item) => item.id === target);
  return category === undefined ? null : openEditor(category);
}

// 按读取结果决定（打开页面与 admin.reload）：取到时显示列表，并按 target 打开编辑区；401 回到 A01（login）；
// 网络中断 common.network_check，其他失败 common.error_retry。
export function readStep(read: CategoriesRead, target: EditTarget = null): CategoriesState | "login" {
  switch (read.kind) {
    case "ok":
      return { status: "ready", categories: read.categories, csrfToken: read.csrf_token, editor: editorFor(read.categories, target) };
    case "none":
      return "login";
    case "network":
      return { status: "failed", error: "common.network_check" };
    case "failed":
      return { status: "failed", error: "common.error_retry" };
  }
}

// 对当前状态的一次改动（请求的结果按结果到达时的状态更新）。
export type CategoriesChange = (current: CategoriesState) => CategoriesState;

// 以返回的分类更新列表：同一 ID 的那一行换成它，新建的排在末尾（接口按 ID 从小到大）。
export function withCategory(categories: readonly AdminCategory[], category: AdminCategory): AdminCategory[] {
  return categories.some((item) => item.id === category.id)
    ? categories.map((item) => (item.id === category.id ? category : item))
    : [...categories, category];
}

// 只改保存进行中的编辑区：保存期间又切换到别的编辑区（经未保存确认）时，结果不覆盖新的编辑区。
function withSavingEditor(current: CategoriesState, change: (editor: Editor) => Editor): CategoriesState {
  if (current.status !== "ready" || current.editor === null || !current.editor.saving) {
    return current;
  }
  return { ...current, editor: change(current.editor) };
}

function failedSave(notice: SaveNotice): CategoriesChange {
  return (current) => withSavingEditor(current, (editor) => ({ ...editor, saving: false, notice }));
}

// 按新建或保存的结果决定：成功以返回的分类更新列表与编辑区（新建后进入该分类的编辑区，基线改为返回的值，内容语言不变），显示
// admin.category_saved（新建为 admin.category_created）；401 回到 A01；403 重新读取（reread）；取值不合格 admin.category_invalid、
// 分类已被改动 admin.category_changed、网络中断 common.network_check（不自动重新读取）、其他失败 common.error_retry，都保留已填的值。
export function saveStep(write: CategoryWrite, created: boolean): CategoriesChange | "login" | "reread" {
  switch (write.kind) {
    case "ok":
      return (current) => {
        if (current.status !== "ready") {
          return current;
        }
        const next = { ...current, categories: withCategory(current.categories, write.category) };
        return withSavingEditor(next, (editor) => ({
          ...openEditor(write.category),
          content: editor.content,
          notice: created ? "admin.category_created" : "admin.category_saved",
        }));
      };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "invalid":
      return failedSave("admin.category_invalid");
    case "changed":
      return failedSave("admin.category_changed");
    case "network":
      return failedSave("common.network_check");
    case "failed":
      return failedSave("common.error_retry");
  }
}

// 403 之后重新读取的结果：取到时换成新的列表与 CSRF 令牌，编辑区仍停在同一分类（或新建表单），已填的值与提交前的 version 都保留
// （category 不换成重新读取的），在表单上方显示 common.error_retry；401 回到 A01；重新读取本身失败时编辑区与列表不变，表单上方显示那次失败。
export function rereadStep(read: CategoriesRead): CategoriesChange | "login" {
  const settle = (alert: CopyKey) => (editor: Editor): Editor => ({ ...editor, saving: false, alert });
  switch (read.kind) {
    case "ok":
      return (current) =>
        current.status === "ready"
          ? withSavingEditor({ ...current, categories: read.categories, csrfToken: read.csrf_token }, settle("common.error_retry"))
          : current;
    case "none":
      return "login";
    case "network":
      return (current) => withSavingEditor(current, settle("common.network_check"));
    case "failed":
      return (current) => withSavingEditor(current, settle("common.error_retry"));
  }
}

// 改内容语言下的名称、改启用状态：收起保存按钮下方的提示；表单上方 403 后的提示不收起（照 A06）。
export function changeName(editor: Editor, value: string): Editor {
  return { ...editor, form: { ...editor.form, names: { ...editor.form.names, [editor.content]: value } }, notice: null };
}

export function changeEnabled(editor: Editor, checked: boolean): Editor {
  return { ...editor, form: { ...editor.form, is_active: checked }, notice: null };
}

// 切换内容语言：只换输入框显示与编辑的语言，各语言已填的值不变。
export function chooseContent(editor: Editor, content: Language): Editor {
  return { ...editor, content };
}

// 页面先查不合格：不提交，显示 admin.category_invalid。
export function rejectSave(editor: Editor): Editor {
  return { ...editor, notice: "admin.category_invalid" };
}

// 提交：按钮禁用并标 aria-busy，先前的提示与停用确认框都收起。
export function startSaving(editor: Editor): Editor {
  return { ...editor, saving: true, notice: null, alert: null, confirming: false };
}

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface CategoriesMoves {
  show: (state: CategoriesState) => void;
  update: (change: CategoriesChange) => void;
  replace: (path: RoutePath) => void;
}

// 读取（打开页面与 admin.reload）：signal 中止（离开页面）之后不再更新。
export async function openCategories(signal: AbortSignal, moves: CategoriesMoves, target: EditTarget = null): Promise<void> {
  const read = await readAdminCategories(signal);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read, target);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 点 admin.reload：先显示读取中、不再确认，未保存的修改丢弃；取到后编辑区显示 target 的最新数据。
export async function reloadCategories(signal: AbortSignal, moves: CategoriesMoves, target: EditTarget): Promise<void> {
  moves.show(LOADING);
  await openCategories(signal, moves, target);
}

// 新建（编辑区没有 category）以三项名称与启用状态提交，保存以四项另加 category 的 version 提交，都带读取给的 CSRF 令牌。
// 403 时重新读取整页（rereadStep）；signal 中止之后其结果不再更新页面。页面先查与停用确认在调用之前（saveAction）。
export async function submitCategory(csrfToken: string, editor: Editor, signal: AbortSignal, moves: CategoriesMoves): Promise<void> {
  const { category, form } = editor;
  const write =
    category === null
      ? await createAdminCategory(createInput(form), csrfToken, signal)
      : await saveAdminCategory(category.id, saveInput(form, category.version), csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = saveStep(write, category === null);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
    return;
  }
  if (step !== "reread") {
    moves.update(step);
    return;
  }
  const read = await readAdminCategories(signal);
  if (signal.aborted) {
    return;
  }
  const reread = rereadStep(read);
  if (reread === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.update(reread);
  }
}

function pageMoves(setState: Dispatch<SetStateAction<CategoriesState>>, replace: (path: RoutePath) => void): CategoriesMoves {
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

function CategoryStatus({ category }: { category: AdminCategory }) {
  const t = useCopy();
  return (
    <span className={category.is_active ? "acs-tag acs-tag--success" : "acs-tag acs-tag--neutral"}>
      {t(category.is_active ? "admin.category_on" : "admin.category_off")}
    </span>
  );
}

function ChevronIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

interface ListHandlers {
  onEdit: (category: AdminCategory) => void;
}

// 列表：admin.categories_count、已满时 admin.categories_full；桌面表格（名称、slug、前台可见件数、状态与每行的 common.edit），
// 手机卡片（英文名称、admin.category_visible 件数与状态，整张卡片为读屏标签 common.edit 的按钮）。顺序照接口。
function CategoryList({ categories, onEdit }: { categories: readonly AdminCategory[] } & ListHandlers) {
  const t = useCopy();
  return (
    <>
      <span className="acs-admin__muted">{t("admin.categories_count", { count: categories.length, max: MAX_CATEGORIES })}</span>
      {isFull(categories) && <span className="acs-admin__muted">{t("admin.categories_full")}</span>}
      <div className="acs-admin__panel site-admin-categories__table">
        <table className="acs-admin__table">
          <thead>
            <tr>
              <th>{t("admin.category_name")}</th>
              <th>{t("admin.category_slug")}</th>
              <th className="site-admin-categories__num">{t("admin.category_visible")}</th>
              <th>{t("admin.category_enabled")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {categories.map((category) => (
              <tr key={category.id}>
                <td>{listName(category)}</td>
                <td>{category.slug}</td>
                <td className="site-admin-categories__num">{category.visible_products}</td>
                <td>
                  <CategoryStatus category={category} />
                </td>
                <td>
                  <button
                    className="acs-admin__btn acs-admin__btn--secondary"
                    type="button"
                    onClick={() => {
                      onEdit(category);
                    }}
                  >
                    {t("common.edit")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="site-admin-categories__cards">
        {categories.map((category) => (
          <li key={category.id}>
            <button
              className="acs-admin__panel site-admin-categories__card"
              type="button"
              aria-label={t("common.edit")}
              onClick={() => {
                onEdit(category);
              }}
            >
              <span className="site-admin-categories__card-text">
                <span>{listName(category)}</span>
                <span className="acs-admin__muted">
                  {t("admin.category_visible")} {category.visible_products}
                </span>
              </span>
              <CategoryStatus category={category} />
              <span className="site-admin-categories__chevron">
                <ChevronIcon />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

interface EditorHandlers {
  onBack: () => void;
  onContent: (content: Language) => void;
  onName: (value: string) => void;
  onEnabled: (checked: boolean) => void;
  onSave: () => void;
  onReload: () => void;
  onConfirmDisable: () => void;
  onCancelDisable: () => void;
}

// 停用确认框（样式照 SHOP-TASK-197 的未保存确认框）：admin.category_disable_confirm（{count} 为该分类的前台可见件数），
// 按钮依次为 common.cancel（次要，打开时取得焦点）与 admin.category_disable（主要）；Escape 等同取消。
function DisableConfirm({ count, onCancel, onDisable }: { count: number; onCancel: () => void; onDisable: () => void }) {
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
        <p id={labelId}>{t("admin.category_disable_confirm", { count })}</p>
        <div className="site-admin__confirm-actions">
          <button className="acs-admin__btn acs-admin__btn--secondary" type="button" autoFocus onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button className="acs-admin__btn" type="button" onClick={onDisable}>
            {t("admin.category_disable")}
          </button>
        </div>
      </div>
    </div>
  );
}

// 保存按钮与其下的提示（A10-states，照 A06）：成功为 role="status"，其他为 AdminAlert；admin.category_changed 带次要按钮 admin.reload。
function SaveArea({ editor, onReload }: { editor: Editor } & Pick<EditorHandlers, "onReload">) {
  const t = useCopy();
  const { notice } = editor;
  const success = notice === "admin.category_saved" || notice === "admin.category_created";
  return (
    <div className="site-admin-categories__save">
      <button className="acs-admin__btn" type="submit" disabled={editor.saving} aria-busy={editor.saving}>
        {t("common.save")}
      </button>
      {notice !== null && success && <span role="status">{t(notice)}</span>}
      {notice === "admin.category_changed" && (
        <div className="acs-admin__alert site-admin-categories__changed" role="alert">
          <span>{t("admin.category_changed")}</span>
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            onClick={() => {
              onReload();
            }}
          >
            {t("admin.reload")}
          </button>
        </div>
      )}
      {notice !== null && !success && notice !== "admin.category_changed" && <AdminAlert error={notice} />}
    </div>
  );
}

// 编辑区：标题 admin.category_edit（新建时 admin.category_new）、内容语言三个按钮（aria-pressed）、名称输入框与缺译提示、启用勾选框、
// slug（新建时不显示）与常显的 admin.category_slug_hint、common.save；手机上方为 common.back（桌面由 site.css 隐藏）。
function EditorView({ editor, ...handlers }: { editor: Editor } & EditorHandlers) {
  const t = useCopy();
  const languagesId = useId();
  const missingId = useId();
  const { category, content, form } = editor;
  const missing = translationMissing(editor);
  return (
    <div className="site-admin-categories__editor">
      <button className="acs-admin__btn acs-admin__btn--secondary site-admin-categories__back" type="button" onClick={handlers.onBack}>
        {t("common.back")}
      </button>
      {editor.alert !== null && <AdminAlert error={editor.alert} />}
      <form
        className="acs-admin__panel site-admin-categories__form"
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          handlers.onSave();
        }}
      >
        <h2 className="acs-admin__h">{t(category === null ? "admin.category_new" : "admin.category_edit")}</h2>
        <div className="site-admin-categories__languages" role="group" aria-labelledby={languagesId}>
          <span id={languagesId} className="acs-admin__muted">
            {t("admin.content_language")}
          </span>
          {LANGUAGES.map((language) => (
            <button
              key={language}
              className={language === content ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary"}
              type="button"
              lang={htmlLang(language)}
              aria-pressed={language === content}
              onClick={() => {
                handlers.onContent(language);
              }}
            >
              {t(LANGUAGE_LABEL[language])}
            </button>
          ))}
        </div>
        <label className="site-admin-categories__name">
          <span>{t("admin.category_name")}</span>
          <input
            className="acs-admin__input"
            value={form.names[content]}
            lang={htmlLang(content)}
            aria-describedby={missing ? missingId : undefined}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handlers.onName(event.target.value);
            }}
          />
        </label>
        {missing && (
          <p id={missingId} className="site-admin-categories__missing">
            {t("admin.category_translation_missing", { language: t(LANGUAGE_LABEL[content]) })}
          </p>
        )}
        <label className="site-admin-categories__enabled">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handlers.onEnabled(event.target.checked);
            }}
          />
          {t("admin.category_enabled")}
        </label>
        <div className="site-admin-categories__slug">
          {category !== null && (
            <>
              <span>{t("admin.category_slug")}</span>
              <span>{category.slug}</span>
            </>
          )}
          <span className="acs-admin__muted">{t("admin.category_slug_hint")}</span>
        </div>
        <SaveArea editor={editor} onReload={handlers.onReload} />
      </form>
      {editor.confirming && category !== null && (
        <DisableConfirm count={category.visible_products} onCancel={handlers.onCancelDisable} onDisable={handlers.onConfirmDisable} />
      )}
    </div>
  );
}

export interface AdminCategoriesViewProps extends ListHandlers, EditorHandlers {
  state: CategoriesState;
  onCreate: () => void;
}

// 标题与 common.create 之下为列表区：读取中标 aria-busy；读取失败时只有提示。编辑区打开时根元素加 --editing（桌面两栏，手机隐藏列表）。
export function AdminCategoriesView({ state, onEdit, onCreate, ...handlers }: AdminCategoriesViewProps) {
  const t = useCopy();
  const editor = state.status === "ready" ? state.editor : null;
  return (
    <div className={editor === null ? "site-admin-categories" : "site-admin-categories site-admin-categories--editing"}>
      <div className="site-admin-categories__list">
        <div className="site-admin-categories__title">
          <h1 className="acs-admin__h">{t("admin.nav_categories")}</h1>
          <button className="acs-admin__btn" type="button" disabled={!canCreate(state)} onClick={onCreate}>
            {t("common.create")}
          </button>
        </div>
        <div className="site-admin-categories__body" aria-busy={state.status === "loading"}>
          {state.status === "failed" && <AdminAlert error={state.error} />}
          {state.status === "ready" && <CategoryList categories={state.categories} onEdit={onEdit} />}
        </div>
      </div>
      {editor !== null && <EditorView editor={editor} {...handlers} />}
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载时读取一次（切换界面语言不重新读取）。
export function AdminCategoriesContent() {
  const { replace } = useRouter();
  const [state, setState] = useState<CategoriesState>(LOADING);
  // 本页的请求（读取、新建、保存、admin.reload 与 403 后的重新读取）共用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openCategories(controller.signal, pageMoves(setState, replace));
    });
  }, [replace]);

  const editor = state.status === "ready" ? state.editor : null;
  const confirmLeave = useUnsavedConfirm(isDirty(editor));

  const edit = (change: (current: Editor) => Editor) => {
    setState((current) => (current.status === "ready" && current.editor !== null ? { ...current, editor: change(current.editor) } : current));
  };
  const showEditor = (next: Editor | null) => {
    setState((current) => (current.status === "ready" ? { ...current, editor: next } : current));
  };
  const submit = (ready: ReadyState, current: Editor) => {
    const controller = lifetime.current;
    if (controller === null) {
      return;
    }
    setState({ ...ready, editor: startSaving(current) });
    void submitCategory(ready.csrfToken, current, controller.signal, pageMoves(setState, replace));
  };

  return (
    <AdminCategoriesView
      state={state}
      onEdit={(category) => {
        if (editor?.category?.id === category.id) {
          return;
        }
        confirmLeave(() => {
          showEditor(openEditor(category));
        });
      }}
      onCreate={() => {
        if (!canCreate(state)) {
          return;
        }
        confirmLeave(() => {
          showEditor(openEditor(null));
        });
      }}
      onBack={() => {
        confirmLeave(() => {
          showEditor(null);
        });
      }}
      onContent={(content) => {
        edit((current) => chooseContent(current, content));
      }}
      onName={(value) => {
        edit((current) => changeName(current, value));
      }}
      onEnabled={(checked) => {
        edit((current) => changeEnabled(current, checked));
      }}
      onSave={() => {
        if (state.status !== "ready" || state.editor === null || state.editor.saving) {
          return;
        }
        const action = saveAction(state.editor);
        if (action === "invalid") {
          edit(rejectSave);
        } else if (action === "confirm") {
          edit((current) => ({ ...current, confirming: true }));
        } else {
          submit(state, state.editor);
        }
      }}
      onConfirmDisable={() => {
        if (state.status !== "ready" || state.editor === null || state.editor.saving || !state.editor.confirming) {
          return;
        }
        submit(state, state.editor);
      }}
      onCancelDisable={() => {
        edit((current) => ({ ...current, confirming: false }));
      }}
      onReload={() => {
        const controller = lifetime.current;
        if (state.status !== "ready" || controller === null || state.editor?.saving === true) {
          return;
        }
        void reloadCategories(controller.signal, pageMoves(setState, replace), targetOf(state.editor));
      }}
    />
  );
}

export default function AdminCategoriesPage() {
  return (
    <AdminFrame current="categories">
      <AdminCategoriesContent />
    </AdminFrame>
  );
}
