import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// A04 后台商品（docs/UX.md 0.17 A04 与「管理后台总体」）：用后台框架渲染，当前导航项为商品，手机顶栏名称为 admin.nav_products。
// 内容区目前只有标题 h1 admin.nav_products，桌面与手机都显示；页面不发起自己的请求。
// 列表、编辑与请求模块留给之后的任务，有未保存的修改时离开本页的确认留给 SHOP-TASK-197。

export function AdminProductsContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.nav_products")}</h1>;
}

export default function AdminProductsPage() {
  return (
    <AdminFrame current="products">
      <AdminProductsContent />
    </AdminFrame>
  );
}
