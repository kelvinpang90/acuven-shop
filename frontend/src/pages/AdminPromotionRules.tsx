import { useId, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";

import { readAdminCategories } from "../api/adminCategories";
import type { AdminCategory, CategoriesRead } from "../api/adminCategories";
import { readAdminProducts } from "../api/adminProducts";
import type { AdminProductListItem, ProductsRead } from "../api/adminProducts";
import { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH } from "../router";
import type { RoutePath } from "../router";
import {
  ITEM_RULES_MAX,
  newPromotionRule,
  nextRuleKey,
  pyStrip,
  RULE_KINDS,
  SCOPE_CATEGORIES_MAX,
  SCOPE_PRODUCTS_MAX,
  SPEND_TIERS_MAX,
  switchDiscountMode,
} from "./promotionForm";
import type {
  BuyXGetYFormRule,
  DiscountMode,
  FreeShippingFormRule,
  ItemDiscountFormRule,
  ItemFormRule,
  PromotionForm,
  PromotionFormRule,
  PromotionFormTier,
  RuleKind,
  SpendDiscountFormRule,
} from "./promotionForm";

// 后台促销活动 A12 编辑区的规则区（docs/UX.md 0.20 A12 线框「[admin.promo_rules]」一段、说明的「规则」一条与金额元素 M1，视觉稿 A12-editor-desktop、
// A12-phone；HANDOFF 0.98 落实写法（3）至（5），SHOP-TASK-271）：每条规则一块（编号、类别名、(↑)(↓) 与 admin.rule_remove，各类别的字段、单选与提示），
// 商品级规则另有范围（分类勾选与商品搜索），末尾为 admin.rule_add 的选择。规则的初值、方式切换、换算与提交前检查用 promotionForm.ts（SHOP-TASK-282）；
// 金额以 RM 文字输入，提交时由 promotionForm.ts 换成整仙。计价、核对与审计在服务端，不碰个人资料。
// - 范围用的分类与商品列表由编辑页在编辑区打开时各读一次（loadScopeLists）：401 回到 A01；网络中断时范围区只显示 common.network_check，
//   其他失败只显示 common.error_retry；已选商品照常显示与移除。
// - 只读（进行中、已结束与 rules_locked 锁定之后）时不显示添加、排序、移除与搜索，输入框只读、单选与勾选框禁用。

// 规则类别的名称，顺序同 RULE_KINDS。
export const RULE_KIND_LABEL: Readonly<Record<RuleKind, CopyKey>> = {
  item_discount: "admin.rule_item_discount",
  buy_x_get_y: "admin.rule_bxgy",
  spend_discount: "admin.rule_tiers",
  free_shipping: "admin.rule_free_shipping",
};

// 商品搜索至多列出的条数。
export const SEARCH_RESULTS_MAX = 20;

// 范围用的两个列表：读取中、都取到，或读取失败（网络中断 common.network_check、其他失败 common.error_retry）。
export type ScopeLists =
  | { status: "loading" }
  | { status: "ok"; categories: AdminCategory[]; products: AdminProductListItem[] }
  | { status: "error"; error: "common.network_check" | "common.error_retry" };

export const SCOPE_LOADING: ScopeLists = { status: "loading" };

// 两个读取的结果：任一为 none（401）回到 A01；任一网络中断为 common.network_check，否则任一失败为 common.error_retry；都取到时为两个列表。
export function scopeListsStep(categories: CategoriesRead, products: ProductsRead): ScopeLists | "login" {
  if (categories.kind === "none" || products.kind === "none") {
    return "login";
  }
  if (categories.kind === "network" || products.kind === "network") {
    return { status: "error", error: "common.network_check" };
  }
  if (categories.kind === "failed" || products.kind === "failed") {
    return { status: "error", error: "common.error_retry" };
  }
  return { status: "ok", categories: categories.categories, products: products.products };
}

// 范围用到的请求，测试以替身代替。
export interface ScopeApi {
  readCategories: typeof readAdminCategories;
  readProducts: typeof readAdminProducts;
}

export const SCOPE_API: ScopeApi = { readCategories: readAdminCategories, readProducts: readAdminProducts };

export interface ScopeMoves {
  show: (lists: ScopeLists) => void;
  replace: (path: RoutePath) => void;
}

// 编辑区打开时各读一次分类与商品列表；signal 中止（离开页面）之后不再更新。
export async function loadScopeLists(signal: AbortSignal, moves: ScopeMoves, api: ScopeApi = SCOPE_API): Promise<void> {
  const [categories, products] = await Promise.all([api.readCategories(signal), api.readProducts(signal)]);
  if (signal.aborted) {
    return;
  }
  const step = scopeListsStep(categories, products);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

const NAME_FIELD = { en: "name_en", zh: "name_zh", ms: "name_ms" } as const satisfies Record<Language, string>;

function filled(value: string | null): value is string {
  return value !== null && value !== "";
}

interface Named {
  slug: string;
  name_en: string | null;
  name_zh: string | null;
  name_ms: string | null;
}

// 当前语言的名称，缺译用英文，再没有用 slug。
function localName(item: Named, language: Language): string {
  const own = item[NAME_FIELD[language]];
  if (filled(own)) {
    return own;
  }
  return filled(item.name_en) ? item.name_en : item.slug;
}

export function categoryName(category: AdminCategory, language: Language): string {
  return localName(category, language);
}

// 已选商品的名称：商品列表里有时取当前语言（缺译用英文，再没有用 slug）；列表里没有（或列表未取到）时用读取结果 products 的 name_en，再没有用 slug；
// 两处都没有时为商品 ID。
export function productName(id: number, rule: ItemFormRule, products: readonly AdminProductListItem[] | null, language: Language): string {
  const listed = products?.find((product) => product.id === id);
  if (listed !== undefined) {
    return localName(listed, language);
  }
  const read = rule.products.find((product) => product.id === id);
  if (read !== undefined) {
    return filled(read.name_en) ? read.name_en : read.slug;
  }
  return String(id);
}

// 商品搜索：搜索词去掉首尾空白、不分大小写，为空时不列；只列已上架（is_active）、名称（任一语言）或 slug 含搜索词且本规则尚未选的商品，按列表顺序至多
// SEARCH_RESULTS_MAX 条。
export function searchProducts(products: readonly AdminProductListItem[], rule: ItemFormRule, query: string): AdminProductListItem[] {
  const needle = pyStrip(query).toLowerCase();
  if (needle === "") {
    return [];
  }
  const chosen = new Set(rule.product_ids);
  const found: AdminProductListItem[] = [];
  for (const product of products) {
    if (!product.is_active || chosen.has(product.id)) {
      continue;
    }
    const texts = [product.name_en, product.name_zh, product.name_ms, product.slug];
    if (texts.some((text) => text !== null && text.toLowerCase().includes(needle))) {
      found.push(product);
      if (found.length === SEARCH_RESULTS_MAX) {
        break;
      }
    }
  }
  return found;
}

export function isItemRule(rule: PromotionFormRule): rule is ItemFormRule {
  return rule.kind === "item_discount" || rule.kind === "buy_x_get_y";
}

// admin.rule_add 里某一类是否禁用：已有满额减或满额折、已有免运费时该项禁用；已有 ITEM_RULES_MAX 条商品级规则时两项商品级禁用。
export function addOptionDisabled(rules: readonly PromotionFormRule[], kind: RuleKind): boolean {
  switch (kind) {
    case "item_discount":
    case "buy_x_get_y":
      return rules.filter(isItemRule).length >= ITEM_RULES_MAX;
    case "spend_discount":
    case "free_shipping":
      return rules.some((rule) => rule.kind === kind);
  }
}

// 按表单当前的值算出新的规则列表；规则不变时返回原数组。
export type RulesChange = (form: PromotionForm) => PromotionFormRule[];

// 添加一条规则：排在最后，初值为 newPromotionRule、key 为 nextRuleKey；该类禁用时不变。
export function addRule(form: PromotionForm, kind: RuleKind): PromotionFormRule[] {
  return addOptionDisabled(form.rules, kind) ? form.rules : [...form.rules, newPromotionRule(kind, nextRuleKey(form))];
}

// 上移（step 为 -1）或下移（1）一位：第一条不能上移、最后一条不能下移。
export function moveRule(rules: PromotionFormRule[], key: number, step: -1 | 1): PromotionFormRule[] {
  const index = rules.findIndex((rule) => rule.key === key);
  const target = index + step;
  if (index < 0 || target < 0 || target >= rules.length) {
    return rules;
  }
  const next = [...rules];
  const [moved] = next.splice(index, 1);
  if (moved === undefined) {
    return rules;
  }
  next.splice(target, 0, moved);
  return next;
}

export function removeRule(rules: PromotionFormRule[], key: number): PromotionFormRule[] {
  return rules.some((rule) => rule.key === key) ? rules.filter((rule) => rule.key !== key) : rules;
}

// 改 key 这一条：change 返回同一对象时整个列表不变。
export function mapRule(rules: PromotionFormRule[], key: number, change: (rule: PromotionFormRule) => PromotionFormRule): PromotionFormRule[] {
  let changed = false;
  const next = rules.map((rule) => {
    if (rule.key !== key) {
      return rule;
    }
    const updated = change(rule);
    changed = changed || updated !== rule;
    return updated;
  });
  return changed ? next : rules;
}

// 满额加一档（门槛与优惠都空）：已有 SPEND_TIERS_MAX 档时不变。
export function addTier(rule: SpendDiscountFormRule): SpendDiscountFormRule {
  return rule.tiers.length >= SPEND_TIERS_MAX ? rule : { ...rule, tiers: [...rule.tiers, { threshold: "", discount: "" }] };
}

// 移除第 index 档：只剩一档时不变。
export function removeTier(rule: SpendDiscountFormRule, index: number): SpendDiscountFormRule {
  return rule.tiers.length <= 1 ? rule : { ...rule, tiers: rule.tiers.filter((_, at) => at !== index) };
}

export function changeTier(rule: SpendDiscountFormRule, index: number, field: keyof PromotionFormTier, value: string): SpendDiscountFormRule {
  return { ...rule, tiers: rule.tiers.map((tier, at) => (at === index ? { ...tier, [field]: value } : tier)) };
}

// 勾选或取消一个分类：已勾 SCOPE_CATEGORIES_MAX 个时不再加。
export function toggleCategory<R extends ItemFormRule>(rule: R, id: number): R {
  if (rule.category_ids.includes(id)) {
    return { ...rule, category_ids: rule.category_ids.filter((item) => item !== id) };
  }
  return rule.category_ids.length >= SCOPE_CATEGORIES_MAX ? rule : { ...rule, category_ids: [...rule.category_ids, id] };
}

// 点选一件商品加入范围（同步显示用的 products）：已选或已选 SCOPE_PRODUCTS_MAX 件时不变。
export function addProduct<R extends ItemFormRule>(rule: R, product: AdminProductListItem): R {
  if (rule.product_ids.includes(product.id) || rule.product_ids.length >= SCOPE_PRODUCTS_MAX) {
    return rule;
  }
  return {
    ...rule,
    product_ids: [...rule.product_ids, product.id],
    products: [...rule.products, { id: product.id, slug: product.slug, name_en: product.name_en }],
  };
}

export function removeProduct<R extends ItemFormRule>(rule: R, id: number): R {
  return { ...rule, product_ids: rule.product_ids.filter((item) => item !== id), products: rule.products.filter((product) => product.id !== id) };
}

export interface RulesProps {
  form: PromotionForm;
  readOnly: boolean;
  lists: ScopeLists;
  // 字段（promotionForm 的问题 field，如 rules.1.percent）是否要在旁边提示 admin.promo_field_invalid。
  invalid: (field: string) => boolean;
  onRules: (change: RulesChange) => void;
}

export interface RulesViewProps extends RulesProps {
  // 各条商品级规则的搜索词（按规则的 key）。
  queries: Readonly<Record<number, string>>;
  onQuery: (key: number, query: string) => void;
}

// 一个字段与其旁的 admin.promo_field_invalid（不合格时），写法同编辑页的字段；输入框以 aria-describedby 指向提示。
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

// 文字输入框（整数文字或 RM 文字），金额前加 RM、百分比后加 %。
function TextInput({
  name,
  label,
  value,
  unit,
  readOnly,
  disabled = false,
  describedBy,
  onText,
}: {
  name: string;
  label?: string;
  value: string;
  unit: "rm" | "percent" | null;
  readOnly: boolean;
  disabled?: boolean;
  describedBy: string | undefined;
  onText: (value: string) => void;
}) {
  return (
    <span className="site-admin-promo-rules__unit">
      {unit === "rm" && <span>RM</span>}
      <input
        className="acs-admin__input"
        name={name}
        aria-label={label}
        inputMode={unit === "rm" ? "decimal" : "numeric"}
        autoComplete="off"
        value={value}
        readOnly={readOnly}
        disabled={disabled}
        aria-invalid={describedBy !== undefined}
        aria-describedby={describedBy}
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          onText(event.target.value);
        }}
      />
      {unit === "percent" && <span>%</span>}
    </span>
  );
}

