import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, Dispatch, FormEvent, SetStateAction } from "react";

import { readAdminShippingRates, saveAdminShippingRates } from "../api/adminShippingRates";
import type {
  AddFxRate,
  AdminRates,
  AdminRatesSave,
  CountryFeeItem,
  FxRateItem,
  FxRateRow,
  KeepFee,
  ShippingRateRow,
  ShippingRatesRead,
  ShippingRatesSave,
} from "../api/adminShippingRates";
import { fetchRegions } from "../api/checkout";
import type { PhoneRegion } from "../api/checkout";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { startDeferred } from "./AdminOrderDetail";
import { countryNames, MALAYSIA, regionOptions } from "./checkoutPhone";
import type { RegionOption } from "./checkoutPhone";

// A06 后台运费区与演示汇率（docs/UX.md 0.16 A06，视觉稿 A06-desktop、A06-phone 与 A06-desktop-states）：用后台框架渲染，当前导航项为运费与演示汇率。
// 内容区为 h1 admin.shipping_title，其下为整页表单：桌面左栏依次为马来西亚两档（admin.shipping_my_state）、按国家（admin.shipping_by_country）
// 与其他国家（admin.shipping_other），右栏为演示汇率（admin.fx_title）、admin.rules_apply_new、common.save 与保存的提示；手机为一列。
// 打开页面与切换界面语言时调用 GET /api/admin/shipping-rates，保存为 PUT（经 api/adminShippingRates.ts；取值范围、并发核对与审计在服务端，
// DESIGN 1.17「计价、优惠、积分与库存」第 9、10 条）。401 换成登录页 A01；离开页面或切换语言时中止读取与进行中的保存，旧请求的结果不再更新页面。
// 删除与新增行（SHOP-TASK-176，UX A06「增删」与 HANDOFF 0.72 第（6）到（8）点）：按国家与演示汇率两块的每行有 admin.remove，
// 两块末尾各有 admin.add_country 与 admin.add_fx；增删随整页保存提交（同一请求先删后增，DESIGN 1.17 第 9 条）。新增行的国家从结账页的
// 收货国家列表（GET /api/checkout/regions，每次整页读取时一起重新取得）里选。

// 马来西亚两档按区域代码取文字（UX A06 线框，西马在前由读取的顺序保证）。
export const MALAYSIA_LABELS: Readonly<Record<string, CopyKey>> = {
  "MY-W": "admin.shipping_my_west",
  "MY-E": "admin.shipping_my_east",
};

// 运费输入（HANDOFF 0.72 第（4）点）：一到九位数字，可再跟小数点与一到两位数字。
const FEE_PATTERN = /^(\d{1,9})(?:\.(\d{1,2}))?$/;
// 汇率输入：数字，可再跟小数点与数字。
const RATE_PATTERN = /^\d+(?:\.\d+)?$/;
// 新增汇率行的币种：三个字母（提交前转大写）；小数位：0 到 3 的一位数字（HANDOFF 0.72 第（4）点）。
const CURRENCY_PATTERN = /^[A-Za-z]{3}$/;
const DECIMALS_PATTERN = /^[0-3]$/;

