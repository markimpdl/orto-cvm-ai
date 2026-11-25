import { dateLocale, tr } from "@/lib/i18n";

// NEXT_PUBLIC_CVM_API_PORT: a second copy on the same Mac (INSTALAR.command with CVM_API_PORT) talks to its own API.
const API_PORT = process.env.NEXT_PUBLIC_CVM_API_PORT || "8790";
export const API = process.env.NEXT_PUBLIC_CVM_API ?? `http://${typeof window !== "undefined" ? window.location.hostname : "127.0.0.1"}:${API_PORT}`;

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

/** Asks for confirmation, then deletes the project (and its files). Returns true when deleted. */
export async function deleteProject(p: Pick<Project, "id" | "title" | "job">): Promise<boolean> {
  const published = p.job?.state === "published" ? "\n" + tr("O vídeo já publicado continua no ar; só o registro e os arquivos locais somem.") : "";
  if (!confirm(tr('Excluir "{title}"? Roteiros, renders e arquivos serão apagados.', { title: p.title }) + published)) return false;
  try {
    await api(`/projects/${p.id}`, { method: "DELETE" });
  } catch (e) {
    if (!String(e).startsWith("Error: 409")) throw e;
    if (!confirm(tr('"{title}" está em processamento agora. Excluir mesmo assim?', { title: p.title }))) return false;
    await api(`/projects/${p.id}?force=true`, { method: "DELETE" });
  }
  return true;
}

/** Defaults the backend suggests for producing a trend/idea (channel by region or by Claude's pick). */
export type Suggested = { channel_id: number | null; avatar_id: number | null; languages: string[]; style: string };

export type Trend = {
  id: number;
  source: string;
  region: string;
  title: string;
  url: string | null;
  score: number;
  score_breakdown: Record<string, number>;
  last_seen: string;
  meta: { related?: string[]; news_title?: string; hours_trending?: number; [k: string]: unknown };
  suggested?: Suggested;
  project_id?: number | null;
};

export type Idea = {
  index: number;
  trend_ids: number[];
  trend_id: number | null;
  trend_title: string | null;
  region: string | null;
  topic: string;
  why: string;
  style: string;
  hooks: Record<string, string>;
  risks?: string;
  channel_id?: number;
  potential?: number;
  suggested: Suggested;
  project_id: number | null;
};
export type Analysis = { id: number; region: string | null; status: string; created_at: string; summary: string | null; items: Idea[]; error: string | null };

export type Job = {
  id: number;
  project_id: number;
  state: string;
  error: string | null;
  updated_at: string;
  history: { from: string; to: string; at: string; note: string | null; worked_s?: number;
    curation_summary?: { ok: number; fixed: number; warnings: number } }[]; // left by the validation step (cvm/curation.py)
  progress?: { started?: string; at?: string; fraction?: number; label?: string | null; waiting?: "gpu" | "prep" | "claude" | "claude_limit" | "episode"; since?: string; until?: string; after_episode?: number };
  paused?: boolean; // stops at the next step boundary until the user resumes it
  orphaned?: boolean; // automatic step lost its worker (restart/crash) and needs resuming
  waiting_for?: "you" | "gpu" | "prep" | "claude" | null;
  queue_position?: number | null;
  eta_s?: number | null;
};

export type Project = {
  seo_title?: string | null; // the title it goes out with, in the channel's language (GET /projects)
  id: number;
  series_id?: number | null;
  title: string;
  topic: string;
  style: string;
  languages: string[];
  platforms?: string[];
  channel_id: number | null;
  avatar_id: number | null;
  trend_id: number | null;
  autopilot?: boolean;
  media_mode?: "mix" | "mine" | "online";
  media_cut?: "full" | "highlights";
  voice_variant?: string | null;
  voice_effective?: string | null; // the voice narration really uses (the channel default when none was picked)
  presenter?: "scenes" | "overlay" | "podcast" | "editorial" | null;
  created_at: string;
  job?: Job | null;
};

export type Scene = {
  index: number;
  narration: string;
  on_screen_text?: string | null;
  visual: { type: string; search_terms?: string[]; image_prompt?: string | null; source_url?: string | null; notes?: string | null };
  target_seconds?: number | null;
};

export type ScriptContent = { hook: string; scenes: Scene[]; call_to_action?: string | null; target_duration_s: number; sources: string[]; hook_variants?: string[]; pronunciation?: Record<string, string>; writer?: "claude" | "codex" };
export type Script = { id: number; language: string; version: number; content: ScriptContent; created_at: string };

