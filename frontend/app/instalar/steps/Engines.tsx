"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiDetail, type EngineFeature, type EnginesInfo } from "@/lib/api";
import { useT, type T } from "@/lib/i18n";
import type { Wizard } from "./types";
import { Note, StepFooter, StepHead } from "./ui";

const POLL_MS = 5000;

function what(t: T, key: string): string {
  return ({
    ai_images: t("Desenha uma imagem."),
    animation: t("Anima 3 segundos de uma imagem."),
    music: t("Compõe 5 segundos de música."),
    avatar: t("O Lucas fala uma frase."),
  } as Record<string, string>)[key] ?? "";
}

/** Step 6 (after Modelos): a short job per feature on this machine, best engine first; the first that works stays. */
export default function Engines({ w }: { w: Wizard }) {
  const { t } = useT();
  const [info, setInfo] = useState<EnginesInfo | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api<EnginesInfo>("/setup/engines").then(setInfo).catch((e) => setErr(apiDetail(e))), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!info?.running) return;
    const i = setInterval(load, POLL_MS);
    return () => clearInterval(i);
  }, [info?.running, load]);

  async function test() {
    setErr("");
    setBusy(true);
    try {
      await api("/setup/engines/test", { method: "POST", body: JSON.stringify({}) });
      await load();
    } catch (e) {
      setErr(apiDetail(e));
    } finally {
      setBusy(false);
    }
  }

  async function pick(feature: string, engine: string) {
    setErr("");
    try {
      await api("/setup/engines", { method: "PUT", body: JSON.stringify({ feature, engine }) });
      await load();
    } catch (e) {
      setErr(apiDetail(e));
    }
  }

  const feats = Object.entries(info?.features ?? {}).filter(([, f]) => f.selected);
  const tested = feats.some(([, f]) => f.test?.results?.length);
  const running = Boolean(info?.running);

  return (
    <>
      <StepHead title={t("Teste dos motores")}
        lead={t("Cada recurso tem mais de um motor, do mais caprichado ao mais leve. O teste faz um trabalho curto com cada um neste computador e fica com o melhor que funcionou. Pode levar de alguns minutos a mais de uma hora, e você pode seguir para o próximo passo enquanto ele roda.")} />

      {!info && !err && <div className="text-muted">{t("Carregando…")}</div>}
      {info && feats.length === 0 && (
        <Note>{t("Nenhum recurso marcado precisa de teste. A base já funciona do jeito que foi instalada.")}</Note>
      )}

      {feats.map(([key, f]) => (
        <FeatureCard key={key} id={key} f={f} running={running} onPick={(engine) => pick(key, engine)} />
      ))}

      {running && (
        <p className="m-0 text-[13px] text-muted" aria-live="polite">
          {t("Testando… a página se atualiza sozinha. Se um vídeo estiver sendo feito, o teste espera ele liberar a placa de vídeo.")}
        </p>
      )}
      {err && <Note tone="alert">{err}</Note>}
      <StepFooter onBack={w.back}>
        {feats.length > 0 && !running && (
          <button type="button" className={`btn ${tested ? "" : "btn-go"}`} disabled={busy} onClick={test}>
            {busy ? t("Começando…") : tested ? t("Testar de novo") : t("Testar agora")}
          </button>
        )}
        <button type="button" className={`btn ${tested || running ? "btn-go" : ""}`} onClick={w.next}>
          {tested || running ? t("Avançar") : t("Pular por agora")}
        </button>
      </StepFooter>
    </>
  );
}

function FeatureCard({ id, f, running, onPick }: { id: string; f: EngineFeature; running: boolean; onPick: (engine: string) => void }) {
  const { t } = useT();
  const labelOf = (engine: string | null) => f.tiers.find((x) => x.engine === engine)?.label ?? engine ?? "";
  const results = f.test?.results ?? [];
  const passed = results.filter((r) => r.ok);
  const nowRunning = running && f.test?.running;
  const usable = f.tiers.filter((x) => x.available && x.fits);
  let status: string;
  if (nowRunning) status = t("Testando agora…");
  else if (f.test && !f.test.chosen && results.length) status = t("Nenhum motor funcionou aqui. Veja o motivo abaixo e tente de novo.");
  else if (f.engine && results.length) status = t("Em uso: {name}", { name: labelOf(f.engine) });
  else if (f.engine) status = t("Ainda não testado. Vai usar: {name}", { name: labelOf(f.engine) });
  else status = t("Nenhum motor cabe neste computador.");

  return (
    <section className="card flex flex-col gap-3 p-5" aria-labelledby={`eng-${id}`}>
      <div className="flex flex-col gap-1">
        <h2 id={`eng-${id}`} className="h2 m-0">{t(f.label)}</h2>
        <span className="text-[13px] text-muted">{what(t, id)} {f.all_tested ? t("Aqui as três opções de avatar são testadas, para você comparar.") : ""}</span>
        <span className={`text-sm font-semibold ${f.test && !f.test.chosen && results.length && !nowRunning ? "text-alert-text" : "text-body"}`}>{status}</span>
      </div>

      {results.length > 0 && (
        <ul className="m-0 flex list-none flex-col p-0">
          {results.map((r) => (
            <li key={r.engine} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line-soft py-2 first:border-t-0">
              <span className="min-w-0 flex-[1_1_240px] text-[15px]">{t(r.label)}</span>
              {r.ok ? (
                <span className="pill bg-go-soft px-2 py-0.5 text-[12px] text-go-text">{t("funcionou em {s} s", { s: Math.round(r.seconds) })}</span>
              ) : (
                <span className="pill bg-alert-soft px-2 py-0.5 text-[12px] text-alert-text">{t("não funcionou")}</span>
              )}
              {!r.ok && r.error && <span className="basis-full text-[13px] text-alert-text">{r.error}</span>}
            </li>
          ))}
        </ul>
      )}

      {/* the owner compares the avatar options and may keep another one that also worked */}
      {f.all_tested && passed.length > 1 && !nowRunning && (
        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-semibold text-body">{t("Qual usar nos vídeos")}</span>
          <div role="radiogroup" aria-label={t("Qual usar nos vídeos")} className="flex flex-wrap gap-2">
            {passed.map((r) => (
              <button key={r.engine} type="button" role="radio" aria-checked={f.engine === r.engine}
                onClick={() => onPick(r.engine)} className={`btn btn-sm ${f.engine === r.engine ? "btn-ink" : ""}`}>
                {t(r.label)}
              </button>
            ))}
          </div>
          <span className="text-[13px] text-muted">{t("Os vídeos do teste ficam em data/engine_test, se quiser assistir antes de escolher.")}</span>
        </div>
      )}

      {!results.length && usable.length > 1 && (
        <span className="text-[13px] text-muted">
          {t("Ordem do teste: {list}", { list: usable.map((x) => t(x.label)).join(" → ") })}
        </span>
      )}
    </section>
  );
}
