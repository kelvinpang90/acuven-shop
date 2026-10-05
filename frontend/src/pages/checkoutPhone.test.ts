import { afterEach, describe, expect, it, vi } from "vitest";

import type { PhoneRegion } from "../api/checkout";
import { countryNames, decidePhone, displayPhone, isMalaysiaOrSingapore, regionOptions } from "./checkoutPhone";

afterEach(() => {
  vi.unstubAllGlobals();
});

const REGIONS: PhoneRegion[] = [
  { code: "DE", calling_code: 49 },
  { code: "GB", calling_code: 44 },
  { code: "MY", calling_code: 60 },
  { code: "SG", calling_code: 65 },
];

describe("which numbers belong to Malaysia and Singapore", () => {
  // SHOP-TASK-025 验收第 5 条「号码属于马新（下拉选 MY 或 SG 且输入不以加号开头，或输入以 +60、+65 开头）」：
  // 不以加号开头时只看下拉；以加号开头时只看输入，下拉的选择不用（UX P05「以 + 开头输入时以输入为准」）。
  it.each([
    ["MY", "12-345 6789", true],
    ["SG", "8123 4567", true],
    ["GB", "7700 900123", false],
    ["MY", "+44 7700 900123", false],
    ["SG", "+1 202 555 0100", false],
    ["GB", "+60 12-345 6789", true],
    ["DE", "+65 8123 4567", true],
    ["GB", " +6012 345 6789", true],
    ["GB", "+(65) 8123 4567", true],
    ["GB", "+6 0 12 345 6789", true],
    ["MY", "+61 4 1234 5678", false],
  ])("region %s with %j is Malaysian or Singaporean: %s", (region, input, expected) => {
    expect(isMalaysiaOrSingapore(region, input)).toBe(expected);
  });
});

describe("continuing from step 1", () => {
  // UX 0.7 P05「短信验证开关关闭时…号码按所选或输入的国家码规范化后显示 [checkout.guest_sms_off]，以游客表单进入第 3 步，马新号码也一样」与
  // SHOP-TASK-025 验收第 5 条「开关关闭时任何号码都以游客继续，第 3 步顶部显示 checkout.guest_sms_off」。
  it.each([
    ["MY", "12-345 6789"],
    ["SG", "8123 4567"],
    ["GB", "+60 12-345 6789"],
    ["GB", "7700 900123"],
    ["MY", "+44 7700 900123"],
  ])("continues as a guest with SMS off for %s %j", (region, input) => {
    expect(decidePhone(false, region, input)).toEqual({ kind: "guest", notice: "checkout.guest_sms_off" });
  });

  // SHOP-TASK-025 验收第 5 条「开关开启时，号码属于马新…在短信验证组件实现前停在第 1 步并显示 common.service_unavailable（Kelvin 2026-10-01 批准的过渡规则，docs/HANDOFF.md 0.25）」。
  it.each([
    ["MY", "12-345 6789"],
    ["SG", "8123 4567"],
    ["GB", "+60 12-345 6789"],
    ["DE", "+65 8123 4567"],
  ])("stops at step 1 with SMS on for %s %j", (region, input) => {
    expect(decidePhone(true, region, input)).toEqual({ kind: "unavailable" });
  });

  // SHOP-TASK-025 验收第 5 条「其他号码以游客继续并在第 3 步顶部显示 checkout.guest_other_country」与 UX P05「白名单外号码…直接在第 3 步顶部显示」；
  // 加号输入以输入为准：下拉仍是 MY 时输入 +44 也是其他号码。
  it.each([
    ["GB", "7700 900123"],
    ["MY", "+44 7700 900123"],
    ["SG", "+1 202 555 0100"],
  ])("continues as a guest from another country with SMS on for %s %j", (region, input) => {
    expect(decidePhone(true, region, input)).toEqual({ kind: "guest", notice: "checkout.guest_other_country" });
  });
});

