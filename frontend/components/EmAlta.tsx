"use client";

/** Em alta v2 tabs: Lives, Vídeos, Pesquisa vidIQ (channel optional) and Seguindo. Every item has "Produzir", which
 *  opens /fabrica/novo pre-filled (?topic=, ?link=, ?trend=) for the user to review before anything starts. */
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useAppData } from "@/components/AppData";
import { api, isProducing, type Trend } from "@/lib/api";
import {
  getDiscover, getFollow, getGlobalResearch, produceHref, runFollow, saveFollow, startDiscover, startGlobalResearch,
  type DiscoverResult, type DiscoverState, type FollowChannel, type FollowState, type GlobalReport, type HotItem,
} from "@/lib/emAlta";
import { useT } from "@/lib/i18n";

function errorText(e: unknown): string {
  const raw = String(e).replace(/^Error: \d+ /, "");
  try {
    const d = JSON.parse(raw).detail;
    return typeof d === "string" ? d : d?.message ?? raw;
  } catch {
    return raw;
  }
}

const PLATFORM_NAME: Record<string, string> = { youtube: "YouTube", twitch: "Twitch", kick: "Kick" };

function when(at: string, locale: string): string {
  const d = new Date(at);
  return isNaN(+d) ? at : d.toLocaleString(locale, { dateStyle: "short", timeStyle: "short" });
}

/* ---------------- Lives / Vídeos ---------------- */

function useDiscover() {
  const [st, setSt] = useState<DiscoverState | null | undefined>(undefined);
  const load = useCallback(() => getDiscover().then(setSt).catch(() => setSt(null)), []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!st?.running) return;
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [st?.running, load]);
  return { st, load };
}

function SearchBar({ region, running, onSearch, hint }: { region: string; running: boolean; onSearch: (topic: string) => void; hint: string }) {
  const { t } = useT();
  const [topic, setTopic] = useState("");
  return (
    <form className="card flex flex-col gap-2 p-5" onSubmit={(e) => { e.preventDefault(); onSearch(topic.trim()); }}>
      <div className="flex flex-wrap items-center gap-3">
        <input className="input flex-[2_1_260px] font-normal" value={topic} onChange={(e) => setTopic(e.target.value)}
          placeholder={t("Assunto (opcional, ex.: GTA 6)")} aria-label={t("Assunto")} maxLength={120} />
        <button type="submit" className="btn btn-go" disabled={running}>{running ? t("Buscando… (até 2 min)") : t("Buscar")}</button>
      </div>
      <p className="m-0 text-[13px] text-muted">{hint} {region ? "" : t("Sem região: o que está em alta no mundo.")}</p>
    </form>
  );
}

function compact(n: number, locale: string) {
  return n.toLocaleString(locale, { notation: "compact", maximumFractionDigits: 1 });
}

function HotCard({ item }: { item: HotItem }) {
  const { t, locale } = useT();
  const platform = item.platform ?? "youtube";
  const metric = item.viewers != null
    ? t("{n} assistindo", { n: compact(item.viewers, locale) })
    : item.views != null ? t("{n} visualizações", { n: compact(item.views, locale) }) : null;
  return (
    <article className="card flex flex-col overflow-hidden p-0">
      {item.thumb ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={item.thumb} alt="" className="aspect-video w-full bg-chip object-cover" loading="lazy" />
      ) : (
        <div className="aspect-video w-full bg-chip" />
      )}
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="pill bg-chip px-2 py-0.5 text-[11px] text-body">{PLATFORM_NAME[platform] ?? platform}</span>
          {item.kind === "live" && (
            <span className="pill inline-flex items-center gap-1.5 bg-chip px-2 py-0.5 text-[11px] text-body">
              <span className="h-2 w-2 rounded-full bg-alert-dot" />{t("ao vivo")}
            </span>
          )}
          {item.kind === "was_live" && <span className="pill bg-chip px-2 py-0.5 text-[11px] text-body">{t("live encerrada")}</span>}
          {item.kind === "short" && <span className="pill bg-chip px-2 py-0.5 text-[11px] text-body">Short</span>}
        </div>
        <h3 className="m-0 line-clamp-2 text-[15px] leading-[1.35] font-semibold">{item.title || t("Sem título")}</h3>
        <span className="text-[13px] text-muted">
          {[item.channel, item.game, metric].filter(Boolean).join(" · ")}
        </span>
        <div className="mt-auto flex flex-wrap gap-2 pt-1">
          {item.kind === "live"
            ? <AfterLive item={item} />
            : <Link href={produceHref({ topic: item.title, link: item.url })} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>}
          <a href={item.url} target="_blank" rel="noreferrer" className="btn btn-sm no-underline">{t("Abrir")}</a>
        </div>
      </div>
    </article>
  );
}

