import AdminFrame from "../components/AdminFrame";
import { useCopy } from "../i18n/language";

// 后台 Stripe 待处理 A11（docs/UX.md 0.19 A11，视觉稿 A11-desktop 与 A11-phone）：用后台框架渲染，当前导航项为 Stripe 待处理。
// 内容区目前只有标题 admin.nav_stripe_pending（h1），页面不发起自己的请求；
// 请求模块留给 SHOP-TASK-227，两个列表、作废确认框与退款重试留给 SHOP-TASK-228。

// 后台框架内容区（已登录时显示）。
export function AdminStripePendingContent() {
  const t = useCopy();
  return <h1 className="acs-admin__h">{t("admin.nav_stripe_pending")}</h1>;
}

export default function AdminStripePendingPage() {
  return (
    <AdminFrame current="stripePending">
      <AdminStripePendingContent />
    </AdminFrame>
  );
}
