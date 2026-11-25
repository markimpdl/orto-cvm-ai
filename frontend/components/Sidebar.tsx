"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { goalLine } from "@/components/ChannelWeek";
import { IconPlus } from "@/components/Icons";
import { api, channelGoals, LANG_FLAG, type ChannelGoals } from "@/lib/api";
import { useFeatures } from "@/lib/features";
import { useT, type Lang } from "@/lib/i18n";
import { fmtAgo, fmtClock, fmtDuration, groupOf } from "@/lib/pipeline";

type SystemStatus = {
  models_ready: number; models_total: number; claude_auto_run: boolean; claude_limit: { until: string; message: string } | null;
  trends_last_collected_at: string | null; trends_every_min: number;
};

function useSystemStatus() {
  const [st, setSt] = useState<SystemStatus | null>(null);
  const [down, setDown] = useState(false);
  const load = () =>
    api<SystemStatus>("/system/status")
      .then((s) => { setSt(s); setDown(false); })
      .catch(() => setDown(true));
  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  return { st, down, load };
}

function StatusBox() {
  const { st, down, load } = useSystemStatus();
  const { t } = useT();
  const missing = st ? st.models_total - st.models_ready : 0;
  const limit = st?.claude_limit;
  const [dot, title] = down
    ? ["bg-alert", t("API fora do ar")]
    : !st
      ? ["bg-field", t("Verificando…")]
      : limit
        ? ["bg-[#d98a00]", t("Claude no limite")]
        : missing
          ? ["bg-[#d98a00]", missing > 1 ? t("Faltam {n} modelos", { n: missing }) : t("Falta {n} modelo", { n: missing })]
          : ["bg-go", t("Tudo funcionando")];
  let trends = "";
  if (st?.trends_last_collected_at) {
    const last = new Date(st.trends_last_collected_at.endsWith("Z") || st.trends_last_collected_at.includes("+") ? st.trends_last_collected_at : st.trends_last_collected_at + "Z");
    const next = Math.max(0, st.trends_every_min * 60 - ((Date.now() - last.getTime()) / 1000) % (st.trends_every_min * 60));
    trends = t("Tendências atualizadas há {ago}. Próxima coleta em {next}.", { ago: fmtAgo(st.trends_last_collected_at), next: fmtDuration(next) });
  }
  return (
    <div className="flex flex-col gap-1.5 rounded-xl bg-bg p-3.5">
      <div className="flex items-center gap-2 text-[13px] font-semibold"><span className={`h-2 w-2 rounded-full ${dot}`} />{title}</div>
      {down && <div className="text-[13px] leading-snug text-muted">{t("Rode")} <code>uv run cvm-api</code> {t("e")} <code>uv run cvm-worker</code> {t("em")} <code>backend/</code>.</div>}
      {limit && (
        <div className="text-[13px] leading-snug text-muted">
          {t("Roteiros, SEO e ideias pausados. Retomam sozinhos às {time}.", { time: fmtClock(limit.until) })}{" "}
          <button type="button" className="underline" onClick={() => api("/system/claude-limit", { method: "DELETE" }).then(load)}>
            {t("Liberar agora")}
          </button>
        </div>
      )}
      {trends && <div className="text-[13px] leading-snug text-muted">{trends}</div>}
      {missing > 0 && <div className="text-[13px] leading-snug text-muted">{t("Baixar:")} <code>uv run python -m cvm.models_registry download</code></div>}
    </div>
  );
}

