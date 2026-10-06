import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, MouseEvent } from "react";

import { readAdminSession, submitAdminLogin, submitAdminLogout } from "../api/admin";
import type { AdminSession, LoginReply, SessionRead } from "../api/admin";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { useRouter } from "../router";

// 后台登录页 A01（docs/UX.md「管理后台总体」与 A01）：管理员以用户名与密码登录，调用 SHOP-TASK-036 的三个接口。
// 不套前台的站点框架（App.tsx 对 /admin 开头的路由直接渲染页面）；根元素为 acs-admin，深浅色随管理员设备设置，
// 服务端渲染与首次客户端渲染都是浅色，挂载后才读 prefers-color-scheme。界面语言沿用 LanguageProvider（默认英文）。
// 过渡规则（Kelvin 2026-10-05，docs/HANDOFF.md）：A02 上线前登录成功后留在本页，面板保留标题与语言切换，
// 其下显示 admin.logged_in 与次要样式的 admin.logout；后台框架与「登录后进入 A02」留给 A02 页面任务。
// 用户名与密码只在页面内存（React 状态）与登录请求体里，每次提交后清空密码框；CSRF 令牌只在内存与退出请求头里。
// 表单不用浏览器自带的必填校验（那不是 UX-COPY 文字）：任一项为空时提交按钮禁用。

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
function useColorMode(): ColorMode {
  const [mode, setMode] = useState<ColorMode>("light");
  useEffect(() => watchColorScheme(browserMatchMedia(), setMode), []);
  return mode;
}

export type SignedIn = { status: "in"; username: string; csrfToken: string };

// 面板的内容：会话请求返回前（loading）只有标题与语言切换；form 为登录表单；in 为已登录状态（过渡规则）。
export type AdminScreen = { status: "loading" } | { status: "form" } | SignedIn;

export interface AdminState {
  screen: AdminScreen;
  error: CopyKey | null;
}

export const INITIAL_STATE: AdminState = { screen: { status: "loading" }, error: null };

const FORM: AdminScreen = { status: "form" };

export interface LoginFields {
  username: string;
  password: string;
}

export const EMPTY_LOGIN: LoginFields = { username: "", password: "" };

// 用户名与密码都非空且没有进行中的请求时才可提交；内容原样提交，由服务端判定。
export function canLogIn(fields: LoginFields, busy: boolean): boolean {
  return !busy && fields.username !== "" && fields.password !== "";
}

// 登录失败的提示：401 不区分用户名不存在与密码错误；其他意外回答显示 common.error_retry。
export const LOGIN_ERROR: Readonly<Record<Exclude<LoginReply, "ok">, CopyKey>> = {
  rejected: "admin.login_failed",
  locked: "admin.locked",
  unavailable: "common.service_unavailable",
  network: "common.network_check",
  failed: "common.error_retry",
};

function signedIn(session: AdminSession): SignedIn {
  return { status: "in", username: session.username, csrfToken: session.csrf_token };
}

// 打开本页时按会话接口决定面板：200 已登录，401 登录表单，其他结果登录表单与 common.error_retry。
export function sessionState(read: SessionRead): AdminState {
  switch (read.kind) {
    case "ok":
      return { screen: signedIn(read.session), error: null };
    case "none":
      return { screen: FORM, error: null };
    case "failed":
    case "network":
      return { screen: FORM, error: "common.error_retry" };
  }
}

// 登录：204 后再取会话并显示已登录状态（取不到时留在表单并显示 common.error_retry）；其余回答留在表单并显示对应提示。
export async function logIn(fields: LoginFields, signal?: AbortSignal): Promise<AdminState> {
  const reply = await submitAdminLogin(fields.username, fields.password);
  if (reply !== "ok") {
    return { screen: FORM, error: LOGIN_ERROR[reply] };
  }
  const read = await readAdminSession(signal);
  return read.kind === "ok" ? { screen: signedIn(read.session), error: null } : { screen: FORM, error: "common.error_retry" };
}

// 提交登录：先清空密码框（保留用户名），再以提交时的值发请求。
export function beginLogIn(fields: LoginFields, setFields: (fields: LoginFields) => void, signal?: AbortSignal): Promise<AdminState> {
  setFields({ username: fields.username, password: "" });
  return logIn(fields, signal);
}

// 退出：204 或 401 回到登录表单；403 重新取会话（取得新令牌）并显示 common.error_retry；
// 网络中断显示 common.network_check、其他结果显示 common.error_retry，都保持已登录状态。
export async function logOut(current: SignedIn, signal?: AbortSignal): Promise<AdminState> {
  const reply = await submitAdminLogout(current.csrfToken);
  switch (reply) {
    case "done":
      return { screen: FORM, error: null };
    case "network":
      return { screen: current, error: "common.network_check" };
    case "failed":
      return { screen: current, error: "common.error_retry" };
    case "csrf": {
      const read = await readAdminSession(signal);
      if (read.kind === "ok") {
        return { screen: signedIn(read.session), error: "common.error_retry" };
      }
      return { screen: read.kind === "none" ? FORM : current, error: "common.error_retry" };
    }
  }
}

const LANGUAGE_LABEL: Readonly<Record<Language, CopyKey>> = {
  en: "common.lang_en",
  zh: "common.lang_zh",
  ms: "common.lang_ms",
};

