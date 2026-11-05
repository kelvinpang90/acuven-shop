import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, Dispatch, FormEvent, SetStateAction } from "react";

import { readAdminCategories } from "../api/adminCategories";
import type { AdminCategory, CategoriesRead } from "../api/adminCategories";
import { createAdminProduct, readAdminProduct, readAdminProducts, saveAdminProduct } from "../api/adminProducts";
import type {
  AdminProduct,
  AdminProductImage,
  AdminProductListItem,
  AdminVariant,
  CreateVariant,
  ExistingVariant,
  Names,
  NewOption,
  NewValue,
  NewValueRef,
  NewVariant,
  ProductCreate,
  ProductRead,
  ProductSave,
  ProductsRead,
  ProductWrite,
  SavedOption,
  ValueRef,
} from "../api/adminProducts";
import AdminFrame, { AdminAlert, LANGUAGE_LABEL, useUnsavedConfirm } from "../components/AdminFrame";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import AdminProductImages from "./AdminProductImages";
import type { RereadOutcome } from "./AdminProductImages";
import { feeSen, feeText } from "./AdminShippingPage";

// A04 后台商品（docs/UX.md 0.17 A04，视觉稿 A04-desktop 与 A04-phone-edit；HANDOFF 0.82 第（2）点）：用后台框架渲染，当前导航项为商品。
// 内容区为标题 admin.nav_products 与全部商品（含未上架的，按接口给的顺序；桌面表格、手机卡片，两套都渲染，由 site.css 按宽度显隐），
// 点 common.edit 后读取该商品，成功才显示编辑区（桌面在列表右侧；手机从点击起隐藏列表，编辑区上方为页内的 common.back）。
// 打开页面时并行调用 GET /api/admin/products 与 GET /api/admin/categories，两者都取到才显示列表；切换界面语言不重新读取。
// 单件为 GET /api/admin/products/{id}（经 api/adminProducts.ts 与 api/adminCategories.ts；DESIGN 1.18「计价、优惠、积分与库存」第 11 条）。
// 401 换成登录页 A01；离开页面时中止进行中的读取，之后不再更新页面。
// 编辑区（SHOP-TASK-203）：内容语言三个按钮切换三语名称、描述与规格名、规格值名称的输入框，另有分类下拉、上架、每单限购与已有 SKU 的价格、
// 每日初始库存与启用；输入框保留所填的原文。有未保存的修改（与最近一次读取或保存的值不同）时以 useUnsavedConfirm
// （SHOP-TASK-197）接入离开确认。
// 保存（SHOP-TASK-204，UX A04「一次保存」「上架」「保存结果」「上限提示」与 HANDOFF 0.82 第（3）点）：编辑区最后为 common.save，页面先查格式、
// 范围与上架条件中请求里看得到的部分，价格按文字换成整仙后以 PUT /api/admin/products/{id} 一次提交全部可改内容；结果显示在 common.save 旁。
// 新增规格值与 SKU（SHOP-TASK-205，UX A04「规格」「SKU」与 HANDOFF 0.82 第（4）点）：每个规格名的值后为 admin.add_value，SKU 区下方为
// admin.add_sku；新增的值与 SKU 排在已有的之后、各带页面生成的临时编号，随 common.save 一次提交，(✗) 去掉（读屏标签沿用 admin.remove）。
// 新建商品（SHOP-TASK-206，UX A04「新建商品」「规格」与 HANDOFF 0.82 第（5）点）：标题旁为 common.create，经未保存确认后显示新建表单（标题
// admin.product_new；文案为空串、分类为下拉框第一项、不上架、限购 10、没有规格名、SKU 表固定一行）；admin.add_option 新增规格名（至多 3 个），
// 规格名、值与 SKU 各带 (✗)；以 POST /api/admin/products 提交，成功后在列表最前加一行、进入该商品的编辑区并显示 admin.product_created。
// 图片区（SHOP-TASK-207，UX A04「图片」与 HANDOFF 0.82 第（6）点）：AdminProductImages.tsx 放在 admin.publish_requirements 与规格区之间，
// 页面传入商品 ID、当前图片（product.images）与 CSRF 令牌；图片操作只换 product.images，不算未保存的修改、不改 version；图片操作进行中
// common.save 禁用，保存或新建进行中图片区的按钮禁用。当日库存调整在 208。

// 三语名称（读取给的，可为 null）。
export type ListNames = Readonly<Record<Language, string | null>>;

// 名称按后台界面语言显示：该语言有译名时用它，缺译（null 或空串）回退英文，英文也缺时为 —。
export function localName(names: ListNames, language: Language): string {
  const own = names[language];
  if (own !== null && own !== "") {
    return own;
  }
  return names.en === null || names.en === "" ? "—" : names.en;
}

// 列表一行的商品名称与分类名称（照 localName）。
export function productName(item: AdminProductListItem, language: Language): string {
  return localName({ en: item.name_en, zh: item.name_zh, ms: item.name_ms }, language);
}

export function categoryName(item: AdminProductListItem, language: Language): string {
  return localName({ en: item.category_name_en, zh: item.category_name_zh, ms: item.category_name_ms }, language);
}

// 三语文案（输入框里的值，读取为 null 时为空串）。
export type Texts = Readonly<Record<Language, string>>;

// 规格值、规格名与已有 SKU 的输入：规格值与规格名只能改三语名称；SKU 的价格与每日初始库存为输入框的原文，启用为勾选状态。
export interface ValueForm {
  id: number;
  names: Texts;
}

// 新增、还没保存的规格值（SHOP-TASK-205）：页面生成的临时编号（v1、v2…）与三语名称。
export interface NewValueForm {
  tempId: string;
  names: Texts;
}

export type OptionValueForm = ValueForm | NewValueForm;

// 规格名的值：已有的按接口顺序，新增的按新增顺序排在其后。
export interface OptionForm {
  id: number;
  names: Texts;
  values: readonly OptionValueForm[];
}

// 新建商品时给出的规格名（SHOP-TASK-206）：页面生成的临时编号（o1、o2…，只用于页面，请求里的规格名没有编号）、三语名称与值（都是新增的）。
export interface NewOptionForm {
  tempId: string;
  names: Texts;
  values: readonly OptionValueForm[];
}

export type AnyOptionForm = OptionForm | NewOptionForm;

export interface VariantForm {
  id: number;
  price: string;
  stock: string;
  isActive: boolean;
}

// 新增、还没保存的 SKU（SHOP-TASK-205）：临时编号（s1、s2…）、按规格名顺序每个规格名所选的值（valueKey，未选为空串）、价格与每日初始库存的原文
// 与启用状态。
export interface NewVariantForm {
  tempId: string;
  choices: readonly string[];
  price: string;
  stock: string;
  isActive: boolean;
}

export type SkuForm = VariantForm | NewVariantForm;

// 编辑区的值：三语名称与描述、分类、上架、每单限购的原文、规格名（按接口顺序；新建表单为按新增顺序的新增规格名）与 SKU（已有的按接口顺序，
// 新增的按新增顺序排在其后）。
export interface ProductForm {
  names: Texts;
  descriptions: Texts;
  categoryId: number;
  isActive: boolean;
  maxPerOrder: string;
  options: readonly AnyOptionForm[];
  variants: readonly SkuForm[];
}

export function isNewValue(value: OptionValueForm): value is NewValueForm {
  return "tempId" in value;
}

export function isNewOption(option: AnyOptionForm): option is NewOptionForm {
  return "tempId" in option;
}

// 规格名在页面里的写法（React 的 key 与比较）：已有的为 ID 的十进制，新增的为临时编号。
function optionKey(option: AnyOptionForm): string {
  return isNewOption(option) ? option.tempId : String(option.id);
}

export function isNewVariant(variant: SkuForm): variant is NewVariantForm {
  return "tempId" in variant;
}

// 规格值在下拉框与新增 SKU 所选里的写法：已有的为 ID 的十进制，新增的为临时编号（以字母开头，与 ID 不会相同）。
export function valueKey(value: OptionValueForm): string {
  return isNewValue(value) ? value.tempId : String(value.id);
}

// 读取给的三语文案换成输入框的值：null 当作空串。
export function texts(names: ListNames): Texts {
  return { en: names.en ?? "", zh: names.zh ?? "", ms: names.ms ?? "" };
}

// 价格输入框的初值：整仙用整数运算写成两位小数文字（照 AdminShippingPage.tsx 的 feeText；0 为 0.00，5 为 0.05）。
export function priceText(sen: number): string {
  return feeText(sen);
}

// 读取到的商品换成编辑区的值（也是比较基线）：文案 null 当作空串，价格为 priceText，库存与限购为十进制文字。
export function formOf(product: AdminProduct): ProductForm {
  return {
    names: texts({ en: product.name_en, zh: product.name_zh, ms: product.name_ms }),
    descriptions: texts({ en: product.description_en, zh: product.description_zh, ms: product.description_ms }),
    categoryId: product.category_id,
    isActive: product.is_active,
    maxPerOrder: String(product.max_per_order),
    options: product.options.map((option) => ({
      id: option.id,
      names: texts({ en: option.name_en, zh: option.name_zh, ms: option.name_ms }),
      values: option.values.map((value) => ({ id: value.id, names: texts({ en: value.name_en, zh: value.name_zh, ms: value.name_ms }) })),
    })),
    variants: product.variants.map((variant) => ({
      id: variant.id,
      price: priceText(variant.price_sen),
      stock: String(variant.daily_initial_stock),
      isActive: variant.is_active,
    })),
  };
}

// common.save 旁的保存结果（A04-states）：成功（保存为 admin.product_saved，新建后为 admin.product_created；role 为 status）、取值不合格、
// 商品已被改动（带 admin.reload）、网络中断与其他失败。
export type SaveNotice =
  | "admin.product_saved"
  | "admin.product_created"
  | "admin.product_invalid"
  | "admin.product_changed"
  | "common.network_check"
  | "common.error_retry";

// 有表单的编辑区（已有商品与新建表单共用）：base 为比较基线，form 为已填的值，content 为内容语言，saving 为保存（或新建）进行中，
// notice 为 common.save 旁的结果。
export interface FormEditor {
  base: ProductForm;
  form: ProductForm;
  content: Language;
  saving: boolean;
  notice: SaveNotice | null;
}

// 编辑区：读取中（该位置不显示内容）、读取失败（只有提示）、已取到与新建表单（SHOP-TASK-206）。id 为点了 common.edit 的商品。
// 已取到时 product 为最近一次读取或保存返回的商品（version、规格列与 SKU 代码、当日可用库存取自它）。
export type Editing =
  | { status: "loading"; id: number }
  | { status: "failed"; id: number; error: CopyKey }
  | ({ status: "ready"; id: number; product: AdminProduct } & FormEditor)
  | ({ status: "new" } & FormEditor);

export type OpenEditor = Extract<Editing, { status: "ready" }>;
export type NewEditor = Extract<Editing, { status: "new" }>;
// 读取一件已有商品得到的编辑区（读取中之后的那一步）。
export type ProductEditing = Exclude<Editing, { status: "new" }>;

// 打开编辑区：基线与已填的值都为读取的值，内容语言默认英文，没有保存结果。
export function editorOf(id: number, product: AdminProduct): OpenEditor {
  const base = formOf(product);
  return { status: "ready", id, product, base, form: base, content: "en", saving: false, notice: null };
}

