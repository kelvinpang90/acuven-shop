import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { PromotionDecoration } from "../api/adminPromotions";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PROMO_PACKS, PROMO_VARIANTS } from "../promoManifest";
import { DEFAULT_STORE_DESIGN } from "../storeDesign";
import type { StoreDesign, StoreDesignResponse } from "../storeDesign";
import AdminPromotionEditorPreview, {
  EditorPreviewView,
  previewCopy,
  previewRuleBadge,
  previewRuleParams,
  previewStripText,
  readPreviewDesign,
} from "./AdminPromotionEditorPreview";
import type { PreviewMode } from "./AdminStoreDesignPage";
import { newPromotionForm, newPromotionRule } from "./promotionForm";
import type { PromotionForm, PromotionFormCopy, PromotionFormRule } from "./promotionForm";

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function render(form: PromotionForm, language: Language, options: { poster?: string | null; design?: StoreDesign; mode?: PreviewMode } = {}): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <EditorPreviewView
        form={form}
        poster={options.poster ?? null}
        design={options.design ?? DEFAULT_STORE_DESIGN}
        mode={options.mode ?? "light"}
        onMode={() => undefined}
      />
    </LanguageProvider>,
  );
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<[^>]*>/g, "\n")
    .split("\n")
    .map((value) => unescapeHtml(value).trim())
    .filter((value) => value !== "");
}

function tags(html: string, name: string): string[] {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].map((m) => m[0]);
}

function attributes(tag: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const m of tag.matchAll(/\s([A-Za-z][\w:-]*)(?:="([^"]*)")?/g)) {
    found.set((m[1] ?? "").toLowerCase(), unescapeHtml(m[2] ?? ""));
  }
  return found;
}

function classes(tag: string): string[] {
  return (attributes(tag).get("class") ?? "").split(/\s+/);
}

// 第一个 class 含 className 的 name 元素（含其内容）。
function element(html: string, name: string, className: string): string {
  const start = [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].find((m) => classes(m[0]).includes(className))?.index;
  if (start === undefined) {
    throw new Error(`no <${name}> with class ${className}`);
  }
  const re = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
  re.lastIndex = start;
  let depth = 0;
  for (let m = re.exec(html); m !== null; m = re.exec(html)) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) {
      return html.slice(start, m.index + m[0].length);
    }
  }
  throw new Error(`unclosed <${name}> with class ${className}`);
}

// 第一个 class 含 className 的元素的开始标签的属性。
function attrsOf(html: string, className: string): Map<string, string> {
  const tag = [...html.matchAll(/<[a-z]+\b[^>]*>/g)].map((m) => m[0]).find((item) => classes(item).includes(className));
  if (tag === undefined) {
    throw new Error(`no element with class ${className}`);
  }
  return attributes(tag);
}

function countOf(html: string, text: string): number {
  return html.split(text).length - 1;
}

const price = (sen: string, language: Language) => formatCopy(COPY["common.price_myr"][language], { amount: sen });

function withCopy(form: PromotionForm, copy: Partial<PromotionFormCopy>): PromotionForm {
  return { ...form, copy: { ...form.copy, ...copy } };
}

function withRules(form: PromotionForm, rules: PromotionFormRule[]): PromotionForm {
  return { ...form, rules };
}

function itemRule(mode: "percent" | "amount", discount: string, key = 0): PromotionFormRule {
  const rule = newPromotionRule("item_discount", key);
  return rule.kind === "item_discount" ? { ...rule, discount_mode: mode, discount } : rule;
}

function bxgyRule(buy: string, get: string, percent: string, key = 0): PromotionFormRule {
  const rule = newPromotionRule("buy_x_get_y", key);
  return rule.kind === "buy_x_get_y" ? { ...rule, buy_quantity: buy, get_quantity: get, get_percent: percent } : rule;
}

// 农历新年素材包、节日配色、各装饰位都不是第一个变体、模板海报。
const CNY: PromotionDecoration = {
  pack: "cny",
  palette: "cny",
  ambient: "corners",
  home_poster: "centered",
  list_strip: "ticket",
  card_badge: "seal",
  detail_ribbon: "panel",
  cart_progress: "steps",
  poster_mode: "template",
};

// 英文与中文标题、只有英文的副标题（带尖括号，确认按纯文本显示）、英文角标短语与马来文横条文字；第一条商品级规则为每件直降 20%。
const FORM: PromotionForm = withRules(
  withCopy(
    { ...newPromotionForm(), decoration: CNY },
    {
      title_en: "Lunar New Year Sale",
      title_zh: "农历新年特卖",
      subtitle_en: "Up to 20% off <b>",
      badge_en: "Lunar deal",
      strip_ms: "Jualan Tahun Baru",
    },
  ),
  [itemRule("percent", "20")],
);

