import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// A06 后台运费区与演示汇率（docs/UX.md A06，视觉稿 A06-desktop 与 A06-phone 的页头与导航部分）：用后台框架渲染，当前导航项为运费与演示汇率。
// 内容区目前只有标题 admin.shipping_title，页面不发起自己的请求；请求模块、读取显示与保存、增删行由 SHOP-TASK-174 到 176 接上。
export function AdminShippingContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.shipping_title")}</h1>;
}

export default function AdminShippingPage() {
  return (
    <AdminFrame current="shipping">
      <AdminShippingContent />
    </AdminFrame>
  );
}
