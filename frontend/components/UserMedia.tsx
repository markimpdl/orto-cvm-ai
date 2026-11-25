"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API, api } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type Segment = { start: number; end: number };
export type Media = {
  id: number; kind: "image" | "video"; filename: string; description: string; duration?: number | null; file_url: string;
  segments?: Segment[]; whole?: boolean; dropped?: { dull: number; repeat: number };
  has_audio?: boolean; keep_audio?: boolean; slide?: number;
};

export const MEDIA_CUTS = [
  { k: "full", label: "Vídeos completos", hint: "cada vídeo aparece inteiro" },
  { k: "highlights", label: "Melhores momentos", hint: "corta partes repetidas e sem graça; se tudo for bom, usa o vídeo inteiro" },
] as const;

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

export const MEDIA_MODES = [
  { k: "mix", label: "Misturar", hint: "seus arquivos onde combinam, e o resto da internet/IA" },
  { k: "mine", label: "Só meus arquivos", hint: "todas as cenas usam o que você enviou (avatar e gráficos continuam)" },
  { k: "online", label: "Só internet e IA", hint: "ignora seus arquivos" },
] as const;

/** Labels above are Portuguese source strings: render them through t(). */

/** Sends files as multipart (the api() helper sends JSON). */
export async function uploadMedia(path: string, file: File, description = ""): Promise<unknown> {
  const body = new FormData();
  body.append("file", file);
  body.append("description", description);
  const res = await fetch(`${API}${path}`, { method: "POST", body });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export function useMedia(projectId: number) {
  const [media, setMedia] = useState<Media[]>([]);
  const load = useCallback(() => api<Media[]>(`/projects/${projectId}/media`).then(setMedia).catch(() => {}), [projectId]);
  useEffect(() => {
    load();
  }, [load]);
  return { media, load };
}

/** A video preview that only streams while the pointer is on it. Autoplaying every source clip (some are hours long)
 * took all of the browser's connections to the API, and the render under review stalled ("fica girando"). */
export function HoverVideo({ src, className = "" }: { src: string; className?: string }) {
  return (
    <video
      src={`${src}#t=0.5`}
      muted
      loop
      playsInline
      preload="metadata"
      onMouseEnter={(e) => e.currentTarget.play().catch(() => {})}
      onMouseLeave={(e) => e.currentTarget.pause()}
      className={className}
    />
  );
}

export function MediaThumb({ m, className = "" }: { m: Media; className?: string }) {
  return m.kind === "video" ? (
    <HoverVideo src={`${API}${m.file_url}`} className={`object-cover ${className}`} />
  ) : (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={`${API}${m.file_url}`} alt={m.description || m.filename} className={`object-cover ${className}`} />
  );
}

/** "Seus arquivos" on the project page: your clips/photos for this video and how the video should use them. */
export default function UserMedia({ projectId, mode, onMode, cut, onCut, canScript, onRewrite }: {
  projectId: number; mode: string; onMode: (m: string) => void; cut: string; onCut: (c: string) => void;
  canScript: boolean; onRewrite?: () => void;
}) {
  const { media, load } = useMedia(projectId);
  const pending = cut === "highlights" && media.some((m) => m.kind === "video" && !m.segments);
  useEffect(() => { // best moments are counted in the background: poll until every video has them
    if (!pending) return;
    const h = setInterval(load, 4000);
    return () => clearInterval(h);
  }, [pending, load]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const { t } = useT();

  async function add(files: FileList | null) {
    if (!files?.length) return;
    setErr(null);
    for (const [i, f] of Array.from(files).entries()) {
      setBusy(t("Enviando {i}/{n}…", { i: i + 1, n: files.length }));
      try {
        await uploadMedia(`/projects/${projectId}/media`, f);
      } catch (e) {
        setErr(`${f.name}: ${e}`);
      }
    }
    setBusy(null);
    if (input.current) input.current.value = "";
    load();
  }

  async function describe(m: Media, description: string) {
    if (description === m.description) return;
    await api(`/projects/${projectId}/media/${m.id}`, { method: "PATCH", body: JSON.stringify({ description }) });
    load();
  }

  async function keepAudio(m: Media, keep_audio: boolean) {
    await api(`/projects/${projectId}/media/${m.id}`, { method: "PATCH", body: JSON.stringify({ keep_audio }) });
    load();
  }

  async function remove(m: Media) {
    if (!confirm(t("Remover “{name}” deste projeto?", { name: m.filename }))) return;
    await api(`/projects/${projectId}/media/${m.id}`, { method: "DELETE" });
    load();
  }

  return (
    <section className="card flex flex-col gap-4 p-5" aria-labelledby="h-media">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="h-media" className="h2 m-0 flex-1">{t("Seus arquivos")}</h2>
        <input ref={input} type="file" multiple accept="video/*,image/*,application/pdf,.pdf" className="hidden" onChange={(e) => add(e.target.files)} />
        <button className="btn btn-sm" disabled={!!busy} onClick={() => input.current?.click()}>{busy ?? `+ ${t("Enviar vídeos e imagens")}`}</button>
      </div>
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("Imagens do vídeo")}>
        {MEDIA_MODES.map((o) => (
          <button
            key={o.k}
            type="button"
            role="radio"
            aria-checked={mode === o.k}
            onClick={() => onMode(o.k)}
            className={`pick flex flex-[1_1_200px] flex-col items-start gap-0.5 rounded-xl px-3.5 py-2.5 text-left ${mode === o.k ? "border-2 border-go bg-go-tint" : "border border-[#d9dbdf] bg-surface"}`}
          >
            <span className="text-sm font-semibold">{t(o.label)}</span>
            <span className="text-xs text-muted">{t(o.hint)}</span>
          </button>
        ))}
      </div>
      {media.some((m) => m.kind === "video") && (
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("Uso dos vídeos")}>
          {MEDIA_CUTS.map((o) => (
            <button
              key={o.k}
              type="button"
              role="radio"
              aria-checked={cut === o.k}
              onClick={() => onCut(o.k)}
              className={`pick flex flex-[1_1_200px] flex-col items-start gap-0.5 rounded-xl px-3.5 py-2.5 text-left ${cut === o.k ? "border-2 border-go bg-go-tint" : "border border-[#d9dbdf] bg-surface"}`}
            >
              <span className="text-sm font-semibold">{t(o.label)}</span>
              <span className="text-xs text-muted">{t(o.hint)}</span>
            </button>
          ))}
        </div>
      )}
      {pending && <p className="m-0 text-[13px] text-muted">{t("Procurando os melhores momentos…")}</p>}
      {err && <p className="m-0 text-sm text-alert-text">{err}</p>}
      {media.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {media.map((m) => (
            <div key={m.id} className="flex flex-col gap-1.5">
              <MediaThumb m={m} className="aspect-video w-full rounded-[10px] bg-ink" />
              <input
                className="input text-[13px]"
                defaultValue={m.description}
                placeholder={t("O que aparece? (ajuda o roteiro)")}
                onBlur={(e) => describe(m, e.target.value)}
                aria-label={t("Descrição de {name}", { name: m.filename })}
              />
              <span className="flex items-center gap-2 text-xs text-muted">
                <span className="flex-1 truncate" title={m.filename}>{m.kind === "video" ? `${t("vídeo")}${m.duration ? ` · ${m.duration}s` : ""}` : t("imagem")} · {m.filename}</span>
                <button className="btn btn-sm" onClick={() => remove(m)} aria-label={t("Remover {name}", { name: m.filename })}>✕</button>
              </span>
              {m.kind === "video" && (
                <label className="flex min-h-9 items-center gap-2 text-xs" title={t("Nas cenas deste vídeo toca o som dele (sua fala); no resto, a narração gerada")}>
                  <input type="checkbox" className="h-4 w-4 accent-go" checked={!!m.keep_audio} onChange={(e) => keepAudio(m, e.target.checked)} />
                  {t("Manter o áudio original")}{m.has_audio === false ? ` (${t("sem som")})` : ""}
                </label>
              )}
              {cut === "highlights" && m.kind === "video" && m.segments && (
                <span className={`text-xs ${m.segments.length ? "text-ink" : "text-alert-text"}`}>
                  {m.whole
                    ? t("Inteiro")
                    : m.segments.length
                      ? `✂ ${m.segments.map((g) => `${mmss(g.start)}–${mmss(g.end)}`).join(", ")}`
                      : m.dropped && m.dropped.repeat > m.dropped.dull ? t("Fora: repete outro vídeo") : t("Fora: sem momentos fortes")}
                </span>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="m-0 text-sm text-muted">{t("Envie trechos de vídeo e fotos seus. Uma descrição curta em cada um ajuda o Claude a colocar na cena certa.")}</p>
      )}
      {onRewrite && !!media.length && (
        <button className="btn btn-go self-start" onClick={onRewrite}>{t("Reescrever o roteiro a partir dos meus arquivos")}</button>
      )}
      <p className="m-0 text-[13px] text-muted">
        {canScript
          ? t("O roteiro usa seus arquivos quando for escrito. Se ele já estiver pronto, peça um novo roteiro ou troque as cenas na linha do tempo.")
          : t("Para usar agora: troque cenas na linha do tempo (clique numa cena), ou mude a opção acima e use “Refazer narração e vídeo”.")}
      </p>
    </section>
  );
}
