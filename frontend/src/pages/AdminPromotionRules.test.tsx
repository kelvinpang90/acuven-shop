import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminCategory, CategoriesRead } from "../api/adminCategories";
import type { AdminProductListItem, ProductsRead } from "../api/adminProducts";
import { COPY, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "../i18n/language";
import {
  addOptionDisabled,
  addProduct,
  addRule,
  addTier,
  AdminPromotionRulesView,
  categoryName,
  loadScopeLists,
  moveRule,
  productName,
  removeProduct,
  removeRule,
  removeTier,
  SCOPE_LOADING,
  scopeListsStep,
  searchProducts,
  toggleCategory,
} from "./AdminPromotionRules";
import type { ScopeApi, ScopeLists } from "./AdminPromotionRules";
import { formProblems, newPromotionForm, newPromotionRule, switchDiscountMode } from "./promotionForm";
import type {
  BuyXGetYFormRule,
  FreeShippingFormRule,
  ItemDiscountFormRule,
  ItemFormRule,
  PromotionForm,
  PromotionFormRule,
  SpendDiscountFormRule,
} from "./promotionForm";

afterEach(() => {
  vi.restoreAllMocks();
});

function storage(language: Language) {
  return {
    getItem: (key: string) => (key === LANGUAGE_STORAGE_KEY ? language : null),
    setItem: () => undefined,
  };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function unescapeHtml(value: string): string {
  return value.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function textNodes(html: string): string[] {
  return html
    .replace(/<!--[\s\S]*?-->/g, "\n")
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

// 所有 class 含 className 的 name 元素（含其内容），按出现的顺序。
function elements(html: string, name: string, className: string): string[] {
  const found: string[] = [];
  for (const start of [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, "g"))].filter((m) => classes(m[0]).includes(className))) {
    const re = new RegExp(`<${name}\\b[^>]*>|</${name}>`, "g");
    re.lastIndex = start.index;
    let depth = 0;
    for (let m = re.exec(html); m !== null; m = re.exec(html)) {
      depth += m[0].startsWith("</") ? -1 : 1;
      if (depth === 0) {
        found.push(html.slice(start.index, m.index + m[0].length));
        break;
      }
    }
  }
  return found;
}

function only(list: string[]): string {
  const [first] = list;
  if (list.length !== 1 || first === undefined) {
    throw new Error(`expected one element, found ${String(list.length)}`);
  }
  return first;
}

// name 属性为 name（单选与勾选另按 value）的 input 标签。
function input(html: string, name: string, value?: string): Map<string, string> {
  const tag = tags(html, "input").find((item) => {
    const attrs = attributes(item);
    return attrs.get("name") === name && (value === undefined || attrs.get("value") === value);
  });
  if (tag === undefined) {
    throw new Error(`no input named ${name}`);
  }
  return attributes(tag);
}

function hasInput(html: string, name: string): boolean {
  return tags(html, "input").some((tag) => attributes(tag).get("name") === name);
}

// aria-label 为 label 的按钮。
function buttonsLabeled(html: string, label: string): Map<string, string>[] {
  return tags(html, "button")
    .map(attributes)
    .filter((attrs) => attrs.get("aria-label") === label);
}

// 文字为 text 的按钮。
function buttonsWithText(html: string, text: string): Map<string, string>[] {
  return [...html.matchAll(/(<button\b[^>]*>)([^<]*)<\/button>/g)].filter((m) => unescapeHtml(m[2] ?? "") === text).map((m) => attributes(m[1] ?? ""));
}

// 元素（以属性表给出）旁的 admin.promo_field_invalid：以 aria-describedby 指向它。
function describedInvalid(html: string, attrs: Map<string, string>, language: Language): boolean {
  const id = attrs.get("aria-describedby");
  if (id === undefined) {
    return false;
  }
  return html.includes(`<span id="${escapeHtml(id)}" class="site-admin-promo-edit__field-invalid">${escapeHtml(COPY["admin.promo_field_invalid"][language])}</span>`);
}

function countOf(html: string, text: string): number {
  return html.split(text).length - 1;
}

function category(id: number, slug: string, names: Partial<Pick<AdminCategory, "name_en" | "name_zh" | "name_ms">> = {}): AdminCategory {
  return { id, slug, name_en: null, name_zh: null, name_ms: null, is_active: true, version: 1, visible_products: 0, ...names };
}

function item(id: number, overrides: Partial<AdminProductListItem> = {}): AdminProductListItem {
  return {
    id,
    slug: `product-${String(id)}`,
    name_en: `Product ${String(id)}`,
    name_zh: null,
    name_ms: null,
    is_active: true,
    category_id: 3,
    category_name_en: null,
    category_name_zh: null,
    category_name_ms: null,
    ...overrides,
  };
}

// 分类：一个有中文名、一个只有 slug、一个有马来文名（缺译用英文，再没有用 slug）。
const CATEGORIES = [category(3, "apparel", { name_en: "Apparel", name_zh: "服装" }), category(4, "bags"), category(5, "home", { name_en: "Home", name_ms: "Rumah" })];
const CATEGORY_NAMES: Record<Language, string[]> = { en: ["Apparel", "bags", "Home"], zh: ["服装", "bags", "Home"], ms: ["Apparel", "bags", "Rumah"] };

// 商品：13 未上架；14 没有名称（用 slug）。
const PRODUCTS = [
  item(11, { slug: "crew-tee", name_en: "Crew Tee", name_zh: "圆领T恤" }),
  item(12, { slug: "soy-candle", name_en: "Soy Wax Candle", name_ms: "Lilin Soya" }),
  item(13, { slug: "old-mug", name_en: "Old Tee Mug", is_active: false }),
  item(14, { slug: "tee-bag", name_en: null }),
];
const OK_LISTS: ScopeLists = { status: "ok", categories: CATEGORIES, products: PRODUCTS };

// 读回的四类规则（各一条）：每件直降（选了分类 3、商品 12 与列表里没有的 99）、买 2 享 1 免费（分类 5）、两档满额减、免运费门槛 RM120。
const ITEM: ItemDiscountFormRule = {
  key: 0,
  rule_id: 1,
  kind: "item_discount",
  discount_mode: "percent",
  discount: "20",
  product_ids: [12, 99],
  products: [
    { id: 12, slug: "soy-candle", name_en: "Soy Wax Candle" },
    { id: 99, slug: "gone-item", name_en: null },
  ],
  category_ids: [3],
};
const BXGY: BuyXGetYFormRule = {
  key: 1,
  rule_id: 2,
  kind: "buy_x_get_y",
  buy_quantity: "2",
  get_quantity: "1",
  get_percent: "100",
  product_ids: [],
  products: [],
  category_ids: [5],
};
const SPEND: SpendDiscountFormRule = {
  key: 2,
  rule_id: 3,
  kind: "spend_discount",
  discount_mode: "amount",
  tiers: [
    { threshold: "150.00", discount: "15.00" },
    { threshold: "300.00", discount: "40.00" },
  ],
};
const FREE: FreeShippingFormRule = { key: 3, rule_id: 4, kind: "free_shipping", threshold: "120.00" };

function formWith(rules: PromotionFormRule[]): PromotionForm {
  return { ...newPromotionForm(), name: "Sale", rules };
}

const FOUR = formWith([ITEM, BXGY, SPEND, FREE]);

interface RenderOptions {
  readOnly?: boolean;
  lists?: ScopeLists;
  invalid?: readonly string[];
  queries?: Readonly<Record<number, string>>;
}

function renderRules(form: PromotionForm, language: Language, options: RenderOptions = {}): string {
  const { readOnly = false, lists = OK_LISTS, invalid = [], queries = {} } = options;
  return renderToStaticMarkup(
    <LanguageProvider storage={storage(language)}>
      <AdminPromotionRulesView
        form={form}
        readOnly={readOnly}
        lists={lists}
        invalid={(field) => invalid.includes(field)}
        onRules={() => undefined}
        queries={queries}
        onQuery={() => undefined}
      />
    </LanguageProvider>,
  );
}

function rulesOf(html: string): string[] {
  return elements(html, "li", "site-admin-promo-rules__rule");
}

function itemRule(id: number, key = 0): ItemDiscountFormRule {
  return { ...(newPromotionRule("item_discount", key) as ItemDiscountFormRule), category_ids: [id] };
}

describe("AdminPromotionRules kinds and fields", () => {
  // UX 0.20 A12 线框「1 [admin.rule_item_discount] (↑)(↓) ([admin.rule_remove])」「[admin.rule_scope]: [admin.rule_categories] ☐<分类> ☐<分类> …」
  // 「[admin.rule_products] [搜索____] <已选商品> ✕ …」「[admin.rule_scope_hint]」「( ) [admin.rule_percent] [__] %   ( ) [admin.rule_amount] [M1 ____]」
  // 「[admin.rule_item_hint]」「2 [admin.rule_bxgy] …」「[admin.rule_buy] [__]  [admin.rule_get] [__]  [admin.rule_get_percent] [___] %」「[admin.rule_xy_hint]」
  // 「[admin.rule_get_percent_hint]」「3 [admin.rule_tiers] …」「( ) [admin.rule_tier_amount] ( ) [admin.rule_tier_percent]」
  // 「[admin.rule_tier_threshold] [M1 ____]  [admin.rule_tier_discount] [M1 ____] 或 [__] % ([admin.rule_remove_tier])」「( [admin.rule_add_tier] )」
  // 「[admin.rule_tiers_hint]」「4 [admin.rule_free_shipping] …」「[admin.rule_threshold] [M1 ____]  [admin.rule_threshold_hint]」：四类规则各一块，依次为编号、
  // 类别名、(↑)(↓) 与 admin.rule_remove，再下为该类别的字段与提示；金额前为 RM、百分比后为 %。
  it.each(LANGUAGES)("shows the four kinds with their fields and hints in %s", (language) => {
    const t = (key: CopyKey) => COPY[key][language];
    const html = renderRules(FOUR, language);
    const rules = rulesOf(html);
    expect(rules).toHaveLength(4);
    const tools = ["↑", "↓", t("admin.rule_remove")];
    const scope = [t("admin.rule_scope"), t("admin.rule_categories"), ...CATEGORY_NAMES[language], t("admin.rule_products")];
    const soy = language === "ms" ? "Lilin Soya" : "Soy Wax Candle";
    expect(textNodes(rules[0] ?? "")).toEqual([
      "1",
      t("admin.rule_item_discount"),
      ...tools,
      ...scope,
      soy,
      "✕",
      "gone-item",
      "✕",
      t("admin.rule_scope_hint"),
      t("admin.rule_percent"),
      "%",
      t("admin.rule_amount"),
      "RM",
      t("admin.rule_item_hint"),
    ]);
    expect(textNodes(rules[1] ?? "")).toEqual([
      "2",
      t("admin.rule_bxgy"),
      ...tools,
      ...scope,
      t("admin.rule_scope_hint"),
      t("admin.rule_buy"),
      t("admin.rule_get"),
      t("admin.rule_get_percent"),
      "%",
      t("admin.rule_xy_hint"),
      t("admin.rule_get_percent_hint"),
    ]);
    const tier = [t("admin.rule_tier_threshold"), "RM", t("admin.rule_tier_discount"), "RM", t("admin.rule_remove_tier")];
    expect(textNodes(rules[2] ?? "")).toEqual([
      "3",
      t("admin.rule_tiers"),
      ...tools,
      t("admin.rule_tier_amount"),
      t("admin.rule_tier_percent"),
      ...tier,
      ...tier,
      t("admin.rule_add_tier"),
      t("admin.rule_tiers_hint"),
    ]);
    expect(textNodes(rules[3] ?? "")).toEqual(["4", t("admin.rule_free_shipping"), ...tools, t("admin.rule_threshold"), "RM", t("admin.rule_threshold_hint")]);

    // 各字段的值（读回的整数文字与 RM 文字）、单选与勾选。
    expect(input(html, "rules.1.discount_mode", "percent").has("checked")).toBe(true);
    expect(input(html, "rules.1.discount_mode", "amount").has("checked")).toBe(false);
    expect(input(html, "rules.1.percent").get("value")).toBe("20");
    expect(input(html, "rules.1.percent").get("aria-label")).toBe(t("admin.rule_percent"));
    expect(input(html, "rules.1.amount_sen").get("value")).toBe("");
    expect(input(html, "rules.1.amount_sen").has("disabled")).toBe(true);
    expect(input(html, "rules.1.amount_sen").get("aria-label")).toBe(t("admin.rule_amount"));
    expect(["3", "4", "5"].map((id) => input(html, "rules.1.category_ids", id).has("checked"))).toEqual([true, false, false]);
    expect(["3", "4", "5"].map((id) => input(html, "rules.2.category_ids", id).has("checked"))).toEqual([false, false, true]);
    expect(["buy_quantity", "get_quantity", "get_percent"].map((name) => input(html, `rules.2.${name}`).get("value"))).toEqual(["2", "1", "100"]);
    expect(input(html, "rules.3.discount_mode", "amount").has("checked")).toBe(true);
    expect(input(html, "rules.3.tiers.1.threshold_sen").get("value")).toBe("150.00");
    expect(input(html, "rules.3.tiers.1.amount_sen").get("value")).toBe("15.00");
    expect(input(html, "rules.3.tiers.2.threshold_sen").get("value")).toBe("300.00");
    expect(input(html, "rules.3.tiers.2.amount_sen").get("value")).toBe("40.00");
    expect(input(html, "rules.4.threshold_sen").get("value")).toBe("120.00");
    expect(input(html, "rules.4.threshold_sen").get("inputmode")).toBe("decimal");
    expect(input(html, "rules.2.buy_quantity").get("inputmode")).toBe("numeric");
  });

  // UX 0.20 A12 线框「( ) [admin.rule_percent] [__] %   ( ) [admin.rule_amount] [M1 ____]」「[admin.rule_tier_discount] [M1 ____] 或 [__] %」与金额元素
  // M1「每件直降额、满额门槛与满额减额、免运费门槛（RM 输入，提交整仙）；百分比为整数」：方式由 SHOP-TASK-282 的 switchDiscountMode 切换；每件直降换成金额后
  // 金额输入框可填、百分比输入框禁用；满额换成满额折后每档优惠为百分比（%），门槛仍为 RM。
  it.each(LANGUAGES)("follows the discount mode in %s", (language) => {
    const html = renderRules(formWith([switchDiscountMode(ITEM, "amount"), switchDiscountMode(SPEND, "percent")]), language);
    expect(input(html, "rules.1.discount_mode", "amount").has("checked")).toBe(true);
    expect(input(html, "rules.1.amount_sen").has("disabled")).toBe(false);
    expect(input(html, "rules.1.amount_sen").get("value")).toBe("");
    expect(input(html, "rules.1.percent").has("disabled")).toBe(true);
    expect(input(html, "rules.2.discount_mode", "percent").has("checked")).toBe(true);
    expect(input(html, "rules.2.tiers.1.threshold_sen").get("value")).toBe("150.00");
    expect(input(html, "rules.2.tiers.1.percent").get("value")).toBe("");
    expect(hasInput(html, "rules.2.tiers.1.amount_sen")).toBe(false);
    const spend = rulesOf(html)[1] ?? "";
    expect(textNodes(spend).filter((text) => text === "%" || text === "RM")).toEqual(["RM", "%", "RM", "%"]);
  });
});

describe("AdminPromotionRules order, removal and adding", () => {
  // UX 0.20 A12 说明「(↑)(↓) 调整规则排序（读屏标签 [admin.rule_move_up]、[admin.rule_move_down]）」与线框「(↑)(↓) ([admin.rule_remove])」：每条规则有
  // 上移、下移与移除；第一条不能上移、最后一条不能下移；上下移交换相邻两条，移除只去掉这一条。
  it.each(LANGUAGES)("moves and removes rules in %s", (language) => {
    const html = renderRules(FOUR, language);
    const up = buttonsLabeled(html, COPY["admin.rule_move_up"][language]);
    const down = buttonsLabeled(html, COPY["admin.rule_move_down"][language]);
    expect(up.map((attrs) => attrs.has("disabled"))).toEqual([true, false, false, false]);
    expect(down.map((attrs) => attrs.has("disabled"))).toEqual([false, false, false, true]);
    expect(buttonsWithText(html, COPY["admin.rule_remove"][language]).map((attrs) => attrs.has("disabled"))).toEqual([false, false, false, false]);
    const keys = (rules: PromotionFormRule[]) => rules.map((rule) => rule.key);
    expect(moveRule(FOUR.rules, 0, -1)).toBe(FOUR.rules);
    expect(moveRule(FOUR.rules, 3, 1)).toBe(FOUR.rules);
    expect(keys(moveRule(FOUR.rules, 1, -1))).toEqual([1, 0, 2, 3]);
    expect(keys(moveRule(FOUR.rules, 1, 1))).toEqual([0, 2, 1, 3]);
    expect(keys(removeRule(FOUR.rules, 2))).toEqual([0, 1, 3]);
    expect(removeRule(FOUR.rules, 9)).toBe(FOUR.rules);
    // 移动之后编号随新的排序。
    const moved = renderRules(formWith(moveRule(FOUR.rules, 3, -1)), language);
    expect(rulesOf(moved).map((rule) => textNodes(rule).slice(0, 2))).toEqual([
      ["1", COPY["admin.rule_item_discount"][language]],
      ["2", COPY["admin.rule_bxgy"][language]],
      ["3", COPY["admin.rule_free_shipping"][language]],
      ["4", COPY["admin.rule_tiers"][language]],
    ]);
  });

  // UX 0.20 A12 线框「( [admin.rule_add] ▾ [admin.rule_item_discount] / [admin.rule_bxgy] / [admin.rule_tiers] / [admin.rule_free_shipping] )」与说明「最多 20 条
  // 商品级规则、一条满额减或满额折、一条免运费；已有满额减或免运费时 [admin.rule_add] 里的对应项禁用」：选择的第一项为 admin.rule_add，其后四个类别；已有
  // 满额与免运费时这两项禁用，已有 20 条商品级规则时两项商品级禁用；新加的规则排在最后，初值与 key 用 newPromotionRule 与 nextRuleKey。
  it.each(LANGUAGES)("disables the add options by count in %s", (language) => {
    const options = (form: PromotionForm) => {
      const select = only(elements(renderRules(form, language), "select", "acs-admin__select"));
      expect(attributes(tags(select, "select")[0] ?? "").get("aria-label")).toBe(COPY["admin.rule_add"][language]);
      expect(textNodes(select)).toEqual(
        (["admin.rule_add", "admin.rule_item_discount", "admin.rule_bxgy", "admin.rule_tiers", "admin.rule_free_shipping"] as const).map((key) => COPY[key][language]),
      );
      return tags(select, "option")
        .slice(1)
        .map((tag) => attributes(tag).has("disabled"));
    };
    const empty = formWith([]);
    expect(options(empty)).toEqual([false, false, false, false]);
    expect(options(FOUR)).toEqual([false, false, true, true]);
    const twenty = formWith(Array.from({ length: 20 }, (_, index) => (index % 2 === 0 ? itemRule(3, index) : { ...BXGY, key: index })));
    expect(options(twenty)).toEqual([true, true, false, false]);
    expect(options(formWith(twenty.rules.slice(1)))).toEqual([false, false, false, false]);
    for (const kind of ["item_discount", "buy_x_get_y"] as const) {
      expect(addOptionDisabled(twenty.rules, kind)).toBe(true);
      expect(addRule(twenty, kind)).toBe(twenty.rules);
    }
    expect(addRule(FOUR, "spend_discount")).toBe(FOUR.rules);
    expect(addRule(FOUR, "free_shipping")).toBe(FOUR.rules);
    expect(addRule(FOUR, "buy_x_get_y")).toEqual([...FOUR.rules, newPromotionRule("buy_x_get_y", 4)]);
    expect(addRule(empty, "spend_discount")).toEqual([newPromotionRule("spend_discount", 0)]);
  });

  // UX 0.20 A12 线框「( [admin.rule_add_tier] )」「([admin.rule_remove_tier])」与 admin.rule_tiers_hint「最多 5 档」：已有 5 档时 admin.rule_add_tier 禁用，
  // 只剩一档时 admin.rule_remove_tier 禁用；加的一档门槛与优惠都空。
  it.each(LANGUAGES)("disables adding and removing tiers at the limits in %s", (language) => {
    const one = newPromotionRule("spend_discount", 0) as SpendDiscountFormRule;
    const five: SpendDiscountFormRule = { ...one, tiers: Array.from({ length: 5 }, (_, index) => ({ threshold: `${String(index + 1)}00.00`, discount: "1.00" })) };
    const addTierText = COPY["admin.rule_add_tier"][language];
    const removeTierText = COPY["admin.rule_remove_tier"][language];
    let html = renderRules(formWith([one]), language);
    expect(buttonsWithText(html, removeTierText).map((attrs) => attrs.has("disabled"))).toEqual([true]);
    expect(buttonsWithText(html, addTierText).map((attrs) => attrs.has("disabled"))).toEqual([false]);
    html = renderRules(formWith([five]), language);
    expect(buttonsWithText(html, removeTierText).map((attrs) => attrs.has("disabled"))).toEqual([false, false, false, false, false]);
    expect(buttonsWithText(html, addTierText).map((attrs) => attrs.has("disabled"))).toEqual([true]);
    expect(addTier(five)).toBe(five);
    expect(addTier(one).tiers).toEqual([
      { threshold: "", discount: "" },
      { threshold: "", discount: "" },
    ]);
    expect(removeTier(one, 0)).toBe(one);
    expect(removeTier(five, 1).tiers.map((tier) => tier.threshold)).toEqual(["100.00", "300.00", "400.00", "500.00"]);
  });
});

describe("AdminPromotionRules scope", () => {
  // UX 0.20 A12 说明「商品范围的搜索只列已上架商品；分类为勾选」与线框「[admin.rule_products] [搜索____] <已选商品> ✕ …」：搜索词去掉首尾空白、不分大小写，
  // 为空时不列；只列已上架、名称（任一语言）或 slug 含搜索词且尚未选的商品，至多 20 条；结果为可点选的按钮（名称按当前语言，缺译用英文，再没有用 slug）。
  it.each(LANGUAGES)("lists only active, unselected products and at most 20 in %s", (language) => {
    const rule = { ...itemRule(3), category_ids: [] };
    const ids = (found: AdminProductListItem[]) => found.map((product) => product.id);
    expect(ids(searchProducts(PRODUCTS, rule, "  TEE "))).toEqual([11, 14]);
    expect(ids(searchProducts(PRODUCTS, rule, "圆领"))).toEqual([11]);
    expect(ids(searchProducts(PRODUCTS, rule, "soya"))).toEqual([12]);
    expect(ids(searchProducts(PRODUCTS, rule, "SOY-CANDLE"))).toEqual([12]);
    expect(ids(searchProducts(PRODUCTS, rule, "mug"))).toEqual([]);
    expect(ids(searchProducts(PRODUCTS, rule, "   "))).toEqual([]);
    expect(ids(searchProducts(PRODUCTS, { ...rule, product_ids: [11] }, "tee"))).toEqual([14]);
    const many = Array.from({ length: 25 }, (_, index) => item(100 + index, { name_en: `Lantern ${String(index)}` }));
    expect(ids(searchProducts(many, rule, "lantern"))).toEqual(Array.from({ length: 20 }, (_, index) => 100 + index));

    const html = renderRules(formWith([rule]), language, { queries: { 0: "tee" } });
    const results = only(elements(html, "ul", "site-admin-promo-rules__results"));
    expect(textNodes(results)).toEqual([language === "zh" ? "圆领T恤" : "Crew Tee", "tee-bag"]);
    expect(input(html, "rules.1.product_search").get("type")).toBe("search");
    expect(input(html, "rules.1.product_search").get("aria-label")).toBe(COPY["admin.rule_products"][language]);
    expect(input(html, "rules.1.product_search").get("value")).toBe("tee");
    expect(elements(renderRules(formWith([rule]), language), "ul", "site-admin-promo-rules__results")).toEqual([]);
    // 点选即加入（同步显示用的 products）；移除去掉这一件。
    const added = addProduct(rule, PRODUCTS[0] ?? item(11));
    expect(added.product_ids).toEqual([11]);
    expect(added.products).toEqual([{ id: 11, slug: "crew-tee", name_en: "Crew Tee" }]);
    expect(addProduct(added, PRODUCTS[0] ?? item(11))).toBe(added);
    expect(removeProduct(added, 11)).toEqual({ ...added, product_ids: [], products: [] });
  });

  // UX 0.20 A12 说明「提交前页面先按 … 逐项检查（…范围至多 200 件商品与 30 个分类…）」与 admin.rule_scope_hint「最多 200 件商品与 30 个分类」：已选 200 件时
  // 搜索框禁用、不再列出也不再加入；已勾 30 个分类时其余勾选框禁用（已勾的仍可取消）。
  it.each(LANGUAGES)("stops at 200 products and 30 categories in %s", (language) => {
    const chosen = Array.from({ length: 200 }, (_, index) => index + 1000);
    const full: ItemDiscountFormRule = { ...itemRule(3), product_ids: chosen, products: chosen.map((id) => ({ id, slug: `p-${String(id)}`, name_en: null })) };
    let html = renderRules(formWith([full]), language, { queries: { 0: "tee" } });
    expect(input(html, "rules.1.product_search").has("disabled")).toBe(true);
    expect(elements(html, "ul", "site-admin-promo-rules__results")).toEqual([]);
    expect(addProduct(full, PRODUCTS[0] ?? item(11))).toBe(full);
    const fewer = removeProduct(full, 1000);
    expect(input(renderRules(formWith([fewer]), language), "rules.1.product_search").has("disabled")).toBe(false);

    const categories = Array.from({ length: 31 }, (_, index) => category(index + 1, `c-${String(index + 1)}`));
    const thirty: ItemDiscountFormRule = { ...itemRule(1), category_ids: categories.slice(0, 30).map((entry) => entry.id) };
    html = renderRules(formWith([thirty]), language, { lists: { status: "ok", categories, products: PRODUCTS } });
    expect(input(html, "rules.1.category_ids", "31").has("disabled")).toBe(true);
    expect(input(html, "rules.1.category_ids", "1").has("disabled")).toBe(false);
    expect(toggleCategory(thirty, 31)).toBe(thirty);
    const unticked = toggleCategory(thirty, 1);
    expect(unticked.category_ids).toHaveLength(29);
    expect(input(renderRules(formWith([unticked]), language, { lists: { status: "ok", categories, products: PRODUCTS } }), "rules.1.category_ids", "31").has("disabled")).toBe(
      false,
    );
    expect(toggleCategory(unticked, 31).category_ids).toContain(31);
  });

  // UX 0.20 A12 线框「[admin.rule_categories] ☐<分类> ☐<分类> …」「<已选商品> ✕ …」（名称的回退照落实写法（4））：分类名称按当前语言，缺译用英文，再没有用 slug；
  // 已选商品取商品列表里的当前语言名称、缺译用英文、再没有用 slug，列表里没有时用读取结果 products 的 name_en，再没有用 slug；✕ 的读屏标签为 admin.rule_remove。
  it.each(LANGUAGES)("falls back for category and product names in %s", (language) => {
    expect(CATEGORIES.map((entry) => categoryName(entry, language))).toEqual(CATEGORY_NAMES[language]);
    const rule: ItemFormRule = {
      ...itemRule(3),
      product_ids: [11, 14, 12, 98, 99],
      products: [
        { id: 12, slug: "soy-candle", name_en: "Old candle name" },
        { id: 98, slug: "kept-name", name_en: "Kept Name" },
        { id: 99, slug: "gone-item", name_en: null },
      ],
    };
    const expected = [language === "zh" ? "圆领T恤" : "Crew Tee", "tee-bag", language === "ms" ? "Lilin Soya" : "Soy Wax Candle", "Kept Name", "gone-item"];
    expect(rule.product_ids.map((id) => productName(id, rule, PRODUCTS, language))).toEqual(expected);
    expect(rule.product_ids.map((id) => productName(id, rule, null, language))).toEqual([String(11), String(14), "Old candle name", "Kept Name", "gone-item"]);
    const html = renderRules(formWith([rule]), language);
    const chips = elements(html, "li", "site-admin-promo-rules__chip");
    expect(chips.map((chip) => textNodes(chip))).toEqual(expected.map((name) => [name, "✕"]));
    expect(buttonsLabeled(html, COPY["admin.rule_remove"][language])).toHaveLength(5);
  });

  // UX 0.20 A12 说明「商品范围的搜索只列已上架商品；分类为勾选」（列表读取失败照落实写法（4））：任一列表为 none（401）回到 A01；网络中断时范围区只显示
  // common.network_check、其他失败只显示 common.error_retry（没有分类勾选与搜索框），已选商品照常显示与移除；读取中不列分类与搜索框；离开之后不再更新。
  it.each(LANGUAGES)("handles failed list reads in %s", async (language) => {
    const ok = { kind: "ok", categories: CATEGORIES, csrf_token: "c" } as const satisfies CategoriesRead;
    const okProducts = { kind: "ok", products: PRODUCTS, csrf_token: "p" } as const satisfies ProductsRead;
    expect(scopeListsStep(ok, okProducts)).toEqual(OK_LISTS);
    expect(scopeListsStep({ kind: "none" }, okProducts)).toBe("login");
    expect(scopeListsStep({ kind: "network" }, { kind: "none" })).toBe("login");
    expect(scopeListsStep(ok, { kind: "network" })).toEqual({ status: "error", error: "common.network_check" });
    expect(scopeListsStep({ kind: "failed" }, { kind: "network" })).toEqual({ status: "error", error: "common.network_check" });
    expect(scopeListsStep({ kind: "failed" }, okProducts)).toEqual({ status: "error", error: "common.error_retry" });
    expect(scopeListsStep(ok, { kind: "failed" })).toEqual({ status: "error", error: "common.error_retry" });

    const cases: [CategoriesRead, ProductsRead, ScopeLists | null, string[]][] = [
      [ok, okProducts, OK_LISTS, []],
      [ok, { kind: "none" }, null, ["/admin/login"]],
      [{ kind: "network" }, okProducts, { status: "error", error: "common.network_check" }, []],
      [ok, { kind: "failed" }, { status: "error", error: "common.error_retry" }, []],
    ];
    for (const [categories, products, shown, replaced] of cases) {
      const api: ScopeApi = { readCategories: vi.fn(() => Promise.resolve(categories)), readProducts: vi.fn(() => Promise.resolve(products)) };
      const record: { shown: ScopeLists | null; replaced: string[] } = { shown: null, replaced: [] };
      const signal = new AbortController().signal;
      const moves = {
        show: (lists: ScopeLists) => {
          record.shown = lists;
        },
        replace: (path: string) => {
          record.replaced.push(path);
        },
      };
      await loadScopeLists(signal, moves, api);
      expect(api.readCategories).toHaveBeenCalledTimes(1);
      expect(api.readCategories).toHaveBeenCalledWith(signal);
      expect(api.readProducts).toHaveBeenCalledWith(signal);
      expect(record).toEqual({ shown, replaced });
    }
    const controller = new AbortController();
    const shown = vi.fn();
    const replace = vi.fn();
    await loadScopeLists(
      controller.signal,
      { show: shown, replace },
      {
        readCategories: () => {
          controller.abort();
          return Promise.resolve({ kind: "none" });
        },
        readProducts: () => Promise.resolve(okProducts),
      },
    );
    expect(shown).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();

    for (const error of ["common.network_check", "common.error_retry"] as const) {
      const html = renderRules(formWith([ITEM]), language, { lists: { status: "error", error } });
      const scope = only(elements(html, "div", "site-admin-promo-rules__scope-body"));
      expect(textNodes(scope)).toEqual([COPY["admin.rule_scope"][language], COPY[error][language], COPY["admin.rule_products"][language], "Soy Wax Candle", "✕", "gone-item", "✕"]);
      expect(scope).toContain('role="alert"');
      expect(hasInput(scope, "rules.1.category_ids")).toBe(false);
      expect(hasInput(scope, "rules.1.product_search")).toBe(false);
      expect(buttonsLabeled(scope, COPY["admin.rule_remove"][language])).toHaveLength(2);
    }
    const loading = only(elements(renderRules(formWith([ITEM]), language, { lists: SCOPE_LOADING }), "div", "site-admin-promo-rules__scope-body"));
    expect(attributes(tags(loading, "div")[0] ?? "").get("aria-busy")).toBe("true");
    expect(hasInput(loading, "rules.1.category_ids")).toBe(false);
    expect(hasInput(loading, "rules.1.product_search")).toBe(false);
  });
});

describe("AdminPromotionRules read-only and field check", () => {
  // UX 0.20 A12「状态与可用操作」「进行中 | 内部名称、文案、装饰、海报、AI（开始与结束时间、规则与同用开关只读…）」「已结束 | 无（全部只读…）」与
  // 「[admin.promo_rules_locked]（已开始时显示；时间、规则与同用开关只读）」：只读时不显示添加、排序、移除、档位增删与搜索，文字输入框只读，单选与勾选框禁用；
  // 各条规则、范围与已选商品照常显示。
  it.each(LANGUAGES)("shows the rules read-only in %s", (language) => {
    const html = renderRules(FOUR, language, { readOnly: true, queries: { 0: "tee" } });
    expect(tags(html, "button")).toEqual([]);
    expect(html).not.toMatch(/<select\b/);
    expect(hasInput(html, "rules.1.product_search")).toBe(false);
    expect(rulesOf(html)).toHaveLength(4);
    for (const tag of tags(html, "input")) {
      const attrs = attributes(tag);
      const type = attrs.get("type");
      expect(type === "radio" || type === "checkbox" ? attrs.has("disabled") : attrs.has("readonly"), attrs.get("name")).toBe(true);
    }
    expect(textNodes(only(elements(html, "ul", "site-admin-promo-rules__chosen")))).toEqual([language === "ms" ? "Lilin Soya" : "Soy Wax Candle", "gone-item"]);
    expect(textNodes(rulesOf(html)[3] ?? "")).toEqual(["4", COPY["admin.rule_free_shipping"][language], COPY["admin.rule_threshold"][language], "RM", COPY["admin.rule_threshold_hint"][language]]);
  });

  // UX 0.20 A12 说明「提交前页面先按 DESIGN 1.23 第 1–3、12 条的取值范围逐项检查（…百分比与金额范围、X 与 Y、档位逐档递增、范围至多 200 件商品与 30 个分类…），
  // 不合格的字段旁显示 [admin.promo_field_invalid] 并不提交」：用 SHOP-TASK-282 的 formProblems 查出的字段（百分比超范围、享的折扣为 0、第二档优惠不高于第一档、
  // 门槛不是 RM 文字、范围为空），各自旁边提示一次，合格的字段不提示。
  it.each(LANGUAGES)("marks the invalid rule fields in %s", (language) => {
    const form = formWith([
      { ...ITEM, discount: "95" },
      { ...BXGY, get_percent: "0", category_ids: [] },
      { ...SPEND, tiers: [SPEND.tiers[0] ?? { threshold: "", discount: "" }, { threshold: "300.00", discount: "10.00" }] },
      { ...FREE, threshold: "abc" },
    ]);
    const fields = formProblems(form).map((problem) => problem.field);
    expect(fields).toEqual(["rules.1.percent", "rules.2.get_percent", "rules.2.product_ids", "rules.3.tiers.2.amount_sen", "rules.4.threshold_sen"]);
    const html = renderRules(form, language, { invalid: fields });
    for (const name of ["rules.1.percent", "rules.2.get_percent", "rules.3.tiers.2.amount_sen", "rules.4.threshold_sen"]) {
      expect(describedInvalid(html, input(html, name), language), name).toBe(true);
      expect(input(html, name).get("aria-invalid")).toBe("true");
    }
    for (const name of ["rules.1.amount_sen", "rules.2.buy_quantity", "rules.3.tiers.1.amount_sen", "rules.3.tiers.2.threshold_sen"]) {
      expect(describedInvalid(html, input(html, name), language), name).toBe(false);
    }
    const scopes = elements(html, "div", "site-admin-promo-rules__scope-body").map((scope) => attributes(tags(scope, "div")[0] ?? ""));
    expect(scopes.map((attrs) => describedInvalid(html, attrs, language))).toEqual([false, true]);
    expect(countOf(html, escapeHtml(COPY["admin.promo_field_invalid"][language]))).toBe(5);
  });
});
