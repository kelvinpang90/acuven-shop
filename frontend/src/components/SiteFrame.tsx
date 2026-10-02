import { useId, useState } from "react";
import type { FormEvent, MouseEvent, ReactNode } from "react";

import { BRAND, LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { keywordSearch, parseProductListQuery } from "../pages/productListQuery";
import { HOME_PATH, isRoutePath, Link, PRODUCTS_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";
import { storeDesign } from "../storeDesign";

// 全站框架（docs/UX.md「全局框架」）：演示横幅、页头、页面主体、页脚。
// 结构与类名沿用 docs/design/pages/ 的静态页面；桌面与手机的差异只由 styles/site.css 的媒体查询切换。
//
// 页头搜索框跳到带搜索词的商品列表 P02。不在本任务的部分：购物车（P04）随对应页面加入；WhatsApp 联系链接的配置来源
// 由之后单独登记的任务提供，在那之前页脚不渲染 WhatsApp 按钮（UX Q10：配置缺失时隐藏）。不设站内联系表单。

interface NavItem {
  path: string;
  label: CopyKey;
}

// 页头导航（UX「全局框架」的顺序）；只渲染路由表里已有的项，尚未实现的页面一律不出现。
// 登录与会员中心二选一、购物车数量随会员与购物车任务加入。
const HEADER_NAV: readonly NavItem[] = [
  { path: "/products", label: "common.nav_shop" },
  { path: "/track", label: "common.nav_track" },
  { path: "/login", label: "common.nav_login" },
];

const LANGUAGE_LABEL: Readonly<Record<Language, CopyKey>> = {
  en: "common.lang_en",
  zh: "common.lang_zh",
  ms: "common.lang_ms",
};

function availableNav(): { path: RoutePath; label: CopyKey }[] {
  const items: { path: RoutePath; label: CopyKey }[] = [];
  for (const item of HEADER_NAV) {
    if (isRoutePath(item.path)) {
      items.push({ path: item.path, label: item.label });
    }
  }
  return items;
}

export function DemoHint({ children }: { children: ReactNode }) {
  return (
    <p className="acs-hint">
      <svg className="acs-hint__mark" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 2.8l2.8 5.8 6.3.9-4.6 4.4 1.1 6.3L12 17.2l-5.6 3 1.1-6.3L2.9 9.5l6.3-.9z" />
      </svg>
      <span>{children}</span>
    </p>
  );
}

// 请求失败时在对应区块或列表的位置显示，不影响页头页脚与其他区块。
export function ErrorNotice() {
  const t = useCopy();
  return (
    <p className="acs-alert acs-alert--danger site-notice" role="alert">
      {t("common.error_retry")}
    </p>
  );
}

// 演示横幅：常驻、不可关闭，没有任何关闭控件；桌面与手机两句由 site.css 只显示其一。
function DemoBanner() {
  const t = useCopy();
  return (
    <div className="acs-banner">
      <span className="acs-tag acs-tag--demo">{t("common.demo_badge")}</span>
      <span className="site-desktop-only">{t("common.demo_banner")}</span>
      <span className="site-phone-only">{t("common.demo_banner_short")}</span>
    </div>
  );
}

// 语言选项：链接指向当前页面本身（网址里不带语言），点击只切换语言并保存在本浏览器。
function LanguageLinks({ onChosen }: { onChosen?: () => void }) {
  const t = useCopy();
  const { language, setLanguage } = useLanguage();
  const { path } = useRouter();
  return LANGUAGES.map((option) => (
    <a
      key={option}
      href={path}
      lang={htmlLang(option)}
      aria-current={option === language ? "true" : undefined}
      onClick={(event: MouseEvent<HTMLAnchorElement>) => {
        event.preventDefault();
        setLanguage(option);
        onChosen?.();
      }}
    >
      {t(LANGUAGE_LABEL[option])}
    </a>
  ));
}

function ChevronIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

// 页头搜索（UX「全局框架」）：提交后跳到只带该搜索词的商品列表。不用脚本时按普通表单以 GET 打开 /products?q=…。
// 在商品列表上，框里预先填入当前的搜索词。
function HeaderSearch({ onSearched }: { onSearched: () => void }) {
  const t = useCopy();
  const { path, search, navigate } = useRouter();
  const current = path === PRODUCTS_PATH ? parseProductListQuery(search).q : "";
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get("q");
    navigate(PRODUCTS_PATH, keywordSearch(typeof value === "string" ? value : ""));
    onSearched();
  };
  return (
    <form className="acs-search site-header__search" role="search" action={PRODUCTS_PATH} method="get" onSubmit={handleSubmit}>
      <input
        key={current}
        className="acs-input"
        type="search"
        name="q"
        maxLength={100}
        defaultValue={current}
        aria-label={t("list.search_placeholder")}
        placeholder={t("list.search_placeholder")}
      />
      <button className="acs-btn acs-btn--secondary site-desktop-only" type="submit">
        {t("common.search")}
      </button>
    </form>
  );
}

type OpenPanel = "menu" | "language" | null;

function SiteHeader() {
  const t = useCopy();
  const { language } = useLanguage();
  const { path } = useRouter();
  const [openPanel, setOpenPanel] = useState<OpenPanel>(null);
  const menuId = useId();
  const languageId = useId();
  const nav = availableNav();
  const close = () => {
    setOpenPanel(null);
  };
  const toggle = (panel: Exclude<OpenPanel, null>) => {
    setOpenPanel((current) => (current === panel ? null : panel));
  };

  return (
    <header className="acs-header">
      <div className="acs-header__row site-header__top">
        <button
          className="acs-btn acs-btn--quiet site-phone-only site-header__menu-button"
          type="button"
          aria-label={t("common.nav_menu")}
          aria-expanded={openPanel === "menu"}
          aria-controls={menuId}
          onClick={() => {
            toggle("menu");
          }}
        >
          <MenuIcon />
        </button>
        <Link className="acs-brand" to={HOME_PATH} onClick={close}>
          {BRAND}
        </Link>
        <HeaderSearch onSearched={close} />
        <nav className="acs-lang site-desktop-only site-header__end">
          <LanguageLinks />
        </nav>
        <button
          className="acs-btn acs-btn--quiet site-phone-only site-header__end site-header__lang-button"
          type="button"
          aria-expanded={openPanel === "language"}
          aria-controls={languageId}
          onClick={() => {
            toggle("language");
          }}
        >
          <span>{t(LANGUAGE_LABEL[language])}</span>
          <ChevronIcon />
        </button>
      </div>
      {nav.length > 0 && (
        <div className="acs-header__row site-desktop-only">
          <nav className="acs-nav">
            {nav.map((item) => (
              <Link key={item.path} to={item.path} aria-current={item.path === path ? "page" : undefined}>
                {t(item.label)}
              </Link>
            ))}
          </nav>
        </div>
      )}
      <div id={menuId} className="site-phone-only site-header__panel" hidden={openPanel !== "menu"}>
        <nav className="acs-nav site-header__stack">
          {nav.map((item) => (
            <Link key={item.path} to={item.path} aria-current={item.path === path ? "page" : undefined} onClick={close}>
              {t(item.label)}
            </Link>
          ))}
          <Link to="/privacy" aria-current={path === "/privacy" ? "page" : undefined} onClick={close}>
            {t("common.nav_privacy")}
          </Link>
        </nav>
        <nav className="acs-lang site-header__stack">
          <LanguageLinks onChosen={close} />
        </nav>
      </div>
      <div id={languageId} className="site-phone-only site-header__panel" hidden={openPanel !== "language"}>
        <nav className="acs-lang site-header__stack">
          <LanguageLinks onChosen={close} />
        </nav>
      </div>
    </header>
  );
}

function SiteFooter() {
  const t = useCopy();
  return (
    <footer className="acs-footer site-footer">
      <div className="site-footer__main">
        <span className="acs-brand">{BRAND}</span>
        <p className="acs-body-s site-footer__text">{t("common.footer_demo")}</p>
        <Link className="site-footer__link" to="/privacy">
          {t("common.nav_privacy")}
        </Link>
      </div>
    </footer>
  );
}

// 前台根元素：class acs 与主题取值（storeDesign.ts）；不设 data-accent，即该主题的默认主色。
export default function SiteFrame({ children }: { children: ReactNode }) {
  return (
    <div className="acs site" data-shop-theme={storeDesign.theme} data-mode={storeDesign.mode}>
      <DemoBanner />
      <SiteHeader />
      {children}
      <SiteFooter />
    </div>
  );
}