function LangSwitch() {
  const { lang, setLang, t } = useT();
  const opts: [Lang, string][] = [["pt", "PT"], ["en", "EN"]];
  return (
    <div role="group" aria-label={t("Idioma da interface")} className="mt-auto flex items-center gap-1.5">
      {opts.map(([k, label]) => (
        <button
          key={k}
          type="button"
          aria-pressed={lang === k}
          onClick={() => setLang(k)}
          className={`btn btn-sm min-h-9 rounded-full px-3.5 ${lang === k ? "btn-ink" : ""}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

const SUB_LIST = "ml-[18px] flex flex-col gap-0.5 border-l border-line pl-2";
const subCls = (active: boolean) =>
  `flex min-h-10 items-center gap-2.5 rounded-[10px] px-3 text-[14px] font-medium no-underline ${active ? "bg-ink text-white hover:bg-ink hover:text-white" : "text-ink hover:bg-[#ecedea] hover:text-ink"}`;

function Count({ n, red }: { n: number; red?: boolean }) {
  if (n <= 0) return null;
  return <span className={`h-[22px] min-w-[22px] rounded-full px-1.5 text-center text-xs leading-[22px] font-semibold ${red ? "bg-alert text-white" : "bg-line text-ink"}`}>{n}</span>;
}

/** Top-level item. `active` = this page (ink fill); `open` = one of its sub-items is the page (green dot, bold, no fill). */
function TopItem({ href, label, active, open, count, red }: { href: string; label: string; active: boolean; open?: boolean; count?: number; red?: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`flex min-h-11 items-center gap-3 rounded-[10px] px-3.5 text-[15px] no-underline ${active ? "bg-ink font-medium text-white hover:bg-ink hover:text-white" : `text-ink hover:bg-[#ecedea] hover:text-ink ${open ? "font-semibold" : "font-medium"}`}`}
    >
      <span className={`h-2 w-2 rounded-full ${active || open ? "bg-go" : "bg-field"}`} />
      <span className="flex-1">{label}</span>
      <Count n={count ?? 0} red={red} />
    </Link>
  );
}

/** Sub-items of Canais: one per active or paused channel with this week's goal; archived ones behind "Mostrar arquivados";
 *  series (status "series") are managed on Fábrica > Séries and not listed. */
function ChannelNav({ path }: { path: string }) {
  const { t, locale } = useT();
  const { channels } = useAppData();
  const [goals, setGoals] = useState<ChannelGoals[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  useEffect(() => {
    const load = () => channelGoals().then(setGoals).catch(() => {});
    load();
    const i = setInterval(load, 60_000);
    return () => clearInterval(i);
  }, [path, channels]);
  const archived = channels.filter((c) => c.status === "archived");
  const items = [
    ...goals.map((g) => ({ id: g.id, name: g.name, language: g.language, status: g.status, line: goalLine(g.goal, t, locale) })),
    ...(showArchived ? archived.map((c) => ({ id: c.id, name: c.name, language: c.language, status: "archived", line: "" })) : []),
  ];
  if (!items.length && !archived.length) return null;
  return (
    <div className={SUB_LIST}>
      {items.map((c) => {
        const active = path === `/canais/${c.id}`;
        return (
          <Link
            key={c.id}
            href={`/canais/${c.id}`}
            aria-current={active ? "page" : undefined}
            className={`flex min-h-10 flex-col justify-center gap-0.5 rounded-[10px] px-3 py-1.5 no-underline ${active ? "bg-ink text-white hover:bg-ink hover:text-white" : "text-ink hover:bg-[#ecedea] hover:text-ink"} ${c.status === "archived" ? "opacity-70" : ""}`}
          >
            <span className="flex min-w-0 items-center gap-2 text-[14px] font-medium">
              <span aria-hidden>{LANG_FLAG[c.language] ?? ""}</span>
              <span className="min-w-0 flex-1 truncate" title={c.name}>{c.name}</span>
              {c.status === "paused" && <span className="pill shrink-0 bg-chip px-2 py-0.5 text-[11px] text-warn-text">{t("pausado")}</span>}
              {c.status === "archived" && <span className="pill shrink-0 bg-line-soft px-2 py-0.5 text-[11px] text-muted">{t("arquivado")}</span>}
            </span>
            {c.line && <span className={`text-[12px] leading-snug ${active ? "text-white/80" : "text-muted"}`}>{c.line}</span>}
          </Link>
        );
      })}
      {!!archived.length && (
        <button type="button" className="min-h-9 rounded-[10px] px-3 text-left text-[13px] text-muted hover:bg-[#ecedea]" aria-pressed={showArchived} onClick={() => setShowArchived((v) => !v)}>
          {showArchived ? t("Esconder arquivados") : t("Mostrar arquivados ({n})", { n: archived.length })}
        </button>
      )}
    </div>
  );
}

export default function Sidebar() {
  const path = usePathname();
  const { t } = useT();
  const features = useFeatures();
  const { projects, publishDue } = useAppData();
  const groups = projects.map((p) => groupOf(p.job));
  const factory = [
    { href: "/fabrica", label: t("Produção"), count: groups.filter((g) => g === "run" || g === "wait").length, red: false, match: (p: string) => p === "/fabrica" || p.startsWith("/projetos") },
    { href: "/fabrica/novo", label: t("Novo vídeo"), count: 0, red: false, match: (p: string) => p.startsWith("/fabrica/novo") },
    { href: "/fabrica/sua-vez", label: t("Sua vez"), count: groups.filter((g) => g === "you").length, red: true, match: (p: string) => p.startsWith("/fabrica/sua-vez") },
    { href: "/fabrica/publicar", label: t("Publicar"), count: publishDue, red: false, match: (p: string) => p.startsWith("/fabrica/publicar") },
    { href: "/fabrica/historico", label: t("Histórico"), count: 0, red: false, match: (p: string) => p.startsWith("/fabrica/historico") },
    { href: "/fabrica/series", label: t("Séries"), count: 0, red: false, match: (p: string) => p.startsWith("/fabrica/series") },
    { href: "/fabrica/cortes", label: t("Clips e cortes"), count: 0, red: false, match: (p: string) => p.startsWith("/fabrica/cortes") },
  ];
  const inFactory = factory.some((n) => n.match(path));
  if (path.startsWith("/instalar")) return null; // the install wizard is a focused full-width page
  return (
    <aside className="box-border flex max-w-full flex-[1_1_240px] flex-col gap-5 border-r border-line bg-surface px-4 py-5 md:max-w-[240px]">
      <Link href="/fabrica" aria-label={t("Orto CVM, início")}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brand/logo-wordmark.png" alt="Orto CVM" className="block h-[70px] w-[208px] object-contain" />
      </Link>
      <nav aria-label={t("Principal")} className="flex flex-col gap-1">
        <TopItem href="/em-alta" label={t("Em alta")} active={path.startsWith("/em-alta")} />
        <TopItem href="/fabrica" label={t("Fábrica")} active={false} open={inFactory} />
        <div className={SUB_LIST}>
          {factory.map((n) => {
            const active = n.match(path);
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined} className={subCls(active)}>
                <span className="flex-1">{n.label}</span>
                <Count n={n.count} red={n.red} />
              </Link>
            );
          })}
        </div>
        <TopItem href="/canais" label={t("Canais")} active={path === "/canais"} open={path.startsWith("/canais/")} />
        {/* the channel list opens only inside Canais, so Avatares and Configurações stay visible without scrolling */}
        {path.startsWith("/canais") && <ChannelNav path={path} />}
        {features.avatars && <TopItem href="/avatares" label={t("Avatares")} active={path === "/avatares"} open={path.startsWith("/avatares/")} />}
        <TopItem href="/configuracoes" label={t("Configurações")} active={path.startsWith("/configuracoes")} />
      </nav>
      <Link href="/fabrica/novo" className="btn w-full"><IconPlus />{t("Novo vídeo")}</Link>
      <LangSwitch />
      <StatusBox />
    </aside>
  );
}
