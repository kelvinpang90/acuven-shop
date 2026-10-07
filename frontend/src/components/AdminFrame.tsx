import { useEffect, useId, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";

import { readAdminSession, submitAdminLogout } from "../api/admin";
import type { SessionRead } from "../api/admin";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_ORDERS_PATH, ADMIN_REFUNDS_PATH, Link, useRouter } from "../router";
import type { RoutePath } from "../router";

// 后台框架（docs/UX.md「管理后台总体」，视觉稿 A02-desktop、A02-phone-list 与 A00-phone-menu 的框架部分）：A01 以外的后台页共用。
// 顶部常驻演示横幅；admin.logout 桌面在横幅右侧、手机在顶栏右端；桌面左侧导航（底部为语言切换）+ 右侧内容；
// 手机顶栏为 ☰ 与当前导航项名称，点开后列出导航项与语言切换。桌面与手机两套都渲染，由 site.css 按宽度显隐。
// 导航只列已上线的页面（Kelvin 2026-10-06，docs/HANDOFF.md）：目前依次为订单与退款。
// 打开时读后台会话：200 才显示页面内容，401 换成登录页 A01；CSRF 令牌只在 React 状态与退出请求头里。
// 根元素为 acs-admin，深浅色随管理员设备设置：服务端渲染与首次客户端渲染都是浅色，挂载后才读 prefers-color-scheme。

export type ColorMode = "light" | "dark";

export const DARK_SCHEME_QUERY = "(prefers-color-scheme: dark)";

// matchMedia 返回值里本页用到的部分。
export interface SchemeQuery {
  readonly matches: boolean;
  addEventListener(type: "change", listener: () => void): void;
  removeEventListener(type: "change", listener: () => void): void;
}

export type MatchMedia = (query: string) => SchemeQuery;

// 按设备的深浅色设置报告一次当前值，并在设置变化时再报告；返回取消订阅。没有 matchMedia 时保持浅色、不报告。
export function watchColorScheme(matchMedia: MatchMedia | null, onMode: (mode: ColorMode) => void): () => void {
  if (matchMedia === null) {
    return () => undefined;
  }
  const query = matchMedia(DARK_SCHEME_QUERY);
  const report = () => {
    onMode(query.matches ? "dark" : "light");
  };
  report();
  query.addEventListener("change", report);
  return () => {
    query.removeEventListener("change", report);
  };
}

function browserMatchMedia(): MatchMedia | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" ? (query) => window.matchMedia(query) : null;
}

// 首次渲染为浅色，挂载后按设备设置并随之变化。
export function useColorMode(): ColorMode {
  const [mode, setMode] = useState<ColorMode>("light");
  useEffect(() => watchColorScheme(browserMatchMedia(), setMode), []);
  return mode;
}

export const LANGUAGE_LABEL: Readonly<Record<Language, CopyKey>> = {
  en: "common.lang_en",
  zh: "common.lang_zh",
  ms: "common.lang_ms",
};

// 语言选项：链接指向本页（网址里不带语言），点击只切换界面语言并保存在本浏览器（与前台页头相同）。
export function LanguageLinks({ onChosen }: { onChosen?: () => void }) {
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

export function AdminAlert({ error }: { error: CopyKey }) {
  const t = useCopy();
  return (
    <div className="acs-admin__alert" role="alert">
      <span>{t(error)}</span>
    </div>
  );
}

// 导航项：只列已上线的页面，依 UX「管理后台总体」的顺序；其余页面上线时各自加上。
export type AdminNavItem = "orders" | "refunds";

export const ADMIN_NAV: readonly AdminNavItem[] = ["orders", "refunds"];

const NAV_ENTRY: Readonly<Record<AdminNavItem, { path: RoutePath; label: CopyKey }>> = {
  orders: { path: ADMIN_ORDERS_PATH, label: "admin.nav_orders" },
  refunds: { path: ADMIN_REFUNDS_PATH, label: "admin.nav_refunds" },
};

// 框架的状态：会话请求返回前（loading）、会话读取失败（failed，内容区只有 common.error_retry）、
// 已登录（in，显示页面内容；error 为退出失败的提示）。
export type FrameState = { status: "loading" } | { status: "failed" } | { status: "in"; csrfToken: string; error: CopyKey | null };

export const INITIAL_FRAME: FrameState = { status: "loading" };

// 一步的结果：留在本页显示某个状态，或回到登录页 A01（login）。
export type FrameStep = FrameState | "login";

// 打开时按会话接口决定：200 显示内容，401 回到 A01，其他结果（含网络中断）显示 common.error_retry。
export function sessionStep(read: SessionRead): FrameStep {
  switch (read.kind) {
    case "ok":
      return { status: "in", csrfToken: read.session.csrf_token, error: null };
    case "none":
      return "login";
    case "failed":
    case "network":
      return { status: "failed" };
  }
}

// 退出：204 或 401 回到 A01；403 重新取会话（取得新令牌）并显示 common.error_retry，取到 401 时回到 A01；
// 网络中断显示 common.network_check、其他结果显示 common.error_retry，都留在本页。
export async function logOut(csrfToken: string, signal?: AbortSignal): Promise<FrameStep> {
  const reply = await submitAdminLogout(csrfToken);
  switch (reply) {
    case "done":
      return "login";
    case "network":
      return { status: "in", csrfToken, error: "common.network_check" };
    case "failed":
      return { status: "in", csrfToken, error: "common.error_retry" };
    case "csrf": {
      const read = await readAdminSession(signal);
      if (read.kind === "none") {
        return "login";
      }
      return { status: "in", csrfToken: read.kind === "ok" ? read.session.csrf_token : csrfToken, error: "common.error_retry" };
    }
  }
}

// 框架对一步结果的处理：显示状态，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface FrameMoves {
  show: (state: FrameState) => void;
  replace: (path: RoutePath) => void;
}