// 运费文字换成整仙：整数部分乘 100 加补足两位的小数部分（不经浮点数相乘）；格式不对为 null。取值范围以服务端为准。
export function feeSen(text: string): number | null {
  const match = FEE_PATTERN.exec(text);
  if (match === null) {
    return null;
  }
  const [, whole = "", fraction = ""] = match;
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

// 运费输入框的初值：整仙用整数运算写成不带 RM 前缀的两位小数文字（0 为 0.00，5 为 0.05，800 为 8.00）。
export function feeText(sen: number): string {
  const cents = sen % 100;
  return `${String((sen - cents) / 100)}.${String(cents).padStart(2, "0")}`;
}

// 汇率输入的格式；合格时原样提交。
export function isRateText(text: string): boolean {
  return RATE_PATTERN.test(text);
}

// 新增的国家运费行：key 只在页面内区分新增行，country 为当前选中的国家代码，fee 为运费输入框的文字。
export interface NewCountryRow {
  key: number;
  country: string;
  fee: string;
}

// 新增的汇率行：当前选中的国家代码与币种、小数位、汇率三个输入框的文字。
export interface NewFxRow {
  key: number;
  country: string;
  currency: string;
  decimals: string;
  rate: string;
}

export type FxPart = "currency" | "decimals" | "rate";

// 表单的内容：各输入框的文字与 base 的各组逐行对应（被删的现有行仍占原位置，不显示），removedCountries 与 removedFx 为点了删除的
// 现有行的 id，newCountries 与 newFx 为新增的行（按新增的先后）。
export interface RatesInputs {
  malaysia: string[];
  other: string;
  countries: string[];
  fx: string[];
  removedCountries: number[];
  removedFx: number[];
  newCountries: NewCountryRow[];
  newFx: NewFxRow[];
}

// 一个输入框的位置：组与组内第几行（其他国家只有一行），或新增行的 key（新增汇率行另有是哪个输入框）。
export type RatesField =
  | { group: "malaysia" | "countries" | "fx"; index: number }
  | { group: "other" }
  | { group: "newCountries"; key: number }
  | { group: "newFx"; key: number; part: FxPart };

// 可增删的两块：按国家与演示汇率。
export type RowGroup = "countries" | "fx";

// 新增的一行（按 key）。
export interface NewRowTarget {
  group: RowGroup;
  key: number;
}

// 要删除的一行：现有的行按读取时的 id，新增的行按 key。
export type RowTarget = { group: RowGroup; id: number } | NewRowTarget;

// 保存按钮下方的提示：成功（role 为 status）、取值不合格、数据已被改动（带 admin.reload）与其他失败。
export type SaveNotice = "admin.rates_saved" | "admin.rates_invalid" | "admin.rates_changed" | "common.error_retry" | "common.network_check";

// 页面的状态：读取中（标 aria-busy，不显示表单）、读取失败（只有提示）、已取到。已取到时 base 为读取（或保存返回）的整页数据，
// csrfToken 为读取给的 CSRF 令牌（只留在页面内存里，保存成功后沿用），inputs 为表单的内容，saving 为保存进行中，
// notice 为保存按钮下方的提示，alert 为表单上方的提示（403 后重新读取，不随改值收起），regions 为与整页一起读取的收货国家列表
// （读取失败为 null，两个新增按钮因此禁用；保存成功后沿用）。
export type ShippingState =
  | { status: "loading" }
  | { status: "failed"; error: CopyKey }
  | {
      status: "ready";
      base: AdminRates;
      csrfToken: string;
      inputs: RatesInputs;
      saving: boolean;
      notice: SaveNotice | null;
      alert: CopyKey | null;
      regions: readonly PhoneRegion[] | null;
    };

export const LOADING: ShippingState = { status: "loading" };

type ReadyState = Extract<ShippingState, { status: "ready" }>;

// 输入框的初值：运费为 feeText，汇率为读取的 rate；没有删除与新增的行。
export function initialInputs(base: AdminRates): RatesInputs {
  return {
    malaysia: base.malaysia.map((row) => feeText(row.fee_sen)),
    other: feeText(base.other.fee_sen),
    countries: base.countries.map((row) => feeText(row.fee_sen)),
    fx: base.fx.map((row) => row.rate),
    removedCountries: [],
    removedFx: [],
    newCountries: [],
    newFx: [],
  };
}

function ready(
  base: AdminRates,
  csrfToken: string,
  alert: CopyKey | null,
  notice: SaveNotice | null,
  regions: readonly PhoneRegion[] | null,
): ShippingState {
  return { status: "ready", base, csrfToken, inputs: initialInputs(base), saving: false, notice, alert, regions };
}

// 按读取结果决定：取到以结果填表（alert 为表单上方的提示，regions 为一起读取的收货国家列表）；401 回到 A01（login）；
// 网络中断 common.network_check，其他失败 common.error_retry，都不显示表单。
export function readStep(read: ShippingRatesRead, alert: CopyKey | null = null, regions: readonly PhoneRegion[] | null = null): ShippingState | "login" {
  switch (read.kind) {
    case "ok":
      return ready(read.rates, read.csrf_token, alert, null, regions);
    case "none":
      return "login";
    case "network":
      return { status: "failed", error: "common.network_check" };
    case "failed":
      return { status: "failed", error: "common.error_retry" };
  }
}

// 一组运费行按逐行对应的文字写成保留的运费行；任一文字格式不对为 null。
function keptFees(rows: readonly ShippingRateRow[], texts: readonly string[]): KeepFee[] | null {
  const kept: KeepFee[] = [];
  for (const [index, row] of rows.entries()) {
    const sen = feeSen(texts[index] ?? "");
    if (sen === null) {
      return null;
    }
    kept.push({ id: row.id, version: row.version, fee_sen: sen });
  }
  return kept;
}

// 国家运费一组：现有的行按读取的顺序，被删的为删除的行，其余为保留的运费行；新增的行排在末尾，为新增的国家运费行。
// 页面上任一运费文字格式不对为 null（被删的行不在页面上，不查）。
function countryItems(base: AdminRates, inputs: RatesInputs): CountryFeeItem[] | null {
  const items: CountryFeeItem[] = [];
  for (const [index, row] of base.countries.entries()) {
    if (inputs.removedCountries.includes(row.id)) {
      items.push({ id: row.id, version: row.version, delete: true });
    } else {
      const sen = feeSen(inputs.countries[index] ?? "");
      if (sen === null) {
        return null;
      }
      items.push({ id: row.id, version: row.version, fee_sen: sen });
    }
  }
  for (const row of inputs.newCountries) {
    const sen = feeSen(row.fee);
    if (sen === null) {
      return null;
    }
    items.push({ country_code: row.country, fee_sen: sen });
  }
  return items;
}

// 一行新增的汇率：币种为三个字母（转大写）、小数位为 0 到 3 的一位数字（换成整数）、汇率合 isRateText（原样）；任一不对为 null。
export function newFxItem(row: NewFxRow): AddFxRate | null {
  if (!CURRENCY_PATTERN.test(row.currency) || !DECIMALS_PATTERN.test(row.decimals) || !isRateText(row.rate)) {
    return null;
  }
  return { country_code: row.country, currency_code: row.currency.toUpperCase(), currency_decimals: Number(row.decimals), rate: row.rate };
}

// 汇率一组：现有的行按读取的顺序，被删的为删除的行，其余为保留的汇率行（汇率原样提交）；新增的行排在末尾。任一格式不对为 null。
function fxItems(base: AdminRates, inputs: RatesInputs): FxRateItem[] | null {
  const items: FxRateItem[] = [];
  for (const [index, row] of base.fx.entries()) {
    if (inputs.removedFx.includes(row.id)) {
      items.push({ id: row.id, version: row.version, delete: true });
    } else {
      const rate = inputs.fx[index] ?? "";
      if (!isRateText(rate)) {
        return null;
      }
      items.push({ id: row.id, version: row.version, rate });
    }
  }
  for (const row of inputs.newFx) {
    const item = newFxItem(row);
    if (item === null) {
      return null;
    }
    items.push(item);
  }
  return items;
}

// 保存内容：按页面的顺序列出现有的行，马来西亚两档、其他国家与每个国家为保留的运费行（运费按文字换成整仙），每个汇率为保留的汇率行
// （汇率原样提交），都带读取时的 id 与 version；被删的国家与汇率以删除的行列在原位置，新增的行排在该组末尾（新增后又删掉的行不在内）。
// 任一输入框格式不对为 null。
export function saveContent(base: AdminRates, inputs: RatesInputs): AdminRatesSave | null {
  const malaysia = keptFees(base.malaysia, inputs.malaysia);
  const other = keptFees([base.other], [inputs.other])?.[0];
  const countries = countryItems(base, inputs);
  const fx = fxItems(base, inputs);
  if (malaysia === null || other === undefined || countries === null || fx === null) {
    return null;
  }
  return { malaysia, other, countries, fx };
}

// 对当前状态的一次改动（保存的结果按保存时的状态更新，不覆盖保存进行中的其他状态）。
export type ShippingChange = (current: ShippingState) => ShippingState;

function failedSave(notice: SaveNotice): ShippingChange {
  return (current) => (current.status === "ready" ? { ...current, saving: false, notice } : current);
}

// 按保存结果决定：200 以返回的整页数据替换页面（输入框改为返回的值，新增的行成为现有的行、删除的行消失，CSRF 令牌沿用 csrfToken，
// 收货国家列表沿用保存时的）并显示 admin.rates_saved；401 回到 A01；
// 403 重新读取（reread）；取值不合格 admin.rates_invalid、数据已被改动 admin.rates_changed、网络中断 common.network_check、
// 其他失败 common.error_retry，都保留已填的值。
export function saveStep(save: ShippingRatesSave, csrfToken: string): ShippingChange | "login" | "reread" {
  switch (save.kind) {
    case "ok":
      return (current) => ready(save.rates, csrfToken, null, "admin.rates_saved", current.status === "ready" ? current.regions : null);
    case "none":
      return "login";
    case "csrf":
      return "reread";
    case "invalid":
      return failedSave("admin.rates_invalid");
    case "changed":
      return failedSave("admin.rates_changed");
    case "network":
      return failedSave("common.network_check");
    case "failed":
      return failedSave("common.error_retry");
  }
}

function replaced(values: readonly string[], index: number, value: string): string[] {
  return values.map((current, at) => (at === index ? value : current));
}

function withInput(inputs: RatesInputs, field: RatesField, value: string): RatesInputs {
  switch (field.group) {
    case "other":
      return { ...inputs, other: value };
    case "malaysia":
      return { ...inputs, malaysia: replaced(inputs.malaysia, field.index, value) };
    case "countries":
      return { ...inputs, countries: replaced(inputs.countries, field.index, value) };
    case "fx":
      return { ...inputs, fx: replaced(inputs.fx, field.index, value) };
    case "newCountries":
      return { ...inputs, newCountries: inputs.newCountries.map((row) => (row.key === field.key ? { ...row, fee: value } : row)) };
    case "newFx":
      return { ...inputs, newFx: inputs.newFx.map((row) => (row.key === field.key ? withFxPart(row, field.part, value) : row)) };
  }
}

function withFxPart(row: NewFxRow, part: FxPart, value: string): NewFxRow {
  switch (part) {
    case "currency":
      return { ...row, currency: value };
    case "decimals":
      return { ...row, decimals: value };
    case "rate":
      return { ...row, rate: value };
  }
}

// 改一个输入框：更新它的文字，收起保存按钮下方的全部提示；表单上方 403 后的提示不收起。
export function changeInput(state: ShippingState, field: RatesField, value: string): ShippingState {
  return state.status === "ready" ? { ...state, inputs: withInput(state.inputs, field, value), notice: null } : state;
}

// 一块里页面上现有的国家：没有被删的现有行与全部新增行当前选中的国家。
export function pageCountries(base: AdminRates, inputs: RatesInputs, group: RowGroup): string[] {
  if (group === "countries") {
    const kept = base.countries.filter((row) => !inputs.removedCountries.includes(row.id)).map((row) => row.zone_code);
    return [...kept, ...inputs.newCountries.map((row) => row.country)];
  }
  const kept = base.fx.filter((row) => !inputs.removedFx.includes(row.id)).map((row) => row.country_code);
  return [...kept, ...inputs.newFx.map((row) => row.country)];
}

// 国家下拉的选项（HANDOFF 0.72 第（7）点）：收货国家列表经 regionOptions 按名称排序，不含马来西亚与 taken 里的国家；own（该行自己选中的国家）
// 仍在选项里。
export function countryOptions(regions: readonly PhoneRegion[], language: Language, taken: readonly string[], own: string | null = null): RegionOption[] {
  return regionOptions(regions, language).filter((option) => option.code !== MALAYSIA && (option.code === own || !taken.includes(option.code)));
}

// 新增按钮可加的国家：收货国家列表读取失败时没有；否则为不含该块页面上现有国家的选项。没有时新增按钮禁用。
export function addChoices(state: ReadyState, group: RowGroup, language: Language): RegionOption[] {
  return state.regions === null ? [] : countryOptions(state.regions, language, pageCountries(state.base, state.inputs, group));
}

function nextKey(rows: readonly { key: number }[]): number {
  return rows.reduce((next, row) => Math.max(next, row.key + 1), 0);
}

// 点 admin.add_country 或 admin.add_fx：在该块末尾加一行，国家默认选中第一个可选国家，输入框空白；收起保存按钮下方的全部提示。
// 没有可选国家（含收货国家列表读取失败）时不变。
export function addRow(state: ShippingState, group: RowGroup, language: Language): ShippingState {
  if (state.status !== "ready") {
    return state;
  }
  const first = addChoices(state, group, language)[0];
  if (first === undefined) {
    return state;
  }
  const { inputs } = state;
  const added: RatesInputs =
    group === "countries"
      ? { ...inputs, newCountries: [...inputs.newCountries, { key: nextKey(inputs.newCountries), country: first.code, fee: "" }] }
      : { ...inputs, newFx: [...inputs.newFx, { key: nextKey(inputs.newFx), country: first.code, currency: "", decimals: "", rate: "" }] };
  return { ...state, inputs: added, notice: null };
}

function withoutRow(inputs: RatesInputs, target: RowTarget): RatesInputs {
  if ("id" in target) {
    const removed = target.group === "countries" ? inputs.removedCountries : inputs.removedFx;
    const ids = removed.includes(target.id) ? removed : [...removed, target.id];
    return target.group === "countries" ? { ...inputs, removedCountries: ids } : { ...inputs, removedFx: ids };
  }
  return target.group === "countries"
    ? { ...inputs, newCountries: inputs.newCountries.filter((row) => row.key !== target.key) }
    : { ...inputs, newFx: inputs.newFx.filter((row) => row.key !== target.key) };
}

// 点 admin.remove：从页面去掉该行（现有的行记下 id，保存时为删除的行；新增的行直接丢弃），不弹确认框；收起保存按钮下方的全部提示。
export function removeRow(state: ShippingState, target: RowTarget): ShippingState {
  return state.status === "ready" ? { ...state, inputs: withoutRow(state.inputs, target), notice: null } : state;
}

// 改新增行的国家选择：更新该行选中的国家，收起保存按钮下方的全部提示。
export function selectCountry(state: ShippingState, target: NewRowTarget, code: string): ShippingState {
  if (state.status !== "ready") {
    return state;
  }
  const { inputs } = state;
  const selected: RatesInputs =
    target.group === "countries"
      ? { ...inputs, newCountries: inputs.newCountries.map((row) => (row.key === target.key ? { ...row, country: code } : row)) }
      : { ...inputs, newFx: inputs.newFx.map((row) => (row.key === target.key ? { ...row, country: code } : row)) };
  return { ...state, inputs: selected, notice: null };
}

// 点保存：按钮禁用并标 aria-busy，先前的提示都收起。
export function startSaving(state: ShippingState): ShippingState {
  return state.status === "ready" ? { ...state, saving: true, notice: null, alert: null } : state;
}

// 页面对结果的处理：换成某个状态、按当前状态改动，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface ShippingMoves {
  show: (state: ShippingState) => void;
  update: (change: ShippingChange) => void;
  replace: (path: RoutePath) => void;
}

