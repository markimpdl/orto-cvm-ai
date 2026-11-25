"use client";

import { useEffect, useState } from "react";
import { HoverVideo, MediaThumb, uploadMedia, useMedia } from "@/components/UserMedia";
import { API, api, type Scene } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Visual = {
  id: number;
  scene: number;
  source: string | null;
  license: string | null;
  file_url: string | null;
  meta: { type: string; kind: string; credit?: string; prompt?: string; url?: string; candidates?: { preview?: string; source: string; kind: string; url: string }[] };
};

const KIND_LABEL: Record<string, string> = {
  ai_image: "Imagem IA", footage: "Vídeo", broll: "Vídeo", headline: "Manchete", chart: "Gráfico",
  avatar_full: "Avatar", avatar_pip: "Avatar", source_clip: "Trecho",
};

/** 9:16 scene cards scrolling sideways; when editable, clicking a card offers the other candidates. */
export default function SceneTimeline({ projectId, scenes, editable, refreshKey }: { projectId: number; scenes: Scene[]; editable: boolean; refreshKey?: string }) {
  const [items, setItems] = useState<Visual[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const { media, load: loadMedia } = useMedia(projectId);
  const { t } = useT();

  const load = () => api<Visual[]>(`/projects/${projectId}/visuals`).then(setItems).catch(() => {});
  useEffect(() => {
    load();
  }, [projectId, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function sendFile(scene: number, f: File | undefined) {
    if (!f) return;
    setSending(true);
    try {
      await uploadMedia(`/projects/${projectId}/visuals/${scene}/upload`, f);
      setOpen(null);
      load();
      loadMedia();
    } finally {
      setSending(false);
    }
  }

  async function choose(scene: number, body: object) {
    await api(`/projects/${projectId}/visuals/${scene}/choose`, { method: "POST", body: JSON.stringify(body) });
    setOpen(null);
    load();
  }

  const byScene = Object.fromEntries(items.map((v) => [v.scene, v]));
  const cards = scenes.length ? scenes.map((s) => ({ n: s.index, type: s.visual.type, v: byScene[s.index] })) : items.map((v) => ({ n: v.scene, type: v.meta.type, v }));
  if (!cards.length) return null;
  const openV = open !== null ? byScene[open] : undefined;

  return (
    <section aria-labelledby="h-tl" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="h-tl" className="h2 m-0 mr-auto">{t("Linha do tempo de cenas")}</h2>
        {editable && <span className="text-[13px] text-muted">{t("Clique numa cena para trocar o visual (inclusive por um arquivo seu)")}</span>}
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1.5">
        {cards.map(({ n, type, v }) => {
          const label = t(KIND_LABEL[type] ?? type);
          const clickable = editable && !!v;
          return (
            <button
              key={n}
              type="button"
              disabled={!clickable}
              aria-pressed={open === n}
              onClick={() => setOpen(open === n ? null : n)}
              className={`pick flex w-[168px] min-w-0 flex-none flex-col gap-2 rounded-[14px] border bg-surface p-2.5 text-ink disabled:cursor-default ${open === n ? "border-2 border-go" : "border-line"} ${clickable ? "hover:border-ink" : ""}`}
            >
              <span className="flex w-full text-[13px]"><strong className="flex-1">{t("Cena {n}", { n })}</strong><span className="text-muted">{type.startsWith("avatar") ? "avatar" : type}</span></span>
              {v ? (
                <span className="flex aspect-[9/16] w-full items-center justify-center overflow-hidden rounded-[10px] bg-ink-hover text-center text-xs text-[#f5f5f2]">
                  {v.file_url && v.meta.kind === "video" ? (
                    <HoverVideo src={`${API}${v.file_url}`} className="h-full w-full object-cover" />
                  ) : v.file_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={`${API}${v.file_url}`} alt={`${t("Cena {n}", { n })}: ${label}`} className="h-full w-full object-cover" />
                  ) : (
                    <span className="flex flex-col gap-1.5 p-2.5">
                      <span className="font-semibold tracking-[0.4px] uppercase">{label}</span>
                      <span>{type.startsWith("avatar") ? t("avatar falando") : t("cartão de texto")}</span>
                    </span>
                  )}
                </span>
              ) : (
                <span className="flex aspect-[9/16] w-full flex-col items-center justify-center gap-1.5 rounded-[10px] border border-dashed border-field bg-bg p-2.5 text-center text-muted">
                  <span className="text-xs font-semibold tracking-[0.4px] uppercase">{label}</span>
                  <span className="text-[13px]">{t("buscando…")}</span>
                </span>
              )}
              <span className="w-full truncate text-xs text-muted" title={v?.license ?? ""}>{v?.source ?? "—"}</span>
            </button>
          );
        })}
      </div>
      {openV && (
        <div className="card flex flex-col gap-3 p-4">
          <span className="text-sm font-semibold">{t("Trocar o visual da cena {n}", { n: openV.scene })}</span>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(110px,1fr))] gap-2">
            {!!openV.meta.candidates?.length && <span className="text-[13px] text-muted">{t("Da internet e IA")}</span>}
          {(openV.meta.candidates ?? []).map((c, i) => (
              <button key={c.url} type="button" onClick={() => choose(openV.scene, { candidate: i })} className="pick overflow-hidden rounded-[10px] border border-line p-0 hover:border-go" aria-label={t("Usar opção {n} ({source})", { n: i + 1, source: c.source })}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={c.preview} alt="" className="aspect-square w-full object-cover" />
                <span className="block truncate px-1.5 py-1 text-[11px] text-muted">{c.source}</span>
              </button>
            ))}
          </div>
          {!!media.length && (
            <>
              <span className="text-[13px] text-muted">{t("Seus arquivos")}</span>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(110px,1fr))] gap-2">
                {media.map((m) => (
                  <button key={m.id} type="button" onClick={() => choose(openV.scene, { media_id: m.id })} className="pick overflow-hidden rounded-[10px] border border-line p-0 hover:border-go" aria-label={t("Usar {name}", { name: m.filename })}>
                    <MediaThumb m={m} className="aspect-square w-full" />
                    <span className="block truncate px-1.5 py-1 text-[11px] text-muted">{m.description || m.filename}</span>
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="flex flex-wrap gap-2">
            <label className={`btn btn-sm cursor-pointer ${sending ? "opacity-60" : ""}`}>
              {sending ? t("Enviando…") : t("Enviar arquivo meu")}
              <input type="file" accept="video/*,image/*" className="hidden" disabled={sending} onChange={(e) => sendFile(openV.scene, e.target.files?.[0])} />
            </label>
            <button className="btn btn-sm" onClick={() => choose(openV.scene, { type: "card" })}>{t("Usar cartão de texto")}</button>
            <button className="btn btn-sm" onClick={() => setOpen(null)}>{t("Fechar")}</button>
          </div>
          <span className="text-[13px] text-muted">{t("Depois de trocar, clique em “Remontar vídeos” para o vídeo usar as imagens novas.")}</span>
        </div>
      )}
    </section>
  );
}