const NO_TEXT: Texts = { en: "", zh: "", ms: "" };

// 新建表单的初值（HANDOFF 0.82 第（5）点与本任务 purpose）：文案全为空串，分类为下拉框第一项（可选的分类按接口顺序的第一个；没有时为 0，
// 下拉框为空，提交时页面先查拦下），不上架，每单限购 10，没有规格名，SKU 表固定一行（s1：没有所选的值，价格与每日初始库存为空串，启用）。
export function newForm(categories: readonly AdminCategory[]): ProductForm {
  return {
    names: NO_TEXT,
    descriptions: NO_TEXT,
    categoryId: categories.find(isChoosable)?.id ?? 0,
    isActive: false,
    maxPerOrder: "10",
    options: [],
    variants: [{ tempId: "s1", choices: [], price: "", stock: "", isActive: true }],
  };
}

// 打开新建表单：基线与已填的值都为 newForm，内容语言默认英文，没有结果。
export function newEditor(categories: readonly AdminCategory[]): NewEditor {
  const base = newForm(categories);
  return { status: "new", base, form: base, content: "en", saving: false, notice: null };
}

function sameTexts(a: Texts, b: Texts): boolean {
  return LANGUAGES.every((language) => a[language] === b[language]);
}

function sameValue(a: OptionValueForm, b: OptionValueForm | undefined): boolean {
  return b !== undefined && isNewValue(a) === isNewValue(b) && valueKey(a) === valueKey(b) && sameTexts(a.names, b.names);
}

function sameOption(a: AnyOptionForm, b: AnyOptionForm | undefined): boolean {
  return (
    b !== undefined &&
    isNewOption(a) === isNewOption(b) &&
    optionKey(a) === optionKey(b) &&
    sameTexts(a.names, b.names) &&
    a.values.length === b.values.length &&
    a.values.every((value, index) => sameValue(value, b.values[index]))
  );
}

function sameRow(a: SkuForm, b: SkuForm | undefined): boolean {
  if (b === undefined || a.price !== b.price || a.stock !== b.stock || a.isActive !== b.isActive) {
    return false;
  }
  if (isNewVariant(a) || isNewVariant(b)) {
    return isNewVariant(a) && isNewVariant(b) && a.tempId === b.tempId && a.choices.length === b.choices.length && a.choices.every((choice, index) => choice === b.choices[index]);
  }
  return a.id === b.id;
}

// 有未保存的修改：编辑区已取到，且三语名称与描述、分类、上架、每单限购、规格名与规格值的三语名称、各 SKU 的价格文字、
// 每日初始库存文字与启用状态任一项与基线不同（改回原值不算），或有任一新增的规格值或 SKU（基线取自读取的商品，没有新增的，个数即不同；
// SHOP-TASK-205）。新建表单同样与其初值（newForm）比较，任一项不同即算，加了又去掉的规格名、值与 SKU 不算（SHOP-TASK-206）。
// 切换内容语言不算修改；没有编辑区、读取中与读取失败时没有修改。
export function isDirty(editing: Editing | null): boolean {
  if (editing === null || (editing.status !== "ready" && editing.status !== "new")) {
    return false;
  }
  const { base, form } = editing;
  return !(
    sameTexts(form.names, base.names) &&
    sameTexts(form.descriptions, base.descriptions) &&
    form.categoryId === base.categoryId &&
    form.isActive === base.isActive &&
    form.maxPerOrder === base.maxPerOrder &&
    form.options.length === base.options.length &&
    form.options.every((option, index) => sameOption(option, base.options[index])) &&
    form.variants.length === base.variants.length &&
    form.variants.every((variant, index) => sameRow(variant, base.variants[index]))
  );
}

// 缺译提示：内容语言为中文或马来文，且该语言的商品名称、描述或任一规格名、规格值（含新增的）的名称为空（去掉首尾空白后）；只显示一条。
export function translationMissing(editor: FormEditor): boolean {
  const { content, form } = editor;
  if (content === "en") {
    return false;
  }
  const blank = (values: Texts) => values[content].trim() === "";
  return blank(form.names) || blank(form.descriptions) || form.options.some((option) => blank(option.names) || option.values.some((value) => blank(value.names)));
}

// 可选的分类：启用、英文名称不为 null 且去掉首尾空白后不为空。
export function isChoosable(category: AdminCategory): boolean {
  return category.is_active && category.name_en !== null && category.name_en.trim() !== "";
}

// 分类下拉的一项：value 为分类 ID，label 为显示的文字，choosable 为是否可选的分类。
export interface CategoryChoice {
  id: number;
  label: string;
  choosable: boolean;
}

// 新建表单的分类下拉（SHOP-TASK-206）：只有可选的分类，按接口顺序，文字按后台界面语言、缺译回退英文；没有可选分类时为空。
export function newCategoryChoices(categories: readonly AdminCategory[], language: Language): CategoryChoice[] {
  return categories
    .filter(isChoosable)
    .map((category) => ({ id: category.id, label: localName({ en: category.name_en, zh: category.name_zh, ms: category.name_ms }, language), choosable: true }));
}

// 分类下拉的选项（HANDOFF 0.82）：可选的分类按接口顺序，文字按后台界面语言、缺译回退英文；商品当前分类不在其中时另加它一项排在最前，
// 文字取商品列表该项的 category_name_*（同样的回退，都缺时为 —；列表该项不在或其分类已不是读取到的分类时也为 —）。
export function categoryChoices(categories: readonly AdminCategory[], product: AdminProduct, item: AdminProductListItem | undefined, language: Language): CategoryChoice[] {
  const choices = newCategoryChoices(categories, language);
  if (choices.some((choice) => choice.id === product.category_id)) {
    return choices;
  }
  const label = item !== undefined && item.category_id === product.category_id ? categoryName(item, language) : "—";
  return [{ id: product.category_id, label, choosable: false }, ...choices];
}

// 选中的不是可选分类时，下拉框之下显示 admin.category_current_unavailable。
export function categoryUnavailable(categories: readonly AdminCategory[], categoryId: number): boolean {
  return !categories.some((category) => category.id === categoryId && isChoosable(category));
}

// 规格列（admin.variant）：该 SKU 各规格值最近一次读取的名称，按后台界面语言、缺译回退英文（照 localName），以「 / 」相连；没有规格名时为 —。
export function variantText(product: AdminProduct, variant: AdminVariant, language: Language): string {
  if (product.options.length === 0) {
    return "—";
  }
  return variant.option_value_ids
    .map((valueId, index) => {
      const value = product.options[index]?.values.find((row) => row.id === valueId);
      return value === undefined ? "—" : localName({ en: value.name_en, zh: value.name_zh, ms: value.name_ms }, language);
    })
    .join(" / ");
}

// SKU 区的一行（桌面表格与手机卡片共用）：index 为在 form.variants 里的位置，SKU 代码、规格列与当日可用库存取自读取的商品。
export interface SkuRow {
  index: number;
  form: VariantForm;
  sku: string;
  variant: string;
  available: number;
}

export function skuRows(editor: OpenEditor, language: Language): SkuRow[] {
  return editor.form.variants.flatMap((form, index) => {
    if (isNewVariant(form)) {
      return [];
    }
    const read = editor.product.variants.find((variant) => variant.id === form.id);
    return read === undefined ? [] : [{ index, form, sku: read.sku, variant: variantText(editor.product, read, language), available: read.available_stock }];
  });
}

// 新增 SKU 的规格值下拉框的一项：value 为 valueKey（空选项为空串），label 为显示的文字。
export interface ValueChoice {
  value: string;
  label: string;
}

// 下拉框的选项（HANDOFF 0.82 第（4）点与本任务 purpose）：首项为值与文字都为空的空选项；其余为该规格名下已有的与本次新增的值（按 form 里的顺序），
// 文字为已填的名称，按后台界面语言、缺译（空串）回退英文，都为空时为空文字。
export function valueChoices(option: AnyOptionForm, language: Language): ValueChoice[] {
  return [
    { value: "", label: "" },
    ...option.values.map((value) => ({ value: valueKey(value), label: value.names[language] !== "" ? value.names[language] : value.names.en })),
  ];
}

// 新增 SKU 一行（桌面表格末尾与手机卡片共用）：index 为在 form.variants 里的位置，selects 为每个规格名一个下拉框的选项与所选的值。
export interface NewSkuRow {
  index: number;
  form: NewVariantForm;
  selects: { choices: ValueChoice[]; selected: string }[];
}

export function newSkuRows(editor: FormEditor, language: Language): NewSkuRow[] {
  const { options, variants } = editor.form;
  return variants.flatMap((form, index) =>
    isNewVariant(form)
      ? [{ index, form, selects: options.map((option, optionIndex) => ({ choices: valueChoices(option, language), selected: form.choices[optionIndex] ?? "" })) }]
      : [],
  );
}

// 每个规格名至多 20 个值、一件商品至多 50 个 SKU（UX A04「规格」「SKU」）；已有的与新增的合计。
export const MAX_VALUES = 20;
export const MAX_VARIANTS = 50;
// 新建商品至多 3 个规格名（UX A04「规格」）。
export const MAX_OPTIONS = 3;

// admin.add_value 是否可用：该规格名下的值未达 20 个。
export function canAddValue(option: AnyOptionForm): boolean {
  return option.values.length < MAX_VALUES;
}

// admin.add_option（只在新建表单里显示）是否可用：规格名未达 3 个。
export function canAddOption(form: ProductForm): boolean {
  return form.options.length < MAX_OPTIONS;
}

// admin.add_sku：没有规格名的商品不显示；SKU 未达 50 个时可用。
export function showsAddSku(form: ProductForm): boolean {
  return form.options.length > 0;
}

export function canAddSku(form: ProductForm): boolean {
  return showsAddSku(form) && form.variants.length < MAX_VARIANTS;
}

// 有任一 SKU 在该规格名下选了这个值。
function chosen(form: ProductForm, optionIndex: number, value: OptionValueForm): boolean {
  return form.variants.some((variant) => isNewVariant(variant) && variant.choices[optionIndex] === valueKey(value));
}

// 新增规格值的 (✗) 是否可用：是新增的值，该规格名下不只这一个值，且没有被任一新增 SKU 在该规格名下选用（UX A04「规格」与 HANDOFF 0.82
// 第（5）点）。已有的值没有 (✗)。
export function valueRemovable(form: ProductForm, optionIndex: number, valueIndex: number): boolean {
  const option = form.options[optionIndex];
  const value = option?.values[valueIndex];
  return option !== undefined && value !== undefined && isNewValue(value) && option.values.length > 1 && !chosen(form, optionIndex, value);
}

// 新建表单里规格名的 (✗) 是否可用（SHOP-TASK-206）：是新增的规格名，且它的值都没有被任一 SKU 选用。已有的规格名没有 (✗)。
export function optionRemovable(form: ProductForm, optionIndex: number): boolean {
  const option = form.options[optionIndex];
  return option !== undefined && isNewOption(option) && !option.values.some((value) => chosen(form, optionIndex, value));
}

