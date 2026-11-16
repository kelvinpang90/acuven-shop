import { useEffect, useState } from "react";

import { readAdminPromotions } from "../api/adminPromotions";
import type { AdminPromotionSummary, PromotionState, PromotionsRead } from "../api/adminPromotions";
import AdminFrame, { AdminAlert } from "../components/AdminFrame";
import type { CopyKey, Language } from "../i18n/copy";
import { htmlLang, useCopy, useLanguage } from "../i18n/language";
import { ADMIN_LOGIN_PATH, ADMIN_PROMOTION_NEW_PATH, adminPromotionPath, Link, useRouter } from "../router";
import type { RoutePath } from "../router";

// 后台促销活动列表 A12（docs/UX.md 0.20 A12「列表」，视觉稿 A12-desktop 与 A12-phone；DESIGN 1.23「节日促销活动」第 1、2 条）：
// 用后台框架渲染，当前导航项为促销活动。内容区为标题行 admin.nav_promotions（h1）与 common.create（链接到新建），其下为
// admin.promo_demo_note 与 admin.rules_apply_new，再下为全部活动：桌面为表格（内部名称、状态、时间、规则条数与 admin.promo_open），
// 手机为卡片（内部名称、状态、时间与 admin.promo_open），两套都渲染，由 site.css 按宽度显隐（照 A07、A11）。
// 行的顺序照接口返回（服务端已按进行中、未开始、草稿、已结束排好），不在页面重排；没有活动时只显示 admin.promo_empty。
// 打开时读取一次（GET /api/admin/promotions）；401 换成登录页 A01，网络中断 common.network_check，其他失败 common.error_retry（照 A07）。
// 离开页面时中止读取，之后的结果不再更新页面。内部名称按纯文本显示。

// 状态标签（视觉稿 A12-desktop）：文案键与类名，编辑区（SHOP-TASK-270）沿用。
export const PROMOTION_STATE_LABEL: Readonly<Record<PromotionState, CopyKey>> = {
  draft: "admin.promo_draft",
  scheduled: "admin.promo_scheduled",
  running: "admin.promo_running",
  ended: "admin.promo_ended",
};

export const PROMOTION_STATE_CLASS: Readonly<Record<PromotionState, string>> = {
  draft: "acs-tag acs-tag--neutral",
  scheduled: "acs-tag acs-tag--outline",
  running: "acs-tag acs-tag--success",
  ended: "acs-tag acs-tag--muted",
};

// 活动时间（马来西亚本地「YYYY-MM-DDTHH:MM」）按界面语言显示：照 formatCouponDate 把年月日时分按 UTC 构造、按 UTC 格式化，
// 只格式化，不随浏览器的时区换算。编辑区沿用。
export function formatPromotionTime(value: string, language: Language): string {
  const [date = "", time = ""] = value.split("T");
  const [year = 0, month = 1, day = 1] = date.split("-").map(Number);
  const [hour = 0, minute = 0] = time.split(":").map(Number);
  return new Intl.DateTimeFormat(htmlLang(language), { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, day, hour, minute)),
  );
}

// 时间列：两端都有时为「开始 – 结束」；只有一端时只显示那一端、没有分隔符；两端都为 null 时为空字符串。
export function promotionTimeText(startsAt: string | null, endsAt: string | null, language: Language): string {
  return [startsAt, endsAt]
    .filter((value): value is string => value !== null)
    .map((value) => formatPromotionTime(value, language))
    .join(" – ");
}

// 列表区的状态：读取进行中（busy，标 aria-busy）、取到的活动（按接口顺序）与失败提示（error）。
export interface ListState {
  busy: boolean;
  promotions: AdminPromotionSummary[] | null;
  error: CopyKey | null;
}

export const INITIAL_LIST: ListState = { busy: true, promotions: null, error: null };

// 按读取结果决定：取到显示列表；401 回到 A01（login）；网络中断显示 common.network_check，其他失败显示 common.error_retry。
export function listStep(read: PromotionsRead): ListState | "login" {
  switch (read.kind) {
    case "ok":
      return { busy: false, promotions: read.promotions, error: null };
    case "none":
      return "login";
    case "network":
      return { busy: false, promotions: null, error: "common.network_check" };
    case "failed":
      return { busy: false, promotions: null, error: "common.error_retry" };
  }
}

// 页面对读取结果的处理：显示，或用路由的 replace 换成 A01（不留后台页的历史记录）。
export interface Moves {
  show: (state: ListState) => void;
  replace: (path: RoutePath) => void;
}