describe("country names", () => {
  // SHOP-TASK-025 验收第 3 条「国家名称用浏览器的 Intl.DisplayNames 按当前界面语言取得」：以界面语言（中文为 zh-Hans）请求地区名称。
  it("asks Intl.DisplayNames in the interface language", () => {
    const requested: string[][] = [];
    class FakeDisplayNames {
      private readonly locale: string;
      constructor(locales: string[]) {
        requested.push(locales);
        this.locale = locales[0] ?? "";
      }
      of(code: string) {
        return `${this.locale}:${code}`;
      }
    }
    vi.stubGlobal("Intl", { ...Intl, DisplayNames: FakeDisplayNames });
    expect(countryNames("zh")("GB")).toBe("zh-Hans:GB");
    expect(countryNames("ms")("MY")).toBe("ms:MY");
    expect(requested).toEqual([["zh-Hans"], ["ms"]]);
  });

  // SHOP-TASK-025 验收第 3 条「取不到时显示两位代码（Kelvin 2026-10-01 决定）」：浏览器不支持 DisplayNames、构造时抛错、取名时抛错或没有名称，都显示代码。
  it("falls back to the two-letter code", () => {
    vi.stubGlobal("Intl", { ...Intl, DisplayNames: undefined });
    expect(countryNames("en")("GB")).toBe("GB");
    class Throwing {
      constructor() {
        throw new RangeError("unsupported");
      }
    }
    vi.stubGlobal("Intl", { ...Intl, DisplayNames: Throwing });
    expect(countryNames("en")("GB")).toBe("GB");
    class Nameless {
      of(code: string) {
        if (code === "XK") {
          throw new RangeError("invalid");
        }
        return undefined;
      }
    }
    vi.stubGlobal("Intl", { ...Intl, DisplayNames: Nameless });
    expect(countryNames("en")("GB")).toBe("GB");
    expect(countryNames("en")("XK")).toBe("XK");
  });

  // SHOP-TASK-025 验收第 4 条「国家码下拉列出全部地区（显示本地化国家名与 +呼叫码，按名称排序）」：全部地区都在，按本地化名称排序，名称随语言变化。
  it("lists every region sorted by its localised name", () => {
    const english = regionOptions(REGIONS, "en");
    expect(english.map((option) => option.code)).toEqual(["DE", "MY", "SG", "GB"]);
    expect(english.map((option) => option.name)).toEqual(["Germany", "Malaysia", "Singapore", "United Kingdom"]);
    expect(english.find((option) => option.code === "MY")?.callingCode).toBe(60);
    const malay = regionOptions(REGIONS, "ms");
    expect(malay).toHaveLength(REGIONS.length);
    expect(malay.map((option) => option.name)).toEqual([...malay.map((option) => option.name)].sort((a, b) => a.localeCompare(b, "ms")));
  });

  // 同一条与「取不到时显示两位代码」：名称取不到时按代码排序显示代码。
  it("sorts by code when no names are available", () => {
    vi.stubGlobal("Intl", { ...Intl, DisplayNames: undefined });
    expect(regionOptions([...REGIONS].reverse(), "en").map((option) => option.name)).toEqual(["DE", "GB", "MY", "SG"]);
  });
});

describe("the read-only phone on step 3", () => {
  // UX P05「游客：[P3] <第 1 步号码>（只读）」与 SHOP-TASK-025 验收第 6 条「显示第 1 步的号码（只读）」：以加号开头时照输入显示，否则前面加所选地区的呼叫码。
  it("shows the number with its country code", () => {
    expect(displayPhone("MY", " 12-345 6789 ", REGIONS)).toBe("+60 12-345 6789");
    expect(displayPhone("MY", "+44 7700 900123", REGIONS)).toBe("+44 7700 900123");
    expect(displayPhone("ZZ", "123", REGIONS)).toBe("123");
  });
});
