"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { PrepBar, PrepList, usePrep } from "@/components/PrepProgress";
import {
  api, connectChannelStats, deleteLive, isProducing, type Channel, getCutSettings, getLives, putCutSettings, getMonetization, getResearch, getViral, saveViralSettings, sendLiveLink, startResearch, startViral, uploadLive,
  type CutSettings, type LiveItem, type LivesStatus, type Monetization, type ResearchReport, type ViralState,
} from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Pieces of the old "Crescer" menu (docs/superpowers/specs/2026-11-21-clipagem-design.md), now spread over
 *  Fábrica > Clips e cortes (Lives, Cortes), Em alta > Pesquisa and Canais > Monetização. */

const KEY = "crescer.channel";
const isActive = (c: Channel) => isProducing(c); // linked TikTok accounts take no material of their own

/** Channel picked for Clips/Cortes, remembered per browser: the saved pick while it is an active channel, else the
 *  first active channel; null while the channels load or when there is none. */
export function useGrowChannel(): [number | null, (id: number) => void] {
  const { channels } = useAppData();
  const [picked, setPicked] = useState<number | null>(null);
  useEffect(() => {
    try {
      const saved = Number(localStorage.getItem(KEY));
      if (saved) setPicked(saved);
    } catch {
      /* private window: no saved pick */
    }
  }, []);
  const active = channels.filter(isActive);
  const channelId = active.some((c) => c.id === picked) ? picked : (active[0]?.id ?? null);
  function pick(id: number) {
    setPicked(id);
    try {
      localStorage.setItem(KEY, String(id));
    } catch {
      /* ignore */
    }
  }
  return [channelId, pick];
}

export function ChannelSelect({ value, onChange }: { value: number; onChange: (id: number) => void }) {
  const { t } = useT();
  const { channels } = useAppData();
  const active = channels.filter(isActive);
  return (
    <label className="flex items-center gap-2 text-sm text-body">
      {t("Canal")}
      <select className="input min-w-[220px] font-normal" value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
    </label>
  );
}

const PILL: Record<LiveItem["status"], string> = {
  working: "bg-chip text-body",
  review: "bg-alert-soft text-alert-text",
  waiting: "bg-chip text-warn-text",
  ok: "bg-go-soft text-go-text",
  error: "bg-alert-soft text-alert-text",
};

function errorText(e: unknown): string {
  const raw = String(e).replace(/^Error: \d+ /, "");
  try {
    return JSON.parse(raw).detail ?? raw;
  } catch {
    return raw;
  }
}