// 离开页面（signal 已中止）之后不再更新。
export function settleFrame(step: FrameStep, signal: AbortSignal, moves: FrameMoves): void {
  if (signal.aborted) {
    return;
  }
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

export async function openFrame(signal: AbortSignal, moves: FrameMoves): Promise<void> {
  const read = await readAdminSession(signal);
  settleFrame(sessionStep(read), signal, moves);
}

export async function leaveFrame(csrfToken: string, signal: AbortSignal, moves: FrameMoves): Promise<void> {
  const step = await logOut(csrfToken, signal);
  settleFrame(step, signal, moves);
}

function MenuIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

function NavLinks({ current, onChosen }: { current: AdminNavItem; onChosen?: () => void }) {
  const t = useCopy();
  return ADMIN_NAV.map((item) => (
    <Link key={item} to={NAV_ENTRY[item].path} aria-current={item === current ? "page" : undefined} onClick={onChosen}>
      {t(NAV_ENTRY[item].label)}
    </Link>
  ));
}

export interface AdminFrameViewProps {
  mode: ColorMode;
  current: AdminNavItem;
  state: FrameState;
  busy: boolean;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onLogOut: () => void;
  children: ReactNode;
}

// 会话请求返回前即显示横幅、导航与语言切换；内容区标 aria-busy，退出按钮禁用（还没有 CSRF 令牌）。
// 手机菜单（A00-phone-menu）展开时顶栏的图形换成 ✕、名称换成 common.nav_menu，菜单占据内容区。
export function AdminFrameView({ mode, current, state, busy, menuOpen, onToggleMenu, onCloseMenu, onLogOut, children }: AdminFrameViewProps) {
  const t = useCopy();
  const menuId = useId();
  const logout = (
    <button className="acs-admin__btn acs-admin__btn--secondary site-admin__logout" type="button" disabled={state.status !== "in" || busy} onClick={onLogOut}>
      {t("admin.logout")}
    </button>
  );
  return (
    <div className="acs-admin site-admin" data-mode={mode}>
      <div className="site-admin__top">
        <div className="acs-admin__banner">
          <span className="acs-tag acs-tag--demo">{t("common.demo_badge")}</span>
          {t("admin.demo_banner")}
        </div>
        <div className="acs-admin__nav site-admin__banner-end">{logout}</div>
      </div>
      <div className="acs-admin__nav site-admin__bar">
        <button
          className="acs-admin__btn acs-admin__btn--secondary site-admin__menu-button"
          type="button"
          aria-label={t("common.nav_menu")}
          aria-expanded={menuOpen}
          aria-controls={menuId}
          onClick={onToggleMenu}
        >
          {menuOpen ? <CloseIcon /> : <MenuIcon />}
        </button>
        <span className="site-admin__bar-title">{menuOpen ? t("common.nav_menu") : t(NAV_ENTRY[current].label)}</span>
        {logout}
      </div>
      <div id={menuId} className="acs-admin__nav site-admin__menu" hidden={!menuOpen}>
        <nav className="site-admin__menu-nav">
          <NavLinks current={current} onChosen={onCloseMenu} />
        </nav>
        <div className="acs-admin__lang site-admin__lang">
          <LanguageLinks />
        </div>
      </div>
      <div className="acs-admin__shell site-admin__shell">
        <nav className="acs-admin__nav site-admin__nav">
          <NavLinks current={current} />
          <div className="acs-admin__lang site-admin__lang">
            <LanguageLinks />
          </div>
        </nav>
        <main className="acs-admin__main" aria-busy={state.status === "loading"}>
          {state.status === "failed" && <AdminAlert error="common.error_retry" />}
          {state.status === "in" && state.error !== null && <AdminAlert error={state.error} />}
          {state.status === "in" && children}
        </main>
      </div>
    </div>
  );
}

export interface AdminFrameProps {
  current: AdminNavItem;
  children: ReactNode;
}

export default function AdminFrame({ current, children }: AdminFrameProps) {
  const mode = useColorMode();
  const { replace } = useRouter();
  const [state, setState] = useState<FrameState>(INITIAL_FRAME);
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // 离开页面时中止进行中的会话请求，之后不再更新页面。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void openFrame(controller.signal, { show: setState, replace });
    return () => {
      controller.abort();
    };
  }, [replace]);

  return (
    <AdminFrameView
      mode={mode}
      current={current}
      state={state}
      busy={busy}
      menuOpen={menuOpen}
      onToggleMenu={() => {
        setMenuOpen((open) => !open);
      }}
      onCloseMenu={() => {
        setMenuOpen(false);
      }}
      onLogOut={() => {
        const controller = lifetime.current;
        if (state.status !== "in" || busy || controller === null) {
          return;
        }
        setBusy(true);
        setState({ ...state, error: null });
        void leaveFrame(state.csrfToken, controller.signal, {
          show: (next) => {
            setState(next);
            setBusy(false);
          },
          replace,
        });
      }}
    >
      {children}
    </AdminFrameView>
  );
}
