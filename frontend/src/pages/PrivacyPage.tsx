import { DemoHint } from "../components/SiteFrame";
import type { CopyKey } from "../i18n/copy";
import { useCopy } from "../i18n/language";

// 隐私说明 P14（docs/UX.md P14 线框）：标题、★ 提示、引言与四个段落。
// 手机上各段默认展开：段落是普通区块，没有折叠控件。
// 「联系」段只在 WhatsApp 联系链接已配置时显示；配置来源由之后单独登记的任务提供，现在一律不渲染（UX Q10）。
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
      </article>
    </main>
  );
}
