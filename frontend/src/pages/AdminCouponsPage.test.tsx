import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { COPY } from "../i18n/copy";
import { LanguageProvider } from "../i18n/language";
import { RouterProvider } from "../router";
import type { AdminCoupon } from "../api/adminCoupons";
import { AdminCouponsContent, CouponRows, couponDate, couponInput, couponInputToSen } from "./AdminCouponsPage";

const storage = { getItem: () => "en", setItem: () => undefined };
const coupons: AdminCoupon[] = [
  { id: 1, code: "SAVE10", type: "fixed", amount_off_sen: 1000, percent_off: null, min_subtotal_sen: 0, starts_on: "2026-01-01", ends_on: "2026-12-31", total_use_limit: null, per_member_use_limit: null, used_count: 2, active: true },
  { id: 2, code: "TEN", type: "percent", amount_off_sen: null, percent_off: 10, min_subtotal_sen: 0, starts_on: "2026-02-01", ends_on: "2026-02-28", total_use_limit: 20, per_member_use_limit: 1, used_count: 4, active: false },
];

function rows() {
  return renderToStaticMarkup(<LanguageProvider storage={storage}><CouponRows coupons={coupons} busyId={null} onDisable={() => undefined} /></LanguageProvider>);
}

describe("AdminCouponsPage", () => {
  // UX A05：固定金额以 RM 加两位小数显示；输入金额只在提交时转成整数仙。
  it("keeps money in sen without floating-point rounding", () => {
    expect(couponInputToSen("0.1")).toBe(10);
    expect(couponInputToSen("12.34")).toBe(1234);
  });

  // UX A05：固定金额显示 common.price_myr 的两位小数；空的两种上限提交为 null，不把“不限”误作零。
  it("renders both list layouts with values, dates, limits and active-only disable controls", () => {
    const html = rows();
    for (const key of ["admin.coupon_code", "admin.coupon_type", "admin.coupon_value", "admin.coupon_valid", "admin.coupon_usage", "admin.filter_status"] as const) expect(html).toContain(COPY[key].en);
    expect(html).toContain("RM 10.00");
    expect(html).toContain("10%");
    expect(html).toContain("2 / —");
    expect(html).toContain("4 / 20");
    expect(html).toContain(COPY["admin.coupon_enabled"].en);
    expect(html).toContain(COPY["admin.coupon_disabled"].en);
    expect(html.match(new RegExp(COPY["admin.coupon_disable"].en, "g")) ?? []).toHaveLength(2);
    expect(html).toContain("Jan 1, 2026 – Dec 31, 2026");
    expect(couponInput({ code: "SAVE10", type: "fixed", value: "12.34", startsOn: "2026-01-01", endsOn: "2026-01-02", minSpend: "0.1", totalLimit: "", memberLimit: "" })).toMatchObject({ amount_off_sen: 1234, min_subtotal_sen: 10, total_use_limit: null, per_member_use_limit: null });
  });

  // SHOP-TASK-060 的空列表约定：仍保留桌面列头，但没有手机卡片或翻页内容。
  it("keeps only the desktop table header for an empty coupon list", () => {
    const html = renderToStaticMarkup(<LanguageProvider storage={storage}><CouponRows coupons={[]} busyId={null} onDisable={() => undefined} /></LanguageProvider>);
    expect(html).toContain(COPY["admin.coupon_code"].en);
    expect(html).toContain("<tbody></tbody>");
    expect(html).not.toContain("site-admin-coupons__cards");
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
