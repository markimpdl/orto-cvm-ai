"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { Initial, PageHeader } from "@/components/Bits";
import { MEDIA_MODES, uploadMedia } from "@/components/UserMedia";
import { effectiveVoiceLabel, useVoiceVariants } from "@/components/VoiceChoice";
import { useT } from "@/lib/i18n";
import { LINK_SITE, linkPlatform } from "@/lib/links";
import { useLanguages } from "@/lib/useLanguages";
import { api, LANG_FLAG, LANG_LABEL, PLATFORM_LABEL, type Analysis, type Channel, type Idea, type Project, type Trend, isProducing } from "@/lib/api";

/** cvm/guards.py: a similar video on the same channel in the last 30 days (409 {code: "similar"}). */
type Similar = { message: string; similar: { project_id: number; title: string; channel: string; created_at: string }[] };

function similarOf(e: unknown): Similar | null {
  const m = /^Error: 409 ([\s\S]*)$/.exec(String(e));
  try {
    const d = m ? JSON.parse(m[1]).detail : null;
    return d?.code === "similar" ? d : null;
  } catch {
    return null;
  }
}

/** One field for how the presenter shows up. "Só narração" was dropped (2026-11-24): Editorial already narrates with
 *  the avatar's voice and never shows it, so the two read the same to the user. */
type PresenterPick = "editorial" | "scenes" | "overlay" | "podcast";
const PRESENTER_OPTS: { k: PresenterPick; label: string; hint: string }[] = [
  { k: "editorial", label: "Editorial", hint: "visual de redação com cartões e provas; o avatar não aparece, só a voz dele" },
  { k: "scenes", label: "Só nas cenas do avatar", hint: "aparece na abertura e no fechamento" },
  { k: "overlay", label: "Vídeo inteiro", hint: "recortado embaixo da tela o tempo todo" },
  { k: "podcast", label: "Podcast", hint: "janelinha no estúdio e tela cheia nas falas dele" },
];

// order and names asked by the user (2026-11-24); keys are the backend styles
const STYLE_OPTS: [string, string, string][] = [
  ["react", "React", "comenta trechos curtos de um vídeo em alta"],
  ["explainer", "Explicativo", "explica uma notícia ou tema do momento, com provas na tela"],
  ["dark", "Mistério", "histórias reais e fatos escondidos, narrados sobre imagens"],
  ["top_list", "Top lista", "ranking de 5 ou 10 itens sobre o tema"],
  ["kids", "Animação infantil", "episódio de desenho de uns 5 minutos com os personagens da série"],
  ["lesson", "Aula", "ensina a partir dos seus slides em PDF, imagens e vídeos"],
  ["review", "Unbox e review", "avalia um produto com as suas gravações e fotos"],
  ["live", "Melhores momentos da live", "os melhores trechos da sua live, com o som original"],
];
const DEST_OPTS: [string, string][] = [
  ["youtube_shorts", "Shorts"], ["youtube", "YouTube"], ["instagram", "Reels"], ["tiktok", "TikTok"],
];
const MUSIC_OPTS = [
  { k: "none", label: "Sem música", hint: "só a voz e os sons do vídeo" },
  { k: "acestep", label: "Gerar no computador", hint: "trilha própria feita aqui (ACE-Step), sem custo" },
  { k: "elevenlabs", label: "ElevenLabs", hint: "trilha feita para este vídeo, gasta créditos do seu plano" },
] as const;
type MusicPick = (typeof MUSIC_OPTS)[number]["k"];

type Cut = "full" | "highlights" | "ranges";
const CUT_OPTS: { k: Cut; label: string }[] = [
  { k: "full", label: "Inteiro" },
  { k: "highlights", label: "Melhores momentos" },
  { k: "ranges", label: "Trechos que eu escolho" },
];
type Range = { from: string; to: string };
type Material = { id: string; file?: File; url?: string; cut: Cut; ranges: Range[]; keepAudio: boolean };
type LinkJob = { key: string; title: string; project_ids: number[]; status: "held" | "working" | "ok" | "error"; error?: string | null; at: string };
type MusicEstimate = { available: boolean; seconds: number; credits: number; remaining: number | null; reserve: number; enough: boolean; error?: string };

/** The site badge of a pasted link ("Kick · experimental"). */
function linkBadge(url: string, t: (s: string) => string): string {
  const site = linkPlatform(url);
  if ("error" in site) return t("Link");
  return site.platform === "kick" ? `Kick · ${t("experimental")}` : LINK_SITE[site.platform];
}
const OWN_STYLES = ["lesson", "review"]; // channels built from the user's own files: opening switches

