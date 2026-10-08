import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, Dispatch, FormEvent, SetStateAction } from "react";

import { readAdminStoreDesign, saveAdminStoreDesign } from "../api/adminStoreDesign";
import type { AdminStoreDesign, AdminStoreDesignDetail, StoreDesignInput, StoreDesignRead, StoreDesignSave } from "../api/adminStoreDesign";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { SHOP_THEMES, THEME_ACCENTS } from "../storeDesign";
import type { HomeBlock, HomeBlockSetting, ShopTheme } from "../storeDesign";
import { startDeferred } from "./AdminOrderDetail";

// 后台店铺装修 A08（docs/UX.md 0.10 A08，视觉稿 A08-desktop 与 A08-phone）：用后台框架渲染，当前导航项为店铺装修。
// 内容区为标题 admin.nav_store_design（h1）与其下的 admin.design_demo_note；手机线框与 A08-phone 不画标题（顶栏已是
// admin.nav_store_design），h1 仍在页面里，由 site.css 在 767px 及以下只留给读屏。
// 其下为表单（SHOP-TASK-064）：主题（10 款，按 THEME_ACCENTS 的顺序）、所选主题的主色、首页四个区块的顺序与显隐与 common.save。
// 不含标志图（Kelvin 2026-10-06 决定）；精选商品本页只原样提交读取到的 ID 顺序、不显示，挑选与预览由 SHOP-TASK-065、066 接上。
// 打开页面与切换界面语言时以界面语言调用 GET /api/admin/store-design?lang=，保存为 PUT（经 api/adminStoreDesign.ts）。
// 401 换成登录页 A01；离开页面或切换语言时中止读取与进行中的保存，旧请求的结果不再更新页面。

// 主题名（UX-COPY admin.theme_<主题 id>）。
export const THEME_NAME: Readonly<Record<ShopTheme, CopyKey>> = {
  pandan: "admin.theme_pandan",
  pasar: "admin.theme_pasar",
  receipt: "admin.theme_receipt",
  kopitiam: "admin.theme_kopitiam",
  batik: "admin.theme_batik",
  malam: "admin.theme_malam",
  gula: "admin.theme_gula",
  galeri: "admin.theme_galeri",
  songket: "admin.theme_songket",
  litar: "admin.theme_litar",
};

// 区块名（UX-COPY admin.block_hero「其余三块用 home.how_title、home.categories、home.featured」）。
export const BLOCK_NAME: Readonly<Record<HomeBlock, CopyKey>> = {
  hero: "admin.block_hero",
  how: "home.how_title",
  categories: "home.categories",
  featured: "home.featured",
};

// 表单里的选择：主题、所选主色（总是该主题的一个选项）与按位置排序的四个区块。
export interface DesignForm {
  theme: ShopTheme;
  accent: string;
  homeBlocks: readonly HomeBlockSetting[];
}

// 主题的第一项主色（null 即它，storeDesign.ts）。
export function defaultAccent(theme: ShopTheme): string {
  return THEME_ACCENTS[theme][0];
}

// 以读取或保存返回的设置重置表单：主色为 null 时选第一项。
export function formOf(design: AdminStoreDesign): DesignForm {
  return { theme: design.theme, accent: design.accent ?? defaultAccent(design.theme), homeBlocks: design.home_blocks };
}

// 换主题时改选新主题的第一项主色（UX A08「选主题后，主色选项换成该主题的一组（默认选第一项）」）。
export function chooseTheme(form: DesignForm, theme: ShopTheme): DesignForm {
  return theme === form.theme ? form : { ...form, theme, accent: defaultAccent(theme) };
}

export function chooseAccent(form: DesignForm, accent: string): DesignForm {
  return { ...form, accent };
}

export function showBlock(form: DesignForm, index: number, visible: boolean): DesignForm {
  return { ...form, homeBlocks: form.homeBlocks.map((setting, at) => (at === index ? { block: setting.block, visible } : setting)) };
}

// 与上一行（-1）或下一行（1）互换；第一行上移与最后一行下移不变。
export function moveBlock(form: DesignForm, index: number, offset: -1 | 1): DesignForm {
  const blocks = [...form.homeBlocks];
  const moved = blocks[index];
  const other = blocks[index + offset];
  if (moved === undefined || other === undefined) {
    return form;
  }
  blocks[index] = other;
  blocks[index + offset] = moved;
  return { ...form, homeBlocks: blocks };
}

