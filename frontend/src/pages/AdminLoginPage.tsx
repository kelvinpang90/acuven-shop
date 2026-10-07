import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { readAdminSession, submitAdminLogin } from "../api/admin";
import type { LoginReply, SessionRead } from "../api/admin";
import { AdminAlert, LANGUAGE_LABEL, LanguageLinks, useColorMode } from "../components/AdminFrame";
import type { ColorMode } from "../components/AdminFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy, useLanguage } from "../i18n/language";
import { ADMIN_ORDERS_PATH, useRouter } from "../router";
import type { RoutePath } from "../router";

export { DARK_SCHEME_QUERY, watchColorScheme } from "../components/AdminFrame";
export type { ColorMode, MatchMedia, SchemeQuery } from "../components/AdminFrame";

// 后台登录页 A01（docs/UX.md「管理后台总体」与 A01）：管理员以邮箱与密码登录，调用 SHOP-TASK-036 的接口。
// 登录名输入框标签为 admin.email（UX 0.10，Kelvin 2026-10-07）：type="text" 加 inputMode="email" 让手机弹出邮箱键盘，
// autocomplete 仍为 username（密码管理按登录名识别），关闭自动大写与拼写检查；内容原样提交，去空白与转小写由服务端做（SHOP-TASK-049），
// 请求体字段名仍为 username。
// 不套前台的站点框架（App.tsx 对 /admin 开头的路由直接渲染页面），也不用后台框架（A01 没有导航与退出按钮）；
// 根元素为 acs-admin，深浅色随管理员设备设置（与后台框架相同，见 components/AdminFrame.tsx）。界面语言沿用 LanguageProvider（默认英文）。
// 去向为 A02（UX A01「去向：A02」）：打开时已有会话即用 replace 进入 /admin/orders，登录成功后用 navigate 进入。
// 用户名与密码只在页面内存（React 状态）与登录请求体里，每次提交后清空密码框。
// 表单不用浏览器自带的必填校验（那不是 UX-COPY 文字）：任一项为空时提交按钮禁用。

// 面板的内容：会话请求返回前（loading）只有标题与语言切换；form 为登录表单。
export type AdminScreen = { status: "loading" } | { status: "form" };

export interface AdminState {
  screen: AdminScreen;
  error: CopyKey | null;
}

export const INITIAL_STATE: AdminState = { screen: { status: "loading" }, error: null };

const FORM: AdminScreen = { status: "form" };

// 进入 A02 订单页（已有会话或登录成功）。
export const TO_ORDERS = "orders";

// 一步的结果：留在本页显示某个状态，或进入 A02。
export type AdminOutcome = AdminState | typeof TO_ORDERS;

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

// 打开本页时按会话接口决定：200 进入 A02，401 登录表单，其他结果登录表单与 common.error_retry。
export function sessionState(read: SessionRead): AdminOutcome {
  switch (read.kind) {
    case "ok":
      return TO_ORDERS;
    case "none":
      return { screen: FORM, error: null };
    case "failed":
    case "network":
      return { screen: FORM, error: "common.error_retry" };
  }
}

// 登录：204 进入 A02（会话由后台框架读取）；其余回答留在表单并显示对应提示。
export async function logIn(fields: LoginFields): Promise<AdminOutcome> {
  const reply = await submitAdminLogin(fields.username, fields.password);
  return reply === "ok" ? TO_ORDERS : { screen: FORM, error: LOGIN_ERROR[reply] };
}

// 提交登录：先清空密码框（保留用户名），再以提交时的值发请求。
export function beginLogIn(fields: LoginFields, setFields: (fields: LoginFields) => void): Promise<AdminOutcome> {
  setFields({ username: fields.username, password: "" });
  return logIn(fields);
}

// 本页对一步结果的处理：显示状态，或经路由进入 A02。
export interface LoginMoves {
  show: (state: AdminState) => void;
  replace: (path: RoutePath) => void;
  navigate: (path: RoutePath) => void;
}

// 打开时已有会话：用 replace 进入 A02，登录页不留在历史记录里。
export function settleSession(outcome: AdminOutcome, moves: LoginMoves): void {
  if (outcome === TO_ORDERS) {
    moves.replace(ADMIN_ORDERS_PATH);
  } else {
    moves.show(outcome);
  }
}

// 登录成功：用 navigate 进入 A02。
export function settleLogIn(outcome: AdminOutcome, moves: LoginMoves): void {
  if (outcome === TO_ORDERS) {
    moves.navigate(ADMIN_ORDERS_PATH);
  } else {
    moves.show(outcome);
  }
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

export interface AdminLoginViewProps {
  mode: ColorMode;
  state: AdminState;
  fields: LoginFields;
  busy: boolean;
  onChange: (fields: LoginFields) => void;
  onLogIn: () => void;
}

// 输入框不设 name：不用脚本时表单（method="post"）也提交不出用户名或密码，更不会进网址。
// 错误提示在提交按钮之后（UX A01 线框）。
export function AdminLoginView({ mode, state, fields, busy, onChange, onLogIn }: AdminLoginViewProps) {
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
                {t("admin.email")}
                <input
                  className="acs-admin__input"
                  type="text"
                  inputMode="email"
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
              <button className="acs-admin__btn site-admin-login__submit" type="submit" disabled={!canLogIn(fields, busy)}>
                {t("auth.login_submit")}
              </button>
              {error !== null && <AdminAlert error={error} />}
            </form>
          )}
        </div>
      </main>
    </div>
  );
}

export default function AdminLoginPage() {
  const mode = useColorMode();
  const { replace, navigate } = useRouter();
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
        settleSession(sessionState(read), { show: setState, replace, navigate });
      }
    });
    return () => {
      controller.abort();
    };
  }, [replace, navigate]);

  const show = (next: AdminState) => {
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
        void beginLogIn(fields, setFields).then((outcome) => {
          if (!lifetime.current?.signal.aborted) {
            settleLogIn(outcome, { show, replace, navigate });
          }
        });
      }}
    />
  );
}
