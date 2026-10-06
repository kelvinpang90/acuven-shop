import { useEffect, useId, useState } from "react";
import type { FormEvent, MouseEvent } from "react";

import { readAdminSession, submitAdminLogin, submitAdminLogout } from "../api/admin";
import type { AdminSession, LoginReply, SessionRead } from "../api/admin";
import { LANGUAGES } from "../i18n/copy";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { useRouter } from "../router";
import type { RoutePath } from "../router";

// 后台登录页 A01（docs/UX.md 0.8「管理后台总体」与 A01）：管理员以用户名与密码登录。
// 不套前台框架（App.tsx），根元素为后台的 acs-admin，深浅色随管理员设备设置（prefers-color-scheme），界面语言沿用 LanguageProvider。
// 过渡规则（Kelvin 2026-10-05，docs/HANDOFF.md 0.32）：A02 订单页上线前，登录成功后留在本页，面板保留标题与语言切换，
// 其下显示 admin.logged_in 与 admin.logout；后台框架与「登录后进入 A02」留给 A02 页面任务，届时取消本规则。
// 用户名与密码只在页面内存（React 状态）与登录请求体里，每次提交后清空密码框；CSRF 令牌只在页面内存与退出请求头里。
// 结构与类名沿用 docs/design/pages/A01-*.html；桌面与手机的差异只由 styles/site.css 的媒体查询切换。

export const ADMIN_LOGIN_PATH: RoutePath = "/admin/login";

// 深浅色：首次渲染为 light，挂载后在浏览器里按 prefers-color-scheme 取值并随设备设置变化。
export type AdminMode = "light" | "dark";

export const DARK_SCHEME_QUERY = "(prefers-color-scheme: dark)";

interface MediaQueryLike {
  readonly matches: boolean;
  addEventListener(type: "change", listener: () => void): void;
  removeEventListener(type: "change", listener: () => void): void;
}

export type MatchMedia = (query: string) => MediaQueryLike;

export function adminMode(matchMedia: MatchMedia | null): AdminMode {
  return matchMedia?.(DARK_SCHEME_QUERY).matches ? "dark" : "light";
}

// 订阅设备深浅色的变化；返回取消订阅的函数。没有 matchMedia 时什么也不做。
export function watchAdminMode(matchMedia: MatchMedia | null, onChange: () => void): () => void {
  if (!matchMedia) {
    return () => undefined;
  }
  const query = matchMedia(DARK_SCHEME_QUERY);
  query.addEventListener("change", onChange);
  return () => {
    query.removeEventListener("change", onChange);
  };
}

function browserMatchMedia(): MatchMedia | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" ? (query) => window.matchMedia(query) : null;
}

// 首次渲染（服务端与浏览器都一样）为 light；挂载后按设备设置取值，并订阅之后的变化。
export function useAdminMode(): AdminMode {
  const [mode, setMode] = useState<AdminMode>("light");
  useEffect(() => {
    const matchMedia = browserMatchMedia();
    let live = true;
    const update = () => {
      if (live) {
        setMode(adminMode(matchMedia));
      }
    };
    const stop = watchAdminMode(matchMedia, update);
    queueMicrotask(update);
    return () => {
      live = false;
      stop();
    };
  }, []);
  return mode;
}

export interface LoginFields {
  username: string;
  password: string;
}

export const EMPTY_LOGIN: LoginFields = { username: "", password: "" };

// 用户名与密码都非空且没有进行中的请求时才能提交；不用浏览器自带的必填校验。
export function canSubmitLogin(fields: LoginFields, busy: boolean): boolean {
  return !busy && fields.username !== "" && fields.password !== "";
}

// 每次提交后：清空密码，保留用户名。
export function afterSubmit(fields: LoginFields): LoginFields {
  return { username: fields.username, password: "" };
}

// 页面状态：loading 为会话请求返回前（只有标题与语言切换）；form 为登录表单；signed_in 为已登录（过渡规则）。
// error 为要显示的提示（只是文案键，不含用户名、密码或接口的错误体）。
export type AdminScreen =
  | { kind: "loading" }
  | { kind: "form"; error: CopyKey | null }
  | { kind: "signed_in"; session: AdminSession; error: CopyKey | null };

// 打开本页时的会话：200 已登录，401 登录表单，其他结果登录表单与 common.error_retry。
export function screenForSession(read: SessionRead): AdminScreen {
  switch (read.kind) {
    case "ok":
      return { kind: "signed_in", session: read.session, error: null };
    case "signed_out":
      return { kind: "form", error: null };
    case "failed":
      return { kind: "form", error: "common.error_retry" };
  }
}

// 登录失败的提示：401 admin.login_failed，429 admin.locked，503 common.service_unavailable，网络中断 common.network_check，
// 其他 common.error_retry。
export const LOGIN_ERROR: Readonly<Record<Exclude<LoginReply, "ok">, CopyKey>> = {
  failed: "admin.login_failed",
  locked: "admin.locked",
  unavailable: "common.service_unavailable",
  network: "common.network_check",
  error: "common.error_retry",
};

// 提交登录：204 后再取会话，取到时为已登录；否则留在登录表单并给出提示。
export async function loginAndRead(username: string, password: string): Promise<AdminScreen> {
  const reply = await submitAdminLogin(username, password);
  if (reply !== "ok") {
    return { kind: "form", error: LOGIN_ERROR[reply] };
  }
  const read = await readAdminSession();
  return read.kind === "ok" ? { kind: "signed_in", session: read.session, error: null } : { kind: "form", error: "common.error_retry" };
}

