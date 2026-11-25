"use client";

import Link from "next/link";
import { useAppData } from "@/components/AppData";
import StageChips, { ProgressBar } from "@/components/StageChips";
import { api, deleteProject, LANG_FLAG, type Project } from "@/lib/api";
import { tr, useT } from "@/lib/i18n";
import { effectiveState, estimate, fmtAgo, fmtDuration, groupOf, isFailed, phaseOf, STAGES, useTick } from "@/lib/pipeline";

const STEP_TEXT: Record<string, string> = {
  queued: "Aguardando vaga",
  approve: "Roteiro pronto · aguardando sua aprovação",
  review: "Vídeo pronto · aguardando sua revisão",
  manual: "Hora de publicar · kit pronto",
  scheduled: "Agendado",
  published: "Publicado",
};

export function stepLine(p: Project): string {
  const ph = phaseOf(p.job);
  if (p.job?.paused && ph !== "failed") return tr(p.job.progress?.started ? "Pausa ao terminar a etapa atual" : "Pausado");
  if (ph === "failed") {
    const st = STAGES.find((s) => s.state === effectiveState(p.job));
    const at = st ? tr(st.label) : effectiveState(p.job);
    return p.job?.orphaned ? tr("Interrompido em {stage}", { stage: at }) : tr("Falhou em {stage}", { stage: at });
  }
  if (ph === "queued") {
    const w = p.job?.progress?.waiting;
    if (w === "claude_limit") return String(p.job?.progress?.label ?? tr("Limite do Claude: aguardando o reset"));
    if (w === "episode") return tr("Espera o roteiro do Ep. {n}", { n: p.job?.progress?.after_episode ?? "" });
    return tr(w === "gpu" ? "Roteiro aprovado · espera a GPU liberar" : w === "prep" ? "Na fila para visuais/montagem" : w === "claude" ? "Aguardando vaga no Claude" : "Na fila");
  }
  return tr(STEP_TEXT[ph] ?? STAGES.find((s) => s.state === p.job?.state)?.now ?? p.job?.state ?? "");
}

/** One project in the production queue: step, progress, stage chips and the action it needs. */
export default function QueueItem({ p }: { p: Project }) {
  useTick();
  const { durations, channelOf, reload, setToast } = useAppData();
  const channel = channelOf(p.channel_id);
  const { t } = useT();
  const job = p.job;
  const g = groupOf(job);
  const ph = phaseOf(job);
  const failed = isFailed(job);
  const est = estimate(job, durations);
  const cta = t(ph === "approve" ? "Ler e aprovar" : ph === "review" ? "Assistir e aprovar" : ph === "manual" ? "Abrir kit" : "Abrir");
  const ctaHref = ph === "manual" ? "/fabrica/publicar" : `/projetos/${p.id}`;

  async function retry() {
    if (!job) return;
    await api(`/jobs/${job.id}/${job.orphaned ? "resume" : "retry"}`, { method: "POST" }).catch((e) => alert(e));
    reload();
  }
  async function togglePause() {
    if (!job) return;
    await api(`/jobs/${job.id}/pause`, { method: "POST", body: JSON.stringify({ paused: !job.paused }) }).catch((e) => alert(e));
    setToast({ msg: job.paused ? t("“{title}” voltou para a fila", { title: p.title }) : t("“{title}” pausado", { title: p.title }) });
    reload();
  }
  async function remove() {
    if (await deleteProject(p)) {
      setToast({ msg: t("“{title}” saiu da fila", { title: p.title }) });
      reload();
    }
  }

  async function unpublish() {
    const ok = await api(`/projects/${p.id}/unpublish`, { method: "POST" }).then(() => true).catch((e) => { alert(e); return false; });
    if (ok) setToast({ msg: t("“{title}” voltou para Publicar", { title: p.title }) });
    reload();
  }

  let timing: string | null = null;
  if (est) timing = t("{pct}% · rodando há {elapsed} · faltam ~{left}", { pct: Math.round(est.fraction * 100), elapsed: fmtDuration(est.elapsedS), left: fmtDuration(est.remainingS) });
  else if (job?.paused) timing = null;
  else if (ph === "queued") timing = job?.queue_position ? t("Posição {n}", { n: job.queue_position }) + (job.eta_s != null ? t(" · começa em ~{d}", { d: fmtDuration(job.eta_s) }) : "") : t("Começa sozinho quando chegar a vez");
  else if (failed) timing = t("parou há {d}", { d: fmtAgo(job?.updated_at) });

  return (
    <div className="flex flex-col gap-3 border-b border-line-soft px-5 py-[18px] last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-[1_1_260px] flex-col gap-1">
          <Link href={`/projetos/${p.id}`} dir="auto" title={p.seo_title && p.seo_title !== p.title ? t("Tema: {topic}", { topic: p.title }) : undefined}
            className="text-base font-semibold text-ink no-underline hover:underline">{p.seo_title || p.title}</Link>
          <span className="text-sm text-muted">{stepLine(p)} · {channel ? <>{LANG_FLAG[channel.language] ?? ""} {channel.name}</> : t("sem canal")}</span>
        </div>
        {timing && (
          <div className="flex flex-[1_1_220px] flex-col gap-1.5">
            {(est || failed) && <ProgressBar fraction={est?.fraction ?? (job?.progress?.fraction ?? 0.5)} color={failed ? "bg-alert" : "bg-go"} />}
            <span className="text-[13px] text-muted">{timing}</span>
          </div>
        )}
        <div className="flex gap-2">
          {(g === "wait" || g === "run") && !failed && <button className="btn btn-sm" onClick={togglePause}>{job?.paused ? t("Continuar") : t("Pausar")}</button>}
          {g === "wait" && <button className="btn btn-sm" onClick={remove}>{t("Tirar da fila")}</button>}
          {ph === "published" && <button className="btn btn-sm" title={t("Volta para Hoje com o kit, para publicar de novo")} onClick={unpublish}>{t("Desfazer publicado")}</button>}
          <Link href={ctaHref} className={`btn btn-sm ${g === "you" && !failed ? "btn-ink" : ""}`}>{cta}</Link>
        </div>
      </div>
      {g !== "done" && ph !== "review" && ph !== "manual" && <StageChips job={job} />}
      {failed && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl bg-alert-soft px-3.5 py-3 text-[#7a0011]">
          <span className="flex-[1_1_260px] text-sm">
            <strong>{stepLine(p)}.</strong>{" "}
            {job?.orphaned ? t("Nenhum processo está trabalhando nesta etapa (o worker foi reiniciado ou caiu).") : job?.error}
          </span>
          <button className="btn btn-ink btn-sm" onClick={retry}>{job?.orphaned ? t("Retomar") : t("Tentar de novo")}</button>
        </div>
      )}
    </div>
  );
}
