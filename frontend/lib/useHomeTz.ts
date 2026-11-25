"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const browserTz = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};
let cached: string | null = null;

/** The studio's timezone (GET /system/locale: CVM_DEFAULT_TZ, else the machine's); the browser's while loading. */
export function useHomeTz(): string {
  const [tz, setTz] = useState<string>(cached ?? browserTz());
  useEffect(() => {
    if (cached) return;
    api<{ tz: string }>("/system/locale")
      .then((r) => {
        cached = r.tz || browserTz();
        setTz(cached);
      })
      .catch(() => {});
  }, []);
  return tz;
}
