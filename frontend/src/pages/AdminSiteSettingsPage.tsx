import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台站点设置 A09（docs/UX.md 0.11 A09，视觉稿 A09-stripe-desktop 与 A09-stripe-phone 的页头部分）：用后台框架渲染，当前导航项为站点设置。
// 内容区目前只有标题 admin.nav_settings（h1）；A09-stripe-phone 不画标题（顶栏已是 admin.nav_settings），h1 仍在页面里，
// 由 site.css 在 767px 及以下只留给读屏。页面不发起自己的请求：支付方式的读取与保存由 SHOP-TASK-138、139 接上。
// 视觉稿与 UX 线框里的短信验证段不做（Kelvin 2026-10-07 与 2026-10-15 决定：短信验证服务与会员结账路径上线之前不提供打开它的入口）。

export function AdminSiteSettingsContent() {
  const t = useCopy();
  return (
    <div className="site-admin-settings">
      <h1 className="acs-admin__h site-admin-settings__title">{t("admin.nav_settings")}</h1>
    </div>
  );
}

export default function AdminSiteSettingsPage() {
  return (
    <AdminFrame current="settings">
      <AdminSiteSettingsContent />
    </AdminFrame>
  );
}
