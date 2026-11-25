"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { CheckBox } from "@/components/Bits";
import { cleanupPreview, fmtBytes, runCleanup, RENDER_TARGETS, type CleanupFile, type CleanupPreview } from "@/lib/api";
import { useT } from "@/lib/i18n";

const KIND_LABEL: Record<CleanupFile["kind"], string> = {
  render: "Vídeo final",
  avatar: "Clipes do avatar",
  visual: "Clipes da IA",
  other: "Outros",
};
const KINDS: CleanupFile["kind"][] = ["render", "avatar", "visual", "other"];

/**
 * "Limpar projetos antigos": preview of the big video files of videos published at least N days ago, with a
 * two-step confirmation. Opening it only reads the preview; nothing is deleted until the second click.
 */
export default function CleanupPanel({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { setToast } = useAppData();
  const { t, locale } = useT();
  const [days, setDays] = useState(3);
  const [preview, setPreview] = useState<CleanupPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!Number.isFinite(days) || days < 0) return;
    let live = true;
    setLoading(true);
    setConfirming(false);
    const timer = setTimeout(() => {
      cleanupPreview(days)
        .then((p) => { if (!live) return; setPreview(p); setPicked(new Set(p.projects.map((x) => x.project_id))); setErr(null); })
        .catch((e) => live && setErr(String(e)))
        .finally(() => live && setLoading(false));
    }, 350);
    return () => { live = false; clearTimeout(timer); };
  }, [days]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !running) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, running]);

  const projects = preview?.projects ?? [];
  const total = projects.filter((p) => picked.has(p.project_id)).reduce((s, p) => s + p.bytes, 0);
  const toggle = (id: number) => {
    setConfirming(false);
    setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  };
  const allOn = projects.length > 0 && projects.every((p) => picked.has(p.project_id));

  async function run() {
    setRunning(true);
    setErr(null);
    try {
      const res = await runCleanup(projects.filter((p) => picked.has(p.project_id)).map((p) => p.project_id), days);
      setToast({ msg: t("Espaço liberado: {size} em {n} projetos.", { size: fmtBytes(res.removed_bytes), n: res.projects.length }) });
      onDone();
      onClose();
    } catch (e) {
      setErr(String(e));
      setConfirming(false);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 px-4 py-10" onClick={(e) => e.target === e.currentTarget && !running && onClose()}>
      <div role="dialog" aria-modal="true" aria-labelledby="cleanup-title" className="card flex w-full max-w-[760px] flex-col gap-4 p-6">
        <div className="flex items-start gap-3">
          <h2 id="cleanup-title" className="h2 m-0 flex-1">{t("Limpar projetos antigos")}</h2>
          <button className="btn btn-sm" onClick={onClose} disabled={running}>{t("Fechar")}</button>
        </div>
        <p className="m-0 text-sm text-body">{t("Mantém: roteiro, vozes, capas, tags, título/descrição, link e métricas do YouTube. Apaga só os vídeos (render final, clipes do avatar e da IA).")}</p>
        <label className="flex flex-wrap items-center gap-2 text-sm font-semibold">
          {t("Publicados há pelo menos")}
          <input type="number" min={0} className="input w-20 font-normal" value={Number.isFinite(days) ? days : ""} onChange={(e) => setDays(e.target.value === "" ? NaN : Math.max(0, Math.floor(Number(e.target.value))))} />
          {t("dias")}
        </label>

        {err && <p className="m-0 text-sm text-alert-text">{err}</p>}
        {loading && !preview ? (
          <p className="m-0 text-muted">{t("Calculando…")}</p>
        ) : !projects.length ? (
          <p className="m-0 text-muted">{loading ? t("Calculando…") : t("Nenhum projeto publicado há {n} dias ou mais com vídeos para apagar.", { n: days })}</p>
        ) : (
          <div className={`flex flex-col ${loading ? "opacity-60" : ""}`}>
            <div className="flex items-center gap-1 border-b border-line-soft pb-1 text-sm text-muted">
              <CheckBox checked={allOn} label={t("Marcar todos")} onChange={() => { setConfirming(false); setPicked(allOn ? new Set() : new Set(projects.map((p) => p.project_id))); }} />
              {t("{n} projetos", { n: projects.length })}
            </div>
            {projects.map((p) => (
              <div key={p.project_id} className="flex items-start gap-1 border-b border-line-soft py-2 last:border-b-0">
                <CheckBox checked={picked.has(p.project_id)} label={p.title} onChange={() => toggle(p.project_id)} />
                <div className="flex min-w-0 flex-1 flex-col gap-1 pt-2.5">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="flex-[1_1_220px] text-[15px] font-semibold">{p.title}</span>
                    <span className="text-sm font-semibold">{fmtBytes(p.bytes)}</span>
                  </div>
                  <span className="text-[13px] text-muted">
                    {p.channel ?? "—"} · {t("publicado em {date}", { date: new Date(p.published_at.endsWith("Z") || p.published_at.includes("+") ? p.published_at : p.published_at + "Z").toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "numeric" }) })}
                  </span>
                  <details className="text-[13px]">
                    <summary className="cursor-pointer text-muted">{t("{n} arquivos", { n: p.files.length })}</summary>
                    <div className="mt-1 flex flex-col gap-2">
                      {KINDS.filter((k) => p.files.some((f) => f.kind === k)).map((k) => {
                        const fs = p.files.filter((f) => f.kind === k);
                        return (
                          <div key={k} className="flex flex-col gap-0.5">
                            <span className="font-semibold">{t(KIND_LABEL[k])} · {fmtBytes(fs.reduce((s, f) => s + f.bytes, 0))}</span>
                            {fs.map((f) => (
                              <span key={f.path} className="flex gap-2 text-muted">
                                <code className="min-w-0 flex-1 truncate" title={f.path}>{f.path}</code>
                                <span>{fmtBytes(f.bytes)}</span>
                              </span>
                            ))}
                          </div>
                        );
                      })}
                      {!!p.kept_renders.length && (
                        <div className="flex flex-col gap-0.5 text-go-text">
                          <span className="font-semibold">{t("Mantidos")}</span>
                          {p.kept_renders.map((r, i) => <span key={i}>{t(RENDER_TARGETS[r.profile] ?? r.profile)} · {r.language}: {r.reason}</span>)}
                        </div>
                      )}
                    </div>
                  </details>
                </div>
              </div>
            ))}
          </div>
        )}

        {projects.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 border-t border-line-soft pt-4">
            <span className="flex-1 text-sm text-body">{t("Total a liberar:")} <strong>{fmtBytes(total)}</strong></span>
            {!confirming ? (
              <button className="btn btn-danger" disabled={!picked.size || loading || running || preview?.days !== days} onClick={() => setConfirming(true)}>{t("Apagar {size}", { size: fmtBytes(total) })}</button>
            ) : (
              <span className="flex flex-wrap items-center gap-2" role="alert">
                <span className="text-sm font-semibold text-alert-text">{t("Tem certeza? Não dá para desfazer")}</span>
                <button className="btn btn-danger" disabled={running} onClick={run}>{running ? t("Apagando…") : t("Sim, apagar {size}", { size: fmtBytes(total) })}</button>
                <button className="btn" disabled={running} onClick={() => setConfirming(false)}>{t("Cancelar")}</button>
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
