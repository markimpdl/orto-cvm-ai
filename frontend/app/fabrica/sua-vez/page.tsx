"use client";

import Link from "next/link";
import { useAppData } from "@/components/AppData";
import { PageHeader } from "@/components/Bits";
import { CurationLine } from "@/components/CurationPanel";
import { FilterBar, optionsOf, projectFilters, projectMatches, useFilters } from "@/components/Filters";
import { LANG_LABEL } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { groupOf, phaseOf } from "@/lib/pipeline";
import { stepLine } from "@/components/QueueItem";

const KIND: Record<string, [string, string]> = {
  approve: ["Aprovar roteiro", "Ler e aprovar"],
  review: ["Vídeo pronto", "Assistir e aprovar"],
  manual: ["Publicar agora", "Abrir kit"],
  failed: ["Precisa de ajuda", "Ver erro"],
};

export default function InboxPage() {
  const { projects, cutReviews, channelOf, loaded } = useAppData();
  const { t } = useT();
  const order = ["approve", "review", "manual", "failed"];
  const [f, setF, clearF] = useFilters("sua-vez");
  const all = projects.filter((p) => groupOf(p.job) === "you").sort((a, b) => order.indexOf(phaseOf(a.job)) - order.indexOf(phaseOf(b.job)));
  const todo = all.filter((p) => projectMatches(p, f) && (!f.kind || phaseOf(p.job) === f.kind));
  const defs = [
    { key: "kind", label: t("Tipo"), options: optionsOf(all, (p) => phaseOf(p.job), (k) => t(KIND[k]?.[0] ?? k)).sort((a, b) => order.indexOf(a.value) - order.indexOf(b.value)) },
    ...projectFilters(all, channelOf, t),
  ];
  return (
    <>
      <PageHeader title={t("Sua vez")} subtitle={t("Só o que precisa de uma decisão sua. O resto da fila segue andando.")} />
      {all.length > 1 && <FilterBar defs={defs} f={f} set={setF} clear={clearF} shown={todo.length} total={all.length} />}
      {todo.length || cutReviews.length ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-3">
          {cutReviews.map((r) => (
            <article key={`cuts-${r.id}`} className="card flex flex-col gap-3 p-5">
              <div className="text-[13px] font-semibold tracking-[0.6px] text-alert-text uppercase">{t("Cortes para revisar")}</div>
              <h3 className="m-0 text-lg leading-[1.3] font-semibold">{r.title}</h3>
              <p className="m-0 text-sm text-muted">
                {`${channelOf(r.channel_id)?.name ?? t("sem canal")} · ${t("{n} propostos, {s} sugeridos", { n: r.counts.suggested + r.counts.proposed + r.counts.approved + r.counts.discarded, s: r.counts.suggested })}`}
              </p>
              <Link href={`/fabrica/cortes/${r.id}`} className="btn btn-ink mt-auto self-start">{t("Revisar cortes")}</Link>
            </article>
          ))}
          {todo.map((p) => {
            const ph = phaseOf(p.job);
            const [kind, cta] = KIND[ph];
            return (
              <article key={p.id} className="card flex flex-col gap-3 p-5">
                <div className="text-[13px] font-semibold tracking-[0.6px] text-alert-text uppercase">{t(kind)}</div>
                <h3 className="m-0 text-lg leading-[1.3] font-semibold">{p.title}</h3>
                <p className="m-0 text-sm text-muted">
                  {ph === "failed" ? stepLine(p) : `${channelOf(p.channel_id)?.name ?? t("sem canal")} · ${p.languages.map((l) => t(LANG_LABEL[l] ?? l)).join(", ")}`}
                </p>
                {ph === "review" && (() => {
                  const s = [...(p.job?.history ?? [])].reverse().find((h) => h.from === "validating" && h.curation_summary)?.curation_summary;
                  return s ? <CurationLine summary={s} /> : null;
                })()}
                <Link href={ph === "manual" ? "/fabrica/publicar" : `/projetos/${p.id}`} className="btn btn-ink mt-auto self-start">{t(cta)}</Link>
              </article>
            );
          })}
        </div>
      ) : (
        loaded && (all.length
          ? <div className="card p-8 text-base text-body">{t("Nada pendente com esses filtros.")} <button className="btn btn-sm ml-2" onClick={clearF}>{t("Limpar filtros")}</button></div>
          : <div className="card p-8 text-base text-body">{t("Nada pendente. A fila está rodando sozinha.")}</div>)
      )}
    </>
  );
}
