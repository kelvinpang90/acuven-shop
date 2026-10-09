import { useWhatsAppContactUrl } from "../api/siteSettings";
import { DemoHint, WhatsAppButton } from "../components/SiteFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";

// 隐私说明 P14（docs/UX.md P14 线框）：标题、★ 提示、引言、四个段落与「联系」段。
// 手机上各段默认展开：段落是普通区块，没有折叠控件。
// 「联系」段在四段之后，链接取自 GET /api/site-settings 的 whatsapp_contact_url（api/siteSettings.ts，与页脚共用一次读取），
// 按钮在新标签页打开；取得前、未配置或读取失败时整段不渲染（UX Q10：配置缺失时隐藏，不显示占位文字）。
// 本页不提供、也不暗示访客删除收货资料的入口（DESIGN「资料保留」）。
const SECTIONS: readonly { heading: CopyKey; paragraphs: readonly CopyKey[] }[] = [
  { heading: "privacy.h_collect", paragraphs: ["privacy.collect", "privacy.fictional"] },
  {
    heading: "privacy.h_retention",
    paragraphs: ["privacy.retention_recipient", "privacy.member", "privacy.member_backup"],
  },
  { heading: "privacy.h_access", paragraphs: ["privacy.browser_access", "privacy.lookup_risk"] },
  { heading: "privacy.h_sms_logs", paragraphs: ["privacy.sms", "privacy.logs", "privacy.sms_toggle"] },
];

export default function PrivacyPage() {
  const t = useCopy();
  const whatsApp = useWhatsAppContactUrl();
  return (
    <main className="site-privacy">
      <div className="site-privacy__aside">
        <h1 className="acs-display-l">{t("privacy.title")}</h1>
        <DemoHint>{t("privacy.demo_hint")}</DemoHint>
      </div>
      <article className="site-privacy__body">
        <p className="acs-body-l">{t("privacy.intro")}</p>
        {SECTIONS.map((section) => (
          <section key={section.heading} className="site-privacy__section">
            <h2 className="acs-display-s">{t(section.heading)}</h2>
            {section.paragraphs.map((paragraph) => (
              <p key={paragraph}>{t(paragraph)}</p>
            ))}
          </section>
        ))}
        {whatsApp !== null && (
          <section className="site-privacy__section">
            <h2 className="acs-display-s">{t("privacy.h_contact")}</h2>
            <p>{t("privacy.contact")}</p>
            <WhatsAppButton href={whatsApp} className="site-privacy__contact">
              {t("privacy.contact_button")}
            </WhatsAppButton>
          </section>
        )}
      </article>
    </main>
  );
}