// 退出：204 或 401 回到登录表单；403 重新取会话并显示 common.error_retry（会话仍有效时保持已登录、换用新令牌）；
// 网络中断显示 common.network_check，其他结果显示 common.error_retry，两者都保持已登录状态。
export async function logoutAndSettle(session: AdminSession): Promise<AdminScreen> {
  const reply = await submitAdminLogout(session.csrf_token);
  switch (reply) {
    case "done":
    case "signed_out":
      return { kind: "form", error: null };
    case "csrf": {
      const read = await readAdminSession();
      if (read.kind === "ok") {
        return { kind: "signed_in", session: read.session, error: "common.error_retry" };
      }
      if (read.kind === "signed_out") {
        return { kind: "form", error: "common.error_retry" };
      }
      return { kind: "signed_in", session, error: "common.error_retry" };
    }
    case "network":
      return { kind: "signed_in", session, error: "common.network_check" };
    case "failed":
      return { kind: "signed_in", session, error: "common.error_retry" };
  }
}

const LANGUAGE_LABEL: Readonly<Record<Language, CopyKey>> = {
  en: "common.lang_en",
  zh: "common.lang_zh",
  ms: "common.lang_ms",
};

// 语言选项：链接指向本页（网址里不带语言），点击只切换界面语言（与前台页头相同，保存在本浏览器）。
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

// 面板首行：标题与语言切换。桌面为三种语言的链接；手机为显示当前语言名称的按钮，展开后列出三种语言
// （与前台页头手机版的当前语言按钮同一做法，可访问名称即按钮上的语言名）。两者都渲染，由 site.css 按宽度显隐。
function PanelHead() {
  const t = useCopy();
  const { language } = useLanguage();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <>
      <div className="site-admin__head">
        <h1 className="acs-admin__h">{t("admin.login_title")}</h1>
        <nav className="acs-admin__lang site-desktop-only">
          <LanguageLinks />
        </nav>
        <button
          className="acs-admin__btn acs-admin__btn--secondary site-phone-only site-admin__lang-button"
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => {
            setOpen((current) => !current);
          }}
        >
          <span>{t(LANGUAGE_LABEL[language])}</span>
          <ChevronIcon />
        </button>
      </div>
      <div id={panelId} className="site-phone-only site-admin__lang-panel" hidden={!open}>
        <nav className="acs-admin__lang">
          <LanguageLinks
            onChosen={() => {
              setOpen(false);
            }}
          />
        </nav>
      </div>
    </>
  );
}

function Alert({ message }: { message: CopyKey }) {
  const t = useCopy();
  return (
    <div className="acs-admin__alert" role="alert">
      <span>{t(message)}</span>
    </div>
  );
}

export interface AdminLoginViewProps {
  mode: AdminMode;
  screen: AdminScreen;
  fields: LoginFields;
  busy: boolean;
  onChange: (fields: LoginFields) => void;
  onSubmit: () => void;
  onLogout: () => void;
}

// 输入框不设 name、表单不设必填：不用脚本时表单也不会把用户名或密码放进网址，也不出现浏览器自带的必填提示。
// 错误提示按 UX A01 线框排在提交按钮之后。
export function AdminLoginView({ mode, screen, fields, busy, onChange, onSubmit, onLogout }: AdminLoginViewProps) {
  const t = useCopy();
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit();
  };
  return (
    <div className="acs-admin site-admin" data-mode={mode}>
      <div className="acs-admin__banner">
        <span className="acs-tag acs-tag--demo">{t("common.demo_badge")}</span>
        <span>{t("admin.demo_banner")}</span>
      </div>
      <main className="site-admin__main" aria-busy={screen.kind === "loading" || busy}>
        <div className="acs-admin__panel site-admin__panel">
          <PanelHead />
          {screen.kind === "form" && (
            <form className="site-admin__form" method="post" noValidate onSubmit={handleSubmit}>
              <label className="acs-admin__field">
                {t("admin.username")}
                <input
                  className="acs-admin__input"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
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
              <button className="acs-admin__btn site-admin__submit" type="submit" disabled={!canSubmitLogin(fields, busy)}>
                {t("auth.login_submit")}
              </button>
              {screen.error !== null && <Alert message={screen.error} />}
            </form>
          )}
          {screen.kind === "signed_in" && (
            <div className="site-admin__signed-in">
              <p className="site-admin__text">{t("admin.logged_in", { username: screen.session.username })}</p>
              <button className="acs-admin__btn acs-admin__btn--secondary site-admin__logout" type="button" disabled={busy} onClick={onLogout}>
                {t("admin.logout")}
              </button>
              {screen.error !== null && <Alert message={screen.error} />}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default function AdminLoginPage() {
  const mode = useAdminMode();
  const [screen, setScreen] = useState<AdminScreen>({ kind: "loading" });
  const [fields, setFields] = useState<LoginFields>(EMPTY_LOGIN);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void readAdminSession(controller.signal).then((read) => {
      if (!controller.signal.aborted) {
        setScreen(screenForSession(read));
      }
    });
    return () => {
      controller.abort();
    };
  }, []);

  return (
    <AdminLoginView
      mode={mode}
      screen={screen}
      fields={fields}
      busy={busy}
      onChange={setFields}
      onSubmit={() => {
        if (screen.kind !== "form" || !canSubmitLogin(fields, busy)) {
          return;
        }
        const { username, password } = fields;
        setFields(afterSubmit(fields));
        setBusy(true);
        setScreen({ kind: "form", error: null });
        void loginAndRead(username, password).then((next) => {
          setScreen(next);
          setBusy(false);
        });
      }}
      onLogout={() => {
        if (screen.kind !== "signed_in" || busy) {
          return;
        }
        const { session } = screen;
        setBusy(true);
        setScreen({ kind: "signed_in", session, error: null });
        void logoutAndSettle(session).then((next) => {
          setScreen(next);
          setBusy(false);
        });
      }}
    />
  );
}
