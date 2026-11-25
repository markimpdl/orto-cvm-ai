"use client";

import { useEffect, useState } from "react";
import { api, STATE_LABEL, type Job } from "@/lib/api";
import { tr } from "@/lib/i18n";

/** Who acts in a step (Portuguese source labels: render through t()). */
export type Who = "você" | "Claude" | "automático";

/** The 8 steps of the project page stepper, each grouping job states. */
export const STEPS: { key: string; title: string; who: Who; states: string[] }[] = [
  { key: "idea", title: "Ideia", who: "você", states: ["trend_selected"] },
  { key: "script", title: "Roteiro", who: "Claude", states: ["scripting"] },
  { key: "approval", title: "Aprovar roteiro", who: "você", states: ["awaiting_approval"] },
  { key: "production", title: "Produção", who: "automático", states: ["voicing", "sourcing_visuals", "lipsyncing", "composing", "rendering", "validating"] },
  { key: "seo", title: "Títulos e tags", who: "Claude", states: ["seo"] },
  { key: "review", title: "Revisão final", who: "você", states: ["awaiting_publish_review"] },
  { key: "publish", title: "Publicar", who: "você", states: ["scheduled", "ready_for_manual_publish"] },
  { key: "done", title: "Publicado", who: "automático", states: ["published"] },
];

/** The 8 worker stages shown as chips in the queue: Roteiro → … → Validação → Títulos e tags. */
export const STAGES: { state: string; label: string; now: string; claude?: boolean }[] = [
  { state: "scripting", label: "Roteiro", now: "Claude escrevendo o roteiro", claude: true },
  { state: "voicing", label: "Narração", now: "Gerando narração" },
  { state: "sourcing_visuals", label: "Visuais", now: "Buscando visuais" },
  { state: "lipsyncing", label: "Avatar", now: "Lipsync do avatar" },
  { state: "composing", label: "Montagem", now: "Montando vídeo" },
  { state: "rendering", label: "Render", now: "Renderizando e checando" },
  { state: "validating", label: "Validação", now: "A IA está conferindo o vídeo" },
  { state: "seo", label: "Títulos e tags", now: "Claude gerando título, descrição e hashtags", claude: true },
];
export const PRODUCTION = STEPS[3].states;
const AFTER_STAGES = new Set(["awaiting_publish_review", "scheduled", "ready_for_manual_publish", "published"]);

/** The state a failed job failed in (or the job's own state). */
export function effectiveState(job: Job | null | undefined): string {
  if (!job) return "trend_selected";
  if (job.state !== "failed") return job.state;
  return [...job.history].reverse().find((h) => h.to === "failed")?.from ?? "trend_selected";
}

export function isFailed(job: Job | null | undefined): boolean {
  return job?.state === "failed" || !!job?.orphaned;
}

export type Phase = "queued" | "writing" | "approve" | "producing" | "seo" | "review" | "scheduled" | "manual" | "published" | "failed";

/** What the project is doing, as the UI groups it. */
export function phaseOf(job: Job | null | undefined): Phase {
  if (!job) return "queued";
  if (isFailed(job)) return "failed";
  const s = job.state;
  if (s === "awaiting_approval") return "approve";
  if (s === "awaiting_publish_review") return "review";
  if (s === "ready_for_manual_publish") return "manual";
  if (s === "scheduled") return "scheduled";
  if (s === "published") return "published";
  if (s === "trend_selected" || job.progress?.waiting || !job.progress?.started) return "queued";
  if (s === "scripting") return "writing";
  if (s === "seo") return "seo";
  return "producing";
}

/** Queue page groups. */
export type Group = "you" | "run" | "wait" | "done" | "published";
export function groupOf(job: Job | null | undefined): Group {
  const p = phaseOf(job);
  if (p === "approve" || p === "review" || p === "failed" || p === "manual") return "you";
  if (p === "writing" || p === "producing" || p === "seo") return "run";
  if (p === "queued") return "wait";
  if (p === "scheduled") return "done";
  return "published";
}

/** Index of the 8-step stepper for the job. */
export function stepIndex(job: Job | null | undefined): number {
  const s = effectiveState(job);
  return Math.max(0, STEPS.findIndex((st) => st.states.includes(s)));
}

