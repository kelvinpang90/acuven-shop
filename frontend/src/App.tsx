import type { ComponentType } from "react";

import SiteFrame from "./components/SiteFrame";
import { LanguageProvider } from "./i18n/language";
import type { LanguageStorage } from "./i18n/language";
import AdminLoginPage from "./pages/AdminLoginPage";
import AdminOrdersPage from "./pages/AdminOrdersPage";
import AdminRefundsPage from "./pages/AdminRefundsPage";
import AdminStockResetsPage from "./pages/AdminStockResetsPage";
import CartPage from "./pages/CartPage";
import CheckoutPage from "./pages/CheckoutPage";
import HomePage from "./pages/HomePage";
import PayPage from "./pages/PayPage";
import PayResultPage from "./pages/PayResultPage";
import PrivacyPage from "./pages/PrivacyPage";
import ProductDetailPage from "./pages/ProductDetailPage";
import ProductListPage from "./pages/ProductListPage";
import RefundPage from "./pages/RefundPage";
import TrackOrderPage from "./pages/TrackOrderPage";
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
  "/admin/login": AdminLoginPage,
  "/admin/orders": AdminOrdersPage,
  // 订单详情与列表是同一页（桌面列表在左、详情在右，手机只显示详情）。
  "/admin/orders/:id": AdminOrdersPage,
  // 全部退款申请与只含某张订单的申请是同一页（后者按路径里的订单内部 ID 筛选）。
  "/admin/refunds": AdminRefundsPage,
  "/admin/refunds/order/:orderId": AdminRefundsPage,
  // 退款详情与列表是同一页（桌面列表在左、详情在右，手机只显示详情）。
  "/admin/refunds/:id": AdminRefundsPage,
  "/admin/stock-resets": AdminStockResetsPage,
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
  // 测试用：给定初始路径与语言、店铺装修所用的存储；浏览器里不传，分别读地址栏与 localStorage。
  initialPath?: string | undefined;
  storage?: LanguageStorage | null | undefined;
}

export default function App({ initialPath, storage }: AppProps) {
  return (
    <LanguageProvider storage={storage}>
      <RouterProvider initialPath={initialPath}>
        <CurrentPage storage={storage} />
      </RouterProvider>
    </LanguageProvider>
  );
}
