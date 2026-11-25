"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { PageHeader, VideoRemovedNote } from "@/components/Bits";
import { FilterBar, matches, optionsOf, useFilters } from "@/components/Filters";
import { IconCopy, IconExternal } from "@/components/Icons";
import { API, api, deleteProject, LANG_LABEL, PLATFORM_LABEL, RENDER_TARGETS, type Channel, type VideoRemoved } from "@/lib/api";
import { dateLocale, tr, useT } from "@/lib/i18n";

type Pub = {
  id: number;
  status: string;
  mode: string;
  scheduled_at: string;
  published_at?: string | null;
  url: string | null;
  project_title: string | null;
  project_id: number | null;
  channel: Channel;
  render: { id: number; language: string; profile: string; meta?: { video_removed?: VideoRemoved | null } };
};

type Kit = {
  title: string | null;
  description: string | null;
  hashtags: string[];
  tags: string[];
  caption: string;
  upload_page: string;
  checklist: string[];
  file_url: string;
  thumbnail_url: string;
  thumbnail_variants?: string[];
  title_variants?: string[];
  video_removed?: VideoRemoved | null;
  // Phase 7 checklist items (backend cvm/api/publish.py kit)
  is_short?: boolean;
  end_screen_video?: KitVideo | null;
  end_screen_from?: number | null;
  related_video?: KitVideo | null;
  made_for_kids?: boolean;
  made_for_kids_locked?: boolean;
  synthetic?: boolean;
  category?: { id: string; name: string } | null;
  chapters?: boolean;
};
type KitVideo = { title: string | null; url: string; views: number | null; why: string };

type BrowserStatus = { state: string; steps: { step: string; ok: boolean; error?: string }[]; detected_url: string | null; error?: string };

const asDate = (s: string) => new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
/** Deletes the whole project behind a post (asks first). */
async function removePub(p: Pub, after: () => void) {
  if (!p.project_id) return;
  if (await deleteProject({ id: p.project_id, title: p.project_title ?? tr("este vídeo"), job: undefined })) after();
}

const RECENT_PAGE = 10;
/** "18:00" today, "sex 15/11 18:00" on another day. */
function whenLabel(d: Date, locale: string): string {
  const time = d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString()
    ? time : `${d.toLocaleDateString(locale, { weekday: "short", day: "2-digit", month: "2-digit" })} ${time}`;
}

const STATUS: Record<string, string> = { scheduled: "agendado", ready_for_manual_publish: "pronto para publicar", published: "publicado", failed: "falhou" };
const BROWSER_STATE: Record<string, string> = {
  starting: "Abrindo o Chrome…",
  waiting_login: "Aguardando a página de upload. Se ela pedir login, feche a janela, use “Fazer login no canal” e tente de novo.",
  filling: "Preenchendo…",
  review: "Pronto: revise na janela do Chrome e clique em Publicar você mesmo.",
  manual: "Não consegui seguir a página; termine manualmente na janela.",
  closed: "Janela fechada.",
};

function CopyField({ label, value }: { label: string; value: string }) {
  const [ok, setOk] = useState(false);
  const { t } = useT();
  return (
    <div className="flex items-center gap-2.5 rounded-xl bg-bg px-3.5 py-3">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-[13px] text-muted">{label}</span>
        <span className="truncate text-sm" title={value}>{value || "—"}</span>
      </span>
      <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(value).then(() => { setOk(true); setTimeout(() => setOk(false), 1500); })}>
        <IconCopy />{ok ? t("Copiado") : t("Copiar")}
      </button>
    </div>
  );
}