// 新增 SKU 的 (✗) 是否可用：是新增的 SKU，且不是只剩的一行（新建表单至少一个 SKU；已有商品有已有的 SKU，新增行总可去掉）。
export function skuRemovable(form: ProductForm, variantIndex: number): boolean {
  const row = form.variants[variantIndex];
  return row !== undefined && isNewVariant(row) && form.variants.length > 1;
}

// 临时编号：前缀加数字，取同一种新增里已用的最大数字加一（按新增顺序递增；去掉之后再新增也不会与请求里的重复）。
function nextTempId(prefix: "o" | "v" | "s", used: readonly string[]): string {
  return `${prefix}${String(Math.max(0, ...used.map((id) => Number(id.slice(1)))) + 1)}`;
}

function newValueIds(form: ProductForm): string[] {
  return form.options.flatMap((option) => option.values.filter(isNewValue).map((value) => value.tempId));
}

// 编辑区的改动：只换掉对应的一项，其余（含其他语言已填的值）不变；改任何输入都收起 common.save 旁的保存结果（含 403 后的提示）。
// 已有商品的编辑区与新建表单共用（E 为其中一种）。
function withForm<E extends FormEditor>(editor: E, change: (form: ProductForm) => ProductForm): E {
  return { ...editor, form: change(editor.form), notice: null };
}

function withText(values: Texts, language: Language, value: string): Texts {
  return { ...values, [language]: value };
}

// 切换内容语言：只换输入框显示与编辑的语言，各语言已填的值不变。
export function chooseContent<E extends FormEditor>(editor: E, content: Language): E {
  return { ...editor, content };
}

// 改内容语言下的商品名称、描述、规格名与规格值的名称。
export function changeName<E extends FormEditor>(editor: E, value: string): E {
  return withForm(editor, (form) => ({ ...form, names: withText(form.names, editor.content, value) }));
}

export function changeDescription<E extends FormEditor>(editor: E, value: string): E {
  return withForm(editor, (form) => ({ ...form, descriptions: withText(form.descriptions, editor.content, value) }));
}

export function changeOptionName<E extends FormEditor>(editor: E, optionIndex: number, value: string): E {
  return withForm(editor, (form) => ({
    ...form,
    options: form.options.map((option, index) => (index === optionIndex ? { ...option, names: withText(option.names, editor.content, value) } : option)),
  }));
}

export function changeValueName<E extends FormEditor>(editor: E, optionIndex: number, valueIndex: number, value: string): E {
  return withForm(editor, (form) => ({
    ...form,
    options: form.options.map((option, index) =>
      index === optionIndex
        ? { ...option, values: option.values.map((row, at) => (at === valueIndex ? { ...row, names: withText(row.names, editor.content, value) } : row)) }
        : option,
    ),
  }));
}

// 改分类、上架与每单限购（原文，保存时才查）。
export function changeCategory<E extends FormEditor>(editor: E, categoryId: number): E {
  return withForm(editor, (form) => ({ ...form, categoryId }));
}

export function changeActive<E extends FormEditor>(editor: E, checked: boolean): E {
  return withForm(editor, (form) => ({ ...form, isActive: checked }));
}

export function changeMaxPerOrder<E extends FormEditor>(editor: E, text: string): E {
  return withForm(editor, (form) => ({ ...form, maxPerOrder: text }));
}

// 改某个 SKU（已有的或新增的）的价格、每日初始库存（原文，保存时才查）与启用状态；index 为在 form.variants 里的位置。
function withVariant<E extends FormEditor>(editor: E, variantIndex: number, change: (row: SkuForm) => SkuForm): E {
  return withForm(editor, (form) => ({ ...form, variants: form.variants.map((row, index) => (index === variantIndex ? change(row) : row)) }));
}

export function changePrice<E extends FormEditor>(editor: E, variantIndex: number, text: string): E {
  return withVariant(editor, variantIndex, (row) => ({ ...row, price: text }));
}

export function changeStock<E extends FormEditor>(editor: E, variantIndex: number, text: string): E {
  return withVariant(editor, variantIndex, (row) => ({ ...row, stock: text }));
}

export function changeVariantActive<E extends FormEditor>(editor: E, variantIndex: number, checked: boolean): E {
  return withVariant(editor, variantIndex, (row) => ({ ...row, isActive: checked }));
}

// 点 admin.add_option（新建表单，SHOP-TASK-206）：在规格名之后加一个新增的规格名（三语名称为空串，带一个三语名称为空串的新增值），
// 每个新增 SKU 在它之下多一个所选的值（空选项）。达 3 个时不变。
export function addOption<E extends FormEditor>(editor: E): E {
  if (!canAddOption(editor.form)) {
    return editor;
  }
  const option: NewOptionForm = {
    tempId: nextTempId("o", editor.form.options.filter(isNewOption).map((row) => row.tempId)),
    names: NO_TEXT,
    values: [{ tempId: nextTempId("v", newValueIds(editor.form)), names: NO_TEXT }],
  };
  return withForm(editor, (form) => ({
    ...form,
    options: [...form.options, option],
    variants: form.variants.map((row) => (isNewVariant(row) ? { ...row, choices: [...row.choices, ""] } : row)),
  }));
}

// 规格名的 (✗)：去掉该规格名与它的值，每个新增 SKU 去掉在它之下所选的值；去掉后没有规格名时 SKU 表只留第一行、清除其所选的值
// （HANDOFF 0.82 第（5）点）。已有的规格名、或它的值被选用时（optionRemovable 为假）不变。
export function removeOption<E extends FormEditor>(editor: E, optionIndex: number): E {
  if (!optionRemovable(editor.form, optionIndex)) {
    return editor;
  }
  return withForm(editor, (form) => {
    const options = form.options.filter((_, index) => index !== optionIndex);
    const variants =
      options.length === 0
        ? form.variants.slice(0, 1).map((row) => (isNewVariant(row) ? { ...row, choices: [] } : row))
        : form.variants.map((row) => (isNewVariant(row) ? { ...row, choices: row.choices.filter((_, index) => index !== optionIndex) } : row));
    return { ...form, options, variants };
  });
}

// 点 admin.add_value：在该规格名的值之后加一个新增的值（三语名称为空串）。达 20 个时不变。
export function addValue<E extends FormEditor>(editor: E, optionIndex: number): E {
  const option = editor.form.options[optionIndex];
  if (option === undefined || !canAddValue(option)) {
    return editor;
  }
  const value: NewValueForm = { tempId: nextTempId("v", newValueIds(editor.form)), names: NO_TEXT };
  return withForm(editor, (form) => ({
    ...form,
    options: form.options.map((row, index) => (index === optionIndex ? { ...row, values: [...row.values, value] } : row)),
  }));
}

// 新增规格值的 (✗)：去掉该值。已有的值、只剩的一个值或被新增 SKU 选用时（valueRemovable 为假）不变。
export function removeValue<E extends FormEditor>(editor: E, optionIndex: number, valueIndex: number): E {
  if (!valueRemovable(editor.form, optionIndex, valueIndex)) {
    return editor;
  }
  return withForm(editor, (form) => ({
    ...form,
    options: form.options.map((row, index) => (index === optionIndex ? { ...row, values: row.values.filter((_, at) => at !== valueIndex) } : row)),
  }));
}

// 点 admin.add_sku：在全部 SKU 之后加一行新增的 SKU（每个规格名选空选项，价格与每日初始库存为空串，默认启用）。没有规格名或达 50 个时不变。
export function addSku<E extends FormEditor>(editor: E): E {
  if (!canAddSku(editor.form)) {
    return editor;
  }
  const row: NewVariantForm = {
    tempId: nextTempId("s", editor.form.variants.filter(isNewVariant).map((variant) => variant.tempId)),
    choices: editor.form.options.map(() => ""),
    price: "",
    stock: "",
    isActive: true,
  };
  return withForm(editor, (form) => ({ ...form, variants: [...form.variants, row] }));
}

// 新增 SKU 的 (✗)：去掉该行。已有的 SKU 与只剩的一行（skuRemovable 为假）不变。
export function removeSku<E extends FormEditor>(editor: E, variantIndex: number): E {
  if (!skuRemovable(editor.form, variantIndex)) {
    return editor;
  }
  return withForm(editor, (form) => ({ ...form, variants: form.variants.filter((_, index) => index !== variantIndex) }));
}

// 改新增 SKU 在某个规格名下所选的值（valueKey，空选项为空串）。已有 SKU 的规格值组合不能改，不变。
export function chooseValue<E extends FormEditor>(editor: E, variantIndex: number, optionIndex: number, key: string): E {
  const row = editor.form.variants[variantIndex];
  if (row === undefined || !isNewVariant(row)) {
    return editor;
  }
  return withVariant(editor, variantIndex, () => ({ ...row, choices: row.choices.map((choice, index) => (index === optionIndex ? key : choice)) }));
}

// 价格上限（UX A04「上限提示」：RM500,000.00）。
export const MAX_PRICE_SEN = 50000000;
// 每日初始库存与每单限购：一到四位数字（HANDOFF 0.82 第（3）点）；限购另须在 1–1000。
const COUNT_PATTERN = /^\d{1,4}$/;
const MAX_PER_ORDER = 1000;

// 价格文字换成整仙：照 A06 运费的 feeSen（一到九位数字，可再跟小数点与一到两位数字；整数部分乘 100 加补足两位的小数部分，不经浮点数相乘），
// 超过 MAX_PRICE_SEN 不合格；不合格为 null。
export function priceSen(text: string): number | null {
  const sen = feeSen(text);
  return sen === null || sen > MAX_PRICE_SEN ? null : sen;
}

// 每日初始库存：一到四位数字（0–9,999）；不合格为 null。
export function stockCount(text: string): number | null {
  return COUNT_PATTERN.test(text) ? Number(text) : null;
}

// 每单限购：一到四位数字且在 1–1000；不合格为 null。
export function limitCount(text: string): number | null {
  if (!COUNT_PATTERN.test(text)) {
    return null;
  }
  const count = Number(text);
  return count >= 1 && count <= MAX_PER_ORDER ? count : null;
}

// 上架条件中请求里看得到的部分（UX A04「上架」）：英文名称与英文描述、全部规格名与规格值（含新增的）的英文名称去掉首尾空白后都不为空，
// 且至少一个 SKU（含新增的）勾选启用。分类是否启用、名称长度与重复、组合是否重复由服务端判断。
export function publishReady(form: ProductForm): boolean {
  const filled = (values: Texts) => values.en.trim() !== "";
  return (
    filled(form.names) &&
    filled(form.descriptions) &&
    form.options.every((option) => filled(option.names) && option.values.every((value) => filled(value.names))) &&
    form.variants.some((variant) => variant.isActive)
  );
}

function namesOf(values: Texts): Names {
  return { name_en: values.en, name_zh: values.zh, name_ms: values.ms };
}

// 新增 SKU 所选的值按规格名的顺序换成引用：已有的值以 id、新增的值以 temp_id。个数与规格名不同、任一规格名未选值（空串）或所选的值
// 不在该规格名下时为 null。
function valueRefs(options: readonly AnyOptionForm[], choices: readonly string[]): ValueRef[] | null {
  if (choices.length !== options.length) {
    return null;
  }
  const refs: ValueRef[] = [];
  for (const [index, option] of options.entries()) {
    const value = option.values.find((row) => valueKey(row) === choices[index]);
    if (value === undefined) {
      return null;
    }
    refs.push(isNewValue(value) ? { temp_id: value.tempId } : { id: value.id });
  }
  return refs;
}

