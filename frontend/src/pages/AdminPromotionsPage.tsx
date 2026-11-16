import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台促销活动列表 A12（docs/UX.md 0.20 A12，/admin/promotions）：用后台框架渲染，当前导航项为促销活动。
// 内容区目前只有标题 admin.nav_promotions（h1）；列表内容由 SHOP-TASK-269 加入。本页不发请求。

export function AdminPromotionsContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.nav_promotions")}</h1>;
}

export default function AdminPromotionsPage() {
  return (
    <AdminFrame current="promotions">
      <AdminPromotionsContent />
    </AdminFrame>
  );
}
