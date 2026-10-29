import { CSRF_HEADER } from "./pay";

// 后台运费与演示汇率 A06 的接口（app/api/admin_shipping_rates.py，SHOP-TASK-171；马来西亚一组按 SHOP-TASK-172 为 malaysia）：读取
// GET /api/admin/shipping-rates 与保存 PUT /api/admin/shipping-rates，都不带查询参数。后台会话凭服务端发的 HttpOnly cookie；读取不要求
// CSRF，保存带读取返回的 CSRF 令牌。只取 AdminRatesPageOut 列出的字段，多出的字段不带进结果；响应体不合格算失败。运费与汇率的取值规则、
// 核对与审计在服务端（DESIGN 1.17 第 9、10 条），这里只传递与校验字段。HANDOFF 0.72 第（3）点：兜底行为 null 时读取算失败（迁移 0005
// 已写入兜底行，保存必须带它），马来西亚行的区域代码只收 MY-W 与 MY-E。

export const ADMIN_SHIPPING_RATES_URL = "/api/admin/shipping-rates";

// 马来西亚两档的区域代码（与 app/models/shipping.py 的 MY_WEST_ZONE_CODE、MY_EAST_ZONE_CODE 一致）与兜底行的区域代码（OTHER_ZONE_CODE）。
export const MALAYSIA_ZONE_CODES = ["MY-W", "MY-E"] as const;
export const OTHER_ZONE_CODE = "OTHER";

// 运费的上限（仙，DESIGN 1.17 第 9 条「0 到 999,999 仙」）。
const MAX_FEE_SEN = 999_999;
// 汇率小数位的上限（DESIGN 1.17 第 9 条「0–3 的整数」）。
const MAX_CURRENCY_DECIMALS = 3;

// 一行运费：马来西亚档、兜底或国家。
export interface ShippingRateRow {
  id: number;
  version: number;
  zone_code: string;
  fee_sen: number;
}

// 一行演示汇率；rate 为服务端给出的规范形式的十进制字符串。
export interface FxRateRow {
  id: number;
  version: number;
  country_code: string;
  currency_code: string;
  currency_decimals: number;
  rate: string;
}

// 整页数据（读取与保存 200 共有的四项）。
export interface AdminRates {
  malaysia: ShippingRateRow[];
  other: ShippingRateRow;
  countries: ShippingRateRow[];
  fx: FxRateRow[];
}

// 保存内容的各种行（与 app/api/admin_shipping_rates.py 的 AdminRatesIn 一致）：保留的运费行、删除的行（国家运费或汇率）、新增的国家运费行、
// 保留的汇率行与新增的汇率行。各种行的字段组合互不相同，按有没有 delete、country_code 区分。
export interface KeepFee {
  id: number;
  version: number;
  fee_sen: number;
}

export interface RemoveRow {
  id: number;
  version: number;
  delete: true;
}

export interface AddCountryFee {
  country_code: string;
  fee_sen: number;
}

export interface KeepFxRate {
  id: number;
  version: number;
  rate: string;
}

export interface AddFxRate {
  country_code: string;
  currency_code: string;
  currency_decimals: number;
  rate: string;
}

export type CountryFeeItem = KeepFee | RemoveRow | AddCountryFee;

export type FxRateItem = KeepFxRate | RemoveRow | AddFxRate;

// 整页的保存内容：马来西亚与兜底的行只有保留，国家运费与汇率的行为三种之一。
export interface AdminRatesSave {
  malaysia: KeepFee[];
  other: KeepFee;
  countries: CountryFeeItem[];
  fx: FxRateItem[];
}

// 读取的结果：取到（200，带整页数据与只用于保存请求头的 CSRF 令牌）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断（含调用方中止）。
export type ShippingRatesRead = { kind: "ok"; rates: AdminRates; csrf_token: string } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 保存的结果：保存了（200，带保存后的整页数据）、没有会话（401）、CSRF 令牌不对（403）、取值不合格（422 且 detail 为 rates_invalid）、
// 数据已被改动（409 且 detail 为 rates_changed）、其他失败（含其他 422、其他 409、413、415 与 200 但响应体不合格）、网络中断（含调用方中止）。
export type ShippingRatesSave =
  | { kind: "ok"; rates: AdminRates }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "invalid" }
  | { kind: "changed" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFilled(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

function isIntegerIn(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isMalaysiaZone(value: unknown): value is string {
  return typeof value === "string" && (MALAYSIA_ZONE_CODES as readonly string[]).includes(value);
}

function isOtherZone(value: unknown): value is string {
  return value === OTHER_ZONE_CODE;
}

// 国家代码：两位大写字母，不是马来西亚。
function isCountryCode(value: unknown): value is string {
  return typeof value === "string" && /^[A-Z]{2}$/.test(value) && value !== "MY";
}

function isCurrencyCode(value: unknown): value is string {
  return typeof value === "string" && /^[A-Z]{3}$/.test(value);
}

// 一行运费：id 与 version 为正整数、zone_code 合 isZone、fee_sen 为 0 到 999999 的整数。
function readZoneRow(value: unknown, isZone: (code: unknown) => code is string): ShippingRateRow | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, version, zone_code, fee_sen } = value;
  if (!isPositiveInteger(id) || !isPositiveInteger(version) || !isZone(zone_code) || !isIntegerIn(fee_sen, 0, MAX_FEE_SEN)) {
    return null;
  }
  return { id, version, zone_code, fee_sen };
}

// 一行汇率：id 与 version 为正整数、country_code 为国家代码、currency_code 为三位大写字母、currency_decimals 为 0 到 3 的整数、rate 为非空字符串。
function readFxRow(value: unknown): FxRateRow | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, version, country_code, currency_code, currency_decimals, rate } = value;
  if (
    !isPositiveInteger(id) ||
    !isPositiveInteger(version) ||
    !isCountryCode(country_code) ||
    !isCurrencyCode(currency_code) ||
    !isIntegerIn(currency_decimals, 0, MAX_CURRENCY_DECIMALS) ||
    !isFilled(rate)
  ) {
    return null;
  }
  return { id, version, country_code, currency_code, currency_decimals, rate };
}

