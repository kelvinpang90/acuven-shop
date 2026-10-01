import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { DEFAULT_LANGUAGE, isLanguage, translate } from "./copy";
import type { CopyKey, CopyVars, Language } from "./copy";

// 语言规则（UX-COPY「约定」）：默认英文；访客在页头选择后只保存在本浏览器的 localStorage；
// 不按 IP 或浏览器语言自动切换，不写 cookie，不放进网址。

export const LANGUAGE_STORAGE_KEY = "acuven-shop.language";

export type LanguageStorage = Pick<Storage, "getItem" | "setItem">;

// 取 localStorage 本身也可能抛错（隐私模式、禁用存储），取不到就当作没有存储。
export function browserStorage(): LanguageStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

// 没有保存、保存的值不合法或读取失败时一律英文。
export function readStoredLanguage(storage: LanguageStorage | null): Language {
  if (!storage) {
    return DEFAULT_LANGUAGE;
  }
  try {
    const stored = storage.getItem(LANGUAGE_STORAGE_KEY);
    return isLanguage(stored) ? stored : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

// 写入失败时照常切换，只是下次不记得。
export function saveLanguage(storage: LanguageStorage | null, language: Language): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // 存不下就不记住。
  }
}

// html 的 lang 属性。中文写成 zh-Hans：文案是简体，读屏与字体回退按简体选。
const HTML_LANG: Readonly<Record<Language, string>> = { en: "en", zh: "zh-Hans", ms: "ms" };

export function htmlLang(language: Language): string {
  return HTML_LANG[language];
}

export function applyDocumentLanguage(doc: { documentElement: { lang: string } }, language: Language): void {
  doc.documentElement.lang = htmlLang(language);
}

interface LanguageContextValue {
  language: Language;
  setLanguage: (language: Language) => void;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

interface LanguageProviderProps {
  children: ReactNode;
  // 测试传入假存储；不传时用本浏览器的 localStorage。
  storage?: LanguageStorage | null | undefined;
}

export function LanguageProvider({ children, storage }: LanguageProviderProps) {
  const store = storage === undefined ? browserStorage() : storage;
  const [language, setLanguageState] = useState<Language>(() => readStoredLanguage(store));

  const setLanguage = useCallback(
    (next: Language) => {
      setLanguageState(next);
      saveLanguage(store, next);
    },
    [store],
  );

  useEffect(() => {
    applyDocumentLanguage(document, language);
  }, [language]);

  const value = useMemo(() => ({ language, setLanguage }), [language, setLanguage]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
  const value = useContext(LanguageContext);
  if (!value) {
    throw new Error("useLanguage must be used inside LanguageProvider");
  }
  return value;
}

// 页面取文案的唯一入口：按当前语言查字典并替换变量。
export function useCopy(): (key: CopyKey, vars?: CopyVars) => string {
  const { language } = useLanguage();
  return useCallback((key: CopyKey, vars?: CopyVars) => translate(language, key, vars), [language]);
}
