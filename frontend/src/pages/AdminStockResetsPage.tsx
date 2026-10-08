import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台库存重置结果 A07（docs/UX.md 0.10 A07，视觉稿 A07-desktop 与 A07-phone 的页头部分）：用后台框架渲染，当前导航项为库存重置。
// 内容区为标题 admin.stock_reset_title（h1）与其下的 admin.stock_reset_note；本页不发起自己的请求。
// 每日重置的列表与展开明细由 SHOP-TASK-060 接上。

// 框架的内容区：框架确认已登录后才挂载。
export function AdminStockResetsContent() {
  const t = useCopy();
  return (
    <div className="site-admin-stock-resets">
      <h1 className="acs-admin__h">{t("admin.stock_reset_title")}</h1>
      <p className="site-admin-stock-resets__note">{t("admin.stock_reset_note")}</p>
    </div>
  );
}

export default function AdminStockResetsPage() {
  return (
    <AdminFrame current="stockResets">
      <AdminStockResetsContent />
    </AdminFrame>
  );
}
