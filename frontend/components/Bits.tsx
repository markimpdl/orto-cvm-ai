"use client";

/** Small shared pieces: potential score badge, styled checkbox, page header. */
import { fmtBytes, type VideoRemoved } from "@/lib/api";
import { useT } from "@/lib/i18n";

export function ScoreBadge({ score, size = "md" }: { score: number; size?: "md" | "lg" }) {
  const { t } = useT();
  const s = Math.round(score);
  const cls = s >= 75 ? "bg-go text-white" : s >= 60 ? "bg-go-soft text-go-text" : "bg-chip text-body";
  return size === "lg" ? (
    <span className={`inline-flex h-11 w-12 flex-none flex-col items-center justify-center rounded-[10px] ${cls}`} title={t("Potencial {n}", { n: s })}>
      <span className="font-display text-lg leading-none font-bold">{s}</span>
      <span className="text-[10px] tracking-[0.4px]">{t("POT.")}</span>
    </span>
  ) : (
    <span className={`inline-flex h-10 w-11 flex-none items-center justify-center rounded-[10px] font-display text-[17px] font-bold ${cls}`} title={t("Potencial {n}", { n: s })}>{s}</span>
  );
}

export function CheckBox({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={label} onClick={onChange} className="pick inline-flex h-11 w-8 flex-none items-center justify-center border-0 bg-transparent p-0">
      <span className={`inline-flex h-5 w-5 items-center justify-center rounded-[5px] border-2 text-[13px] font-bold ${checked ? "border-go bg-go text-white" : "border-[#8a8f97] bg-surface"}`}>{checked ? "✓" : ""}</span>
    </button>
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-end gap-3">
      <div className="flex-[1_1_320px]">
        <h1 className="h1 m-0">{title}</h1>
        {subtitle && <p className="mt-2 mb-0 text-base text-body">{subtitle}</p>}
      </div>
      {children}
    </header>
  );
}

export function Initial({ name, size = 40 }: { name: string; size?: number }) {
  return (
    <span className="inline-flex flex-none items-center justify-center rounded-full bg-ink font-display font-semibold text-white" style={{ width: size, height: size, fontSize: size * 0.38 }}>
      {name.trim().charAt(0).toUpperCase()}
    </span>
  );
}

/** Shown in place of a render's player once "Limpar projetos antigos" deleted its video file. */
export function VideoRemovedNote({ removed, url, className = "" }: { removed: VideoRemoved; url?: string | null; className?: string }) {
  const { t, locale } = useT();
  const d = new Date(removed.at.endsWith("Z") || removed.at.includes("+") ? removed.at : removed.at + "Z");
  const date = isNaN(+d) ? removed.at : d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "numeric" });
  return (
    <div className={`flex flex-col gap-1 rounded-[14px] bg-chip px-4 py-3 text-sm text-body ${className}`}>
      <span>{t("Vídeo removido para liberar espaço em {date} — está no YouTube", { date })}{removed.bytes ? ` (${fmtBytes(removed.bytes)})` : ""}.</span>
      {url && <a href={url} target="_blank" rel="noreferrer" className="font-semibold">{t("Ver no YouTube")}</a>}
    </div>
  );
}

/** Segmented tabs shared by the pages that split one screen in sections. */
export function Tabs<K extends string>({ tabs, value, onChange, label }: { tabs: [K, string][]; value: K; onChange: (k: K) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex w-fit flex-wrap gap-1 rounded-xl bg-[#e9eae6] p-1">
      {tabs.map(([k, text]) => (
        <button key={k} role="tab" aria-selected={value === k} onClick={() => onChange(k)}
          className={`pick inline-flex min-h-10 items-center rounded-[9px] border-0 px-3.5 text-sm font-semibold ${value === k ? "bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.08)]" : "bg-transparent text-body"}`}>
          {text}
        </button>
      ))}
    </div>
  );
}
