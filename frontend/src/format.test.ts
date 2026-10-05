import { describe, expect, it } from "vitest";

import { formatMinorUnits, formatSen } from "./format";
import { translate } from "./i18n/copy";

describe("formatSen", () => {
  // UX-COPY「约定」：金额以 MYR 显示为 `RM {amount}`，{amount} 由整数仙格式化为两位小数。
  it.each([
    [0, "0.00"],
    [5, "0.05"],
    [100, "1.00"],
    [2190, "21.90"],
    [123456, "1234.56"],
  ])("formats %i sen as %s", (sen, text) => {
    expect(formatSen(sen)).toBe(text);
  });

  // UX-COPY common.price_myr：2190 仙显示为 RM 21.90。
  it("fills the MYR price template", () => {
    expect(translate("en", "common.price_myr", { amount: formatSen(2190) })).toBe("RM 21.90");
  });

  // DESIGN「边界与原则」：金额以仙为整数单位，前端不经浮点换算——不是整数仙的输入不猜测，直接拒绝。
  it.each([21.9, -1, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53])("rejects %s", (sen) => {
    expect(() => formatSen(sen)).toThrow(RangeError);
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-015 验收第 4 条「格式化只用整数运算、不用浮点」与 DESIGN「边界与原则」——第 8 条只列 0、5、100、2190、123456 仙，另加最大安全整数，验证逐位保留、不出现尾差或科学计数法。
  it("keeps every digit of large amounts", () => {
    expect(formatSen(9007199254740991)).toBe("90071992547409.91");
    expect(formatSen(1999)).toBe("19.99");
    expect(formatSen(1)).toBe("0.01");
  });
});

describe("formatMinorUnits", () => {
  // UX P05 的 M7「参考外币金额，按收货国家与固定演示汇率」与 UX-COPY common.fx_reference「≈ {currency} {amount}」：
  // 服务端给出该币种最小单位的整数与小数位数（SHOP-TASK-007 的 FxReference），{amount} 按位数切分。
  it.each([
    [2295, 2, "22.95"],
    [5, 2, "0.05"],
    [0, 2, "0.00"],
    [1250, 0, "1250"],
    [0, 0, "0"],
    [5, 3, "0.005"],
    [12345, 3, "12.345"],
    [7, 1, "0.7"],
  ])("formats %i with %i decimals as %s", (amount, decimals, text) => {
    expect(formatMinorUnits(amount, decimals)).toBe(text);
  });

  // UX-COPY common.fx_reference 的英文列：GBP 2295 便士显示为 ≈ GBP 22.95 (demo rate, for reference only)。
  it("fills the reference currency template", () => {
    expect(translate("en", "common.fx_reference", { currency: "GBP", amount: formatMinorUnits(2295, 2) })).toBe("≈ GBP 22.95 (demo rate, for reference only)");
  });

  // SHOP-TASK-025 验收第 6 条「参考外币金额的格式化加在 frontend/src/format.ts（整数运算、不用浮点）」与 DESIGN「边界与原则」（金额以整数为单位）：
  // 不是非负整数的金额、或小数位数不在 0 到 3 之间时拒绝，不猜测；大数逐位保留，没有尾差或科学计数法。
  it.each([
    [22.95, 2],
    [-1, 2],
    [Number.NaN, 2],
    [2 ** 53, 2],
    [100, -1],
    [100, 4],
    [100, 1.5],
  ])("rejects %s with %s decimals", (amount, decimals) => {
    expect(() => formatMinorUnits(amount, decimals)).toThrow(RangeError);
  });

  it("keeps every digit of large amounts", () => {
    expect(formatMinorUnits(9007199254740991, 3)).toBe("9007199254740.991");
    expect(formatMinorUnits(9007199254740991, 0)).toBe("9007199254740991");
  });
});