// 收货国家列表（结账页的 fetchRegions）；读取失败为 null，只禁用两个新增按钮。
async function readRegions(signal: AbortSignal): Promise<PhoneRegion[] | null> {
  try {
    return (await fetchRegions(signal)).regions;
  } catch {
    return null;
  }
}

// 读取（打开页面、切换语言、admin.reload 与 403 之后）：整页数据与收货国家列表一起重新取得；signal 中止（离开页面或已切换语言）之后不再更新。
export async function openRates(signal: AbortSignal, moves: ShippingMoves, alert: CopyKey | null = null): Promise<void> {
  const [read, regions] = await Promise.all([readAdminShippingRates(signal), readRegions(signal)]);
  if (signal.aborted) {
    return;
  }
  const step = readStep(read, alert, regions);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

// 点 admin.reload：照打开页面重新读取（先显示读取中），未保存的修改（含增删）丢弃。
export async function reloadRates(signal: AbortSignal, moves: ShippingMoves): Promise<void> {
  moves.show(LOADING);
  await openRates(signal, moves);
}

// 保存整页：先检查全部输入框的格式，任一不对即不发请求、显示 admin.rates_invalid；合格时带读取给的 CSRF 令牌提交。
// signal 中止之后其结果不再更新表单。403 时与首次读取一样显示读取中，取得后以结果重置表单并在表单上方显示 common.error_retry。
export async function submitRates(state: ReadyState, signal: AbortSignal, moves: ShippingMoves): Promise<void> {
  const content = saveContent(state.base, state.inputs);
  if (content === null) {
    moves.update(failedSave("admin.rates_invalid"));
    return;
  }
  const save = await saveAdminShippingRates(content, state.csrfToken, signal);
  if (signal.aborted) {
    return;
  }
  const step = saveStep(save, state.csrfToken);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else if (step === "reread") {
    moves.show(LOADING);
    await openRates(signal, moves, "common.error_retry");
  } else {
    moves.update(step);
  }
}

// 页面状态与它所属的界面语言：语言换了而新结果还没到时按读取中显示。
export interface ShippingEntry {
  language: Language;
  state: ShippingState;
}

export function shownState(entry: ShippingEntry, language: Language): ShippingState {
  return entry.language === language ? entry.state : LOADING;
}

function pageMoves(language: Language, setEntry: Dispatch<SetStateAction<ShippingEntry>>, replace: (path: RoutePath) => void): ShippingMoves {
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

interface FormHandlers {
  onChange: (field: RatesField, value: string) => void;
  onSave: () => void;
  onReload: () => void;
  onAdd: (group: RowGroup) => void;
  onRemove: (target: RowTarget) => void;
  onSelect: (target: NewRowTarget, code: string) => void;
}

// 一个运费输入框（不带 RM 前缀的文字）：无障碍标签为 admin.shipping_fee、英文逗号加空格与行名（HANDOFF 0.72 第（6）点）。
function FeeRow({ name, value, field, onChange }: { name: string; value: string; field: RatesField } & Pick<FormHandlers, "onChange">) {
  const t = useCopy();
  return (
    <label className="site-admin-shipping__fee">
      <span>{name}</span>
      <input
        className="acs-admin__input"
        value={value}
        inputMode="decimal"
        aria-label={`${t("admin.shipping_fee")}, ${name}`}
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          onChange(field, event.target.value);
        }}
      />
    </label>
  );
}

