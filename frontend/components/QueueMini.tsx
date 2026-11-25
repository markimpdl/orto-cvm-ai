"use client";

import Link from "next/link";
import { useAppData } from "@/components/AppData";
import { SegmentBar } from "@/components/StageChips";
import { useT } from "@/lib/i18n";
import { groupOf, isFailed, statusText } from "@/lib/pipeline";

/** Right-hand queue summary on the trends screen. */
export default function QueueMini() {
  const { projects, durations } = useAppData();
  const { t } = useT();
  const order = { run: 0, wait: 1, you: 2 } as Record<string, number>;
  const items = projects
    .filter((p) => groupOf(p.job) in order)
    .sort((a, b) => order[groupOf(a.job)] - order[groupOf(b.job)] || (a.job?.queue_position ?? 0) - (b.job?.queue_position ?? 0));
  return (
    <aside aria-labelledby="h-q" className="flex min-w-0 flex-[1_1_320px] flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 id="h-q" className="h2 m-0 mr-auto">{t("Fila")}</h2>
        <Link href="/fabrica" className="btn btn-sm min-h-9">{t("Ver tudo")}</Link>
      </div>
      <div className="card overflow-hidden">
        {items.map((p) => {
          const g = groupOf(p.job);
          const failed = isFailed(p.job);
          const [badge, badgeCls] = failed ? ["✕", "bg-alert text-white"] : g === "you" ? ["!", "bg-alert text-white"] : g === "run" ? ["▶", "bg-ink text-white"] : [String(p.job?.queue_position ?? "·"), "bg-chip text-body"];
          return (
            <Link key={p.id} href={`/projetos/${p.id}`} className="flex w-full flex-col gap-2 border-b border-line-soft bg-surface px-4 py-3.5 text-ink no-underline last:border-b-0 hover:bg-bg hover:text-ink">
              <span className="flex w-full items-center gap-2.5">
                <span className={`inline-flex h-6 w-6 flex-none items-center justify-center rounded-full text-xs font-bold ${badgeCls}`}>{badge}</span>
                <span className="min-w-0 flex-1 truncate text-sm font-semibold">{p.title}</span>
              </span>
              <SegmentBar job={p.job} />
              <span className={`text-[13px] ${g === "you" ? "text-alert-text" : g === "run" ? "text-body" : "text-muted"}`}>{statusText(p.job, durations)}</span>
            </Link>
          );
        })}
        {!items.length && <p className="m-0 px-4 py-3.5 text-sm text-muted">{t("Nada na fila. Clique em Produzir num tema.")}</p>}
      </div>
      <p className="m-0 text-[13px] leading-[1.45] text-muted">
        {t("Etapas do Claude (roteiro, títulos e tags) começam sozinhas pela sua assinatura, até 2 ao mesmo tempo. Produção: 1 vídeo por vez na GPU do Mac.")}
      </p>
    </aside>
  );
}