function BrowserAssist({ pub, onUrl }: { pub: Pub; onUrl: (u: string) => void }) {
  const [st, setSt] = useState<BrowserStatus | null>(null);
  const { t } = useT();
  const active = st && !["closed", "idle"].includes(st.state);
  const busy = st && ["starting", "waiting_login", "filling"].includes(st.state); // review/manual: may start over
  useEffect(() => {
    api<BrowserStatus>(`/publications/${pub.id}/browser`).then(setSt).catch(() => {});
  }, [pub.id]);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => api<BrowserStatus>(`/publications/${pub.id}/browser`).then((s) => {
      setSt(s);
      if (s.detected_url) onUrl(s.detected_url);
    }), 2000);
    return () => clearInterval(t);
  }, [active, pub.id, onUrl]);
  return (
    <details className="rounded-xl border border-line px-4 py-3">
      <summary className="cursor-pointer text-sm font-semibold">{t("Preparar no navegador (opcional)")}</summary>
      <div className="mt-3 flex flex-col gap-2 text-sm text-body">
        <p className="m-0">{t("Abre o upload no Chrome, anexa o vídeo e preenche os textos. O clique em Publicar é seu.")}</p>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" title={busy ? t("Para a tentativa atual e começa de novo") : undefined} onClick={() => api(`/publications/${pub.id}/browser`, { method: "POST" }).then(() => { setSt({ state: "starting", steps: [], detected_url: null }); onUrl(""); })}>{busy ? t("Recomeçar") : st && st.state !== "idle" ? t("Preparar de novo") : t("Preparar no navegador")}</button>
          <button className="btn btn-sm" onClick={() => api(`/channels/${pub.channel.id}/browser-login`, { method: "POST" })}>{t("Fazer login no canal (1ª vez)")}</button>
        </div>
        {st && st.state !== "idle" && <p className="m-0 text-warn-text">{BROWSER_STATE[st.state] ? t(BROWSER_STATE[st.state]) : st.state}</p>}
        {st?.steps.map((s, i) => <span key={i} className={s.ok ? "text-go-text" : "text-alert-text"} title={s.error}>{s.ok ? "✓" : `✕ ${t("(fazer à mão)")}`} {s.step}</span>)}
        {st?.error && <p className="m-0 text-alert-text">{st.error}</p>}
      </div>
    </details>
  );
}

const VIDEO_WHY: Record<string, string> = {
  best: "o longo com mais views do canal",
  latest: "o longo mais recente do canal",
  same_project: "o longo deste mesmo tema",
  topic: "o longo de tema mais parecido",
};