// 一行演示汇率：「国家名称 → 币种代码 (小数位)」，其下 admin.fx_rate（读取或保存返回的汇率与币种）与行内标签 admin.fx_version，
// 右边为汇率输入框；无障碍标签为 admin.fx_rate_new、英文逗号加空格与国家名称（不用币种代码）。
function FxRow({ row, name, value, index, onChange }: { row: FxRateRow; name: string; value: string; index: number } & Pick<FormHandlers, "onChange">) {
  const t = useCopy();
  return (
    <label className="site-admin-shipping__fx-row">
      <span className="site-admin-shipping__fx-name">
        {`${name} → ${row.currency_code} (${String(row.currency_decimals)})`}
        <br />
        <span className="acs-admin__muted">{t("admin.fx_rate", { rate: row.rate, currency: row.currency_code })}</span>{" "}
        <span className="acs-tag acs-tag--neutral">{t("admin.fx_version", { n: row.version })}</span>
      </span>
      <input
        className="acs-admin__input"
        value={value}
        inputMode="decimal"
        aria-label={`${t("admin.fx_rate_new")}, ${name}`}
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          onChange({ group: "fx", index }, event.target.value);
        }}
      />
    </label>
  );
}

// 删除按钮（次要按钮 admin.remove）：无障碍标签为 admin.remove 的文字、英文逗号加空格与行名（HANDOFF 0.72 第（6）点）。
function RemoveButton({ name, target, onRemove }: { name: string; target: RowTarget } & Pick<FormHandlers, "onRemove">) {
  const t = useCopy();
  return (
    <button
      className="acs-admin__btn acs-admin__btn--secondary site-admin-shipping__remove"
      type="button"
      aria-label={`${t("admin.remove")}, ${name}`}
      onClick={() => {
        onRemove(target);
      }}
    >
      {t("admin.remove")}
    </button>
  );
}

