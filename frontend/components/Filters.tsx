"use client";

import { useEffect, useState } from "react";
import { LANG_LABEL, STYLE_LABEL, type Channel, type Project } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type Opt = { value: string; label: string; n?: number };
export type FilterDef = { key: string; label: string; options: Opt[] };
export type FilterState = Record<string, string>;

/** Filter values for one page, remembered per page in this browser (search text is not remembered). */
export function useFilters(page: string): [FilterState, (k: string, v: string) => void, () => void] {
  const storeKey = `cvm.filters.${page}`;
  const [f, setF] = useState<FilterState>({});
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storeKey) ?? "{}");
      if (saved && typeof saved === "object") setF(saved);
    } catch { /* storage blocked or bad JSON: start empty */ }
  }, [storeKey]);
  const save = (n: FilterState) => {
    try { const keep = { ...n }; delete keep.q; localStorage.setItem(storeKey, JSON.stringify(keep)); } catch { /* ignore */ }
  };
  const set = (k: string, v: string) => setF((s) => { const n = { ...s, [k]: v }; if (!v) delete n[k]; save(n); return n; });
  const clear = () => { setF({}); save({}); };
  return [f, set, clear];
}

/** Case- and accent-insensitive "contains". */
export function matches(q: string | undefined, ...texts: (string | null | undefined)[]): boolean {
  if (!q?.trim()) return true;
  const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const needle = norm(q.trim());
  return texts.some((t) => t && norm(t).includes(needle));
}

/** Distinct options from a list, sorted by label, with counts. */
export function optionsOf<T>(items: T[], value: (x: T) => string | null | undefined, label: (v: string) => string): Opt[] {
  const count = new Map<string, number>();
  for (const x of items) {
    const v = value(x);
    if (v) count.set(v, (count.get(v) ?? 0) + 1);
  }
  return [...count.entries()].map(([v, n]) => ({ value: v, label: label(v), n })).sort((a, b) => a.label.localeCompare(b.label));
}

/** Search box + one select per filter (hidden when it has fewer than two options) + clear. */
export function FilterBar({ defs, f, set, clear, shown, total }: {
  defs: FilterDef[]; f: FilterState; set: (k: string, v: string) => void; clear: () => void; shown: number; total: number;
}) {
  const { t } = useT();
  const visible = defs.filter((d) => d.options.length > 1 || f[d.key]);
  const active = Object.keys(f).some((k) => f[k]);
  return (
    <div className="flex flex-wrap items-center gap-2.5" role="search">
      <input
        type="search"
        className="input min-h-10 flex-[1_1_220px] text-[15px]"
        placeholder={t("Buscar pelo título")}
        aria-label={t("Buscar pelo título")}
        value={f.q ?? ""}
        onChange={(e) => set("q", e.target.value)}
      />
      {visible.map((d) => (
        <select
          key={d.key}
          className={`input min-h-10 max-w-[220px] text-[15px] ${f[d.key] ? "border-ink font-semibold" : ""}`}
          aria-label={d.label}
          value={f[d.key] ?? ""}
          onChange={(e) => set(d.key, e.target.value)}
        >
          <option value="">{d.label}: {t("todos")}</option>
          {d.options.map((o) => <option key={o.value} value={o.value}>{o.label}{o.n != null ? ` (${o.n})` : ""}</option>)}
        </select>
      ))}
      {active && (
        <>
          <span className="text-sm text-muted">{t("{shown} de {total}", { shown, total })}</span>
          <button className="btn btn-sm" onClick={clear}>{t("Limpar filtros")}</button>
        </>
      )}
    </div>
  );
}

/** Channel / language / style filters for a list of projects, and the matching predicate. */
export function projectFilters(
  list: Project[], channelOf: (id: number | null | undefined) => Channel | undefined, t: (s: string) => string,
): FilterDef[] {
  return [
    { key: "channel", label: t("Canal"), options: optionsOf(list, (p) => p.channel_id != null ? String(p.channel_id) : null, (v) => channelOf(Number(v))?.name ?? `#${v}`) },
    { key: "lang", label: t("Idioma"), options: optionsOf(list.flatMap((p) => p.languages), (l) => l, (l) => t(LANG_LABEL[l] ?? l)) },
    { key: "style", label: t("Estilo"), options: optionsOf(list, (p) => p.style, (s) => t(STYLE_LABEL[s] ?? s)) },
  ];
}
export function projectMatches(p: Project, f: FilterState): boolean {
  return (!f.channel || String(p.channel_id) === f.channel)
    && (!f.lang || p.languages.includes(f.lang))
    && (!f.style || p.style === f.style)
    && matches(f.q, p.title, p.topic);
}