// 读取全部活动；signal 中止（离开页面）之后不再更新。read 默认为请求模块的 readAdminPromotions，测试以替身代替。
export async function loadPromotions(
  signal: AbortSignal,
  moves: Moves,
  read: (signal?: AbortSignal) => Promise<PromotionsRead> = readAdminPromotions,
): Promise<void> {
  const result = await read(signal);
  if (signal.aborted) {
    return;
  }
  const step = listStep(result);
  if (step === "login") {
    moves.replace(ADMIN_LOGIN_PATH);
  } else {
    moves.show(step);
  }
}

function StateTag({ state }: { state: PromotionState }) {
  const t = useCopy();
  return <span className={PROMOTION_STATE_CLASS[state]}>{t(PROMOTION_STATE_LABEL[state])}</span>;
}

function OpenLink({ promotion }: { promotion: AdminPromotionSummary }) {
  const t = useCopy();
  return (
    <Link className="acs-admin__btn acs-admin__btn--secondary" to={adminPromotionPath(promotion.id)}>
      {t("admin.promo_open")}
    </Link>
  );
}

// 桌面表格：列依次为内部名称、状态标签、时间、规则条数与 admin.promo_open（靠右）。
function PromotionTable({ promotions }: { promotions: AdminPromotionSummary[] }) {
  const t = useCopy();
  const { language } = useLanguage();
  return (
    <div className="acs-admin__panel site-admin-promotions__table">
      <table className="acs-admin__table">
        <thead>
          <tr>
            <th>{t("admin.promo_name")}</th>
            <th>{t("admin.filter_status")}</th>
            <th>{t("admin.promo_time")}</th>
            <th>{t("admin.promo_rule_count")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {promotions.map((promotion) => (
            <tr key={promotion.id}>
              <td>{promotion.name}</td>
              <td>
                <StateTag state={promotion.state} />
              </td>
              <td>{promotionTimeText(promotion.starts_at, promotion.ends_at, language)}</td>
              <td>{promotion.rule_count}</td>
              <td className="site-admin-promotions__end">
                <OpenLink promotion={promotion} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 手机卡片（UX A12「列表改为卡片：<内部名称>、<状态>、<开始>–<结束>、( [admin.promo_open] )」）：第一行内部名称与状态标签分列两端，
// 其下为时间（两端都为 null 时不显示）与 admin.promo_open。
function PromotionCards({ promotions }: { promotions: AdminPromotionSummary[] }) {
  const { language } = useLanguage();
  return (
    <ul className="site-admin-promotions__cards">
      {promotions.map((promotion) => {
        const time = promotionTimeText(promotion.starts_at, promotion.ends_at, language);
        return (
          <li key={promotion.id} className="acs-admin__panel site-admin-promotions__card">
            <div className="site-admin-promotions__card-head">
              <span>{promotion.name}</span>
              <StateTag state={promotion.state} />
            </div>
            {time !== "" && <span>{time}</span>}
            <div>
              <OpenLink promotion={promotion} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// 内容区：标题行、两条说明，再下为列表区（读取中标 aria-busy；失败时只有提示；取到后为表格与卡片，没有活动时只有 admin.promo_empty）。
export function AdminPromotionsView({ state }: { state: ListState }) {
  const t = useCopy();
  const { promotions } = state;
  return (
    <div className="site-admin-promotions">
      <div className="site-admin-promotions__title">
        <h1 className="acs-admin__h">{t("admin.nav_promotions")}</h1>
        <Link className="acs-admin__btn" to={ADMIN_PROMOTION_NEW_PATH}>
          {t("common.create")}
        </Link>
      </div>
      <p className="site-admin-promotions__note">
        <span>{t("admin.promo_demo_note")}</span> <span>{t("admin.rules_apply_new")}</span>
      </p>
      <div className="site-admin-promotions__list" aria-busy={state.busy}>
        {state.error !== null && <AdminAlert error={state.error} />}
        {promotions !== null &&
          (promotions.length === 0 ? (
            <p className="acs-admin__panel acs-admin__muted site-admin-promotions__empty">{t("admin.promo_empty")}</p>
          ) : (
            <>
              <PromotionTable promotions={promotions} />
              <PromotionCards promotions={promotions} />
            </>
          ))}
      </div>
    </div>
  );
}

// 框架的内容区：框架确认已登录后才挂载，挂载即读取一次；离开页面时中止。
export function AdminPromotionsContent() {
  const { replace } = useRouter();
  const [state, setState] = useState<ListState>(INITIAL_LIST);

  useEffect(() => {
    const controller = new AbortController();
    void loadPromotions(controller.signal, { show: setState, replace });
    return () => {
      controller.abort();
    };
  }, [replace]);

  return <AdminPromotionsView state={state} />;
}

export default function AdminPromotionsPage() {
  return (
    <AdminFrame current="promotions">
      <AdminPromotionsContent />
    </AdminFrame>
  );
}
