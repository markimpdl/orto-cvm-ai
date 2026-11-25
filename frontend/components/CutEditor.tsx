"use client";

/** Cut editor (spec docs/superpowers/specs/2026-11-25-editor-de-cortes-design.md): the video, its lanes on one
 *  zoomable timeline (Cortes, Áudio, Fala, Replay, Chat, Comentários, Reações; a lane without data is hidden) and the
 *  panel of the selected cut. Edits are saved on their own (PUT /cuts/{id}/cuts, debounced); "Produzir aprovados"
 *  hands the approved cuts to the worker. Lane graphs are drawn on a canvas the width of the view only, so a 3 h
 *  live zoomed in never makes a huge canvas. */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAppData } from "@/components/AppData";
import { PageHeader } from "@/components/Bits";
import { IconBack, IconPause, IconPlay } from "@/components/Icons";
import {
  cutVideoUrl, dismissCuts, getCutReview, produceCuts, saveCuts,
  type Cut, type CutReviewDetail, type CutStatus,
} from "@/lib/api";
import { useT, type T } from "@/lib/i18n";
import { FacecamControls, FacecamOverlay, RelayoutButton, useFacecam } from "@/components/CutFacecam";

const LABEL_W = 104;
const RULER_H = 26;
const H = { cuts: 56, audio: 56, speech: 44, graph: 34 };
const MAX_PPS = 60; // px per second at the closest zoom
const SHORT_MIN = 15;
const SHORT_MAX = 60;
const INK = "#111214";
const MUTED = "#5b6068";
const LINE = "#e6e7ea";
const BODY = "#3b3f45";
const SCORE_KEYS = ["hook", "payoff", "emotion", "standalone", "trend"] as const;
const SCORE_LABEL: Record<(typeof SCORE_KEYS)[number], string> = {
  hook: "Gancho", payoff: "Desfecho", emotion: "Emoção ou valor", standalone: "Entende sozinho", trend: "Tendência",
};
const STATUS_LABEL: Record<CutStatus, string> = { suggested: "sugerido", proposed: "proposto", approved: "aprovado", discarded: "descartado" };
const STATUS_PILL: Record<CutStatus, string> = {
  suggested: "bg-go-tint text-go-text", proposed: "bg-chip text-body", approved: "bg-go-soft text-go-text", discarded: "bg-line-soft text-muted",
};
const BLOCK: Record<CutStatus, string> = {
  approved: "border-go bg-go text-white",
  suggested: "border-dashed border-go bg-go-soft text-go-text",
  proposed: "border-field bg-surface text-body",
  discarded: "border-line bg-chip text-muted line-through opacity-70",
};
const GRAPHS = [
  ["replay", "Replay", "#4b2a99"],
  ["chat", "Chat", "#8a4b00"],
  ["comments", "Comentários", "#3b3f45"],
  ["reactions", "Reações", "#a10016"],
] as const;
const TICKS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

