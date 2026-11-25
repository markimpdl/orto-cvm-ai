"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getCutReviews, type Avatar, type Channel, type CutReviewSummary, type Project } from "@/lib/api";
import { useDurations, type Durations } from "@/lib/pipeline";

type Toast = { msg: string; link?: { href: string; label: string } } | null;

type AppData = {
  projects: Project[];
  /** Publications ready to post now (same rule as the Publish page's "ready" list), for the sidebar badge. */
  publishDue: number;
  /** Cut editor reviews waiting for the owner (status "review"): shown in Sua vez and its sidebar count. */
  cutReviews: CutReviewSummary[];
  channels: Channel[];
  /** GET /channels has answered at least once (an empty list then means no channels yet). */
  channelsLoaded: boolean;
  avatars: Avatar[];
  durations: Durations;
  loaded: boolean;
  reload: () => Promise<void>;
  reloadSetup: () => void;
  toast: Toast;
  setToast: (t: Toast) => void;
  channelOf: (id: number | null | undefined) => Channel | undefined;
  avatarOf: (id: number | null | undefined) => Avatar | undefined;
};

const Ctx = createContext<AppData | null>(null);

/** Projects (polled every 5 s), channels, avatars and the global toast, shared by every screen. */
export function AppDataProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [avatars, setAvatars] = useState<Avatar[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [channelsLoaded, setChannelsLoaded] = useState(false);
  const [publishDue, setPublishDue] = useState(0);
  const [cutReviews, setCutReviews] = useState<CutReviewSummary[]>([]);
  const [toast, setToast] = useState<Toast>(null);
  const durations = useDurations();

  const reload = useCallback(async () => {
    try {
      setProjects(await api<Project[]>("/projects"));
      setLoaded(true);
    } catch {
      /* API offline: keep last data */
    }
  }, []);
  const reloadSetup = useCallback(() => {
    api<Channel[]>("/channels").then((c) => { setChannels(c); setChannelsLoaded(true); }).catch(() => {});
    api<Avatar[]>("/avatars").then(setAvatars).catch(() => {});
  }, []);

  const reloadDue = useCallback(() => {
    const end = new Date();
    end.setHours(23, 59, 59, 999); // the page counts posts scheduled up to the end of the viewer's day
    api<{ count: number; total?: number }>(`/publications/due?until=${encodeURIComponent(end.toISOString())}`)
      .then((r) => setPublishDue(r.total ?? r.count)) // everything waiting on /publicar, not only today's
      .catch(() => {});
    getCutReviews("review").then((r) => setCutReviews(r.items)).catch(() => {});
  }, []);

  useEffect(() => {
    reload();
    reloadSetup();
    reloadDue();
    const t = setInterval(reload, 5000);
    const d = setInterval(reloadDue, 15000);
    return () => { clearInterval(t); clearInterval(d); };
  }, [reload, reloadSetup, reloadDue]);

  const value = useMemo<AppData>(
    () => ({
      projects, publishDue, cutReviews, channels, channelsLoaded, avatars, durations, loaded, reload, reloadSetup, toast, setToast,
      channelOf: (id) => channels.find((c) => c.id === id),
      avatarOf: (id) => avatars.find((a) => a.id === id),
    }),
    [projects, publishDue, cutReviews, channels, channelsLoaded, avatars, durations, loaded, reload, reloadSetup, toast],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppData(): AppData {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppData outside AppDataProvider");
  return v;
}