// 新增按钮（次要按钮 admin.add_country 或 admin.add_fx）：没有可选国家（含收货国家列表读取失败）时禁用，不另加提示。
function AddButton({ group, text, disabled, onAdd }: { group: RowGroup; text: string; disabled: boolean } & Pick<FormHandlers, "onAdd">) {
  return (
    <button
      className="acs-admin__btn acs-admin__btn--secondary site-admin-shipping__add"
      type="button"
      disabled={disabled}
      onClick={() => {
        onAdd(group);
      }}
    >
      {text}
    </button>
  );
}

// 新增行的国家下拉：选项只显示名称，值为国家代码。
function CountrySelect({
  options,
  target,
  value,
  label,
  onSelect,
}: { options: readonly RegionOption[]; target: NewRowTarget; value: string; label?: string } & Pick<FormHandlers, "onSelect">) {
  return (
    <select
      className="acs-admin__select"
      value={value}
      aria-label={label}
      onChange={(event: ChangeEvent<HTMLSelectElement>) => {
        onSelect(target, event.target.value);
      }}
    >
      {options.map((option) => (
        <option key={option.code} value={option.code}>
          {option.name}
        </option>
      ))}
    </select>
  );
}

// 新增的国家运费行：国家下拉（无障碍标签 admin.shipping_country）、空白起的运费输入框（无障碍标签照现有的运费输入框，行名为当前选中的国家名称）
// 与 admin.remove。
function NewCountryLine({
  row,
  name,
  options,
  ...handlers
}: { row: NewCountryRow; name: string; options: readonly RegionOption[] } & Pick<FormHandlers, "onChange" | "onRemove" | "onSelect">) {
  const t = useCopy();
  const target: NewRowTarget = { group: "countries", key: row.key };
  return (
    <div className="site-admin-shipping__line site-admin-shipping__new-country">
      <CountrySelect options={options} target={target} value={row.country} label={t("admin.shipping_country")} onSelect={handlers.onSelect} />
      <input
        className="acs-admin__input"
        value={row.fee}
        inputMode="decimal"
        aria-label={`${t("admin.shipping_fee")}, ${name}`}
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          handlers.onChange({ group: "newCountries", key: row.key }, event.target.value);
        }}
      />
      <RemoveButton name={name} target={target} onRemove={handlers.onRemove} />
    </div>
  );
}

