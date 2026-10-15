import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { COPY } from "../i18n/copy";
import { LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import { AdminCouponsContent, couponDate, couponInputToSen } from "./AdminCouponsPage";

const storage = { getItem: () => "en", setItem: () => undefined };

describe("AdminCouponsPage", () => {
  // UX A05：固定金额以 RM 加两位小数显示；输入金额只在提交时转成整数仙。
  it("keeps money in sen without floating-point rounding", () => {
    expect(couponInputToSen("0.1")).toBe(10);
    expect(couponInputToSen("12.34")).toBe(1234);
  });

  // Kelvin 2026-10-12：有效期只录日期，因此显示绝不随浏览器时区偏移。
  it("formats coupon calendar dates in UTC", () => {
    expect(couponDate("2026-01-01", "en")).toContain("2026");
    expect(couponDate("2026-01-01", "en")).not.toContain("Dec");
  });

  // 验收：拿到列表与 CSRF 令牌以前不得新建；所有初始页面文字来自字典。
  it("disables creation before the list request resolves and uses dictionary copy", () => {
    const html = renderToStaticMarkup(<LanguageProvider storage={storage}><RouterProvider initialPath="/admin/coupons"><AdminCouponsContent /></RouterProvider></LanguageProvider>);
    expect(html).toContain(COPY["admin.nav_coupons"].en);
    expect(html).toContain(COPY["admin.coupon_listed_note"].en);
    expect(html).toContain(`disabled=""`);
    expect(html).toContain(COPY["common.create"].en);
  });
});
