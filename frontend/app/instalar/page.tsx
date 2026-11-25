"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { getSetup, saveSetup, type SetupInfo, type SetupStep } from "@/lib/api";
import { useT } from "@/lib/i18n";
import Ai from "./steps/Ai";
import Channel from "./steps/Channel";
import Done from "./steps/Done";
import Engines from "./steps/Engines";
import Features from "./steps/Features";
import Language from "./steps/Language";
import Models from "./steps/Models";
import System from "./steps/System";
import { STEPS, type Wizard } from "./steps/types";

/**
 * /instalar: the install wizard (spec docs/superpowers/specs/2026-11-24-install-wizard-design.md §3). Progress lives in
 * Setting "setup" (GET/PUT /setup), so closing the tab and coming back resumes on the same step.
 * `?revisit=1` (Configurações) opens it on a finished install without forcing anything.
 */
export default function InstallPage() {
  const { t } = useT();
  const [info, setInfo] = useState<SetupInfo | null | undefined>(undefined);
  const [step, setStep] = useState<SetupStep>("system");
  const [furthest, setFurthest] = useState(0);
  const [revisit, setRevisit] = useState(false);
  const stepRef = useRef<SetupStep>("system");
  stepRef.current = step;
  // PUT /setup is read-merge-write on the server: two at once can drop each other's choices, so they go one by one
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const put = useCallback((s: SetupStep, choices: Record<string, unknown> = {}) => {
    const p = queue.current.then(() => saveSetup(s, choices));
    queue.current = p.catch(() => {});
    return p;
  }, []);

  const seq = useRef(0);
  const reload = useCallback(async () => {
    const mine = ++seq.current; // quick toggles: only the newest answer may replace the state
    try {
      const r = await getSetup();
      if (mine === seq.current) setInfo(r);
      return r;
    } catch {
      setInfo((cur) => cur ?? null);
      return null;
    }
  }, []);

  useEffect(() => {
    const again = new URLSearchParams(window.location.search).get("revisit") === "1";
    setRevisit(again);
    (async () => {
      const r = await reload();
      if (!r) return;
      const start: SetupStep = again || !r.state.step || !STEPS.includes(r.state.step) ? "system" : r.state.step;
      setStep(start);
      setFurthest(r.state.done || again ? STEPS.length - 1 : STEPS.indexOf(start));
      if (r.state.done && !again) return;
      // first visit: suggest every feature this machine can run (the API's blockers are the truth)
      if (!Array.isArray(r.state.choices?.features)) {
        try {
          await put(start, { features: Object.keys(r.features) });
          const all = await getSetup();
          const blocked = new Set(all.blockers.map((b) => b.feature));
          await put(start, { features: Object.keys(r.features).filter((k) => !blocked.has(k)) });
          await reload();
        } catch { /* the Models step lets the person choose anyway */ }
      }
    })();
  }, [reload, put]);

  const go = useCallback((s: SetupStep) => {
    setStep(s);
    setFurthest((f) => Math.max(f, STEPS.indexOf(s)));
    if (s !== "done") put(s).catch(() => {}); // the Done step's POST /setup/finish records it (no racing write)
    window.scrollTo({ top: 0 });
  }, [put]);

  if (info === undefined) return <div className="mx-auto w-full max-w-[760px] text-muted">{t("Carregando…")}</div>;
  if (info === null) {
    return (
      <Shell>
        <div className="card flex flex-col gap-2 p-6">
          <h1 className="h2 m-0">{t("Não consegui falar com a API")}</h1>
          <p className="m-0 text-body">{t("O assistente precisa do serviço da fábrica ligado. Abra um terminal na pasta backend, rode uv run cvm-api e recarregue esta página.")}</p>
          <button type="button" className="btn mt-2 w-fit" onClick={() => location.reload()}>{t("Tentar de novo")}</button>
        </div>
      </Shell>
    );
  }
  if (info.state.done && !revisit) {
    return (
      <Shell>
        <div className="card flex flex-col gap-3 p-6">
          <h1 className="h2 m-0">{t("A instalação já está pronta")}</h1>
          <p className="m-0 text-body">{t("Você pode rever as escolhas a qualquer hora. Nada do que já está instalado é apagado.")}</p>
          <div className="flex flex-wrap gap-3">
            <Link href="/em-alta" className="btn btn-go">{t("Ir para Em alta")}</Link>
            <button type="button" className="btn" onClick={() => { setRevisit(true); setFurthest(STEPS.length - 1); }}>{t("Rever o assistente")}</button>
          </div>
        </div>
      </Shell>
    );
  }

  const idx = STEPS.indexOf(step);
  const w: Wizard = {
    info,
    choices: info.state.choices ?? {},
    save: async (choices) => {
      const st = await put(stepRef.current, choices);
      setInfo((cur) => (cur ? { ...cur, state: st } : cur));
    },
    reload,
    next: () => go(STEPS[Math.min(idx + 1, STEPS.length - 1)]),
    back: () => go(STEPS[Math.max(idx - 1, 0)]),
    go,
  };
  const Step = { system: System, language: Language, ai: Ai, features: Features, models: Models, engines: Engines, channel: Channel, done: Done }[step];
  return (
    <Shell>
      <Stepper step={step} furthest={furthest} onPick={go} />
      <Step w={w} />
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { t, lang, setLang } = useT();
  return (
    <div className="mx-auto flex w-full max-w-[760px] flex-col gap-7">
      <div className="flex flex-wrap items-center gap-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brand/logo-wordmark.png" alt="Orto CVM" className="block h-[52px] w-[156px] object-contain object-left" />
        <span className="text-[15px] font-semibold text-muted">{t("Instalação")}</span>
        <div role="group" aria-label={t("Idioma da interface")} className="ml-auto flex gap-1">
          {(["pt", "en"] as const).map((l) => (
            <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)}
              className={`btn btn-sm min-w-11 ${lang === l ? "btn-ink" : ""}`}>{l.toUpperCase()}</button>
          ))}
        </div>
      </div>
      {children}
    </div>
  );
}

function Stepper({ step, furthest, onPick }: { step: SetupStep; furthest: number; onPick: (s: SetupStep) => void }) {
  const { t } = useT();
  const labels: Record<SetupStep, string> = {
    system: t("Sistema"), language: t("Idioma"), ai: t("IAs"), features: t("Recursos"), models: t("Modelos"), engines: t("Teste"), channel: t("Canal"), done: t("Pronto"),
  };
  const cur = STEPS.indexOf(step);
  return (
    <nav aria-label={t("Passos da instalação")} className="flex flex-col gap-2">
      <ol className="m-0 flex list-none flex-wrap gap-1 p-0">
        {STEPS.map((s, i) => {
          const here = i === cur;
          const done = i < cur || (i <= furthest && !here);
          const open = i <= furthest;
          return (
            <li key={s}>
              <button type="button" disabled={!open} aria-current={here ? "step" : undefined} onClick={() => onPick(s)}
                className={`pick inline-flex min-h-10 items-center gap-2 rounded-[10px] border-0 px-3 text-sm font-medium disabled:cursor-default ${
                  here ? "bg-ink text-white" : open ? "bg-transparent text-ink hover:bg-[#ecedea]" : "bg-transparent text-muted"}`}>
                <span aria-hidden className={`h-2 w-2 rounded-full ${here || done ? "bg-go" : "bg-field"}`} />
                {labels[s]}
              </button>
            </li>
          );
        })}
      </ol>
      <div className="text-[13px] text-muted">{t("Passo {n} de {total}", { n: cur + 1, total: STEPS.length })}</div>
    </nav>
  );
}