export type Channel = {
  id: number;
  name: string;
  platform: string;
  language: string;
  default_style: string;
  avatar_id: number | null;
  publish_mode: string;
  brief: string | null;
  schedule?: { slots?: string[] };
  presenter?: "scenes" | "overlay" | "podcast" | "editorial";
  prefs?: { languages?: string[]; platforms?: string[]; avatar_id?: number | null; no_avatar?: boolean; voice_variant?: string; last_episode?: number; episode_max?: Record<string, number>; hook?: boolean; bumper_mode?: string; bumper_from?: number | null; music?: boolean; image_engine?: string };
  made_for_kids?: boolean;
  characters?: Record<string, { description?: string; voice?: string; ref_text?: string; language?: string; pending?: boolean; error?: string;
    look?: string; sheet?: string; sheet_seed?: number; sheet_note?: string; sheet_pending?: boolean; sheet_error?: string }>;
  allow_official_clips?: boolean;
  allow_article_images?: boolean;
  status?: "active" | "paused" | "archived" | "series" | null;
  youtube_handle?: string | null;
  youtube_channel_id?: string | null;
  group?: string | null;
  profile?: ChannelProfile;
};
export type ChannelFormat = { kind: string; min_s: number; max_s: number; per_week: number };
export type PublishSlot = { format: string; days: string[]; time: string; tz: string };
export type ChannelProfile = {
  series?: boolean; publish_group?: string; series_names?: Record<string, string>;
  priority?: number; niche?: string; audience?: string; persona?: string; tone?: string;
  voice?: { engine?: string; profile?: string; speed?: number; energy?: string };
  presenter?: "avatar" | "real_voice" | "narration";
  content_pillars?: string[]; banned_topics?: string[]; rules?: string[]; title_patterns?: string[];
  formats?: ChannelFormat[]; publish_slots?: PublishSlot[];
  description_footer?: string; default_tags?: string[]; youtube_category?: number;
  cta?: { spoken?: string; written?: string }; cross_links?: string[];
};
/** Channels that take new videos (paused/archived ones keep their history but get no new production). */
export const isProducing = (c: Channel) => (c.status ?? "active") === "active";
export const LANG_FLAG: Record<string, string> = { "pt-BR": "🇧🇷", "en-US": "🇺🇸", "ar-AE": "🇦🇪" };
export type Avatar = { id: number; name: string; status: string; notes: string | null; podcast_video_dir?: string | null };

export const STYLES = ["dark", "react", "explainer", "top_list", "kids", "lesson", "review", "live"];
/** UI labels below are Portuguese source strings: render them through t(...) from useT(). */
export const STYLE_LABEL: Record<string, string> = { dark: "Mistério", react: "React", explainer: "Explicativo", top_list: "Top lista", kids: "Animação infantil", lesson: "Videoaula", review: "Review / unboxing", live: "Melhores momentos da live" };
export const LANGS = ["pt-BR", "ar-AE", "en-US"];
export const LANG_LABEL: Record<string, string> = { "pt-BR": "Português", "ar-AE": "Árabe", "en-US": "Inglês" };
export const REGION_LABEL: Record<string, string> = { AE: "Emirados", BR: "Brasil", US: "Global", GLOBAL: "Global" };
export const PLATFORMS = ["youtube", "youtube_shorts", "instagram", "tiktok"];
export const PLATFORM_LABEL: Record<string, string> = { youtube: "YouTube", youtube_shorts: "YouTube Shorts", instagram: "Instagram", tiktok: "TikTok" };
export const RENDER_TARGETS: Record<string, string> = {
  youtube_shorts: "Shorts 9:16",
  youtube: "YouTube 16:9",
  instagram: "Reels",
  tiktok: "TikTok",
  youtube_shorts_cuts: "Cortes (vários Shorts)",
};

export const STATE_LABEL: Record<string, string> = {
  trend_selected: "Na fila",
  scripting: "Escrevendo roteiro",
  awaiting_approval: "Aguardando aprovação do roteiro",
  voicing: "Gerando narração",
  sourcing_visuals: "Buscando visuais",
  lipsyncing: "Lipsync do avatar",
  composing: "Montando vídeo",
  rendering: "Renderizando e checando",
  validating: "A IA está conferindo o vídeo",
  seo: "Gerando títulos e tags",
  awaiting_publish_review: "Aguardando revisão final",
  scheduled: "Agendado",
  ready_for_manual_publish: "Pronto para publicar",
  published: "Publicado",
  failed: "Falhou",
};