/** A live still on air: "Produzir quando acabar" hands it to the Crescer flow (cvm/lives/after_live.py), which waits
 *  for the recording (up to 24 h, retried hourly), then analyses loudness/motion, chat and viral potential and makes
 *  one long best-moments video plus Shorts of the strong moments. The user picks the channel first. */
function AfterLive({ item }: { item: HotItem }) {
  const { t } = useT();
  const { channels, setToast } = useAppData();
  const active = channels.filter((c) => c.status === "active" && isProducing(c) && !c.made_for_kids);
  const [open, setOpen] = useState(false);
  const [cid, setCid] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pick = cid ?? active[0]?.id ?? null;
  async function go() {
    if (!pick) return;
    setBusy(true);
    setErr(null);
    try {
      await api(`/grow/${pick}/lives/link`, { method: "POST", body: JSON.stringify({ url: item.url, title: item.title || null, after_live: true }) });
      setOpen(false);
      setToast({ msg: t("Combinado. Quando a live acabar e a gravação sair, ela vira melhores momentos e Shorts. Acompanhe em Fábrica > Clips e cortes.") });
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  if (!open) return <button type="button" className="btn btn-go btn-sm" onClick={() => setOpen(true)}>{t("Produzir quando acabar")}</button>;
  return (
    <div className="flex w-full flex-col gap-2 rounded-xl bg-bg p-3 text-[13px]">
      <p className="m-0 text-body">{t("A live ainda está no ar. Quando a gravação sair (confiro de hora em hora, por até 24 h), o sistema analisa o chat, o volume e os momentos com chance de viralizar e faz um vídeo de melhores momentos e Shorts dos momentos fortes.")}</p>
      <label className="flex flex-col gap-1 font-semibold">
        {t("Canal")}
        <select className="input font-normal" value={pick ?? ""} onChange={(e) => setCid(Number(e.target.value))}>
          {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </label>
      {err && <p role="alert" className="m-0 text-alert-text">{err}</p>}
      <div className="flex gap-2">
        <button type="button" className="btn btn-go btn-sm" disabled={busy || !pick} onClick={go}>{busy ? t("Enviando…") : t("Confirmar")}</button>
        <button type="button" className="btn btn-sm" onClick={() => setOpen(false)}>{t("Cancelar")}</button>
      </div>
    </div>
  );
}

/** "Ver mais": a long list shows `step` items and opens the next `step` on each click. */
export function useMore<T>(items: T[], step: number): [T[], ReactNode] {
  const { t } = useT();
  const [n, setN] = useState(step);
  const left = items.length - n;
  const button = left > 0
    ? <button type="button" className="btn btn-sm self-center" onClick={() => setN((x) => x + step)}>{t("Ver mais ({n})", { n: left })}</button>
    : null;
  return [items.slice(0, n), button];
}

function Grid({ items, empty }: { items: HotItem[]; empty: string }) {
  const [shown, more] = useMore(items, 12);
  if (!items.length) return <p className="m-0 text-sm text-muted">{empty}</p>;
  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
        {shown.map((i) => <HotCard key={i.id} item={i} />)}
      </div>
      {more}
    </>
  );
}

function pickResult(results: DiscoverResult[], region: string): DiscoverResult | null {
  return results.find((r) => r.region === region) ?? null;
}

function useSearch(region: string) {
  const { setToast } = useAppData();
  const { st, load } = useDiscover();
  async function search(topic: string) {
    try {
      await startDiscover(topic, region);
      load();
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }
  return { st, search };
}

function ResultHeader({ r, running, onRefresh }: { r: DiscoverResult; running: boolean; onRefresh: (topic: string) => void }) {
  const { t, locale } = useT();
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="m-0 flex-1 text-[13px] text-muted">
        {r.topic ? t("Busca: “{topic}”", { topic: r.topic }) : t("Sem assunto: o que está em alta em geral")} · {t("atualizado em {when}", { when: when(r.at, locale) })}
      </p>
      <button type="button" className="btn btn-sm" disabled={running} onClick={() => onRefresh(r.topic || "")}>
        {running ? t("Buscando…") : t("Atualizar")}
      </button>
    </div>
  );
}

function SourceNote({ text, tone = "muted" }: { text: string; tone?: "muted" | "warn" }) {
  return <p className={`m-0 rounded-xl bg-chip px-3 py-2 text-[13px] ${tone === "warn" ? "text-warn-text" : "text-body"}`}>{text}</p>;
}

export function HotLivesTab({ region, platforms }: { region: string; platforms?: string[] }) {
  const { t } = useT();
  const { st, search } = useSearch(region);
  const [platform, setPlatform] = useState("");
  const r = st ? pickResult(st.results, region) : null;
  const live = useMemo(() => {
    if (!r) return [];
    const all = [...r.live.map((i) => ({ ...i, platform: i.platform ?? "youtube" as const })), ...(r.twitch?.items ?? []), ...(r.kick?.items ?? [])];
    return all.filter((i) => !platform || i.platform === platform).sort((a, b) => (b.viewers ?? 0) - (a.viewers ?? 0));
  }, [r, platform]);
  if (st === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (st === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  return (
    <div className="flex flex-col gap-4">
      <SearchBar region={region} running={st.running} onSearch={search} hint={t("Lives no ar agora no YouTube, na Twitch e na Kick, e as lives mais vistas da semana.")} />
      {st.error && <SourceNote tone="warn" text={st.error} />}
      {!r ? (
        <div className="card p-5 text-body">{st.running ? t("Buscando… os resultados aparecem aqui.") : t("Nenhuma busca para esta região ainda. Clique em Buscar.")}</div>
      ) : (
        <>
          <ResultHeader r={r} running={st.running} onRefresh={search} />
          <div className="flex flex-wrap gap-2" role="group" aria-label={t("Plataforma")}>
            {([["", t("Todas")], ["youtube", "YouTube"], ["twitch", "Twitch"], ["kick", "Kick"]] as const).filter(([k]) => !k || !platforms || platforms.includes(k)).map(([k, label]) => (
              <button key={k} aria-pressed={platform === k} onClick={() => setPlatform(k)}
                className={`pick min-h-9 rounded-full border px-3.5 text-sm font-semibold ${platform === k ? "border-ink bg-ink text-white" : "border-[#d9dbdf] bg-surface text-ink"}`}>
                {label}
              </button>
            ))}
          </div>
          {r.youtube_error && (!platform || platform === "youtube") && <SourceNote tone="warn" text={r.youtube_error} />}
          {r.twitch?.error && (!platform || platform === "twitch") && <SourceNote tone="warn" text={t(r.twitch.error)} />}
          {(!platform || platform === "kick") && (
            <SourceNote text={r.kick?.error ? t(r.kick.error) : t("Kick é experimental: usa a página pública do site, que pode mudar ou bloquear sem aviso.")} tone={r.kick?.error ? "warn" : "muted"} />
          )}
          <section className="flex flex-col gap-3" aria-labelledby="h-live-now">
            <h2 id="h-live-now" className="h2 m-0">{t("Ao vivo agora")}</h2>
            <Grid items={live} empty={t("Nenhuma live no ar encontrada.")} />
          </section>
          {(!platform || platform === "youtube") && (
            <section className="flex flex-col gap-3" aria-labelledby="h-past-lives">
              <h2 id="h-past-lives" className="h2 m-0">{t("Lives mais vistas da semana")}</h2>
              <p className="m-0 text-[13px] text-muted">{t("Já terminaram, então dá para baixar e cortar.")}</p>
              <Grid items={r.lives} empty={t("Nenhuma live encerrada nesta semana.")} />
            </section>
          )}
        </>
      )}
    </div>
  );
}

export function HotVideosTab({ region }: { region: string }) {
  const { t } = useT();
  const { st, search } = useSearch(region);
  const r = st ? pickResult(st.results, region) : null;
  if (st === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (st === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  return (
    <div className="flex flex-col gap-4">
      <SearchBar region={region} running={st.running} onSearch={search} hint={t("Os vídeos mais vistos da semana no YouTube.")} />
      {st.error && <SourceNote tone="warn" text={st.error} />}
      {!r ? (
        <div className="card p-5 text-body">{st.running ? t("Buscando… os resultados aparecem aqui.") : t("Nenhuma busca para esta região ainda. Clique em Buscar.")}</div>
      ) : (
        <>
          <ResultHeader r={r} running={st.running} onRefresh={search} />
          {r.youtube_error && <SourceNote tone="warn" text={r.youtube_error} />}
          <Grid items={r.videos} empty={t("Nenhum vídeo encontrado.")} />
        </>
      )}
    </div>
  );
}

/* ---------------- Pesquisa vidIQ (canal opcional) ---------------- */

const VERDICT: Record<string, string> = { subindo: "bg-go-soft text-go-text", "estável": "bg-chip text-body", saturado: "bg-alert-soft text-alert-text" };

export function GlobalResearchTab() {
  const { t, locale } = useT();
  const { channels, setToast } = useAppData();
  const [data, setData] = useState<{ running: boolean; credits: number; reports: GlobalReport[] } | null | undefined>(undefined);
  const [topic, setTopic] = useState("");
  const [lang, setLang] = useState("pt");
  const [channelId, setChannelId] = useState(0);
  const [pick, setPick] = useState(0);
  const load = useCallback(() => getGlobalResearch().then(setData).catch(() => setData(null)), []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!data?.running) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [data?.running, load]);
  const credits = data?.credits ?? 20;
  async function start() {
    if (!confirm(t("Esta pesquisa gasta cerca de {n} créditos do vidIQ. Continuar?", { n: credits }))) return;
    try {
      await startGlobalResearch(topic.trim(), lang, channelId || null);
      setToast({ msg: t("Pesquisando o tema no vidIQ. Leva alguns minutos.") });
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
  const active = channels.filter(isProducing);
  return (
    <div className="flex flex-col gap-4">
      <section className="card flex flex-col gap-3 p-5" aria-labelledby="h-gresearch">
        <h2 id="h-gresearch" className="h2 m-0">{t("Pesquisar um tema")}</h2>
        <div className="flex flex-wrap items-end gap-3">
          <input className="input flex-[2_1_240px] font-normal" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder={t("Assunto (ex.: GTA 6)")} aria-label={t("Assunto")} maxLength={120} />
          <select className="input w-32 font-normal" value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t("Idioma")}>
            <option value="pt">{t("Português")}</option>
            <option value="en">{t("Inglês")}</option>
          </select>
          <select className="input min-w-[200px] font-normal" value={channelId} onChange={(e) => setChannelId(Number(e.target.value))} aria-label={t("Canal (opcional)")}>
            <option value={0}>{t("Sem canal (pesquisa geral)")}</option>
            {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn btn-go" disabled={data.running || topic.trim().length < 2} onClick={start}>{data.running ? t("Pesquisando…") : t("Pesquisar")}</button>
        </div>
        <p className="m-0 text-[13px] text-muted">{t("Cerca de {n} créditos do vidIQ por pesquisa. Com um canal, ela também olha os concorrentes dele.", { n: credits })}</p>
      </section>
      {data.reports.length > 1 && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label={t("Pesquisas anteriores")}>
          {data.reports.map((x, i) => (
            <button key={x.at + i} role="tab" aria-selected={pick === i} onClick={() => setPick(i)} className={`btn btn-sm ${pick === i ? "btn-ink" : ""}`}>
              {x.topic}{x.channel_name ? ` · ${x.channel_name}` : ""} · {new Date(x.at).toLocaleDateString(locale)}
            </button>
          ))}
        </div>
      )}
      {!r ? (
        <div className="card p-5 text-body">{data.running ? t("Pesquisando… os resultados aparecem aqui.") : t("Nenhuma pesquisa ainda. Digite um tema acima.")}</div>
      ) : r.error ? (
        <div className="card p-5 text-alert-text">{r.error}</div>
      ) : (
        <>
          <section className="card flex flex-wrap items-center gap-3 p-5" aria-label={t("Veredito")}>
            <span className="text-[15px] font-semibold">{r.topic}</span>
            {r.verdict && <span className={`rounded-full px-3 py-1.5 text-[13px] font-semibold ${VERDICT[r.verdict]}`}>{t(r.verdict)}</span>}
            <span className="flex-[1_1_260px] text-sm text-body">{r.verdict_why}</span>
            <Link href={produceHref({ topic: r.topic })} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>
          </section>
          {!!r.ideas.length && (
            <section className="card flex flex-col gap-1 p-5" aria-labelledby="h-gideas">
              <h2 id="h-gideas" className="h2 m-0 mb-1">{t("Ideias de vídeo")}</h2>
              {r.ideas.map((i) => (
                <div key={i.title} className="flex flex-wrap items-center gap-3 border-b border-line-soft py-2.5 last:border-b-0">
                  <div className="flex flex-[1_1_260px] flex-col gap-0.5">
                    <span className="text-[15px] font-semibold">{i.title}</span>
                    <span className="text-sm text-muted">{i.why}</span>
                  </div>
                  <Link href={produceHref({ topic: i.title })} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>
                </div>
              ))}
            </section>
          )}
          {!!r.competitor_outliers.length && (
            <section className="card flex flex-col gap-1 p-5" aria-labelledby="h-gcomp">
              <h2 id="h-gcomp" className="h2 m-0 mb-1">{t("Vídeos que estão estourando")}</h2>
              {r.competitor_outliers.map((o) => (
                <div key={o.video_id} className="flex flex-wrap items-center gap-3 border-b border-line-soft py-2.5 last:border-b-0">
                  <div className="flex flex-[1_1_260px] flex-col gap-0.5">
                    <a href={o.url} target="_blank" rel="noreferrer" className="text-[15px] font-semibold">{o.title}</a>
                    <span className="text-sm text-muted">{o.channel} · {t("{n} visualizações", { n: n(o.views) })}{o.outlier != null && ` · ${o.outlier.toLocaleString(locale, { maximumFractionDigits: 1 })}×`}</span>
                  </div>
                  <Link href={produceHref({ topic: o.title, link: o.url })} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>
                </div>
              ))}
            </section>
          )}
          {!!r.keywords.length && (
            <section className="card flex flex-col gap-2 overflow-x-auto p-5" aria-labelledby="h-gkw">
              <h2 id="h-gkw" className="h2 m-0">{t("Palavras-chave")}</h2>
              <table className="w-full text-left text-sm">
                <thead className="text-muted"><tr><th className="py-1.5 pr-3 font-semibold">{t("Palavra")}</th><th className="pr-3 font-semibold">{t("Volume")}</th><th className="pr-3 font-semibold">{t("Concorrência")}</th><th className="pr-3 font-semibold">{t("Crescimento")}</th><th><span className="sr-only">{t("Ação")}</span></th></tr></thead>
                <tbody>
                  {r.keywords.map((k) => (
                    <tr key={k.keyword} className="border-t border-line-soft">
                      <td className="py-1.5 pr-3">{k.keyword}</td><td className="pr-3">{n(k.volume)}</td><td className="pr-3">{n(k.competition)}</td>
                      <td className="pr-3">{k.growth == null ? "—" : `${k.growth > 0 ? "+" : ""}${n(k.growth)}%`}</td>
                      <td className="py-1.5 text-right"><Link href={produceHref({ topic: k.keyword })} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </div>
  );
}

/* ---------------- Seguindo ---------------- */

export function FollowTab() {
  const { t, locale } = useT();
  const { setToast } = useAppData();
  const [st, setSt] = useState<FollowState | null | undefined>(undefined);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [channels, setChannels] = useState<FollowChannel[]>([]);
  const [region, setRegion] = useState("BR");
  const [kw, setKw] = useState("");
  const [platform, setPlatform] = useState<FollowChannel["platform"]>("youtube");
  const [handle, setHandle] = useState("");
  const [dirty, setDirty] = useState(false);
  const [matches, setMatches] = useState<Trend[]>([]);

  const [shownMatches, moreMatches] = useMore(matches, 15);
  const loadMatches = useCallback(() => api<Trend[]>("/trends?source=follow&limit=100").then(setMatches).catch(() => {}), []);
  useEffect(() => {
    getFollow().then((v) => {
      setSt(v);
      setKeywords(v.keywords);
      setChannels(v.channels);
      setRegion(v.region || "BR");
    }).catch(() => setSt(null));
    loadMatches();
  }, [loadMatches]);

  function addKeyword() {
    const k = kw.trim().replace(/\s+/g, " ");
    if (k.length < 2 || keywords.some((x) => x.toLowerCase() === k.toLowerCase())) return;
    setKeywords([...keywords, k]);
    setKw("");
    setDirty(true);
  }
  function addChannel() {
    const h = handle.trim();
    if (!h) return;
    setChannels([...channels, { platform, handle: h }]);
    setHandle("");
    setDirty(true);
  }
  async function save() {
    try {
      const v = await saveFollow({ keywords, channels, region });
      setSt(v);
      setKeywords(v.keywords);
      setChannels(v.channels);
      setDirty(false);
      setToast({ msg: t("Salvo. A próxima busca traz o que for novo.") });
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }
  async function runNow() {
    try {
      if (dirty) await save();
      await runFollow();
      setToast({ msg: t("Buscando o que você segue. Os resultados entram em Todas as tendências em alguns minutos.") });
      setTimeout(loadMatches, 60000);
    } catch (e) {
      setToast({ msg: errorText(e) });
    }
  }

  if (st === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (st === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  return (
    <div className="flex flex-col gap-4">
      <section className="card flex flex-col gap-4 p-5" aria-labelledby="h-follow">
        <div className="flex flex-col gap-1">
          <h2 id="h-follow" className="h2 m-0">{t("O que você segue")}</h2>
          <p className="m-0 text-sm text-body">{t("A cada 30 minutos o painel procura essas palavras e esses canais e coloca o que achar em Todas as tendências, marcado como “seguindo”.")}</p>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-semibold text-body">{t("Palavras-chave")}</span>
          <div className="flex flex-wrap gap-2">
            {keywords.map((k) => (
              <span key={k} className="pill inline-flex items-center gap-1.5 bg-chip text-body">
                {k}
                <button type="button" className="border-0 bg-transparent p-0 text-muted hover:text-ink" aria-label={t("Remover {name}", { name: k })}
                  onClick={() => { setKeywords(keywords.filter((x) => x !== k)); setDirty(true); }}>×</button>
              </span>
            ))}
            {!keywords.length && <span className="text-sm text-muted">{t("Nenhuma palavra ainda.")}</span>}
          </div>
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); addKeyword(); }}>
            <input className="input flex-[1_1_220px] font-normal" value={kw} onChange={(e) => setKw(e.target.value)} placeholder={t("Ex.: GTA 6, Copa do Mundo")} aria-label={t("Nova palavra-chave")} maxLength={80} />
            <button type="submit" className="btn" disabled={kw.trim().length < 2}>{t("Adicionar")}</button>
          </form>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-semibold text-body">{t("Canais")}</span>
          {channels.length ? (
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
              {channels.map((c, i) => (
                <li key={`${c.platform}:${c.handle}:${i}`} className="flex items-center gap-2 rounded-xl bg-bg px-3 py-2 text-sm">
                  <span className="pill bg-chip px-2 py-0.5 text-[11px] text-body">{PLATFORM_NAME[c.platform]}</span>
                  <span className="flex-1 font-semibold">{c.handle}</span>
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => { setChannels(channels.filter((_, j) => j !== i)); setDirty(true); }}>{t("Remover")}</button>
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-sm text-muted">{t("Nenhum canal ainda.")}</span>
          )}
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); addChannel(); }}>
            <select className="input w-36 font-normal" value={platform} onChange={(e) => setPlatform(e.target.value as FollowChannel["platform"])} aria-label={t("Plataforma")}>
              <option value="youtube">YouTube</option>
              <option value="twitch">Twitch</option>
              <option value="kick">{t("Kick (experimental)")}</option>
            </select>
            <input className="input flex-[1_1_220px] font-normal" value={handle} onChange={(e) => setHandle(e.target.value)} placeholder={t("@canal ou link")} aria-label={t("Canal a seguir")} maxLength={200} />
            <button type="submit" className="btn" disabled={!handle.trim()}>{t("Adicionar")}</button>
          </form>
        </div>

        <label className="flex flex-wrap items-center gap-2 text-sm text-body">
          {t("Região das buscas por palavra")}
          <select className="input w-40 font-normal" value={region} onChange={(e) => { setRegion(e.target.value); setDirty(true); }}>
            <option value="BR">{t("Brasil")}</option>
            <option value="AE">{t("Emirados")}</option>
            <option value="US">{t("Global")}</option>
          </select>
        </label>

        <div className="flex flex-wrap items-center gap-3">
          <button className="btn btn-go" onClick={save} disabled={!dirty}>{dirty ? t("Salvar") : t("Salvo")}</button>
          <button className="btn" onClick={runNow} disabled={!keywords.length && !channels.length}>{t("Buscar agora")}</button>
          <span className="text-[13px] text-muted">
            {st.last_run ? t("Última busca: {when}", { when: when(st.last_run, locale) }) : t("Ainda não buscou.")}
            {st.last_error ? ` · ${st.last_error}` : ""}
          </span>
        </div>
      </section>

      <section className="card overflow-hidden" aria-labelledby="h-follow-found">
        <h2 id="h-follow-found" className="h2 m-0 px-5 pt-5 pb-2">{t("Encontrados")}</h2>
        {matches.length ? shownMatches.map((m) => (
          <div key={m.id} className="flex flex-wrap items-center gap-3 border-b border-line-soft px-5 py-3 last:border-b-0">
            <div className="flex min-w-0 flex-[1_1_260px] flex-col gap-0.5">
              <span className="text-[15px] font-semibold">{m.title}</span>
              <span className="text-[13px] text-muted">{[String(m.meta.followed ?? ""), String(m.meta.channel ?? "")].filter(Boolean).join(" · ")}</span>
            </div>
            {m.url && <a href={m.url} target="_blank" rel="noreferrer" className="btn btn-sm no-underline">{t("Abrir")}</a>}
            <Link href={produceHref({ trend: m.id, topic: m.title, link: m.url })} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>
          </div>
        )) : (
          <p className="m-0 px-5 pb-5 text-sm text-muted">{t("Nada encontrado ainda. Adicione palavras ou canais e clique em Buscar agora.")}</p>
        )}
        {moreMatches && <div className="flex justify-center p-3">{moreMatches}</div>}
      </section>
    </div>
  );
}