// 标签在上、输入框在下的一个字段。
function LabeledText({
  name,
  label,
  value,
  unit,
  readOnly,
  invalid,
  onText,
}: {
  name: string;
  label: CopyKey;
  value: string;
  unit: "rm" | "percent" | null;
  readOnly: boolean;
  invalid: boolean;
  onText: (value: string) => void;
}) {
  const t = useCopy();
  return (
    <Field invalid={invalid}>
      {(describedBy) => (
        <label className="acs-admin__field">
          {t(label)}
          <TextInput name={name} value={value} unit={unit} readOnly={readOnly} describedBy={describedBy} onText={onText} />
        </label>
      )}
    </Field>
  );
}

function Radio({ name, value, checked, readOnly, label, onPick }: { name: string; value: string; checked: boolean; readOnly: boolean; label: CopyKey; onPick: () => void }) {
  const t = useCopy();
  return (
    <label className="site-admin-promo-edit__check">
      <input type="radio" name={name} value={value} checked={checked} disabled={readOnly} onChange={onPick} />
      {t(label)}
    </label>
  );
}

interface BlockProps<R extends PromotionFormRule> {
  rule: R;
  prefix: string;
  readOnly: boolean;
  invalid: (field: string) => boolean;
  onRule: (change: (rule: R) => R) => void;
}