/** Studio settings to tick on YouTube, plus the videos to link (end screen / Short's related video). */
function KitFlags({ kit }: { kit: Kit }) {
  const { t } = useT();
  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}`;
  const video = (label: string, v: KitVideo) => (
    <div className="flex flex-col gap-1 text-sm">
      <span className="font-semibold">{label}</span>
      <span className="flex flex-wrap items-center gap-2">
        <a href={v.url} target="_blank" rel="noreferrer" className="font-medium text-ink underline">{v.title || v.url}</a>
        <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(v.url)}><IconCopy />{t("Copiar link")}</button>
        <span className="text-xs text-muted">{t(VIDEO_WHY[v.why] ?? v.why)}{v.views ? ` · ${v.views.toLocaleString(dateLocale())} views` : ""}</span>
      </span>
    </div>
  );
  return (
    <div className="flex flex-col gap-2.5 rounded-[14px] bg-chip p-3">
      <div className="flex flex-wrap gap-2 text-xs">
        {kit.synthetic && <span className="pill bg-surface text-body">{t("Conteúdo alterado ou sintético: Sim")}</span>}
        <span className="pill bg-surface text-body" title={kit.made_for_kids_locked ? t("Canal infantil: sempre “Sim”") : undefined}>
          {kit.made_for_kids ? t("Conteúdo para crianças: Sim (travado)") : t("Conteúdo para crianças: Não")}
        </span>
        {kit.category && <span className="pill bg-surface text-body">{t("Categoria: {name}", { name: kit.category.name })}</span>}
        {!kit.is_short && kit.chapters && <span className="pill bg-surface text-body">{t("Capítulos na descrição")}</span>}
      </div>
      {!kit.is_short && (kit.end_screen_video
        ? video(kit.end_screen_from != null ? t("Tela final (a partir de {time})", { time: mmss(kit.end_screen_from) }) : t("Tela final"), kit.end_screen_video)
        : <span className="text-sm text-muted">{t("Tela final: nenhum longo publicado ainda; use “Melhor para o espectador”.")}</span>)}
      {kit.is_short && (kit.related_video
        ? video(t("Vídeo relacionado do Short"), kit.related_video)
        : <span className="text-sm text-muted">{t("Vídeo relacionado: nenhum longo publicado neste canal ainda.")}</span>)}
    </div>
  );
}

function PublishKit({ pub, onDone }: { pub: Pub; onDone: () => void }) {
  const [kit, setKit] = useState<Kit | null>(null);
  const [url, setUrl] = useState("");
  const onUrl = useCallback((u: string) => setUrl(u), []);
  const { t, locale } = useT();
  useEffect(() => {
    api<Kit>(`/publications/${pub.id}/kit`).then(setKit).catch(() => {});
  }, [pub.id]);
  const yt = pub.channel.platform.startsWith("youtube");
  const removed = pub.render.meta?.video_removed ?? kit?.video_removed;

  async function done() {
    await api(`/publications/${pub.id}/published`, { method: "POST", body: JSON.stringify({ url: url || null }) });
    onDone();
  }

  return (
    <article className="card flex flex-col gap-4 p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-display text-[22px] leading-none font-bold">{whenLabel(asDate(pub.scheduled_at), locale)}</span>
        <span className="flex-[1_1_240px] text-base font-semibold">{pub.project_title}</span>
        {pub.status === "scheduled" && <span className="pill bg-chip text-warn-text">{t("agendado para {time}, pode postar antes ou agendar no YouTube para esse horário", { time: whenLabel(asDate(pub.scheduled_at), locale) })}</span>}
        <span className="pill bg-chip text-body">{pub.channel.name} · {PLATFORM_LABEL[pub.channel.platform] ?? pub.channel.platform} · {t(RENDER_TARGETS[pub.render.profile] ?? pub.render.profile)} · {pub.render.language}</span>
      </div>
      {!kit ? (
        <p className="m-0 text-muted">{t("Carregando kit…")}</p>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          {removed ? (
            <div className="flex w-[320px] max-w-full flex-none flex-col gap-2">
              <img src={`${API}${kit.thumbnail_url}`} alt="" className="aspect-video w-full rounded-[14px] object-cover" />
              <VideoRemovedNote removed={removed} url={pub.url} />
            </div>
          ) : (
          <video
            src={`${API}${kit.file_url}`}
            poster={`${API}${kit.thumbnail_url}`}
            controls
            preload="metadata"
            className={`flex-none rounded-[14px] bg-ink object-contain ${pub.render.profile === "youtube" ? "aspect-video w-[320px] max-w-full" : "aspect-[9/16] h-[300px] w-auto"}`}
          />
          )}
          <div className="flex min-w-0 flex-[1_1_320px] flex-col gap-4">
          <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-2.5">
            {yt ? (
              <>
                <CopyField label={t("Título")} value={kit.title ?? ""} />
                <CopyField label={t("Descrição e hashtags")} value={`${kit.description ?? ""}\n\n${kit.hashtags.map((h) => "#" + h.replace(/^#/, "")).join(" ")}`} />
                <CopyField label={t("Tags")} value={kit.tags.join(", ")} />
              </>
            ) : (
              <CopyField label={t("Legenda e hashtags")} value={kit.caption} />
            )}
          </div>
          {yt && <KitFlags kit={kit} />}
          {!!kit.checklist.length && (
            <ul className="m-0 flex flex-col gap-1 pl-5 text-sm text-body">{kit.checklist.map((c) => <li key={c}>{c}</li>)}</ul>
          )}
          <div className="flex flex-wrap items-end gap-3">
            {!removed && <button className="btn" onClick={() => api(`/publications/${pub.id}/reveal`, { method: "POST" })}>{t("Abrir arquivo do vídeo")}</button>}
            <a className="btn" href={kit.upload_page} target="_blank" rel="noreferrer"><IconExternal />{t("Página de upload")}</a>
            <a className="btn" href={`${API}${kit.thumbnail_url}`} download>{t("Baixar thumbnail")}</a>
          </div>
          {(kit.title_variants?.length ?? 0) > 1 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-semibold">{t("Títulos para “Testar e comparar”")}</span>
              <ol className="m-0 flex flex-col gap-1 pl-5 text-sm text-body">
                {kit.title_variants!.map((v, i) => <li key={v}><span className="text-muted">{t("Versão {v}", { v: "ABC"[i] })}:</span> {v}</li>)}
              </ol>
            </div>
          )}
          {(kit.thumbnail_variants?.length ?? 0) > 1 && (
            <div className="flex flex-col gap-2">
              <span className="text-sm font-semibold">{t("Thumbnails para “Testar e comparar”")}</span>
              <div className="flex flex-wrap gap-2.5">
                {kit.thumbnail_variants!.map((u, i) => (
                  <a key={u} href={`${API}${u}`} download={`thumbnail_${"ABC"[i]}.jpg`} className="flex flex-col items-center gap-1 text-xs text-muted">
                    <img src={`${API}${u}`} alt="" className="aspect-video w-[160px] rounded-[10px] object-cover" />
                    {t("Versão {v}", { v: "ABC"[i] })}
                  </a>
                ))}
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-[1_1_260px] flex-col gap-1.5 text-sm font-semibold">
              {t("Link do post")}
              <input type="url" className="input font-normal" placeholder={t("Cole aqui depois de publicar")} value={url} onChange={(e) => setUrl(e.target.value)} />
            </label>
            <button className="btn btn-go" onClick={done}>{t("Marcar como publicado")}</button>
            <button className="btn btn-danger" onClick={() => removePub(pub, onDone)}>{t("Excluir")}</button>
          </div>
          <BrowserAssist pub={pub} onUrl={onUrl} />
          </div>
        </div>
      )}
    </article>
  );
}