describe("AdminPromotionEditorPreview", () => {
  // UX 0.20 A12 线框「[admin.editor_preview]（编辑区预览，随本页未保存的输入即时更新）」「<模板海报缩样> <活动横条缩样> <两张示例商品卡（角标）> <价格飘带缩样>」
  // 「<购物车进度条缩样>   ( [admin.preview_light] ) ( [admin.preview_dark] )」「[admin.editor_preview_note]」：依次为两个切换按钮、海报（标题与副标题）、横条文字、
  // 两张商品卡（角标与示意金额）、价格飘带（示意金额与角标）、进度条的两个金额与 admin.editor_preview_note；文案按当前语言，缺译用英文，按纯文本显示。
  it.each(LANGUAGES)("draws the five sketches in order in %s", (language) => {
    const html = render(FORM, language);
    const t = (key: CopyKey) => COPY[key][language];
    const title = language === "zh" ? "农历新年特卖" : "Lunar New Year Sale";
    const strip = language === "ms" ? "Jualan Tahun Baru" : title;
    const badges = ["Lunar deal", formatCopy(t("promo.badge_percent"), { percent: 20 })];
    const prices = [t("promo.a11y_original"), price("120.00", language), t("promo.a11y_sale"), price("96.00", language)];
    expect(textNodes(html)).toEqual([
      t("admin.preview_light"),
      t("admin.preview_dark"),
      title,
      "Up to 20% off <b>",
      strip,
      ...badges,
      ...prices,
      ...badges,
      ...prices,
      ...prices,
      ...badges,
      price("60.00", language),
      price("100.00", language),
      t("admin.editor_preview_note"),
    ]);
    expect(html).toContain(escapeHtml("Up to 20% off <b>"));
    expect(html).not.toContain("<b>");
    // 只画外观：没有链接、表单控件与倒计时，按钮只有两个切换按钮。
    expect(html).not.toMatch(/<(a|input|select|textarea|form)\b/);
    expect(tags(html, "button")).toHaveLength(2);
    expect(html).not.toContain("acs-promo-count");
  });

  // UX 0.20 A12 说明「编辑区预览」「按本页当前（含未保存）的文案、素材包、配色、各装饰位变体与海报模式，在浏览器里画出…的缩样」与落实写法（1）「装饰属性与 SiteFrame
  // 的规则相同」：预览框内为 .acs，其内 .acs-promo 带素材包；配色为 theme 时不带 data-promo-palette；corners 与 fall 为 data-promo-ambient，garland 为读屏忽略的
  // .acs-promo-garland，none 什么都不画。
  it.each(LANGUAGES)("marks the pack, palette and page ornaments like SiteFrame in %s", (language) => {
    for (const pack of PROMO_PACKS) {
      for (const palette of ["theme", ...PROMO_PACKS]) {
        const html = render({ ...FORM, decoration: { ...CNY, pack, palette } }, language);
        const promo = attrsOf(html, "acs-promo");
        expect(promo.get("class")).toBe("acs-promo");
        expect(promo.get("data-promo-pack")).toBe(pack);
        expect(promo.get("data-promo-palette")).toBe(palette === "theme" ? undefined : palette);
      }
    }
    for (const ambient of [...PROMO_VARIANTS.ambient, "none"]) {
      const html = render({ ...FORM, decoration: { ...CNY, ambient } }, language);
      const promo = attrsOf(html, "acs-promo");
      expect(promo.get("data-promo-ambient"), ambient).toBe(ambient === "corners" || ambient === "fall" ? ambient : undefined);
      expect(countOf(html, '<div class="acs-promo-garland" aria-hidden="true"></div>'), ambient).toBe(ambient === "garland" ? 1 : 0);
      expect(html.startsWith('<span class="site-admin-promo-preview__modes">'), ambient).toBe(true);
      expect(element(html, "div", "acs-admin__preview").startsWith('<div class="acs-admin__preview site-admin-promo-preview__box"><div class="acs site-admin-promo-preview__shop"')).toBe(true);
    }
  });

  // UX 0.20 A12 说明「编辑区预览」「…各装饰位变体…」：海报按 home_poster（split、centered）、横条按 list_strip（band、ticket）、商品卡与飘带的角标按 card_badge（tag、seal，
  // seal 以活动图案开头）、飘带按 detail_ribbon（ribbon、panel）、进度条按 cart_progress（bar 为 60% 的进度条，steps 为 10 格点亮 6 格）。
  it.each(LANGUAGES)("draws every variant of each slot in %s", (language) => {
    for (const variant of PROMO_VARIANTS.home_poster) {
      const html = render({ ...FORM, decoration: { ...CNY, home_poster: variant } }, language);
      expect(attrsOf(html, "acs-promo-poster").get("class")).toBe(`acs-promo-poster acs-promo-poster--${variant} site-admin-promo-preview__poster`);
      expect(element(html, "div", "acs-promo-poster")).toContain('<div class="acs-promo-poster__art" aria-hidden="true"></div>');
    }
    for (const variant of PROMO_VARIANTS.list_strip) {
      const html = render({ ...FORM, decoration: { ...CNY, list_strip: variant } }, language);
      expect(attrsOf(html, "acs-promo-strip").get("class")).toBe(`acs-promo-strip acs-promo-strip--${variant} site-admin-promo-preview__strip`);
    }
    for (const variant of PROMO_VARIANTS.card_badge) {
      const html = render({ ...FORM, decoration: { ...CNY, card_badge: variant } }, language);
      // 两张商品卡与飘带各两个角标。
      expect(countOf(html, `class="acs-promo-badge acs-promo-badge--${variant}"`), variant).toBe(6);
      expect(countOf(html, '<span class="acs-promo-motif" aria-hidden="true"></span>'), variant).toBe(1 + (variant === "seal" ? 6 : 0));
    }
    for (const variant of PROMO_VARIANTS.detail_ribbon) {
      const html = render({ ...FORM, decoration: { ...CNY, detail_ribbon: variant } }, language);
      expect(attrsOf(html, "acs-promo-ribbon").get("class")).toBe(`acs-promo-ribbon acs-promo-ribbon--${variant} site-admin-promo-preview__ribbon`);
    }
    const bar = element(render({ ...FORM, decoration: { ...CNY, cart_progress: "bar" } }, language), "div", "acs-promo-progress");
    expect(bar).toContain('<div class="acs-promo-progress__track" aria-hidden="true"><span class="acs-promo-progress__fill" style="--progress:60%"></span></div>');
    expect(bar).not.toContain("acs-promo-progress__steps");
    const steps = element(render({ ...FORM, decoration: { ...CNY, cart_progress: "steps" } }, language), "div", "acs-promo-progress");
    expect(steps).toContain(`<div class="acs-promo-progress__steps" aria-hidden="true">${"<span data-on=\"\"></span>".repeat(6)}${"<span></span>".repeat(4)}</div>`);
    expect(steps).not.toContain("acs-promo-progress__track");
    for (const html of [bar, steps]) {
      expect(textNodes(element(html, "div", "acs-promo-progress__labels"))).toEqual([price("60.00", language), price("100.00", language)]);
    }
  });

  // UX 0.20 A12 说明「海报」「编辑区预览在上传图模式下显示已上传的图，没有图的位置显示占位」与落实写法（2）「为 template 时按 home_poster 变体画 acs-promo-poster，只有标题与副标题，
  // 不显示倒计时与按钮」：模板模式为海报缩样；上传图模式为已保存的桌面海报图（不显示模板文字），没有图时为同样位置的占位块。
  it.each(LANGUAGES)("shows the template, the saved desktop image or a placeholder in %s", (language) => {
    const desktop = `/api/promotion-posters/${"a".repeat(32)}.webp`;
    const template = render(FORM, language, { poster: desktop });
    expect(template).toContain("acs-promo-poster__art");
    expect(template).not.toMatch(/<img\b/);

    const image = { ...FORM, decoration: { ...CNY, poster_mode: "image" } };
    const shown = render(image, language, { poster: desktop });
    expect(shown).toContain(`<div class="acs-promo-poster-img site-admin-promo-preview__poster"><img src="${desktop}" alt=""/></div>`);
    expect(shown).not.toContain("acs-promo-poster__text");
    const empty = render(image, language);
    expect(empty).toContain('<div class="acs-promo-poster-img site-admin-promo-preview__poster" aria-hidden="true"></div>');
    expect(empty).not.toMatch(/<img\b/);
    expect(empty).not.toContain("acs-promo-poster__text");
  });

  // UX 0.20 A12 说明「文案：…其他缺译回退英文（[admin.copy_fallback]）」与落实写法（2）「文案按当前语言，缺译用英文，为空时该行不显示」「文字为横条文字，缺时用标题」：
  // 当前语言为空或全是空白时用英文；都为空时海报的标题、副标题与横条文字整行不显示，角标短语不显示；横条文字缺时用标题。
  it.each(LANGUAGES)("falls back to English and hides empty lines in %s", (language) => {
    const blank = render(withRules({ ...newPromotionForm(), decoration: CNY }, []), language);
    expect(blank).not.toContain("acs-promo-poster__title");
    expect(blank).not.toContain("acs-promo-poster__sub");
    expect(blank).not.toContain("acs-promo-strip__title");
    expect(blank).not.toContain("acs-promo-badges");
    expect(blank).not.toContain("acs-promo-ribbon__meta");
    expect(blank).toContain("acs-promo-strip");

    const english: PromotionFormCopy = { ...newPromotionForm().copy, title_en: "Sale", badge_en: "Deal", strip_en: "Strip" };
    // 当前语言的标题全是空白（英文时就是英文本身，仍为 Sale）。
    const copy: PromotionFormCopy = language === "en" ? english : { ...english, [`title_${language}`]: "  " };
    expect(previewCopy(copy, "title", language)).toBe("Sale");
    expect(previewCopy(copy, "subtitle", language)).toBeNull();
    expect(previewCopy(copy, "badge", language)).toBe("Deal");
    expect(previewStripText(copy, language)).toBe("Strip");
    expect(previewStripText({ ...copy, strip_en: " " }, language)).toBe("Sale");
    expect(previewStripText({ ...copy, strip_en: "", title_en: "" }, language)).toBeNull();
    const own = { ...copy, [`title_${language}`]: " Own ", [`badge_${language}`]: "Mine" };
    expect(previewCopy(own, "title", language)).toBe("Own");
    expect(previewCopy(own, "badge", language)).toBe("Mine");
  });

  // UX 0.20 A12 说明「编辑区预览」「缩样用固定的示例商品与金额（[admin.editor_preview_note] 写明是示意），不调用计价，也不代表前台的实际价格」与落实写法（2）「价格用 PromoPrices
  // 显示固定的示意金额原价 12000 仙与活动价 9600 仙…示意金额不论规则类别都照样显示」：没有规则或只有整单级规则时金额照样显示；商品卡没有商品名称，图片位为占位。
  it.each(LANGUAGES)("shows the same sample amounts whatever the rules in %s", (language) => {
    const free = newPromotionRule("free_shipping", 0);
    const spend = newPromotionRule("spend_discount", 1);
    for (const rules of [[], [free], [spend, free], [itemRule("amount", "5.00")], [bxgyRule("2", "1", "100")]]) {
      const html = render(withRules(FORM, rules), language);
      expect(countOf(html, `<span class="acs-price-was"><span class="acs-vh">${escapeHtml(COPY["promo.a11y_original"][language])}</span>${price("120.00", language)}</span>`)).toBe(2);
      expect(countOf(html, `<span class="acs-price-sale"><span class="acs-vh">${escapeHtml(COPY["promo.a11y_sale"][language])}</span>${price("96.00", language)}</span>`)).toBe(2);
      expect(element(html, "div", "acs-promo-ribbon__prices")).toContain(price("120.00", language));
      expect(element(html, "div", "acs-promo-ribbon__prices")).toContain(price("96.00", language));
      expect(countOf(html, '<span class="acs-pcard__img">')).toBe(2);
      expect(html).not.toContain("acs-pcard__name");
    }
  });

  // UX 0.20 A12 线框「<两张示例商品卡（角标）>」与落实写法（2）「角标短语与第一条商品级规则的 badgeText（该规则的参数为空或换不成整数、整仙时不显示规则角标），没有时不显示」：
  // 规则角标取第一条商品级规则（每件直降按百分比或金额，买 X 享 Y 按送或折），整单级规则不算；该规则参数为空或不合格时不显示规则角标（不往后找）；角标短语照常显示。
  it.each(LANGUAGES)("takes the rule badge from the first item rule in %s", (language) => {
    const t = (key: CopyKey, vars: Record<string, string | number>) => formatCopy(COPY[key][language], vars);
    const free = newPromotionRule("free_shipping", 9);
    expect(previewRuleBadge([itemRule("percent", "20")], language)).toBe(t("promo.badge_percent", { percent: 20 }));
    expect(previewRuleBadge([free, itemRule("amount", "5.5")], language)).toBe(t("promo.badge_amount", { amount: "5.50" }));
    expect(previewRuleBadge([bxgyRule("2", "1", "100")], language)).toBe(t("promo.badge_bxgy_free", { x: 2, y: 1 }));
    expect(previewRuleBadge([bxgyRule("3", "1", "50"), itemRule("percent", "10")], language)).toBe(t("promo.badge_bxgy_pct", { x: 3, y: 1, percent: 50 }));
    for (const rules of [[], [free], [itemRule("percent", "")], [itemRule("percent", "1.5")], [itemRule("amount", "abc")], [bxgyRule("2", "", "100")], [itemRule("percent", ""), itemRule("percent", "10", 1)]]) {
      expect(previewRuleBadge(rules, language)).toBeNull();
      const html = render(withRules(FORM, rules), language);
      expect(countOf(html, "acs-promo-badges")).toBe(3);
      expect(textNodes(element(html, "span", "acs-promo-badges"))).toEqual(["Lunar deal"]);
    }
    expect(previewRuleParams([itemRule("amount", "12.30")])).toEqual({
      kind: "item_discount",
      discount_mode: "amount",
      percent: null,
      amount_sen: 1230,
      buy_quantity: null,
      get_quantity: null,
      get_percent: null,
    });
    // 角标短语与规则角标都没有时，商品卡与飘带都不放角标。
    const none = render(withRules(withCopy(FORM, { badge_en: "" }), []), language);
    expect(none).not.toContain("acs-promo-badges");
    expect(none).not.toContain("acs-promo-ribbon__meta");
  });

  // UX 0.20 A12 线框「( [admin.preview_light] ) ( [admin.preview_dark] )」与说明「可切换浅色与深色（沿用 A08 的 [admin.preview_light]、[admin.preview_dark]）」：
  // 两个按钮以 aria-pressed 标当前（当前的为 acs-admin__btn，另一个加 acs-admin__btn--secondary），.acs 的 data-mode 为所选深浅色；初值为浅色。
  it.each(LANGUAGES)("switches between light and dark in %s", (language) => {
    for (const mode of ["light", "dark"] as const) {
      const html = render(FORM, language, { mode });
      expect(tags(html, "button").map(attributes).map((attrs) => [attrs.get("class"), attrs.get("type"), attrs.get("aria-pressed")])).toEqual([
        [mode === "light" ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary", "button", String(mode === "light")],
        [mode === "dark" ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary", "button", String(mode === "dark")],
      ]);
      expect(attrsOf(html, "site-admin-promo-preview__shop").get("data-mode")).toBe(mode);
    }
    const initial = renderToStaticMarkup(
      <LanguageProvider storage={storage(language)}>
        <AdminPromotionEditorPreview form={FORM} poster={null} />
      </LanguageProvider>,
    );
    expect(attrsOf(initial, "site-admin-promo-preview__shop").get("data-mode")).toBe("light");
    expect(tags(initial, "button").map((tag) => attributes(tag).get("aria-pressed"))).toEqual(["true", "false"]);
  });

  // 落实写法（1）「data-shop-theme 与 data-accent 取前台店铺装修，经 fetchStoreDesign 读一次，读取失败或读取中用 DEFAULT_STORE_DESIGN」（UX 0.20 A12 说明「编辑区预览」
  // 「在浏览器里画出…的缩样」）：取到时为店铺的主题与主色（默认主色不设 data-accent，同 SiteFrame）；读取中（首次渲染）与读取失败时为默认主题。
  it.each(LANGUAGES)("uses the store theme and falls back to the default in %s", async (language) => {
    const batik: StoreDesign = { ...DEFAULT_STORE_DESIGN, theme: "batik", accent: "sogan" };
    const read = vi.fn((signal?: AbortSignal) => {
      expect(signal).toBeInstanceOf(AbortSignal);
      return Promise.resolve<StoreDesignResponse>({ design: batik, featuredSlugs: [], logo: null });
    });
    const signal = new AbortController().signal;
    await expect(readPreviewDesign(signal, read)).resolves.toEqual(batik);
    expect(read).toHaveBeenCalledTimes(1);
    await expect(readPreviewDesign(signal, () => Promise.reject(new Error("offline")))).resolves.toBe(DEFAULT_STORE_DESIGN);

    const shop = attrsOf(render(FORM, language, { design: batik }), "site-admin-promo-preview__shop");
    expect([shop.get("data-shop-theme"), shop.get("data-accent")]).toEqual(["batik", "sogan"]);
    for (const html of [
      render(FORM, language),
      renderToStaticMarkup(
        <LanguageProvider storage={storage(language)}>
          <AdminPromotionEditorPreview form={FORM} poster={null} />
        </LanguageProvider>,
      ),
    ]) {
      const fallback = attrsOf(html, "site-admin-promo-preview__shop");
      expect(fallback.get("class")).toBe("acs site-admin-promo-preview__shop");
      expect(fallback.get("data-shop-theme")).toBe("pandan");
      expect(fallback.has("data-accent")).toBe(false);
    }
  });
});
