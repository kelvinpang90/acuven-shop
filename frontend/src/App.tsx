import type { ComponentType } from "react";

import SiteFrame from "./components/SiteFrame";
import { LanguageProvider } from "./i18n/language";
import type { LanguageStorage } from "./i18n/language";
import HomePage from "./pages/HomePage";
import PrivacyPage from "./pages/PrivacyPage";
import { RouterProvider, useRouter } from "./router";
import type { RoutePath } from "./router";

// 按 router.tsx 的路由表穷举：表里每个路径恰好对应一个页面。
const PAGES: Readonly<Record<RoutePath, ComponentType>> = {
  "/": HomePage,
  "/privacy": PrivacyPage,
};

function CurrentPage() {
  const { path } = useRouter();
  const Page = PAGES[path];
  return <Page />;
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
