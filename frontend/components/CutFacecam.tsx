"use client";

/** The streamer's camera in the cut editor (backend cvm/lives/facecam.py + cvm/render/stack.py): a box over the
 *  player, found by the analysis or drawn by the owner, that becomes the top (or middle) panel of every Short of this
 *  video — camera + header + gameplay. Saved on its own (PUT /cuts/{id}/layout). */

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { apiDetail, relayoutProject, saveCutLayout, type CutFacecam, type CutOrder, type CutReviewSummary } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Rect = { x: number; y: number; w: number; h: number };
type Mode = "move" | "nw" | "ne" | "sw" | "se" | "draw";
const MIN = 0.05;
const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));

export type FacecamState = {
  rect: Rect | null; enabled: boolean; order: CutOrder; origin: "auto" | "owner" | "channel" | null;
  drawing: boolean; save: "idle" | "saving" | "saved" | "error"; error: string;
  setRect: (r: Rect | null) => void; commit: (body: { facecam?: Rect | null; order?: CutOrder; enabled?: boolean }) => void;
  setDrawing: (v: boolean) => void;
};

export function useFacecam(reviewId: number, options: CutReviewSummary["options"] | undefined,
  onSaved?: (s: CutReviewSummary) => void): FacecamState {
  const fc: CutFacecam | null | undefined = options?.facecam;
  const [rect, setRect] = useState<Rect | null>(fc ? { x: fc.x, y: fc.y, w: fc.w, h: fc.h } : null);
  const [enabled, setEnabled] = useState(!options?.facecam_off);
  const [order, setOrder] = useState<CutOrder>(options?.facecam_order ?? "youtuber_top");
  const [origin, setOrigin] = useState<FacecamState["origin"]>(fc ? (fc.from === "channel" ? "channel" : fc.auto ? "auto" : "owner") : null);
  const [drawing, setDrawing] = useState(false);
  const [save, setSave] = useState<FacecamState["save"]>("idle");
  const [error, setError] = useState("");
  const loaded = useRef(false);
  useEffect(() => { // the first options from the server (the page loads them after mounting)
    if (loaded.current || !options) return;
    loaded.current = true;
    const f = options.facecam;
    setRect(f ? { x: f.x, y: f.y, w: f.w, h: f.h } : null);
    setEnabled(!options.facecam_off);
    setOrder(options.facecam_order ?? "youtuber_top");
    setOrigin(f ? (f.from === "channel" ? "channel" : f.auto ? "auto" : "owner") : null);
  }, [options]);

  const commit = useCallback(async (body: { facecam?: Rect | null; order?: CutOrder; enabled?: boolean }) => {
    if (body.order) setOrder(body.order);
    if (body.enabled !== undefined) setEnabled(body.enabled);
    if (body.facecam !== undefined) { setRect(body.facecam); setOrigin(body.facecam ? "owner" : null); }
    setSave("saving");
    try {
      const r = await saveCutLayout(reviewId, body);
      setSave("saved");
      setError("");
      onSaved?.(r);
    } catch (e) {
      setSave("error");
      setError(apiDetail(e));
    }
  }, [reviewId, onSaved]);

  return { rect, enabled, order, origin, drawing, save, error, setRect, commit, setDrawing };
}

/** Where the picture really is inside the <video> box (object-fit: contain), in px of the box. */
function contentBox(v: HTMLVideoElement | null) {
  if (!v) return null;
  const cw = v.clientWidth, ch = v.clientHeight;
  const ar = v.videoWidth && v.videoHeight ? v.videoWidth / v.videoHeight : 16 / 9;
  const w = Math.min(cw, ch * ar), h = w / ar;
  return { left: (cw - w) / 2, top: (ch - h) / 2, width: w, height: h };
}

