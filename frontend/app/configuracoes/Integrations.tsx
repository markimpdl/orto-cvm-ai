"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type Engines = {
  writer: "claude" | "codex" | "manual";
  codex_fallback: boolean;
  video: Record<"kids" | "realistic" | "cartoon", string>;
  image_engine: string;
  options: { video: EngineOption[]; image: EngineOption[] };
  codex_installed: boolean;
};
export type EngineOption = { id: string; label: string; ready: boolean };
type Eleven = { configured: boolean; source?: "keychain" | "env" | null; remaining?: number | null; error?: string };

/** Configurações > Integrações: who writes the scripts (Claude, Codex or by hand) and the ElevenLabs key. */
export default function Integrations() {
  return (
    <div className="flex flex-col gap-[28px]">
      <Writer />
      <ElevenKey />
    </div>
  );
}

function Writer() {
  const { t } = useT();
  const { setToast } = useAppData();
  const [cfg, setCfg] = useState<Engines | null | undefined>(undefined);
  useEffect(() => {
    api<Engines>("/settings/engines").then(setCfg).catch(() => setCfg(null));
  }, []);
  async function save(body: Partial<Engines>) {
    try {
      const r = await api<Engines & { started: number[] }>("/settings/engines", { method: "PUT", body: JSON.stringify(body) });
      setCfg(r);
      if (r.started?.length) setToast({ msg: r.started.length > 1 ? t("{n} etapas que esperavam começaram agora", { n: r.started.length }) : t("{n} etapa que esperava começou agora", { n: r.started.length }) });
    } catch {
      setToast({ msg: t("Não consegui salvar. A API está ligada?") });
    }
  }
  if (cfg === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (cfg === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  const choices: { id: Engines["writer"]; label: string; hint: string }[] = [
    { id: "claude", label: t("Claude"), hint: cfg.codex_fallback ? t("O Codex assume quando o Claude chega no limite de uso.") : t("Quando o Claude chega no limite, as etapas esperam ele voltar.") },
    { id: "codex", label: t("Só Codex"), hint: t("Roteiros infantis e em árabe continuam com o Claude.") },
    { id: "manual", label: t("Manual"), hint: t("Nada começa sozinho. Você aperta “Rodar no Claude” em cada projeto.") },
  ];
  return (
    <section className="card flex flex-col gap-4 p-5" aria-labelledby="h-writer">
      <div className="flex flex-col gap-1">
        <h2 id="h-writer" className="h2 m-0">{t("Quem escreve os roteiros")}</h2>
        <p className="m-0 text-sm text-body">{t("Vale também para o SEO e as ideias. A troca vale na hora, sem reiniciar nada.")}</p>
      </div>
      <div role="radiogroup" aria-labelledby="h-writer" className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-2">
        {choices.map((c) => {
          const on = cfg.writer === c.id;
          return (
            <button key={c.id} type="button" role="radio" aria-checked={on} onClick={() => !on && save({ writer: c.id })}
              className={`pick flex flex-col gap-1 rounded-xl border p-4 ${on ? "border-ink bg-surface shadow-[inset_0_0_0_1px_var(--color-ink)]" : "border-line bg-surface hover:bg-chip"}`}>
              <span className="flex items-center gap-2 text-[15px] font-semibold">
                <span aria-hidden className={`h-2.5 w-2.5 rounded-full ${on ? "bg-go" : "bg-field"}`} />
                {c.label}
              </span>
              <span className="text-[13px] text-muted">{c.hint}</span>
            </button>
          );
        })}
      </div>
      <label className="flex cursor-pointer items-center gap-3 border-t border-line-soft pt-4">
        <button type="button" role="switch" aria-checked={cfg.codex_fallback} aria-label={t("Codex como reserva")}
          onClick={() => save({ codex_fallback: !cfg.codex_fallback })}
          className={`pick flex h-[24px] w-[42px] flex-none rounded-full border-0 p-[3px] ${cfg.codex_fallback ? "justify-end bg-go" : "justify-start bg-field"}`}>
          <span className="block h-[18px] w-[18px] rounded-full bg-white" />
        </button>
        <span className="flex flex-col">
          <span className="text-sm font-semibold">{t("Codex como reserva")}</span>
          <span className="text-xs text-muted">
            {cfg.codex_installed ? t("Usa sua assinatura do ChatGPT quando o Claude estiver no limite.") : t("O Codex não está instalado ou falta fazer login nesta máquina.")}
          </span>
        </span>
      </label>
    </section>
  );
}

function ElevenKey() {
  const { t } = useT();
  const [state, setState] = useState<Eleven | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    api<Eleven>("/settings/secrets/elevenlabs").then((s) => {
      setState(s);
      if (s.configured) check();
    }).catch(() => setState(null));
  }, []);
  async function check() {
    try {
      const r = await api<Eleven>("/settings/secrets/elevenlabs/test", { method: "POST" });
      setState((s) => ({ ...(s ?? { configured: false }), ...r }));
    } catch { /* the status line stays as it was */ }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api<Eleven>("/settings/secrets/elevenlabs", { method: "PUT", body: JSON.stringify({ key: key.trim() }) });
      setState(r);
      await check();
    } catch (x) {
      setErr(String(x).includes("422") ? t("Essa chave não parece certa. Copie de novo do site da ElevenLabs.") : t("Não consegui guardar a chave no Keychain."));
    } finally {
      setKey("");
      setBusy(false);
    }
  }
  async function remove() {
    if (!confirm(t("Tirar a chave da ElevenLabs do Keychain?"))) return;
    setState(await api<Eleven>("/settings/secrets/elevenlabs", { method: "DELETE" }));
  }
  const fmt = (n: number) => n.toLocaleString();
  let status: React.ReactNode = <span className="pill bg-chip text-body">{t("não configurada")}</span>;
  if (state?.configured) {
    status = state.error
      ? <span className="pill bg-alert-soft text-alert-text">{t(state.error)}</span>
      : <span className="pill bg-go-soft text-go-text">{t("configurada ✓")}{state.remaining != null && ` · ${t("{n} créditos", { n: fmt(state.remaining) })}`}</span>;
  }
  return (
    <section className="card flex flex-col gap-4 p-5" aria-labelledby="h-eleven">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id="h-eleven" className="h2 m-0">ElevenLabs</h2>
        {state && status}
      </div>
      <p className="m-0 text-sm text-body">
        {t("A chave fica guardada no Keychain do Mac. Ela não vai para o banco, para os logs nem aparece de novo nesta tela.")}
        {state?.source === "env" && " " + t("Agora ela está sendo lida do arquivo .env.")}
      </p>
      <form onSubmit={save} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-[1_1_320px] flex-col gap-1.5 text-sm font-semibold">
          {state?.configured ? t("Trocar a chave") : t("Chave da API")}
          <input type="password" autoComplete="off" spellCheck={false} className="input font-normal" value={key}
            onChange={(e) => setKey(e.target.value)} placeholder="sk_…" />
        </label>
        <button className="btn btn-go" disabled={busy || !key.trim()}>{busy ? t("Salvando…") : t("Salvar chave")}</button>
        {state?.configured && <button type="button" className="btn" onClick={check}>{t("Ver créditos")}</button>}
        {state?.source === "keychain" && <button type="button" className="btn btn-danger" onClick={remove}>{t("Remover")}</button>}
      </form>
      {err && <p role="alert" className="m-0 text-sm text-alert-text">{err}</p>}
    </section>
  );
}