// 新增的汇率行：带可见标签的国家下拉（admin.shipping_country）、admin.fx_currency（至多三个字符）、admin.fx_decimals 与 admin.fx_rate_new
// 三个输入框，以及 admin.remove（行名为当前选中的国家名称）。
function NewFxLine({
  row,
  name,
  options,
  ...handlers
}: { row: NewFxRow; name: string; options: readonly RegionOption[] } & Pick<FormHandlers, "onChange" | "onRemove" | "onSelect">) {
  const t = useCopy();
  const target: NewRowTarget = { group: "fx", key: row.key };
  const part = (field: FxPart) => (event: ChangeEvent<HTMLInputElement>) => {
    handlers.onChange({ group: "newFx", key: row.key, part: field }, event.target.value);
  };
  return (
    <div className="site-admin-shipping__new-fx">
      <label className="acs-admin__field">
        <span>{t("admin.shipping_country")}</span>
        <CountrySelect options={options} target={target} value={row.country} onSelect={handlers.onSelect} />
      </label>
      <label className="acs-admin__field">
        <span>{t("admin.fx_currency")}</span>
        <input className="acs-admin__input" value={row.currency} maxLength={3} autoCapitalize="characters" onChange={part("currency")} />
      </label>
      <label className="acs-admin__field">
        <span>{t("admin.fx_decimals")}</span>
        <input className="acs-admin__input" value={row.decimals} inputMode="numeric" onChange={part("decimals")} />
      </label>
      <label className="acs-admin__field">
        <span>{t("admin.fx_rate_new")}</span>
        <input className="acs-admin__input" value={row.rate} inputMode="decimal" onChange={part("rate")} />
      </label>
      <RemoveButton name={name} target={target} onRemove={handlers.onRemove} />
    </div>
  );
}

