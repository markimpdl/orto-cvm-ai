"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckBox } from "@/components/Bits";
import { api, apiDetail, type InstallItem, type InstallPlan, type InstallStatus } from "@/lib/api";
import { useT, type T } from "@/lib/i18n";
import { chosenFeatures, type Wizard } from "./types";
import { Note, StepFooter, StepHead, gbText } from "./ui";

const POLL_MS = 3000;

function featureHint(t: T, key: string): string {
  return ({
    base: t("Toda instalação precisa disto."),
    ai_images: t("Desenha cenas e miniaturas quando não há foto boa no banco de imagens."),
    avatar: t("O apresentador que fala olhando para a câmera."),
    animation: t("Dá movimento às melhores imagens de cada vídeo."),
    kids: t("Episódios de desenho animado com personagens fixos."),
    music: t("Compõe a trilha de fundo em vez de buscar música pronta."),
  } as Record<string, string>)[key] ?? "";
}

/** Step 5: tools and model weights for the chosen features, with sizes, blockers and a background install. */
export default function Models({ w }: { w: Wizard }) {
  const { t } = useT();
  const keys = Object.keys(w.info.features);
  const [feats, setFeats] = useState<string[]>(() => chosenFeatures(w).filter((f) => keys.includes(f)));
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const [status, setStatus] = useState<InstallStatus | null>(null);
  const [syncing, setSyncing] = useState(true);
  const [err, setErr] = useState("");
  const [starting, setStarting] = useState(false);
  const { save, reload } = w;
  const saveRef = useRef(save);
  saveRef.current = save;

  // the blockers come from GET /setup for the SAVED choice: save first, then read both back
  const key = feats.join(",");
  useEffect(() => {
    let live = true;
    setSyncing(true);
    (async () => {
      try {
        await saveRef.current({ features: key ? key.split(",") : [] });
        await reload();
        const p = key ? await api<InstallPlan>(`/setup/models?features=${encodeURIComponent(key)}`) : { items: [], missing_gb: 0 };
        if (live) setPlan(p);
      } catch (e) {
        if (live) setErr(apiDetail(e));
      } finally {
        if (live) setSyncing(false);
      }
    })();
    return () => { live = false; };
  }, [key, reload]);

  const poll = useCallback(() => api<InstallStatus>("/setup/models/status").then(setStatus).catch(() => {}), []);
  useEffect(() => { poll(); }, [poll]);
  useEffect(() => {
    if (!status?.running) return;
    const i = setInterval(poll, POLL_MS);
    return () => clearInterval(i);
  }, [status?.running, poll]);

  const blockers = w.info.blockers;
  const free = w.info.hardware.disk_free_gb;
  // the number the disk blocker used (GET /setup disk.need_gb), so the header and the blocker never disagree
  const need = w.info.disk?.need_gb ?? null;
  const diskBlocked = blockers.some((b) => b.feature === "disk");
  const running = Boolean(status?.running);
  const byId = new Map((status?.items ?? []).map((i) => [`${i.kind}:${i.id}`, i]));
  const items: InstallItem[] = (plan?.items ?? []).map((i) => {
    const s = byId.get(`${i.kind}:${i.id}`);
    return s && (running || s.state === "failed") && i.state !== "ready" ? { ...i, state: s.state, error: s.error } : i;
  });
  const failed = items.filter((i) => i.state === "failed");
  const readyCount = items.filter((i) => i.state === "ready").length;
  const allReady = Boolean(plan) && !syncing && items.length > 0 && readyCount === items.length;
  const canInstall = !syncing && !running && !starting && feats.length > 0 && blockers.length === 0 && items.some((i) => i.state !== "ready");

  const toggle = (k: string) => setFeats((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : keys.filter((x) => x === k || cur.includes(x))));

  async function install() {
    setErr("");
    setStarting(true);
    try {
      setStatus(await api<InstallStatus>("/setup/models", { method: "POST", body: JSON.stringify({ features: feats }) }));
    } catch (e) {
      setErr(apiDetail(e));
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <StepHead title={t("Modelos de IA")}
        lead={t("Cada recurso precisa de alguns programas e modelos baixados neste computador. Eles rodam aqui mesmo, sem custo. Marque o que quer usar; a instalação segue em segundo plano e você pode continuar os próximos passos.")} />

      <section className="card flex flex-col p-2" aria-label={t("Recursos")}>
        {keys.map((k) => {
          const b = blockers.find((x) => x.feature === k);
          return (
            <div key={k} className={`flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl px-2 py-1.5 ${b ? "bg-alert-soft" : ""}`}>
              <CheckBox checked={feats.includes(k)} onChange={() => toggle(k)} label={t(w.info.features[k])} />
              <div className="flex min-w-0 flex-[1_1_240px] flex-col py-1">
                <span className="text-[15px] font-semibold">{t(w.info.features[k])}</span>
                {b ? <span className="text-[13px] font-semibold text-alert-text">{b.reason}</span>
                  : <span className="text-[13px] text-muted">{featureHint(t, k)}</span>}
              </div>
              {b && <button type="button" className="btn btn-sm btn-danger" onClick={() => toggle(k)}>{t("Desmarcar")}</button>}
            </div>
          );
        })}
      </section>

      {blockers.filter((b) => !keys.includes(b.feature)).map((b) => (
        <Note key={b.feature} tone="alert">{b.reason}. {t("Desmarque algum recurso ou libere espaço no disco.")}</Note>
      ))}

      <section className="card flex flex-col gap-3 p-5" aria-labelledby="h-items" aria-busy={syncing}>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 id="h-items" className="h2 m-0 flex-1">{t("O que será baixado")}</h2>
          {plan && !syncing && (
            <span className={`text-sm tabular-nums ${diskBlocked ? "font-semibold text-alert-text" : "text-body"}`}>
              {t("{need} a baixar · {free} livres no disco", { need: gbText(need ?? plan.missing_gb), free: gbText(free) })}
            </span>
          )}
        </div>
        {syncing && !plan && <p className="m-0 text-sm text-muted">{t("Calculando…")}</p>}
        {plan && items.length === 0 && <p className="m-0 text-sm text-muted">{t("Nada marcado. Marque pelo menos a Base para a fábrica funcionar.")}</p>}
        {items.length > 0 && (
          <ul className="m-0 flex list-none flex-col p-0">
            {items.map((i) => (
              <li key={`${i.kind}:${i.id}`} className="flex flex-col gap-1 border-t border-line-soft py-2.5 first:border-t-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="min-w-0 flex-1 text-[15px]">
                    <span className="font-semibold">{i.id}</span>{" "}
                    <span className="text-[13px] text-muted">{i.kind === "tool" ? t("programa") : t("modelo")}</span>
                  </span>
                  <span className="text-sm tabular-nums text-muted">{gbText(i.size_gb)}</span>
                  <ItemState item={i} />
                </div>
                {i.state === "failed" && i.error && <span className="text-[13px] text-alert-text">{i.error}</span>}
              </li>
            ))}
          </ul>
        )}
        {(running || readyCount > 0) && items.length > 0 && (
          <div className="flex flex-col gap-1.5" aria-live="polite">
            <div className="h-2 overflow-hidden rounded-full bg-track">
              <div className="h-full rounded-full bg-go transition-[width] duration-500" style={{ width: `${Math.round((readyCount / items.length) * 100)}%` }} />
            </div>
            <span className="text-[13px] text-muted">
              {running ? t("{n} de {total} prontos. Pode seguir para o próximo passo; a instalação continua.", { n: readyCount, total: items.length })
                : t("{n} de {total} prontos.", { n: readyCount, total: items.length })}
            </span>
          </div>
        )}
      </section>

      {err && <Note tone="alert">{err}</Note>}
      {!running && blockers.length > 0 && (
        <p className="m-0 text-right text-[13px] text-alert-text">{t("Desmarque o que está em vermelho para liberar a instalação.")}</p>
      )}
      <StepFooter onBack={w.back}>
        {failed.length > 0 && !running && (
          <button type="button" className="btn" disabled={starting || blockers.length > 0} onClick={install}>{t("Tentar de novo")}</button>
        )}
        <button type="button" className={`btn ${running || allReady ? "btn-go" : ""}`} onClick={w.next}>
          {running || allReady ? t("Avançar") : t("Pular por agora")}
        </button>
        {!running && !allReady && (
          <button type="button" className="btn btn-go" disabled={!canInstall} onClick={install}>{starting ? t("Começando…") : t("Instalar")}</button>
        )}
      </StepFooter>
    </>
  );
}

function ItemState({ item }: { item: InstallItem }) {
  const { t } = useT();
  const [label, cls] = {
    ready: [t("pronto"), "bg-go-soft text-go-text"],
    missing: [t("falta baixar"), "bg-chip text-body"],
    running: [t("instalando…"), "bg-chip text-warn-text"],
    failed: [t("erro"), "bg-alert-soft text-alert-text"],
  }[item.state];
  return <span className={`pill px-2 py-0.5 text-[12px] ${cls}`}>{label}</span>;
}
