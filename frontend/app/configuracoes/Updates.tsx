"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiDetail, getUpdate, startUpdate, type UpdateInfo, type UpdateStep } from "@/lib/api";
import { useT } from "@/lib/i18n";

const BUSY_MS = 15000;

/** "Atualizações" (Manutenção): installs cloned from the public repo move to the newest version. Hidden elsewhere. */
export default function Updates() {
  const { t } = useT();
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [offline, setOffline] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // set by a click until a poll shows the run: cleared when the poll shows it finished, or after BUSY_MS (the
  // detached run can die before the first poll; the dead run is then reported below)
  const clicked = useRef<{ at: number; prevStart?: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await getUpdate();
      setInfo(r);
      setOffline(false);
      const c = clicked.current;
      if (c && !r.running) {
        const finished = (r.run?.state === "done" || r.run?.state === "failed") && r.run.started_at !== c.prevStart;
        if (finished || Date.now() - c.at > BUSY_MS) {
          clicked.current = null;
          setBusy(false);
        }
      }
    } catch {
      setOffline(true); // the API restarts during an update: keep what we had and try again
    }
  }, []);

  const running = !!info?.running || busy;
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!running && !offline) return;
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, [running, offline, load]);
  useEffect(() => {
    if (info?.running) { clicked.current = null; setBusy(false); }
  }, [info?.running]);

  if (!info?.public_install) return null;

  const STEPS: Record<UpdateStep, string> = {
    starting: t("Começando a atualização"),
    checking: t("Conferindo a versão nova"),
    pausing: t("Pausando a fila"),
    waiting: t("Esperando os vídeos terminarem a etapa atual"),
    downloading: t("Baixando a versão nova"),
    installing: t("Atualizando as bibliotecas (pode levar alguns minutos)"),
    restarting: t("Reiniciando o CVM"),
    resuming: t("Voltando a fila"),
    rollback: t("Deu erro. Voltando para a versão anterior"),
    done: t("Pronto"),
    failed: t("A atualização falhou"),
  };
  const run = info.run;

  async function go() {
    if (!info?.latest) return;
    const msg = t("Atualizar para a versão {v}? Cada vídeo da fila termina a etapa atual, a fila pausa, o CVM reinicia e a fila continua sozinha. Se algo der errado, o CVM volta para a versão {cur}.", { v: info.latest, cur: info.current ?? t("atual") });
    if (!confirm(msg)) return;
    setErr("");
    clicked.current = { at: Date.now(), prevStart: info.run?.started_at };
    setBusy(true);
    try {
      await startUpdate();
      await load();
    } catch (e) {
      clicked.current = null;
      setBusy(false);
      setErr(apiDetail(e));
    }
  }

  return (
    <section className="card flex flex-wrap items-center gap-4 p-5" aria-labelledby="h-update">
      <div className="flex flex-[1_1_320px] flex-col gap-1">
        <h2 id="h-update" className="h2 m-0">{t("Atualizações")}</h2>
        <p className="m-0 text-sm text-body">
          {t("Versão instalada: {v}", { v: info.current ?? t("sem número") })}
          {info.available && info.latest ? ` · ${t("nova: {v}", { v: info.latest })}` : ""}
        </p>
        {!running && !info.available && !info.error && !info.skipped && <p className="m-0 text-[13px] text-muted">{t("Você já está na versão mais nova.")}</p>}
        {!running && info.skipped && <p className="m-0 text-[13px] text-muted">{t("A versão {v} não funcionou neste computador, então o CVM voltou para a anterior. O botão volta quando sair uma versão mais nova.", { v: info.skipped })}</p>}
        {info.error && !running && <p className="m-0 text-[13px] text-muted">{info.error}</p>}
        {info.supported === false && <p className="m-0 text-[13px] text-muted">{t("Atualizar pelo app ainda não funciona neste sistema.")}</p>}
        {running && (
          <p className="m-0 text-sm text-body" role="status">
            {offline ? STEPS.restarting : STEPS[run?.step ?? "starting"]}…
            {run?.step === "waiting" && run.label ? <span className="block text-[13px] text-muted">{run.label}</span> : null}
          </p>
        )}
        {!running && run?.state === "done" && run.to && <p className="m-0 text-sm text-body" role="status">{t("Atualizado para a versão {v}. Se a página parecer estranha, recarregue.", { v: run.to })}</p>}
        {!running && run?.state === "failed" && run.error && <p className="m-0 text-sm text-alert-text" role="alert">{run.error}</p>}
        {!running && run?.state === "running" && <p className="m-0 text-sm text-alert-text" role="alert">{t("A atualização parou antes de terminar. Veja data/logs/update.log e tente de novo.")}</p>}
        {!running && run?.warning && <p className="m-0 text-sm text-alert-text" role="alert">{run.warning}</p>}
        {err && <p className="m-0 text-sm text-alert-text" role="alert">{err}</p>}
      </div>
      {info.available && info.supported !== false && (
        <button className="btn" onClick={go} disabled={running}>
          {running ? t("Atualizando…") : t("Atualizar para {v}", { v: info.latest ?? "" })}
        </button>
      )}
    </section>
  );
}