// 保存按钮与其下的提示（A06-desktop-states）：admin.rates_saved 为 role="status"，其他为 AdminAlert；admin.rates_changed 带次要按钮 admin.reload。
function SaveArea({ state, onReload }: { state: ReadyState } & Pick<FormHandlers, "onReload">) {
  const t = useCopy();
  const { notice } = state;
  return (
    <div className="site-admin-shipping__save">
      <button className="acs-admin__btn" type="submit" disabled={state.saving} aria-busy={state.saving}>
        {t("common.save")}
      </button>
      {notice === "admin.rates_saved" && <span role="status">{t("admin.rates_saved")}</span>}
      {notice === "admin.rates_changed" && (
        <div className="acs-admin__alert site-admin-shipping__changed" role="alert">
          <span>{t("admin.rates_changed")}</span>
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
      {notice !== null && notice !== "admin.rates_saved" && notice !== "admin.rates_changed" && <AdminAlert error={notice} />}
    </div>
  );
}

function ShippingFormView({ state, onChange, onSave, onReload, onAdd, onRemove, onSelect }: { state: ReadyState } & FormHandlers) {
  const t = useCopy();
  const { language } = useLanguage();
  const country = countryNames(language);
  const { base, inputs } = state;
  const rowHandlers = { onChange, onRemove, onSelect };
  // 新增行的选项：该块页面上现有的国家之外，加上该行自己选中的国家。
  const options = (group: RowGroup, own: string) => countryOptions(state.regions ?? [], language, pageCountries(base, inputs, group), own);
  return (
    <form
      className="site-admin-shipping__form"
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onSave();
      }}
    >
      <div className="site-admin-shipping__fees">
        {state.alert !== null && <AdminAlert error={state.alert} />}
        <details className="acs-admin__panel site-admin-shipping__group site-admin-shipping__malaysia" open>
          <summary className="site-admin-shipping__summary">{t("admin.shipping_my_state")}</summary>
          <div className="site-admin-shipping__content">
            <span className="acs-admin__muted">{t("admin.shipping_fee")}</span>
            <div className="site-admin-shipping__rows">
              {base.malaysia.map((row, index) => {
                const label = MALAYSIA_LABELS[row.zone_code];
                return (
                  <FeeRow
                    key={row.id}
                    name={label === undefined ? row.zone_code : t(label)}
                    value={inputs.malaysia[index] ?? ""}
                    field={{ group: "malaysia", index }}
                    onChange={onChange}
                  />
                );
              })}
            </div>
          </div>
        </details>
        <details className="acs-admin__panel site-admin-shipping__group site-admin-shipping__countries" open>
          <summary className="site-admin-shipping__summary">{t("admin.shipping_by_country")}</summary>
          <div className="site-admin-shipping__content">
            <span className="acs-admin__muted">{t("admin.shipping_fee")}</span>
            <div className="site-admin-shipping__rows">
              {base.countries.map((row, index) =>
                inputs.removedCountries.includes(row.id) ? null : (
                  <div key={row.id} className="site-admin-shipping__line">
                    <FeeRow name={country(row.zone_code)} value={inputs.countries[index] ?? ""} field={{ group: "countries", index }} onChange={onChange} />
                    <RemoveButton name={country(row.zone_code)} target={{ group: "countries", id: row.id }} onRemove={onRemove} />
                  </div>
                ),
              )}
              {inputs.newCountries.map((row) => (
                <NewCountryLine key={row.key} row={row} name={country(row.country)} options={options("countries", row.country)} {...rowHandlers} />
              ))}
            </div>
            <AddButton
              group="countries"
              text={t("admin.add_country")}
              disabled={addChoices(state, "countries", language).length === 0}
              onAdd={onAdd}
            />
          </div>
        </details>
        <div className="acs-admin__panel site-admin-shipping__other">
          <FeeRow name={t("admin.shipping_other")} value={inputs.other} field={{ group: "other" }} onChange={onChange} />
        </div>
      </div>
      <div className="site-admin-shipping__side">
        <details className="acs-admin__panel site-admin-shipping__group site-admin-shipping__fx" open>
          <summary className="site-admin-shipping__summary">{t("admin.fx_title")}</summary>
          <div className="site-admin-shipping__content">
            {base.fx.map((row, index) =>
              inputs.removedFx.includes(row.id) ? null : (
                <div key={row.id} className="site-admin-shipping__line site-admin-shipping__fx-line">
                  <FxRow row={row} name={country(row.country_code)} value={inputs.fx[index] ?? ""} index={index} onChange={onChange} />
                  <RemoveButton name={country(row.country_code)} target={{ group: "fx", id: row.id }} onRemove={onRemove} />
                </div>
              ),
            )}
            {inputs.newFx.map((row) => (
              <NewFxLine key={row.key} row={row} name={country(row.country)} options={options("fx", row.country)} {...rowHandlers} />
            ))}
            <AddButton group="fx" text={t("admin.add_fx")} disabled={addChoices(state, "fx", language).length === 0} onAdd={onAdd} />
          </div>
        </details>
        <div className="site-admin-shipping__rules">{t("admin.rules_apply_new")}</div>
        <SaveArea state={state} onReload={onReload} />
      </div>
    </form>
  );
}