// 保存的请求体（SHOP-TASK-200 的 ProductSave）：读取（或上次保存）给的 version、文案原样（不去空白）、下拉框所选的分类 ID、上架、每单限购、
// 全部规格名与规格值（已有的值为 id 与三语名称，按读取的顺序；新增的值为 temp_id 与三语名称，按新增顺序排在其后）与全部 SKU（已有的为 id、
// 价格整仙、每日初始库存与启用，按读取的顺序，不带规格值组合；新增的为 temp_id、按规格名顺序的 option_values 与这三项，按新增顺序排在其后）。
// 页面先查：价格、每日初始库存与每单限购任一格式或范围不对，任一新增 SKU 有规格名未选值，或勾选上架而 publishReady 不满足时为 null（不提交）。
// 已有商品的规格名不能增减，有新增的规格名时也为 null。
export function saveInput(form: ProductForm, version: number): ProductSave | null {
  const maxPerOrder = limitCount(form.maxPerOrder);
  if (maxPerOrder === null || (form.isActive && !publishReady(form))) {
    return null;
  }
  const variants: (ExistingVariant | NewVariant)[] = [];
  for (const row of form.variants) {
    const price = priceSen(row.price);
    const stock = stockCount(row.stock);
    if (price === null || stock === null) {
      return null;
    }
    if (isNewVariant(row)) {
      const refs = valueRefs(form.options, row.choices);
      if (refs === null) {
        return null;
      }
      variants.push({ temp_id: row.tempId, option_values: refs, price_sen: price, daily_initial_stock: stock, is_active: row.isActive });
    } else {
      variants.push({ id: row.id, price_sen: price, daily_initial_stock: stock, is_active: row.isActive });
    }
  }
  const options: SavedOption[] = [];
  for (const option of form.options) {
    if (isNewOption(option)) {
      return null;
    }
    options.push({
      id: option.id,
      ...namesOf(option.names),
      values: option.values.map((value) => (isNewValue(value) ? { temp_id: value.tempId, ...namesOf(value.names) } : { id: value.id, ...namesOf(value.names) })),
    });
  }
  return {
    version,
    category_id: form.categoryId,
    name_en: form.names.en,
    name_zh: form.names.zh,
    name_ms: form.names.ms,
    description_en: form.descriptions.en,
    description_zh: form.descriptions.zh,
    description_ms: form.descriptions.ms,
    is_active: form.isActive,
    max_per_order: maxPerOrder,
    options,
    variants,
  };
}

// 新建的请求体（SHOP-TASK-200 的 ProductCreate，DESIGN 1.18 第 11 条）：下拉框所选的分类 ID、文案原样（不去空白）、上架、每单限购、规格名
// （按顺序，三语名称与值；值为 temp_id 与三语名称）与 SKU（temp_id、option_values 以 temp_id 按规格名的顺序引用值，没有规格名时为空数组；
// 价格整仙、每日初始库存与启用）。页面先查（HANDOFF 0.82 第（5）点）：在 saveInput 的各项之外，所选的分类不是可选的分类（没有可选分类时
// 即如此）时为 null。规格名与值的英文名称是否为空与重复由服务端判断（上架时照 publishReady 先查）。
export function createInput(form: ProductForm, categories: readonly AdminCategory[]): ProductCreate | null {
  const maxPerOrder = limitCount(form.maxPerOrder);
  if (categoryUnavailable(categories, form.categoryId) || maxPerOrder === null || (form.isActive && !publishReady(form))) {
    return null;
  }
  const options: NewOption[] = [];
  for (const option of form.options) {
    const values: NewValue[] = [];
    for (const value of option.values) {
      if (!isNewValue(value)) {
        return null;
      }
      values.push({ temp_id: value.tempId, ...namesOf(value.names) });
    }
    options.push({ ...namesOf(option.names), values });
  }
  const variants: CreateVariant[] = [];
  for (const row of form.variants) {
    const price = priceSen(row.price);
    const stock = stockCount(row.stock);
    const refs = isNewVariant(row) ? valueRefs(form.options, row.choices) : null;
    if (price === null || stock === null || !isNewVariant(row) || refs === null) {
      return null;
    }
    const optionValues: NewValueRef[] = [];
    for (const ref of refs) {
      if (!("temp_id" in ref)) {
        return null;
      }
      optionValues.push(ref);
    }
    variants.push({ temp_id: row.tempId, option_values: optionValues, price_sen: price, daily_initial_stock: stock, is_active: row.isActive });
  }
  return {
    category_id: form.categoryId,
    name_en: form.names.en,
    name_zh: form.names.zh,
    name_ms: form.names.ms,
    description_en: form.descriptions.en,
    description_zh: form.descriptions.zh,
    description_ms: form.descriptions.ms,
    is_active: form.isActive,
    max_per_order: maxPerOrder,
    options,
    variants,
  };
}

// 点 common.save：按钮禁用并标 aria-busy，先前的保存结果收起。
export function startSaving<E extends FormEditor>(editor: E): E {
  return { ...editor, saving: true, notice: null };
}

// 页面的状态：读取中（标 aria-busy）、读取失败（只有提示）、已取到（按接口顺序的商品、分类读取的结果（供 203 的分类下拉）、
// 列表给的只用于写请求头的 CSRF 令牌与编辑区；没有点 common.edit 时为 null）。
export type ProductsState =
  | { status: "loading" }
  | { status: "failed"; error: CopyKey }
  | { status: "ready"; products: AdminProductListItem[]; categories: AdminCategory[]; csrfToken: string; editing: Editing | null };

export type ReadyState = Extract<ProductsState, { status: "ready" }>;

export const LOADING: ProductsState = { status: "loading" };

// 按两个读取的结果决定（HANDOFF 0.82）：都取到时显示列表、不选中任何商品；任一 401 回到 A01（login）；否则任一网络中断
// common.network_check，其他失败 common.error_retry。
export function readStep(products: ProductsRead, categories: CategoriesRead): Exclude<ProductsState, { status: "loading" }> | "login" {
  if (products.kind === "none" || categories.kind === "none") {
    return "login";
  }
  if (products.kind === "network" || categories.kind === "network") {
    return { status: "failed", error: "common.network_check" };
  }
  if (products.kind === "failed" || categories.kind === "failed") {
    return { status: "failed", error: "common.error_retry" };
  }
  return { status: "ready", products: products.products, categories: categories.categories, csrfToken: products.csrf_token, editing: null };
}

// 编辑区已打开（或正在读取）的商品；没有、读取失败（编辑区没有打开）或为新建表单时为 null。
export function editingId(state: ProductsState): number | null {
  if (state.status !== "ready" || state.editing === null) {
    return null;
  }
  const { editing } = state;
  return editing.status === "loading" || editing.status === "ready" ? editing.id : null;
}

// 点 common.create（经未保存确认之后）：收起当前的编辑区、改为空白的新建表单（照 newEditor，分类取自分类读取的结果）。
// 列表还没取到时不变（null）。
export function startCreating(state: ProductsState): ProductsState | null {
  return state.status === "ready" ? { ...state, editing: newEditor(state.categories) } : null;
}

// 点某行 common.edit：收起当前的编辑区、改为读取该商品（读取中）。点的正是编辑区已打开或正在读取的那一件时不变（null，不做任何事）；
// 列表还没取到时也不变。读取失败的那一件再点时重新读取。
export function startEditing(state: ProductsState, id: number): ProductsState | null {
  if (state.status !== "ready" || editingId(state) === id) {
    return null;
  }
  return { ...state, editing: { status: "loading", id } };
}

// 按单件读取的结果决定：取到时显示编辑区（照 editorOf）；401 回到 A01（login）；网络中断 common.network_check，其他失败（含商品不存在）
// common.error_retry，都显示在编辑区的位置。
export function productStep(read: ProductRead, id: number): ProductEditing | "login" {
  switch (read.kind) {
    case "ok":
      return editorOf(id, read.product);
    case "none":
      return "login";
    case "network":
      return { status: "failed", id, error: "common.network_check" };
    case "failed":
      return { status: "failed", id, error: "common.error_retry" };
  }
}

// 单件读取的结果只换掉仍在读取同一件商品的编辑区：之后又点了另一行或返回时（以最后一次为准），先前的结果不显示。
export function settleEditing(state: ProductsState, next: Editing): ProductsState {
  if (state.status !== "ready" || next.status === "new") {
    return state;
  }
  const { editing } = state;
  return editing !== null && editing.status === "loading" && editing.id === next.id ? { ...state, editing: next } : state;
}

// 手机的 common.back 与收起编辑区：回到只有列表。
export function closeEditing(state: ProductsState): ProductsState {
  return state.status === "ready" ? { ...state, editing: null } : state;
}

// 对当前状态的一次改动（请求的结果按结果到达时的状态更新）。
export type ProductsChange = (current: ProductsState) => ProductsState;

// 保存成功后列表中该行：名称与状态取自返回的商品，分类名称取自分类读取的结果（分类不在其中时，分类没变则沿用该行原有的名称，否则为 null，显示 —）。
export function savedItem(item: AdminProductListItem, product: AdminProduct, categories: readonly AdminCategory[]): AdminProductListItem {
  const category = categories.find((row) => row.id === product.category_id);
  const names =
    category !== undefined
      ? { category_name_en: category.name_en, category_name_zh: category.name_zh, category_name_ms: category.name_ms }
      : item.category_id === product.category_id
        ? { category_name_en: item.category_name_en, category_name_zh: item.category_name_zh, category_name_ms: item.category_name_ms }
        : { category_name_en: null, category_name_zh: null, category_name_ms: null };
  return {
    ...item,
    name_en: product.name_en,
    name_zh: product.name_zh,
    name_ms: product.name_ms,
    is_active: product.is_active,
    category_id: product.category_id,
    ...names,
  };
}

// 只改保存进行中的那一件的编辑区：保存期间又切换到别的商品或收起（经未保存确认）时，结果不覆盖之后的编辑区。
function withSavingEditor(current: ProductsState, id: number, change: (editor: OpenEditor) => OpenEditor): ProductsState {
  if (current.status !== "ready" || current.editing === null || current.editing.status !== "ready") {
    return current;
  }
  const { editing } = current;
  return editing.id === id && editing.saving ? { ...current, editing: change(editing) } : current;
}

// 只改新建进行中的新建表单（SHOP-TASK-206）：新建期间又打开了别的商品或收起（经未保存确认）时，结果不覆盖之后的编辑区。
function withCreatingEditor(current: ProductsState, change: (editor: NewEditor) => Editing): ProductsState {
  if (current.status !== "ready" || current.editing === null || current.editing.status !== "new" || !current.editing.saving) {
    return current;
  }
  return { ...current, editing: change(current.editing) };
}

// 保存的对象：已有商品的 ID，或新建表单（"new"）。
export type SaveTarget = number | "new";

function withTarget(current: ProductsState, target: SaveTarget, change: <E extends FormEditor>(editor: E) => E): ProductsState {
  return target === "new" ? withCreatingEditor(current, change) : withSavingEditor(current, target, change);
}

