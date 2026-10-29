import type { ComponentType } from "react";

import { MemberSessionProvider } from "./api/memberSession";
import type { MemberSessionReader } from "./api/memberSession";
import { WhatsAppContactProvider } from "./api/siteSettings";
import type { WhatsAppContactReader } from "./api/siteSettings";
import SiteFrame from "./components/SiteFrame";
import { LanguageProvider } from "./i18n/language";
import type { LanguageStorage } from "./i18n/language";
import AccountPage from "./pages/AccountPage";
import AdminLoginPage from "./pages/AdminLoginPage";
import AdminCouponsPage from "./pages/AdminCouponsPage";
import AdminOrdersPage from "./pages/AdminOrdersPage";
import AdminRefundsPage from "./pages/AdminRefundsPage";
import AdminShippingPage from "./pages/AdminShippingPage";
import AdminSiteSettingsPage from "./pages/AdminSiteSettingsPage";
import AdminStockResetsPage from "./pages/AdminStockResetsPage";
import AdminStoreDesignPage from "./pages/AdminStoreDesignPage";
import CartPage from "./pages/CartPage";
import CheckoutPage from "./pages/CheckoutPage";
import ForgotPasswordPage from "./pages/ForgotPasswordPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import PayPage from "./pages/PayPage";
import PayResultPage from "./pages/PayResultPage";
import PrivacyPage from "./pages/PrivacyPage";
import ProductDetailPage from "./pages/ProductDetailPage";
import ProductListPage from "./pages/ProductListPage";
import RefundPage, { MemberRefundPage } from "./pages/RefundPage";
import RegisterPage from "./pages/RegisterPage";
import TrackOrderPage, { MemberOrderPage } from "./pages/TrackOrderPage";
import TrackPage from "./pages/TrackPage";
import { RouterProvider, useRouter } from "./router";
import type { RoutePattern } from "./router";
import { StoreDesignProvider } from "./storeDesign";

// 按 router.tsx 的路由表穷举：表里每个模式恰好对应一个页面。
const PAGES: Readonly<Record<RoutePattern, ComponentType>> = {
  "/": HomePage,
  "/products": ProductListPage,
  "/products/:slug": ProductDetailPage,
  "/cart": CartPage,
  "/checkout": CheckoutPage,
  "/pay": PayPage,
  "/pay/result": PayResultPage,
  "/track": TrackPage,
  "/track/order": TrackOrderPage,
  "/track/order/refund": RefundPage,
  "/privacy": PrivacyPage,
  "/login": LoginPage,
  "/forgot-password": ForgotPasswordPage,
  "/register": RegisterPage,
  "/account": AccountPage,
  // 订单详情的会员模式与查单模式在同一文件里（TrackOrderPage.tsx 另导出的页面组件）。
  "/account/order": MemberOrderPage,
  // 退款申请的会员模式与查单模式在同一文件里（RefundPage.tsx 另导出的页面组件）。
  "/account/order/refund": MemberRefundPage,
  "/admin/login": AdminLoginPage,
  "/admin/orders": AdminOrdersPage,
  // 订单详情与列表是同一页（桌面列表在左、详情在右，手机只显示详情）。
  "/admin/orders/:id": AdminOrdersPage,
  // 全部退款申请与只含某张订单的申请是同一页（后者按路径里的订单内部 ID 筛选）。
  "/admin/refunds": AdminRefundsPage,
  "/admin/refunds/order/:orderId": AdminRefundsPage,
  // 退款详情与列表是同一页（桌面列表在左、详情在右，手机只显示详情）。
  "/admin/refunds/:id": AdminRefundsPage,
  "/admin/coupons": AdminCouponsPage,
  "/admin/shipping": AdminShippingPage,
  "/admin/stock-resets": AdminStockResetsPage,
  "/admin/store-design": AdminStoreDesignPage,
  "/admin/settings": AdminSiteSettingsPage,
};

// 后台页（以 /admin 开头的路由）不套前台的站点框架，直接渲染页面；其余页面照旧放在 SiteFrame 里。
function isAdminPattern(pattern: RoutePattern): boolean {
  return pattern.startsWith("/admin");
}

// 以实际路径为 key：从一件商品换到另一件时页面重新开始（规格、数量与提示不带过去）。
// 店铺装修设置只给前台：提供者在前台页之间切换时保留，设置只取一次；后台页不取。
function CurrentPage({ storage }: { storage: LanguageStorage | null | undefined }) {
  const { path, pattern } = useRouter();
  const Page = PAGES[pattern];
  const page = <Page key={path} />;
  return isAdminPattern(pattern) ? (
    page
  ) : (
    <StoreDesignProvider storage={storage}>
      <SiteFrame>{page}</SiteFrame>
    </StoreDesignProvider>
  );
}

interface AppProps {
  // 测试用：给定初始路径与语言、店铺装修所用的存储、WhatsApp 联系链接的读取与会员会话的读取；浏览器里不传，
  // 分别读地址栏、localStorage 与本次页面加载唯一的读取（api/siteSettings.ts、api/memberSession.ts）。
  initialPath?: string | undefined;
  storage?: LanguageStorage | null | undefined;
  whatsAppContact?: WhatsAppContactReader | undefined;
  memberSession?: MemberSessionReader | undefined;
}

// 会员会话只由前台页头（SiteFrame）与会员页面读取，后台页不读取，也不因此发请求。
export default function App({ initialPath, storage, whatsAppContact, memberSession }: AppProps) {
  return (
    <LanguageProvider storage={storage}>
      <WhatsAppContactProvider reader={whatsAppContact}>
        <MemberSessionProvider reader={memberSession}>
          <RouterProvider initialPath={initialPath}>
            <CurrentPage storage={storage} />
          </RouterProvider>
        </MemberSessionProvider>
      </WhatsAppContactProvider>
    </LanguageProvider>
  );
}