export function LivesTab({ channelId }: { channelId: number }) {
  const { t, locale } = useT();
  const { setToast } = useAppData();
  const [st, setSt] = useState<LivesStatus | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const load = useCallback(() => getLives(channelId).then(setSt).catch(() => setSt(null)), [channelId]);
  useEffect(() => {
    setSt(undefined);
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load]);
  // a download/analysis is running: its step moves every few seconds
  const working = !!st?.items.some((i) => i.status === "working");
  useEffect(() => {
    if (!working) return;
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [working, load]);

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      setToast({ msg: done });
      setUrl("");
      setFile(null);
      setTitle("");
      load();
    } catch (e) {
      setToast({ msg: errorText(e) });
    } finally {
      setBusy(false);
    }
  }
  const [confirming, setConfirming] = useState<string | null>(null);
  async function remove(it: LiveItem) {
    setBusy(true);
    try {
      const r = await deleteLive(channelId, it.video_id);
      const gb = r.freed / 1024 ** 3;
      const size = gb >= 0.1 ? `${gb.toLocaleString(locale, { maximumFractionDigits: 1 })} GB` : `${Math.round(r.freed / 1024 ** 2).toLocaleString(locale)} MB`;
      setToast({ msg: r.freed ? t("Vídeo excluído. {size} liberados.", { size }) : t("Vídeo excluído.") });
      setConfirming(null);
      load();
    } catch (e) {
      setToast({ msg: errorText(e) });
    } finally {
      setBusy(false);
    }
  }
  const status = (it: LiveItem) =>
    it.status === "review" ? t("cortes para revisar") : it.status === "ok" ? t("pronto") : it.status === "error" ? t("erro") : it.status === "waiting" ? t("aguardando") : t("gerando");

  if (st === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (st === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  return (
    <div className="flex flex-col gap-4">
      <section className="card flex flex-col gap-3 p-5" aria-labelledby="h-link">
        <h2 id="h-link" className="h2 m-0">{t("Colar link")}</h2>
        <p className="m-0 text-sm text-body">{t("Sua live ou vídeo, ou de outro criador, do YouTube, Twitch, Kick, TikTok ou Instagram (posts públicos). Vídeos de outras pessoas viram react, com comentário seu e crédito ao criador. Uma live que ainda está no ar fica aguardando e é feita quando a gravação sair.")}</p>
        <div className="flex flex-wrap items-end gap-3">
          <input className="input flex-[2_1_320px] font-normal" type="url" inputMode="url" placeholder={t("Link do YouTube, Twitch, Kick, TikTok ou Instagram")} value={url} onChange={(e) => setUrl(e.target.value)} />
          <input className="input flex-[1_1_200px] font-normal" placeholder={t("Título (opcional)")} value={title} onChange={(e) => setTitle(e.target.value)} />
          <button className="btn btn-go" disabled={!url.trim() || busy} onClick={() => run(() => sendLiveLink(channelId, url.trim(), title.trim()), t("Link recebido. O vídeo está sendo baixado e analisado; o pacote aparece abaixo."))}>
            {t("Gerar melhores momentos e Shorts")}
          </button>
        </div>
        <p className="m-0 text-sm text-muted">{t("O sistema analisa o vídeo e marca os momentos com mais chance de viralizar. Você revisa os cortes no editor antes de qualquer Short ser feito.")}</p>
        <CutPrefs channelId={channelId} />
      </section>

      <section className="card flex flex-col gap-3 p-5" aria-labelledby="h-upload">
        <h2 id="h-upload" className="h2 m-0">{t("Enviar uma gravação")}</h2>
        <p className="m-0 text-sm text-body">{t("Para gravações que não estão em nenhum site, como as do console.")}</p>
        <div className="flex flex-wrap items-end gap-3">
          <input type="file" accept="video/*" className="text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <button className="btn" disabled={!file || busy} onClick={() => file && run(() => uploadLive(channelId, file, title.trim()), t("Gravação recebida. O pacote aparece abaixo quando ficar pronto."))}>
            {busy ? t("Enviando…") : t("Enviar e gerar")}
          </button>
        </div>
      </section>

      <section className="flex flex-col gap-2" aria-labelledby="h-list">
        <h2 id="h-list" className="h2 m-0">{t("Vídeos enviados")}</h2>
        {st.items.length === 0 ? (
          <div className="card p-5 text-body">{t("Nada enviado ainda. Cole o link de uma live acima.")}</div>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {st.items.map((it) => (
              <li key={it.video_id} className="card flex flex-col gap-2 px-5 py-4">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="flex-[1_1_260px] text-[15px] font-semibold">
                    {it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.title}</a> : it.title}
                  </span>
                  {it.own === false && (
                    <span className="rounded-full bg-chip px-3 py-1.5 text-[13px] font-semibold text-warn-text">
                      🟡 {t("de {who}", { who: it.uploader ?? t("outro criador") })}
                    </span>
                  )}
                  <span className={`rounded-full px-3 py-1.5 text-[13px] font-semibold ${PILL[it.status]}`}>{status(it)}</span>
                  <span className="text-[13px] text-muted">{new Date(it.at).toLocaleString(locale)}</span>
                  {it.status !== "working" && confirming !== it.video_id && (
                    <button className="btn btn-sm" onClick={() => setConfirming(it.video_id)}>{t("Excluir")}</button>
                  )}
                </div>
                {confirming === it.video_id && (
                  <div className="flex flex-wrap items-center gap-2 rounded-[10px] bg-alert-soft px-4 py-3 text-sm" role="group" aria-label={t("Confirmar exclusão")}>
                    <span className="flex-[1_1_260px] text-alert-text">{t("Excluir este vídeo? A gravação, a versão leve e a transcrição saem do disco. Os Shorts e vídeos já feitos com ele continuam.")}</span>
                    <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => remove(it)}>{busy ? t("Excluindo…") : t("Excluir")}</button>
                    <button className="btn btn-sm" disabled={busy} onClick={() => setConfirming(null)}>{t("Cancelar")}</button>
                  </div>
                )}
                {it.status === "working" && it.progress && <PrepBar p={it.progress} />}
                {it.error && it.status !== "ok" && <p className={`m-0 text-sm ${it.status === "error" ? "text-alert-text" : "text-warn-text"}`}>{it.error}</p>}
                {it.status === "review" && it.review != null && (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <Link className="btn btn-sm btn-ink" href={`/fabrica/cortes/${it.review}`}>{t("Revisar cortes")}</Link>
                    <span className="text-muted">{t("Nenhum Short é feito antes de você aprovar.")}</span>
                  </div>
                )}
                {it.package?.method && (
                  <p className="m-0 text-[13px] text-muted">
                    {it.package.method === "viral" ? t("Shorts escolhidos pelo potencial de viralizar.") : t("Shorts escolhidos pelo volume e movimento (a análise de viralização não estava disponível).")}
                  </p>
                )}
                {it.package && (
                  <div className="flex flex-wrap gap-2 text-sm">
                    {it.package.long != null && <Link className="btn btn-sm" href={`/projetos/${it.package.long}`}>{t("Melhores momentos")}</Link>}
                    {it.package.shorts.map((id, i) => <Link key={id} className="btn btn-sm" href={`/projetos/${id}`}>{t("Short {n}", { n: i + 1 })}</Link>)}
                    {!!it.package.removed && <span className="text-muted">{t("{n} apagado(s)", { n: it.package.removed })}</span>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** Per-channel cut options (backend /cuts/channels/{id}/settings). */
function CutPrefs({ channelId }: { channelId: number }) {
  const { t } = useT();
  const { setToast } = useAppData();
  const [p, setP] = useState<CutSettings | null>(null);
  useEffect(() => { getCutSettings(channelId).then(setP).catch(() => setP(null)); }, [channelId]);
  if (!p) return null;
  async function change(patch: Partial<CutSettings>) {
    try {
      setP(await putCutSettings(channelId, patch));
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }
  return (
    <div className="flex flex-col gap-1 border-t border-line pt-3">
      <label className="flex min-h-11 items-center gap-2 text-sm text-body">
        <input type="checkbox" className="h-5 w-5 accent-[#00843d]" checked={p.shorts_commentary} onChange={(e) => change({ shorts_commentary: e.target.checked })} />
        {t("Shorts com o meu comentário por padrão (desligado: só o corte, com o som original)")}
      </label>
      <label className="flex min-h-11 items-center gap-2 text-sm text-body">
        <input type="checkbox" className="h-5 w-5 accent-[#00843d]" checked={p.auto_approve_cuts} onChange={(e) => change({ auto_approve_cuts: e.target.checked })} />
        {t("Aprovar os cortes sozinho neste canal, sem passar pelo editor")}
      </label>
    </div>
  );
}

/** Videos whose cuts wait in the editor (any channel), on top of Clips e cortes. */
export function PendingCutReviews() {
  const { t } = useT();
  const { cutReviews, channelOf, reload } = useAppData();
  const prep = usePrep();
  const running = prep.filter((i) => i.status === "working" || i.status === "analysing" || i.status === "error");
  const nRunning = running.filter((i) => i.status !== "error").length;
  const [was, setWas] = useState(0);
  useEffect(() => {
    if (nRunning < was) reload(); // an analysis finished: its review joins the list below
    setWas(nRunning);
  }, [nRunning, was, reload]);
  if (!cutReviews.length && !running.length) return null;
  return (
    <section className="card flex flex-col gap-2 p-5" aria-labelledby="h-pending-cuts">
      <h2 id="h-pending-cuts" className="h2 m-0">{t("Cortes para revisar")}</h2>
      {running.length > 0 && (
        <div className="-mx-5 border-b border-line-soft">
          <PrepList items={running} />
        </div>
      )}
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {cutReviews.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-3 py-1">
            <span className="flex-[1_1_260px] text-[15px] font-semibold">{r.title}</span>
            <span className="text-[13px] text-muted">{channelOf(r.channel_id)?.name ?? ""}</span>
            <Link className="btn btn-sm btn-ink" href={`/fabrica/cortes/${r.id}`}>{t("Revisar cortes")}</Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function CutsTab({ channelId, onCut }: { channelId: number; onCut: () => void }) {
  const { t, locale } = useT();
  const { setToast } = useAppData();
  const [st, setSt] = useState<ViralState | null | undefined>(undefined);
  const [topic, setTopic] = useState("GTA 6");
  const [lang, setLang] = useState("pt");
  const [pick, setPick] = useState(0);
  const [topicsText, setTopicsText] = useState<string | null>(null);
  const load = useCallback(() => getViral(channelId).then(setSt).catch(() => setSt(null)), [channelId]);
  useEffect(() => {
    setSt(undefined);
    setPick(0);
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  async function search() {
    try {
      await startViral(channelId, topic.trim(), lang);
      setToast({ msg: t("Pesquisando vídeos virais. Leva alguns minutos.") });
      setPick(0);
      load();
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }
  async function cut(url: string, title: string) {
    try {
      await sendLiveLink(channelId, url, title);
      setToast({ msg: t("Corte pedido. O vídeo está sendo baixado e analisado na aba Lives.") });
      onCut();
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }
  async function removeSearch(at: string, name: string) {
    try {
      setSt(await api<ViralState>(`/grow/${channelId}/viral/searches?at=${encodeURIComponent(at)}`, { method: "DELETE" }));
      setPick(0);
      setToast({ msg: t("Pesquisa “{topic}” apagada.", { topic: name }) });
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }
  async function saveTopics() {
    if (topicsText === null || !st) return;
    const topics = topicsText.split(",").map((x) => x.trim()).filter(Boolean).slice(0, 6);
    setSt(await saveViralSettings(channelId, { topics, language: st.language, daily_credits: st.daily_credits, auto_cuts: st.auto_cuts }));
    setTopicsText(null);
    setToast({ msg: t("Temas salvos. A pesquisa diária roda às 7h (horário local).") });
  }

  if (st === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (st === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  const n = (v: number | null) => (v == null ? "—" : v.toLocaleString(locale, { maximumFractionDigits: 0 }));
  const cur = st.searches[pick];
  return (
    <div className="flex flex-col gap-4">
      <section className="card flex flex-col gap-3 p-5" aria-labelledby="h-viral">
        <h2 id="h-viral" className="h2 m-0">{t("Vídeos virais para cortar")}</h2>
        <div className="flex flex-wrap items-end gap-3">
          <input className="input flex-[2_1_260px] font-normal" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder={t("Assunto (ex.: GTA 6)")} />
          <select className="input w-32 font-normal" value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t("Idioma")}>
            <option value="pt">{t("Português")}</option>
            <option value="en">{t("Inglês")}</option>
          </select>
          <button className="btn btn-go" disabled={st.running || topic.trim().length < 2} onClick={search}>{st.running ? t("Pesquisando…") : t("Buscar vídeos virais")}</button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm text-body">
          <span>{t("Todo dia às 7h (horário local), pesquisa e prepara os {n} melhores cortes:", { n: st.auto_cuts })}</span>
          {topicsText === null ? (
            <>
              <strong>{st.topics.length ? st.topics.join(", ") : t("nenhum tema")}</strong>
              <button className="btn btn-sm" onClick={() => setTopicsText(st.topics.join(", "))}>{t("Editar temas")}</button>
            </>
          ) : (
            <>
              <input className="input flex-[1_1_260px] font-normal" value={topicsText} onChange={(e) => setTopicsText(e.target.value)} placeholder={t("receitas rápidas, finanças pessoais")} />
              <button className="btn btn-sm" onClick={saveTopics}>{t("Salvar")}</button>
            </>
          )}
        </div>
      </section>

      {st.searches.length > 0 && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label={t("Pesquisas anteriores")}>
          {st.searches.map((x, i) => (
            <span key={x.at} className={`btn btn-sm gap-0 p-0 ${pick === i ? "btn-ink" : ""}`}>
              <button role="tab" aria-selected={pick === i} onClick={() => setPick(i)} className="min-h-9 px-3">
                {x.topic} · {new Date(x.at).toLocaleDateString(locale)}{x.daily ? " · " + t("diária") : ""}
              </button>
              <button type="button" className="min-h-9 rounded-r-full px-2.5 opacity-70 hover:opacity-100" onClick={() => removeSearch(x.at, x.topic)}
                aria-label={t("Apagar a pesquisa “{topic}”", { topic: x.topic })} title={t("Apagar esta pesquisa")}>×</button>
            </span>
          ))}
        </div>
      )}

      {!cur ? (
        <div className="card p-5 text-body">{st.running ? t("Pesquisando… os resultados aparecem aqui.") : t("Nenhuma pesquisa ainda. Busque um assunto acima.")}</div>
      ) : (
        <>
          {!!cur.patterns?.length && (
            <section className="card flex flex-col gap-2 p-5" aria-labelledby="h-patterns">
              <h2 id="h-patterns" className="h2 m-0">{t("O que está funcionando em “{topic}”", { topic: cur.topic })}</h2>
              <ul className="m-0 flex flex-col gap-1.5 pl-5 text-sm text-body">
                {cur.patterns.map((p) => <li key={p.format}><strong>{p.format}</strong>: “{p.example}” ({n(p.views)} views). {p.why}</li>)}
              </ul>
            </section>
          )}
          <p className="m-0 text-[13px] text-muted">
            {cur.source === "vidiq" ? t("Fonte: vidIQ") : t("Fonte: busca do YouTube (vidIQ indisponível nesta pesquisa)")} · {new Date(cur.at).toLocaleString(locale)}
          </p>
          <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 md:grid-cols-2 xl:grid-cols-3">
            {cur.items.map((v) => (
              <li key={v.video_id} className="card flex flex-col gap-2 overflow-hidden">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`https://i.ytimg.com/vi/${v.video_id}/mqdefault.jpg`} alt="" className="aspect-video w-full object-cover" loading="lazy" />
                <div className="flex flex-col gap-2 px-4 pb-4">
                  <a href={v.url} target="_blank" rel="noreferrer" className="text-[15px] font-semibold leading-snug">{v.title}</a>
                  <span className="text-[13px] text-muted">{v.channel}</span>
                  <div className="flex flex-wrap gap-1.5 text-[13px]">
                    <span className="rounded-full bg-chip px-2.5 py-1 text-body">{n(v.views)} views</span>
                    {v.vph != null && <span className="rounded-full bg-chip px-2.5 py-1 text-body">{n(v.vph)}{t("/hora")}</span>}
                    {v.outlier != null && <span className="rounded-full bg-go-soft px-2.5 py-1 text-go-text">{v.outlier.toLocaleString(locale, { maximumFractionDigits: 1 })}× {t("a média do canal")}</span>}
                  </div>
                  {v.why && <p className="m-0 text-sm text-body">{v.why}</p>}
                  {v.cut_idea && <p className="m-0 text-sm text-body"><strong>{t("Ideia de corte:")}</strong> {v.cut_idea}</p>}
                  <button className="btn btn-sm mt-1 self-start" onClick={() => cut(v.url, v.title)}>{t("Cortar este")}</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

const VERDICT: Record<string, string> = { subindo: "bg-go-soft text-go-text", "estável": "bg-chip text-body", saturado: "bg-alert-soft text-alert-text" };

export function ResearchTab({ channelId }: { channelId: number }) {
  const { t, locale } = useT();
  const { setToast } = useAppData();
  const [data, setData] = useState<{ running: boolean; reports: ResearchReport[] } | null | undefined>(undefined);
  const [topic, setTopic] = useState("GTA 6");
  const [lang, setLang] = useState("pt");
  const [pick, setPick] = useState(0);
  const load = useCallback(() => getResearch(channelId).then(setData).catch(() => setData(null)), [channelId]);
  useEffect(() => {
    setData(undefined);
    setPick(0);
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);
  async function start() {
    try {
      await startResearch(channelId, topic.trim(), lang);
      setToast({ msg: t("Analisando o tema no vidIQ. Leva alguns minutos.") });
      setPick(0);
      load();
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }
  if (data === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (data === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  const n = (v: number | null) => (v == null ? "—" : v.toLocaleString(locale, { maximumFractionDigits: 0 }));
  const r = data.reports[pick];
  return (
    <div className="flex flex-col gap-4">
      <section className="card flex flex-col gap-3 p-5" aria-labelledby="h-research">
        <h2 id="h-research" className="h2 m-0">{t("Pesquisar um tema")}</h2>
        <div className="flex flex-wrap items-end gap-3">
          <input className="input flex-[2_1_260px] font-normal" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder={t("Assunto (ex.: GTA 6)")} />
          <select className="input w-32 font-normal" value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t("Idioma")}>
            <option value="pt">{t("Português")}</option>
            <option value="en">{t("Inglês")}</option>
          </select>
          <button className="btn btn-go" disabled={data.running || topic.trim().length < 2} onClick={start}>{data.running ? t("Pesquisando…") : t("Analisar tema")}</button>
        </div>
        <p className="m-0 text-[13px] text-muted">{t("Cerca de 20 créditos do vidIQ por análise.")}</p>
      </section>
      {data.reports.length > 1 && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label={t("Pesquisas anteriores")}>
          {data.reports.map((x, i) => (
            <button key={x.at} role="tab" aria-selected={pick === i} onClick={() => setPick(i)} className={`btn btn-sm ${pick === i ? "btn-ink" : ""}`}>
              {x.topic} · {new Date(x.at).toLocaleDateString(locale)}
            </button>
          ))}
        </div>
      )}
      {!r ? (
        <div className="card p-5 text-body">{data.running ? t("Pesquisando… os resultados aparecem aqui.") : t("Nenhuma análise ainda. Digite um tema acima.")}</div>
      ) : r.error ? (
        <div className="card p-5 text-alert-text">{r.error}</div>
      ) : (
        <>
          <section className="card flex flex-wrap items-center gap-3 p-5" aria-label={t("Veredito")}>
            <span className="text-[15px] font-semibold">{r.topic}</span>
            {r.verdict && <span className={`rounded-full px-3 py-1.5 text-[13px] font-semibold ${VERDICT[r.verdict]}`}>{t(r.verdict)}</span>}
            <span className="flex-[1_1_260px] text-sm text-body">{r.verdict_why}</span>
          </section>
          {!!r.ideas.length && (
            <section className="card flex flex-col gap-2 p-5" aria-labelledby="h-ideas">
              <h2 id="h-ideas" className="h2 m-0">{t("Ideias de vídeo")}</h2>
              <ul className="m-0 flex flex-col gap-1.5 pl-5 text-sm text-body">
                {r.ideas.map((i) => <li key={i.title}><strong>{i.title}</strong>: {i.why}</li>)}
              </ul>
            </section>
          )}
          {!!r.keywords.length && (
            <section className="card flex flex-col gap-2 overflow-x-auto p-5" aria-labelledby="h-kw">
              <h2 id="h-kw" className="h2 m-0">{t("Palavras-chave")}</h2>
              <table className="w-full text-left text-sm">
                <thead className="text-muted"><tr><th className="py-1.5 pr-3 font-semibold">{t("Palavra")}</th><th className="pr-3 font-semibold">{t("Volume")}</th><th className="pr-3 font-semibold">{t("Concorrência")}</th><th className="font-semibold">{t("Crescimento")}</th></tr></thead>
                <tbody>
                  {r.keywords.map((k) => (
                    <tr key={k.keyword} className="border-t border-line-soft">
                      <td className="py-1.5 pr-3">{k.keyword}</td><td className="pr-3">{n(k.volume)}</td><td className="pr-3">{n(k.competition)}</td>
                      <td>{k.growth == null ? "—" : `${k.growth > 0 ? "+" : ""}${n(k.growth)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          {!!r.competitor_outliers.length && (
            <section className="card flex flex-col gap-2 p-5" aria-labelledby="h-comp">
              <h2 id="h-comp" className="h2 m-0">{t("O que está estourando nos concorrentes")}</h2>
              <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-body">
                {r.competitor_outliers.map((o) => (
                  <li key={o.video_id}><a href={o.url} target="_blank" rel="noreferrer">{o.title}</a> · {o.channel} · {n(o.views)} views{o.outlier != null && ` · ${o.outlier.toLocaleString(locale, { maximumFractionDigits: 1 })}×`}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Bar({ label, value, target, unit }: { label: string; value: number | null; target: number; unit: string }) {
  const { locale } = useT();
  const pct = value == null ? 0 : Math.min(100, Math.round((value / target) * 100));
  const n = (v: number | null) => (v == null ? "—" : v.toLocaleString(locale, { maximumFractionDigits: 0 }));
  return (
    <div className="flex flex-col gap-2 rounded-xl bg-bg p-4">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] text-muted">{label}</span>
        <span className="ml-auto font-display text-2xl font-bold">{n(value)}</span>
        <span className="text-sm text-body">/ {n(target)} {unit}</span>
      </div>
      <div className="h-2.5 overflow-hidden rounded-full bg-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <div className={`h-full rounded-full ${pct >= 100 ? "bg-go" : "bg-ink"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function MoneyTab({ channelId }: { channelId: number }) {
  const { t, locale } = useT();
  const [m, setM] = useState<Monetization | null | undefined>(undefined);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => getMonetization(channelId).then(setM).catch(() => setM(null)), [channelId]);
  useEffect(() => {
    setM(undefined);
    setMsg(null);
    load();
  }, [load]);
  const syncing = !!m?.vidiq_sync?.running;
  useEffect(() => {
    if (!syncing) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [syncing, load]);

  async function connect() {
    try {
      const { auth_url } = await connectChannelStats(channelId);
      window.open(auth_url, "_blank", "noopener");
      setMsg(t("Autorize na aba do Google e depois volte aqui."));
    } catch (e) {
      setMsg(errorText(e)); // e.g. the Google OAuth client is not set up yet
    }
  }
  async function syncVidiq() {
    if (!confirm(t("Sincronizar gasta cerca de {n} créditos do vidIQ. Continuar?", { n: m?.vidiq_sync?.credits ?? 10 }))) return;
    try {
      await api(`/grow/${channelId}/monetization/vidiq`, { method: "POST" });
      setMsg(null);
      load();
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  if (m === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (m === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  const days = (d: number | null) => (d == null ? null : d === 0 ? t("meta batida") : t("~{n} dias no ritmo atual", { n: d.toLocaleString(locale) }));
  return (
    <section className="card flex flex-col gap-4 p-5" aria-labelledby="h-money">
      <h2 id="h-money" className="h2 m-0">{t("Programa de Parceiros do YouTube")}</h2>
      {!m.connected && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="m-0 flex-[1_1_300px] text-[13px] text-muted">
            {m.source === "vidiq"
              ? <>{t("Números do vidIQ (atualizados na pesquisa diária), enquanto o YouTube Analytics não está conectado.")}{m.updated_at ? " · " + new Date(m.updated_at).toLocaleString(locale) : ""}</>
              : t("Ainda sem números do vidIQ para este canal.")}
          </p>
          {m.vidiq_sync?.available && (
            <button className="btn btn-sm" disabled={syncing} onClick={syncVidiq}>{syncing ? t("Sincronizando… (até 5 min)") : t("Sincronizar com o vidIQ")}</button>
          )}
        </div>
      )}
      {m.vidiq_sync?.error && !syncing && <p className="m-0 text-sm text-warn-text">{m.vidiq_sync.error}</p>}
      {!m.connected && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl bg-bg px-4 py-3">
          <span className="flex-[1_1_300px] text-sm text-body">{t("Conecte o YouTube Analytics (gratuito) para ver as horas, os inscritos e quanto tempo falta.")}</span>
          <button className="btn btn-go btn-sm" onClick={connect}>{t("Conectar YouTube Analytics")}</button>
        </div>
      )}
      {msg && <p className="m-0 text-sm text-warn-text">{msg}</p>}
      {m.error && <p className="m-0 text-sm text-warn-text">{m.error}</p>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Bar label={t("Inscritos")} value={m.subs} target={m.subs_target} unit="" />
        {m.shorts && <Bar label={t("Views de Shorts (últimos 90 dias)")} value={m.shorts.views_90d} target={m.shorts.target} unit="" />}
        <Bar label={t("Horas públicas de vídeo longo e live (12 meses)")} value={m.hours} target={m.hours_target} unit="h" />
      </div>
      {m.shorts && (
        <p className="m-0 text-sm text-body">
          {t("Caminho dos Shorts: 1.000 inscritos + 10 milhões de views em 90 dias. Precisa de ~{need} views por dia; agora: {now}.", {
            need: m.shorts.needed_per_day.toLocaleString(locale),
            now: m.shorts.views_per_day == null ? "—" : Math.round(m.shorts.views_per_day).toLocaleString(locale),
          })}
        </p>
      )}
      {(m.connected || m.source === "vidiq") && (
        <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-body">
          {days(m.pace.days_to_subs) && <li>{t("Inscritos:")} {days(m.pace.days_to_subs)}</li>}
          {days(m.pace.days_to_hours) && <li>{t("Horas:")} {days(m.pace.days_to_hours)}</li>}
        </ul>
      )}
      <p className="m-0 text-sm text-body">
        {t("As regras mudam depois de {date}. Shorts não contam horas; lives públicas e vídeos longos contam.", { date: new Date(m.deadline + "T12:00:00").toLocaleDateString(locale) })}
      </p>
    </section>
  );
}
