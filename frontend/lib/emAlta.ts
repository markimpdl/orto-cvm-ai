/** Em alta v2 (backend cvm/api/em_alta.py): Lives / Vídeos (YouTube + Twitch + Kick), vidIQ research without a
 *  channel, and Seguindo (followed keywords and channels). Every item can open the start page pre-filled. */
import { api } from "@/lib/api";

export type HotItem = {
  id: string;
  platform?: "youtube" | "twitch" | "kick";
  kind: "live" | "was_live" | "short" | "video";
  url: string;
  title: string;
  channel: string;
  channel_url?: string | null;
  thumb?: string | null;
  views?: number | null;
  viewers?: number | null;
  duration?: number | null;
  game?: string;
  language?: string;
};

export type HotSource = { items: HotItem[]; error: string | null; configured?: boolean; experimental?: boolean };

export type DiscoverResult = {
  topic: string;
  region: string;
  at: string;
  live: HotItem[];
  lives: HotItem[];
  videos: HotItem[];
  youtube_error?: string | null;
  twitch?: HotSource;
  kick?: HotSource;
};

export type DiscoverState = { running: boolean; error: string | null; results: DiscoverResult[]; twitch_configured: boolean };

export const getDiscover = () => api<DiscoverState>("/trends/discover");
export const startDiscover = (topic: string, region: string) =>
  api<{ started: boolean }>("/trends/discover", { method: "POST", body: JSON.stringify({ topic, region }) });

export type GlobalReport = {
  topic: string;
  at: string;
  channel_id?: number | null;
  channel_name?: string | null;
  verdict: string | null;
  verdict_why: string;
  keywords: { keyword: string; volume: number | null; competition: number | null; growth: number | null }[];
  competitor_outliers: { video_id: string; url: string; title: string; channel: string; views: number | null; outlier: number | null }[];
  ideas: { title: string; why: string }[];
  error?: string;
};

export const getGlobalResearch = () => api<{ running: boolean; credits: number; reports: GlobalReport[] }>("/trends/research");
export const startGlobalResearch = (topic: string, language: string, channelId: number | null) =>
  api<{ started: boolean }>("/trends/research", {
    method: "POST",
    body: JSON.stringify({ topic, language, channel_id: channelId, confirm_credits: true }),
  });

export type FollowChannel = { platform: "youtube" | "twitch" | "kick"; handle: string };
export type FollowState = { keywords: string[]; channels: FollowChannel[]; region: string; last_run: string | null; last_error: string | null };

export const getFollow = () => api<FollowState>("/trends/follow");
export const saveFollow = (v: Pick<FollowState, "keywords" | "channels" | "region">) =>
  api<FollowState>("/trends/follow", { method: "PUT", body: JSON.stringify(v) });
export const runFollow = () => api<{ started: boolean }>("/trends/follow/run", { method: "POST" });

/** The start page pre-filled: the user reviews everything there before the video starts. */
export function produceHref(p: { topic?: string; link?: string | null; trend?: number }): string {
  const q = new URLSearchParams();
  if (p.trend) q.set("trend", String(p.trend));
  if (p.topic) q.set("topic", p.topic);
  if (p.link) q.set("link", p.link);
  return `/fabrica/novo?${q.toString()}`;
}