export interface AdminShippingViewProps extends FormHandlers {
  state: ShippingState;
}

// 标题之下为表单区：读取中标 aria-busy、不显示表单；读取失败时只有提示。
export function AdminShippingView({ state, ...handlers }: AdminShippingViewProps) {
  const t = useCopy();
  return (
    <div className="site-admin-shipping">
      <h1 className="acs-admin__h site-admin-shipping__title">{t("admin.shipping_title")}</h1>
      <div className="site-admin-shipping__body" aria-busy={state.status === "loading"}>
        {state.status === "failed" && <AdminAlert error={state.error} />}
        {state.status === "ready" && <ShippingFormView state={state} {...handlers} />}
      </div>
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载与切换界面语言时读取。
export function AdminShippingContent() {
  const { replace } = useRouter();
  const { language } = useLanguage();
  const [entry, setEntry] = useState<ShippingEntry>({ language, state: LOADING });
  // 每次切换语言都先回到读取中（含读取未到时又换回先前语言的情形），与首次读取相同。
  if (entry.language !== language) {
    setEntry({ language, state: LOADING });
  }
  // 当前语言下的请求（读取、保存、admin.reload 与 403 后的重新读取）共用的中止；离开页面或切换语言时中止。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return startDeferred(controller, () => {
      void openRates(controller.signal, pageMoves(language, setEntry, replace));
    });
  }, [language, replace]);

  const state = shownState(entry, language);
  // 改值、增删与选国家：只改当前语言下的状态。
  const edit = (change: ShippingChange) => {
    setEntry((current) => (current.language === language ? { language, state: change(current.state) } : current));
  };

  return (
    <AdminShippingView
      state={state}
      onChange={(field, value) => {
        edit((current) => changeInput(current, field, value));
      }}
      onAdd={(group) => {
        edit((current) => addRow(current, group, language));
      }}
      onRemove={(target) => {
        edit((current) => removeRow(current, target));
      }}
      onSelect={(target, code) => {
        edit((current) => selectCountry(current, target, code));
      }}
      onSave={() => {
        const controller = lifetime.current;
        if (state.status !== "ready" || state.saving || controller === null) {
          return;
        }
        setEntry({ language, state: startSaving(state) });
        void submitRates(state, controller.signal, pageMoves(language, setEntry, replace));
      }}
      onReload={() => {
        const controller = lifetime.current;
        if (state.status !== "ready" || state.saving || controller === null) {
          return;
        }
        void reloadRates(controller.signal, pageMoves(language, setEntry, replace));
      }}
    />
  );
}

export default function AdminShippingPage() {
  return (
    <AdminFrame current="shipping">
      <AdminShippingContent />
    </AdminFrame>
  );
}