// 商品级规则的范围：admin.rule_scope 下为分类勾选（全部分类，已勾 SCOPE_CATEGORIES_MAX 个时其余禁用）与商品（搜索框、搜索结果、已选商品与 ✕），
// 其下为 admin.rule_scope_hint。列表读取失败时以提示代替分类与搜索。
function ScopeBlock({
  rule,
  prefix,
  readOnly,
  invalid,
  onRule,
  lists,
  query,
  onQuery,
}: BlockProps<ItemFormRule> & { lists: ScopeLists; query: string; onQuery: (query: string) => void }) {
  const t = useCopy();
  const { language } = useLanguage();
  const scopeId = useId();
  const categoriesId = useId();
  const productsId = useId();
  const products = lists.status === "ok" ? lists.products : null;
  const categoriesFull = rule.category_ids.length >= SCOPE_CATEGORIES_MAX;
  const productsFull = rule.product_ids.length >= SCOPE_PRODUCTS_MAX;
  const results = products === null || readOnly || productsFull ? [] : searchProducts(products, rule, query);
  return (
    <>
      <Field className="site-admin-promo-rules__scope" invalid={invalid(`${prefix}product_ids`) || invalid(`${prefix}category_ids`)}>
        {(describedBy) => (
          <div className="site-admin-promo-rules__scope-body" role="group" aria-labelledby={scopeId} aria-describedby={describedBy} aria-busy={lists.status === "loading"}>
            <span id={scopeId} className="acs-admin__muted">
              {t("admin.rule_scope")}
            </span>
            {lists.status === "error" ? (
              <AdminAlert error={lists.error} />
            ) : (
              <div className="site-admin-promo-rules__categories" role="group" aria-labelledby={categoriesId}>
                <span id={categoriesId}>{t("admin.rule_categories")}</span>
                {lists.status === "ok" &&
                  lists.categories.map((category) => {
                    const checked = rule.category_ids.includes(category.id);
                    return (
                      <label key={category.id} className="site-admin-promo-edit__check">
                        <input
                          type="checkbox"
                          name={`${prefix}category_ids`}
                          value={String(category.id)}
                          checked={checked}
                          disabled={readOnly || (!checked && categoriesFull)}
                          onChange={() => {
                            onRule((current) => toggleCategory(current, category.id));
                          }}
                        />
                        {categoryName(category, language)}
                      </label>
                    );
                  })}
              </div>
            )}
            <div className="site-admin-promo-rules__products" role="group" aria-labelledby={productsId}>
              <span id={productsId}>{t("admin.rule_products")}</span>
              {!readOnly && products !== null && (
                <input
                  className="acs-admin__input site-admin-promo-rules__search"
                  type="search"
                  name={`${prefix}product_search`}
                  aria-label={t("admin.rule_products")}
                  autoComplete="off"
                  value={query}
                  disabled={productsFull}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => {
                    onQuery(event.target.value);
                  }}
                />
              )}
              {results.length > 0 && (
                <ul className="site-admin-promo-rules__results">
                  {results.map((product) => (
                    <li key={product.id}>
                      <button
                        className="acs-admin__btn acs-admin__btn--secondary"
                        type="button"
                        onClick={() => {
                          onRule((current) => addProduct(current, product));
                        }}
                      >
                        {localName(product, language)}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {rule.product_ids.length > 0 && (
                <ul className="site-admin-promo-rules__chosen">
                  {rule.product_ids.map((id) => (
                    <li key={id} className="site-admin-promo-rules__chip">
                      <span>{productName(id, rule, products, language)}</span>
                      {!readOnly && (
                        <button
                          className="acs-admin__btn acs-admin__btn--secondary"
                          type="button"
                          aria-label={t("admin.rule_remove")}
                          onClick={() => {
                            onRule((current) => removeProduct(current, id));
                          }}
                        >
                          ✕
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </Field>
      <span className="acs-admin__muted">{t("admin.rule_scope_hint")}</span>
    </>
  );
}

// 每件直降：( ) admin.rule_percent [__] %   ( ) admin.rule_amount RM [____]；未选方式的输入框禁用并留空。其下为 admin.rule_item_hint。
function ItemDiscountBlock({ rule, prefix, readOnly, invalid, onRule }: BlockProps<ItemDiscountFormRule>) {
  const t = useCopy();
  const modes = [
    { mode: "percent", label: "admin.rule_percent", field: "percent", unit: "percent" },
    { mode: "amount", label: "admin.rule_amount", field: "amount_sen", unit: "rm" },
  ] as const satisfies readonly { mode: DiscountMode; label: CopyKey; field: string; unit: "rm" | "percent" }[];
  return (
    <>
      <div className="site-admin-promo-rules__modes">
        {modes.map(({ mode, label, field, unit }) => {
          const active = rule.discount_mode === mode;
          return (
            <div key={mode} className="site-admin-promo-rules__mode">
              <Radio
                name={`${prefix}discount_mode`}
                value={mode}
                checked={active}
                readOnly={readOnly}
                label={label}
                onPick={() => {
                  onRule((current) => switchDiscountMode(current, mode));
                }}
              />
              <Field className="site-admin-promo-rules__amount" invalid={active && invalid(`${prefix}${field}`)}>
                {(describedBy) => (
                  <TextInput
                    name={`${prefix}${field}`}
                    label={t(label)}
                    value={active ? rule.discount : ""}
                    unit={unit}
                    readOnly={readOnly}
                    disabled={!active}
                    describedBy={describedBy}
                    onText={(value) => {
                      onRule((current) => ({ ...current, discount: value }));
                    }}
                  />
                )}
              </Field>
            </div>
          );
        })}
      </div>
      <span className="acs-admin__muted">{t("admin.rule_item_hint")}</span>
    </>
  );
}

// 买 X 享 Y：admin.rule_buy、admin.rule_get 与 admin.rule_get_percent（%）三个整数，其下为 admin.rule_xy_hint 与 admin.rule_get_percent_hint。
function BuyXGetYBlock({ rule, prefix, readOnly, invalid, onRule }: BlockProps<BuyXGetYFormRule>) {
  const t = useCopy();
  const fields = [
    { field: "buy_quantity", label: "admin.rule_buy", unit: null },
    { field: "get_quantity", label: "admin.rule_get", unit: null },
    { field: "get_percent", label: "admin.rule_get_percent", unit: "percent" },
  ] as const satisfies readonly { field: keyof BuyXGetYFormRule; label: CopyKey; unit: "percent" | null }[];
  return (
    <>
      <div className="site-admin-promo-rules__row">
        {fields.map(({ field, label, unit }) => (
          <LabeledText
            key={field}
            name={`${prefix}${field}`}
            label={label}
            value={rule[field]}
            unit={unit}
            readOnly={readOnly}
            invalid={invalid(`${prefix}${field}`)}
            onText={(value) => {
              onRule((current) => ({ ...current, [field]: value }));
            }}
          />
        ))}
      </div>
      <span className="acs-admin__muted">{t("admin.rule_xy_hint")}</span>
      <span className="acs-admin__muted">{t("admin.rule_get_percent_hint")}</span>
    </>
  );
}

// 满额减或满额折：( ) admin.rule_tier_amount ( ) admin.rule_tier_percent；每档为 admin.rule_tier_threshold RM [____] 与 admin.rule_tier_discount
// （满额减 RM [____]、满额折 [__] %）及 admin.rule_remove_tier（只剩一档时禁用）；其下为 admin.rule_add_tier（已有 SPEND_TIERS_MAX 档时禁用）与 admin.rule_tiers_hint。
function SpendDiscountBlock({ rule, prefix, readOnly, invalid, onRule }: BlockProps<SpendDiscountFormRule>) {
  const t = useCopy();
  const modes = [
    { mode: "amount", label: "admin.rule_tier_amount" },
    { mode: "percent", label: "admin.rule_tier_percent" },
  ] as const satisfies readonly { mode: DiscountMode; label: CopyKey }[];
  const discountField = rule.discount_mode === "percent" ? "percent" : "amount_sen";
  const discountUnit = rule.discount_mode === "percent" ? "percent" : "rm";
  return (
    <>
      <div className="site-admin-promo-rules__modes">
        {modes.map(({ mode, label }) => (
          <Radio
            key={mode}
            name={`${prefix}discount_mode`}
            value={mode}
            checked={rule.discount_mode === mode}
            readOnly={readOnly}
            label={label}
            onPick={() => {
              onRule((current) => switchDiscountMode(current, mode));
            }}
          />
        ))}
      </div>
      <Field className="site-admin-promo-rules__tiers" invalid={invalid(`${prefix}tiers`)}>
        {(describedBy) => (
          <div className="site-admin-promo-rules__tier-list" role="group" aria-label={t("admin.rule_tiers")} aria-describedby={describedBy}>
            {rule.tiers.map((tier, index) => {
              const tierPrefix = `${prefix}tiers.${String(index + 1)}.`;
              return (
                <div key={index} className="site-admin-promo-rules__tier">
                  <LabeledText
                    name={`${tierPrefix}threshold_sen`}
                    label="admin.rule_tier_threshold"
                    value={tier.threshold}
                    unit="rm"
                    readOnly={readOnly}
                    invalid={invalid(`${tierPrefix}threshold_sen`)}
                    onText={(value) => {
                      onRule((current) => changeTier(current, index, "threshold", value));
                    }}
                  />
                  <LabeledText
                    name={`${tierPrefix}${discountField}`}
                    label="admin.rule_tier_discount"
                    value={tier.discount}
                    unit={discountUnit}
                    readOnly={readOnly}
                    invalid={invalid(`${tierPrefix}${discountField}`)}
                    onText={(value) => {
                      onRule((current) => changeTier(current, index, "discount", value));
                    }}
                  />
                  {!readOnly && (
                    <button
                      className="acs-admin__btn acs-admin__btn--secondary"
                      type="button"
                      disabled={rule.tiers.length <= 1}
                      onClick={() => {
                        onRule((current) => removeTier(current, index));
                      }}
                    >
                      {t("admin.rule_remove_tier")}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Field>
      {!readOnly && (
        <button
          className="acs-admin__btn acs-admin__btn--secondary site-admin-promo-rules__add-tier"
          type="button"
          disabled={rule.tiers.length >= SPEND_TIERS_MAX}
          onClick={() => {
            onRule(addTier);
          }}
        >
          {t("admin.rule_add_tier")}
        </button>
      )}
      <span className="acs-admin__muted">{t("admin.rule_tiers_hint")}</span>
    </>
  );
}

// 满额免运费：admin.rule_threshold RM [____]，其下为 admin.rule_threshold_hint。
function FreeShippingBlock({ rule, prefix, readOnly, invalid, onRule }: BlockProps<FreeShippingFormRule>) {
  const t = useCopy();
  return (
    <>
      <div className="site-admin-promo-rules__row">
        <LabeledText
          name={`${prefix}threshold_sen`}
          label="admin.rule_threshold"
          value={rule.threshold}
          unit="rm"
          readOnly={readOnly}
          invalid={invalid(`${prefix}threshold_sen`)}
          onText={(value) => {
            onRule((current) => ({ ...current, threshold: value }));
          }}
        />
      </div>
      <span className="acs-admin__muted">{t("admin.rule_threshold_hint")}</span>
    </>
  );
}

// 只改同一类的规则：类别不同（不会发生）时不变。
function sameKind<R extends PromotionFormRule>(kind: R["kind"], change: (rule: R) => R): (rule: PromotionFormRule) => PromotionFormRule {
  return (rule) => (rule.kind === kind ? change(rule as R) : rule);
}

// 一条规则：编号、类别名与 (↑)(↓) ([admin.rule_remove])（只读时不显示），其下为范围（商品级规则）与该类别的字段。
function RuleBlock({
  rule,
  index,
  count,
  readOnly,
  lists,
  invalid,
  query,
  onRules,
  onQuery,
}: {
  rule: PromotionFormRule;
  index: number;
  count: number;
  readOnly: boolean;
  lists: ScopeLists;
  invalid: (field: string) => boolean;
  query: string;
  onRules: (change: RulesChange) => void;
  onQuery: (key: number, query: string) => void;
}) {
  const t = useCopy();
  const prefix = `rules.${String(index + 1)}.`;
  const { key } = rule;
  const onRule = <R extends PromotionFormRule>(kind: R["kind"]) => (change: (current: R) => R) => {
    onRules((form) => mapRule(form.rules, key, sameKind(kind, change)));
  };
  const common = { prefix, readOnly, invalid };
  let body: ReactNode = null;
  switch (rule.kind) {
    case "item_discount":
    case "buy_x_get_y": {
      const scope = (
        <ScopeBlock
          rule={rule}
          {...common}
          lists={lists}
          query={query}
          onQuery={(value) => {
            onQuery(key, value);
          }}
          onRule={onRule<ItemFormRule>(rule.kind)}
        />
      );
      body =
        rule.kind === "item_discount" ? (
          <>
            {scope}
            <ItemDiscountBlock rule={rule} {...common} onRule={onRule<ItemDiscountFormRule>("item_discount")} />
          </>
        ) : (
          <>
            {scope}
            <BuyXGetYBlock rule={rule} {...common} onRule={onRule<BuyXGetYFormRule>("buy_x_get_y")} />
          </>
        );
      break;
    }
    case "spend_discount":
      body = <SpendDiscountBlock rule={rule} {...common} onRule={onRule<SpendDiscountFormRule>("spend_discount")} />;
      break;
    case "free_shipping":
      body = <FreeShippingBlock rule={rule} {...common} onRule={onRule<FreeShippingFormRule>("free_shipping")} />;
      break;
  }
  return (
    <li className="site-admin-promo-rules__rule">
      <div className="site-admin-promo-rules__head">
        <span className="site-admin-promo-rules__number">{index + 1}</span>
        <strong>{t(RULE_KIND_LABEL[rule.kind])}</strong>
      </div>
      {!readOnly && (
        <div className="site-admin-promo-rules__tools">
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            aria-label={t("admin.rule_move_up")}
            disabled={index === 0}
            onClick={() => {
              onRules((form) => moveRule(form.rules, key, -1));
            }}
          >
            ↑
          </button>
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            aria-label={t("admin.rule_move_down")}
            disabled={index === count - 1}
            onClick={() => {
              onRules((form) => moveRule(form.rules, key, 1));
            }}
          >
            ↓
          </button>
          <button
            className="acs-admin__btn acs-admin__btn--secondary"
            type="button"
            onClick={() => {
              onRules((form) => removeRule(form.rules, key));
            }}
          >
            {t("admin.rule_remove")}
          </button>
        </div>
      )}
      <div className="site-admin-promo-rules__body">{body}</div>
    </li>
  );
}

// 规则区：各条规则依次排列，末尾为 admin.rule_add 的选择（只读时不显示；第一项为 admin.rule_add 本身，四个类别按 addOptionDisabled 禁用，选中即在最后添加）。
// 条数问题（rules）的提示在 admin.rule_add 旁。
export function AdminPromotionRulesView({ form, readOnly, lists, invalid, onRules, queries, onQuery }: RulesViewProps) {
  const t = useCopy();
  const { rules } = form;
  return (
    <div className="site-admin-promo-rules">
      {rules.length > 0 && (
        <ol className="site-admin-promo-rules__list">
          {rules.map((rule, index) => (
            <RuleBlock
              key={rule.key}
              rule={rule}
              index={index}
              count={rules.length}
              readOnly={readOnly}
              lists={lists}
              invalid={invalid}
              query={queries[rule.key] ?? ""}
              onRules={onRules}
              onQuery={onQuery}
            />
          ))}
        </ol>
      )}
      {!readOnly && (
        <Field className="site-admin-promo-rules__add" invalid={invalid("rules")}>
          {(describedBy) => (
            <select
              className="acs-admin__select"
              name="rule_add"
              value=""
              aria-label={t("admin.rule_add")}
              aria-invalid={describedBy !== undefined}
              aria-describedby={describedBy}
              onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                const kind = RULE_KINDS.find((item) => item === event.target.value);
                if (kind !== undefined) {
                  onRules((current) => addRule(current, kind));
                }
              }}
            >
              <option value="" disabled>
                {t("admin.rule_add")}
              </option>
              {RULE_KINDS.map((kind) => (
                <option key={kind} value={kind} disabled={addOptionDisabled(rules, kind)}>
                  {t(RULE_KIND_LABEL[kind])}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
    </div>
  );
}

// 规则区（编辑页用）：各条商品级规则的搜索词只在本页保存，不进表单、不提交。
export default function AdminPromotionRules(props: RulesProps) {
  const [queries, setQueries] = useState<Readonly<Record<number, string>>>({});
  return (
    <AdminPromotionRulesView
      {...props}
      queries={queries}
      onQuery={(key, query) => {
        setQueries((current) => ({ ...current, [key]: query }));
      }}
    />
  );
}
