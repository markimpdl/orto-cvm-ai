"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { EN } from "@/lib/i18n-en";

/** UI language (not the videos' content languages pt-BR / ar-AE / en-US). */
export type Lang = "pt" | "en";
export type Vars = Record<string, string | number>;
export type T = (pt: string, vars?: Vars) => string;

const KEY = "cvm.lang";
let current: Lang = "pt";

/** Translates a Portuguese UI string (the dictionary key) and fills {name} placeholders. Unknown keys stay Portuguese. */
export function translate(lang: Lang, pt: string, vars?: Vars): string {
  const s = lang === "en" ? (EN[pt] ?? pt) : pt;
  return vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : s;
}

/**
 * Same as `t`, for plain functions outside components (status lines, confirm() texts).
 * Callers render inside components that use `useT()`, so they re-render when the language changes.
 */
export const tr: T = (pt, vars) => translate(current, pt, vars);

/** Locale for dates and numbers in the current UI language. */
export const dateLocale = (): string => (current === "en" ? "en-US" : "pt-BR");

type Ctx = { lang: Lang; setLang: (l: Lang) => void; t: T; locale: string };
const LangCtx = createContext<Ctx>({ lang: "pt", setLang: () => {}, t: (pt, vars) => translate("pt", pt, vars), locale: "pt-BR" });

function apply(l: Lang) {
  current = l;
  try {
    document.documentElement.lang = l === "en" ? "en" : "pt-BR";
  } catch {
    /* no document */
  }
}

/** PT | EN switch state, remembered per browser in localStorage ("cvm.lang"). */
export function LangProvider({ children }: { children: React.ReactNode }) {
  const [lang, setState] = useState<Lang>("pt");

  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(KEY);
    } catch {
      /* storage blocked */
    }
    if (saved === "en") {
      apply("en");
      setState("en");
    }
  }, []);

  const setLang = useCallback((l: Lang) => {
    apply(l);
    try {
      localStorage.setItem(KEY, l);
    } catch {
      /* storage blocked */
    }
    setState(l);
  }, []);

  const value = useMemo<Ctx>(
    () => ({ lang, setLang, t: (pt, vars) => translate(lang, pt, vars), locale: lang === "en" ? "en-US" : "pt-BR" }),
    [lang, setLang],
  );
  return <LangCtx.Provider value={value}>{children}</LangCtx.Provider>;
}

export function useT(): Ctx {
  return useContext(LangCtx);
}