// 语言选项：链接指向本页（网址里不带语言），点击只切换界面语言并保存在本浏览器（与前台页头相同）。
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

// 标题与语言切换：桌面为三种语言的链接；手机为显示当前语言名称的按钮，展开后列出三种语言（与前台页头手机版相同）。
// 两者都渲染，由 site.css 按宽度显隐。
function PanelHead() {
  const t = useCopy();
  const { language } = useLanguage();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  return (
    <>
      <div className="site-admin-login__head">
        <h1 className="acs-admin__h">{t("admin.login_title")}</h1>
        <nav className="acs-admin__lang site-admin-login__lang-links">
          <LanguageLinks />
        </nav>
        <button
          className="acs-admin__btn acs-admin__btn--secondary site-admin-login__lang-button"
          type="button"
          aria-expanded={open}
          aria-controls={menuId}
          onClick={() => {
            setOpen((current) => !current);
          }}
        >
          <span>{t(LANGUAGE_LABEL[language])}</span>
          <ChevronIcon />
        </button>
      </div>
      <nav id={menuId} className="acs-admin__lang site-admin-login__lang-menu" hidden={!open}>
        <LanguageLinks
          onChosen={() => {
            setOpen(false);
          }}
        />
      </nav>
    </>
  );
}

function Alert({ error }: { error: CopyKey }) {
  const t = useCopy();
  return (
    <div className="acs-admin__alert" role="alert">
      <span>{t(error)}</span>
    </div>
  );
}

export interface AdminLoginViewProps {
  mode: ColorMode;
  state: AdminState;
  fields: LoginFields;
  busy: boolean;
  onChange: (fields: LoginFields) => void;
  onLogIn: () => void;
  onLogOut: () => void;
}

// 输入框不设 name：不用脚本时表单（method="post"）也提交不出用户名或密码，更不会进网址。
// 错误提示在提交按钮之后（UX A01 线框）。
export function AdminLoginView({ mode, state, fields, busy, onChange, onLogIn, onLogOut }: AdminLoginViewProps) {
  const t = useCopy();
  const { screen, error } = state;
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onLogIn();
  };
  return (
    <div className="acs-admin site-admin-login" data-mode={mode}>
      <div className="acs-admin__banner">
        <span className="acs-tag acs-tag--demo">{t("common.demo_badge")}</span>
        {t("admin.demo_banner")}
      </div>
      <main className="site-admin-login__main" aria-busy={screen.status === "loading"}>
        <div className="acs-admin__panel site-admin-login__panel">
          <PanelHead />
          {screen.status === "form" && (
            <form className="site-admin-login__body" method="post" noValidate onSubmit={handleSubmit}>
              <label className="acs-admin__field">
                {t("admin.username")}
                <input
                  className="acs-admin__input"
                  autoComplete="username"
                  value={fields.username}
                  onChange={(event) => {
                    onChange({ ...fields, username: event.currentTarget.value });
                  }}
                />
              </label>
              <label className="acs-admin__field">
                {t("auth.password")}
                <input
                  className="acs-admin__input"
                  type="password"
                  autoComplete="current-password"
                  value={fields.password}
                  onChange={(event) => {
                    onChange({ ...fields, password: event.currentTarget.value });
                  }}
                />
              </label>
              <button className="acs-admin__btn site-admin-login__submit" type="submit" disabled={!canLogIn(fields, busy)}>
                {t("auth.login_submit")}
              </button>
              {error !== null && <Alert error={error} />}
            </form>
          )}
          {screen.status === "in" && (
            <div className="site-admin-login__body">
              <p>{t("admin.logged_in", { username: screen.username })}</p>
              <button className="acs-admin__btn acs-admin__btn--secondary site-admin-login__logout" type="button" disabled={busy} onClick={onLogOut}>
                {t("admin.logout")}
              </button>
              {error !== null && <Alert error={error} />}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default function AdminLoginPage() {
  const mode = useColorMode();
  const [state, setState] = useState<AdminState>(INITIAL_STATE);
  const [fields, setFields] = useState<LoginFields>(EMPTY_LOGIN);
  const [busy, setBusy] = useState(false);
  // 离开页面时中止进行中的会话请求，之后不再更新页面。
  const lifetime = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void readAdminSession(controller.signal).then((read) => {
      if (!controller.signal.aborted) {
        setState(sessionState(read));
      }
    });
    return () => {
      controller.abort();
    };
  }, []);

  const settle = (next: AdminState) => {
    if (lifetime.current?.signal.aborted) {
      return;
    }
    setState(next);
    setBusy(false);
  };

  return (
    <AdminLoginView
      mode={mode}
      state={state}
      fields={fields}
      busy={busy}
      onChange={setFields}
      onLogIn={() => {
        if (state.screen.status !== "form" || !canLogIn(fields, busy)) {
          return;
        }
        setBusy(true);
        setState({ screen: state.screen, error: null });
        void beginLogIn(fields, setFields, lifetime.current?.signal).then(settle);
      }}
      onLogOut={() => {
        if (state.screen.status !== "in" || busy) {
          return;
        }
        setBusy(true);
        setState({ screen: state.screen, error: null });
        void logOut(state.screen, lifetime.current?.signal).then(settle);
      }}
    />
  );
}