// 保存的请求：主题与读取到的相同、且所选主色就是读取到的主色（读取到 null 时即该主题的第一项）时原样提交读取到的 accent（含 null），
// 否则提交所选主色 id，所以未改动的表单再次保存不改变存储；精选原样提交读取到的商品 ID 顺序。
export function saveInput(base: AdminStoreDesign, form: DesignForm): StoreDesignInput {
  const unchanged = form.theme === base.theme && form.accent === (base.accent ?? defaultAccent(base.theme));
  return {
    theme: form.theme,
    accent: unchanged ? base.accent : form.accent,
    home_blocks: form.homeBlocks,
    featured_product_ids: base.featured.map((product) => product.product_id),
  };
}

// 页面的状态：读取中（标 aria-busy，不显示表单）、读取失败（只有提示）、已取到。已取到时 base 为读取或保存返回的设置
// （保存的响应不含 choices 与 CSRF 令牌，沿用读取的），form 为表单的选择，saving 为保存进行中，saved 为显示 admin.design_saved，
// alert 为表单上方的提示（403 后重新读取），saveError 为保存按钮旁的提示。
export type DesignState =
  | { status: "loading" }
  | { status: "failed"; error: CopyKey }
  | {
      status: "ready";
      base: AdminStoreDesignDetail;
      form: DesignForm;
      saving: boolean;
      saved: boolean;
      alert: CopyKey | null;
      saveError: CopyKey | null;
    };

export const LOADING: DesignState = { status: "loading" };

function ready(base: AdminStoreDesignDetail, alert: CopyKey | null, saved = false): DesignState {
  return { status: "ready", base, form: formOf(base), saving: false, saved, alert, saveError: null };
}

// 按读取结果决定：取到以结果重置表单（alert 为表单上方的提示）；401 回到 A01（login）；网络中断 common.network_check，
// 其他失败 common.error_retry，都不显示表单。
export function readStep(read: StoreDesignRead, alert: CopyKey | null = null): DesignState | "login" {
  switch (read.kind) {
    case "ok":
      return ready(read.design, alert);
    case "none":
      return "login";
    case "network":
      return { status: "failed", error: "common.network_check" };
    case "failed":
      return { status: "failed", error: "common.error_retry" };
  }
}

// 对当前状态的一次改动（保存的结果按保存时的状态更新，不覆盖保存进行中的其他状态）。
export type DesignChange = (current: DesignState) => DesignState;

function failedSave(error: CopyKey): DesignChange {
  return (current) => (current.status === "ready" ? { ...current, saving: false, saveError: error } : current);
}

// 按保存结果决定：200 以返回的设置更新表单并显示 admin.design_saved；401 回到 A01；403 重新读取（reread）；
// 网络中断 common.network_check，其他失败（含 422）common.error_retry，都保留表单。
export function saveStep(save: StoreDesignSave, base: AdminStoreDesignDetail): DesignChange | "login" | "reread" {
  switch (save.kind) {
    case "ok": {
      const saved = ready({ ...base, ...save.design }, null, true);
      return () => saved;
    }
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "network":
      return failedSave("common.network_check");
    case "failed":
      return failedSave("common.error_retry");
  }
}

// 改了任何设置：更新表单并隐藏 admin.design_saved。
export function editForm(state: DesignState, change: (form: DesignForm) => DesignForm): DesignState {
  return state.status === "ready" ? { ...state, form: change(state.form), saved: false } : state;
}

// 点保存：按钮禁用并标 aria-busy，先前的提示都收起。
export function startSaving(state: DesignState): DesignState {
  return state.status === "ready" ? { ...state, saving: true, saved: false, alert: null, saveError: null } : state;
}

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface DesignMoves {
  show: (state: DesignState) => void;
  update: (change: DesignChange) => void;
  replace: (path: RoutePath) => void;
}

// 读取（打开页面、切换语言与 403 之后）；signal 中止（离开页面或已切换语言）之后不再更新。
export async function openDesign(language: Language, signal: AbortSignal, moves: DesignMoves, alert: CopyKey | null = null): Promise<void> {
  const read = await readAdminStoreDesign(language, signal);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read, alert);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 保存当前选择（带读取给的 CSRF 令牌与界面语言）；signal 中止之后其结果不再更新表单。403 时与首次读取一样显示读取中，
// 取得后以结果重置表单并在表单上方显示 common.error_retry。
export async function submitDesign(
  base: AdminStoreDesignDetail,
  form: DesignForm,
  language: Language,
  signal: AbortSignal,
  moves: DesignMoves,
): Promise<void> {
  const save = await saveAdminStoreDesign(saveInput(base, form), base.csrf_token, language, signal);
  if (signal.aborted) {
    return;
  }
  const step = saveStep(save, base);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else if (step === "reread") {
    moves.show(LOADING);
    await openDesign(language, signal, moves, "common.error_retry");
  } else {
    moves.update(step);
  }
}

