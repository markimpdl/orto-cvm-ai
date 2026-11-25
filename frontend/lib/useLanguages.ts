"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const FALLBACK = ["pt-BR", "en-US"];
let cached: string[] | null = null;

/** Video languages this install offers (GET /system/languages); pt-BR/en-US while loading or when the API fails. */
export function useLanguages(): string[] {
  const [langs, setLangs] = useState<string[]>(cached ?? FALLBACK);
  useEffect(() => {
    if (cached) return;
    api<{ video: string[] }>("/system/languages")
      .then((r) => {
        cached = r.video?.length ? r.video : FALLBACK;
        setLangs(cached);
      })
      .catch(() => {});
  }, []);
  return langs;
}