// 数组的每一项按 read 读取；不是数组或任一项不合格为 null。
function readRows<T>(value: unknown, read: (row: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const rows: T[] = [];
  for (const item of value) {
    const row = read(item);
    if (row === null) {
      return null;
    }
    rows.push(row);
  }
  return rows;
}

// 整页的四项：malaysia（MY-W 或 MY-E，同一代码不出现两次）、other（OTHER 的一行，null 不合格）、countries（国家代码）与 fx。
function readRates(value: unknown): AdminRates | null {
  if (!isRecord(value)) {
    return null;
  }
  const malaysia = readRows(value.malaysia, (row) => readZoneRow(row, isMalaysiaZone));
  const other = readZoneRow(value.other, isOtherZone);
  const countries = readRows(value.countries, (row) => readZoneRow(row, isCountryCode));
  const fx = readRows(value.fx, readFxRow);
  if (malaysia === null || other === null || countries === null || fx === null) {
    return null;
  }
  if (new Set(malaysia.map((row) => row.zone_code)).size !== malaysia.length) {
    return null;
  }
  return { malaysia, other, countries, fx };
}

// 读取的响应体：整页的四项与非空的 csrf_token。
function readPage(value: unknown): { rates: AdminRates; csrf_token: string } | null {
  if (!isRecord(value)) {
    return null;
  }
  const rates = readRates(value);
  const { csrf_token } = value;
  if (rates === null || !isFilled(csrf_token)) {
    return null;
  }
  return { rates, csrf_token };
}

// 4xx 的响应体：detail 恰为给定的字符串时为 true，否则为 null。
function detailIs(detail: string): (body: unknown) => true | null {
  return (body) => (isRecord(body) && body.detail === detail ? true : null);
}

// 响应体按 read 读取；读不出或不合格为 null。
async function readBody<T>(response: Response, read: (body: unknown) => T | null): Promise<T | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return read(body);
}

// 请求体的各种行：按种类只取规定的字段，调用方对象上多出的属性不带进请求体。
function keepFeeBody(row: KeepFee): KeepFee {
  return { id: row.id, version: row.version, fee_sen: row.fee_sen };
}

function removeBody(row: RemoveRow): RemoveRow {
  return { id: row.id, version: row.version, delete: true };
}

function countryBody(row: CountryFeeItem): CountryFeeItem {
  if ("delete" in row) {
    return removeBody(row);
  }
  if ("country_code" in row) {
    return { country_code: row.country_code, fee_sen: row.fee_sen };
  }
  return keepFeeBody(row);
}

function fxBody(row: FxRateItem): FxRateItem {
  if ("delete" in row) {
    return removeBody(row);
  }
  if ("country_code" in row) {
    return { country_code: row.country_code, currency_code: row.currency_code, currency_decimals: row.currency_decimals, rate: row.rate };
  }
  return { id: row.id, version: row.version, rate: row.rate };
}

function saveBody(rates: AdminRatesSave): AdminRatesSave {
  return {
    malaysia: rates.malaysia.map(keepFeeBody),
    other: keepFeeBody(rates.other),
    countries: rates.countries.map(countryBody),
    fx: rates.fx.map(fxBody),
  };
}

// GET /api/admin/shipping-rates：同源 cookie、不缓存、没有请求体。
export async function readAdminShippingRates(signal?: AbortSignal): Promise<ShippingRatesRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_SHIPPING_RATES_URL, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "none" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  const page = await readBody(response, readPage);
  return page === null ? { kind: "failed" } : { kind: "ok", ...page };
}

// PUT /api/admin/shipping-rates：JSON 请求体只有 malaysia、other、countries 与 fx 四项，请求头带读取给的 CSRF 令牌；同源 cookie、不缓存。
export async function saveAdminShippingRates(rates: AdminRatesSave, csrfToken: string, signal?: AbortSignal): Promise<ShippingRatesSave> {
  let response: Response;
  try {
    response = await fetch(ADMIN_SHIPPING_RATES_URL, {
      method: "PUT",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify(saveBody(rates)),
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const saved = await readBody(response, readRates);
      return saved === null ? { kind: "failed" } : { kind: "ok", rates: saved };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 422:
      return (await readBody(response, detailIs("rates_invalid"))) === null ? { kind: "failed" } : { kind: "invalid" };
    case 409:
      return (await readBody(response, detailIs("rates_changed"))) === null ? { kind: "failed" } : { kind: "changed" };
    default:
      return { kind: "failed" };
  }
}