function failedSave(target: SaveTarget, notice: SaveNotice): ProductsChange {
  return (current) => withTarget(current, target, (editor) => ({ ...editor, saving: false, notice }));
}

// 新建成功后列表最前多出的一行：名称、slug、状态与分类取自返回的商品，分类名称取自分类读取的结果（分类不在其中时为 null，显示 —）。
export function createdItem(product: AdminProduct, categories: readonly AdminCategory[]): AdminProductListItem {
  const category = categories.find((row) => row.id === product.category_id);
  return {
    id: product.id,
    slug: product.slug,
    name_en: product.name_en,
    name_zh: product.name_zh,
    name_ms: product.name_ms,
    is_active: product.is_active,
    category_id: product.category_id,
    category_name_en: category?.name_en ?? null,
    category_name_zh: category?.name_zh ?? null,
    category_name_ms: category?.name_ms ?? null,
  };
}

// 按新建结果决定（UX A04「保存结果」与 HANDOFF 0.82 第（5）点）：成功时以返回的商品在列表最前加一行（照 createdItem；新建表单已被收起时也加），
// 新建表单仍在时改为该商品的编辑区（照 editorOf，内容语言不变）并显示 admin.product_created；401 回到 A01；403 重新读取（reread）；
// 取值不合格 admin.product_invalid、商品已被改动 admin.product_changed（带 admin.reload）、网络中断 common.network_check（不自动重新读取：
// 新建可能已经成功）、其他失败 common.error_retry，都保留已填的值。
export function createStep(write: ProductWrite): ProductsChange | "login" | "reread" {
  switch (write.kind) {
    case "ok":
      return (current) => {
        if (current.status !== "ready") {
          return current;
        }
        const products = [createdItem(write.product, current.categories), ...current.products];
        return withCreatingEditor({ ...current, products }, (editor) => ({
          ...editorOf(write.product.id, write.product),
          content: editor.content,
          notice: "admin.product_created",
        }));
      };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "invalid":
      return failedSave("new", "admin.product_invalid");
    case "changed":
      return failedSave("new", "admin.product_changed");
    case "network":
      return failedSave("new", "common.network_check");
    case "failed":
      return failedSave("new", "common.error_retry");
  }
}

// 按保存结果决定（UX A04「保存结果」）：成功时编辑区改显示返回的商品（新的比较基线与 version，内容语言不变）、列表该行照 savedItem 更新，
// 显示 admin.product_saved；401 回到 A01；403 重新读取（reread）；取值不合格 admin.product_invalid、商品已被改动 admin.product_changed、
// 网络中断 common.network_check（不自动重新读取）、其他失败 common.error_retry，都保留已填的值。
export function saveStep(write: ProductWrite, id: number): ProductsChange | "login" | "reread" {
  switch (write.kind) {
    case "ok":
      return (current) => {
        if (current.status !== "ready") {
          return current;
        }
        const products = current.products.map((item) => (item.id === write.product.id ? savedItem(item, write.product, current.categories) : item));
        return withSavingEditor({ ...current, products }, id, (editor) => ({
          ...editorOf(id, write.product),
          content: editor.content,
          notice: "admin.product_saved",
        }));
      };
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "invalid":
      return failedSave(id, "admin.product_invalid");
    case "changed":
      return failedSave(id, "admin.product_changed");
    case "network":
      return failedSave(id, "common.network_check");
    case "failed":
      return failedSave(id, "common.error_retry");
  }
}

// 403 之后重新读取列表与分类的结果（HANDOFF 0.82 第（3）点）：都取到时换成新的列表、分类与 CSRF 令牌，编辑区、已填的值与提交前的 version
// 都保留（product 不换），在 common.save 旁显示 common.error_retry；任一 401 回到 A01；否则任一网络中断 common.network_check、其他失败
// common.error_retry，列表、编辑区与令牌（仍为旧的）都不变。新建（target 为 "new"，SHOP-TASK-206）照此写法，新建表单与已填的值保留。
export function rereadStep(products: ProductsRead, categories: CategoriesRead, target: SaveTarget): ProductsChange | "login" {
  const step = readStep(products, categories);
  if (step === "login") {
    return "login";
  }
  if (step.status === "failed") {
    return failedSave(target, step.error === "common.network_check" ? "common.network_check" : "common.error_retry");
  }
  return (current) =>
    current.status === "ready"
      ? withTarget({ ...current, products: step.products, categories: step.categories, csrfToken: step.csrfToken }, target, (editor) => ({
          ...editor,
          saving: false,
          notice: "common.error_retry",
        }))
      : current;
}

// 图片区返回的图片列表（SHOP-TASK-207）：只换已取到的编辑区里该商品的 product.images；基线、已填的值、version 与保存结果都不变（图片不算在
// 未保存的修改里，也不改版本号）。编辑区已不是该商品（切到别的商品、收起、读取中或新建表单）时不变。
export function withImages(state: ProductsState, id: number, images: AdminProductImage[]): ProductsState {
  if (state.status !== "ready" || state.editing === null || state.editing.status !== "ready" || state.editing.id !== id) {
    return state;
  }
  const { editing } = state;
  return { ...state, editing: { ...editing, product: { ...editing.product, images } } };
}

// 图片操作 403 之后重新读取列表与分类的结果（SHOP-TASK-207 purpose）：都取到时换成新的列表、分类与 CSRF 令牌，编辑区与已填的值都保留；
// 任一 401 为 login，否则任一网络中断为 network、其他失败为 failed（列表、编辑区与令牌都不变）。图片区按它提示。
export function tokenStep(products: ProductsRead, categories: CategoriesRead): ProductsChange | Exclude<RereadOutcome, "ok"> {
  const step = readStep(products, categories);
  if (step === "login") {
    return "login";
  }
  if (step.status === "failed") {
    return step.error === "common.network_check" ? "network" : "failed";
  }
  return (current) =>
    current.status === "ready" ? { ...current, products: step.products, categories: step.categories, csrfToken: step.csrfToken } : current;
}

// 图片区的 onReread：并行读取列表与分类，照 tokenStep 更新页面并返回结果（401 由图片区回到 A01）。signal 中止之后不再更新（返回 failed）。
export async function rereadToken(signal: AbortSignal, moves: ProductsMoves): Promise<RereadOutcome> {
  const [products, categories] = await Promise.all([readAdminProducts(signal), readAdminCategories(signal)]);
  if (signal.aborted) {
    return "failed";
  }
  const step = tokenStep(products, categories);
  if (typeof step === "string") {
    return step;
  }
  moves.update(step);
  return "ok";
}

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface ProductsMoves {
  show: (state: ProductsState) => void;
  update: (change: ProductsChange) => void;
  replace: (path: RoutePath) => void;
}

// 打开页面：并行读取商品列表与分类；signal 中止（离开页面）之后不再更新。
export async function openProducts(signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  const [products, categories] = await Promise.all([readAdminProducts(signal), readAdminCategories(signal)]);
  if (signal.aborted) {
    return;
  }
  const step = readStep(products, categories);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 读取一件商品（点 common.edit 之后）：signal 中止（又点了另一行、点了 common.back 或离开页面）之后不再更新。
export async function openProduct(id: number, signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  const read = await readAdminProduct(id, signal);
  if (signal.aborted) {
    return;
  }
  const step = productStep(read, id);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.update((current) => settleEditing(current, step));
  }
}

// 整页重新读取（admin.reload）：先显示读取中，再读取列表与分类；为 none 时回到 A01，其他失败只有提示（都为 null）。signal 中止之后不再更新。
async function rereadPage(signal: AbortSignal, moves: ProductsMoves): Promise<ReadyState | null> {
  moves.show(LOADING);
  const [products, categories] = await Promise.all([readAdminProducts(signal), readAdminCategories(signal)]);
  if (signal.aborted) {
    return null;
  }
  const step = readStep(products, categories);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
    return null;
  }
  if (step.status === "failed") {
    moves.show(step);
    return null;
  }
  return step;
}

// 点 admin.reload（HANDOFF 0.82 第（3）点）：先显示读取中、不再确认，未保存的修改丢弃；整页重新读取列表与分类，取到后编辑区为读取中，
// 再重新读取同一商品（照 openProduct）。整页读取为 none 时回到 A01，其他失败只有提示。signal 中止之后不再更新。
export async function reloadProducts(id: number, signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  const step = await rereadPage(signal, moves);
  if (step === null) {
    return;
  }
  moves.show({ ...step, editing: { status: "loading", id } });
  await openProduct(id, signal, moves);
}

// 新建表单里点 admin.reload（本任务 purpose「changed 时 admin.reload 整页重新读取，新建表单恢复为空白」）：照 reloadProducts 整页重新读取，
// 取到后为空白的新建表单（照 newEditor，分类取自新读取的结果）。
export async function reloadNewProduct(signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  const step = await rereadPage(signal, moves);
  if (step !== null) {
    moves.show({ ...step, editing: newEditor(step.categories) });
  }
}

// 写请求的结果：401 回到 A01；403 时重新读取列表与分类（rereadStep）；其余照 step 更新。signal 中止之后不再更新。
async function settleWrite(step: ProductsChange | "login" | "reread", target: SaveTarget, signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
    return;
  }
  if (step !== "reread") {
    moves.update(step);
    return;
  }
  const [products, categories] = await Promise.all([readAdminProducts(signal), readAdminCategories(signal)]);
  if (signal.aborted) {
    return;
  }
  const reread = rereadStep(products, categories, target);
  if (reread === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.update(reread);
  }
}

