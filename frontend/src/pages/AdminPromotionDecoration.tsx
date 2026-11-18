import { useId } from "react";
import type { ChangeEvent, ReactNode } from "react";

import type { PromotionDecoration } from "../api/adminPromotions";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { PROMO_AMBIENT_NONE, PROMO_KEEP_THEME, PROMO_PACKS, PROMO_VARIANTS } from "../promoManifest";
import type { PromoPack, PromoSlot } from "../promoManifest";

// 后台促销活动 A12 编辑区的装饰区（docs/UX.md 0.20 A12 线框「[admin.promo_decoration]」一段与说明的「装饰」一条，视觉稿 A12-editor-desktop、
// A12-phone 与 A12-states 的 Page ornaments；HANDOFF 0.98 落实写法（2）至（4）；DESIGN 1.23「节日促销活动」第 12 条，SHOP-TASK-273）：
// 素材包（原生 select）、配色（admin.palette_keep 与 admin.palette_of 两个单选，后者以列出 7 套配色的 select 为可见标签，旁边为所选配色的色块）、
// 页面装饰（带缩略图的单选，含 admin.variant_none）与其余五个装饰位（原生 select）。选项都来自 promoManifest.ts 的清单常量；读到的 ID 已经服务端
// 按同一清单校验，不另处理不在常量里的值。装饰只影响外观、不影响金额，保存时由服务端再校验；不碰个人资料。
// - 换素材包时各装饰位改为该素材包的第一个变体（页面装饰为 garland）；配色为节日配色时改为新素材包的配色，沿用主题配色时不变。
// - 只读（已结束）时单选与选择都禁用。poster_mode 由 SHOP-TASK-275 编辑，本区不改。
// - 编辑区预览（SHOP-TASK-274，UX 0.20 A12「[admin.editor_preview]（编辑区预览，随本页未保存的输入即时更新）」）：编辑页以 preview 传入
//   AdminPromotionEditorPreview，放在本区末尾，前面为小标题 admin.editor_preview（以它为组标签）；没传时不显示。

// 素材包的名称，顺序同 PROMO_PACKS。
export const PACK_LABEL: Readonly<Record<PromoPack, CopyKey>> = {
  cny: "admin.pack_cny",
  raya: "admin.pack_raya",
  deepavali: "admin.pack_deepavali",
  christmas: "admin.pack_christmas",
  merdeka: "admin.pack_merdeka",
  mega_sale: "admin.pack_mega_sale",
  generic: "admin.pack_generic",
};

// 以原生 select 编辑的五个装饰位及其标签（页面装饰另为带缩略图的单选），顺序同 UX 线框。
export const SELECT_SLOTS = [
  { slot: "home_poster", label: "admin.slot_home_poster" },
  { slot: "list_strip", label: "admin.slot_list_strip" },
  { slot: "card_badge", label: "admin.slot_card_badge" },
  { slot: "detail_ribbon", label: "admin.slot_detail_ribbon" },
  { slot: "cart_progress", label: "admin.slot_cart_progress" },
] as const satisfies readonly { slot: PromoSlot; label: CopyKey }[];

// 页面装饰的选项：清单的变体，最后为 none。
export const AMBIENT_CHOICES: readonly string[] = [...PROMO_VARIANTS.ambient, PROMO_AMBIENT_NONE];

// 照 SiteFrame：corners 与 fall 由 .acs-promo 的 data-promo-ambient 画出，garland 为其内的 .acs-promo-garland，none 什么都不画。
const WRAPPER_AMBIENTS: readonly string[] = ["corners", "fall"];
const AMBIENT_GARLAND = "garland";

// 对装饰的一次改动；值不变时返回原对象。
export type DecorationChange = (decoration: PromotionDecoration) => PromotionDecoration;

function firstVariant(decoration: PromotionDecoration, slot: PromoSlot): string {
  return PROMO_VARIANTS[slot][0] ?? decoration[slot];
}

// 换素材包（落实写法（3））：六个装饰位都改为第一个变体；配色为节日配色时改为新素材包的配色，沿用主题配色时不变；poster_mode 不变。
export function pickPack(decoration: PromotionDecoration, pack: string): PromotionDecoration {
  if (pack === decoration.pack) {
    return decoration;
  }
  return {
    ...decoration,
    pack,
    palette: decoration.palette === PROMO_KEEP_THEME ? PROMO_KEEP_THEME : pack,
    ambient: firstVariant(decoration, "ambient"),
    home_poster: firstVariant(decoration, "home_poster"),
    list_strip: firstVariant(decoration, "list_strip"),
    card_badge: firstVariant(decoration, "card_badge"),
    detail_ribbon: firstVariant(decoration, "detail_ribbon"),
    cart_progress: firstVariant(decoration, "cart_progress"),
  };
}

// 配色：PROMO_KEEP_THEME 或某个素材包 ID。
export function pickPalette(decoration: PromotionDecoration, palette: string): PromotionDecoration {
  return palette === decoration.palette ? decoration : { ...decoration, palette };
}

// 选「节日配色」：下拉默认是所选素材包的配色；已是节日配色时不变。
export function pickFestivalPalette(decoration: PromotionDecoration): PromotionDecoration {
  return decoration.palette === PROMO_KEEP_THEME ? pickPalette(decoration, decoration.pack) : decoration;
}

export function pickVariant(decoration: PromotionDecoration, slot: PromoSlot, variant: string): PromotionDecoration {
  return variant === decoration[slot] ? decoration : { ...decoration, [slot]: variant };
}

