import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, Dispatch, FormEvent, SetStateAction } from "react";

import { readAdminStoreDesign, saveAdminStoreDesign } from "../api/adminStoreDesign";
import type {
  AdminStoreDesign,
  AdminStoreDesignDetail,
  FeaturedProduct,
  ProductChoice,
  StoreDesignInput,
  StoreDesignRead,
  StoreDesignSave,
} from "../api/adminStoreDesign";
import { FEATURED_COUNT } from "../api/catalog";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import { PlaceholderShape } from "../components/ProductCard";
import { DemoHint } from "../components/SiteFrame";
import { BRAND } from "../i18n/copy";
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
// 其下为表单（SHOP-TASK-064）：主题（10 款，按 THEME_ACCENTS 的顺序）、所选主题的主色、首页四个区块的顺序与显隐、
// 精选商品的挑选（SHOP-TASK-065：最多 FEATURED_COUNT 件，可排序、移出，原已在精选里、之后下架的照列）与 common.save。
// 不含标志图（Kelvin 2026-10-06 决定）。预览（SHOP-TASK-066）：桌面为表单右侧的预览栏，手机为精选之后、保存之前收起的 details，
// 按表单当前（未保存）的主题与主色显示首页缩样，可切换浅色与深色。
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

// 表单里的选择：主题、所选主色（总是该主题的一个选项）、按位置排序的四个区块与按位置排序的精选商品。
export interface DesignForm {
  theme: ShopTheme;
  accent: string;
  homeBlocks: readonly HomeBlockSetting[];
  featured: readonly FeaturedProduct[];
}

// 主题的第一项主色（null 即它，storeDesign.ts）。
export function defaultAccent(theme: ShopTheme): string {
  return THEME_ACCENTS[theme][0];
}

