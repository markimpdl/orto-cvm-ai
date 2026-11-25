"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { getPrep, type PrepItem, type PrepProgress } from "@/lib/api";
import type { T } from "@/lib/i18n";
import { useT } from "@/lib/i18n";

/** The steps before the cut editor (backend cvm/lives/progress.py): download, light copy, transcript, signals, the AI
 *  reading the transcript block by block, then "Pronto para revisar". */

/** Seconds of audio as the backend writes them in its labels: "1h12", "12 min", "45 s". */
export function hms(t: number): string {
  const s = Math.max(0, Math.floor(t));
  if (s >= 3600) return `${Math.floor(s / 3600)}h${String(Math.floor(s / 60) % 60).padStart(2, "0")}`;
  if (s >= 60) return `${Math.floor(s / 60)} min`;
  return `${s} s`;
}

/** The step's text in the UI language, built from its numbers (the backend label is pt-BR). */
export function prepLabel(p: PrepProgress, t: T): string {
  const pct = p.pct == null ? null : Math.round(p.pct);
  const has = p.done != null && p.total != null && p.total > 0;
  switch (p.step) {
    case "queued":
      return t("Esperando outro vídeo terminar de baixar");
    case "download":
      if (pct != null && pct > 0) return t("Baixando {pct}%", { pct });
      return p.label.startsWith("Lendo") ? t("Lendo o link") : t("Baixando");
    case "proxy":
      if (pct != null && pct > 0) return t("Versão leve {pct}%", { pct });
      if (p.done) return t("Versão leve ({time} prontos)", { time: hms(p.done) });
      return t("Versão leve");
    case "transcript":
      if (p.waiting) return t("Transcrição esperando a vez da GPU");
      if (has) return t("Transcrevendo {done} de {total}", { done: hms(p.done!), total: hms(p.total!) });
      return p.label.startsWith("Transcrevendo:") ? t("Transcrevendo: separando o áudio") : t("Transcrevendo");
    case "ai":
      if (has && p.label.startsWith("IA analisando")) return t("IA analisando bloco {n} de {total}", { n: p.done! + 1, total: p.total! });
      return t(p.label);
    case "ready":
      return t("Pronto para revisar");
    default:
      return t(p.label);
  }
}

function elapsed(since: string | null | undefined, t: T): string | null {
  if (!since) return null;
  const s = (Date.now() - new Date(since).getTime()) / 1000;
  if (!Number.isFinite(s) || s < 60) return null;
  return t("{time} nesta etapa", { time: s >= 3600 ? hms(s) : `${Math.floor(s / 60)} min` });
}

/** Label + bar (a bar only when the step knows its percentage). */
export function PrepBar({ p }: { p: PrepProgress }) {
  const { t } = useT();
  const label = prepLabel(p, t);
  const pct = p.pct == null ? null : Math.max(0, Math.min(100, p.pct));
  const ago = p.step === "ready" ? null : elapsed(p.since, t);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
        <span className={`font-semibold ${p.waiting || p.step === "queued" ? "text-warn-text" : p.step === "ready" ? "text-go-text" : "text-ink"}`}>{label}</span>
        {ago && <span className="text-[13px] text-muted">{ago}</span>}
      </div>
      {pct != null && (
        <div className="h-2 overflow-hidden rounded-full bg-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
          <div className={`h-full rounded-full ${p.step === "ready" ? "bg-go" : "bg-ink"} transition-[width] duration-500`} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

/** GET /cuts/prep, polled while the page is open (faster while something runs). */
export function usePrep(): PrepItem[] {
  const [items, setItems] = useState<PrepItem[]>([]);
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = () => getPrep().then((r) => {
      if (stop) return;
      setItems(r.items);
      timer = setTimeout(load, r.items.some((i) => i.status === "working" || i.status === "analysing") ? 4000 : 15000);
    }).catch(() => { if (!stop) timer = setTimeout(load, 15000); });
    load();
    return () => { stop = true; clearTimeout(timer); };
  }, []);
  return items;
}

/** One line per download/analysis: "Analisando cortes: <title>" with its step; a link to the editor when ready. */
export function PrepList({ items, showChannel = true }: { items: PrepItem[]; showChannel?: boolean }) {
  const { t } = useT();
  const { channelOf } = useAppData();
  return (
    <ul className="m-0 flex list-none flex-col p-0">
      {items.map((it) => (
        <li key={it.key} className="flex flex-col gap-2 border-b border-line-soft px-5 py-3.5 last:border-b-0">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex-[1_1_260px] text-[15px] font-semibold" dir="auto">
              <span className="font-medium text-muted">{it.status === "review" ? t("Cortes prontos:") : t("Analisando cortes:")}</span> {it.title}
            </span>
            {showChannel && <span className="text-[13px] text-muted">{channelOf(it.channel_id ?? undefined)?.name ?? ""}</span>}
            {it.status === "review" && it.review_id != null && (
              <Link className="btn btn-sm btn-ink" href={`/fabrica/cortes/${it.review_id}`}>{t("Revisar cortes")}</Link>
            )}
          </div>
          {it.status === "error"
            ? <span className="text-[13px] text-alert-text">{t("A análise falhou:")} {it.error}</span>
            : it.progress ? <PrepBar p={it.progress} /> : <span className="text-[13px] text-muted">{t("Na fila para começar")}</span>}
        </li>
      ))}
    </ul>
  );
}
