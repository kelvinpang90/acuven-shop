import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台促销活动编辑区 A12（docs/UX.md 0.20 A12）：新建 /admin/promotions/new 与编辑 /admin/promotions/:id（段值为活动的内部 ID）共用本页，
// 用后台框架渲染，当前导航项为促销活动。内容区目前只有标题 admin.nav_promotions（h1）；编辑区由 SHOP-TASK-270 起加入，
// 段值不合法时的处理也在那里。本页不发请求。

export function AdminPromotionEditContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.nav_promotions")}</h1>;
}

export default function AdminPromotionEditPage() {
  return (
    <AdminFrame current="promotions">
      <AdminPromotionEditContent />
    </AdminFrame>
  );
}