// 以读取或保存返回的设置重置表单：主色为 null 时选第一项。
export function formOf(design: AdminStoreDesign): DesignForm {
  return { theme: design.theme, accent: design.accent ?? defaultAccent(design.theme), homeBlocks: design.home_blocks, featured: design.featured };
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

// 与上一行（-1）或下一行（1）互换；第一行上移与最后一行下移时为 null。
function swapped<T>(items: readonly T[], index: number, offset: -1 | 1): T[] | null {
  const next = [...items];
  const moved = next[index];
  const other = next[index + offset];
  if (moved === undefined || other === undefined) {
    return null;
  }
  next[index] = other;
  next[index + offset] = moved;
  return next;
}

// 与上一行（-1）或下一行（1）互换；第一行上移与最后一行下移不变。
export function moveBlock(form: DesignForm, index: number, offset: -1 | 1): DesignForm {
  const blocks = swapped(form.homeBlocks, index, offset);
  return blocks === null ? form : { ...form, homeBlocks: blocks };
}

// 精选的排序规则同首页区块（UX A08「(↑)(↓) 调整顺序，规则同首页区块」）。
export function moveFeatured(form: DesignForm, index: number, offset: -1 | 1): DesignForm {
  const featured = swapped(form.featured, index, offset);
  return featured === null ? form : { ...form, featured };
}

export function removeFeatured(form: DesignForm, index: number): DesignForm {
  return { ...form, featured: form.featured.filter((_product, at) => at !== index) };
}

// 下拉里可添加的商品：按 choices 的顺序、尚未在精选里的（同一商品不重复）。
export function featuredOptions(choices: readonly ProductChoice[], form: DesignForm): ProductChoice[] {
  return choices.filter((choice) => !form.featured.some((product) => product.product_id === choice.product_id));
}

// 加到末尾；已有 FEATURED_COUNT 件或已在精选里时不变。可挑选的商品都满足 published()，所以加入的一件 published 为 true。
export function addFeatured(form: DesignForm, choice: ProductChoice): DesignForm {
  if (form.featured.length >= FEATURED_COUNT || form.featured.some((product) => product.product_id === choice.product_id)) {
    return form;
  }
  return { ...form, featured: [...form.featured, { product_id: choice.product_id, slug: choice.slug, name: choice.name, published: true }] };
}

// 保存的请求：主题与读取到的相同、且所选主色就是读取到的主色（读取到 null 时即该主题的第一项）时原样提交读取到的 accent（含 null），
// 否则提交所选主色 id，所以未改动的表单再次保存不改变存储；精选按列表当前的顺序提交商品 ID。
export function saveInput(base: AdminStoreDesign, form: DesignForm): StoreDesignInput {
  const unchanged = form.theme === base.theme && form.accent === (base.accent ?? defaultAccent(base.theme));
  return {
    theme: form.theme,
    accent: unchanged ? base.accent : form.accent,
    home_blocks: form.homeBlocks,
    featured_product_ids: form.featured.map((product) => product.product_id),
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

function RemoveIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

interface FormHandlers {
  onTheme: (theme: ShopTheme) => void;
  onAccent: (accent: string) => void;
  onShow: (index: number, visible: boolean) => void;
  onMove: (index: number, offset: -1 | 1) => void;
  onFeaturedMove: (index: number, offset: -1 | 1) => void;
  onFeaturedRemove: (index: number) => void;
  onFeaturedAdd: (choice: ProductChoice) => void;
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

// 精选商品（UX A08「精选商品（0.6）」，视觉稿未画，写法沿用首页区块一组）：按列表顺序每行序号、名称（name 为 null 时显示 slug；
// 之后下架的照列、不加标记）、上移、下移与移出；其下为添加的下拉（按 choices 的顺序列出尚未在精选里的商品，默认选第一项）与按钮，
// 已有 FEATURED_COUNT 件或没有可添加的商品时两者都禁用；最后为 admin.featured_hint。
function FeaturedGroup({
  form,
  choices,
  onFeaturedMove,
  onFeaturedRemove,
  onFeaturedAdd,
}: { form: DesignForm; choices: readonly ProductChoice[] } & Pick<FormHandlers, "onFeaturedMove" | "onFeaturedRemove" | "onFeaturedAdd">) {
  const t = useCopy();
  const [picked, setPicked] = useState<number | null>(null);
  const options = featuredOptions(choices, form);
  // 所选的一件已加入（不再是选项）或还没选过时，选第一项。
  const selected = options.find((choice) => choice.product_id === picked) ?? options[0];
  const blocked = form.featured.length >= FEATURED_COUNT || selected === undefined;
  const last = form.featured.length - 1;
  return (
    <fieldset className="acs-admin__panel site-admin-store-design__group site-admin-store-design__featured">
      <legend>{t("admin.featured_pick")}</legend>
      {form.featured.map(({ product_id, slug, name }, index) => (
        <div key={product_id} className="site-admin-store-design__pick">
          <span className="site-admin-store-design__pick-name">
            <span className="acs-admin__muted">{index + 1}</span>
            <span>{name ?? slug}</span>
          </span>
          <span className="site-admin-store-design__moves">
            <button
              className="acs-admin__btn acs-admin__btn--secondary"
              type="button"
              aria-label={t("admin.block_move_up")}
              disabled={index === 0}
              onClick={() => {
                onFeaturedMove(index, -1);
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
                onFeaturedMove(index, 1);
              }}
            >
              <ArrowIcon up={false} />
            </button>
            <button
              className="acs-admin__btn acs-admin__btn--secondary"
              type="button"
              aria-label={t("admin.featured_remove")}
              onClick={() => {
                onFeaturedRemove(index);
              }}
            >
              <RemoveIcon />
            </button>
          </span>
        </div>
      ))}
      <div className="site-admin-store-design__add">
        <select
          className="acs-admin__select"
          aria-label={t("admin.featured_add")}
          value={selected?.product_id ?? ""}
          disabled={blocked}
          onChange={(event: ChangeEvent<HTMLSelectElement>) => {
            setPicked(Number(event.target.value));
          }}
        >
          {options.map((choice) => (
            <option key={choice.product_id} value={choice.product_id}>
              {choice.name}
            </option>
          ))}
        </select>
        <button
          className="acs-admin__btn acs-admin__btn--secondary"
          type="button"
          disabled={blocked}
          onClick={() => {
            if (selected !== undefined) {
              onFeaturedAdd(selected);
            }
          }}
        >
          {t("admin.featured_add")}
        </button>
      </div>
      <span className="acs-admin__muted site-admin-store-design__hint">{t("admin.featured_hint")}</span>
    </fieldset>
  );
}

// 预览的深浅色（默认浅色）与切换按钮的文字。
export type PreviewMode = "light" | "dark";

export const PREVIEW_MODES: readonly PreviewMode[] = ["light", "dark"];

const MODE_NAME: Readonly<Record<PreviewMode, CopyKey>> = {
  light: "admin.preview_light",
  dark: "admin.preview_dark",
};

// 缩样里的一张商品卡：只有名称（name 为 null 时用 slug，同精选列表），没有价格（Kelvin 2026-10-08，HANDOFF 0.40）。
export interface PreviewProduct {
  product_id: number;
  name: string;
}

export const PREVIEW_CARD_COUNT = 2;

// 两张商品卡（Kelvin 2026-10-08「取精选里前两件仍上架的商品，不足时按可挑选商品的顺序补足」）：按精选列表当前的顺序取 published 为 true 的，
// 不足两件时按 choices 的顺序补上尚未取到的商品，仍不足时只有现有的（可以为零张）。
export function previewProducts(featured: readonly FeaturedProduct[], choices: readonly ProductChoice[]): PreviewProduct[] {
  const picked: PreviewProduct[] = featured
    .filter((product) => product.published)
    .slice(0, PREVIEW_CARD_COUNT)
    .map((product) => ({ product_id: product.product_id, name: product.name ?? product.slug }));
  for (const choice of choices) {
    if (picked.length >= PREVIEW_CARD_COUNT) {
      break;
    }
    if (!picked.some((product) => product.product_id === choice.product_id)) {
      picked.push({ product_id: choice.product_id, name: choice.name });
    }
  }
  return picked;
}

// 浅色与深色两个切换按钮：aria-pressed 标当前，当前的为 acs-admin__btn，另一个加 acs-admin__btn--secondary。
function PreviewModes({ mode, onMode }: { mode: PreviewMode; onMode: (mode: PreviewMode) => void }) {
  const t = useCopy();
  return (
    <span className="site-admin-store-design__modes">
      {PREVIEW_MODES.map((option) => (
        <button
          key={option}
          className={option === mode ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary"}
          type="button"
          aria-pressed={option === mode}
          onClick={() => {
            onMode(option);
          }}
        >
          {t(MODE_NAME[option])}
        </button>
      ))}
    </span>
  );
}

// 首页缩样（UX A08「所选主题与主色的首页缩样」，视觉稿的预览框）：acs-admin__preview 内为设了表单当前主题、主色（第一项不设，同前台）
// 与所选深浅色的 .acs 元素；内容固定、不随首页区块的顺序与显隐变化，依次为演示横幅、页头一行（品牌文字与 common.nav_cart，{count} 为 0）、
// ★ home.demo_hint、主视觉（home.hero_title 与按钮样式的 home.hero_cta）、home.how_title 与商品卡。只用 span、div 与 p，
// 没有链接、按钮或其他可交互元素。
function ShopSample({ form, choices, mode }: { form: DesignForm; choices: readonly ProductChoice[]; mode: PreviewMode }) {
  const t = useCopy();
  const products = previewProducts(form.featured, choices);
  return (
    <div className="acs-admin__preview">
      <div
        className="acs site-admin-store-design__shop"
        data-shop-theme={form.theme}
        data-accent={form.accent === defaultAccent(form.theme) ? undefined : form.accent}
        data-mode={mode}
      >
        <div className="acs-banner">
          <span className="acs-tag acs-tag--demo">{t("common.demo_badge")}</span>
          <span>{t("common.demo_banner_short")}</span>
        </div>
        <div className="site-admin-store-design__shop-head">
          <span className="acs-brand">{BRAND}</span>
          <span>{t("common.nav_cart", { count: 0 })}</span>
        </div>
        <div className="site-admin-store-design__shop-body">
          <DemoHint>{t("home.demo_hint")}</DemoHint>
          <div className="acs-panel site-admin-store-design__hero">
            <span className="acs-display-s">{t("home.hero_title")}</span>
            <span className="acs-btn acs-btn--primary acs-btn--sm">{t("home.hero_cta")}</span>
          </div>
          <span>{t("home.how_title")}</span>
          {products.length > 0 && (
            <div className="site-admin-store-design__cards">
              {products.map((product) => (
                <span key={product.product_id} className="acs-pcard">
                  <span className="acs-pcard__img">
                    <PlaceholderShape />
                  </span>
                  <span className="acs-pcard__name">{product.name}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export interface PreviewProps {
  form: DesignForm;
  choices: readonly ProductChoice[];
  mode: PreviewMode;
  onMode: (mode: PreviewMode) => void;
}

// 桌面的预览栏（视觉稿右侧 460px 的 aside）：顶部为 admin.preview 与切换按钮，其下为缩样；767px 及以下由 site.css 隐藏。
export function PreviewAside({ form, choices, mode, onMode }: PreviewProps) {
  const t = useCopy();
  return (
    <aside className="acs-admin__panel site-admin-store-design__aside">
      <div className="site-admin-store-design__preview-head">
        <span>{t("admin.preview")}</span>
        <PreviewModes mode={mode} onMode={onMode} />
      </div>
      <ShopSample form={form} choices={choices} mode={mode} />
    </aside>
  );
}

// 手机的预览（UX A08 手机线框「▸ [admin.preview]」，A08-phone）：精选之后、保存之前，默认收起的 details，展开后为与桌面相同的
// 切换按钮与缩样；768px 及以上由 site.css 隐藏。
export function PreviewDetails({ form, choices, mode, onMode }: PreviewProps) {
  const t = useCopy();
  return (
    <details className="acs-admin__panel site-admin-store-design__details">
      <summary>{t("admin.preview")}</summary>
      <div className="site-admin-store-design__preview-body">
        <PreviewModes mode={mode} onMode={onMode} />
        <ShopSample form={form} choices={choices} mode={mode} />
      </div>
    </details>
  );
}

type ReadyState = Extract<DesignState, { status: "ready" }>;

function DesignFormView({
  state,
  mode,
  onMode,
  onTheme,
  onAccent,
  onShow,
  onMove,
  onFeaturedMove,
  onFeaturedRemove,
  onFeaturedAdd,
  onSave,
}: { state: ReadyState; mode: PreviewMode; onMode: (mode: PreviewMode) => void } & FormHandlers) {
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
      <FeaturedGroup
        form={form}
        choices={state.base.choices}
        onFeaturedMove={onFeaturedMove}
        onFeaturedRemove={onFeaturedRemove}
        onFeaturedAdd={onFeaturedAdd}
      />
      <PreviewDetails form={form} choices={state.base.choices} mode={mode} onMode={onMode} />
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

// 页头之下为表单区：读取中标 aria-busy、不显示表单；读取失败时只有提示。取得设置后另有预览：桌面为表单区右侧的预览栏，
// 手机为表单里的 details（两份按 site.css 的媒体查询显隐，共用同一个深浅色选择，默认浅色）。
export function AdminStoreDesignView({ state, ...handlers }: AdminStoreDesignViewProps) {
  const t = useCopy();
  const [mode, setMode] = useState<PreviewMode>("light");
  return (
    <div className="site-admin-store-design">
      <h1 className="acs-admin__h site-admin-store-design__title">{t("admin.nav_store_design")}</h1>
      <p className="site-admin-store-design__note">{t("admin.design_demo_note")}</p>
      <div className="site-admin-store-design__body" aria-busy={state.status === "loading"}>
        {state.status === "failed" && <AdminAlert error={state.error} />}
        {state.status === "ready" && <DesignFormView state={state} mode={mode} onMode={setMode} {...handlers} />}
      </div>
      {state.status === "ready" && <PreviewAside form={state.form} choices={state.base.choices} mode={mode} onMode={setMode} />}
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载与切换界面语言时读取。
export function AdminStoreDesignContent() {
  const { replace } = useRouter();
  const { language } = useLanguage();
  const [entry, setEntry] = useState<DesignEntry>({ language, state: LOADING });
  // 每次切换语言都先回到读取中（含读取未到时又换回先前语言的情形，不再显示那个语言旧的表单），与首次读取相同。
  if (entry.language !== language) {
    setEntry({ language, state: LOADING });
  }
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
      onFeaturedMove={(index, offset) => {
        edit((form) => moveFeatured(form, index, offset));
      }}
      onFeaturedRemove={(index) => {
        edit((form) => removeFeatured(form, index));
      }}
      onFeaturedAdd={(choice) => {
        edit((form) => addFeatured(form, choice));
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
