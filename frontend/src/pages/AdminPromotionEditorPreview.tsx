import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

import type { PromotionDecoration } from "../api/adminPromotions";
import { PlaceholderShape } from "../components/ProductCard";
import { PromoBadges, PromoPrices } from "../components/PromoParts";
import { formatSen } from "../format";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { PROMO_KEEP_THEME } from "../promoManifest";
import { badgeText, KIND_BUY_X_GET_Y, KIND_ITEM_DISCOUNT } from "../promoText";
import type { PromoRuleParams } from "../promoText";
import { DEFAULT_STORE_DESIGN, fetchStoreDesign } from "../storeDesign";
import type { StoreDesign, StoreDesignResponse } from "../storeDesign";
import { PREVIEW_MODES } from "./AdminStoreDesignPage";
import type { PreviewMode } from "./AdminStoreDesignPage";
import { integerOf, pyStrip, rmSen } from "./promotionForm";
import type { CopyField, PromotionForm, PromotionFormCopy, PromotionFormRule } from "./promotionForm";

// 后台促销活动 A12 的编辑区预览（docs/UX.md 0.20 A12 线框「[admin.editor_preview]（编辑区预览，随本页未保存的输入即时更新）」一段与说明的「编辑区预览」一条，
// 视觉稿 A12-editor-desktop 的预览框；HANDOFF 0.98 落实写法（0）至（4）；DESIGN 1.23「节日促销活动」第 12 条，SHOP-TASK-274）：
// 按编辑页传入的未保存表单（文案、规则、装饰与海报模式）与已保存的桌面海报图，在浏览器里画出模板海报、活动横条、两张示例商品卡、价格飘带与购物车进度条的缩样，
// 可切换浅色与深色。只画外观：金额是固定的示意值，不调用计价，也不代表前台的实际价格；不碰个人资料。
// - 结构照 SiteFrame：acs-admin__preview 内为 .acs（店铺装修的主题与主色，经 fetchStoreDesign 读一次，读取中或失败时为 DEFAULT_STORE_DESIGN；data-mode 为所选深浅色），
//   其内为 .acs-promo（素材包；配色不为 theme 时带配色；页面装饰为 corners 或 fall 时带 data-promo-ambient，为 garland 时其内放读屏忽略的 .acs-promo-garland）。
// - 文案按当前语言，缺译用英文，为空时该行不显示，都按纯文本显示。任何状态都显示（已结束的同样显示），切换按钮不受只读影响。

// 示意金额（整仙）：商品卡与价格飘带的原价与活动价，购物车进度条的已达金额与门槛（进度固定为 60%）。
export const SAMPLE_ORIGINAL_SEN = 12_000;
export const SAMPLE_SALE_SEN = 9_600;
export const SAMPLE_REACHED_SEN = 6_000;
export const SAMPLE_THRESHOLD_SEN = 10_000;
export const SAMPLE_PROGRESS_PERCENT = 60;
// 示例商品卡的张数。
export const SAMPLE_CARD_COUNT = 2;

// 各装饰位的取值，照前台页面：home_poster 为 centered 时居中、其余左文右图；list_strip 为 ticket 时票券、其余色带；detail_ribbon 为 panel 时面板、
// 其余飘带；cart_progress 为 steps 时 10 格、其余进度条（card_badge 由 PromoBadges 按 seal 与 tag 处理）。
const POSTER_CENTERED = "centered";
const POSTER_IMAGE = "image";
const STRIP_TICKET = "ticket";
const RIBBON_PANEL = "panel";
const PROGRESS_STEPS = "steps";
const STEP_COUNT = 10;

// 照 SiteFrame：corners 与 fall 由 .acs-promo 的 data-promo-ambient 画出，garland 为其内的 .acs-promo-garland，none 什么都不画。
const WRAPPER_AMBIENTS: readonly string[] = ["corners", "fall"];
const AMBIENT_GARLAND = "garland";

