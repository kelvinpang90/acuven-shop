import { createContext, useContext } from "react";
import type { ReactNode } from "react";

// 前台预览的上下文（UX 0.20 A12「前台预览」，DESIGN 1.23「节日促销活动」第 11 条，HANDOFF 0.98）：标明当前在预览哪个活动，
// 前台页面据此改经 api/adminPromotionPreview.ts 的后台接口取数（凭管理员会话），公开接口不加任何预览参数。
// Provider 之外为 null：前台平常的页面都在 Provider 之外，行为不变。

export interface StorefrontPreview {
  // 正在预览的活动 ID。
  readonly promotionId: number;
  // 预览计价（POST …/preview/quote）请求头所带的 CSRF 令牌。
  readonly csrfToken: string;
}

const PreviewContext = createContext<StorefrontPreview | null>(null);

interface PreviewProviderProps {
  preview: StorefrontPreview;
  children: ReactNode;
}

export function PreviewProvider({ preview, children }: PreviewProviderProps) {
  return <PreviewContext.Provider value={preview}>{children}</PreviewContext.Provider>;
}

// 正在预览的活动与 CSRF 令牌；不在预览中（Provider 之外）为 null。
export function usePreview(): StorefrontPreview | null {
  return useContext(PreviewContext);
}
