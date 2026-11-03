import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// A10 后台分类（docs/UX.md 0.17 A10 与「管理后台总体」，视觉稿 A10-desktop 与 A10-phone 的框架与标题）：用后台框架渲染，
// 当前导航项为分类，手机顶栏名称为 admin.nav_categories。
// 内容区目前只有标题 h1 admin.nav_categories，桌面与手机都显示（A10-phone 的内容区有标题）；页面不发起自己的请求。
// 请求模块与列表、编辑留给 SHOP-TASK-198、199，有未保存的修改时离开本页的确认留给 SHOP-TASK-197。

export function AdminCategoriesContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.nav_categories")}</h1>;
}

export default function AdminCategoriesPage() {
  return (
    <AdminFrame current="categories">
      <AdminCategoriesContent />
    </AdminFrame>
  );
}