export default function PublishPage() {
  const { reload } = useAppData();
  const { t, locale } = useT();
  const [pubs, setPubs] = useState<Pub[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [allRecent, setAllRecent] = useState(false);
  const kit = (p: Pub) => <PublishKit key={p.id} pub={p} onDone={() => { load(); reload(); }} />;
  // a list read while a project is being deleted can carry a publication whose render/channel is already gone
  const load = () => api<Pub[]>("/publications").then((p) => { setPubs(p.filter((x) => x.render && x.channel)); setLoaded(true); }).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  async function unpublish(id: number) {
    await api(`/publications/${id}/unpublish`, { method: "POST" });
    await load();
    reload();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const [f, setF, clearF] = useFilters("publicar");
  const defs = [
    { key: "channel", label: t("Canal"), options: optionsOf(pubs, (p) => String(p.channel.id), (v) => pubs.find((p) => String(p.channel.id) === v)?.channel.name ?? v) },
    { key: "lang", label: t("Idioma"), options: optionsOf(pubs, (p) => p.render.language, (l) => t(LANG_LABEL[l] ?? l)) },
    { key: "profile", label: t("Formato"), options: optionsOf(pubs, (p) => p.render.profile, (v) => t(RENDER_TARGETS[v] ?? v)) },
    { key: "status", label: t("Situação"), options: optionsOf(pubs, (p) => p.status, (v) => t(STATUS[v] ?? v)) },
  ];
  const shown = pubs.filter((p) => (!f.channel || String(p.channel.id) === f.channel)
    && (!f.lang || p.render.language === f.lang)
    && (!f.profile || p.render.profile === f.profile)
    && (!f.status || p.status === f.status)
    && matches(f.q, p.project_title, p.channel.name));
  const endOfToday = new Date();
  endOfToday.setHours(23, 59, 59, 999);
  const byTime = (a: Pub, b: Pub) => +asDate(a.scheduled_at) - +asDate(b.scheduled_at);
  const ready = shown.filter((p) => p.status === "ready_for_manual_publish" || (p.status === "scheduled" && asDate(p.scheduled_at) <= endOfToday)).sort(byTime);
  const upcoming = shown.filter((p) => p.status === "scheduled" && asDate(p.scheduled_at) > endOfToday).sort(byTime);
  // every upcoming video shows its whole kit, by day: post early or schedule it in YouTube Studio for its slot
  const upcomingDays = Object.entries(upcoming.reduce<Record<string, Pub[]>>((acc, p) => {
    const day = asDate(p.scheduled_at).toLocaleDateString(locale, { weekday: "long", day: "2-digit", month: "2-digit" });
    (acc[day] ??= []).push(p);
    return acc;
  }, {}));
  const recent = shown.filter((p) => p.status === "published" || p.status === "failed").sort((a, b) => +asDate(b.published_at ?? b.scheduled_at) - +asDate(a.published_at ?? a.scheduled_at));
  const shownRecent = allRecent ? recent : recent.slice(0, RECENT_PAGE);

  return (
    <>
      <PageHeader title={t("Publicar")} subtitle={t("Na hora marcada, abra o kit, poste e cole o link.")}>
        <Link href="/fabrica/historico" className="btn">{t("Ver histórico")}</Link>
      </PageHeader>
      {pubs.length > 1 && <FilterBar defs={defs} f={f} set={setF} clear={clearF} shown={shown.length} total={pubs.length} />}
      <section className="flex flex-col gap-3">
        <h2 className="h2 m-0">{t("Hoje")}</h2>
        {ready.map(kit)}
        {loaded && !ready.length && <div className="card p-6 text-body">{t("Nada para publicar hoje. Para adiantar um vídeo dos próximos dias, use “Próximos”.")}</div>}
      </section>
      <section className="flex flex-col gap-3">
        <h2 className="h2 m-0">{t("Próximos")}</h2>
        {!upcoming.length && <div className="card p-6 text-body">{t("Nada agendado para os próximos dias.")}</div>}
        {upcomingDays.map(([day, list]) => (
          <div key={day} className="flex flex-col gap-3">
            <h3 className="m-0 text-[15px] font-semibold text-muted">{day} <span className="font-medium">({list.length})</span></h3>
            {list.map(kit)}
          </div>
        ))}
      </section>
      {!!recent.length && (
        <section className="flex flex-col gap-3">
          <h2 className="h2 m-0">{t("Recentes")}</h2>
          <div className="card overflow-hidden">
            {shownRecent.map((p) => (
              <div key={p.id} className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-line-soft px-5 py-4 last:border-b-0">
                <span className="flex-[1_1_240px] text-[15px]">{p.project_title}</span>
                <span className={`pill ${p.status === "published" ? "bg-go-soft text-go-text" : "bg-alert-soft text-alert-text"}`}>{STATUS[p.status] ? t(STATUS[p.status]) : p.status}</span>
                {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-sm">{t("ver post")}</a>}
                {p.render.meta?.video_removed && <span className="pill bg-chip text-muted" title={t("Vídeo removido para liberar espaço em {date} — está no YouTube", { date: asDate(p.render.meta.video_removed.at).toLocaleDateString(locale) })}>{t("vídeo removido")}</span>}
                {p.status === "published" && <button className="btn btn-sm" title={t("Volta para Hoje com o kit, para publicar de novo")} onClick={() => unpublish(p.id)}>{t("Desfazer publicado")}</button>}
              </div>
            ))}
            {recent.length > RECENT_PAGE && (
              <button className="w-full border-0 bg-transparent px-5 py-3 text-left text-sm font-semibold text-ink hover:bg-bg" onClick={() => setAllRecent(!allRecent)}>
                {allRecent ? t("Ver menos") : t("Ver todos ({n})", { n: recent.length })}
              </button>
            )}
          </div>
        </section>
      )}
    </>
  );
}
