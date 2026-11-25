"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { Initial, PageHeader, ScoreBadge, Tabs } from "@/components/Bits";
import { NextSlots, WeekCalendar, WeekGoal } from "@/components/ChannelWeek";
import FacecamMarker from "@/components/FacecamMarker";
import { MoneyTab } from "@/components/Grow";
import QueueItem from "@/components/QueueItem";
import { api, channelWeek, connectChannelStats, decideTitlePattern, LANG_FLAG, LANG_LABEL, PLATFORM_LABEL, STYLE_LABEL, type Channel, type ChannelStatsFull, type ChannelWeek } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { groupOf } from "@/lib/pipeline";
import ProfileForm from "./ProfileForm";

type NicheIdea = { index: number; topic: string; why: string; style: string; hooks: Record<string, string>; risks?: string; potential?: number; project_id?: number | null };
type NicheAnalysis = { id: number; status: string; created_at: string; summary: string | null; items: NicheIdea[]; error: string | null };

function nicheOf(c: Channel): string | null {
  if (c.profile?.niche) return c.profile.niche;
  const line = (c.brief ?? "").split("\n").find((l) => /^(temas?|topics?):/i.test(l.trim()));
  return line ? line.split(":").slice(1).join(":").trim() : null;
}

const STATUS_PILL: Record<string, { label: string; cls: string }> = {
  active: { label: "ativo", cls: "bg-go-soft text-go-text" },
  paused: { label: "pausado", cls: "bg-chip text-warn-text" },
  archived: { label: "arquivado", cls: "bg-line-soft text-muted" },
  series: { label: "série (sem canal próprio)", cls: "bg-claude-soft text-claude-text" },
};

function Tile({ label, value, sub, pct }: { label: string; value: string; sub?: string; pct?: number }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-xl bg-bg p-3.5">
      <span className="text-[13px] text-muted">{label}</span>
      <span className="font-display text-2xl font-bold">{value}</span>
      {pct != null && (
        <div className="h-2 overflow-hidden rounded-full bg-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={label}>
          <div className={`h-full rounded-full ${pct >= 100 ? "bg-go" : "bg-ink"}`} style={{ width: `${Math.min(100, pct)}%` }} />
        </div>
      )}
      {sub && <span className="text-[13px] text-body">{sub}</span>}
    </div>
  );
}

/** "Dados" (Phase 8): YouTube Analytics numbers, monetization progress, the weekly suggestions and the title pattern
 *  to approve (GET /channels/{id}/stats, backend cvm/api/stats.py); a "Conectar" button until the channel is linked. */
