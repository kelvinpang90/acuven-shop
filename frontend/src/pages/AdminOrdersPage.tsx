import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台订单 A02（docs/UX.md A02，A01 登录后的默认页）：用后台框架渲染，当前导航项为订单。
// 内容区暂时只有标题 admin.nav_orders；订单列表、搜索、筛选与详情由 SHOP-TASK-048 与之后的任务接上。
export function AdminOrdersContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.nav_orders")}</h1>;
}

export default function AdminOrdersPage() {
  return (
    <AdminFrame current="orders">
      <AdminOrdersContent />
    </AdminFrame>
  );
}
