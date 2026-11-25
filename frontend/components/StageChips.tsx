"use client";

import type { Job } from "@/lib/api";
import { useT, type T } from "@/lib/i18n";
import { fmtDuration, isFailed, stageAt, stageSeconds, STAGES } from "@/lib/pipeline";

type Mark = { mark: string; detail: string; cls: string };

function marks(job: Job | null | undefined, t: T): Mark[] {
  const { at, running } = stageAt(job);
  const failed = isFailed(job);
  const took = stageSeconds(job);
  return STAGES.map((st, i) => {
    if (i < at) return { mark: "✓", detail: took[st.state] != null ? t("pronto · {d}", { d: fmtDuration(took[st.state]) }) : t("pronto"), cls: "bg-go-soft text-go-text" };
    if (i === at && failed) return { mark: "✕", detail: job?.orphaned ? t("interrompido") : t("falhou"), cls: "bg-alert-soft text-alert-text" };
    if (i === at && running) return { mark: "▶", detail: job?.progress?.label || t("rodando"), cls: "bg-ink text-white" };
    return { mark: "○", detail: i === at && job?.progress?.waiting ? t("na fila") : t("a seguir"), cls: "bg-bg text-muted" };
  });
}

/** The 7 worker stages: compact pills (queue) or detail cards (project page). */
export default function StageChips({ job, variant = "chips" }: { job: Job | null | undefined; variant?: "chips" | "cards" }) {
  const { t } = useT();
  const ms = marks(job, t);
  if (variant === "cards") {
    return (
      <ol aria-label={t("Etapas da produção")} className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2 p-0">
        {STAGES.map((st, i) => (
          <li key={st.state} className={`flex flex-col gap-1 rounded-xl px-3.5 py-3 ${ms[i].cls}`}>
            <span className="flex flex-wrap items-center gap-1.5 text-[15px] font-semibold">
              <span aria-hidden="true">{ms[i].mark}</span> {t(st.label)}
              {st.claude && <span className="tag-claude">Claude</span>}
            </span>
            <span className="text-[13px] opacity-85">{ms[i].detail}</span>
          </li>
        ))}
      </ol>
    );
  }
  return (
    <ol aria-label={t("Etapas")} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
      {STAGES.map((st, i) => (
        <li key={st.state} className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[13px] ${ms[i].cls}`}>
          <span aria-hidden="true">{ms[i].mark}</span>
          {t(st.label)}
          {st.claude && <span className="tag-claude">Claude</span>}
          {ms[i].mark === "▶" && job?.progress?.label && <span className="opacity-85">· {job.progress.label}</span>}
          <span className="sr-only">({ms[i].detail})</span>
        </li>
      ))}
    </ol>
  );
}

/** Thin 7-segment progress bar (queue mini list). */
export function SegmentBar({ job }: { job: Job | null | undefined }) {
  const { at, running } = stageAt(job);
  const failed = isFailed(job);
  return (
    <span className="flex w-full gap-[3px]" aria-hidden="true">
      {STAGES.map((st, i) => (
        <span
          key={st.state}
          className={`h-[5px] flex-1 rounded-[3px] ${failed && i === at ? "bg-alert" : i < at ? "bg-go" : i === at && running ? "bg-ink" : "bg-[#e1e2e5]"}`}
        />
      ))}
    </span>
  );
}

export function ProgressBar({ fraction, color = "bg-go", height = "h-2" }: { fraction: number; color?: string; height?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
  return (
    <div className={`${height} overflow-hidden rounded bg-track`} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className={`h-full rounded ${color} transition-all`} style={{ width: `${pct}%` }} />
    </div>
  );
}