/** Index of the current stage among the chips (STAGES.length = all done); `running` is false while waiting or at a gate. */
export function stageAt(job: Job | null | undefined): { at: number; running: boolean } {
  const s = effectiveState(job);
  if (AFTER_STAGES.has(s)) return { at: STAGES.length, running: false };
  if (s === "trend_selected") return { at: 0, running: false };
  if (s === "awaiting_approval") return { at: 1, running: false };
  const at = STAGES.findIndex((st) => st.state === s);
  const p = phaseOf(job);
  return { at: Math.max(0, at), running: p === "writing" || p === "producing" || p === "seo" };
}

/** Real seconds each stage took (last successful pass), from the job history. */
export function stageSeconds(job: Job | null | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  for (const h of job?.history ?? []) if (h.worked_s != null && h.to !== "failed") out[h.from] = h.worked_s;
  return out;
}

export type Durations = Record<string, number>;

/** Typical seconds per step (median of past jobs), loaded once. */
export function useDurations(): Durations {
  const [d, setD] = useState<Durations>({});
  useEffect(() => {
    api<Durations>("/jobs/meta/durations").then(setD).catch(() => {});
  }, []);
  return d;
}

/** Re-render every few seconds so elapsed/remaining times tick. */
export function useTick(ms = 3000) {
  const [, setT] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setT((x) => x + 1), ms);
    return () => clearInterval(t);
  }, [ms]);
}

export type Estimate = { fraction: number; remainingS: number; label?: string | null; elapsedS: number };

/**
 * Progress of the running step: the fraction the worker reports (frases, cenas…) when there is one,
 * else elapsed time against the step's typical duration. In production, the remaining time also counts
 * the production sub-steps still ahead. Null when nothing is running.
 */
export function estimate(job: Job | null | undefined, durations: Durations): Estimate | null {
  const started = job?.progress?.started;
  if (!job || job.orphaned || !started || !(job.state in durations)) return null;
  const elapsedS = Math.max(0, (Date.now() - new Date(started).getTime()) / 1000);
  const typical = durations[job.state];
  const reported = job.progress?.fraction ?? 0;
  const fraction = reported > 0.03 ? reported : Math.min(0.95, elapsedS / typical);
  let remainingS = reported > 0.03 ? (elapsedS / reported) * (1 - reported) : Math.max(0, typical - elapsedS);
  const i = PRODUCTION.indexOf(job.state);
  if (i >= 0) remainingS += PRODUCTION.slice(i + 1).reduce((t, st) => t + (durations[st] ?? 0), 0);
  return { fraction, remainingS, label: job.progress?.label, elapsedS };
}

export function fmtDuration(s: number): string {
  if (s < 45) return tr("menos de 1 min");
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return fmtDuration((Date.now() - d.getTime()) / 1000);
}

/** Local wall-clock time ("20:52") of an ISO timestamp with offset. */
export function fmtClock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Short status line for a project (queue mini list, trend rows). */
export function statusText(job: Job | null | undefined, durations: Durations): string {
  const p = phaseOf(job);
  const est = estimate(job, durations);
  const eta = job?.eta_s != null ? tr(" · começa em ~{d}", { d: fmtDuration(job.eta_s) }) : "";
  const left = est ? tr(" · faltam ~{d}", { d: fmtDuration(est.remainingS) }) : "";
  switch (p) {
    case "queued":
      return job?.queue_position ? tr("Na fila · posição {n}", { n: job.queue_position }) + eta : tr("Na fila");
    case "writing":
      return tr("Escrevendo roteiro…") + left;
    case "seo":
      return tr("Claude: títulos e tags") + left;
    case "producing":
      return `${tr(STATE_LABEL[job!.state] ?? job!.state)}${est ? ` · ${Math.round(est.fraction * 100)}%` : ""}${left}`;
    case "approve":
      return tr("Sua vez · aprovar roteiro");
    case "review":
      return tr("Sua vez · revisar vídeo");
    case "manual":
      return tr("Sua vez · publicar");
    case "failed":
      return job?.orphaned ? tr("Interrompido · retome") : tr("Falhou · tente de novo");
    case "scheduled":
      return tr("Agendado");
    default:
      return tr("Publicado");
  }
}
