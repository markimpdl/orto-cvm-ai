"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { VideoRemovedNote } from "@/components/Bits";
import CurationPanel, { latestCuration, type Curation } from "@/components/CurationPanel";
import { slotText } from "@/components/ChannelWeek";
import { API, api, channelSlots, RENDER_TARGETS, type SlotTime, type VideoRemoved } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Seo = { title?: string; description?: string; tags?: string[]; hashtags?: string[] };
type VidiqReview = { title_score?: number | null; titles?: { title: string; score: number | null }[]; thumb_score?: number | null;
  thumb_feedback?: string | null; tags?: string[]; description_keywords?: string[]; error?: string; at: string };
export type RenderRow = {
  id: number;
  language: string;
  profile: string;
  status: string;
  seo: Seo;
  meta: { duration?: number; qa?: string[]; qa_warnings?: string[]; contact_sheet?: string; credits?: string[]; video_removed?: VideoRemoved;
    vidiq?: VidiqReview; vidiq_running_since?: string | null; curation?: Curation };
  publications: { id: number; status: string; scheduled_at: string; url: string | null }[];
};

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** Next publishing times per render: the channel's publish_slots for that render's format (a long render gets the
 *  long/episode slots, a Short the Shorts ones), else the old rule — from GET /channels/{id}/slots (cvm/schedule.py). */
function useNextSlots(renders: RenderRow[], channelFor: (r: RenderRow) => string): Record<string, SlotTime[]> {
  const [byKey, setByKey] = useState<Record<string, SlotTime[]>>({});
  const keys = Array.from(new Set(renders.map((r) => `${channelFor(r)}|${r.profile}`).filter((k) => !k.startsWith("|")))).sort().join(",");
  useEffect(() => {
    if (!keys) return;
    let live = true;
    Promise.all(keys.split(",").map((k) => {
      const [ch, profile] = k.split("|");
      return channelSlots(Number(ch), { profile, n: 3 }).then((s) => [k, s] as const).catch(() => [k, []] as const);
    })).then((rows) => live && setByKey(Object.fromEntries(rows)));
    return () => {
      live = false;
    };
  }, [keys]);
  return byKey;
}

const localInput = (d: Date) => {
  const x = new Date(d.getTime() - d.getTimezoneOffset() * 60_000);
  return x.toISOString().slice(0, 16);
};