export function fmtTime(t: number, tenths = false): string {
  const s = Math.max(0, t);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const ss = tenths ? sec.toFixed(1).padStart(4, "0") : String(Math.floor(sec)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** The warnings the backend gives (cvm/lives/cuts.warnings), worked out here too so they follow every edit. */
function cutWarnings(c: Cut, own: boolean, t: T): string[] {
  const out: string[] = [];
  const n = c.end - c.start;
  if (!own && !c.commentary && c.status !== "discarded")
    out.push(t("Vídeo de outro criador sem comentário: o YouTube pode tratar como conteúdo reutilizado e bloquear a monetização do canal."));
  if (n < SHORT_MIN) out.push(t("Curto para um Short ({n} s). O normal é de 15 a 60 s.", { n: Math.round(n) }));
  else if (n > SHORT_MAX) out.push(t("Longo para um Short ({n} s). O normal é de 15 a 60 s.", { n: Math.round(n) }));
  return out;
}

type Drag = { id: string; mode: "move" | "start" | "end" | "create"; x0: number; a0: number; b0: number; moved: boolean };

export default function CutEditor({ id }: { id: number }) {
  const { t } = useT();
  const { setToast, channelOf } = useAppData();
  const [d, setD] = useState<CutReviewDetail | null | undefined>(undefined);
  const [cuts, setCuts] = useState<Cut[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [save, setSave] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveErr, setSaveErr] = useState("");
  const [version, setVersion] = useState(0);
  const [makeLong, setMakeLong] = useState(true);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [pps, setPps] = useState(0); // 0 = fit the whole video
  const [viewW, setViewW] = useState(800);
  const [scrollX, setScrollX] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stopAt = useRef<number | null>(null);
  const drag = useRef<Drag | null>(null);
  const savedVersion = useRef(0);
  const facecam = useFacecam(id, d?.options);

  const load = useCallback(async () => {
    try {
      const r = await getCutReview(id);
      setD(r);
      setCuts((old) => (savedVersion.current === 0 || r.status !== "review" ? r.cuts : old));
      if (r.options?.make_long !== undefined) setMakeLong(!!r.options.make_long);
    } catch (e) {
      setD(String(e).includes("404") ? null : (prev) => prev ?? null);
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);
  const status = d?.status;
  useEffect(() => { // analysing / producing: follow until it changes
    if (status !== "analysing" && status !== "producing") return;
    const h = setInterval(load, 5000);
    return () => clearInterval(h);
  }, [status, load]);

  const duration = d?.duration || 1;
  const fit = Math.max(0.01, viewW / duration);
  const scale = pps ? Math.max(fit, Math.min(MAX_PPS, pps)) : fit;
  const total = Math.max(viewW, duration * scale);
  const viewFrom = scrollX / scale;
  const viewTo = (scrollX + viewW) / scale;

  // --- edits: local first, saved after a short pause ---
  const edit = useCallback((fn: (cs: Cut[]) => Cut[]) => {
    setCuts((cs) => fn(cs));
    setVersion((v) => v + 1);
  }, []);
  const cutsRef = useRef(cuts);
  cutsRef.current = cuts;
  const versionRef = useRef(version);
  versionRef.current = version;
  useEffect(() => {
    if (!version || drag.current) return;
    setSave("saving");
    const h = setTimeout(async () => {
      const mine = version;
      try {
        const r = await saveCuts(id, cutsRef.current);
        savedVersion.current = mine;
        setSave("saved");
        setSaveErr("");
        if (versionRef.current === mine) { // nothing changed meanwhile: take the server's ids for new cuts
          const local = cutsRef.current;
          setSel((s) => {
            const mineCut = local.find((c) => c.id === s);
            const match = mineCut && r.cuts.find((c) => c.id === s || (Math.abs(c.start - mineCut.start) < 0.01 && Math.abs(c.end - mineCut.end) < 0.01));
            return match ? match.id : s;
          });
          setCuts(r.cuts);
        }
      } catch (e) {
        setSave("error");
        setSaveErr(String(e).replace(/^Error: \d+ /, "").replace(/^\{"detail":"(.*)"\}$/, "$1"));
      }
    }, 700);
    return () => clearTimeout(h);
  }, [version, id]);

  const selected = cuts.find((c) => c.id === sel) ?? null;
  const update = useCallback((cid: string, patch: Partial<Cut>) => edit((cs) => cs.map((c) => (c.id === cid ? { ...c, ...patch } : c))), [edit]);

  // --- player ---
  const seek = useCallback((tt: number) => {
    const v = video.current;
    const x = Math.max(0, Math.min(duration, tt));
    if (v) v.currentTime = x;
    setNow(x);
  }, [duration]);
  useEffect(() => {
    const v = video.current;
    if (!v || !playing) return;
    let raf = 0;
    let last = 0;
    const tick = (ts: number) => {
      if (ts - last > 66) {
        last = ts;
        setNow(v.currentTime);
        if (stopAt.current != null && v.currentTime >= stopAt.current) {
          v.pause();
          stopAt.current = null;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);
  const toggle = useCallback(() => {
    const v = video.current;
    if (!v) return;
    stopAt.current = null;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }, []);
  const playCut = useCallback((c: Cut) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = c.start;
    stopAt.current = c.end;
    v.play().catch(() => {});
  }, []);

  // follow the playhead while playing
  useEffect(() => {
    const el = scroller.current;
    if (!el || !playing) return;
    const x = now * scale;
    if (x < el.scrollLeft || x > el.scrollLeft + viewW - 40) el.scrollLeft = Math.max(0, x - viewW * 0.2);
  }, [now, playing, scale, viewW]);

  // --- view size / scroll ---
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewW(el.clientWidth));
    ro.observe(el);
    setViewW(el.clientWidth);
    return () => ro.disconnect();
  }, [status]);
  const zoom = useCallback((f: number) => {
    const el = scroller.current;
    const next = Math.max(fit, Math.min(MAX_PPS, scale * f));
    setPps(next <= fit * 1.001 ? 0 : next);
    if (el) requestAnimationFrame(() => { el.scrollLeft = Math.max(0, now * next - viewW / 2); setScrollX(el.scrollLeft); });
  }, [fit, scale, now, viewW]);

  // --- keyboard ---
  const setEdge = useCallback((edge: "start" | "end") => {
    if (!selected) return;
    const tt = Math.round(now * 10) / 10;
    if (edge === "start" && tt < selected.end - 1) update(selected.id, { start: tt });
    if (edge === "end" && tt > selected.start + 1) update(selected.id, { end: Math.min(duration, tt) });
  }, [selected, now, update, duration]);
  useEffect(() => {
    if (status !== "review") return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (e.key === " ") { e.preventDefault(); toggle(); }
      else if (k === "i") setEdge("start");
      else if (k === "o") setEdge("end");
      else if (e.key === "ArrowLeft") { e.preventDefault(); seek(now - (e.shiftKey ? 5 : 1)); }
      else if (e.key === "ArrowRight") { e.preventDefault(); seek(now + (e.shiftKey ? 5 : 1)); }
      else if (k === "a" && selected) update(selected.id, { status: "approved" });
      else if (k === "d" && selected) update(selected.id, { status: "discarded" });
      else if (e.key === "+" || e.key === "=") zoom(1.6);
      else if (e.key === "-") zoom(1 / 1.6);
      else return;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [status, toggle, setEdge, seek, now, selected, update, zoom]);

  // --- cuts lane: move, resize, create ---
  const timeAt = (e: React.PointerEvent, lane: HTMLElement) => (e.clientX - lane.getBoundingClientRect().left) / scale;
  function onCutDown(e: React.PointerEvent, c: Cut) {
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const off = e.clientX - r.left;
    const mode = off < 8 ? "start" : off > r.width - 8 ? "end" : "move";
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { id: c.id, mode, x0: e.clientX, a0: c.start, b0: c.end, moved: false };
    setSel(c.id);
  }
  function onLaneDown(e: React.PointerEvent) {
    const lane = e.currentTarget as HTMLElement;
    lane.setPointerCapture(e.pointerId);
    const tt = timeAt(e, lane);
    drag.current = { id: `new-${Date.now()}`, mode: "create", x0: e.clientX, a0: tt, b0: tt, moved: false };
  }
  function onMove(e: React.PointerEvent) {
    const g = drag.current;
    if (!g) return;
    const dt = (e.clientX - g.x0) / scale;
    if (!g.moved && Math.abs(e.clientX - g.x0) < 3) return;
    g.moved = true;
    const r1 = (x: number) => Math.round(Math.max(0, Math.min(duration, x)) * 10) / 10;
    if (g.mode === "create") {
      const a = r1(Math.min(g.a0, g.a0 + dt));
      const b = r1(Math.max(g.a0, g.a0 + dt));
      setCuts((cs) => {
        const rest = cs.filter((c) => c.id !== g.id);
        const fresh: Cut = { id: g.id, origin: "owner", start: a, end: Math.max(b, a + 0.1), status: "approved", hook: "", why: "",
          scores: {}, score: null, rank: null, tags: "", comment: "", commentary: !!d?.options?.commentary };
        return [...rest, fresh];
      });
      setSel(g.id);
      return;
    }
    setCuts((cs) => cs.map((c) => {
      if (c.id !== g.id) return c;
      const len = g.b0 - g.a0;
      if (g.mode === "move") {
        const a = r1(Math.min(Math.max(0, g.a0 + dt), duration - len));
        return { ...c, start: a, end: r1(a + len) };
      }
      if (g.mode === "start") return { ...c, start: r1(Math.min(g.a0 + dt, c.end - 1)) };
      return { ...c, end: r1(Math.max(g.b0 + dt, c.start + 1)) };
    }));
  }
  function onUp(e: React.PointerEvent) {
    const g = drag.current;
    drag.current = null;
    if (!g) return;
    if (!g.moved) {
      if (g.mode === "create") seek(g.a0);
      return;
    }
    if (g.mode === "create") {
      const c = cutsRef.current.find((x) => x.id === g.id);
      if (!c || c.end - c.start < 1) {
        setCuts((cs) => cs.filter((x) => x.id !== g.id));
        setSel(null);
        if (!c) seek(timeAt(e, e.currentTarget as HTMLElement));
        return;
      }
    }
    setVersion((v) => v + 1);
  }

  const sorted = useMemo(() => [...cuts].sort((a, b) => a.start - b.start), [cuts]);
  const approvedN = cuts.filter((c) => c.status === "approved").length;
  const suggestedN = cuts.filter((c) => c.status === "suggested").length;
  const line = d?.transcript.find((l) => l.s <= now && now < l.e + 0.3);
  const own = d?.own ?? true;
  const canLong = d?.kind === "shorts" && own && duration >= 300;

  async function produce() {
    if (!d) return;
    setBusy(true);
    try {
      if (version > savedVersion.current) await saveCuts(id, cutsRef.current); // never produce from an unsaved list
      await produceCuts(id, canLong ? makeLong : false);
      setToast({ msg: t("Pronto. Os projetos dos cortes aprovados estão sendo criados.") });
      load();
    } catch (e) {
      setToast({ msg: String(e).replace(/^Error: \d+ /, "").replace(/^\{"detail":"(.*)"\}$/, "$1") });
    } finally {
      setBusy(false);
    }
  }
  async function dismiss() {
    if (!confirm(t("Descartar este vídeo? Nenhum corte vira Short, e as suas escolhas continuam ensinando as próximas análises."))) return;
    try {
      await dismissCuts(id);
      load();
    } catch (e) {
      setToast({ msg: String(e) });
    }
  }

  const back = (
    <Link href="/fabrica/cortes" className="btn btn-sm"><IconBack />{t("Clips e cortes")}</Link>
  );
  if (d === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (d === null) return <><PageHeader title={t("Cortes")}>{back}</PageHeader><div className="card mt-6 p-5 text-alert-text">{t("Não achei essa revisão de cortes.")}</div></>;

  const header = (
    <PageHeader
      title={d.title}
      subtitle={<>
        {channelOf(d.channel_id)?.name ?? t("sem canal")} · {fmtTime(d.duration)}
        {!d.own && <> · <span className="text-warn-text">🟡 {t("de {who}", { who: d.uploader ?? t("outro criador") })}</span></>}
        {d.url && <> · <a href={d.url} target="_blank" rel="noreferrer">{t("vídeo original")}</a></>}
      </>}
    >{back}</PageHeader>
  );
  if (d.status !== "review") {
    const done = d.result?.shorts ?? d.result?.projects ?? [];
    return (
      <>
        {header}
        <section className="card mt-6 flex flex-col gap-3 p-5">
          {d.status === "analysing" && <p className="m-0 text-body">{t("Analisando o vídeo. Num vídeo longo isso leva alguns minutos; a página se atualiza sozinha.")}</p>}
          {d.status === "producing" && <p className="m-0 text-body">{t("Criando os projetos dos cortes aprovados…")}</p>}
          {d.status === "error" && <p className="m-0 text-alert-text">{d.error ?? t("A análise falhou.")}</p>}
          {d.status === "dismissed" && <p className="m-0 text-body">{t("Você descartou este vídeo. Nenhum corte foi produzido.")}</p>}
          {d.status === "done" && (
            <>
              <p className="m-0 text-body">{t("Projetos criados. Eles seguem a fila normal e voltam para você aprovar o roteiro.")}</p>
              <div className="flex flex-wrap gap-2">
                {d.result?.long != null && <Link className="btn btn-sm" href={`/projetos/${d.result.long}`}>{t("Melhores momentos")}</Link>}
                {done.map((pid, i) => <Link key={pid} className="btn btn-sm" href={`/projetos/${pid}`}>{d.kind === "segments" ? t("Projeto {n}", { n: i + 1 }) : t("Short {n}", { n: i + 1 })}</Link>)}
              </div>
              {d.kind === "shorts" && (d.result?.shorts?.length ?? 0) > 0 && (
                <div className="flex flex-col gap-3 border-t border-line pt-3">
                  <p className="m-0 text-sm text-body">{t("A câmera do youtuber entra nos Shorts que ainda vão ser montados. Para os que já ficaram prontos, ajuste a câmera aqui e peça para refazer.")}</p>
                  <div className="relative max-w-2xl">
                    <video ref={video} src={cutVideoUrl(id)} preload="metadata" controls className="block aspect-video w-full rounded-xl bg-ink" />
                    <FacecamOverlay state={facecam} video={video} />
                  </div>
                  <FacecamControls state={facecam} />
                  <div><RelayoutButton shorts={d.result?.shorts ?? []} onDone={(msg) => setToast({ msg })} /></div>
                </div>
              )}
            </>
          )}
        </section>
      </>
    );
  }

  const lanes: { key: string; label: string; h: number }[] = [
    { key: "cuts", label: t("Cortes"), h: H.cuts },
    { key: "audio", label: t("Áudio"), h: H.audio },
    ...(d.transcript.length ? [{ key: "speech", label: t("Fala"), h: H.speech }] : []),
    ...GRAPHS.filter(([k]) => d.tracks[k]?.length).map(([k, label]) => ({ key: k, label: t(label), h: H.graph })),
  ];

  return (
    <>
      {header}
      {d.error && <div className="card mt-4 border-alert-soft bg-alert-soft px-5 py-3 text-sm text-alert-text">{d.error}</div>}
      {d.method === "volume" && (
        <p className="mt-3 mb-0 text-sm text-warn-text">{t("Estes cortes vieram só do volume e do movimento: a análise da fala não estava disponível. Confira com mais cuidado.")}</p>
      )}

      <div className="mt-5 grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]">
        <section className="card flex flex-col gap-3 p-4" aria-label={t("Vídeo")}>
          <div className="relative">
            <video
              ref={video}
              src={cutVideoUrl(id)}
              preload="metadata"
              className="block aspect-video w-full rounded-xl bg-ink"
              onPlay={() => setPlaying(true)}
              onPause={() => { setPlaying(false); setNow(video.current?.currentTime ?? 0); }}
              onSeeked={() => setNow(video.current?.currentTime ?? 0)}
              onClick={toggle}
            />
            {d.kind === "shorts" && <FacecamOverlay state={facecam} video={video} />}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn btn-sm" onClick={toggle} aria-label={playing ? t("Pausar") : t("Tocar")}>
              {playing ? <IconPause /> : <IconPlay />}
              {playing ? t("Pausar") : t("Tocar")}
            </button>
            <span className="font-semibold tabular-nums text-ink">{fmtTime(now, true)}</span>
            <span className="text-sm text-muted tabular-nums">/ {fmtTime(duration)}</span>
            {selected && (
              <span className="text-sm text-muted">
                {t("Corte selecionado: {a}–{b}", { a: fmtTime(selected.start, true), b: fmtTime(selected.end, true) })}
              </span>
            )}
          </div>
          <p className="m-0 min-h-[3em] text-[15px] leading-normal text-body" aria-live="off">{line?.text ?? ""}</p>
          {d.kind === "shorts" && <FacecamControls state={facecam} />}
        </section>

        <CutPanel
          cut={selected}
          own={own}
          index={selected ? sorted.findIndex((c) => c.id === selected.id) + 1 : 0}
          onChange={(patch) => selected && update(selected.id, patch)}
          onDelete={() => { if (selected) { edit((cs) => cs.filter((c) => c.id !== selected.id)); setSel(null); } }}
          onPlay={() => selected && playCut(selected)}
          onMark={setEdge}
          cuts={sorted}
          onPick={(c) => { setSel(c.id); seek(c.start); const el = scroller.current; if (el && (c.start * scale < el.scrollLeft || c.start * scale > el.scrollLeft + viewW)) el.scrollLeft = Math.max(0, c.start * scale - viewW * 0.2); }}
          duration={duration}
        />
      </div>

      <section className="card mt-4 flex flex-col gap-2 p-4" aria-label={t("Linha do tempo")}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-ink">{t("Linha do tempo")}</span>
          <span className="text-[13px] text-muted">{t("Arraste na faixa Cortes para criar um corte; arraste as bordas para ajustar.")}</span>
          <span className="ml-auto flex items-center gap-2">
            <button className="btn btn-sm" onClick={() => zoom(1 / 1.6)} disabled={!pps} aria-label={t("Afastar")}>−</button>
            <button className="btn btn-sm" onClick={() => zoom(1.6)} disabled={scale >= MAX_PPS} aria-label={t("Aproximar")}>+</button>
            <button className="btn btn-sm" onClick={() => setPps(0)} disabled={!pps}>{t("Ver tudo")}</button>
          </span>
        </div>
        <div className="flex select-none">
          <div className="flex-none" style={{ width: LABEL_W }}>
            <div style={{ height: RULER_H }} />
            {lanes.map((l) => (
              <div key={l.key} className="flex items-center border-t border-line-soft pr-2 text-[13px] font-semibold text-body" style={{ height: l.h }}>{l.label}</div>
            ))}
          </div>
          <div
            ref={scroller}
            className="relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
            onScroll={(e) => setScrollX((e.target as HTMLDivElement).scrollLeft)}
          >
            <div className="relative" style={{ width: total }}>
              <Graph kind="ruler" height={RULER_H} viewW={viewW} from={viewFrom} scale={scale} onSeek={seek} />
              {lanes.map((l) => (
                <div key={l.key} className="relative border-t border-line-soft" style={{ height: l.h }}>
                  {l.key === "cuts" && (
                    <div className="absolute inset-0 cursor-crosshair" onPointerDown={onLaneDown} onPointerMove={onMove} onPointerUp={onUp}>
                      {cuts.map((c) => {
                        const w = Math.max(4, (c.end - c.start) * scale);
                        const isSel = c.id === sel;
                        return (
                          <div
                            key={c.id}
                            role="button"
                            tabIndex={0}
                            aria-label={`${fmtTime(c.start)}–${fmtTime(c.end)} ${t(STATUS_LABEL[c.status])}`}
                            onPointerDown={(e) => onCutDown(e, c)}
                            onPointerMove={onMove}
                            onPointerUp={onUp}
                            onKeyDown={(e) => { if (e.key === "Enter") setSel(c.id); }}
                            className={`absolute top-2 bottom-2 cursor-grab overflow-hidden rounded-md border px-1.5 text-[12px] leading-[38px] font-semibold whitespace-nowrap ${BLOCK[c.status]} ${isSel ? "outline-2 outline-offset-1 outline-ink" : ""}`}
                            style={{ left: c.start * scale, width: w }}
                          >
                            <span className="pointer-events-none absolute inset-y-0 left-0 w-2 cursor-ew-resize" />
                            {w > 40 && (c.hook || (c.score != null ? `${c.score}` : ""))}
                            <span className="pointer-events-none absolute inset-y-0 right-0 w-2 cursor-ew-resize" />
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {l.key === "audio" && <Graph kind="wave" height={l.h} viewW={viewW} from={viewFrom} scale={scale} values={d.waveform.peaks} step={d.waveform.step} color={BODY} onSeek={seek} />}
                  {l.key === "speech" && (
                    <div className="absolute inset-0">
                      {d.transcript.filter((x) => x.e > viewFrom - 5 && x.s < viewTo + 5).map((x, i) => {
                        const w = Math.max(2, (x.e - x.s) * scale);
                        const on = x.s <= now && now < x.e;
                        return (
                          <button
                            key={`${x.s}-${i}`}
                            onClick={() => seek(x.s)}
                            title={x.text}
                            className={`absolute top-1.5 bottom-1.5 cursor-pointer overflow-hidden rounded-[5px] border-0 px-1 text-left text-[12px] whitespace-nowrap ${on ? "bg-ink text-white" : "bg-chip text-body hover:bg-[#e3e4e7]"}`}
                            style={{ left: x.s * scale, width: w }}
                          >
                            {w > 28 ? x.text : ""}
                          </button>
                        );
                      })}
                    </div>
                  )}
                  {GRAPHS.map(([k, , color]) => l.key === k && (
                    <Graph key={k} kind="bars" height={l.h} viewW={viewW} from={viewFrom} scale={scale} values={d.tracks[k] ?? []} step={1} color={color} onSeek={seek} />
                  ))}
                </div>
              ))}
              <div className="pointer-events-none absolute top-0 bottom-0 w-0.5 bg-alert" style={{ left: now * scale }} aria-hidden />
            </div>
          </div>
        </div>
        <p className="m-0 text-[13px] text-muted">
          {t("Atalhos: espaço toca ou pausa · I e O marcam o início e o fim do corte selecionado · ← e → andam 1 s (com Shift, 5 s) · A aprova · D descarta · + e − mudam o zoom")}
        </p>
      </section>

      <section className="card mt-4 flex flex-wrap items-center gap-3 p-4" aria-label={t("Produzir")}>
        <span className="text-sm text-body">
          {t("{a} aprovados · {s} sugeridos · {n} no total", { a: approvedN, s: suggestedN, n: cuts.length })}
        </span>
        <span className={`text-[13px] ${save === "error" ? "text-alert-text" : "text-muted"}`} aria-live="polite">
          {save === "saving" ? t("Salvando…") : save === "saved" ? t("Salvo") : save === "error" ? t("Não salvou: {e}", { e: saveErr }) : ""}
        </span>
        {suggestedN > 0 && (
          <button className="btn btn-sm" onClick={() => edit((cs) => cs.map((c) => (c.status === "suggested" ? { ...c, status: "approved" } : c)))}>
            {t("Aprovar os sugeridos")}
          </button>
        )}
        {canLong && (
          <label className="flex items-center gap-2 text-sm text-body">
            <input type="checkbox" className="h-5 w-5 accent-[#00843d]" checked={makeLong} onChange={(e) => setMakeLong(e.target.checked)} />
            {t("Fazer também o vídeo longo de melhores momentos")}
          </label>
        )}
        <span className="ml-auto flex flex-wrap gap-2">
          <button className="btn btn-danger" onClick={dismiss}>{t("Descartar este vídeo")}</button>
          <button className="btn btn-go" disabled={busy || (!approvedN && !(canLong && makeLong))} onClick={produce}>
            {d.kind === "segments" ? t("Usar os trechos aprovados ({n})", { n: approvedN }) : t("Produzir aprovados ({n})", { n: approvedN })}
          </button>
        </span>
      </section>
    </>
  );
}

function CutPanel({ cut, own, index, onChange, onDelete, onPlay, onMark, cuts, onPick, duration }: {
  cut: Cut | null; own: boolean; index: number; onChange: (p: Partial<Cut>) => void; onDelete: () => void; onPlay: () => void;
  onMark: (edge: "start" | "end") => void; cuts: Cut[]; onPick: (c: Cut) => void; duration: number;
}) {
  const { t } = useT();
  const warn = cut ? cutWarnings(cut, own, t) : [];
  return (
    <aside className="card flex min-w-0 flex-col gap-4 p-5" aria-label={t("Corte selecionado")}>
      {cut ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="h2 m-0">{t("Corte {n}", { n: index })}</h2>
            <span className={`pill ${STATUS_PILL[cut.status]}`}>{t(STATUS_LABEL[cut.status])}</span>
            {cut.origin === "owner" && <span className="pill bg-chip text-muted">{t("feito por você")}</span>}
            {cut.score != null && <span className="ml-auto text-sm font-semibold text-ink tabular-nums">{t("nota {n}", { n: cut.score })}</span>}
          </div>
          <div className="grid grid-cols-2 gap-3">
            {(["start", "end"] as const).map((edge) => (
              <label key={edge} className="flex flex-col gap-1 text-[13px] font-semibold text-body">
                {edge === "start" ? t("Início (s)") : t("Fim (s)")}
                <input
                  type="number"
                  step={0.1}
                  min={0}
                  max={duration}
                  className="input w-full font-normal tabular-nums"
                  value={cut[edge]}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) onChange({ [edge]: Math.max(0, Math.min(duration, v)) });
                  }}
                />
                <span className="flex items-center gap-2 font-normal text-muted">
                  {fmtTime(cut[edge], true)}
                  <button className="btn btn-sm ml-auto" onClick={() => onMark(edge)}>{edge === "start" ? t("Marcar aqui (I)") : t("Marcar aqui (O)")}</button>
                </span>
              </label>
            ))}
          </div>
          <p className="m-0 text-sm text-body">{t("Duração: {n} s", { n: (cut.end - cut.start).toFixed(1) })}</p>

          <div className="flex flex-col gap-1.5" role="radiogroup" aria-label={t("Comentário no Short")}>
            <span className="text-[13px] font-semibold text-body">{t("No Short")}</span>
            <div className="inline-flex gap-1 self-start rounded-xl bg-[#e9eae6] p-1">
              {([false, true] as const).map((v) => (
                <button
                  key={String(v)}
                  role="radio"
                  aria-checked={cut.commentary === v}
                  onClick={() => onChange({ commentary: v })}
                  className={`min-h-10 cursor-pointer rounded-[10px] border-0 px-3 text-sm font-semibold ${cut.commentary === v ? "bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.08)]" : "bg-transparent text-body"}`}
                >
                  {v ? t("Com comentário") : t("Só o corte")}
                </button>
              ))}
            </div>
            <span className="text-[13px] text-muted">
              {cut.commentary ? t("Sua voz comenta por cima do trecho.") : t("Só o trecho com o som original, título no topo e legendas da fala.")}
            </span>
          </div>

          {warn.length > 0 && (
            <ul className="m-0 flex list-none flex-col gap-1 rounded-xl bg-chip p-3 text-sm text-warn-text">
              {warn.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}

          {(cut.hook || cut.why || Object.keys(cut.scores).length > 0) && (
            <div className="flex flex-col gap-2 rounded-xl bg-paper p-3">
              {cut.hook && <p className="m-0 text-[15px] font-semibold text-ink">“{cut.hook}”</p>}
              {cut.why && <p className="m-0 text-sm text-body">{cut.why}</p>}
              {cut.tags && <p className="m-0 text-[13px] text-muted">{t("Sinais: {s}", { s: cut.tags })}</p>}
              {Object.keys(cut.scores).length > 0 && (
                <dl className="m-0 grid grid-cols-[auto_1fr_auto] items-center gap-x-2 gap-y-1 text-[13px]">
                  {SCORE_KEYS.map((k) => {
                    const v = cut.scores[k];
                    return v == null ? null : [
                      <dt key={`${k}-l`} className="text-body">{t(SCORE_LABEL[k])}</dt>,
                      <dd key={`${k}-b`} className="m-0 h-1.5 rounded-full bg-track"><span className="block h-1.5 rounded-full bg-ink" style={{ width: `${v * 10}%` }} /></dd>,
                      <dd key={`${k}-v`} className="m-0 text-right font-semibold text-ink tabular-nums">{v}</dd>,
                    ];
                  })}
                </dl>
              )}
            </div>
          )}

          <label className="flex flex-col gap-1 text-[13px] font-semibold text-body">
            {t("Seu comentário para a produção")}
            <textarea
              className="input min-h-24 w-full py-2.5 font-normal"
              placeholder={t("Ex.: começa no grito, corta antes da propaganda, título sobre a fuga")}
              value={cut.comment}
              maxLength={2000}
              onChange={(e) => onChange({ comment: e.target.value })}
            />
            <span className="font-normal text-muted">{t("Vai junto para o roteiro deste corte e manda nele.")}</span>
          </label>

          <div className="flex flex-wrap gap-2">
            <button className={`btn ${cut.status === "approved" ? "btn-ink" : "btn-go"}`} onClick={() => onChange({ status: "approved" })} disabled={cut.status === "approved"}>
              {cut.status === "approved" ? t("Aprovado") : t("Aprovar (A)")}
            </button>
            <button className="btn" onClick={() => onChange({ status: "discarded" })} disabled={cut.status === "discarded"}>{t("Descartar (D)")}</button>
            <button className="btn" onClick={onPlay}><IconPlay />{t("Tocar só o corte")}</button>
            <button className="btn btn-danger" onClick={onDelete}>{t("Apagar")}</button>
          </div>
        </>
      ) : (
        <p className="m-0 text-body">{t("Escolha um corte na lista ou na linha do tempo. Para criar um, arraste na faixa Cortes.")}</p>
      )}

      <div className="flex flex-col gap-1 border-t border-line pt-3">
        <h3 className="m-0 text-[13px] font-semibold text-muted">{t("Todos os cortes ({n})", { n: cuts.length })}</h3>
        <ul className="m-0 flex max-h-72 list-none flex-col overflow-y-auto p-0">
          {cuts.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => onPick(c)}
                className={`flex min-h-10 w-full cursor-pointer items-center gap-2 rounded-[10px] border-0 px-2 text-left text-sm ${c.id === cut?.id ? "bg-chip" : "bg-transparent hover:bg-paper"}`}
              >
                <span className="w-28 flex-none font-semibold text-ink tabular-nums">{fmtTime(c.start)}–{fmtTime(c.end)}</span>
                <span className="min-w-0 flex-1 truncate text-body">{c.hook || c.why || t("sem gancho")}</span>
                {c.score != null && <span className="text-[13px] font-semibold text-ink tabular-nums">{c.score}</span>}
                <span className={`pill px-2 py-0.5 text-[11px] ${STATUS_PILL[c.status]}`}>{t(STATUS_LABEL[c.status])}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}

/** One lane drawn for the visible window only (sticky canvas inside the scrolling timeline). */
function Graph({ kind, height, viewW, from, scale, values = [], step = 1, color = INK, onSeek }: {
  kind: "ruler" | "wave" | "bars"; height: number; viewW: number; from: number; scale: number; values?: number[]; step?: number;
  color?: string; onSeek: (t: number) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(viewW * dpr);
    cv.height = Math.round(height * dpr);
    const g = cv.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, viewW, height);
    if (kind === "ruler") {
      const every = TICKS.find((x) => x * scale >= 80) ?? 3600;
      g.fillStyle = MUTED;
      g.strokeStyle = LINE;
      g.font = "12px Figtree, system-ui, sans-serif";
      for (let s = Math.floor(from / every) * every; s <= from + viewW / scale; s += every) {
        const x = Math.round((s - from) * scale) + 0.5;
        g.beginPath();
        g.moveTo(x, height - 8);
        g.lineTo(x, height);
        g.stroke();
        g.fillText(fmtTime(s), x + 3, height - 10);
      }
      return;
    }
    if (!values.length) return;
    let top = 0;
    if (kind === "bars") for (const v of values) top = Math.max(top, v);
    const norm = kind === "wave" ? 255 : top || 1;
    g.fillStyle = color;
    for (let x = 0; x < viewW; x++) {
      const t0 = from + x / scale;
      const t1 = from + (x + 1) / scale;
      const i0 = Math.floor(t0 / step);
      const i1 = Math.max(i0 + 1, Math.ceil(t1 / step));
      if (i0 >= values.length) break;
      let v = 0;
      for (let i = Math.max(0, i0); i < Math.min(values.length, i1); i++) v = Math.max(v, values[i]);
      const h = (v / norm) * (height - 6);
      if (kind === "wave") g.fillRect(x, (height - h) / 2, 1, Math.max(1, h));
      else g.fillRect(x, height - 3 - h, 1, h);
    }
  }, [kind, height, viewW, from, scale, values, step, color]);
  return (
    <canvas
      ref={ref}
      className="sticky left-0 block cursor-pointer"
      style={{ width: viewW, height }}
      onClick={(e) => onSeek(from + (e.clientX - e.currentTarget.getBoundingClientRect().left) / scale)}
      aria-hidden
    />
  );
}