// 页面装饰的缩样（A12-states 的 Page ornaments）：默认主题（不设 data-shop-theme）的浅色 .acs，其内 .acs-promo 带当前素材包与配色（沿用主题配色时不带），
// 按 SiteFrame 的规则画出该变体；none 为同样大小、不画装饰的框。整块读屏忽略，文字在旁边。
function AmbientThumb({ decoration, ambient }: { decoration: PromotionDecoration; ambient: string }) {
  return (
    <span className="acs site-admin-promo-deco__thumb" data-mode="light" aria-hidden="true">
      <span
        className="acs-promo"
        data-promo-pack={decoration.pack}
        data-promo-palette={decoration.palette === PROMO_KEEP_THEME ? undefined : decoration.palette}
        data-promo-ambient={WRAPPER_AMBIENTS.includes(ambient) ? ambient : undefined}
      >
        {ambient === AMBIENT_GARLAND && <span className="acs-promo-garland" />}
      </span>
    </span>
  );
}

export interface AdminPromotionDecorationProps {
  decoration: PromotionDecoration;
  readOnly: boolean;
  onDecoration: (change: DecorationChange) => void;
  // 编辑区预览（编辑页传入）；没有时不显示小标题。
  preview?: ReactNode;
}

export default function AdminPromotionDecoration({ decoration, readOnly, onDecoration, preview }: AdminPromotionDecorationProps) {
  const t = useCopy();
  const paletteId = useId();
  const paletteSelectId = useId();
  const ambientId = useId();
  const previewId = useId();
  const keep = decoration.palette === PROMO_KEEP_THEME;
  const packName = (pack: PromoPack) => t(PACK_LABEL[pack]);
  return (
    <div className="site-admin-promo-deco">
      <label className="acs-admin__field site-admin-promo-deco__pack">
        {t("admin.promo_pack")}
        <select
          className="acs-admin__select"
          name="pack"
          value={decoration.pack}
          disabled={readOnly}
          onChange={(event: ChangeEvent<HTMLSelectElement>) => {
            const pack = event.target.value;
            onDecoration((current) => pickPack(current, pack));
          }}
        >
          {PROMO_PACKS.map((pack) => (
            <option key={pack} value={pack}>
              {packName(pack)}
            </option>
          ))}
        </select>
      </label>
      <div className="acs-admin__field" role="radiogroup" aria-labelledby={paletteId}>
        <span id={paletteId}>{t("admin.promo_palette")}</span>
        <div className="site-admin-promo-deco__palette">
          <label className="site-admin-promo-edit__check">
            <input
              type="radio"
              name="palette_mode"
              value={PROMO_KEEP_THEME}
              checked={keep}
              disabled={readOnly}
              onChange={() => {
                onDecoration((current) => pickPalette(current, PROMO_KEEP_THEME));
              }}
            />
            {t("admin.palette_keep")}
          </label>
          <span className="site-admin-promo-edit__check">
            <input
              type="radio"
              name="palette_mode"
              value="pack"
              checked={!keep}
              disabled={readOnly}
              aria-labelledby={paletteSelectId}
              onChange={() => {
                onDecoration(pickFestivalPalette);
              }}
            />
            <select
              id={paletteSelectId}
              className="acs-admin__select site-admin-promo-deco__palette-select"
              name="palette"
              aria-label={t("admin.promo_palette")}
              value={keep ? decoration.pack : decoration.palette}
              disabled={readOnly || keep}
              onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                const palette = event.target.value;
                onDecoration((current) => pickPalette(current, palette));
              }}
            >
              {PROMO_PACKS.map((pack) => (
                <option key={pack} value={pack}>
                  {t("admin.palette_of", { pack: packName(pack) })}
                </option>
              ))}
            </select>
          </span>
          {!keep && (
            <span className="acs" data-mode="light">
              <span className="acs-promo-swatch" data-promo-palette={decoration.palette} aria-hidden="true">
                <span className="acs-promo-swatch__1" />
                <span className="acs-promo-swatch__2" />
                <span className="acs-promo-swatch__soft" />
                <span className="acs-promo-swatch__surface" />
              </span>
            </span>
          )}
        </div>
      </div>
      <div className="acs-admin__field" role="radiogroup" aria-labelledby={ambientId}>
        <span id={ambientId}>{t("admin.slot_ambient")}</span>
        <div className="site-admin-promo-deco__choices">
          {AMBIENT_CHOICES.map((ambient, index) => (
            <label key={ambient} className="acs-admin__choice site-admin-promo-deco__choice">
              <input
                type="radio"
                name="ambient"
                value={ambient}
                checked={decoration.ambient === ambient}
                disabled={readOnly}
                onChange={() => {
                  onDecoration((current) => pickVariant(current, "ambient", ambient));
                }}
              />
              <AmbientThumb decoration={decoration} ambient={ambient} />
              <span>{ambient === PROMO_AMBIENT_NONE ? t("admin.variant_none") : t("admin.variant_n", { n: index + 1 })}</span>
            </label>
          ))}
        </div>
      </div>
      <div className="site-admin-promo-deco__slots">
        {SELECT_SLOTS.map(({ slot, label }) => (
          <label key={slot} className="acs-admin__field">
            {t(label)}
            <select
              className="acs-admin__select"
              name={slot}
              value={decoration[slot]}
              disabled={readOnly}
              onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                const variant = event.target.value;
                onDecoration((current) => pickVariant(current, slot, variant));
              }}
            >
              {PROMO_VARIANTS[slot].map((variant, index) => (
                <option key={variant} value={variant}>
                  {t("admin.variant_n", { n: index + 1 })}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {preview !== undefined && (
        <div className="site-admin-promo-deco__preview" role="group" aria-labelledby={previewId}>
          <strong id={previewId} className="site-admin-promo-deco__preview-title">
            {t("admin.editor_preview")}
          </strong>
          {preview}
        </div>
      )}
    </div>
  );
}