const MODE_NAME: Readonly<Record<PreviewMode, CopyKey>> = {
  light: "admin.preview_light",
  dark: "admin.preview_dark",
};

// 缩样用到的文案项。
export type PreviewCopyGroup = "title" | "subtitle" | "badge" | "strip";

// 一项文案：当前语言去掉首尾空白后不为空时用它，否则用英文；都为空时为 null（该行不显示）。
export function previewCopy(copy: PromotionFormCopy, group: PreviewCopyGroup, language: Language): string | null {
  for (const field of [`${group}_${language}`, `${group}_en`] as CopyField[]) {
    const text = pyStrip(copy[field]);
    if (text !== "") {
      return text;
    }
  }
  return null;
}

// 横条文字：横条文字，缺时用标题（照 P02）。
export function previewStripText(copy: PromotionFormCopy, language: Language): string | null {
  return previewCopy(copy, "strip", language) ?? previewCopy(copy, "title", language);
}

const NO_PARAMS: Omit<PromoRuleParams, "kind"> = {
  discount_mode: null,
  percent: null,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
};

// 第一条商品级规则（每件直降或买 X 享 Y）换成 badgeText 的参数；没有商品级规则，或该规则的参数为空、换不成整数或整仙时为 null（不往后找）。
export function previewRuleParams(rules: readonly PromotionFormRule[]): PromoRuleParams | null {
  const rule = rules.find((item) => item.kind === KIND_ITEM_DISCOUNT || item.kind === KIND_BUY_X_GET_Y);
  if (rule === undefined) {
    return null;
  }
  if (rule.kind === "item_discount") {
    if (rule.discount_mode === "percent") {
      const percent = integerOf(rule.discount);
      return percent === null ? null : { ...NO_PARAMS, kind: rule.kind, discount_mode: "percent", percent };
    }
    const amount = rmSen(rule.discount);
    return amount === null ? null : { ...NO_PARAMS, kind: rule.kind, discount_mode: "amount", amount_sen: amount };
  }
  if (rule.kind === "buy_x_get_y") {
    const buy = integerOf(rule.buy_quantity);
    const get = integerOf(rule.get_quantity);
    const percent = integerOf(rule.get_percent);
    if (buy === null || get === null || percent === null) {
      return null;
    }
    return { ...NO_PARAMS, kind: rule.kind, buy_quantity: buy, get_quantity: get, get_percent: percent };
  }
  return null;
}

// 规则角标：第一条商品级规则的 badgeText；没有时为 null。
export function previewRuleBadge(rules: readonly PromotionFormRule[], language: Language): string | null {
  const params = previewRuleParams(rules);
  return params === null ? null : badgeText(language, params);
}

// 店铺装修读一次：取到时为其主题与主色，失败时为 DEFAULT_STORE_DESIGN。测试以替身代替 fetchStoreDesign。
export async function readPreviewDesign(
  signal: AbortSignal,
  fetchDesign: (signal?: AbortSignal) => Promise<StoreDesignResponse> = fetchStoreDesign,
): Promise<StoreDesign> {
  try {
    return (await fetchDesign(signal)).design;
  } catch {
    return DEFAULT_STORE_DESIGN;
  }
}

