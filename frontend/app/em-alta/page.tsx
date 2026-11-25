"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAppData } from "@/components/AppData";
import { CheckBox, PageHeader, ScoreBadge } from "@/components/Bits";
import { api, STYLE_LABEL, type Analysis, type Idea, type Project, type Suggested, type Trend } from "@/lib/api";
import { FollowTab, GlobalResearchTab, HotLivesTab, HotVideosTab, useMore } from "@/components/EmAlta";
import { produceHref } from "@/lib/emAlta";
import QueueMini from "@/components/QueueMini";
import { useFeatures } from "@/lib/features";
import { useT } from "@/lib/i18n";
import { regionName } from "@/lib/regions";
import { groupOf, statusText } from "@/lib/pipeline";

/** Old machines without the wizard's Setting: the tabs they always had. */
const DEFAULT_TABS = ["AE", "BR", "US"];
/** Regions the lives/videos search knows (cvm/grow/discover.BROAD); any other region searches worldwide. */
const DISCOVER = ["BR", "US", "AE"];
type BatchItem = { trend_id: number; analysis_id?: number; idea_index?: number };

function useAnalysis(region: string) {
  const [a, setA] = useState<Analysis | null>(null);
  const load = useCallback(() => {
    api<Analysis[]>(`/trends/analyses?limit=1${region ? `&region=${region}` : ""}`).then((r) => setA(r[0] ?? null)).catch(() => {});
  }, [region]);
  useEffect(load, [load]);
  useEffect(() => {
    if (a?.status !== "running") return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [a?.status, load]);
  return { a, setA, load };
}

function Destination({ s }: { s: Suggested }) {
  const { channelOf, avatarOf } = useAppData();
  const { t } = useT();
  return (
    <>
      {t("vai para")} <strong>{channelOf(s.channel_id)?.name ?? t("sem canal")}</strong> · {t("avatar")} <strong>{avatarOf(s.avatar_id)?.name ?? t("sem avatar")}</strong> · {t(STYLE_LABEL[s.style] ?? s.style)}
    </>
  );
}

function QueuedPill({ project }: { project: Project | undefined }) {
  const { durations } = useAppData();
  const { t } = useT();
  if (!project) return <span className="pill bg-go-soft text-go-text">✓ {t("Na fila")}</span>;
  const you = groupOf(project.job) === "you";
  return (
    <Link href={`/projetos/${project.id}`} className={`pill no-underline ${you ? "bg-alert-soft text-alert-text hover:text-alert-text" : "bg-go-soft text-go-text hover:text-go-text"}`}>
      ✓ {statusText(project.job, durations)}
    </Link>
  );
}

function IdeaCard({ idea, analysisId, selected, toggle, project }: {
  idea: Idea; analysisId: number; selected: boolean; toggle: () => void; project?: Project;
}) {
  const queued = !!idea.project_id;
  const { t } = useT();
  return (
    <article className={`card flex flex-col gap-2.5 p-[18px] ${selected ? "bg-go-tint" : ""}`}>
      <div className="flex items-start gap-2.5">
        <ScoreBadge score={idea.potential ?? 0} />
        <h3 className="m-0 flex-1 text-base leading-[1.35] font-semibold">{idea.topic}</h3>
        {!queued && <CheckBox checked={selected} onChange={toggle} label={t("Selecionar {name}", { name: idea.topic })} />}
      </div>
      <p className="m-0 text-sm leading-normal text-muted">{idea.why}</p>
      {!!Object.keys(idea.hooks ?? {}).length && (
        <div className="flex flex-col gap-1.5 rounded-[10px] bg-bg px-3 py-2.5">
          <span className="text-xs font-semibold tracking-[0.5px] text-muted uppercase">{t("Ganchos")}</span>
          {Object.entries(idea.hooks).map(([l, h]) => (
            <p key={l} dir={l === "ar-AE" ? "rtl" : "ltr"} className="m-0 text-sm leading-[1.45]">
              <span className="text-xs text-muted">{l}</span> “{h}”
            </p>
          ))}
        </div>
      )}
      {idea.risks && <p className="m-0 text-[13px] leading-[1.45] text-warn-text">{t("Atenção:")} {idea.risks}</p>}
      <span className="text-[13px] text-body"><Destination s={idea.suggested} /></span>
      <div className="mt-auto flex flex-wrap gap-2">
        {queued ? (
          <QueuedPill project={project} />
        ) : (
          idea.trend_id ? (
            <Link href={`/fabrica/novo?trend=${idea.trend_id}&idea=${analysisId}:${idea.index}`} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>
          ) : (
            <button className="btn btn-go btn-sm" disabled>{t("Produzir")}</button>
          )
        )}
      </div>
    </article>
  );
}

function TrendRow({ trend, selected, toggle, project, regions }: { trend: Trend; selected: boolean; toggle: () => void; project?: Project; regions: string[] }) {
  const { t } = useT();
  const queued = !!trend.project_id;
  const why = trend.meta.news_title ?? (trend.meta.hours_trending ? t("Em alta há {h} h", { h: trend.meta.hours_trending }) : null);
  const sources = [trend.source, ...(trend.meta.related ?? []).map((r) => r.split(":")[0])].filter((v, i, a) => a.indexOf(v) === i).slice(0, 3).join(" · ");
  return (
    <div className={`flex flex-wrap items-start gap-x-4 gap-y-3 border-b border-line-soft px-[18px] py-4 last:border-b-0 ${selected ? "bg-go-tint" : ""}`}>
      {queued ? (
        <span aria-hidden="true" className="inline-flex h-11 w-7 items-center justify-center font-bold text-go">✓</span>
      ) : (
        <CheckBox checked={selected} onChange={toggle} label={t("Selecionar {name}", { name: trend.title })} />
      )}
      <ScoreBadge score={trend.score} size="lg" />
      <div className="flex min-w-0 flex-[1_1_260px] flex-col gap-1">
        <span className="text-base leading-[1.3] font-semibold">{trend.title}</span>
        {why && <span className="text-sm leading-[1.45] text-muted">{why}</span>}
        <span className="text-[13px] text-body">
          {t(regionName(trend.region, regions))} · {sources} · {trend.suggested && <Destination s={trend.suggested} />}
        </span>
      </div>
      <div className="flex flex-none items-center gap-2">
        {queued ? (
          <QueuedPill project={project} />
        ) : (
          <Link href={trend.source === "follow" ? produceHref({ trend: trend.id, topic: trend.title, link: trend.url }) : `/fabrica/novo?trend=${trend.id}`} className="btn btn-go btn-sm no-underline">{t("Produzir")}</Link>
        )}
      </div>
    </div>
  );
}

export default function TrendsPage() {
  const router = useRouter();
  const { projects, reload, setToast } = useAppData();
  const { t, locale } = useT();
  const features = useFeatures();
  const [region, setRegion] = useState("");
  const [tab, setTab] = useState<"ideas" | "raw" | "lives" | "videos" | "research" | "follow">("ideas");
  const [trends, setTrends] = useState<Trend[]>([]);
  const [shownTrends, moreTrends] = useMore(trends, 30);
  const [sel, setSel] = useState<Record<string, { item: BatchItem; title: string }>>({});
  const [refreshing, setRefreshing] = useState(false);
  const { a, setA, load: loadAnalysis } = useAnalysis(region);

  const loadTrends = useCallback(() => {
    api<Trend[]>(`/trends?limit=150${region ? `&region=${region}` : ""}`).then(setTrends).catch(() => {});
  }, [region]);
  useEffect(loadTrends, [loadTrends]);
  // keep "already in the queue" marks fresh as projects change
  useEffect(() => {
    loadTrends();
    loadAnalysis();
  }, [projects.length]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (a?.status !== "running") setRefreshing(false);
  }, [a?.status]);
  // "Sugestões da IA" off in the wizard: no ideas tab, Em alta opens on the trends
  useEffect(() => {
    if (!features.ai_ideas && tab === "ideas") setTab("raw");
  }, [features.ai_ideas, tab]);

  const byId = useMemo(() => Object.fromEntries(projects.map((p) => [p.id, p])), [projects]);
  const ideas = (a?.items ?? []).filter((i) => !region || i.region === region);
  // one tab per region the machine collects (several at once since 2026-11-25), plus "Todas"
  const chosen = features.regions.length ? features.regions : DEFAULT_TABS;
  const regionTabs: [string, string][] = [["", "Todas"], ...chosen.map((c): [string, string] => [c, regionName(c, chosen)])];
  const discoverRegion = DISCOVER.includes(region) ? region : "";
  const todo = projects.filter((p) => groupOf(p.job) === "you").length;

  async function produce(items: BatchItem[]) {
    if (!items.length) return;
    const r = await api<{ created: Project[]; skipped: number[] }>("/projects/batch", { method: "POST", body: JSON.stringify({ items }) });
    setSel({});
    await reload();
    loadTrends();
    loadAnalysis();
    const n = r.created.length;
    setToast({ msg: n === 1 ? t("“{title}” entrou na fila", { title: r.created[0].title }) : n ? t("{n} temas entraram na fila", { n }) : t("Esses temas já estavam na fila") });
  }

  async function refresh() {
    setRefreshing(true);
    const r = await api<{ analysis: Analysis | null }>("/trends/refresh", { method: "POST", body: JSON.stringify({ analyze: features.ai_ideas, region: region || null }) });
    if (r.analysis) setA({ ...r.analysis, items: [] });
    setTimeout(loadTrends, 15000);
  }

  async function reanalyze() {
    setA(await api<Analysis>("/trends/analyze", { method: "POST", body: JSON.stringify({ region: region || null }) }));
  }

  const toggle = (key: string, item: BatchItem, title: string) =>
    setSel((s) => {
      const next = { ...s };
      if (next[key]) delete next[key];
      else next[key] = { item, title };
      return next;
    });
  const selCount = Object.keys(sel).length;
  const running = a?.status === "running";

  return (
    <>
      <PageHeader title={t("Em alta agora")} subtitle={<>{t("Tendências coletadas e analisadas pelo Claude.")} <strong>{t("Produzir")}</strong> {t("abre o tema pronto para você revisar e começar. Para vários de uma vez, marque e use Produzir selecionados.")}</>}>
        <button className="btn" onClick={refresh} disabled={refreshing || running} title={t("Coleta tendências novas e pede ideias novas ao Claude")}>
          {refreshing || running ? t("Coletando e analisando… (1–5 min)") : t("Atualizar agora")}
        </button>
        {todo > 0 && (
          <button className="btn btn-ink" onClick={() => router.push("/fabrica/sua-vez")}>
            <span className="h-2 w-2 rounded-full bg-alert-dot" />
            {todo === 1 ? t("{n} item espera você", { n: todo }) : t("{n} itens esperam você", { n: todo })}
          </button>
        )}
      </PageHeader>

      <div className="flex flex-wrap items-start gap-6">
        <section aria-labelledby="h-trends" className="flex min-w-0 flex-[999_1_520px] flex-col gap-3">
          <h2 id="h-trends" className="sr-only">{t("Temas")}</h2>
          <div className="flex flex-wrap items-center gap-2">
            <div role="tablist" aria-label={t("Tipo de lista")} className="mr-auto flex flex-wrap gap-1 rounded-xl bg-[#e9eae6] p-1">
              {([["ideas", t("Sugestões do Claude"), ideas.length], ["raw", t("Todas as tendências"), trends.length], ["lives", t("Lives"), null], ["videos", t("Vídeos"), null], ["research", t("Pesquisa vidIQ"), null], ["follow", t("Seguindo"), null]] as const)
                .filter(([k]) => (k !== "ideas" || features.ai_ideas) && (k !== "lives" || features.lives.length > 0) && (k !== "research" || features.vidiq) && (k !== "follow" || features.follow))
                .map(([k, label, n]) => (
                <button
                  key={k}
                  role="tab"
                  aria-selected={tab === k}
                  onClick={() => setTab(k)}
                  className={`pick inline-flex min-h-10 items-center gap-2 rounded-[9px] border-0 px-3.5 text-sm font-semibold ${tab === k ? "bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.08)]" : "bg-transparent text-body"}`}
                >
                  {label}
                  {n != null && <span className={`h-5 min-w-5 rounded-full px-1.5 text-center text-xs leading-5 ${tab === k ? "bg-ink text-white" : "bg-[#d9dbdf] text-ink"}`}>{n}</span>}
                </button>
              ))}
            </div>
            {(tab === "research" || tab === "follow") ? null : regionTabs.map(([k, label]) => (
              <button
                key={k}
                aria-pressed={region === k}
                onClick={() => setRegion(k)}
                className={`pick min-h-9 rounded-full border px-3.5 text-sm font-semibold ${region === k ? "border-ink bg-ink text-white" : "border-[#d9dbdf] bg-surface text-ink"}`}
              >
                {t(label)}
              </button>
            ))}
          </div>

          {selCount > 0 && (tab === "ideas" || tab === "raw") && (
            <div className="sticky top-2 z-10 flex flex-wrap items-center gap-3 rounded-xl bg-ink px-4 py-3 text-white">
              <span className="flex-[1_1_200px] text-[15px] font-semibold">{selCount === 1 ? t("{n} tema selecionado", { n: selCount }) : t("{n} temas selecionados", { n: selCount })}</span>
              <button className="btn btn-sm border-muted bg-transparent text-white hover:bg-ink-hover hover:text-white" onClick={() => setSel({})}>{t("Limpar")}</button>
              <button className="btn btn-go btn-sm" onClick={() => produce(Object.values(sel).map((s) => s.item))}>{t("Produzir selecionados")}</button>
            </div>
          )}

          {tab === "research" ? (
            <GlobalResearchTab />
          ) : tab === "lives" ? (
            <HotLivesTab region={discoverRegion} platforms={features.lives} />
          ) : tab === "videos" ? (
            <HotVideosTab region={discoverRegion} />
          ) : tab === "follow" ? (
            <FollowTab />
          ) : tab === "ideas" ? (
            <>
              <div className="card flex flex-wrap items-start gap-x-4 gap-y-3 px-[18px] py-4">
                <div className="flex flex-[1_1_320px] flex-col gap-1.5">
                  <span className="flex items-center gap-2 text-sm font-semibold">
                    <span className="tag-claude px-2 py-0.5 text-xs">Claude</span>
                    {t("Resumo da análise")}{a && !running ? ` · ${new Date(a.created_at.endsWith("Z") || a.created_at.includes("+") ? a.created_at : a.created_at + "Z").toLocaleString(locale, { dateStyle: "short", timeStyle: "short" })}` : ""}
                  </span>
                  <p className="m-0 text-sm leading-[1.55] text-body">
                    {running
                      ? t("O Claude está agrupando os assuntos, pesquisando os mais fortes e sugerindo ângulo, gancho e canal.")
                      : a?.status === "failed"
                        ? t("A última análise falhou: {error}", { error: a.error ?? "" })
                        : a?.summary ?? t("Nenhuma análise ainda. Peça ao Claude ideias a partir das tendências coletadas.")}
                  </p>
                </div>
                <button className="btn btn-sm" onClick={reanalyze} disabled={running}>{running ? t("Analisando… (1–5 min)") : a ? t("Ver novas sugestões") : t("Analisar com Claude")}</button>
              </div>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-3">
                {a && ideas.map((idea) => {
                  const key = `idea:${a.id}:${idea.index}`;
                  const item = { trend_id: idea.trend_id!, analysis_id: a.id, idea_index: idea.index };
                  return (
                    <IdeaCard
                      key={key}
                      idea={idea}
                      analysisId={a.id}
                      selected={!!sel[key]}
                      toggle={() => toggle(key, item, idea.topic)}
                      project={idea.project_id ? byId[idea.project_id] : undefined}
                    />
                  );
                })}
              </div>
              {a && !running && !ideas.length && <p className="text-muted">{t("Sem sugestões para esta região.")}</p>}
            </>
          ) : (
            <div className="card overflow-hidden">
              {shownTrends.map((tr) => (
                <TrendRow
                  key={tr.id}
                  trend={tr}
                  selected={!!sel[`trend:${tr.id}`]}
                  toggle={() => toggle(`trend:${tr.id}`, { trend_id: tr.id }, tr.title)}
                  project={tr.project_id ? byId[tr.project_id] : undefined}
                  regions={chosen}
                />
              ))}
              {!trends.length && <p className="m-0 p-4 text-muted">{t("Nenhuma tendência coletada ainda. Clique em Atualizar agora.")}</p>}
              {moreTrends && <div className="flex justify-center p-3">{moreTrends}</div>}
            </div>
          )}
        </section>
        <QueueMini />
      </div>
    </>
  );
}

