import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台优惠券 A05（docs/UX.md 0.10，视觉稿 A05-desktop 与 A05-phone-list 的页头部分）：用后台框架渲染，当前导航项为优惠券。
// 本任务的内容区只有标题 admin.nav_coupons 与会员中心说明 admin.coupon_listed_note；列表、新建与停用由后续任务接入，不发起请求。
export function AdminCouponsContent() {
  const t = useCopy();
  return (
    <div className="site-admin-coupons">
      <h1 className="acs-admin__h">{t("admin.nav_coupons")}</h1>
      <p className="site-admin-coupons__note">{t("admin.coupon_listed_note")}</p>
    </div>
  );
}

export default function AdminCouponsPage() {
  return (
    <AdminFrame current="coupons">
      <AdminCouponsContent />
    </AdminFrame>
  );
}