// 保存一件已有商品：先照 saveInput 查，不通过时不发请求、显示 admin.product_invalid（保留已填的值）；通过时以列表给的 CSRF 令牌提交。
// 403 时重新读取列表与分类（rereadStep）；signal 中止（离开页面）之后其结果不再更新页面。调用前编辑区已经 startSaving。
export async function submitProduct(csrfToken: string, editor: OpenEditor, signal: AbortSignal, moves: ProductsMoves): Promise<void> {
  const input = saveInput(editor.form, editor.product.version);
  if (input === null) {
    moves.update(failedSave(editor.id, "admin.product_invalid"));
    return;
  }
  const write = await saveAdminProduct(editor.id, input, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  await settleWrite(saveStep(write, editor.id), editor.id, signal, moves);
}

// 新建商品（SHOP-TASK-206）：先照 createInput 查（分类取自页面的分类读取结果），不通过时不发请求、显示 admin.product_invalid（保留表单）；
// 通过时以列表给的 CSRF 令牌 POST，结果照 createStep；403 时照 submitProduct 重新读取并保留表单。网络中断只提示、不自动重新读取。
// 调用前新建表单已经 startSaving。
export async function submitNewProduct(
  csrfToken: string,
  editor: NewEditor,
  categories: readonly AdminCategory[],
  signal: AbortSignal,
  moves: ProductsMoves,
): Promise<void> {
  const input = createInput(editor.form, categories);
  if (input === null) {
    moves.update(failedSave("new", "admin.product_invalid"));
    return;
  }
  const write = await createAdminProduct(input, csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  await settleWrite(createStep(write), "new", signal, moves);
}

function pageMoves(setState: Dispatch<SetStateAction<ProductsState>>, replace: (path: RoutePath) => void): ProductsMoves {
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

function ProductStatus({ item }: { item: AdminProductListItem }) {
  const t = useCopy();
  return (
    <span className={item.is_active ? "acs-tag acs-tag--neutral" : "acs-tag acs-tag--outline"}>
      {t(item.is_active ? "admin.active" : "admin.unpublished")}
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
  onEdit: (item: AdminProductListItem) => void;
}

// 列表：桌面表格（每行商品名称与其下的分类名称、状态与 common.edit，照 A04-desktop），手机卡片（商品名称与状态，整张卡片为读屏标签
// common.edit 的按钮）。顺序照接口；名称照 localName。
function ProductList({ products, onEdit }: { products: readonly AdminProductListItem[] } & ListHandlers) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <>
      <div className="acs-admin__panel site-admin-products__table">
        <table className="acs-admin__table">
          <tbody>
            {products.map((item) => (
              <tr key={item.id}>
                <td className="site-admin-products__names">
                  <span>{productName(item, language)}</span>
                  <span className="acs-admin__muted">{categoryName(item, language)}</span>
                </td>
                <td>
                  <ProductStatus item={item} />
                </td>
                <td>
                  <button
                    className="acs-admin__btn acs-admin__btn--secondary"
                    type="button"
                    onClick={() => {
                      onEdit(item);
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
      <ul className="site-admin-products__cards">
        {products.map((item) => (
          <li key={item.id}>
            <button
              className="acs-admin__panel site-admin-products__card"
              type="button"
              aria-label={t("common.edit")}
              onClick={() => {
                onEdit(item);
              }}
            >
              <span className="site-admin-products__card-name">{productName(item, language)}</span>
              <ProductStatus item={item} />
              <span className="site-admin-products__chevron">
                <ChevronIcon />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

// 编辑区输入的处理：optionIndex、valueIndex 与 variantIndex 为在 form 里的位置。
interface InputHandlers {
  onContent: (content: Language) => void;
  onName: (value: string) => void;
  onDescription: (value: string) => void;
  onCategory: (categoryId: number) => void;
  onActive: (checked: boolean) => void;
  onMaxPerOrder: (text: string) => void;
  onOptionName: (optionIndex: number, value: string) => void;
  onValueName: (optionIndex: number, valueIndex: number, value: string) => void;
  onPrice: (variantIndex: number, text: string) => void;
  onStock: (variantIndex: number, text: string) => void;
  onVariantActive: (variantIndex: number, checked: boolean) => void;
  // 新增规格值与 SKU（SHOP-TASK-205）：key 为所选值的 valueKey（空选项为空串）。
  onAddValue: (optionIndex: number) => void;
  onRemoveValue: (optionIndex: number, valueIndex: number) => void;
  onAddSku: () => void;
  onRemoveSku: (variantIndex: number) => void;
  onChooseValue: (variantIndex: number, optionIndex: number, key: string) => void;
  // 新建表单的规格名（SHOP-TASK-206）。
  onAddOption: () => void;
  onRemoveOption: (optionIndex: number) => void;
}

// 保存与「商品已被改动」提示里的 admin.reload。
interface SaveHandlers {
  onSave: () => void;
  onReload: () => void;
}

// 图片区（SHOP-TASK-207）：onImages 以图片操作返回的列表替换该商品的图片，onReread 在图片操作 403 后重新读取列表与分类，onLogin 回到 A01，
// onImagesBusy 记下图片操作是否进行中（须为不变的函数）。
interface ImageHandlers {
  onImages: (id: number, images: AdminProductImage[]) => void;
  onReread: (signal: AbortSignal) => Promise<RereadOutcome>;
  onLogin: () => void;
  onImagesBusy: (busy: boolean) => void;
}

interface EditorHandlers extends InputHandlers, SaveHandlers, ImageHandlers {
  onBack: () => void;
}

// 图片区要的页面数据：列表给的 CSRF 令牌与图片操作是否进行中。
interface ImageContext {
  csrfToken: string;
  imagesBusy: boolean;
}

const NO_IMAGES: readonly AdminProductImage[] = [];

function CrossIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

// 新增规格值与新增 SKU 的 (✗)：次要按钮，读屏标签沿用 admin.remove（UX 没有为 A04 的 (✗) 另给读屏标签，HANDOFF 0.82 第（4）点），图标不读出。
function RemoveButton({ disabled, onRemove }: { disabled: boolean; onRemove: () => void }) {
  const t = useCopy();
  return (
    <button
      className="acs-admin__btn acs-admin__btn--secondary site-admin-products__remove"
      type="button"
      aria-label={t("admin.remove")}
      disabled={disabled}
      onClick={() => {
        onRemove();
      }}
    >
      <CrossIcon />
    </button>
  );
}

// 规格值输入框（读屏标签 admin.option_value），显示与编辑内容语言下的名称。
function ValueInput({ text, content, describedBy, onChange }: { text: string; content: Language; describedBy: string | undefined; onChange: (text: string) => void }) {
  const t = useCopy();
  return (
    <input
      className="acs-admin__input"
      aria-label={t("admin.option_value")}
      value={text}
      lang={htmlLang(content)}
      aria-describedby={describedBy}
      onChange={(event: ChangeEvent<HTMLInputElement>) => {
        onChange(event.target.value);
      }}
    />
  );
}

// 规格区：admin.product_options 一组，每个规格名一行（admin.option_name 输入框、admin.option_values 与各规格值输入框，读屏标签
// admin.option_value），都显示与编辑内容语言下的名称；已有的规格名与规格值只能改名称，没有 (✗)；新增的值排在已有的之后、各带 (✗)
// （被新增 SKU 选用或只剩一个时禁用）；每个规格名的值后为 admin.add_value（达 20 个禁用）；其下常显 admin.options_fixed_note。
// 新建表单（SHOP-TASK-206，UX A04 线框「新建商品」一行与视觉稿 A04-desktop-new）：没有规格名时先为 admin.no_options；新增的规格名在
// admin.add_value 之后带 (✗)（它的值被选用时禁用）；规格名之后为 admin.add_option（达 3 个禁用）。
function OptionsGroup({ editor, creating, describedBy, handlers }: { editor: FormEditor; creating: boolean; describedBy: string | undefined; handlers: InputHandlers }) {
  const t = useCopy();
  const { content, form } = editor;
  return (
    <div className="site-admin-products__options">
      <span>{t("admin.product_options")}</span>
      {creating && form.options.length === 0 && <span className="acs-admin__muted">{t("admin.no_options")}</span>}
      {form.options.map((option, optionIndex) => (
        <div key={optionKey(option)} className="site-admin-products__option">
          <label className="site-admin-products__option-name">
            <span>{t("admin.option_name")}</span>
            <input
              className="acs-admin__input"
              value={option.names[content]}
              lang={htmlLang(content)}
              aria-describedby={describedBy}
              onChange={(event: ChangeEvent<HTMLInputElement>) => {
                handlers.onOptionName(optionIndex, event.target.value);
              }}
            />
          </label>
          <span className="acs-admin__muted">{t("admin.option_values")}</span>
          <div className="site-admin-products__values">
            {option.values.map((value, valueIndex) => {
              const onChange = (text: string) => {
                handlers.onValueName(optionIndex, valueIndex, text);
              };
              return isNewValue(value) ? (
                <span key={value.tempId} className="site-admin-products__new-value">
                  <ValueInput text={value.names[content]} content={content} describedBy={describedBy} onChange={onChange} />
                  <RemoveButton
                    disabled={!valueRemovable(form, optionIndex, valueIndex)}
                    onRemove={() => {
                      handlers.onRemoveValue(optionIndex, valueIndex);
                    }}
                  />
                </span>
              ) : (
                <ValueInput key={value.id} text={value.names[content]} content={content} describedBy={describedBy} onChange={onChange} />
              );
            })}
          </div>
          <button
            className="acs-admin__btn acs-admin__btn--secondary site-admin-products__add-value"
            type="button"
            disabled={!canAddValue(option)}
            onClick={() => {
              handlers.onAddValue(optionIndex);
            }}
          >
            {t("admin.add_value")}
          </button>
          {isNewOption(option) && (
            <RemoveButton
              disabled={!optionRemovable(form, optionIndex)}
              onRemove={() => {
                handlers.onRemoveOption(optionIndex);
              }}
            />
          )}
        </div>
      ))}
      {creating && (
        <button
          className="acs-admin__btn acs-admin__btn--secondary site-admin-products__add-option"
          type="button"
          disabled={!canAddOption(form)}
          onClick={() => {
            handlers.onAddOption();
          }}
        >
          {t("admin.add_option")}
        </button>
      )}
      <span className="acs-admin__muted">{t("admin.options_fixed_note")}</span>
    </div>
  );
}

// 新增 SKU 每个规格名一个下拉框（读屏标签 admin.option_value），选项照 valueChoices。
function ValueSelects({ row, handlers }: { row: NewSkuRow; handlers: InputHandlers }) {
  const t = useCopy();
  return (
    <div className="site-admin-products__choices">
      {row.selects.map((select, optionIndex) => (
        <select
          key={optionIndex}
          className="acs-admin__select"
          aria-label={t("admin.option_value")}
          value={select.selected}
          onChange={(event: ChangeEvent<HTMLSelectElement>) => {
            handlers.onChooseValue(row.index, optionIndex, event.target.value);
          }}
        >
          {select.choices.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      ))}
    </div>
  );
}

// admin.add_sku（桌面在表格之下、手机在卡片之下，各渲染一个）：没有规格名的商品不显示，达 50 个 SKU 禁用。
function AddSkuButton({ form, onAddSku }: { form: ProductForm; onAddSku: () => void }) {
  const t = useCopy();
  if (!showsAddSku(form)) {
    return null;
  }
  return (
    <button
      className="acs-admin__btn acs-admin__btn--secondary site-admin-products__add-sku"
      type="button"
      disabled={!canAddSku(form)}
      onClick={() => {
        onAddSku();
      }}
    >
      {t("admin.add_sku")}
    </button>
  );
}

// 桌面的 SKU 表格：admin.sku、admin.variant、admin.price、admin.initial_stock、admin.sku_enabled、admin.available_today 六列；
// 价格与每日初始库存为输入框（读屏标签为列名），启用为勾选框，当日可用库存只显示。新增的 SKU 排在表格末尾（SHOP-TASK-205）：admin.sku_code_on_save、
// 每个规格名一个下拉框、价格与每日初始库存输入框、启用勾选框、当日可用库存 — 与 (✗)；有新增行时多一列（列头为空，已有行该格为空）放 (✗)。
// 表格之下为 admin.add_sku。新建表单（SHOP-TASK-206）的行全是新增的；没有规格名时那一行的规格列为 —、没有 (✗)，表格也不多那一列；
// 有规格名时只剩一行的 (✗) 禁用。
function SkuTable({ form, rows, newRows, handlers }: { form: ProductForm; rows: readonly SkuRow[]; newRows: readonly NewSkuRow[]; handlers: InputHandlers }) {
  const t = useCopy();
  const extra = newRows.length > 0 && form.options.length > 0;
  return (
    <div className="site-admin-products__sku-table">
      <div className="site-admin-products__skus">
        <table className="acs-admin__table">
          <thead>
            <tr>
              <th>{t("admin.sku")}</th>
              <th>{t("admin.variant")}</th>
              <th>{t("admin.price")}</th>
              <th>{t("admin.initial_stock")}</th>
              <th>{t("admin.sku_enabled")}</th>
              <th>{t("admin.available_today")}</th>
              {extra && <th />}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.form.id}>
                <td className="site-admin-products__sku">{row.sku}</td>
                <td>{row.variant}</td>
                <SkuCells index={row.index} form={row.form} handlers={handlers} />
                <td>{row.available}</td>
                {extra && <td />}
              </tr>
            ))}
            {newRows.map((row) => (
              <tr key={row.form.tempId}>
                <td className="acs-admin__muted site-admin-products__sku">{t("admin.sku_code_on_save")}</td>
                <td>{row.selects.length > 0 ? <ValueSelects row={row} handlers={handlers} /> : "—"}</td>
                <SkuCells index={row.index} form={row.form} handlers={handlers} />
                <td>—</td>
                {extra && (
                  <td>
                    <RemoveButton
                      disabled={!skuRemovable(form, row.index)}
                      onRemove={() => {
                        handlers.onRemoveSku(row.index);
                      }}
                    />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <AddSkuButton form={form} onAddSku={handlers.onAddSku} />
    </div>
  );
}

// 表格一行的价格与每日初始库存输入框（读屏标签为列名）与启用勾选框，已有的与新增的 SKU 共用；index 为在 form.variants 里的位置。
function SkuCells({ index, form, handlers }: { index: number; form: SkuForm; handlers: InputHandlers }) {
  const t = useCopy();
  return (
    <>
      <td>
        <input
          className="acs-admin__input site-admin-products__price"
          aria-label={t("admin.price")}
          inputMode="decimal"
          value={form.price}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            handlers.onPrice(index, event.target.value);
          }}
        />
      </td>
      <td>
        <input
          className="acs-admin__input site-admin-products__stock"
          aria-label={t("admin.initial_stock")}
          inputMode="numeric"
          value={form.stock}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            handlers.onStock(index, event.target.value);
          }}
        />
      </td>
      <td>
        <input
          className="site-admin-products__check"
          type="checkbox"
          aria-label={t("admin.sku_enabled")}
          checked={form.isActive}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            handlers.onVariantActive(index, event.target.checked);
          }}
        />
      </td>
    </>
  );
}

// 卡片里的价格与每日初始库存输入框（可见标签，两栏）与启用勾选框，已有的与新增的 SKU 共用。
function CardInputs({ index, form, handlers }: { index: number; form: SkuForm; handlers: InputHandlers }) {
  const t = useCopy();
  return (
    <>
      <div className="site-admin-products__sku-inputs">
        <label className="site-admin-products__field">
          <span>{t("admin.price")}</span>
          <input
            className="acs-admin__input"
            inputMode="decimal"
            value={form.price}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handlers.onPrice(index, event.target.value);
            }}
          />
        </label>
        <label className="site-admin-products__field">
          <span>{t("admin.initial_stock")}</span>
          <input
            className="acs-admin__input"
            inputMode="numeric"
            value={form.stock}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handlers.onStock(index, event.target.value);
            }}
          />
        </label>
      </div>
      <label className="site-admin-products__toggle">
        <input
          type="checkbox"
          checked={form.isActive}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            handlers.onVariantActive(index, event.target.checked);
          }}
        />
        {t("admin.sku_enabled")}
      </label>
    </>
  );
}

// 手机的 SKU 卡片：每个 SKU 一张，同样六项（SKU 代码、规格列、价格与每日初始库存输入框、启用勾选框、当日可用库存）。新增的 SKU 各一张排在其后
// （SHOP-TASK-205）：第一行为 admin.sku_code_on_save 与 (✗)，其下为规格值下拉框、价格与每日初始库存、启用与当日可用库存 —。卡片之下为 admin.add_sku。
// 新建表单没有规格名时那一张没有 (✗) 与下拉框（SHOP-TASK-206）。
function SkuCards({ form, rows, newRows, handlers }: { form: ProductForm; rows: readonly SkuRow[]; newRows: readonly NewSkuRow[]; handlers: InputHandlers }) {
  const t = useCopy();
  const removable = form.options.length > 0;
  return (
    <div className="site-admin-products__sku-list">
      <ul className="site-admin-products__sku-cards">
        {rows.map((row) => (
          <li key={row.form.id} className="acs-admin__panel site-admin-products__sku-card">
            <div className="site-admin-products__sku-row">
              <span>{t("admin.sku")}</span>
              <span>{row.sku}</span>
            </div>
            <div className="site-admin-products__sku-row">
              <span>{t("admin.variant")}</span>
              <span>{row.variant}</span>
            </div>
            <CardInputs index={row.index} form={row.form} handlers={handlers} />
            <div className="site-admin-products__sku-row">
              <span>{t("admin.available_today")}</span>
              <span>{row.available}</span>
            </div>
          </li>
        ))}
        {newRows.map((row) => (
          <li key={row.form.tempId} className="acs-admin__panel site-admin-products__sku-card">
            <div className="site-admin-products__sku-row site-admin-products__new-head">
              <span className="acs-admin__muted">{t("admin.sku_code_on_save")}</span>
              {removable && (
                <RemoveButton
                  disabled={!skuRemovable(form, row.index)}
                  onRemove={() => {
                    handlers.onRemoveSku(row.index);
                  }}
                />
              )}
            </div>
            {row.selects.length > 0 && <ValueSelects row={row} handlers={handlers} />}
            <CardInputs index={row.index} form={row.form} handlers={handlers} />
            <div className="site-admin-products__sku-row">
              <span>{t("admin.available_today")}</span>
              <span>—</span>
            </div>
          </li>
        ))}
      </ul>
      <AddSkuButton form={form} onAddSku={handlers.onAddSku} />
    </div>
  );
}

// common.save 与其旁的保存结果（A04-states，照 A06 与 A10）：保存中禁用并标 aria-busy，图片操作进行中也禁用（SHOP-TASK-207）；
// admin.product_saved 与 admin.product_created 为 role="status"，其他为 AdminAlert；admin.product_changed 带次要按钮 admin.reload。
function SaveArea({ editor, imagesBusy, onReload }: { editor: FormEditor; imagesBusy: boolean } & Pick<SaveHandlers, "onReload">) {
  const t = useCopy();
  const { notice } = editor;
  const done = notice === "admin.product_saved" || notice === "admin.product_created";
  return (
    <div className="site-admin-products__save">
      <button className="acs-admin__btn" type="submit" disabled={editor.saving || imagesBusy} aria-busy={editor.saving}>
        {t("common.save")}
      </button>
      {notice !== null && done && <span role="status">{t(notice)}</span>}
      {notice === "admin.product_changed" && (
        <div className="acs-admin__alert site-admin-products__changed" role="alert">
          <span>{t("admin.product_changed")}</span>
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
      {notice !== null && !done && notice !== "admin.product_changed" && <AdminAlert error={notice} />}
    </div>
  );
}

// 已取到的编辑区（照 A04-desktop 与 A04-phone-edit 的顺序）：标题 admin.product_edit、内容语言三个按钮（aria-pressed）、商品名称与描述
// （多行）、缺译提示（至多一条）、分类下拉与 admin.category_current_unavailable、上架勾选框、常显的 admin.publish_requirements、规格区、
// 桌面的 SKU 表格与 admin.add_sku、每单限购与 admin.max_per_order_hint、手机的 SKU 卡片与 admin.add_sku、admin.no_delete_note、admin.initial_stock_note 与 admin.rules_apply_new，
// 最后为 common.save 与保存结果。桌面的表格在限购之前、手机的卡片在限购之后（两套都渲染，由 site.css 按宽度显隐）。
// 新建表单（SHOP-TASK-206，视觉稿 A04-desktop-new）标题为 admin.product_new，分类下拉只有可选的分类（照 newCategoryChoices），不显示
// admin.category_current_unavailable，规格区多 admin.no_options 与 admin.add_option，SKU 全是新增行（不显示 slug、SKU 代码与当日可用库存的值）。
// 图片区（SHOP-TASK-207，视觉稿 A04-desktop 与 A04-desktop-new）在 admin.publish_requirements 之后、规格区之前；以商品 ID 作 key（新建表单为
// new），换了商品即重新挂载；新建表单没有商品 ID 与图片。
function EditorForm({
  editor,
  categories,
  item,
  context,
  handlers,
}: {
  editor: OpenEditor | NewEditor;
  categories: readonly AdminCategory[];
  item: AdminProductListItem | undefined;
  context: ImageContext;
  handlers: InputHandlers & SaveHandlers & ImageHandlers;
}) {
  const t = useCopy();
  const { language } = useLanguage();
  const languagesId = useId();
  const missingId = useId();
  const unavailableId = useId();
  const limitHintId = useId();
  const { content, form } = editor;
  const creating = editor.status === "new";
  const missing = translationMissing(editor);
  const describedBy = missing ? missingId : undefined;
  const unavailable = editor.status === "ready" && categoryUnavailable(categories, form.categoryId);
  const choices = editor.status === "ready" ? categoryChoices(categories, editor.product, item, language) : newCategoryChoices(categories, language);
  const rows = editor.status === "ready" ? skuRows(editor, language) : [];
  const newRows = newSkuRows(editor, language);
  const productId = editor.status === "ready" ? editor.id : null;
  return (
    <form
      className="acs-admin__panel site-admin-products__form"
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        handlers.onSave();
      }}
    >
      <h2 className="acs-admin__h">{t(creating ? "admin.product_new" : "admin.product_edit")}</h2>
      <div className="site-admin-products__languages" role="group" aria-labelledby={languagesId}>
        <span id={languagesId} className="acs-admin__muted">
          {t("admin.content_language")}
        </span>
        {LANGUAGES.map((option) => (
          <button
            key={option}
            className={option === content ? "acs-admin__btn" : "acs-admin__btn acs-admin__btn--secondary"}
            type="button"
            lang={htmlLang(option)}
            aria-pressed={option === content}
            onClick={() => {
              handlers.onContent(option);
            }}
          >
            {t(LANGUAGE_LABEL[option])}
          </button>
        ))}
      </div>
      <label className="site-admin-products__field">
        <span>{t("admin.product_name")}</span>
        <input
          className="acs-admin__input"
          value={form.names[content]}
          lang={htmlLang(content)}
          aria-describedby={describedBy}
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            handlers.onName(event.target.value);
          }}
        />
      </label>
      <label className="site-admin-products__field">
        <span>{t("admin.product_description")}</span>
        <textarea
          className="acs-admin__input site-admin-products__description"
          rows={2}
          value={form.descriptions[content]}
          lang={htmlLang(content)}
          aria-describedby={describedBy}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => {
            handlers.onDescription(event.target.value);
          }}
        />
      </label>
      {missing && (
        <p id={missingId} className="site-admin-products__missing">
          {t("admin.translation_missing", { language: t(LANGUAGE_LABEL[content]) })}
        </p>
      )}
      <div className="site-admin-products__placement">
        <label className="site-admin-products__field site-admin-products__category">
          <span>{t("admin.product_category")}</span>
          <select
            className="acs-admin__select"
            value={String(form.categoryId)}
            aria-describedby={unavailable ? unavailableId : undefined}
            onChange={(event: ChangeEvent<HTMLSelectElement>) => {
              handlers.onCategory(Number(event.target.value));
            }}
          >
            {choices.map((choice) => (
              <option key={choice.id} value={String(choice.id)}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        {unavailable && (
          <p id={unavailableId} className="site-admin-products__unavailable">
            {t("admin.category_current_unavailable")}
          </p>
        )}
        <label className="site-admin-products__toggle site-admin-products__active">
          <input
            type="checkbox"
            checked={form.isActive}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handlers.onActive(event.target.checked);
            }}
          />
          {t("admin.active")}
        </label>
      </div>
      <span className="acs-admin__muted">{t("admin.publish_requirements")}</span>
      <AdminProductImages
        key={productId ?? "new"}
        productId={productId}
        images={editor.status === "ready" ? editor.product.images : NO_IMAGES}
        csrfToken={context.csrfToken}
        locked={editor.saving}
        onImages={(images) => {
          if (productId !== null) {
            handlers.onImages(productId, images);
          }
        }}
        onReread={handlers.onReread}
        onLogin={handlers.onLogin}
        onBusy={handlers.onImagesBusy}
      />
      <OptionsGroup editor={editor} creating={creating} describedBy={describedBy} handlers={handlers} />
      <SkuTable form={form} rows={rows} newRows={newRows} handlers={handlers} />
      <div className="site-admin-products__limit">
        <label className="site-admin-products__limit-field">
          <span>{t("admin.max_per_order")}</span>
          <input
            className="acs-admin__input"
            inputMode="numeric"
            value={form.maxPerOrder}
            aria-describedby={limitHintId}
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              handlers.onMaxPerOrder(event.target.value);
            }}
          />
        </label>
        <span id={limitHintId} className="acs-admin__muted">
          {t("admin.max_per_order_hint")}
        </span>
      </div>
      <SkuCards form={form} rows={rows} newRows={newRows} handlers={handlers} />
      <span className="acs-admin__muted">{t("admin.no_delete_note")}</span>
      <div className="site-admin-products__notes">
        <span>{t("admin.initial_stock_note")}</span>
        <span>{t("admin.rules_apply_new")}</span>
      </div>
      <SaveArea editor={editor} imagesBusy={context.imagesBusy} onReload={handlers.onReload} />
    </form>
  );
}

