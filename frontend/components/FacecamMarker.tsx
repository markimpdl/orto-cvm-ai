"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API, api } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Streamer camera of a channel (Channel.prefs.facecam, backend cvm/render/stack.py): drawn once over a frame of one
 *  of the channel's recent videos. Lives of this channel then come out with the camera on top and the game below. */
type Rect = { x: number; y: number; w: number; h: number };
type Facecam = { facecam: Rect | null; landscape: boolean; sources: { index: number; name: string }[] };

const AT = [0.3, 0.5, 0.7, 0.15]; // "Outro quadro" walks through these points of the video
const clamp = (v: number) => Math.min(1, Math.max(0, v));

export default function FacecamMarker({ channelId }: { channelId: number }) {
  const { t } = useT();
  const [data, setData] = useState<Facecam | null>(null);
  const [editing, setEditing] = useState(false);
  const [source, setSource] = useState(0);
  const [at, setAt] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [landscape, setLandscape] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const start = useRef<{ x: number; y: number } | null>(null);

  const load = useCallback(() => api<Facecam>(`/channels/${channelId}/facecam`).then((d) => {
    setData(d);
    setRect(d.facecam);
    setLandscape(d.landscape);
  }).catch(() => setData(null)), [channelId]);
  useEffect(() => { load(); }, [load]);

  function point(e: React.PointerEvent) {
    const r = box.current!.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
  }
  function down(e: React.PointerEvent) {
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = point(e);
    setRect({ ...start.current, w: 0, h: 0 });
  }
  function move(e: React.PointerEvent) {
    if (!start.current) return;
    const p = point(e), s = start.current;
    setRect({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) });
  }
  function up() {
    start.current = null;
  }

  async function save(facecam: Rect | null) {
    setBusy(true);
    setMsg(null);
    try {
      await api(`/channels/${channelId}/facecam`, { method: "PUT", body: JSON.stringify({ facecam, landscape }) });
      await load();
      setEditing(false);
      setMsg({ ok: true, text: facecam ? t("Câmera salva.") : t("Câmera removida.") });
    } catch (e) {
      const m = /^Error: \d+ ([\s\S]*)$/.exec(String(e));
      let text = String(e);
      try { text = m ? JSON.parse(m[1]).detail ?? text : text; } catch { /* plain text */ }
      setMsg({ ok: false, text });
    } finally {
      setBusy(false);
    }
  }

  if (!data) return null;
  const small = !rect || rect.w < 0.05 || rect.h < 0.05;
  const frame = `${API}/channels/${channelId}/facecam/frame?source=${source}&at=${AT[at]}`;

  return (
    <section className="flex flex-col gap-3">
      <h2 className="h2 m-0">{t("Câmera do streamer")}</h2>
      <div className="card flex flex-col gap-4 p-5">
        <p className="m-0 text-[15px] text-body">
          {data.facecam
            ? t("Câmera marcada. Os melhores momentos das lives deste canal saem com a câmera em cima e o jogo embaixo nos Shorts.")
            : t("Sem câmera marcada: as lives deste canal saem no layout normal. Marque onde fica a sua câmera para os Shorts terem a câmera em cima e o jogo embaixo.")}
        </p>
        {!data.sources.length && (
          <p className="m-0 text-[13px] text-muted">{t("Envie ou cole o link de uma live deste canal primeiro. Depois dá para marcar a câmera num quadro dela.")}</p>
        )}
        {!editing && !!data.sources.length && (
          <div className="flex flex-wrap gap-2.5">
            <button type="button" className="btn" onClick={() => { setEditing(true); setLoaded(false); setMsg(null); }}>
              {data.facecam ? t("Ajustar a câmera") : t("Marcar a câmera")}
            </button>
            {data.facecam && <button type="button" className="btn btn-danger" disabled={busy} onClick={() => save(null)}>{t("Remover câmera")}</button>}
          </div>
        )}
        {editing && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2.5">
              {data.sources.length > 1 && (
                <select className="input min-w-0 flex-1 font-normal" aria-label={t("Vídeo")} value={source}
                  onChange={(e) => { setSource(Number(e.target.value)); setLoaded(false); }}>
                  {data.sources.map((s) => <option key={s.index} value={s.index}>{s.name}</option>)}
                </select>
              )}
              <button type="button" className="btn btn-sm" onClick={() => { setAt((a) => (a + 1) % AT.length); setLoaded(false); }}>{t("Outro quadro")}</button>
            </div>
            <span className="text-[13px] text-muted">{t("Arraste sobre a imagem para cobrir só a sua câmera.")}</span>
            <div ref={box} className="relative w-full max-w-[720px] cursor-crosshair touch-none select-none overflow-hidden rounded-xl bg-chip"
              onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
              {/* eslint-disable-next-line @next/next/no-img-element -- a frame served by the local API */}
              <img src={frame} alt={t("Quadro do vídeo")} className="block w-full" draggable={false} onLoad={() => setLoaded(true)}
                onError={() => setMsg({ ok: false, text: t("Não consegui tirar um quadro deste vídeo.") })} />
              {!loaded && <span className="absolute inset-0 flex items-center justify-center text-muted">{t("Carregando…")}</span>}
              {rect && rect.w > 0 && (
                <div className="pointer-events-none absolute border-2 border-go bg-go/15"
                  style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }} />
              )}
            </div>
            <label className="flex min-h-10 items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 accent-go" checked={landscape} onChange={(e) => setLandscape(e.target.checked)} />
              {t("Usar também nos vídeos 16:9 (câmera ampliada no canto)")}
            </label>
            <div className="flex flex-wrap gap-2.5">
              <button type="button" className="btn btn-go" disabled={busy || small} onClick={() => rect && save(rect)}>{t("Salvar câmera")}</button>
              <button type="button" className="btn" onClick={() => { setEditing(false); setRect(data.facecam); setLandscape(data.landscape); }}>{t("Cancelar")}</button>
            </div>
          </div>
        )}
        {msg && <p className={`m-0 text-[15px] ${msg.ok ? "text-go-text" : "text-alert-text"}`} role="status">{msg.text}</p>}
      </div>
    </section>
  );
}