/** Final review: watch, check QA, edit title/description, pick when and where, approve and schedule. */
export default function ReviewPanel({ projectId, channelId, onScheduled, onSwapImages, scheduleLabel }: {
  projectId: number;
  channelId: number | null;
  onScheduled: (whenLabel: string) => void;
  onSwapImages: () => void;
  scheduleLabel: string;
}) {
  const { channels, channelOf } = useAppData();
  const { t, locale } = useT();
  const [renders, setRenders] = useState<RenderRow[]>([]);
  const [cur, setCur] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [when, setWhen] = useState(0);
  const [custom, setCustom] = useState(localInput(new Date(Date.now() + 3600_000)));
  const [plan, setPlan] = useState<Record<number, { on: boolean; channel: string }>>({});
  const [err, setErr] = useState<string | null>(null);
  const channel = channelOf(channelId);
  const channelFor = (x: RenderRow) => plan[x.id]?.channel || String(channelId ?? "");
  const slotsBy = useNextSlots(renders, channelFor);
  const slotsOf = (x: RenderRow | undefined) => (x ? slotsBy[`${channelFor(x)}|${x.profile}`] ?? [] : []);

  const load = () => api<RenderRow[]>(`/projects/${projectId}/renders`).then((rs) => {
    setRenders(rs);
    setCur((c) => c ?? (rs.find((r) => r.profile !== "youtube") ?? rs[0])?.id ?? null);
  }).catch(() => {});
  useEffect(() => {
    load();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setPlan((p) => {
      const next = { ...p };
      const matches = renders.filter((r) => channel && (r.profile === channel.platform || r.profile.startsWith(channel.platform)));
      for (const r of renders) {
        // no project channel: the only channel in the video's language, else none (the user must pick — never the first in the list)
        const sameLang = channels.filter((c) => c.language === r.language && c.status !== "archived");
        next[r.id] ??= { on: matches.length ? matches.includes(r) : true, channel: String(channelId ?? (sameLang.length === 1 ? sameLang[0].id : "")) };
      }
      return next;
    });
  }, [renders, channel, channelId, channels]);

  const r = renders.find((x) => x.id === cur);
  const slots = slotsOf(r ?? renders[0]);
  useEffect(() => {
    setTitle(r?.seo.title ?? "");
    setDescription(r?.seo.description ?? "");
    setHashtags((r?.seo.hashtags ?? []).join(" "));
    setSaved(null);
  }, [r?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!renders.length) return <p className="text-muted">{t("Carregando vídeos…")}</p>;
  const problems = renders.flatMap((x) => (x.meta.qa ?? []).map((q) => `${x.language}/${x.profile}: ${q}`));
  const warnings = r?.meta.qa_warnings ?? [];
  const vertical = r && r.profile !== "youtube";
  const curation = latestCuration(renders);

  async function saveSeo() {
    if (!r) return;
    await api(`/renders/${r.id}/seo`, { method: "PATCH", body: JSON.stringify({ title, description, hashtags: hashtags.split(/\s+/).filter(Boolean) }) });
    setSaved(t("Salvo."));
    load();
  }

  async function schedule() {
    setErr(null);
    const at = when < slots.length ? new Date(slots[when].utc) : new Date(custom);
    // each render goes to the n-th free slot of its own format (a Short at the Shorts time, the long at the long time)
    const atOf = (x: RenderRow) => (when < slots.length ? new Date((slotsOf(x)[when] ?? slots[when]).utc) : at);
    const items = renders
      .filter((x) => plan[x.id]?.on && plan[x.id]?.channel)
      .map((x) => ({ render_id: x.id, channel_id: Number(plan[x.id].channel), scheduled_at: atOf(x).toISOString() }));
    if (!items.length) {
      setErr(t("Escolha pelo menos um vídeo e um canal em “Onde publicar”."));
      return;
    }
    try {
      if (r && (title !== (r.seo.title ?? "") || description !== (r.seo.description ?? ""))) await saveSeo();
      await api(`/projects/${projectId}/schedule`, { method: "POST", body: JSON.stringify(items) });
      onScheduled(when < slots.length ? slotText(slots[when], locale, t) : at.toLocaleString(locale, { dateStyle: "short", timeStyle: "short" }));
    } catch (e) {
      setErr(String(e));
    }
  }

  const on = "border-2 border-go bg-go-tint";
  const off = "border border-[#d9dbdf] bg-surface";

  return (
    <section className="flex flex-wrap gap-6">
      <div className="flex flex-none flex-col gap-2" style={{ width: vertical ? 190 : 360, maxWidth: "100%" }}>
        {r?.meta.video_removed ? (
          <VideoRemovedNote removed={r.meta.video_removed} url={r.publications.find((p) => p.url)?.url} />
        ) : r && (
          <video key={r.id} src={`${API}/renders/${r.id}/file`} controls preload="metadata" className={`w-full rounded-[20px] bg-ink ${vertical ? "aspect-[9/16]" : "aspect-video"}`} />
        )}
        {renders.length > 1 && (
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label={t("Versões do vídeo")}>
            {renders.map((x) => (
              <button key={x.id} role="tab" aria-selected={x.id === cur} onClick={() => setCur(x.id)} className={`pick min-h-9 rounded-full px-3 text-[13px] font-semibold ${x.id === cur ? "bg-ink text-white" : "bg-chip text-body"}`}>
                {t(RENDER_TARGETS[x.profile] ?? x.profile)} · {x.language}{x.meta.duration ? ` · ${mmss(x.meta.duration)}` : ""}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-[1_1_340px] flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-ink px-5 py-4 text-white">
          <span className="h-2.5 w-2.5 rounded-full bg-alert-dot" />
          <span className="flex-[1_1_240px] text-base"><strong>{t("Sua vez:")}</strong> {t("assista e aprove. Título e horário já vêm sugeridos.")}</span>
        </div>
        {curation ? <CurationPanel report={curation} /> : <>
          {problems.length ? (
            <div className="text-sm font-semibold text-alert-text">{t("Checagem automática encontrou problemas:")} {problems.join("; ")}</div>
          ) : (
            <div className="text-sm font-semibold text-go-text">✓ {t("Checagem automática ok: áudio, imagem e volume")}</div>
          )}
          {!!warnings.length && <div className="text-sm text-warn-text">{t("Atenção:")} {warnings.join("; ")}</div>}
        </>}
        <label className="flex flex-col gap-2 text-sm font-semibold">
          {t("Título")}
          <input className="input min-h-12 font-normal" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="flex flex-col gap-2 text-sm font-semibold">
          {t("Descrição")}
          <textarea rows={3} className="rounded-xl border border-field px-3.5 py-3 text-[15px] leading-normal font-normal" value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="flex flex-col gap-2 text-sm font-semibold">
          Hashtags
          <input className="input font-normal" value={hashtags} onChange={(e) => setHashtags(e.target.value)} placeholder={t("#exemplo #tema")} />
        </label>
        <div className="flex items-center gap-3">
          <button className="btn btn-sm" onClick={saveSeo}>{t("Salvar textos")}</button>
          {saved && <span role="status" className="text-sm text-go-text">{saved}</span>}
        </div>
        {r && <VidiqBox r={r} onUse={(x) => setTitle(x)} reload={load} />}
        <fieldset className="m-0 border-0 p-0">
          <legend className="mb-2 text-sm font-semibold">{t("Quando publicar?")}</legend>
          <div className="flex flex-wrap gap-2.5">
            {[...slots.map((x) => slotText(x, locale, t)), t("Escolher data")].map((label, i) => (
              <button key={label} type="button" aria-pressed={when === i} onClick={() => setWhen(i)} className={`pick min-h-12 rounded-xl px-4 text-[15px] font-semibold text-ink ${when === i ? on : off}`}>{label}</button>
            ))}
          </div>
          {when === slots.length && <input type="datetime-local" className="input mt-2.5" aria-label={t("Data e hora")} value={custom} onChange={(e) => setCustom(e.target.value)} />}
        </fieldset>
        <details className="rounded-xl border border-line bg-surface px-4 py-3">
          <summary className="cursor-pointer text-sm font-semibold">{t("Onde publicar ({n} de {total} vídeos)", { n: renders.filter((x) => plan[x.id]?.on).length, total: renders.length })}</summary>
          <div className="mt-3 flex flex-col gap-2">
            {renders.map((x) => (
              <div key={x.id} className="flex flex-wrap items-center gap-2 text-sm">
                <label className="flex min-h-11 flex-[1_1_200px] items-center gap-2">
                  <input type="checkbox" className="h-4 w-4 accent-go" checked={!!plan[x.id]?.on} onChange={(e) => setPlan({ ...plan, [x.id]: { ...plan[x.id], on: e.target.checked } })} />
                  {t(RENDER_TARGETS[x.profile] ?? x.profile)} · {x.language}
                </label>
                <select className="input" aria-label={t("Canal para {name}", { name: x.profile })} value={plan[x.id]?.channel ?? ""} onChange={(e) => setPlan({ ...plan, [x.id]: { ...plan[x.id], channel: e.target.value } })}>
                  <option value="">{t("canal…")}</option>
                  {channels.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.platform}, {c.publish_mode})</option>)}
                </select>
              </div>
            ))}
          </div>
        </details>
        {err && <p className="m-0 text-sm text-alert-text">{err}</p>}
        <div className="flex flex-wrap gap-3">
          <button className="btn" onClick={onSwapImages}>{t("Trocar imagens")}</button>
          <button className="btn btn-go min-h-[52px] px-6 text-base" onClick={schedule}>{scheduleLabel}</button>
        </div>
      </div>
    </section>
  );
}


/** "Avaliar com vidIQ" (backend POST /grow/renders/{id}/vidiq): title score, scored alternatives, tags. Never publishes. */
function VidiqBox({ r, onUse, reload }: { r: RenderRow; onUse: (title: string) => void; reload: () => void }) {
  const { t } = useT();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const v = r.meta.vidiq;
  const running = !!r.meta.vidiq_running_since || busy;
  useEffect(() => {
    if (!r.meta.vidiq_running_since) return;
    const id = setInterval(reload, 5000);
    return () => clearInterval(id);
  }, [r.meta.vidiq_running_since]); // eslint-disable-line react-hooks/exhaustive-deps
  async function start() {
    setBusy(true);
    setMsg(null);
    try {
      await api(`/grow/renders/${r.id}/vidiq`, { method: "POST" });
      reload();
    } catch (e) {
      setMsg(String(e).replace(/^Error: \d+ /, ""));
    } finally {
      setBusy(false);
    }
  }
  const short = r.profile.includes("short");
  return (
    <section className="flex flex-col gap-2 rounded-xl bg-bg px-4 py-3" aria-label={t("Avaliação do vidIQ")}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex-[1_1_220px] text-sm font-semibold">{t("Avaliação do vidIQ")}</span>
        <button className="btn btn-sm" disabled={running} onClick={start}>
          {running ? t("Avaliando…") : v ? t("Avaliar de novo") : t("Avaliar com vidIQ")}
        </button>
        <span className="text-[13px] text-muted">{short ? t("~10 créditos") : t("~20 créditos")}</span>
      </div>
      {msg && <p className="m-0 text-sm text-warn-text">{msg}</p>}
      {v?.error && <p className="m-0 text-sm text-alert-text">{v.error}</p>}
      {v && !v.error && (
        <div className="flex flex-col gap-2 text-sm text-body">
          {v.title_score != null && <p className="m-0">{t("Nota do título atual:")} <strong>{v.title_score}/100</strong></p>}
          {!!v.titles?.length && (
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
              {v.titles.map((x) => (
                <li key={x.title} className="flex flex-wrap items-center gap-2">
                  <span className="flex-[1_1_220px]">{x.title}{x.score != null && <strong> · {x.score}/100</strong>}</span>
                  <button className="btn btn-sm" onClick={() => onUse(x.title)}>{t("Usar este título")}</button>
                </li>
              ))}
            </ul>
          )}
          {v.thumb_score != null && <p className="m-0">{t("Nota da thumbnail:")} <strong>{v.thumb_score}/100</strong></p>}
          {v.thumb_feedback && <p className="m-0 text-muted">{v.thumb_feedback}</p>}
          {!!v.tags?.length && <p className="m-0"><strong>{t("Tags:")}</strong> {v.tags.join(", ")}</p>}
          {!!v.description_keywords?.length && <p className="m-0"><strong>{t("Palavras para a descrição:")}</strong> {v.description_keywords.join(", ")}</p>}
        </div>
      )}
    </section>
  );
}
