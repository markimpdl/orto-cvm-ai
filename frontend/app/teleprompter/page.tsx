"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLanguages } from "@/lib/useLanguages";

type Section = { title: string; direction: string; lines: { text: string; cue: boolean }[] };

const KINDS: [string, string][] = [["video", "Vídeo-base"], ["pt-BR", "Voz pt-BR"], ["en-US", "Voz en-US"], ["ar-AE", "Voz ar-AE"]];

function Prompter() {
  const params = useSearchParams();
  const [kind, setKind] = useState(params.get("kind") ?? "video");
  const [sections, setSections] = useState<Section[]>([]);
  const [size, setSize] = useState(44);
  const [speed, setSpeed] = useState(30); // px per second
  const [running, setRunning] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const { t } = useT();
  const videoLangs = useLanguages();

  useEffect(() => {
    api<{ sections: Section[] }>(`/avatars/meta/reading-text/${kind}`).then((r) => setSections(r.sections));
    box.current?.scrollTo({ top: 0 });
    setRunning(false);
  }, [kind]);

  useEffect(() => {
    if (!running) return;
    let last = performance.now();
    let pos = box.current?.scrollTop ?? 0;
    let raf = 0;
    const step = (now: number) => {
      pos += ((now - last) / 1000) * speed;
      last = now;
      box.current?.scrollTo({ top: pos });
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [running, speed]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code === "Space") { e.preventDefault(); setRunning((r) => !r); }
      if (e.key === "ArrowUp") setSpeed((s) => Math.min(200, s + 5));
      if (e.key === "ArrowDown") setSpeed((s) => Math.max(5, s - 5));
      if (e.key === "+" || e.key === "=") setSize((s) => Math.min(96, s + 4));
      if (e.key === "-") setSize((s) => Math.max(20, s - 4));
      if (e.key === "Home") box.current?.scrollTo({ top: 0 });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const rtl = kind === "ar-AE";
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black text-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 p-2 text-sm">
        {KINDS.filter(([k]) => k === "video" || videoLangs.includes(k) || k === kind).map(([k, label]) => (
          <button key={k} aria-pressed={k === kind} className={`btn btn-sm ${k === kind ? "btn-go" : ""}`} onClick={() => setKind(k)}>{t(label)}</button>
        ))}
        <button className="btn btn-sm" onClick={() => setRunning((r) => !r)}>{running ? t("Pausar") : t("Rolar")}</button>
        <label className="flex items-center gap-1 text-zinc-400">{t("velocidade")}
          <input type="range" min={5} max={200} value={speed} onChange={(e) => setSpeed(+e.target.value)} />
        </label>
        <label className="flex items-center gap-1 text-zinc-400">{t("letra")}
          <input type="range" min={20} max={96} value={size} onChange={(e) => setSize(+e.target.value)} />
        </label>
        <span className="text-xs text-zinc-500">{t("espaço: rolar/pausar · ↑↓ velocidade · +/− letra · Home: voltar ao início")}</span>
      </div>
      <div ref={box} className="flex-1 overflow-y-auto px-[8vw]" dir={rtl ? "rtl" : "ltr"}>
        {/* the line being read stays near the top, close to the camera */}
        <div className="h-[8vh]" />
        {sections.map((s, i) => (
          <section key={i} className="mb-[6vh]">
            <div className="mb-3 text-[0.45em] font-semibold uppercase tracking-wide text-amber-400" style={{ fontSize: Math.max(16, size * 0.42) }}>
              {s.title}{s.direction && <span className="font-normal normal-case text-amber-200/80"> · {s.direction}</span>}
            </div>
            {s.lines.map((l, j) => (
              <p key={j} className={l.cue ? "my-4 italic text-sky-300" : "my-4 leading-snug"} style={{ fontSize: l.cue ? Math.max(16, size * 0.55) : size }}>
                {l.cue ? `⏸ ${l.text}` : l.text}
              </p>
            ))}
          </section>
        ))}
        <div className="h-[90vh]" />
      </div>
    </div>
  );
}

export default function TeleprompterPage() {
  return (
    <Suspense>
      <Prompter />
    </Suspense>
  );
}
