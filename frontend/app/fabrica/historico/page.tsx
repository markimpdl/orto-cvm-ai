"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { PageHeader } from "@/components/Bits";
import { FilterBar, matches, optionsOf, useFilters } from "@/components/Filters";
import { IconExternal } from "@/components/Icons";
import { api, LANG_FLAG, LANG_LABEL, PLATFORM_LABEL, RENDER_TARGETS, type Channel, type VideoRemoved } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Pub = {
  id: number;
  status: string;
  scheduled_at: string;
  published_at?: string | null;
  url: string | null;
  project_title: string | null;
  project_id: number | null;
  channel: Channel;
  render: { id: number; language: string; profile: string; meta?: { video_removed?: VideoRemoved | null } };
};

const asDate = (s: string) => new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
const PAGE = 50;

/** Fábrica > Histórico: every published video, newest first (from /publications; the project title links back). */
export default function HistoryPage() {
  const { t, locale } = useT();
  const { projects } = useAppData();
  const [pubs, setPubs] = useState<Pub[] | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => {
    api<Pub[]>("/publications")
      .then((p) => setPubs(p.filter((x) => x.status === "published" && x.render && x.channel)))
      .catch(() => setPubs([]));
  }, [projects.length]);

  const list = (pubs ?? []).slice().sort((a, b) => +asDate(b.published_at ?? b.scheduled_at) - +asDate(a.published_at ?? a.scheduled_at));
  const [f, setF, clearF] = useFilters("historico");
  const defs = [
    { key: "channel", label: t("Canal"), options: optionsOf(list, (p) => String(p.channel.id), (v) => list.find((p) => String(p.channel.id) === v)?.channel.name ?? v) },
    { key: "lang", label: t("Idioma"), options: optionsOf(list, (p) => p.render.language, (l) => t(LANG_LABEL[l] ?? l)) },
    { key: "profile", label: t("Formato"), options: optionsOf(list, (p) => p.render.profile, (v) => t(RENDER_TARGETS[v] ?? v)) },
  ];
  const shown = list.filter((p) => (!f.channel || String(p.channel.id) === f.channel)
    && (!f.lang || p.render.language === f.lang)
    && (!f.profile || p.render.profile === f.profile)
    && matches(f.q, p.project_title, p.channel.name));
  const visible = all ? shown : shown.slice(0, PAGE);

  return (
    <>
      <PageHeader title={t("Histórico")} subtitle={t("Tudo o que já foi publicado, do mais novo ao mais antigo.")} />
      {list.length > 1 && <FilterBar defs={defs} f={f} set={setF} clear={clearF} shown={shown.length} total={list.length} />}
      {pubs === null ? (
        <div className="card p-5 text-muted">{t("Carregando…")}</div>
      ) : !list.length ? (
        <div className="card p-6 text-body">{t("Nenhum vídeo publicado ainda.")}</div>
      ) : (
        <div className="card overflow-hidden">
          {visible.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-line-soft px-5 py-4 last:border-b-0">
              <span className="w-[92px] flex-none text-[13px] text-muted">{asDate(p.published_at ?? p.scheduled_at).toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "numeric" })}</span>
              <span className="min-w-0 flex-[1_1_240px] text-[15px]">
                {p.project_id ? <Link href={`/projetos/${p.project_id}`}>{p.project_title ?? t("Sem título")}</Link> : (p.project_title ?? t("Sem título"))}
              </span>
              <span className="pill bg-chip text-body">
                {LANG_FLAG[p.render.language] ?? ""} {p.channel.name} · {PLATFORM_LABEL[p.channel.platform] ?? p.channel.platform} · {t(RENDER_TARGETS[p.render.profile] ?? p.render.profile)}
              </span>
              {p.render.meta?.video_removed && <span className="pill bg-chip text-muted">{t("vídeo removido")}</span>}
              {p.url ? (
                <a href={p.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm"><IconExternal />{t("ver post")}</a>
              ) : (
                <span className="text-sm text-muted">{t("sem link")}</span>
              )}
            </div>
          ))}
          {shown.length > PAGE && (
            <button className="w-full border-0 bg-transparent px-5 py-3 text-left text-sm font-semibold text-ink hover:bg-bg" onClick={() => setAll(!all)}>
              {all ? t("Ver menos") : t("Ver todos ({n})", { n: shown.length })}
            </button>
          )}
          {!shown.length && <p className="m-0 px-5 py-4 text-muted">{t("Nenhum vídeo com esses filtros.")}</p>}
        </div>
      )}
    </>
  );
}