// 页面状态与它所属的界面语言：语言换了而新结果还没到时按读取中显示。
export interface DesignEntry {
  language: Language;
  state: DesignState;
}

export function shownState(entry: DesignEntry, language: Language): DesignState {
  return entry.language === language ? entry.state : LOADING;
}

function pageMoves(language: Language, setEntry: Dispatch<SetStateAction<DesignEntry>>, replace: (path: RoutePath) => void): DesignMoves {
  return {
    show: (state) => {
      setEntry({ language, state });
    },
    update: (change) => {
      setEntry((current) => ({ language, state: change(current.state) }));
    },
    replace,
  };
}

function ArrowIcon({ up }: { up: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={up ? "M12 19V5M6 11l6-6 6 6" : "M12 5v14M6 13l6 6 6-6"} />
    </svg>
  );
}

interface FormHandlers {
  onTheme: (theme: ShopTheme) => void;
  onAccent: (accent: string) => void;
  onShow: (index: number, visible: boolean) => void;
  onMove: (index: number, offset: -1 | 1) => void;
  onSave: () => void;
}

// 主题：10 个单选，每项为 acs-admin__choice 里的单选、主题色条与主题名；桌面两列、手机单列（site.css），其下 admin.theme_dark_note。
function ThemeGroup({ form, onTheme }: { form: DesignForm } & Pick<FormHandlers, "onTheme">) {
  const t = useCopy();
  return (
    <fieldset className="acs-admin__panel site-admin-store-design__group">
      <legend>{t("admin.theme")}</legend>
      <div className="site-admin-store-design__themes">
        {SHOP_THEMES.map((theme) => (
          <label key={theme} className="acs-admin__choice">
            <input
              type="radio"
              name="theme"
              value={theme}
              checked={form.theme === theme}
              onChange={() => {
                onTheme(theme);
              }}
            />
            <span className="acs acs-swatch" data-shop-theme={theme}>
              <span className="acs-swatch__surface" />
              <span className="acs-swatch__accent" />
              <span className="acs-swatch__demo" />
            </span>
            <span>{t(THEME_NAME[theme])}</span>
          </label>
        ))}
      </div>
      <span className="acs-admin__muted">{t("admin.theme_dark_note")}</span>
    </fieldset>
  );
}

// 主色：所选主题的各主色依次为 acs-admin__ring 里的单选（读屏名称 admin.accent_option），内放该主色的圆点；其下 admin.accent_hint。
function AccentGroup({ form, onAccent }: { form: DesignForm } & Pick<FormHandlers, "onAccent">) {
  const t = useCopy();
  const accents: readonly string[] = THEME_ACCENTS[form.theme];
  return (
    <fieldset className="acs-admin__panel site-admin-store-design__group">
      <legend>{t("admin.accent")}</legend>
      <div className="site-admin-store-design__accents">
        {accents.map((accent, index) => (
          <label key={`${form.theme}-${accent}`} className="acs-admin__ring">
            <input
              type="radio"
              name="accent"
              value={accent}
              aria-label={t("admin.accent_option", { n: index + 1 })}
              checked={form.accent === accent}
              onChange={() => {
                onAccent(accent);
              }}
            />
            <span className="acs acs-swatch--dot" data-shop-theme={form.theme} data-accent={accent} />
          </label>
        ))}
      </div>
      <span className="acs-admin__muted">{t("admin.accent_hint")}</span>
    </fieldset>
  );
}