/** "Limpar projetos antigos": frees disk by deleting only the big video files of videos published long ago. */
export type VideoRemoved = { at: string; bytes: number };
export type CleanupFile = { path: string; bytes: number; kind: "render" | "avatar" | "visual" | "other" };
export type CleanupProject = {
  project_id: number;
  title: string;
  channel: string | null;
  published_at: string;
  bytes: number;
  files: CleanupFile[];
  kept_renders: { language: string; profile: string; reason: string }[];
};
export type CleanupPreview = { days: number; total_bytes: number; projects: CleanupProject[] };
export type CleanupResult = { removed_bytes: number; projects: { project_id: number; removed_bytes: number; files: number }[] };

export const cleanupPreview = (days: number) => api<CleanupPreview>(`/projects/cleanup/preview?days=${days}`);
export const runCleanup = (project_ids: number[], days: number) =>
  api<CleanupResult>("/projects/cleanup", { method: "POST", body: JSON.stringify({ project_ids, days }) });

/** 1.2 GB / 340 MB / 12 KB. */
export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toLocaleString(dateLocale(), { maximumFractionDigits: n >= 1e10 ? 0 : 1 })} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.max(0, Math.round(n / 1e3))} KB`;
}

/** Publishing calendar per channel (backend cvm/schedule.py): week goal, calendar and next free slots. */
/** `home` = the same instant in the studio's timezone `home_tz` (CVM_DEFAULT_TZ, else the machine's). */
export type SlotTime = { kind: string | null; utc: string; local: string; tz: string; home: string; home_tz: string };
export type GoalItem = { kind: string; target: number; scheduled: number; published: number; remaining: number };
export type CalPub = { id: number; project_id: number | null; title: string | null; status: string; url: string | null; kind: string; at: string };
export type CalDay = { date: string; weekday: string; slots: (SlotTime & { publication: CalPub | null })[]; extra: CalPub[] };
export type ChannelWeek = { tz: string; week_start: string; goal: GoalItem[]; calendar: CalDay[]; next_slots: Record<string, SlotTime[]> };
export type ChannelGoals = { id: number; name: string; language: string; flag: string; status: string; youtube_handle: string | null; goal: GoalItem[] };
/** GET /channels/{id}/stats (YouTube Analytics); 404 or {connected:false} until the channel is connected. */
export type ChannelStats = {
  connected?: boolean;
  views_7d?: number; views_28d?: number; watch_hours_365d?: number; subscribers?: number;
  top_views?: { title: string; url: string; views: number } | null;
  top_retention?: { title: string; url: string; avg_view_pct: number } | null;
  monetization?: { hours_target: number; subs_target: number; deadline: string };
  suggestions?: { title: string; why: string; topic?: string; project_id?: number | null }[];
};
export const channelWeek = (id: number) => api<ChannelWeek>(`/channels/${id}/week`);
export const channelGoals = () => api<ChannelGoals[]>("/channels/goals");
export const channelSlots = (id: number, q: { kind?: string; profile?: string; n?: number }) =>
  api<SlotTime[]>(`/channels/${id}/slots?${new URLSearchParams(Object.entries(q).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]))}`);
/** Format kinds: [singular, plural] Portuguese labels (render through t). */
export const FORMAT_LABEL: Record<string, [string, string]> = {
  long: ["longo", "longos"], short: ["Short", "Shorts"], episode: ["episódio", "episódios"],
  compilation: ["compilação", "compilações"], live_cut: ["corte de live", "cortes de live"],
};
export const AUDIENCE_TZ = ["America/Sao_Paulo", "America/New_York", "Asia/Dubai"];
export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
export const WEEKDAY_LABEL: Record<string, string> = { mon: "seg", tue: "ter", wed: "qua", thu: "qui", fri: "sex", sat: "sáb", sun: "dom" };
/** "14:00" in a timezone, from a UTC ISO time. */
export const timeIn = (utc: string, tz: string, locale: string) =>
  new Date(utc).toLocaleTimeString(locale, { timeZone: tz, hour: "2-digit", minute: "2-digit" });
export const dayIn = (utc: string, tz: string, locale: string) =>
  new Date(utc).toLocaleDateString(locale, { timeZone: tz, weekday: "short", day: "2-digit", month: "2-digit" });
/** Full GET /channels/{id}/stats contract (backend cvm/api/stats.py, Phase 8). */
export type ChannelStatsFull = ChannelStats & {
  monetization?: { hours_target: number; subs_target: number; deadline: string; hours: number | null; subs: number | null };
  suggestions?: { title: string; why: string; topic?: string; project_id?: number | null }[];
  title_pattern_suggestion?: { pattern: string; status: "pending" | "approved" | "rejected" } | null;
  updated_at?: string | null;
  error?: string | null;
};
/** Starts the Google sign-in for YouTube Analytics: open the returned URL in a new tab. */
export const connectChannelStats = (id: number) => api<{ auth_url: string }>(`/channels/${id}/stats/connect`, { method: "POST" });
export const decideTitlePattern = (id: number, decision: "approve" | "reject") =>
  api<{ pattern: string; status: string }>(`/channels/${id}/title-patterns/${decision}`, { method: "POST" });

/** Menu "Crescer" (backend cvm/api/grow.py): lives → melhores momentos + Shorts, and the monetization numbers. */
export type LivePackage = { long: number | null; shorts: number[]; moments: { start: number; end: number }[]; method?: "viral" | "volume" };
export type LiveItem = { video_id: string; title: string; url: string | null; own?: boolean; uploader?: string | null; status: "working" | "ok" | "error" | "waiting"; error: string | null; package?: LivePackage; at: string };
export type LivesStatus = { items: LiveItem[] };
export type Monetization = {
  hours_target: number; subs_target: number; deadline: string; hours: number | null; subs: number | null;
  connected: boolean; error: string | null; source?: "analytics" | "vidiq" | null; updated_at?: string | null;
  shorts?: { views_90d: number | null; target: number; views_per_day: number | null; needed_per_day: number };
  pace: { hours_per_day: number | null; subs_per_day: number | null; days_to_hours: number | null; days_to_subs: number | null };
  vidiq_sync?: { running: boolean; error: string | null; credits: number; available: boolean };
};
export const getLives = (channelId: number) => api<LivesStatus>(`/grow/${channelId}/lives`);
export const sendLiveLink = (channelId: number, url: string, title: string) =>
  api<{ started: boolean; video_id: string }>(`/grow/${channelId}/lives/link`, { method: "POST", body: JSON.stringify({ url, title: title || null }) });
export const getMonetization = (channelId: number) => api<Monetization>(`/grow/${channelId}/monetization`);
export async function uploadLive(channelId: number, file: File, title: string): Promise<{ started: boolean; video_id: string }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", title);
  const res = await fetch(`${API}/grow/${channelId}/lives/upload`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

/** Viral video suggestions per channel (backend cvm/grow/viral_search.py + daily.py). */
export type ViralItem = { video_id: string; url: string; title: string; channel: string; views: number | null; vph: number | null; outlier: number | null; duration: number | null; published: string | null; why: string; cut_idea: string };
export type ViralPattern = { format: string; example: string; views: number | null; why: string };
export type ViralSearch = { topic: string; language: string; source: "vidiq" | "yt-dlp"; at: string; daily?: boolean; items: ViralItem[]; patterns: ViralPattern[] };
export type ViralState = { topics: string[]; language: string; daily_credits: number; auto_cuts: number; running: boolean; searches: ViralSearch[] };
export const getViral = (channelId: number) => api<ViralState>(`/grow/${channelId}/viral`);
export const startViral = (channelId: number, topic: string, language: string) =>
  api<{ started: boolean }>(`/grow/${channelId}/viral`, { method: "POST", body: JSON.stringify({ topic, language }) });
export const saveViralSettings = (channelId: number, body: { topics: string[]; language: string; daily_credits: number; auto_cuts: number }) =>
  api<ViralState>(`/grow/${channelId}/viral/settings`, { method: "PUT", body: JSON.stringify(body) });

/** Pesquisa tab (backend cvm/grow/research.py). */
export type ResearchReport = {
  topic: string; language: string; at: string; verdict: "subindo" | "estável" | "saturado" | null; verdict_why: string; error?: string;
  keywords: { keyword: string; volume: number | null; competition: number | null; growth: number | null }[];
  competitor_outliers: { video_id: string; url: string; title: string; channel: string; views: number | null; outlier: number | null }[];
  ideas: { title: string; why: string }[];
};
export const getResearch = (channelId: number) => api<{ running: boolean; reports: ResearchReport[] }>(`/grow/${channelId}/research`);
export const startResearch = (channelId: number, topic: string, language: string) =>
  api<{ started: boolean }>(`/grow/${channelId}/research`, { method: "POST", body: JSON.stringify({ topic, language }) });

// ---------- install wizard (/instalar, backend cvm/api/setup.py) ----------

export type SetupStep = "system" | "language" | "ai" | "features" | "models" | "engines" | "channel" | "done";
/** Setting "setup". `legacy` = a machine that already had channels before the wizard existed (never redirected). */
export type SetupState = { done: boolean; legacy?: boolean; step?: SetupStep; choices?: Record<string, unknown> };
export type Hardware = { platform: "mac" | "windows" | "other"; chip: string; ram_gb: number; vram_gb: number; disk_free_gb: number };
/** A feature this machine cannot run (or "disk" when the selection does not fit), with the reason in pt-BR. */
export type Blocker = { feature: string; reason: string };
/** `disk.need_gb`: GB still to download for the chosen features, the number the disk blocker uses (null = unknown). */
export type SetupInfo = { state: SetupState; hardware: Hardware; blockers: Blocker[]; features: Record<string, string>; disk?: { need_gb: number | null; free_gb: number; reserve_gb: number } };
export type InstallItem = { kind: "tool" | "model"; id: string; size_gb: number; state: "ready" | "missing" | "running" | "failed"; error: string | null };
export type InstallPlan = { items: InstallItem[]; missing_gb: number };
export type InstallStatus = { running: boolean; features: string[]; items: InstallItem[]; at: string | null };
/** Hardware tiers (GET /setup/engines): per feature, the engines of this machine best first and the last test. */
export type EngineTier = { engine: string; label: string; fits: boolean; available: boolean; reason: string | null };
export type EngineResult = { engine: string; label: string; ok: boolean; seconds: number; error: string | null; at: string };
export type EngineFeature = {
  label: string; selected: boolean; engine: string | null; all_tested: boolean; tiers: EngineTier[];
  test: { chosen: string | null; running: boolean; results: EngineResult[] } | null;
};
export type EnginesInfo = { platform: string; running: boolean; at: string | null; features: Record<string, EngineFeature> };
export type AiCheck = { installed: boolean; logged_in: boolean; reason?: string };
export type AiStatus = { claude: AiCheck; codex: AiCheck };
export type SetupFeatures = {
  ai_ideas: boolean; regions: string[]; lives: string[]; vidiq: boolean; follow: boolean; avatars: boolean; elevenlabs: boolean;
  keys?: Record<"pexels" | "pixabay" | "youtube" | "twitch_id" | "twitch_secret" | "elevenlabs", boolean>;
};
export type BumperPreview = { state: "idle" | "running" | "ready" | "failed"; prompt?: string | null; params?: Record<string, unknown> | null; error?: string | null; url?: string };

export const getSetup = () => api<SetupInfo>("/setup");
export const saveSetup = (step: SetupStep, choices: Record<string, unknown> = {}) =>
  api<SetupState>("/setup", { method: "PUT", body: JSON.stringify({ step, choices }) });

/** "Atualizar" (backend cvm/setup/update.py): only installs cloned from the public repo get more than public_install. */
export type UpdateStep = "starting" | "checking" | "pausing" | "waiting" | "downloading" | "installing" | "restarting" | "resuming" | "rollback" | "done" | "failed";
export type UpdateRun = { state: "idle" | "running" | "done" | "failed"; step?: UpdateStep; label?: string; from?: string; to?: string; error?: string | null; warning?: string | null; started_at?: string };
export type UpdateInfo = {
  public_install: boolean; supported?: boolean; current?: string | null; latest?: string | null; available?: boolean;
  skipped?: string | null;
  error?: string | null; run?: UpdateRun; running?: boolean;
};
export const getUpdate = () => api<UpdateInfo>("/setup/update");
export const startUpdate = () => api<{ started: boolean }>("/setup/update", { method: "POST" });

/** Multipart upload of the channel's logo and/or banner (no JSON content type, the browser sets the boundary). */
export async function uploadBrand(channelId: number, files: { logo?: File | null; banner?: File | null }) {
  const form = new FormData();
  if (files.logo) form.append("logo", files.logo);
  if (files.banner) form.append("banner", files.banner);
  const res = await fetch(`${API}/setup/channel/${channelId}/brand`, { method: "POST", body: form });
  if (!res.ok) {
    let msg = await res.text();
    try { msg = JSON.parse(msg).detail ?? msg; } catch { /* plain text */ }
    throw new Error(String(msg));
  }
  return res.json() as Promise<Record<string, { width: number; height: number }>>;
}

/** The `detail` of a FastAPI error thrown by api() ("422 {\"detail\": …}"), else the raw message. */
export function apiDetail(e: unknown): string {
  const s = String(e instanceof Error ? e.message : e);
  const m = s.match(/^\d{3} ([\s\S]*)$/);
  if (!m) return s;
  try {
    const d = JSON.parse(m[1]).detail;
    return typeof d === "string" ? d : JSON.stringify(d);
  } catch {
    return m[1];
  }
}
