"use client";

/** Small pieces shared by the install wizard steps (same look as Configurações). */
import { useT } from "@/lib/i18n";

/** Title + one plain line on what the step is for and what happens when it is skipped. */
export function StepHead({ title, lead, children }: { title: string; lead: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="flex flex-col gap-2">
      <h1 className="h1 m-0 text-balance">{title}</h1>
      <p className="m-0 max-w-[62ch] text-base text-body">{lead}</p>
      {children}
    </header>
  );
}

/** Back on the left, the step's own actions on the right. */
export function StepFooter({ onBack, children }: { onBack?: () => void; children: React.ReactNode }) {
  const { t } = useT();
  return (
    <footer className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
      {onBack && <button type="button" className="btn" onClick={onBack}>{t("Voltar")}</button>}
      <div className="ml-auto flex flex-wrap items-center justify-end gap-3">{children}</div>
    </footer>
  );
}

export function Section({ title, hint, children, aside }: { title: string; hint?: React.ReactNode; children?: React.ReactNode; aside?: React.ReactNode }) {
  const id = `s-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <section className="card flex flex-col gap-4 p-5" aria-labelledby={id}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="flex flex-[1_1_280px] flex-col gap-1">
          <h2 id={id} className="h2 m-0">{title}</h2>
          {hint && <p className="m-0 text-sm text-body">{hint}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** On/off row: the switch, a label and a short line under it. */
export function Toggle({ on, onChange, label, hint, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; hint?: React.ReactNode; disabled?: boolean }) {
  return (
    <div className="flex items-start gap-3">
      <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
        onClick={() => onChange(!on)}
        className={`pick mt-0.5 flex h-[24px] w-[42px] flex-none rounded-full border-0 p-[3px] transition-colors disabled:cursor-default disabled:opacity-45 ${on ? "justify-end bg-go" : "justify-start bg-field"}`}>
        <span className="block h-[18px] w-[18px] rounded-full bg-white" />
      </button>
      <span className="flex min-w-0 flex-col">
        <span className="text-[15px] font-semibold">{label}</span>
        {hint && <span className="text-[13px] text-muted">{hint}</span>}
      </span>
    </div>
  );
}

/** One option of a radio group drawn as a card (ink ring when chosen, like "Quem escreve os roteiros"). */
export function Choice({ on, onPick, label, hint, badge }: { on: boolean; onPick: () => void; label: string; hint?: React.ReactNode; badge?: React.ReactNode }) {
  return (
    <button type="button" role="radio" aria-checked={on} onClick={onPick}
      className={`pick flex flex-col gap-1 rounded-xl border p-4 ${on ? "border-ink bg-surface shadow-[inset_0_0_0_1px_var(--color-ink)]" : "border-line bg-surface hover:bg-chip"}`}>
      <span className="flex items-center gap-2 text-[15px] font-semibold">
        <span aria-hidden className={`h-2.5 w-2.5 flex-none rounded-full ${on ? "bg-go" : "bg-field"}`} />
        <span className="flex-1">{label}</span>
        {badge}
      </span>
      {hint && <span className="text-[13px] text-muted">{hint}</span>}
    </button>
  );
}

/** Labelled field; `note` goes under the input. */
export function Field({ label, note, children }: { label: string; note?: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className="text-[13px] font-semibold text-body">{label}</span>
      {children}
      {note && <span className="text-[13px] text-muted">{note}</span>}
    </label>
  );
}

export function Note({ tone = "plain", children }: { tone?: "plain" | "ok" | "warn" | "alert"; children: React.ReactNode }) {
  const cls = { plain: "bg-bg text-body", ok: "bg-go-soft text-go-text", warn: "bg-chip text-warn-text", alert: "bg-alert-soft text-alert-text" }[tone];
  return <div role={tone === "alert" ? "alert" : undefined} className={`rounded-xl px-4 py-3 text-sm ${cls}`}>{children}</div>;
}

export function gbText(n: number): string {
  if (!n) return "0 GB";
  return n >= 10 ? `${Math.round(n)} GB` : n >= 1 ? `${n.toFixed(1).replace(/\.0$/, "")} GB` : `${Math.max(1, Math.round(n * 1000))} MB`;
}
