import type { ComponentType } from "react";

import SiteFrame from "./components/SiteFrame";
import { LanguageProvider } from "./i18n/language";
import type { LanguageStorage } from "./i18n/language";
import HomePage from "./pages/HomePage";
import PrivacyPage from "./pages/PrivacyPage";
import ProductDetailPage from "./pages/ProductDetailPage";
import ProductListPage from "./pages/ProductListPage";
import { RouterProvider, useRouter } from "./router";
import type { RoutePattern } from "./router";

// 按 router.tsx 的路由表穷举：表里每个模式恰好对应一个页面。
const PAGES: Readonly<Record<RoutePattern, ComponentType>> = {
  "/": HomePage,
  "/products": ProductListPage,
  "/products/:slug": ProductDetailPage,
  "/privacy": PrivacyPage,
};

// 以实际路径为 key：从一件商品换到另一件时页面重新开始（规格、数量与提示不带过去）。
function CurrentPage() {
  const { path, pattern } = useRouter();
  const Page = PAGES[pattern];
  return <Page key={path} />;
}

interface AppProps {
  // 测试用：给定初始路径与语言存储；浏览器里不传，分别读地址栏与 localStorage。
  initialPath?: string | undefined;
  storage?: LanguageStorage | null | undefined;
}

export default function App({ initialPath, storage }: AppProps) {
  return (
    <LanguageProvider storage={storage}>
      <RouterProvider initialPath={initialPath}>
        <SiteFrame>
          <CurrentPage />
        </SiteFrame>
      </RouterProvider>
    </LanguageProvider>
  );
}
