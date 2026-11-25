"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Named voice overrides for an avatar. Their order is display-only; the channel decides the default voice. */
export type VoiceVariant = { key: string; label: string; languages: string[] };

const POLICY_LABEL: Record<string, string> = {
  mix: "Voz real + ElevenLabs em palavras estrangeiras",
  real: "Voz real (treinada)",
  eleven: "ElevenLabs",
};

export function effectiveVoiceLabel(voices: VoiceVariant[], key: string | null | undefined): string | null {
  if (!key) return null;
  return voices.find((v) => v.key === key)?.label ?? POLICY_LABEL[key] ?? key;
}

let cache: Promise<Record<string, VoiceVariant[]>> | null = null;

export function useVoiceVariants(avatarId: number | null | undefined): VoiceVariant[] {
  const [all, setAll] = useState<Record<string, VoiceVariant[]>>({});
  useEffect(() => {
    cache ??= api<Record<string, VoiceVariant[]>>("/avatars/meta/voices").catch(() => ({}));
    cache.then(setAll);
  }, []);
  return avatarId ? all[String(avatarId)] ?? [] : [];
}
