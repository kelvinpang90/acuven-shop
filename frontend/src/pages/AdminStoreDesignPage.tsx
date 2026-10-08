import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台店铺装修 A08（docs/UX.md 0.10 A08，视觉稿 A08-desktop 与 A08-phone 的页头部分）：用后台框架渲染，当前导航项为店铺装修。
// 内容区为标题 admin.nav_store_design（h1）与其下的 admin.design_demo_note；手机线框与 A08-phone 不画标题（顶栏已是
// admin.nav_store_design），h1 仍在页面里，由 site.css 在 767px 及以下只留给读屏。
// 本页不发起自己的请求：主题、主色、首页区块与保存由 SHOP-TASK-064 接上，精选商品与预览由 SHOP-TASK-065、066 接上。

// 框架的内容区：框架确认已登录后才挂载。
export function AdminStoreDesignContent() {
  const t = useCopy();
  return (
    <div className="site-admin-store-design">
      <h1 className="acs-admin__h site-admin-store-design__title">{t("admin.nav_store_design")}</h1>
      <p className="site-admin-store-design__note">{t("admin.design_demo_note")}</p>
    </div>
  );
}

export default function AdminStoreDesignPage() {
  return (
    <AdminFrame current="storeDesign">
      <AdminStoreDesignContent />
    </AdminFrame>
  );
}
