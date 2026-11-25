"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export type Features = {
  ai_ideas: boolean; regions: string[]; lives: string[]; vidiq: boolean; follow: boolean; avatars: boolean; elevenlabs: boolean;
};

/** Everything on: used until the answer arrives and whenever the API is down (a failed request never hides a menu). */
export const ALL_ON: Features = {
  ai_ideas: true, regions: [], lives: ["youtube", "twitch", "kick"], vidiq: true, follow: true, avatars: true, elevenlabs: true,
};

export function useFeatures(): Features {
  const [f, setF] = useState<Features>(ALL_ON);
  useEffect(() => {
    let live = true;
    api<Features>("/setup/features").then((r) => { if (live) setF({ ...ALL_ON, ...r }); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return f;
}
