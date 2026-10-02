import { describe, expect, it } from "vitest";

import { formatSen } from "./format";
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