// 首页区块：每行序号、区块名、显示勾选框与上移、下移（第一行上移与最后一行下移禁用）；隐藏的区块名为次要文字（视觉稿）。
// 其下 admin.home_blocks_hint。
function BlocksGroup({ form, onShow, onMove }: { form: DesignForm } & Pick<FormHandlers, "onShow" | "onMove">) {
  const t = useCopy();
  const last = form.homeBlocks.length - 1;
  return (
    <fieldset className="acs-admin__panel site-admin-store-design__group site-admin-store-design__blocks">
      <legend>{t("admin.home_blocks")}</legend>
      {form.homeBlocks.map(({ block, visible }, index) => (
        <div key={block} className="site-admin-store-design__block">
          <span className="site-admin-store-design__block-name">
            <span className="acs-admin__muted">{index + 1}</span>
            <span className={visible ? undefined : "acs-admin__muted"}>{t(BLOCK_NAME[block])}</span>
          </span>
          <label className="site-admin-store-design__show">
            <input
              type="checkbox"
              checked={visible}
              onChange={(event: ChangeEvent<HTMLInputElement>) => {
                onShow(index, event.target.checked);
              }}
            />
            <span>{t("admin.block_show")}</span>
          </label>
          <span className="site-admin-store-design__moves">
            <button
              className="acs-admin__btn acs-admin__btn--secondary"
              type="button"
              aria-label={t("admin.block_move_up")}
              disabled={index === 0}
              onClick={() => {
                onMove(index, -1);
              }}
            >
              <ArrowIcon up />
            </button>
            <button
              className="acs-admin__btn acs-admin__btn--secondary"
              type="button"
              aria-label={t("admin.block_move_down")}
              disabled={index === last}
              onClick={() => {
                onMove(index, 1);
              }}
            >
              <ArrowIcon up={false} />
            </button>
          </span>
        </div>
      ))}
      <span className="acs-admin__muted site-admin-store-design__hint">{t("admin.home_blocks_hint")}</span>
    </fieldset>
  );
}

type ReadyState = Extract<DesignState, { status: "ready" }>;

function DesignFormView({ state, onTheme, onAccent, onShow, onMove, onSave }: { state: ReadyState } & FormHandlers) {
  const t = useCopy();
  const { form } = state;
  return (
    <form
      className="site-admin-store-design__form"
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onSave();
      }}
    >
      {state.alert !== null && <AdminAlert error={state.alert} />}
      <ThemeGroup form={form} onTheme={onTheme} />
      <AccentGroup form={form} onAccent={onAccent} />
      <BlocksGroup form={form} onShow={onShow} onMove={onMove} />
      <div className="site-admin-store-design__save">
        <button className="acs-admin__btn" type="submit" disabled={state.saving} aria-busy={state.saving}>
          {t("common.save")}
        </button>
        {state.saved && <span role="status">{t("admin.design_saved")}</span>}
        {state.saveError !== null && <AdminAlert error={state.saveError} />}
      </div>
    </form>
  );
}

export interface AdminStoreDesignViewProps extends FormHandlers {
  state: DesignState;
}

// 页头之下为表单区：读取中标 aria-busy、不显示表单；读取失败时只有提示。
export function AdminStoreDesignView({ state, ...handlers }: AdminStoreDesignViewProps) {
  const t = useCopy();
  return (
    <div className="site-admin-store-design">
      <h1 className="acs-admin__h site-admin-store-design__title">{t("admin.nav_store_design")}</h1>
      <p className="site-admin-store-design__note">{t("admin.design_demo_note")}</p>
      <div className="site-admin-store-design__body" aria-busy={state.status === "loading"}>
        {state.status === "failed" && <AdminAlert error={state.error} />}
        {state.status === "ready" && <DesignFormView state={state} {...handlers} />}
      </div>
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载与切换界面语言时读取。
export function AdminStoreDesignContent() {
  const { replace } = useRouter();
  const { language } = useLanguage();
  const [entry, setEntry] = useState<DesignEntry>({ language, state: LOADING });
  // 当前语言下的请求（读取、保存与 403 后的重新读取）共用的中止；离开页面或切换语言时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openDesign(language, controller.signal, pageMoves(language, setEntry, replace));
    });
  }, [language, replace]);

  const state = shownState(entry, language);
  const edit = (change: (form: DesignForm) => DesignForm) => {
    setEntry((current) => (current.language === language ? { language, state: editForm(current.state, change) } : current));
  };

  return (
    <AdminStoreDesignView
      state={state}
      onTheme={(theme) => {
        edit((form) => chooseTheme(form, theme));
      }}
      onAccent={(accent) => {
        edit((form) => chooseAccent(form, accent));
      }}
      onShow={(index, visible) => {
        edit((form) => showBlock(form, index, visible));
      }}
      onMove={(index, offset) => {
        edit((form) => moveBlock(form, index, offset));
      }}
      onSave={() => {
        const controller = lifetime.current;
        if (state.status !== "ready" || state.saving || controller === null) {
          return;
        }
        setEntry({ language, state: startSaving(state) });
        void submitDesign(state.base, state.form, language, controller.signal, pageMoves(language, setEntry, replace));
      }}
    />
  );
}

export default function AdminStoreDesignPage() {
  return (
    <AdminFrame current="storeDesign">
      <AdminStoreDesignContent />
    </AdminFrame>
  );
}
