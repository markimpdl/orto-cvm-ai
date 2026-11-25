"use client";

/** The channel's week: goal per format, the Mon–Sun calendar of slots (audience time + the studio's local time) and the next
 *  free slots. Data from GET /channels/{id}/week (backend cvm/schedule.py). */
import Link from "next/link";
import { dayIn, FORMAT_LABEL, timeIn, WEEKDAY_LABEL, type CalPub, type ChannelWeek, type GoalItem, type SlotTime } from "@/lib/api";
import { useT, type T } from "@/lib/i18n";

const num = (n: number, locale: string) => n.toLocaleString(locale, { maximumFractionDigits: 1 });

export const formatLabel = (kind: string, n: number, t: T) => {
  const [one, many] = FORMAT_LABEL[kind] ?? [kind, kind];
  return t(n === 1 ? one : many);
};

/** "2 de 3 longos · 5 de 7 Shorts" (formats with a weekly target only). */
export function goalLine(goal: GoalItem[], t: T, locale: string): string {
  return goal
    .filter((g) => g.target >= 1)
    .map((g) => t("{done} de {target} {label}", { done: g.scheduled + g.published, target: num(g.target, locale), label: formatLabel(g.kind, g.target, t) }))
    .join(" · ");
}

export function WeekGoal({ goal }: { goal: GoalItem[] }) {
  const { t, locale } = useT();
  const shown = goal.filter((g) => g.target > 0);
  if (!shown.length) return <p className="m-0 text-sm text-muted">{t("Sem meta semanal: defina “por semana” nos formatos do perfil.")}</p>;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(200px,1fr))]">
      {shown.map((g) => {
        const done = g.scheduled + g.published;
        const pct = Math.min(100, Math.round((done / g.target) * 100));
        return (
          <div key={g.kind} className="flex flex-col gap-2 rounded-xl bg-bg p-3.5">
            <div className="flex items-baseline gap-2">
              <span className="font-display text-2xl font-bold">{done}</span>
              <span className="text-sm text-body">{t("de {target} {label}", { target: num(g.target, locale), label: formatLabel(g.kind, g.target, t) })}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-track" role="progressbar" aria-valuemin={0} aria-valuemax={g.target} aria-valuenow={done} aria-label={formatLabel(g.kind, 2, t)}>
              <div className={`h-full rounded-full ${done >= g.target ? "bg-go" : "bg-ink"}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="text-[13px] text-muted">
              {t("{p} publicados · {s} agendados", { p: g.published, s: g.scheduled })}
              {g.remaining > 0 ? ` · ${t("faltam {n}", { n: num(g.remaining, locale) })}` : ` · ${t("meta cumprida")}`}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function PubLink({ p }: { p: CalPub }) {
  const { t } = useT();
  const title = p.title ?? t("Vídeo");
  const mark = p.status === "published" ? "✓ " : "";
  return p.project_id ? (
    <Link href={`/projetos/${p.project_id}`} className="line-clamp-2 text-[13px] leading-snug font-semibold text-ink no-underline hover:underline" title={title}>{mark}{title}</Link>
  ) : (
    <span className="line-clamp-2 text-[13px] leading-snug font-semibold" title={title}>{mark}{title}</span>
  );
}

/** Mon–Sun columns: each slot filled (title) or empty, with the audience time and, beside it, the studio's local time. */
export function WeekCalendar({ week }: { week: ChannelWeek }) {
  const { t, locale } = useT();
  const today = new Date().toLocaleDateString("en-CA", { timeZone: week.tz });
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-7">
      {week.calendar.map((d) => {
        const [, m, day] = d.date.split("-");
        const isToday = d.date === today;
        return (
          <div key={d.date} className={`flex min-w-0 flex-col gap-2 rounded-xl p-2.5 ${isToday ? "border-2 border-ink bg-surface" : "border border-line bg-surface"}`}>
            <div className="flex items-baseline gap-1.5 text-sm font-semibold">
              <span className="capitalize">{t(WEEKDAY_LABEL[d.weekday] ?? d.weekday)}</span>
              <span className="text-muted">{day}/{m}</span>
              {isToday && <span className="ml-auto text-[11px] font-semibold text-go-text uppercase">{t("hoje")}</span>}
            </div>
            {!d.slots.length && !d.extra.length && <span className="text-[13px] text-muted">—</span>}
            {d.slots.map((s) => (
              <div key={s.utc + s.kind} className={`flex flex-col gap-1 rounded-lg px-2 py-1.5 ${s.publication ? "bg-go-soft" : "border border-dashed border-field"}`}>
                <span className="flex flex-wrap items-baseline gap-x-1.5 text-[12px] text-body">
                  <strong className="text-ink">{timeIn(s.utc, week.tz, locale)}</strong>
                  {formatLabel(s.kind ?? "", 1, t)}
                  {s.home_tz && s.home_tz !== week.tz && (
                    <span className="text-muted" title={t("horário local ({tz})", { tz: s.home_tz })}>· {t("local")} {timeIn(s.utc, s.home_tz, locale)}</span>
                  )}
                </span>
                {s.publication ? <PubLink p={s.publication} /> : <span className="text-[13px] text-muted">{t("vazio")}</span>}
              </div>
            ))}
            {!!d.extra.length && (
              <details className="text-[12px] text-body">
                <summary className="cursor-pointer">{t("+{n} fora dos horários", { n: d.extra.length })}</summary>
                <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
                  {d.extra.map((p) => (
                    <li key={p.id} className="flex flex-col">
                      <span className="text-muted">{timeIn(p.at, week.tz, locale)} · {formatLabel(p.kind, 1, t)}</span>
                      <PubLink p={p} />
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** "sáb 14/11, 19:00 (horário local (Europe/Lisbon): sáb 23:00)". */
export function slotText(s: SlotTime, locale: string, t: T): string {
  const local = `${dayIn(s.utc, s.tz, locale)}, ${timeIn(s.utc, s.tz, locale)}`;
  const home = s.home_tz;
  if (!home || home === s.tz) return local;
  const sameDay = dayIn(s.utc, s.tz, locale) === dayIn(s.utc, home, locale);
  return `${local} (${t("horário local ({tz})", { tz: home })}: ${sameDay ? "" : dayIn(s.utc, home, locale) + " "}${timeIn(s.utc, home, locale)})`;
}

export function NextSlots({ next }: { next: Record<string, SlotTime[]> }) {
  const { t, locale } = useT();
  const kinds = Object.keys(next);
  if (!kinds.length) return null;
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-[repeat(auto-fit,minmax(260px,1fr))]">
      {kinds.map((k) => (
        <div key={k} className="flex flex-col gap-1.5">
          <span className="text-sm font-semibold">{formatLabel(k, 2, t)}</span>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-body">
            {next[k].map((s) => <li key={s.utc}>{slotText(s, locale, t)}</li>)}
            {!next[k].length && <li className="text-muted">{t("Nenhum horário livre.")}</li>}
          </ul>
        </div>
      ))}
    </div>
  );
}
