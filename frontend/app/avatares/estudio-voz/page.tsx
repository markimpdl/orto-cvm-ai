"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { API, api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PageHeader } from "@/components/Bits";

type Take = { status: "ok" | "redo"; problems: string[]; heard: string | null; wer: number | null; duration: number; peak_db: number; speech_db: number };
type Sentence = { idx: number; text: string; section: string; take: Take | null };
type Summary = { language: string; sentences: Sentence[]; total: number; ok: number; redo: number; minutes_ok: number; noise_db: number | null };

const LANGS = ["pt-BR", "en-US"];
// raw microphone: no browser noise suppression / auto gain, so the model learns the real voice
const MIC: MediaTrackConstraints = { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1, sampleRate: 48000 };

function Studio() {
  const params = useSearchParams();
  const avatar = Number(params.get("avatar") ?? 1);
  const { t } = useT();
  const [lang, setLang] = useState("pt-BR");
  const [data, setData] = useState<Summary | null>(null);
  const [cur, setCur] = useState(0);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [level, setLevel] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const [noise, setNoise] = useState<string | null>(null);
  const [v, setV] = useState(0); // cache-buster for the take audio
  const stream = useRef<MediaStream | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const raf = useRef(0);
  const player = useRef<HTMLAudioElement>(null);

  const load = useCallback(async (jump = false) => {
    const s = await api<Summary>(`/avatars/${avatar}/studio/${lang}`);
    setData(s);
    if (jump) setCur(Math.max(0, s.sentences.findIndex((x) => x.take?.status !== "ok")));
  }, [avatar, lang]);
  useEffect(() => { load(true).catch((e) => setMsg(String(e))); }, [load]);

  async function mic(): Promise<MediaStream> {
    if (stream.current) return stream.current;
    const s = await navigator.mediaDevices.getUserMedia({ audio: MIC });
    stream.current = s;
    const ctx = new AudioContext();
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    ctx.createMediaStreamSource(s).connect(an);
    const buf = new Float32Array(an.fftSize);
    const tick = () => {
      an.getFloatTimeDomainData(buf);
      let peak = 0;
      for (const x of buf) peak = Math.max(peak, Math.abs(x));
      setLevel(peak);
      raf.current = requestAnimationFrame(tick);
    };
    tick();
    return s;
  }
  useEffect(() => () => { cancelAnimationFrame(raf.current); stream.current?.getTracks().forEach((x) => x.stop()); }, []);

  async function record(): Promise<Blob> {
    const s = await mic();
    const r = new MediaRecorder(s, { mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "" , audioBitsPerSecond: 256000 });
    chunks.current = [];
    r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    const done = new Promise<Blob>((res) => { r.onstop = () => res(new Blob(chunks.current, { type: r.mimeType })); });
    r.start();
    rec.current = r;
    setRecording(true);
    return done;
  }

  async function toggle() {
    if (busy || !data) return;
    if (recording) {
      rec.current?.stop();
      setRecording(false);
      return;
    }
    setMsg(null);
    const blob = await record();
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", blob, `take_${cur}.webm`);
      const res = await fetch(`${API}/avatars/${avatar}/studio/${lang}/${cur}`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      const take: Take = await res.json();
      setV((x) => x + 1);
      await load();
      if (take.status === "ok") {
        setMsg(t("Boa! ({s} s)", { s: take.duration }));
        setCur((c) => {
          const next = data.sentences.findIndex((x, i) => i > c && x.take?.status !== "ok");
          return next === -1 ? Math.min(c + 1, data.total - 1) : next;
        });
      } else {
        setMsg(t("Refazer: {p}", { p: take.problems.join("; ") }) + (take.heard ? ` — ${t("ouvi")}: “${take.heard}”` : ""));
      }
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function noiseTest() {
    setNoise(t("Fique em silêncio por 10 segundos…"));
    const blob = record();
    setTimeout(() => { rec.current?.stop(); setRecording(false); }, 10000);
    const fd = new FormData();
    fd.append("file", await blob, "noise.webm");
    const r = await fetch(`${API}/avatars/${avatar}/studio/${lang}/noise`, { method: "POST", body: fd }).then((x) => x.json());
    const verdict: Record<string, string> = { "ótimo": t("ótimo"), ok: t("ok"), barulhento: t("barulhento: desligue ar-condicionado e ventilador") };
    setNoise(t("Ruído do ambiente: {db} dB ({v})", { db: r.noise_db, v: verdict[r.verdict] ?? r.verdict }));
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.code === "Space") { e.preventDefault(); toggle(); }
      else if (e.code === "ArrowRight") setCur((c) => Math.min(c + 1, (data?.total ?? 1) - 1));
      else if (e.code === "ArrowLeft") setCur((c) => Math.max(c - 1, 0));
      else if (e.code === "KeyP") player.current?.play();
      else if (e.code === "KeyR" && !recording) toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!data) return <p className="text-muted">{msg ?? t("Carregando…")}</p>;
  if (!data.total) return <p className="text-muted">{t("Roteiro de leitura não encontrado para {lang}.", { lang })}</p>;
  const s = data.sentences[cur];
  const pct = Math.round((data.ok / data.total) * 100);
  const hot = level > 0.95;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        {LANGS.map((l) => (
          <button key={l} aria-pressed={l === lang} className={`btn btn-sm ${l === lang ? "btn-ink" : ""}`} onClick={() => setLang(l)}>{l}</button>
        ))}
        <span className="text-sm text-muted">{t("{ok} de {total} frases boas · {min} min", { ok: data.ok, total: data.total, min: data.minutes_ok })}</span>
        <button className="btn btn-sm" disabled={recording || busy} onClick={noiseTest}>{t("Testar ruído do quarto")}</button>
        {noise && <span className="text-sm text-body">{noise}</span>}
        <a className="btn btn-sm ml-auto" href={`${API}/avatars/${avatar}/studio-export.zip`}>{t("Exportar para treino")}</a>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-line-soft"><div className="h-full bg-go" style={{ width: `${pct}%` }} /></div>

      <div className="card flex flex-col gap-4 p-6">
        <div className="flex items-center gap-3 text-sm">
          <span className="pill bg-go-soft text-go-text">{s.section}</span>
          <span className="text-muted">{t("Frase {n} de {total}", { n: cur + 1, total: data.total })}</span>
        </div>
        <p className="m-0 text-[32px] font-semibold leading-snug">{s.text}</p>
        <div className="flex flex-wrap items-center gap-3">
          <button className={`btn ${recording ? "btn-danger" : "btn-go"}`} disabled={busy} onClick={toggle}>
            <span className={`h-2.5 w-2.5 rounded-full ${recording ? "animate-pulse bg-white" : "bg-alert"}`} />
            {busy ? t("Conferindo…") : recording ? t("Parar (espaço)") : s.take ? t("Regravar (espaço)") : t("Gravar (espaço)")}
          </button>
          <button className="btn btn-sm" disabled={cur === 0} onClick={() => setCur(cur - 1)}>← {t("Anterior")}</button>
          <button className="btn btn-sm" disabled={cur >= data.total - 1} onClick={() => setCur(cur + 1)}>{t("Próxima")} →</button>
          <div className="h-3 w-40 overflow-hidden rounded-full bg-line-soft" title={t("Nível do microfone")}>
            <div className={`h-full ${hot ? "bg-alert" : "bg-go"}`} style={{ width: `${Math.min(100, level * 100)}%` }} />
          </div>
          {hot && <span className="text-sm text-alert-text">{t("muito alto")}</span>}
        </div>
        {s.take && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className={`pill ${s.take.status === "ok" ? "bg-go-soft text-go-text" : "bg-alert-soft text-alert-text"}`}>{s.take.status === "ok" ? t("boa") : t("refazer")}</span>
            <audio ref={player} src={`${API}/avatars/${avatar}/studio/${lang}/${cur}.wav?v=${v}`} controls className="h-8" />
            <span className="text-muted">{s.take.duration} s · {t("pico")} {s.take.peak_db} dB · {t("voz")} {s.take.speech_db} dB</span>
          </div>
        )}
        {msg && <p role="status" className="m-0 text-sm text-body">{msg}</p>}
        <p className="m-0 text-xs text-muted">{t("Leia do jeito que você fala nos vídeos, no tom da seção. Microfone a ~20 cm, quarto silencioso. Atalhos: espaço grava/para, ← → navegam, P ouve, R regrava. Pode parar e continuar outro dia.")}</p>
      </div>

      <div className="flex flex-wrap gap-1" aria-label={t("Mapa das frases")}>
        {data.sentences.map((x, i) => (
          <button key={x.idx} title={x.text} onClick={() => setCur(i)}
            className={`h-4 w-4 rounded-sm border-0 ${i === cur ? "ring-2 ring-ink" : ""} ${x.take?.status === "ok" ? "bg-go" : x.take?.status === "redo" ? "bg-alert" : "bg-line-soft"}`} />
        ))}
      </div>
    </div>
  );
}

export default function Page() {
  const { t } = useT();
  return (
    <>
      <PageHeader title={t("Estúdio de voz")} subtitle={t("~30 min de leitura, frase por frase, para treinar a voz do seu avatar.")} />
      <Suspense><Studio /></Suspense>
    </>
  );
}