/** The box over the player: drag inside to move, the corners to resize; in "draw" mode drag anywhere for a new one. */
export function FacecamOverlay({ state, video }: { state: FacecamState; video: RefObject<HTMLVideoElement | null> }) {
  const { t } = useT();
  const [box, setBox] = useState(() => contentBox(null));
  const drag = useRef<{ mode: Mode; x0: number; y0: number; r0: Rect } | null>(null);
  const area = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const update = () => setBox(contentBox(v));
    update();
    v.addEventListener("loadedmetadata", update);
    const ro = new ResizeObserver(update);
    ro.observe(v);
    return () => { v.removeEventListener("loadedmetadata", update); ro.disconnect(); };
  }, [video]);

  if (!box || (!state.drawing && (!state.rect || !state.enabled))) return null;

  function point(e: React.PointerEvent) {
    const r = area.current!.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
  }
  function down(e: React.PointerEvent, mode: Mode) {
    e.stopPropagation();
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = point(e);
    const r0 = mode === "draw" ? { x: p.x, y: p.y, w: 0, h: 0 } : state.rect!;
    drag.current = { mode, x0: p.x, y0: p.y, r0 };
    if (mode === "draw") state.setRect(r0);
  }
  function move(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const p = point(e);
    const dx = p.x - d.x0, dy = p.y - d.y0, r = d.r0;
    if (d.mode === "draw") {
      state.setRect({ x: Math.min(d.x0, p.x), y: Math.min(d.y0, p.y), w: Math.abs(dx), h: Math.abs(dy) });
    } else if (d.mode === "move") {
      state.setRect({ ...r, x: clamp(r.x + dx, 0, 1 - r.w), y: clamp(r.y + dy, 0, 1 - r.h) });
    } else {
      let { x, y, w, h } = r;
      if (d.mode.includes("w")) { const nx = clamp(r.x + dx, 0, r.x + r.w - MIN); w = r.x + r.w - nx; x = nx; }
      if (d.mode.includes("e")) w = clamp(r.w + dx, MIN, 1 - r.x);
      if (d.mode.includes("n")) { const ny = clamp(r.y + dy, 0, r.y + r.h - MIN); h = r.y + r.h - ny; y = ny; }
      if (d.mode.includes("s")) h = clamp(r.h + dy, MIN, 1 - r.y);
      state.setRect({ x, y, w, h });
    }
  }
  function up() {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    const r = state.rect;
    if (d.mode === "draw") state.setDrawing(false);
    if (r && r.w >= MIN && r.h >= MIN) {
      state.commit({ facecam: { x: +r.x.toFixed(4), y: +r.y.toFixed(4), w: +r.w.toFixed(4), h: +r.h.toFixed(4) }, enabled: true });
    } else if (d.mode === "draw") {
      state.setRect(null);
    }
  }

  const r = state.rect;
  return (
    <div
      ref={area}
      className={`absolute ${state.drawing ? "cursor-crosshair" : "pointer-events-none"}`}
      style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
      onPointerDown={state.drawing ? (e) => down(e, "draw") : undefined}
      onPointerMove={move}
      onPointerUp={up}
    >
      {r && (
        <div
          role="group"
          aria-label={t("Câmera do youtuber")}
          className="pointer-events-auto absolute cursor-move rounded-[4px] border-2 border-[#FFD21E] bg-[rgba(255,210,30,0.12)]"
          style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.w * 100}%`, height: `${r.h * 100}%` }}
          onPointerDown={(e) => down(e, "move")}
          onPointerMove={move}
          onPointerUp={up}
        >
          <span className="pointer-events-none absolute -top-6 left-0 rounded-[4px] bg-[#FFD21E] px-1.5 text-[12px] font-semibold leading-5 whitespace-nowrap text-ink">
            {t("Câmera do youtuber")}
          </span>
          {(["nw", "ne", "sw", "se"] as const).map((c) => (
            <span
              key={c}
              aria-hidden
              onPointerDown={(e) => down(e, c)}
              onPointerMove={move}
              onPointerUp={up}
              className="absolute h-4 w-4 rounded-[3px] border-2 border-ink bg-[#FFD21E]"
              style={{
                left: c.includes("w") ? -8 : undefined, right: c.includes("e") ? -8 : undefined,
                top: c.includes("n") ? -8 : undefined, bottom: c.includes("s") ? -8 : undefined,
                cursor: c === "nw" || c === "se" ? "nwse-resize" : "nesw-resize",
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** Tiny 9:16 schematic of one order: the camera panel in yellow, the header dark, the game striped. */
function Schematic({ order }: { order: CutOrder }) {
  const camY = order === "youtuber_top" ? 0 : 9;
  const headY = order === "youtuber_top" ? 13 : 0;
  return (
    <svg width="18" height="32" viewBox="0 0 18 32" aria-hidden className="flex-none rounded-[3px] border border-field">
      <rect x="0" y={camY} width="18" height="13" fill="#FFD21E" />
      <rect x="0" y={headY} width="18" height="9" fill="#111214" />
      <rect x="0" y="22" width="18" height="10" fill="#5b6068" />
      <rect x="2" y="24" width="5" height="3" fill="#c9ccd1" />
    </svg>
  );
}

/** The controls beside the player: on/off, redraw, order. */
export function FacecamControls({ state }: { state: FacecamState }) {
  const { t } = useT();
  const { rect, enabled, order, origin, drawing } = state;
  const status = !rect
    ? t("Não achei a câmera neste vídeo. Clique em Marcar a câmera e arraste sobre ela no vídeo.")
    : origin === "auto" ? t("Achei a câmera sozinho. Confira o retângulo amarelo no vídeo e ajuste se precisar.")
    : origin === "channel" ? t("Câmera marcada no canal.") : t("Câmera marcada por você.");
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3" aria-label={t("Câmera do youtuber")} role="group">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold text-ink">{t("Câmera do youtuber")}</span>
        {rect && (
          <label className="flex items-center gap-2 text-sm text-body">
            <input type="checkbox" className="h-5 w-5 accent-[#00843d]" checked={enabled}
              onChange={(e) => state.commit({ enabled: e.target.checked })} />
            {t("Mostrar no Short")}
          </label>
        )}
        <span className="ml-auto flex gap-2">
          {drawing ? (
            <button className="btn btn-sm" onClick={() => state.setDrawing(false)}>{t("Cancelar")}</button>
          ) : (
            <button className="btn btn-sm" onClick={() => state.setDrawing(true)}>{rect ? t("Desenhar de novo") : t("Marcar a câmera")}</button>
          )}
          {rect && !drawing && <button className="btn btn-sm" onClick={() => state.commit({ facecam: null })}>{t("Sem câmera")}</button>}
        </span>
      </div>
      <p className="m-0 text-[13px] text-muted" aria-live="polite">
        {drawing ? t("Arraste sobre o vídeo para marcar a câmera.") : status}
        {state.save === "saving" && <> · {t("Salvando…")}</>}
        {state.save === "saved" && <> · {t("Salvo")}</>}
        {state.save === "error" && <span className="text-alert-text"> · {t("Não salvou: {e}", { e: state.error })}</span>}
      </p>
      {rect && enabled && (
        <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label={t("Ordem no Short")}>
          <span className="text-[13px] font-semibold text-body">{t("Ordem no Short")}</span>
          <div className="inline-flex gap-1 rounded-xl bg-[#e9eae6] p-1">
            {(["youtuber_top", "header_top"] as const).map((o) => (
              <button
                key={o}
                role="radio"
                aria-checked={order === o}
                onClick={() => order !== o && state.commit({ order: o })}
                className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-[10px] border-0 px-3 text-sm font-semibold ${order === o ? "bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.08)]" : "bg-transparent text-body"}`}
              >
                <Schematic order={o} />
                <span className="flex flex-col items-start leading-tight">
                  {o === "youtuber_top" ? t("Youtuber em cima") : t("Título em cima")}
                  <span className="text-[12px] font-normal text-muted">
                    {o === "youtuber_top" ? t("câmera, título, jogo") : t("título, câmera, jogo")}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      {(!rect || !enabled) && (
        <p className="m-0 text-[13px] text-muted">{t("Sem a câmera, o Short sai com o título em cima e o vídeo embaixo.")}</p>
      )}
    </div>
  );
}

/** "Refazer com o layout novo": the Shorts already made from this video are composed again with the camera. */
export function RelayoutButton({ shorts, onDone }: { shorts: number[]; onDone: (msg: string) => void }) {
  const { t } = useT();
  const [busy, setBusy] = useState(false);
  if (!shorts.length) return null;
  async function run() {
    setBusy(true);
    const errors: string[] = [];
    let ok = 0;
    for (const pid of shorts) {
      try {
        await relayoutProject(pid);
        ok += 1;
      } catch (e) {
        errors.push(`#${pid}: ${apiDetail(e)}`);
      }
    }
    setBusy(false);
    onDone(errors.length
      ? t("Refiz {ok} de {n}. {e}", { ok, n: shorts.length, e: errors.join(" · ") })
      : t("Pronto. {n} Shorts voltaram para a montagem com a câmera.", { n: ok }));
  }
  return (
    <button className="btn btn-sm" disabled={busy} onClick={run}>
      {busy ? t("Refazendo…") : t("Refazer os Shorts com a câmera")}
    </button>
  );
}