// 编辑区的位置：手机上方为 common.back（桌面由 site.css 隐藏）；读取中不显示内容（标 aria-busy），读取失败只有提示，取到后与新建表单为 EditorForm。
function EditorView({
  editing,
  categories,
  item,
  context,
  onBack,
  ...handlers
}: { editing: Editing; categories: readonly AdminCategory[]; item: AdminProductListItem | undefined; context: ImageContext } & EditorHandlers) {
  const t = useCopy();
  return (
    <div className="site-admin-products__editor" aria-busy={editing.status === "loading"}>
      <button className="acs-admin__btn acs-admin__btn--secondary site-admin-products__back" type="button" onClick={onBack}>
        {t("common.back")}
      </button>
      {editing.status === "failed" && <AdminAlert error={editing.error} />}
      {(editing.status === "ready" || editing.status === "new") && <EditorForm editor={editing} categories={categories} item={item} context={context} handlers={handlers} />}
    </div>
  );
}

export interface AdminProductsViewProps extends ListHandlers, EditorHandlers {
  state: ProductsState;
  // 标题旁的 common.create（SHOP-TASK-206）。
  onCreate: () => void;
  // 图片操作进行中（SHOP-TASK-207）：common.save 禁用。
  imagesBusy: boolean;
}

// 标题与 common.create（列表取到后可点）之下为列表区：读取中标 aria-busy；读取失败时只有提示。点了 common.edit 或 common.create 之后
// 根元素加 --editing（桌面两栏，手机隐藏列表）。
export function AdminProductsView({ state, imagesBusy, onEdit, onCreate, ...handlers }: AdminProductsViewProps) {
  const t = useCopy();
  const editing = state.status === "ready" ? state.editing : null;
  return (
    <div className={editing === null ? "site-admin-products" : "site-admin-products site-admin-products--editing"}>
      <div className="site-admin-products__list">
        <div className="site-admin-products__title">
          <h1 className="acs-admin__h">{t("admin.nav_products")}</h1>
          <button
            className="acs-admin__btn"
            type="button"
            disabled={state.status !== "ready"}
            onClick={() => {
              onCreate();
            }}
          >
            {t("common.create")}
          </button>
        </div>
        <div className="site-admin-products__body" aria-busy={state.status === "loading"}>
          {state.status === "failed" && <AdminAlert error={state.error} />}
          {state.status === "ready" && <ProductList products={state.products} onEdit={onEdit} />}
        </div>
      </div>
      {state.status === "ready" && editing !== null && (
        <EditorView
          editing={editing}
          categories={state.categories}
          item={editing.status === "new" ? undefined : state.products.find((product) => product.id === editing.id)}
          context={{ csrfToken: state.csrfToken, imagesBusy }}
          {...handlers}
        />
      )}
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载时读取一次（切换界面语言不重新读取）。
export function AdminProductsContent() {
  const { replace } = useRouter();
  const [state, setState] = useState<ProductsState>(LOADING);
  // 单件读取（含 admin.reload）的中止：又点了另一行、点了 common.back、又点了 admin.reload 或离开页面时中止前一次。
  const productRequest = useRef<AbortController | null>(null);
  // 打开页面与保存（含 403 后的重新读取）共用的中止；离开页面时中止。保存的结果只改仍在保存的同一件（withSavingEditor）。
  const lifetime = useRef<AbortController | null>(null);
  // 图片操作进行中（SHOP-TASK-207）：由图片区经 onImagesBusy 设定，为真时 common.save 禁用、不提交。
  const [imagesBusy, setImagesBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openProducts(controller.signal, pageMoves(setState, replace));
    });
  }, [replace]);

  useEffect(
    () => () => {
      productRequest.current?.abort();
    },
    [],
  );

  const confirmLeave = useUnsavedConfirm(isDirty(state.status === "ready" ? state.editing : null));

  // 只改已取到的编辑区或新建表单。
  const edit = (change: (editor: OpenEditor | NewEditor) => OpenEditor | NewEditor) => {
    setState((current) =>
      current.status === "ready" && current.editing !== null && (current.editing.status === "ready" || current.editing.status === "new")
        ? { ...current, editing: change(current.editing) }
        : current,
    );
  };

  return (
    <AdminProductsView
      state={state}
      imagesBusy={imagesBusy}
      onImages={(id, images) => {
        setState((current) => withImages(current, id, images));
      }}
      onReread={(signal) => rereadToken(signal, pageMoves(setState, replace))}
      onLogin={() => {
        replace(ADMIN_LOGIN_PATH);
      }}
      onImagesBusy={setImagesBusy}
      onCreate={() => {
        if (state.status !== "ready") {
          return;
        }
        // 先经未保存确认（UX A04「列表」）；新建表单已打开时也换成空白的。
        confirmLeave(() => {
          productRequest.current?.abort();
          productRequest.current = null;
          setState((current) => startCreating(current) ?? current);
        });
      }}
      onEdit={(item) => {
        // 点的正是已打开或正在读取的那一件：不做任何事，不经确认。
        if (startEditing(state, item.id) === null) {
          return;
        }
        confirmLeave(() => {
          productRequest.current?.abort();
          const controller = new AbortController();
          productRequest.current = controller;
          setState((current) => startEditing(current, item.id) ?? current);
          void openProduct(item.id, controller.signal, pageMoves(setState, replace));
        });
      }}
      onBack={() => {
        confirmLeave(() => {
          productRequest.current?.abort();
          productRequest.current = null;
          setState(closeEditing);
        });
      }}
      onContent={(content) => {
        edit((current) => chooseContent(current, content));
      }}
      onName={(value) => {
        edit((current) => changeName(current, value));
      }}
      onDescription={(value) => {
        edit((current) => changeDescription(current, value));
      }}
      onCategory={(categoryId) => {
        edit((current) => changeCategory(current, categoryId));
      }}
      onActive={(checked) => {
        edit((current) => changeActive(current, checked));
      }}
      onMaxPerOrder={(text) => {
        edit((current) => changeMaxPerOrder(current, text));
      }}
      onOptionName={(optionIndex, value) => {
        edit((current) => changeOptionName(current, optionIndex, value));
      }}
      onValueName={(optionIndex, valueIndex, value) => {
        edit((current) => changeValueName(current, optionIndex, valueIndex, value));
      }}
      onPrice={(variantIndex, text) => {
        edit((current) => changePrice(current, variantIndex, text));
      }}
      onStock={(variantIndex, text) => {
        edit((current) => changeStock(current, variantIndex, text));
      }}
      onVariantActive={(variantIndex, checked) => {
        edit((current) => changeVariantActive(current, variantIndex, checked));
      }}
      onAddValue={(optionIndex) => {
        edit((current) => addValue(current, optionIndex));
      }}
      onRemoveValue={(optionIndex, valueIndex) => {
        edit((current) => removeValue(current, optionIndex, valueIndex));
      }}
      onAddSku={() => {
        edit(addSku);
      }}
      onRemoveSku={(variantIndex) => {
        edit((current) => removeSku(current, variantIndex));
      }}
      onChooseValue={(variantIndex, optionIndex, key) => {
        edit((current) => chooseValue(current, variantIndex, optionIndex, key));
      }}
      onAddOption={() => {
        edit(addOption);
      }}
      onRemoveOption={(optionIndex) => {
        edit((current) => removeOption(current, optionIndex));
      }}
      onSave={() => {
        const controller = lifetime.current;
        // 图片操作进行中不保存（两者互斥，SHOP-TASK-207）。
        if (state.status !== "ready" || state.editing === null || controller === null || imagesBusy) {
          return;
        }
        const editor = state.editing;
        if (editor.status === "ready" && !editor.saving) {
          edit(startSaving);
          void submitProduct(state.csrfToken, editor, controller.signal, pageMoves(setState, replace));
        } else if (editor.status === "new" && !editor.saving) {
          edit(startSaving);
          void submitNewProduct(state.csrfToken, editor, state.categories, controller.signal, pageMoves(setState, replace));
        }
      }}
      onReload={() => {
        if (state.status !== "ready" || state.editing === null) {
          return;
        }
        const editor = state.editing;
        if ((editor.status !== "ready" && editor.status !== "new") || editor.saving) {
          return;
        }
        // 明确放弃修改，不经确认（UX A04「点「已被改动」提示里的 [admin.reload] 是明确放弃修改，不再确认」）；新建表单恢复为空白。
        productRequest.current?.abort();
        const controller = new AbortController();
        productRequest.current = controller;
        if (editor.status === "ready") {
          void reloadProducts(editor.id, controller.signal, pageMoves(setState, replace));
        } else {
          void reloadNewProduct(controller.signal, pageMoves(setState, replace));
        }
      }}
    />
  );
}

export default function AdminProductsPage() {
  return (
    <AdminFrame current="products">
      <AdminProductsContent />
    </AdminFrame>
  );
}