/** "1:23", "83" or "1:02:03" → seconds; null when it isn't a time. */
function seconds(v: string): number | null {
  const parts = v.trim().split(":");
  if (!v.trim() || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

function rangesOf(m: Material): [number, number][] | null {
  const out = m.ranges.map((r) => [seconds(r.from), seconds(r.to)] as const);
  if (!out.length || out.some(([a, b]) => a === null || b === null || b - a < 1)) return null;
  return out as [number, number][];
}

let nextId = 0;
const newMaterial = (x: Partial<Material>): Material => ({ id: `m${++nextId}`, cut: "full", ranges: [{ from: "", to: "" }], keepAudio: false, ...x });
const isVideo = (m: Material) => !!m.url || !!m.file?.type.startsWith("video/");

function Block({ title, summary, children, open = true }: { title: string; summary?: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} className="card group p-0">
      <summary className="flex min-h-14 cursor-pointer list-none items-center gap-3 px-5 py-3 [&::-webkit-details-marker]:hidden">
        <span aria-hidden className="text-muted transition-transform group-open:rotate-90">›</span>
        <span className="h2 m-0">{title}</span>
        {summary && <span className="ml-auto truncate text-[13px] text-muted">{summary}</span>}
      </summary>
      <div className="flex flex-col gap-5 border-t border-line-soft px-5 pb-5 pt-4">{children}</div>
    </details>
  );
}

function CreateForm() {
  const params = useSearchParams();
  const router = useRouter();
  const { channels: allChannels, avatars, reload, setToast } = useAppData();
  const channels = allChannels.filter(isProducing); // paused/archived channels get no new videos
  const { t } = useT();
  const videoLangs = useLanguages();
  const trendId = params.get("trend");
  const ideaRef = params.get("idea"); // "<analysisId>:<index>"
  const fromChannel = params.get("channel"); // a channel's niche idea: ?channel=<id>&cidea=<index>
  const channelIdea = params.get("cidea");
  const topicParam = params.get("topic"); // from Em alta: ?topic=… and/or ?link=…
  const linkParam = params.get("link");
  const [nicheRef, setNicheRef] = useState<{ analysis_id: number; index: number } | null>(null);
  const [trend, setTrend] = useState<Trend | null>(null);
  const [idea, setIdea] = useState<Idea | null>(null);
  const [topic, setTopic] = useState(topicParam ?? "");
  const [draft, setDraft] = useState("");
  const [channelId, setChannelId] = useState<number | null>(null);
  const [avatar, setAvatar] = useState<string>("");
  const [langs, setLangs] = useState<string[]>(["pt-BR"]);
  const [style, setStyle] = useState("explainer");
  const [targets, setTargets] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [materials, setMaterials] = useState<Material[]>(() => (linkParam ? [newMaterial({ url: linkParam })] : []));
  const [linkInput, setLinkInput] = useState("");
  const [mediaMode, setMediaMode] = useState("mix");
  const [presenter, setPresenter] = useState<PresenterPick>("scenes");
  const [voice, setVoice] = useState<string>(""); // empty = follow the channel; only explicit overrides are saved
  const [objective, setObjective] = useState(""); // style "lesson": what the viewer can do by the end
  const [hook, setHook] = useState(false); // lesson/review: open with hook + promise (default off)
  const [music, setMusic] = useState<MusicPick>("none");
  const [estimate, setEstimate] = useState<MusicEstimate | null>(null);
  const [bumper, setBumper] = useState(false);
  const [bumperFrom, setBumperFrom] = useState<number | null>(null);
  const [bumperOpts, setBumperOpts] = useState<{ own: boolean; others: { id: number; name: string }[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [also, setAlso] = useState<number[]>([]); // sibling channels of the group that also get this topic
  const [similar, setSimilar] = useState<Similar | null>(null);
  const [linkJobs, setLinkJobs] = useState<LinkJob[]>([]);
  const prefilled = !!(trendId || ideaRef || nicheRef || channelIdea || topicParam || linkParam);

  function pickChannel(c: Channel | undefined, styleOverride?: string) {
    setChannelId(c?.id ?? null);
    setAlso([]);
    setSimilar(null);
    const prefs = (c?.prefs ?? {}) as NonNullable<Channel["prefs"]> & { music_source?: MusicPick };
    setAvatar(prefs.avatar_id ? String(prefs.avatar_id) : c?.avatar_id ? String(c.avatar_id) : "");
    setPresenter(prefs.no_avatar ? "editorial" : c?.presenter ?? "scenes");
    setLangs(c ? [c.language] : ["pt-BR"]); // one channel = one language
    setTargets((prefs.platforms ?? []).filter((k) => DEST_OPTS.some(([x]) => x === k))); // "Cortes" was dropped (2026-11-24)
    setMusic(prefs.music ? prefs.music_source ?? "acestep" : "none");
    setVoice("");
    if (c) setStyle(styleOverride ?? c.default_style);
    if (c && OWN_STYLES.includes(c.default_style)) { // lesson/review channels: the user's files first
      setMediaMode("mine");
      setHook(prefs.hook === true);
      setBumper(!!prefs.bumper_mode && prefs.bumper_mode !== "none");
      setBumperFrom(prefs.bumper_from ?? null);
      api<{ own: boolean; others: { id: number; name: string }[] }>(`/channels/${c.id}/bumpers`).then(setBumperOpts).catch(() => setBumperOpts(null));
    }
  }

  useEffect(() => {
    if (!trendId) return;
    api<Trend>(`/trends/${trendId}`).then((t) => {
      setTrend(t);
      setTopic((v) => v || t.title);
    }).catch(() => {});
  }, [trendId]);
  useEffect(() => {
    if (!ideaRef) return;
    const [aid, idx] = ideaRef.split(":").map(Number);
    api<Analysis[]>("/trends/analyses?limit=10").then((list) => {
      const it = list.find((a) => a.id === aid)?.items[idx];
      if (it) {
        setIdea(it);
        setTopic(it.topic);
      }
    }).catch(() => {});
  }, [ideaRef]);
  useEffect(() => {
    if (!fromChannel || channelIdea === null) return;
    api<{ id: number; items: { index: number; topic: string; style?: string }[] } | null>(`/channels/${fromChannel}/ideas`).then((a) => {
      const it = a?.items.find((i) => i.index === Number(channelIdea));
      if (!a || !it) return;
      setTopic(it.topic);
      setNicheRef({ analysis_id: a.id, index: it.index });
      setIdea({ topic: it.topic, style: it.style, suggested: { channel_id: Number(fromChannel) } } as unknown as Idea);
    }).catch(() => {});
  }, [fromChannel, channelIdea]);
  // defaults: the idea's suggested channel, else ?channel=, else the trend's region channel, else the first channel
  useEffect(() => {
    if (!channels.length || channelId !== null) return;
    if (fromChannel && channelIdea !== null && !idea) return; // wait for the channel idea, then preselect its channel
    const suggested = idea?.suggested.channel_id ?? (fromChannel ? Number(fromChannel) : null) ?? trend?.suggested?.channel_id;
    pickChannel(channels.find((c) => c.id === suggested) ?? channels[0], idea?.style);
  }, [channels, idea, trend]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<LinkJob[]>("/projects/links").then(setLinkJobs).catch(() => {});
  }, []);
  useEffect(() => {
    if (music !== "elevenlabs" || estimate) return;
    api<MusicEstimate>("/projects/music/estimate").then(setEstimate)
      .catch(() => setEstimate({ available: true, seconds: 0, credits: 0, remaining: null, reserve: 0, enough: false }));
  }, [music, estimate]);

  const channel = channels.find((c) => c.id === channelId);
  // "Fazer este tema para…": the other active channels of the same group (same persona, other languages)
  const siblings = channel?.group ? channels.filter((c) => c.group === channel.group && c.id !== channel.id) : [];
  const chosenAvatar = avatars.find((a) => a.id === (avatar ? Number(avatar) : channel?.avatar_id));
  const canPodcast = !!chosenAvatar?.podcast_video_dir;
  const voices = useVoiceVariants(chosenAvatar?.id);
  // Mirror backend project_variant(): a channel policy applies only while this project uses that channel's voice avatar.
  const channelVoiceCompatible = !!chosenAvatar && [channel?.avatar_id, channel?.prefs?.avatar_id].some((id) => id === chosenAvatar.id);
  const channelVoice = channelVoiceCompatible ? channel?.prefs?.voice_variant ?? null : null;
  const channelVoiceLabel = effectiveVoiceLabel(voices, channelVoice);
  const needsExplicitVoice = voices.length > 1 && !channelVoice && !voice;
  const videos = materials.filter(isVideo);
  const files = materials.filter((m) => m.file);
  const links = materials.filter((m) => m.url);
  const badRanges = videos.some((m) => m.cut === "ranges" && !rangesOf(m));
  const soundOn = [...OWN_STYLES, "live"].includes(style); // the user's own recordings talk: their sound plays by default
  const title = topic.trim() || draft.trim().split("\n")[0].slice(0, 90).trim();

  useEffect(() => {
    if (voice && !voices.some((v) => v.key === voice)) setVoice("");
  }, [voice, voices]);
  useEffect(() => {
    if (presenter === "podcast" && !canPodcast && avatars.length) setPresenter("scenes");
  }, [canPodcast, presenter, avatars.length]);

  function update(id: string, patch: Partial<Material>) {
    setMaterials((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }
  function addLink() {
    const url = linkInput.trim();
    const site = linkPlatform(url);
    if ("error" in site) {
      setErr(t(site.error));
      return;
    }
    setErr(null);
    setMaterials((ms) => [...ms, newMaterial({ url, keepAudio: soundOn })]);
    setLinkInput("");
  }

  async function finish(forceSimilar = false) {
    if (needsExplicitVoice) {
      setErr(t("Escolha uma voz para este avatar. O padrão do canal não se aplica a ele."));
      return;
    }
    if (badRanges) {
      setErr(t("Confira os trechos: cada um precisa de início e fim (ex.: 1:20 até 2:05), com o fim depois do início."));
      return;
    }
    setBusy(true);
    setErr(null);
    setSimilar(null);
    const goal = style === "lesson" && objective.trim()
      ? ` — ${(channel?.language ?? langs[0]) === "pt-BR" ? "Objetivo da aula" : "Lesson objective"}: ${objective.trim()}` : "";
    const channelIds = channelId !== null && also.length ? [channelId, ...also.filter((id) => siblings.some((c) => c.id === id))] : null;
    const highlights = videos.some((m) => m.cut === "highlights");
    try {
      const p = await api<Project & { projects: Project[]; warnings: string[]; held: boolean; link_job: LinkJob | null }>("/projects", {
        method: "POST",
        body: JSON.stringify({
          title,
          topic: (trend && title !== trend.title ? `${title} (tendência: ${trend.title})` : nicheRef && channel ? `${title} (nicho do canal ${channel.name})` : title) + goal,
          draft: draft.trim() || null,
          channel_idea: nicheRef,
          save_prefs: true, // these choices become the channel's defaults
          trend_id: trend?.id ?? null,
          style,
          languages: langs,
          platforms: targets,
          channel_id: channelId,
          ...(channelIds ? { channel_ids: channelIds } : {}),
          ...(forceSimilar ? { force_similar: true } : {}),
          avatar_id: avatar ? Number(avatar) : null,
          presenter,
          media_mode: mediaMode,
          media_cut: highlights ? "highlights" : "full",
          ...(voice ? { voice_variant: voice } : {}),
          ...(OWN_STYLES.includes(style) ? { hook, bumper, bumper_from: bumper ? bumperFrom : undefined } : {}),
          music_source: music,
          links: links.map((m) => ({ url: m.url, cut: m.cut, keep_audio: m.keepAudio, ...(m.cut === "ranges" ? { ranges: rangesOf(m) } : {}) })),
          hold: materials.length > 0, // files and links go in before the job starts (POST /projects/start)
        }),
      });
      const projects = p.projects?.length ? p.projects : [p];
      if (p.held) {
        for (const proj of projects) {
          for (const m of files) {
            const up = (await uploadMedia(`/projects/${proj.id}/media`, m.file!)) as { id: number; kind?: string; duration?: number; has_audio?: boolean };
            if (up.kind !== "video") continue;
            const ranges = m.cut === "ranges" ? rangesOf(m) : m.cut === "full" && highlights && up.duration ? [[0, up.duration]] : null;
            await api(`/projects/${proj.id}/media/${up.id}`, {
              method: "PATCH",
              body: JSON.stringify({ keep_audio: m.keepAudio && up.has_audio !== false, ...(ranges ? { segments: ranges } : {}) }),
            });
          }
        }
        await api("/projects/start", { method: "POST", body: JSON.stringify({ project_ids: projects.map((x) => x.id) }) });
      }
      await reload();
      const n = projects.length;
      const msg = links.length
        ? t("“{title}” está baixando o link. Entra na fila assim que o download terminar.", { title: p.title })
        : n > 1 ? t("“{title}” entrou na fila em {n} canais", { title: p.title, n }) : t("“{title}” entrou na fila", { title: p.title });
      setToast({ msg: [msg, ...(p.warnings ?? [])].join(" · ") });
      router.push("/fabrica");
    } catch (e) {
      const sim = similarOf(e);
      if (sim) setSimilar(sim);
      else setErr(String(e));
      setBusy(false);
    }
  }

  const on = "border-2 border-go bg-go-tint";
  const off = "border border-[#d9dbdf] bg-surface";
  const card = (sel: boolean) => `pick flex flex-col items-start gap-0.5 rounded-xl px-3.5 py-2.5 text-left ${sel ? on : off}`;
  const chip = (sel: boolean) => `pick min-h-11 rounded-full px-4 text-[15px] font-semibold text-ink ${sel ? on : off}`;
  const presenterLabel = PRESENTER_OPTS.find((o) => o.k === presenter)?.label ?? "";
  const pendingLinks = linkJobs.filter((j) => j.status === "working" || j.status === "error").slice(0, 4);

  return (
    <>
      <PageHeader title={prefilled ? t("Revisar antes de produzir") : t("Novo vídeo")}
        subtitle={prefilled ? t("Já vem preenchido. Confira, ajuste o que quiser e coloque na fila.") : t("Uma tela só. Ao confirmar, o vídeo entra na fila.")} />
      <section className="flex max-w-[820px] flex-col gap-4">
        <Block title={t("O quê")} summary={title}>
          <label className="flex flex-col gap-2 text-[15px] font-semibold">
            {t("Tema")}
            <input className="input min-h-[52px] px-4 font-normal" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder={t("Ex.: Como funciona a energia solar em casa")} />
            {trend && <span className="text-[13px] font-normal text-muted">{t("Tendência de origem:")} {trend.title}</span>}
          </label>
          <label className="flex flex-col gap-2 text-[15px] font-semibold">
            {t("Rascunho do roteiro")} <span className="-mt-1.5 text-[13px] font-normal text-muted">{t("Opcional. Cole suas ideias ou um roteiro pronto: o roteirista parte dele, mantém a sua ordem e só ajusta o formato.")}</span>
            <textarea className="input min-h-[120px] px-4 py-3 font-normal" value={draft} onChange={(e) => setDraft(e.target.value)}
              placeholder={t("Ex.: Abrir com a pergunta “quanto custa pôr painéis solares em casa?”. Depois instalação, conta de luz e manutenção, com valores.")} />
          </label>
          {style === "lesson" && (
            <label className="flex flex-col gap-2 text-[15px] font-semibold">
              {t("Objetivo da aula")}
              <textarea className="input min-h-[84px] px-4 py-3 font-normal" value={objective} onChange={(e) => setObjective(e.target.value)}
                placeholder={t("Ex.: ao final, o aluno sabe montar um orçamento simples no Excel")} />
            </label>
          )}
        </Block>

        <Block title={t("Onde")} summary={channel ? `${channel.name} · ${t(LANG_LABEL[channel.language] ?? channel.language)}` : t("Sem canal")}>
          <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
            <legend className="mb-2.5 text-[15px] font-semibold">{t("Canal")}</legend>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-2.5">
              {channels.map((c) => (
                <button key={c.id} type="button" aria-pressed={channelId === c.id} onClick={() => pickChannel(c)} className={`pick flex items-center gap-3 rounded-[14px] p-3.5 text-ink hover:border-ink ${channelId === c.id ? on : off}`}>
                  <Initial name={c.name} />
                  <span className="flex flex-col gap-0.5 text-left">
                    <span className="text-[15px] font-semibold">{c.name}</span>
                    <span className="text-[13px] text-muted">{PLATFORM_LABEL[c.platform] ?? c.platform} · {t(LANG_LABEL[c.language] ?? c.language)}</span>
                  </span>
                </button>
              ))}
              <button type="button" aria-pressed={channelId === null} onClick={() => pickChannel(undefined)} className={`pick flex items-center gap-3 rounded-[14px] p-3.5 text-ink hover:border-ink ${channelId === null ? on : off}`}>
                <span className="text-[15px] font-semibold">{t("Sem canal")}</span>
              </button>
            </div>
          </fieldset>
          {channel && siblings.length > 0 && (
            <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
              <legend className="mb-2 text-[15px] font-semibold">{t("Fazer este tema para…")}</legend>
              <label className="flex min-h-11 items-center gap-2.5 text-[15px]">
                <input type="checkbox" className="h-4 w-4 accent-go" checked disabled />
                <span dir="auto">{LANG_FLAG[channel.language] ?? ""} {channel.name}</span>
                <span className="text-[13px] text-muted">{t("(este canal)")}</span>
              </label>
              {siblings.map((c) => (
                <label key={c.id} className="flex min-h-11 items-center gap-2.5 text-[15px]">
                  <input type="checkbox" className="h-4 w-4 accent-go" checked={also.includes(c.id)}
                    onChange={(e) => setAlso(e.target.checked ? [...also, c.id] : also.filter((x) => x !== c.id))} />
                  <span dir="auto">{LANG_FLAG[c.language] ?? ""} {c.name}</span>
                  <span className="text-[13px] text-muted">{t(LANG_LABEL[c.language] ?? c.language)}</span>
                </label>
              ))}
              <span className="text-[13px] text-muted">{t("Cada canal ganha um projeto próprio, no idioma dele, com gancho, título, exemplos e moeda do seu público: adaptação, nunca tradução. Os outros canais usam os próprios padrões (avatar, formatos).")}</span>
            </fieldset>
          )}
          {channel ? (
            <p className="m-0 text-[13px] text-muted">{t("Idioma: {lang}. Cada canal tem um idioma só; para outro idioma, use o canal daquele idioma.", { lang: t(LANG_LABEL[channel.language] ?? channel.language) })}</p>
          ) : (
            <fieldset className="m-0 border-0 p-0">
              <legend className="mb-2.5 text-[15px] font-semibold">{t("Idiomas")}</legend>
              <div className="flex flex-wrap gap-2.5">
                {videoLangs.map((l) => {
                  const sel = langs.includes(l);
                  return (
                    <button key={l} type="button" aria-pressed={sel} onClick={() => setLangs(sel ? langs.filter((x) => x !== l) : [...langs, l])} className={chip(sel)}>
                      {sel ? "✓" : "+"} {t(LANG_LABEL[l])}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          )}
        </Block>

        <Block title={t("Quem")} summary={[t(presenterLabel), chosenAvatar?.name].filter(Boolean).join(" · ")}>
          <fieldset className="m-0 border-0 p-0">
            <legend className="mb-2.5 text-[15px] font-semibold">{t("Formato do apresentador")}</legend>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-2" role="radiogroup" aria-label={t("Formato do apresentador")}>
              {PRESENTER_OPTS.map((o) => {
                const disabled = o.k === "podcast" && !canPodcast;
                return (
                  <button key={o.k} type="button" role="radio" aria-checked={presenter === o.k} disabled={disabled} onClick={() => setPresenter(o.k)} className={card(presenter === o.k)}>
                    <span className="text-sm font-semibold">{t(o.label)}</span>
                    <span className="text-xs text-muted">{disabled ? t("este avatar ainda não tem o estúdio de podcast") : t(o.hint)}</span>
                  </button>
                );
              })}
            </div>
          </fieldset>
          <label className="flex flex-col gap-2 text-[15px] font-semibold">
            {t("Avatar")}
            <select className="input min-h-[52px] font-normal" value={avatar} onChange={(e) => { setAvatar(e.target.value); setVoice(""); }}>
              {!channel?.avatar_id && <option value="">{t("Automático (seu avatar pronto, se houver só um)")}</option>}
              {avatars.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}{a.id === channel?.avatar_id ? ` — ${t("padrão do canal")}` : ""}{a.status === "ready" ? "" : ` ${t("(gravação incompleta)")}`}
                </option>
              ))}
            </select>
            {presenter === "editorial" && <span className="text-[13px] font-normal text-muted">{t("O avatar fica guardado como identidade da voz, mas nunca aparece no vídeo. Em Shorts verticais, entra também o cabeçalho editorial.")}</span>}
          </label>
          {voices.length > 1 && (
            <fieldset className="m-0 border-0 p-0">
              <legend className="mb-2 text-[15px] font-semibold">{t("Voz de {name}", { name: chosenAvatar?.name ?? "" })}</legend>
              <div className="flex flex-wrap gap-2.5" role="radiogroup">
                {channelVoice && <button type="button" role="radio" aria-checked={!voice} onClick={() => setVoice("")} className={chip(!voice)}>
                  {channelVoiceLabel ? t("Padrão do canal ({voice})", { voice: t(channelVoiceLabel) }) : t("Padrão do canal")}
                </button>}
                {voices.map((v) => (
                  <button key={v.key} type="button" role="radio" aria-checked={voice === v.key} onClick={() => setVoice(v.key)} className={chip(voice === v.key)}>
                    {t(v.label)}
                  </button>
                ))}
              </div>
              <p className="mb-0 mt-2 text-[13px] text-muted">{channelVoice
                ? t("Já vem com a voz configurada no canal. Escolha outra somente para substituir neste vídeo.")
                : t("A voz padrão do canal só vale para o avatar configurado nele.")}</p>
              {needsExplicitVoice && <p role="alert" className="mb-0 mt-2 text-[13px] text-alert-text">{t("Escolha uma voz para este avatar. O padrão do canal não se aplica a ele.")}</p>}
            </fieldset>
          )}
        </Block>

        <Block title={t("Material")} summary={materials.length ? (materials.length === 1 ? t("1 item") : t("{n} itens", { n: materials.length })) : t(MEDIA_MODES.find((o) => o.k === mediaMode)?.label ?? "")}>
          <fieldset className="m-0 border-0 p-0">
            <legend className="mb-2.5 text-[15px] font-semibold">{t("De onde vêm as imagens")}</legend>
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("De onde vêm as imagens")}>
              {MEDIA_MODES.map((o) => (
                <button key={o.k} type="button" role="radio" aria-checked={mediaMode === o.k} onClick={() => setMediaMode(o.k)} className={`${card(mediaMode === o.k)} flex-[1_1_200px]`}>
                  <span className="text-sm font-semibold">{t(o.k === "mine" ? "Só minhas mídias" : o.k === "online" ? "Internet e IA" : "Mistura")}</span>
                  <span className="text-xs text-muted">{t(o.hint)}</span>
                </button>
              ))}
            </div>
          </fieldset>

          <div className="flex flex-col gap-2">
            <span className="text-[15px] font-semibold">{style === "lesson" ? t("Slides (PDF), imagens e vídeos da aula") : t("Arquivos e links")}</span>
            <div className="flex flex-wrap items-center gap-2.5">
              <label className="btn cursor-pointer">
                + {t("Escolher arquivos")}
                <input type="file" multiple accept="video/*,image/*,application/pdf,.pdf" className="hidden"
                  onChange={(e) => { const fs = Array.from(e.target.files ?? []); setMaterials((ms) => [...ms, ...fs.map((file) => newMaterial({ file, keepAudio: soundOn }))]); e.target.value = ""; }} />
              </label>
              <form className="flex min-w-[260px] flex-1 gap-2" onSubmit={(e) => { e.preventDefault(); addLink(); }}>
                <input className="input min-w-0 flex-1 font-normal" value={linkInput} onChange={(e) => setLinkInput(e.target.value)} placeholder={t("Link do vídeo (YouTube, Twitch, Kick, TikTok, Instagram)")} aria-label={t("Link do vídeo")} />
                <button type="submit" className="btn" disabled={!linkInput.trim()}>{t("Adicionar")}</button>
              </form>
            </div>
            <span className="text-[13px] text-muted">{t("Vídeo, imagem ou PDF (cada página vira um slide). Links são baixados antes do roteiro começar. Instagram e TikTok: só posts públicos. Kick ainda é experimental.")}</span>
          </div>

          {materials.length > 0 && (
            <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
              {materials.map((m) => (
                <li key={m.id} className="flex flex-col gap-3 rounded-xl border border-line bg-paper px-4 py-3">
                  <div className="flex items-center gap-3">
                    <span className="pill shrink-0">{m.url ? linkBadge(m.url, t) : m.file?.type.startsWith("video/") ? t("Vídeo") : m.file?.type === "application/pdf" || m.file?.name.toLowerCase().endsWith(".pdf") ? "PDF" : t("Imagem")}</span>
                    <span className="min-w-0 flex-1 truncate text-[15px]" dir="auto">{m.url ?? m.file?.name}</span>
                    <button type="button" className="btn btn-sm btn-danger" onClick={() => setMaterials((ms) => ms.filter((x) => x.id !== m.id))}>{t("Remover")}</button>
                  </div>
                  {isVideo(m) && (
                    <>
                      <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label={t("Quanto do vídeo usar")}>
                        {CUT_OPTS.map((o) => (
                          <button key={o.k} type="button" role="radio" aria-checked={m.cut === o.k} onClick={() => update(m.id, { cut: o.k })}
                            className={`pick min-h-10 rounded-full px-3.5 text-sm font-semibold text-ink ${m.cut === o.k ? on : off}`}>{t(o.label)}</button>
                        ))}
                        <label className="ml-auto flex min-h-10 items-center gap-2 text-sm">
                          <input type="checkbox" className="h-4 w-4 accent-go" checked={m.keepAudio} onChange={(e) => update(m.id, { keepAudio: e.target.checked })} />
                          {t("Manter o som original")}
                        </label>
                      </div>
                      {m.cut === "ranges" && (
                        <div className="flex flex-col gap-2">
                          {m.ranges.map((r, i) => (
                            <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
                              <span className="w-8 text-muted">{t("de")}</span>
                              <input className="input w-28 font-normal" inputMode="numeric" placeholder="0:30" aria-label={t("Início do trecho {n}", { n: i + 1 })} value={r.from}
                                onChange={(e) => update(m.id, { ranges: m.ranges.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)) })} />
                              <span className="text-muted">{t("até")}</span>
                              <input className="input w-28 font-normal" inputMode="numeric" placeholder="1:45" aria-label={t("Fim do trecho {n}", { n: i + 1 })} value={r.to}
                                onChange={(e) => update(m.id, { ranges: m.ranges.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)) })} />
                              {m.ranges.length > 1 && <button type="button" className="btn btn-sm" onClick={() => update(m.id, { ranges: m.ranges.filter((_, j) => j !== i) })}>{t("Tirar")}</button>}
                            </div>
                          ))}
                          <button type="button" className="btn btn-sm self-start" onClick={() => update(m.id, { ranges: [...m.ranges, { from: "", to: "" }] })}>+ {t("Outro trecho")}</button>
                          <span className="text-[13px] text-muted">{t("Minutos:segundos, como 1:20. Só esses trechos entram, na ordem.")}</span>
                        </div>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Block>

        <Block title={t("Estilo")} summary={t(STYLE_OPTS.find(([k]) => k === style)?.[1] ?? style)}>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-2" role="radiogroup" aria-label={t("Estilo")}>
            {STYLE_OPTS.map(([k, label, hint]) => (
              <button key={k} type="button" role="radio" aria-checked={style === k} onClick={() => setStyle(k)} className={card(style === k)}>
                <span className="text-sm font-semibold">{t(label)}</span>
                <span className="text-xs text-muted">{t(hint)}</span>
              </button>
            ))}
          </div>
          {OWN_STYLES.includes(style) && (
            <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
              <legend className="mb-1 text-[15px] font-semibold">{t("Abertura")}</legend>
              <label className="flex min-h-11 items-center gap-2.5 text-[15px]">
                <input type="checkbox" className="h-5 w-5 accent-go" checked={hook} onChange={(e) => setHook(e.target.checked)} />
                {t("Gancho no início")}
                <span className="text-[13px] text-muted">{t("desligado: começa direto no conteúdo")}</span>
              </label>
              <label className="flex min-h-11 items-center gap-2.5 text-[15px]">
                <input type="checkbox" className="h-5 w-5 accent-go" checked={bumper} onChange={(e) => setBumper(e.target.checked)} />
                {t("Vinheta")}
              </label>
              {bumper && (
                <label className="flex flex-col gap-1.5 pl-8 text-sm font-semibold">
                  {t("Qual vinheta")}
                  <select className="input font-normal" value={bumperFrom ?? ""} onChange={(e) => setBumperFrom(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">{bumperOpts?.own ? t("A deste canal") : t("A deste canal (ainda sem logo: peça para cadastrar)")}</option>
                    {(bumperOpts?.others ?? []).map((o) => <option key={o.id} value={o.id}>{t("A do canal {name}", { name: o.name })}</option>)}
                  </select>
                </label>
              )}
            </fieldset>
          )}
        </Block>

        <Block title={t("Destino")} summary={targets.length ? targets.map((k) => t(DEST_OPTS.find(([x]) => x === k)?.[1] ?? k)).join(", ") : t("formato do canal")}>
          <div className="flex flex-wrap gap-2.5">
            {DEST_OPTS.map(([k, label]) => {
              const sel = targets.includes(k);
              return (
                <button key={k} type="button" aria-pressed={sel} onClick={() => setTargets(sel ? targets.filter((x) => x !== k) : [...targets, k])} className={chip(sel)}>
                  {sel ? "✓" : "+"} {t(label)}
                </button>
              );
            })}
          </div>
          <span className="text-[13px] text-muted">{t("Nada marcado: usa os formatos de sempre do canal.")}</span>
        </Block>

        <Block title={t("Música")} summary={t(MUSIC_OPTS.find((o) => o.k === music)?.label ?? "")}>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("Música de fundo")}>
            {MUSIC_OPTS.map((o) => (
              <button key={o.k} type="button" role="radio" aria-checked={music === o.k} onClick={() => setMusic(o.k)} className={`${card(music === o.k)} flex-[1_1_200px]`}>
                <span className="text-sm font-semibold">{t(o.label)}</span>
                <span className="text-xs text-muted">{t(o.hint)}</span>
              </button>
            ))}
          </div>
          {music === "elevenlabs" && (
            <p className={`m-0 rounded-xl bg-paper px-4 py-3 text-sm ${estimate && (!estimate.available || !estimate.enough) ? "text-warn-text" : "text-body"}`} role="status">
              {!estimate ? t("Consultando seus créditos…")
                : !estimate.available ? t("A chave da ElevenLabs não está configurada. O vídeo usa a trilha gerada no computador.")
                : estimate.remaining === null ? t("Não consegui ler seus créditos agora. Se faltar na hora, o vídeo usa a trilha gerada no computador.")
                : estimate.enough
                  ? t("Custa cerca de {credits} créditos (trilha de {s} s). Você tem {left}; ficam pelo menos {reserve} guardados.", { credits: estimate.credits.toLocaleString(), s: estimate.seconds, left: estimate.remaining.toLocaleString(), reserve: estimate.reserve.toLocaleString() })
                  : t("Créditos insuficientes: custa cerca de {credits} e você tem {left} (com {reserve} guardados). O vídeo usa a trilha gerada no computador.", { credits: estimate.credits.toLocaleString(), left: estimate.remaining.toLocaleString(), reserve: estimate.reserve.toLocaleString() })}
            </p>
          )}
          {channelId && <span className="text-[13px] text-muted">{t("A escolha vira o padrão deste canal.")}</span>}
        </Block>

        {pendingLinks.length > 0 && (
          <div className="card flex flex-col gap-2 px-5 py-4">
            <p className="m-0 text-[15px] font-semibold">{t("Links baixando")}</p>
            {pendingLinks.map((j) => (
              <p key={j.key} className="m-0 flex flex-wrap items-center gap-2 text-sm">
                <span className={`pill ${j.status === "error" ? "bg-alert-soft text-alert-text" : ""}`}>{j.status === "error" ? t("erro") : t("baixando")}</span>
                <span dir="auto" className="font-semibold">{j.title}</span>
                {j.error && <span className="text-muted">{j.error}</span>}
              </p>
            ))}
          </div>
        )}

        {similar && (
          <div role="alert" className="card flex flex-col gap-2 border-2 border-alert-text px-4 py-3.5">
            <p className="m-0 text-[15px] font-semibold">{t("Já existe um vídeo parecido neste canal nos últimos 30 dias")}</p>
            <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
              {similar.similar.map((x) => (
                <li key={x.project_id}>
                  <a href={`/projetos/${x.project_id}`} className="font-semibold underline" dir="auto">{x.title}</a>
                  <span className="text-muted"> · {x.channel} · {new Date(x.created_at).toLocaleDateString()}</span>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2.5">
              <button className="btn" onClick={() => setSimilar(null)}>{t("Mudar o tema")}</button>
              <button className="btn btn-go" onClick={() => finish(true)} disabled={busy || needsExplicitVoice}>{t("Fazer mesmo assim")}</button>
            </div>
          </div>
        )}
        {err && <p role="alert" className="m-0 text-sm text-alert-text">{err}</p>}
        <div className="flex flex-wrap justify-end gap-3 pt-2">
          <button className="btn" onClick={() => router.back()}>{t("Cancelar")}</button>
          <button className="btn btn-go min-h-[52px] px-6 text-base" onClick={() => finish()} disabled={!langs.length || !title || busy || needsExplicitVoice}>
            {busy ? t("Enviando…") : t("Colocar na fila")}
          </button>
        </div>
      </section>
    </>
  );
}

export default function CreatePage() {
  return (
    <Suspense>
      <CreateForm />
    </Suspense>
  );
}
