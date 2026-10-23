import { useEffect, useRef, useState } from "react";

import { DemoHint, ErrorNotice } from "../components/SiteFrame";
import SmsVerify from "../components/SmsVerify";
import type { SmsVerified, SmsVerifyProps } from "../components/SmsVerify";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";
import { guestPath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";
import { LOADING_SETTINGS, loadSettings, redirectLater, smsVerifiedCopy, timerWait } from "./LoginPage";
import type { LoginSettings, Wait } from "./LoginPage";

// 会员注册 P11（docs/UX.md 0.12 P11，docs/UX-COPY.md 0.13，视觉稿 P11-desktop 与 P11-phone；DESIGN 1.12「权限与资料保护」第 3 条与
// 「失败、并发与重试」，HANDOFF 0.58）。居中单栏卡片：标题 auth.register_title、★ auth.register_demo_hint、auth.same_flow_note、
// V1（用途 register，与短信登录同一流程：号码已注册则登录，未注册则由服务端创建无密码会员并登录；短信失败不创建账号）与去 /login 的
// common.nav_login。打开页面时照登录页 loadSettings 读一次站点设置（切换语言不重新读取），读完之前 V1 所在的内容区标 aria-busy、
// 不渲染 V1；读取失败只显示 common.error_retry；开关关闭时 V1 自己换成 auth.sms_paused 与 auth.continue_guest。
// 验证通过后按 created 显示 auth.verified_registered 或 auth.verified_login，VERIFIED_REDIRECT_MS 之后以路由的 replace 前往来源页
// （router.tsx 的 verifiedPath，经登录页的 redirectLater）。号码与验证码只在 V1 的状态与请求体里；离开页面时中止读取与计时。

const LOGIN_PATH: RoutePath = "/login";

// 站点设置：与登录页相同（读取中、读取失败、已读到开关的值与人机挑战公开 key）。
export type RegisterSettings = LoginSettings;

// 嵌入的 V1：用途 register，开关的值与公开 key 取自站点设置，auth.continue_guest 的去向由页面给出（router.tsx 的 guestPath）。
export function registerVerify(settings: { smsEnabled: boolean; siteKey: string | null }, guestTo: RoutePath, onVerified: (verified: SmsVerified) => void): SmsVerifyProps {
  return { purpose: "register", smsEnabled: settings.smsEnabled, siteKey: settings.siteKey, guestTo, onVerified };
}

// 页面对结果的处理：显示验证通过的文字，或用路由的 replace 前往成功后的去向。
export interface RegisterMoves {
  verified: (copy: CopyKey) => void;
  replace: (path: RoutePath) => void;
}

// 短信验证通过（V1 的回调）：显示 auth.verified_*，VERIFIED_REDIRECT_MS 之后以 replace 前往来源页（没有来源时首页）；
// signal 中止（离开页面）之后不再更新、不再跳转。重设密码的结果不会出现在这一用途，忽略。
export async function finishRegister(verified: SmsVerified, source: RoutePath | null, signal: AbortSignal, wait: Wait, moves: RegisterMoves): Promise<void> {
  if (signal.aborted || verified.kind !== "member") {
    return;
  }
  moves.verified(smsVerifiedCopy(verified));
  await redirectLater(source, signal, wait, moves.replace);
}

// 验证通过的提示（视觉稿 V1-verified，同登录页）。
function VerifiedNotice({ copy }: { copy: CopyKey }) {
  const t = useCopy();
  return (
    <div className="acs-alert acs-alert--success" role="status">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M8 12.5l3 3 5-6" />
      </svg>
      <span>{t(copy)}</span>
    </div>
  );
}

export interface RegisterViewProps {
  settings: RegisterSettings;
  // 验证通过后的文字；未通过为 null。
  done: CopyKey | null;
  // auth.continue_guest 的去向（router.tsx 的 guestPath）。
  guestTo: RoutePath;
  onVerified: (verified: SmsVerified) => void;
}

export function RegisterView({ settings, done, guestTo, onVerified }: RegisterViewProps) {
  const t = useCopy();
  return (
    <main className="site-register">
      <section className="acs-card site-register__card">
        <h1 className="acs-display-l">{t("auth.register_title")}</h1>
        <DemoHint>{t("auth.register_demo_hint")}</DemoHint>
        <p className="acs-body-s site-register__text">{t("auth.same_flow_note")}</p>
        <div className="site-register__body" aria-busy={settings.status === "loading"}>
          {settings.status === "failed" && <ErrorNotice />}
          {settings.status === "ready" && <SmsVerify {...registerVerify(settings, guestTo, onVerified)} />}
          {done !== null && <VerifiedNotice copy={done} />}
        </div>
        <Link className="site-register__login" to={LOGIN_PATH}>
          {t("common.nav_login")}
        </Link>
      </section>
    </main>
  );
}

export default function RegisterPage() {
  const { replace, source } = useRouter();
  // 打开页面时读一次来源页（在本页期间不会改变）。
  const [origin] = useState<RoutePath | null>(() => source());
  const [settings, setSettings] = useState<RegisterSettings>(LOADING_SETTINGS);
  const [done, setDone] = useState<CopyKey | null>(null);
  // 站点设置的读取与成功后的计时共用的中止；离开页面时中止。
  const lifetime = useRef<AbortController | null>(null);

  // 打开时读一次站点设置（不随语言重新读取）。
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void loadSettings(controller.signal, (loaded) => {
      setSettings(loaded);
    });
    return () => {
      controller.abort();
    };
  }, []);

  return (
    <RegisterView
      settings={settings}
      done={done}
      guestTo={guestPath(origin)}
      onVerified={(verified) => {
        const controller = lifetime.current;
        if (controller === null) {
          return;
        }
        void finishRegister(verified, origin, controller.signal, timerWait, {
          verified: (copy) => {
            setDone(copy);
          },
          replace: (path) => {
            replace(path);
          },
        });
      }}
    />
  );
}