function StatsSection({ channelId }: { channelId: number }) {
  const { t, locale } = useT();
  const [stats, setStats] = useState<ChannelStatsFull | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback((refresh = false) => {
    setBusy(refresh);
    return api<ChannelStatsFull>(`/channels/${channelId}/stats${refresh ? "?refresh=1" : ""}`)
      .then(setStats).catch(() => setStats(null)).finally(() => setBusy(false));
  }, [channelId]);
  useEffect(() => {
    load();
  }, [load]);

  async function connect() {
    setMsg(null);
    try {
      const { auth_url } = await connectChannelStats(channelId);
      window.open(auth_url, "_blank", "noopener");
      setMsg(t("Autorize na aba do Google (abra no Mac onde roda a API: o retorno vai para 127.0.0.1). Depois clique em “Atualizar”."));
    } catch (e) {
      const raw = String(e).replace(/^Error: \d+ /, "");
      let text = raw;
      try {
        text = JSON.parse(raw).detail ?? raw;
      } catch {
        /* plain text */
      }
      setMsg(text);
    }
  }

  async function decide(d: "approve" | "reject") {
    try {
      await decideTitlePattern(channelId, d);
      load();
    } catch (e) {
      setMsg(String(e));
    }
  }

  const n = (v?: number | null) => (v == null ? "—" : v.toLocaleString(locale, { maximumFractionDigits: 0 }));
  const m = stats?.monetization;
  const hours = m?.hours ?? stats?.watch_hours_365d ?? null;
  const subs = m?.subs ?? stats?.subscribers ?? null;
  const tp = stats?.title_pattern_suggestion;
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="h2 m-0 flex-1">{t("Dados")}</h2>
        {stats?.connected && <button className="btn btn-sm" disabled={busy} onClick={() => load(true)}>{busy ? t("Atualizando…") : t("Atualizar")}</button>}
      </div>
      {stats === undefined ? (
        <div className="card p-5 text-muted">{t("Carregando…")}</div>
      ) : (
        <div className="card flex flex-col gap-4 p-5">
          {!stats?.connected && (
            <div className="flex flex-wrap items-center gap-3">
              <span className="flex-[1_1_300px] text-sm text-body">{t("Conecte o YouTube Analytics (gratuito, por canal) para ver views, horas de exibição, inscritos, retenção e a meta de monetização.")}</span>
              {stats && <button className="btn btn-go btn-sm" onClick={connect}>{t("Conectar YouTube Analytics")}</button>}
            </div>
          )}
          {msg && <p className="m-0 text-sm text-warn-text">{msg}</p>}
          {stats?.connected && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(170px,1fr))]">
              <Tile label={t("Views (7 dias)")} value={n(stats.views_7d)} />
              <Tile label={t("Views (28 dias)")} value={n(stats.views_28d)} />
              <Tile label={t("Horas de exibição (365 dias)")} value={n(hours)}
                sub={m ? t("meta {n} h", { n: n(m.hours_target) }) : undefined}
                pct={m && hours != null ? Math.round((hours / m.hours_target) * 100) : undefined} />
              <Tile label={t("Inscritos")} value={n(subs)}
                sub={m ? t("meta {n}", { n: n(m.subs_target) }) : undefined}
                pct={m && subs != null ? Math.round((subs / m.subs_target) * 100) : undefined} />
            </div>
          )}
          {m?.deadline && <p className="m-0 text-sm text-body">{t("Prazo do Programa de Parceiros: {date}", { date: new Date(m.deadline + "T12:00:00").toLocaleDateString(locale) })} · {t("{h} h e {s} inscritos", { h: n(m.hours_target), s: n(m.subs_target) })}</p>}
          {stats?.connected && (stats.top_views || stats.top_retention) && (
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-body">
              {stats.top_views && <li>{t("Mais visto:")} <a href={stats.top_views.url} target="_blank" rel="noreferrer">{stats.top_views.title}</a> ({n(stats.top_views.views)} views)</li>}
              {stats.top_retention && <li>{t("Maior retenção:")} <a href={stats.top_retention.url} target="_blank" rel="noreferrer">{stats.top_retention.title}</a> ({Math.round(stats.top_retention.avg_view_pct)}%)</li>}
            </ul>
          )}
          {tp?.status === "pending" && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl bg-claude-soft px-4 py-3">
              <span className="flex-[1_1_260px] text-sm text-claude-text"><strong>{t("Padrão de título sugerido:")}</strong> {tp.pattern}</span>
              <button className="btn btn-go btn-sm" onClick={() => decide("approve")}>{t("Aprovar")}</button>
              <button className="btn btn-sm" onClick={() => decide("reject")}>{t("Recusar")}</button>
            </div>
          )}
          {!!stats?.suggestions?.length && (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-semibold">{t("Sugestões da semana")}</span>
              <ul className="m-0 flex flex-col gap-1 pl-5 text-sm text-body">
                {stats.suggestions.map((x, i) => (
                  <li key={i}>
                    {x.project_id ? <Link href={`/projetos/${x.project_id}`}>{x.title}</Link> : <strong>{x.title}</strong>}
                    {x.why && <span className="text-muted"> — {x.why}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(stats?.error || stats?.updated_at) && (
            <p className="m-0 text-[12px] text-muted">
              {stats.updated_at && t("Atualizado em {date}", { date: new Date(stats.updated_at).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" }) })}
              {stats.error && <span className="text-warn-text"> · {stats.error}</span>}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export default function ChannelQueuePage() {
  const { id } = useParams<{ id: string }>();
  const channelId = Number(id);
  const { channels, channelOf, avatarOf, projects, reload, reloadSetup, setToast } = useAppData();
  const { t } = useT();
  const channel = channelOf(channelId);
  const [week, setWeek] = useState<ChannelWeek | null>(null);
  const [editing, setEditing] = useState(false);
  const [view, setView] = useState<"canal" | "money">("canal");
  const loadWeek = useCallback(() => channelWeek(channelId).then(setWeek).catch(() => {}), [channelId]);
  useEffect(() => {
    loadWeek();
    const i = setInterval(loadWeek, 60_000);
    return () => clearInterval(i);
  }, [loadWeek]);
  const [a, setA] = useState<NicheAnalysis | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [also, setAlso] = useState<number[]>([]); // sibling channels of the group that also get the ideas

  const load = useCallback(() => api<NicheAnalysis | null>(`/channels/${channelId}/ideas`).then((r) => { setA(r); setLoaded(true); }).catch(() => setLoaded(true)), [channelId]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (a?.status !== "running") return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [a?.status, load]);

  async function generate() {
    setErr(null);
    try {
      setA(await api<NicheAnalysis>(`/channels/${channelId}/ideas`, { method: "POST" }));
    } catch (e) {
      setErr(String(e));
    }
  }

  async function produce(body: { indexes?: number[]; best?: number }) {
    setBusy(true);
    setErr(null);
    const ids = also.filter((id) => siblings.some((c) => c.id === id));
    try {
      const r = await api<{ created: number[]; skipped?: { index: number; channel_id: number; reason: string }[] }>(`/channels/${channelId}/ideas/produce`, {
        method: "POST", body: JSON.stringify({ ...body, ...(ids.length ? { channel_ids: [channelId, ...ids] } : {}) }),
      });
      await Promise.all([load(), reload()]);
      const made = r.created.length ? (r.created.length > 1 ? t("{n} vídeos na fila", { n: r.created.length }) : t("{n} vídeo na fila", { n: r.created.length })) : t("Nada novo para produzir");
      const skipped = r.skipped ?? [];
      setToast({ msg: skipped.length ? `${made}. ${t("Pulados ({n}):", { n: skipped.length })} ${skipped.map((x) => `${channelOf(x.channel_id)?.name ?? x.channel_id}: ${x.reason}`).join(" · ")}` : made });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  // "Também para…": the other active channels of the same group (same persona, other languages)
  const siblings = channel?.group ? channels.filter((c) => c.group === channel.group && c.id !== channel.id && (c.status ?? "active") === "active") : [];

  if (!channel) return <p className="text-muted">{t("Carregando canal…")}</p>;
  const niche = nicheOf(channel);
  const open = (a?.items ?? []).filter((i) => !i.project_id);
  const mine = projects.filter((p) => p.channel_id === channelId);
  const active = mine.filter((p) => groupOf(p.job) !== "published").sort((x, y) => (x.job?.queue_position ?? 0) - (y.job?.queue_position ?? 0) || y.id - x.id);
  const running = a?.status === "running";
  const pill = STATUS_PILL[channel.status ?? "active"] ?? STATUS_PILL.active;
  const producing = (channel.status ?? "active") === "active";

  return (
    <>
      <PageHeader
        title={`${LANG_FLAG[channel.language] ?? ""} ${channel.name}`.trim()}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span className={`pill ${pill.cls}`}>{t(pill.label)}</span>
            {`${channel.youtube_handle ? channel.youtube_handle + " · " : ""}${PLATFORM_LABEL[channel.platform] ?? channel.platform} · ${channel.language} · ${t("avatar")} ${avatarOf(channel.avatar_id)?.name ?? t("padrão")} · ${t(STYLE_LABEL[channel.default_style] ?? channel.default_style)}`}
          </span>
        }
      >
        <button className="btn" onClick={() => setEditing((e) => !e)} aria-expanded={editing}>{editing ? t("Fechar perfil") : t("Editar perfil")}</button>
        <Link href="/canais" className="btn">← {t("Canais")}</Link>
      </PageHeader>

      {editing && (
        <ProfileForm
          key={channel.id}
          channel={channel}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            reloadSetup();
            loadWeek();
            setToast({ msg: t("Perfil do canal salvo") });
          }}
        />
      )}

      <Tabs label={t("Seções do canal")} value={view} onChange={setView} tabs={[["canal", t("Canal")], ["money", t("Monetização")]]} />

      {view === "money" && <MoneyTab channelId={channelId} />}

      {view === "canal" && <>
      <section className="flex flex-col gap-3">
        <h2 className="h2 m-0">{t("Meta da semana")}{week && <span className="ml-2 font-sans text-[15px] font-medium text-muted">{t("fuso do público: {tz}", { tz: week.tz })}</span>}</h2>
        <div className="card flex flex-col gap-4 p-5">
          {week ? <WeekGoal goal={week.goal} /> : <span className="text-muted">{t("Carregando…")}</span>}
        </div>
      </section>

      {week && (
        <section className="flex flex-col gap-3">
          <h2 className="h2 m-0">{t("Calendário da semana")}</h2>
          {week.calendar.some((d) => d.slots.length) ? (
            <WeekCalendar week={week} />
          ) : (
            <div className="card p-5 text-body">{t("Sem horários de publicação no perfil. Use “Editar perfil” para definir dias e horas por formato.")}</div>
          )}
          <div className="card flex flex-col gap-3 p-5">
            <h3 className="m-0 text-base font-semibold">{t("Próximos horários livres")}</h3>
            <NextSlots next={week.next_slots} />
          </div>
        </section>
      )}

      <section className="flex flex-col gap-3">
        <h2 className="h2 m-0">{t("Fila deste canal")} <span className="font-sans text-[15px] font-medium text-muted">({active.length})</span></h2>
        <div className="card overflow-hidden">
          {active.map((p) => <QueueItem key={p.id} p={p} />)}
          {!active.length && <p className="m-0 px-5 py-4 text-muted">{t("Nenhum vídeo deste canal em produção.")}</p>}
        </div>
      </section>

      {(channel.default_style === "live" || "facecam" in (channel.prefs ?? {})) && <FacecamMarker channelId={channelId} />}

      <section className="card flex flex-wrap items-center gap-4 p-5">
        <Initial name={channel.name} size={48} />
        <div className="flex flex-[1_1_320px] flex-col gap-1">
          <span className="text-base font-semibold">{t("Nicho:")} {niche ?? t("não definido")}</span>
          <span className="text-sm text-muted">
            {niche
              ? t("O Claude pesquisa novidades e temas atemporais desse nicho e sugere vídeos para este canal. Cada um segue o mesmo pipeline: roteiro, aprovação, voz, visuais, vídeo e publicação.")
              : t("Preencha o nicho em “Editar perfil” para o Claude saber o que pesquisar.")}
          </span>
        </div>
        <button className="btn btn-go" onClick={generate} disabled={running || !producing} title={producing ? undefined : t("Canal pausado ou arquivado: não recebe vídeos novos.")}>{running ? t("Pesquisando o nicho…") : a ? t("Gerar novas ideias") : t("Gerar ideias do nicho")}</button>
      </section>
      {err && <p className="m-0 text-alert-text">{err}</p>}

      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="h2 m-0 flex-1">{t("Ideias do nicho")}</h2>
          {!!open.length && (
            <button className="btn btn-go btn-sm" disabled={busy} onClick={() => produce({ best: Math.min(3, open.length) })}>
              {t("Produzir as {n} melhores", { n: Math.min(3, open.length) })}
            </button>
          )}
        </div>
        {siblings.length > 0 && !!open.length && (
          <fieldset className="m-0 flex flex-wrap items-center gap-x-4 gap-y-1 border-0 p-0 text-sm">
            <legend className="mb-1 font-semibold">{t("Produzir também para…")}</legend>
            <label className="flex min-h-11 items-center gap-2">
              <input type="checkbox" className="h-4 w-4 accent-go" checked disabled />
              <span dir="auto">{LANG_FLAG[channel.language] ?? ""} {channel.name}</span>
              <span className="text-muted">{t("(este canal)")}</span>
            </label>
            {siblings.map((c) => (
              <label key={c.id} className="flex min-h-11 items-center gap-2">
                <input type="checkbox" className="h-4 w-4 accent-go" checked={also.includes(c.id)}
                  onChange={(e) => setAlso(e.target.checked ? [...also, c.id] : also.filter((x) => x !== c.id))} />
                <span dir="auto">{LANG_FLAG[c.language] ?? ""} {c.name}</span>
                <span className="text-muted">{t(LANG_LABEL[c.language] ?? c.language)}</span>
              </label>
            ))}
            <span className="basis-full text-[13px] text-muted">{t("Cada canal ganha um projeto próprio, adaptado ao seu público (não traduzido).")}</span>
          </fieldset>
        )}
        {running && <div className="card p-5 text-body">{t("O Claude está pesquisando o nicho. Leva alguns minutos; a lista aparece aqui sozinha.")}</div>}
        {a?.status === "failed" && <div className="card p-5 text-alert-text">{t("A pesquisa falhou:")} {a.error}</div>}
        {loaded && !a && <div className="card p-5 text-body">{t("Nenhuma ideia ainda. Clique em “Gerar ideias do nicho”.")}</div>}
        {a?.status === "done" && a.summary && <p className="m-0 text-sm text-muted">{a.summary}</p>}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[repeat(auto-fit,minmax(300px,1fr))]">
          {a?.status === "done" &&
            a.items.map((i) => (
              <article key={i.index} className="card flex flex-col gap-2.5 p-[18px]">
                <div className="flex items-start gap-2.5">
                  <ScoreBadge score={i.potential ?? 0} />
                  <h3 className="m-0 flex-1 text-base leading-[1.35] font-semibold">{i.topic}</h3>
                </div>
                <p className="m-0 text-sm leading-normal text-muted">{i.why}</p>
                {Object.entries(i.hooks ?? {}).slice(0, 1).map(([l, h]) => (
                  <p key={l} dir={l === "ar-AE" ? "rtl" : "ltr"} className="m-0 rounded-[10px] bg-bg px-3 py-2.5 text-sm leading-[1.45]">“{h}”</p>
                ))}
                {i.risks && <p className="m-0 text-[13px] text-warn-text">{t("Atenção:")} {i.risks}</p>}
                <span className="text-[13px] text-body">{t(STYLE_LABEL[i.style] ?? i.style)}</span>
                <div className="mt-auto">
                  {i.project_id ? (
                    <Link href={`/projetos/${i.project_id}`} className="pill bg-go-soft text-go-text no-underline">✓ {t("Na fila · ver projeto")}</Link>
                  ) : (
                    <div className="flex gap-2">
                      <Link href={`/fabrica/novo?channel=${channelId}&cidea=${i.index}`} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>
                    </div>
                  )}
                </div>
              </article>
            ))}
        </div>
      </section>

      <StatsSection channelId={channelId} />
      </>}
    </>
  );
}
