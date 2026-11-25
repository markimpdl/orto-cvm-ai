"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { PageHeader } from "@/components/Bits";
import { FilterBar, projectFilters, projectMatches, useFilters } from "@/components/Filters";
import QueueItem from "@/components/QueueItem";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { groupOf, type Group } from "@/lib/pipeline";

const GROUPS: { k: Group; title: string; dot: string }[] = [
  { k: "run", title: "Processando agora", dot: "bg-ink" },
  { k: "wait", title: "Na fila", dot: "bg-field" },
  { k: "done", title: "Agendados", dot: "bg-go" },
];

type LinkJob = { key: string; title: string; project_ids: number[]; status: "held" | "working" | "ok" | "error"; error: string | null; at: string | null; n_links: number };

/** Videos whose links are still downloading (/fabrica/novo with links): the project has no job yet, so it would be
 *  missing from every group until the downloads end. Failed ones stay a day so the error is seen. */
function LinkDownloads() {
  const { t } = useT();
  const { reload } = useAppData();
  const [items, setItems] = useState<LinkJob[]>([]);
  useEffect(() => {
    let was = 0;
    const load = () => api<LinkJob[]>("/projects/links").then((all) => {
      const day = Date.now() - 24 * 3600_000;
      const open = all.filter((e) => e.status === "held" || e.status === "working"
        || (e.status === "error" && e.at && new Date(e.at).getTime() > day));
      if (was && open.filter((e) => e.status !== "error").length < was) reload(); // a download finished: its job exists now
      was = open.filter((e) => e.status !== "error").length;
      setItems(open);
    }).catch(() => {});
    load();
    const i = setInterval(load, 10_000);
    return () => clearInterval(i);
  }, [reload]);
  if (!items.length) return null;
  return (
    <section className="flex flex-col gap-3">
      <h2 className="h2 m-0 flex items-center gap-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-field" />
        {t("Baixando material")} <span className="font-sans text-[15px] font-medium text-muted">({items.length})</span>
      </h2>
      <div className="card overflow-hidden">
        {items.map((e) => (
          <div key={e.key} className="flex flex-col gap-0.5 border-b border-line-soft px-5 py-3.5 last:border-b-0">
            <span className="text-[15px] font-semibold">{e.title}</span>
            {e.status === "error"
              ? <span className="text-[13px] text-alert-text">{t("O download falhou e o vídeo não foi criado:")} {e.error}</span>
              : <span className="text-[13px] text-muted">{e.n_links === 1 ? t("Baixando 1 link. Entra na fila sozinho quando terminar.") : t("Baixando {n} links. Entra na fila sozinho quando terminarem.", { n: e.n_links })}</span>}
          </div>
        ))}
      </div>
    </section>
  );
}

export default function QueuePage() {
  const { projects, loaded, reload, setToast, channelOf } = useAppData();
  const [f, setF, clearF] = useFilters("fila");
  const inProduction = projects.filter((p) => (groupOf(p.job) === "run" || groupOf(p.job) === "wait") && p.job?.state !== "failed");
  const anyActive = inProduction.some((p) => !p.job?.paused);
  const anyPaused = inProduction.some((p) => p.job?.paused);
  async function setAll(paused: boolean) {
    const r = await api<{ changed: number[] }>("/jobs/pause-all", { method: "POST", body: JSON.stringify({ paused }) }).catch((e) => { alert(e); return null; });
    if (r) setToast({ msg: paused ? t("{n} vídeos pausados", { n: r.changed.length }) : t("{n} vídeos voltaram para a fila", { n: r.changed.length }) });
    reload();
  }
  const { t } = useT();
  // a project without a job is still downloading its links (LinkDownloads), not published
  const queued = projects.filter((p) => p.job && groupOf(p.job) !== "you");
  // waiting on the user: counted over every project (the list below leaves them to Sua vez)
  const youCount = projects.filter((p) => p.job && groupOf(p.job) === "you").length;
  const pass = (p: (typeof projects)[number]) => projectMatches(p, f)
    && (!f.state || (f.state === "paused" ? !!p.job?.paused : !p.job?.paused));
  const shown = queued.filter(pass);
  const defs = [
    ...projectFilters(queued, channelOf, t),
    { key: "state", label: t("Situação"), options: [{ value: "active", label: t("Andando") }, { value: "paused", label: t("Pausados") }] },
  ];
  const by = (g: Group) =>
    shown
      .filter((p) => groupOf(p.job) === g)
      .sort((a, b) => (a.job?.queue_position ?? 0) - (b.job?.queue_position ?? 0) || b.id - a.id);
  const n = (g: Group) => by(g).length;
  const published = by("published");

  return (
    <>
      <PageHeader title={t("Produção")} subtitle={t("{run} processando · {wait} na fila", { run: n("run"), wait: n("wait") })}>
        {youCount > 0 && <Link href="/fabrica/sua-vez" className="btn btn-ink no-underline hover:text-white"><span className="h-2 w-2 rounded-full bg-alert-dot" />{youCount === 1 ? t("{n} precisa de você", { n: youCount }) : t("{n} precisam de você", { n: youCount })} →</Link>}
        {anyActive && <button className="btn" onClick={() => setAll(true)}>{t("Pausar todos")}</button>}
        {anyPaused && <button className="btn" onClick={() => setAll(false)}>{t("Continuar todos")}</button>}
        <Link href="/fabrica/novo" className="btn btn-go">+ {t("Novo vídeo")}</Link>
      </PageHeader>
      {queued.length > 1 && <FilterBar defs={defs} f={f} set={setF} clear={clearF} shown={shown.length} total={queued.length} />}
      <LinkDownloads />
      {GROUPS.filter((g) => n(g.k)).map((g) => (
        <section key={g.k} className="flex flex-col gap-3">
          <h2 className="h2 m-0 flex items-center gap-2.5">
            <span className={`h-2.5 w-2.5 rounded-full ${g.dot}`} />
            {t(g.title)} <span className="font-sans text-[15px] font-medium text-muted">({n(g.k)})</span>
          </h2>
          <div className="card overflow-hidden">
            {by(g.k).map((p) => <QueueItem key={p.id} p={p} />)}
          </div>
        </section>
      ))}
      {loaded && !!queued.length && !shown.length && (
        <div className="card p-8 text-base text-body">{t("Nenhum vídeo com esses filtros.")} <button className="btn btn-sm ml-2" onClick={clearF}>{t("Limpar filtros")}</button></div>
      )}
      {loaded && !projects.some((p) => !["published", "you"].includes(groupOf(p.job))) && (
        <div className="card p-8 text-base text-body">{t("A fila está vazia. Escolha temas em")} <Link href="/em-alta">{t("Em alta")}</Link> {t("e clique em Produzir.")}</div>
      )}
      {!!published.length && (
        <details className="card px-5 py-4">
          <summary className="cursor-pointer text-[15px] font-semibold">{t("Publicados ({n})", { n: published.length })}</summary>
          <ul className="mt-3 mb-0 flex list-none flex-col gap-2 p-0">
            {published.map((p) => <li key={p.id}><Link href={`/projetos/${p.id}`} dir="auto">{p.seo_title || p.title}</Link></li>)}
          </ul>
        </details>
      )}
    </>
  );
}
