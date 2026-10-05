import type { PhoneRegion } from "../api/checkout";
import type { Language } from "../i18n/copy";
import { htmlLang } from "../i18n/language";

// 结账页 P05 第 1 步的号码规则（docs/UX.md P05「第 1 步手机号」与 0.7「短信验证开关关闭时」）：
// 国家码下拉列出全部地区、默认 MY；输入以加号开头时以输入为准。号码格式是否成立由服务端在下单时判定，这里只看属于哪个国家码。
//
// 继续后的判定：
// - 开关关闭：任何号码（含马新号码）都以游客继续，第 3 步顶部显示 checkout.guest_sms_off。
// - 开关开启：马新号码（下拉选 MY 或 SG 且输入不以加号开头，或输入以 +60、+65 开头）在短信验证组件 V1 实现之前停在第 1 步，
//   显示 common.service_unavailable（Kelvin 2026-10-01 批准的过渡规则，记录见 docs/HANDOFF.md 0.25，由之后的 V1 与会员路径任务取消）；
//   其他号码以游客继续，第 3 步顶部显示 checkout.guest_other_country。

export const MALAYSIA = "MY";
const SMS_REGIONS: ReadonlySet<string> = new Set([MALAYSIA, "SG"]);
const SMS_CALLING_CODES = ["60", "65"] as const;

// 输入以加号开头：国家码以输入为准，下拉的选择不用。
export function typesOwnCode(input: string): boolean {
  return input.trim().startsWith("+");
}

// 号码是否属于马来西亚或新加坡。以加号开头时看加号后的数字（忽略空格、连字符、括号与点）是否以 60、65 开头。
export function isMalaysiaOrSingapore(region: string, input: string): boolean {
  if (typesOwnCode(input)) {
    const digits = input.trim().slice(1).replace(/[\s\-().]/g, "");
    return SMS_CALLING_CODES.some((code) => digits.startsWith(code));
  }
  return SMS_REGIONS.has(region);
}

export type GuestNotice = "checkout.guest_sms_off" | "checkout.guest_other_country";

export type PhoneDecision = { kind: "guest"; notice: GuestNotice } | { kind: "unavailable" };

export function decidePhone(smsEnabled: boolean, region: string, input: string): PhoneDecision {
  if (!smsEnabled) {
    return { kind: "guest", notice: "checkout.guest_sms_off" };
  }
  return isMalaysiaOrSingapore(region, input) ? { kind: "unavailable" } : { kind: "guest", notice: "checkout.guest_other_country" };
}

// 国家名称：用浏览器的 Intl.DisplayNames 按当前界面语言取得；取不到（不支持、抛错或没有名称）时为两位代码。
export function countryNames(language: Language): (code: string) => string {
  let names: Intl.DisplayNames | null;
  try {
    names = new Intl.DisplayNames([htmlLang(language)], { type: "region" });
  } catch {
    names = null;
  }
  return (code) => {
    if (names === null) {
      return code;
    }
    try {
      return names.of(code) ?? code;
    } catch {
      return code;
    }
  };
}

export interface RegionOption {
  code: string;
  callingCode: number;
  name: string;
}

// 下拉选项：全部地区，名称本地化，按名称排序（名称相同按代码）。第 1 步显示「名称 +呼叫码」，收货国家只显示名称。
export function regionOptions(regions: readonly PhoneRegion[], language: Language): RegionOption[] {
  const name = countryNames(language);
  const locale = htmlLang(language);
  return regions
    .map((region) => ({ code: region.code, callingCode: region.calling_code, name: name(region.code) }))
    .sort((a, b) => a.name.localeCompare(b.name, locale) || a.code.localeCompare(b.code));
}

// 第 3 步只读显示的号码：以加号开头时为输入本身，否则在前面加所选地区的 +呼叫码。只用于显示，下单时送原文与地区代码。
export function displayPhone(region: string, input: string, regions: readonly PhoneRegion[]): string {
  const trimmed = input.trim();
  if (typesOwnCode(trimmed)) {
    return trimmed;
  }
  const found = regions.find((option) => option.code === region);
  return found === undefined ? trimmed : `+${String(found.calling_code)} ${trimmed}`;
}