// 浅色与深色两个切换按钮（照 A08 的 PreviewModes）：aria-pressed 标当前，当前的为 acs-admin__btn，另一个加 acs-admin__btn--secondary。
function PreviewModes({ mode, onMode }: { mode: PreviewMode; onMode: (mode: PreviewMode) => void }) {
  const t = useCopy();
  return (
    <span className="site-admin-promo-preview__modes">
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

// 海报缩样：模板模式按 home_poster 画 acs-promo-poster，只有标题与副标题（编辑区没有生效中的结束时间，不显示倒计时与按钮）；上传图模式为已保存的桌面海报图，
// 没有图时为同样大小的占位块。
function PosterSample({ decoration, title, subtitle, poster }: { decoration: PromotionDecoration; title: string | null; subtitle: string | null; poster: string | null }) {
  if (decoration.poster_mode === POSTER_IMAGE) {
    return poster === null ? (
      <div className="acs-promo-poster-img site-admin-promo-preview__poster" aria-hidden="true" />
    ) : (
      <div className="acs-promo-poster-img site-admin-promo-preview__poster">
        <img src={poster} alt="" />
      </div>
    );
  }
  const variant = decoration.home_poster === POSTER_CENTERED ? "centered" : "split";
  return (
    <div className={`acs-promo-poster acs-promo-poster--${variant} site-admin-promo-preview__poster`}>
      <div className="acs-promo-poster__text">
        {title !== null && <span className="acs-display-s acs-promo-poster__title">{title}</span>}
        {subtitle !== null && <span className="acs-promo-poster__sub">{subtitle}</span>}
      </div>
      <div className="acs-promo-poster__art" aria-hidden="true" />
    </div>
  );
}

// 活动横条缩样（照 PromoStrip）：活动图案与文字，不带倒计时与 promo.view_all。
function StripSample({ variant, text }: { variant: string; text: string | null }) {
  return (
    <div className={`acs-promo-strip acs-promo-strip--${variant === STRIP_TICKET ? "ticket" : "band"} site-admin-promo-preview__strip`}>
      <span className="acs-promo-motif" aria-hidden="true" />
      {text !== null && <span className="acs-promo-strip__title">{text}</span>}
    </div>
  );
}

// 价格飘带缩样（照 P03 的 PriceRibbon）：示意的划线原价与活动价，角标同商品卡，不带倒计时。
function RibbonSample({ variant, original, sale, badges }: { variant: string; original: string; sale: string; badges: { phrase: string | null; rule: string | null; style: string } }) {
  const t = useCopy();
  const hasBadge = [badges.phrase, badges.rule].some((text) => text !== null && text !== "");
  return (
    <div className={`acs-promo-ribbon acs-promo-ribbon--${variant === RIBBON_PANEL ? "panel" : "ribbon"} site-admin-promo-preview__ribbon`}>
      <div className="acs-promo-ribbon__prices">
        <span className="acs-price-was acs-price--l">
          <span className="acs-vh">{t("promo.a11y_original")}</span>
          {original}
        </span>
        <span className="acs-price-sale acs-promo-ribbon__sale">
          <span className="acs-vh">{t("promo.a11y_sale")}</span>
          {sale}
        </span>
      </div>
      {hasBadge && (
        <div className="acs-promo-ribbon__meta">
          <PromoBadges {...badges} />
        </div>
      )}
    </div>
  );
}

// 购物车进度条缩样（照 P04 的 PromoProgress）：固定为 60%（steps 点亮 6 格），其下为示意的已达金额与门槛。
function ProgressSample({ variant }: { variant: string }) {
  const t = useCopy();
  const lit = Math.floor(SAMPLE_PROGRESS_PERCENT / STEP_COUNT);
  return (
    <div className="acs-promo-progress site-admin-promo-preview__progress">
      {variant === PROGRESS_STEPS ? (
        <div className="acs-promo-progress__steps" aria-hidden="true">
          {Array.from({ length: STEP_COUNT }, (_, index) => (
            <span key={index} data-on={index < lit ? "" : undefined} />
          ))}
        </div>
      ) : (
        <div className="acs-promo-progress__track" aria-hidden="true">
          <span className="acs-promo-progress__fill" style={{ "--progress": `${String(SAMPLE_PROGRESS_PERCENT)}%` } as CSSProperties} />
        </div>
      )}
      <div className="acs-promo-progress__labels">
        <span>{t("common.price_myr", { amount: formatSen(SAMPLE_REACHED_SEN) })}</span>
        <span>{t("common.price_myr", { amount: formatSen(SAMPLE_THRESHOLD_SEN) })}</span>
      </div>
    </div>
  );
}

export interface EditorPreviewViewProps {
  // 本页当前（含未保存）的表单。
  form: PromotionForm;
  // 已保存的桌面海报图；没有时为 null。
  poster: string | null;
  // 店铺装修的主题与主色。
  design: StoreDesign;
  mode: PreviewMode;
  onMode: (mode: PreviewMode) => void;
}

// 预览本身（不读取）：切换按钮、预览框与 admin.editor_preview_note。小标题 admin.editor_preview 在装饰区（AdminPromotionDecoration）。
export function EditorPreviewView({ form, poster, design, mode, onMode }: EditorPreviewViewProps) {
  const t = useCopy();
  const { language } = useLanguage();
  const { decoration, copy } = form;
  const price = (sen: number) => t("common.price_myr", { amount: formatSen(sen) });
  const original = price(SAMPLE_ORIGINAL_SEN);
  const sale = price(SAMPLE_SALE_SEN);
  const badges = { phrase: previewCopy(copy, "badge", language), rule: previewRuleBadge(form.rules, language), style: decoration.card_badge };
  return (
    <>
      <PreviewModes mode={mode} onMode={onMode} />
      <div className="acs-admin__preview site-admin-promo-preview__box">
        <div className="acs site-admin-promo-preview__shop" data-shop-theme={design.theme} data-accent={design.accent ?? undefined} data-mode={mode}>
          <div
            className="acs-promo"
            data-promo-pack={decoration.pack}
            data-promo-palette={decoration.palette === PROMO_KEEP_THEME ? undefined : decoration.palette}
            data-promo-ambient={WRAPPER_AMBIENTS.includes(decoration.ambient) ? decoration.ambient : undefined}
          >
            {decoration.ambient === AMBIENT_GARLAND && <div className="acs-promo-garland" aria-hidden="true" />}
            <div className="site-admin-promo-preview__body">
              <PosterSample
                decoration={decoration}
                title={previewCopy(copy, "title", language)}
                subtitle={previewCopy(copy, "subtitle", language)}
                poster={poster}
              />
              <StripSample variant={decoration.list_strip} text={previewStripText(copy, language)} />
              <div className="site-admin-promo-preview__cards">
                {Array.from({ length: SAMPLE_CARD_COUNT }, (_, index) => (
                  <span key={index} className="acs-pcard">
                    <span className="acs-pcard__img">
                      <PromoBadges {...badges} />
                      <PlaceholderShape />
                    </span>
                    <PromoPrices original={original} sale={sale} />
                  </span>
                ))}
              </div>
              <RibbonSample variant={decoration.detail_ribbon} original={original} sale={sale} badges={badges} />
              <ProgressSample variant={decoration.cart_progress} />
            </div>
          </div>
        </div>
      </div>
      <p className="acs-admin__muted site-admin-promo-preview__note">{t("admin.editor_preview_note")}</p>
    </>
  );
}

export interface AdminPromotionEditorPreviewProps {
  form: PromotionForm;
  poster: string | null;
}

// 编辑区预览：挂载后读一次店铺装修（读取中与失败时用默认主题），深浅色初值为浅色；离开时中止读取。
export default function AdminPromotionEditorPreview({ form, poster }: AdminPromotionEditorPreviewProps) {
  const [design, setDesign] = useState<StoreDesign>(DEFAULT_STORE_DESIGN);
  const [mode, setMode] = useState<PreviewMode>("light");

  useEffect(() => {
    const controller = new AbortController();
    void readPreviewDesign(controller.signal).then((read) => {
      if (!controller.signal.aborted) {
        setDesign(read);
      }
    });
    return () => {
      controller.abort();
    };
  }, []);

  return <EditorPreviewView form={form} poster={poster} design={design} mode={mode} onMode={setMode} />;
}
