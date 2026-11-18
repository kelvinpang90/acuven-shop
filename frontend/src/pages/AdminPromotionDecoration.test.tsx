import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PromotionDecoration } from "../api/adminPromotions";
import { COPY, formatCopy, LANGUAGES } from "../i18n/copy";
import type { Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import { PROMO_PACKS, PROMO_VARIANTS } from "../promoManifest";
import AdminPromotionDecoration, {
  AMBIENT_CHOICES,
  PACK_LABEL,
  pickFestivalPalette,
  pickPack,
  pickPalette,
  pickVariant,
  SELECT_SLOTS,
} from "./AdminPromotionDecoration";
import { newPromotionForm } from "./promotionForm";

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function render(decoration: PromotionDecoration, language: Language, readOnly = false): string {
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <AdminPromotionDecoration decoration={decoration} readOnly={readOnly} onDecoration={() => undefined} />
    </LanguageProvider>,
  );
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

// name 属性为 name 的 select（含其选项）。
function select(html: string, name: string): string {
  const start = [...html.matchAll(/<select\b[^>]*>/g)].find((m) => attributes(m[0]).get("name") === name)?.index;
  if (start === undefined) {
    throw new Error(`no select named ${name}`);
  }
  return html.slice(start, html.indexOf("</select>", start) + "</select>".length);
}

interface Option {
  value: string;
  text: string;
  selected: boolean;
}

function options(selectHtml: string): Option[] {
  return [...selectHtml.matchAll(/<option\b([^>]*)>([^<]*)<\/option>/g)].map((m) => {
    const attrs = attributes(`<option${m[1] ?? ""}>`);
    return { value: attrs.get("value") ?? "", text: unescapeHtml(m[2] ?? ""), selected: attrs.has("selected") };
  });
}

function radios(html: string, name: string): Map<string, string>[] {
  return tags(html, "input")
    .map(attributes)
    .filter((attrs) => attrs.get("type") === "radio" && attrs.get("name") === name);
}

// 页面装饰的各项（label.acs-admin__choice 的内容）。
function choices(html: string): string[] {
  return html
    .split('<label class="acs-admin__choice site-admin-promo-deco__choice">')
    .slice(1)
    .map((part) => part.slice(0, part.indexOf("</label>")));
}

const packName = (pack: (typeof PROMO_PACKS)[number], language: Language) => COPY[PACK_LABEL[pack]][language];
const style = (n: number, language: Language) => formatCopy(COPY["admin.variant_n"][language], { n });

// 农历新年素材包、节日配色，各装饰位都不是第一个变体，上传图海报。
const CNY: PromotionDecoration = {
  pack: "cny",
  palette: "cny",
  ambient: "corners",
  home_poster: "centered",
  list_strip: "ticket",
  card_badge: "seal",
  detail_ribbon: "panel",
  cart_progress: "steps",
  poster_mode: "image",
};
// 新表单的装饰：通用素材包、沿用主题配色、各装饰位第一个变体。
const THEME = newPromotionForm().decoration;

describe("AdminPromotionDecoration", () => {
  // UX 0.20 A12「[admin.promo_pack]    <素材包> ▾（[admin.pack_*] 七项）」「[admin.promo_palette] ( ) [admin.palette_keep]  ( ) [admin.palette_of] ▾」
  // 「[admin.slot_ambient]  <变体> ▾（含 [admin.variant_none]）」「[admin.slot_home_poster] <变体> ▾   [admin.slot_list_strip] <变体> ▾」…与说明「[admin.palette_of] 下拉
  // 列出 7 套节日配色（{pack} 为对应素材包名称）」「各装饰位的下拉列出所选素材包的变体（以视觉稿的缩略图与 [admin.variant_n] 表示）」：依次为素材包（七项）、
  // 配色（admin.palette_keep 与七套 admin.palette_of）、页面装饰（样式 1 至 3 与 admin.variant_none）与其余五个装饰位（各为样式 1、2），文字按当前语言。
  it.each(LANGUAGES)("shows the packs, palettes, slots and variants in %s", (language) => {
    const html = render(CNY, language);
    const t = (key: keyof typeof COPY) => COPY[key][language];
    expect(textNodes(html)).toEqual([
      t("admin.promo_pack"),
      ...PROMO_PACKS.map((pack) => packName(pack, language)),
      t("admin.promo_palette"),
      t("admin.palette_keep"),
      ...PROMO_PACKS.map((pack) => formatCopy(t("admin.palette_of"), { pack: packName(pack, language) })),
      t("admin.slot_ambient"),
      style(1, language),
      style(2, language),
      style(3, language),
      t("admin.variant_none"),
      ...SELECT_SLOTS.flatMap(({ label }) => [t(label), style(1, language), style(2, language)]),
    ]);
    expect(options(select(html, "pack")).map((option) => option.value)).toEqual([...PROMO_PACKS]);
    expect(options(select(html, "palette")).map((option) => option.value)).toEqual([...PROMO_PACKS]);
    for (const { slot } of SELECT_SLOTS) {
      expect(options(select(html, slot)).map((option) => option.value), slot).toEqual(PROMO_VARIANTS[slot]);
      expect(options(select(html, slot)).find((option) => option.selected)?.value, slot).toBe(CNY[slot]);
    }
    expect(options(select(html, "pack")).find((option) => option.selected)?.value).toBe("cny");
    expect(radios(html, "ambient").map((attrs) => attrs.get("value"))).toEqual([...AMBIENT_CHOICES]);
    expect(radios(html, "ambient").map((attrs) => attrs.has("checked"))).toEqual([false, true, false, false]);
  });

  // UX 0.20 A12「[admin.promo_palette] ( ) [admin.palette_keep]  ( ) [admin.palette_of] ▾」与说明「[admin.palette_of] 下拉列出 7 套节日配色…，默认是所选素材包的配色」：
  // 沿用主题配色时第一个单选选中，下拉禁用、显示所选素材包的那一项，没有色块；节日配色时第二个单选选中，下拉可选、显示所选配色，旁边为该配色的色块。
  // 下拉的读屏标签为 admin.promo_palette，第二个单选以下拉为标签。
  it.each(LANGUAGES)("shows the two palette modes and the swatch in %s", (language) => {
    const keep = render(THEME, language);
    const keepSelect = attributes(tags(select(keep, "palette"), "select")[0] ?? "");
    expect(radios(keep, "palette_mode").map((attrs) => [attrs.get("value"), attrs.has("checked")])).toEqual([
      ["theme", true],
      ["pack", false],
    ]);
    expect(keepSelect.has("disabled")).toBe(true);
    expect(keepSelect.get("aria-label")).toBe(COPY["admin.promo_palette"][language]);
    expect(radios(keep, "palette_mode")[1]?.get("aria-labelledby")).toBe(keepSelect.get("id"));
    expect(options(select(keep, "palette")).find((option) => option.selected)?.value).toBe("generic");
    expect(keep).not.toContain("acs-promo-swatch");

    for (const decoration of [CNY, { ...CNY, palette: "raya" }]) {
      const festival = render(decoration, language);
      expect(radios(festival, "palette_mode").map((attrs) => attrs.has("checked"))).toEqual([false, true]);
      expect(attributes(tags(select(festival, "palette"), "select")[0] ?? "").has("disabled")).toBe(false);
      expect(options(select(festival, "palette")).find((option) => option.selected)?.value).toBe(decoration.palette);
      expect(festival).toContain(
        `<span class="acs" data-mode="light"><span class="acs-promo-swatch" data-promo-palette="${decoration.palette}" aria-hidden="true">` +
          '<span class="acs-promo-swatch__1"></span><span class="acs-promo-swatch__2"></span><span class="acs-promo-swatch__soft"></span>' +
          '<span class="acs-promo-swatch__surface"></span></span></span>',
      );
    }
  });

  // UX 0.20 A12 说明「各装饰位的下拉列出所选素材包的变体（以视觉稿的缩略图与 [admin.variant_n] 表示）」与「[admin.slot_ambient]  <变体> ▾（含 [admin.variant_none]）」：
  // 页面装饰每项为单选、默认主题浅色的缩样与文字；缩样的 .acs-promo 带当前素材包（节日配色时另带配色），garland 画 .acs-promo-garland，corners 与 fall 为
  // data-promo-ambient，none 为同样的框、不画装饰。
  it.each(LANGUAGES)("draws a thumbnail for each page ornament and none in %s", (language) => {
    for (const decoration of [CNY, THEME]) {
      const items = choices(render(decoration, language));
      expect(items).toHaveLength(4);
      for (const [index, item] of items.entries()) {
        const ambient = AMBIENT_CHOICES[index] ?? "";
        const [radio, outer, inner] = [tags(item, "input")[0] ?? "", tags(item, "span")[0] ?? "", tags(item, "span")[1] ?? ""].map(attributes);
        expect(radio?.get("value")).toBe(ambient);
        expect(outer?.get("class")).toBe("acs site-admin-promo-deco__thumb");
        expect(outer?.get("data-mode")).toBe("light");
        expect(outer?.has("data-shop-theme")).toBe(false);
        expect(outer?.get("aria-hidden")).toBe("true");
        expect(inner?.get("class")).toBe("acs-promo");
        expect(inner?.get("data-promo-pack")).toBe(decoration.pack);
        expect(inner?.get("data-promo-palette")).toBe(decoration.palette === "theme" ? undefined : decoration.palette);
        expect(inner?.get("data-promo-ambient")).toBe(ambient === "corners" || ambient === "fall" ? ambient : undefined);
        expect(item.includes("acs-promo-garland"), ambient).toBe(ambient === "garland");
        expect(textNodes(item)).toEqual([ambient === "none" ? COPY["admin.variant_none"][language] : style(index + 1, language)]);
      }
    }
  });

  // UX 0.20 A12 说明「换素材包时各装饰位改为新素材包的第一个变体」与落实写法（3）「配色为节日配色时改为新素材包的配色，为 theme 时不变」：六个装饰位都回到第一个变体
  // （页面装饰为 garland），节日配色跟着新素材包，沿用主题配色不变，海报模式不变；同一个素材包不改。
  it("resets every slot and follows the festival palette when the pack changes", () => {
    const firsts = { ambient: "garland", home_poster: "split", list_strip: "band", card_badge: "tag", detail_ribbon: "ribbon", cart_progress: "bar" };
    expect(pickPack(CNY, "raya")).toEqual({ ...firsts, pack: "raya", palette: "raya", poster_mode: "image" });
    expect(pickPack({ ...CNY, palette: "merdeka" }, "christmas")).toEqual({ ...firsts, pack: "christmas", palette: "christmas", poster_mode: "image" });
    expect(pickPack({ ...CNY, palette: "theme" }, "deepavali")).toEqual({ ...firsts, pack: "deepavali", palette: "theme", poster_mode: "image" });
    expect(pickPack(CNY, "cny")).toBe(CNY);
    // 换后的页面：各装饰位的下拉与页面装饰都选第一项。
    const html = render(pickPack(CNY, "mega_sale"), "en");
    for (const { slot } of SELECT_SLOTS) {
      expect(options(select(html, slot)).findIndex((option) => option.selected), slot).toBe(0);
    }
    expect(radios(html, "ambient").map((attrs) => attrs.has("checked"))).toEqual([true, false, false, false]);
    expect(options(select(html, "palette")).find((option) => option.selected)?.value).toBe("mega_sale");
  });

  // UX 0.20 A12 说明「[admin.palette_of] 下拉列出 7 套节日配色…，默认是所选素材包的配色」：从沿用主题配色改选节日配色时为所选素材包的配色；改回 admin.palette_keep
  // 为 theme；在下拉里可选其他素材包的配色；改单个装饰位只改那一项。值不变时返回原对象。
  it("picks palettes and variants", () => {
    expect(pickFestivalPalette(THEME)).toEqual({ ...THEME, palette: "generic" });
    expect(pickFestivalPalette(pickPack(THEME, "raya"))).toEqual({ ...pickPack(THEME, "raya"), palette: "raya" });
    expect(pickFestivalPalette(CNY)).toBe(CNY);
    expect(pickPalette(CNY, "theme")).toEqual({ ...CNY, palette: "theme" });
    expect(pickPalette(CNY, "deepavali")).toEqual({ ...CNY, palette: "deepavali" });
    expect(pickPalette(CNY, "cny")).toBe(CNY);
    expect(pickVariant(CNY, "ambient", "none")).toEqual({ ...CNY, ambient: "none" });
    expect(pickVariant(CNY, "cart_progress", "bar")).toEqual({ ...CNY, cart_progress: "bar" });
    expect(pickVariant(CNY, "list_strip", "ticket")).toBe(CNY);
  });

  // UX 0.20 A12「状态与可用操作」「已结束 | 无（全部只读，显示 [admin.promo_ended_readonly]） | 复制」与「进行中 | 内部名称、文案、装饰、海报、AI …」：只读时素材包、
  // 配色、页面装饰与五个装饰位都禁用；可改时只有沿用主题配色下的配色下拉禁用。
  it.each(LANGUAGES)("disables every control when read-only in %s", (language) => {
    for (const decoration of [CNY, THEME]) {
      const locked = render(decoration, language, true);
      const controls = [...tags(locked, "input"), ...tags(locked, "select")];
      // 素材包、配色与五个装饰位的 7 个 select，配色的 2 个与页面装饰的 4 个单选。
      expect(controls).toHaveLength(7 + 2 + 4);
      expect(controls.every((tag) => attributes(tag).has("disabled"))).toBe(true);
    }
    const open = render(THEME, language);
    const disabled = [...tags(open, "input"), ...tags(open, "select")].filter((tag) => attributes(tag).has("disabled")).map((tag) => attributes(tag).get("name"));
    expect(disabled).toEqual(["palette"]);
    expect([...tags(render(CNY, language), "input"), ...tags(render(CNY, language), "select")].some((tag) => attributes(tag).has("disabled"))).toBe(false);
  });
});
