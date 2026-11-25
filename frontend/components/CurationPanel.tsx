"use client";

import { useT } from "@/lib/i18n";

/** One check of the automatic validation step (backend cvm/curation.py, Render.meta.curation). */
export type CurationCheck = { name: string; label: string; ok: boolean; detail: string; fix_state?: string; soft?: boolean; skipped?: boolean };
export type Curation = {
  at: string;
  attempts: number;
  max_attempts: number;
  checks: CurationCheck[];
  fixed: { name: string; label: string; attempts: number; detail: string }[];
  left: string[];
  capped?: boolean;
  routed_to?: string;
};
export type CurationSummary = { ok: number; fixed: number; warnings: number };

/** Portuguese source labels per check name (rendered through t()). */
const CHECK_LABEL: Record<string, string> = {
  voice: "Narração",
  narration_version: "Narração do roteiro atual",
  visuals: "Imagens de IA",
  clips: "Clipes animados",
  flags: "Bandeiras",
  real_footage: "Material real",
  render: "Render",
  end_screen: "Tela final",
  structure: "Estrutura",
  shorts: "Regras de Shorts",
  content: "Conteúdo e fatos",
  internal: "Conferidor",
};

/** The newest report among a project's renders (they all carry the same one). */
export function latestCuration(renders: { meta: { curation?: Curation } }[]): Curation | null {
  return renders.map((r) => r.meta.curation).filter((c): c is Curation => !!c).sort((a, b) => b.at.localeCompare(a.at))[0] ?? null;
}

/** "Conferido pela IA": what the validation step checked, fixed by itself and left for the user. */
export default function CurationPanel({ report }: { report: Curation }) {
  const { t } = useT();
  const label = (name: string, fallback: string) => t(CHECK_LABEL[name] ?? fallback);
  const passed = report.checks.filter((c) => c.ok);
  const warnings = report.checks.filter((c) => !c.ok);
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-bg px-4 py-3" aria-labelledby="h-curation">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="h-curation" className="m-0 flex-[1_1_200px] text-[15px] font-semibold">{t("Conferido pela IA")}</h3>
        <span className="pill bg-go-soft text-go-text">{t("{n} ok", { n: passed.length })}</span>
        {!!report.fixed.length && <span className="pill bg-go-soft text-go-text">{t(report.fixed.length > 1 ? "{n} corrigidos" : "{n} corrigido", { n: report.fixed.length })}</span>}
        {!!warnings.length && <span className="pill bg-chip text-warn-text">{t("{n} para olhar", { n: warnings.length })}</span>}
      </div>
      {!!report.fixed.length && (
        <div className="flex flex-col gap-1">
          <span className="text-[13px] font-semibold text-go-text">{t("Corrigido sozinho antes de chegar a você")}</span>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-body">
            {report.fixed.map((f) => (
              <li key={f.name}>
                <strong>{label(f.name, f.label)}</strong>
                {" · "}{f.attempts > 1 ? t("em {n} tentativas", { n: f.attempts }) : t("na primeira tentativa")}
                {f.detail && <span className="text-muted"> · {f.detail}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {!!warnings.length && (
        <div className="flex flex-col gap-1">
          <span className="text-[13px] font-semibold text-warn-text">{t("Para você olhar")}</span>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-body">
            {warnings.map((c) => (
              <li key={c.name}>
                <strong>{label(c.name, c.label)}</strong>
                {c.skipped && <span className="text-muted"> · {t("não conferido")}</span>}
                {report.left.includes(c.name) && <span className="text-warn-text"> · {t("a IA não conseguiu corrigir")}</span>}
                {c.detail && <span> · {c.detail}</span>}
              </li>
            ))}
          </ul>
          {report.capped && <p className="m-0 text-[13px] text-muted">{t("Chegou ao limite de {n} tentativas automáticas; o resto fica com você.", { n: report.max_attempts })}</p>}
        </div>
      )}
      {!!passed.length && (
        <details className="text-sm text-body">
          <summary className="cursor-pointer text-[13px] font-semibold text-muted">{t("Ver o que passou")}</summary>
          <ul className="m-0 mt-1.5 flex list-none flex-col gap-1 p-0">
            {passed.map((c) => (
              <li key={c.name}>✓ <strong>{label(c.name, c.label)}</strong>{c.detail && <span className="text-muted"> · {c.detail}</span>}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/** One line for a "Sua vez" card, from the job history entry the validation step leaves ("curation_summary"). */
export function CurationLine({ summary }: { summary: CurationSummary }) {
  const { t } = useT();
  const parts = [t("{n} ok", { n: summary.ok })];
  if (summary.fixed) parts.push(t(summary.fixed > 1 ? "{n} corrigidos" : "{n} corrigido", { n: summary.fixed }));
  if (summary.warnings) parts.push(t("{n} para olhar", { n: summary.warnings }));
  return (
    <p className={`m-0 text-[13px] ${summary.warnings ? "text-warn-text" : "text-go-text"}`}>
      {t("Conferido pela IA")}: {parts.join(" · ")}
    </p>
  );
}
