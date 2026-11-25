"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { api, apiDetail, type AiCheck, type AiStatus } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Wizard } from "./types";
import { Choice, Note, StepFooter, StepHead } from "./ui";

type Pick = "claude" | "codex" | "both";
type Which = "claude" | "codex";

/** Step 3: who writes (Claude, Codex or both) and whether each one is logged in on this computer. */
export default function Ai({ w }: { w: Wizard }) {
  const { t } = useT();
  const { setToast } = useAppData();
  const [st, setSt] = useState<AiStatus | null | undefined>(undefined);
  const [choice, setChoice] = useState<Pick | null>((w.choices.ai as Pick) ?? null);
  const [testing, setTesting] = useState<Which | null>(null);
  const [saving, setSaving] = useState(false);

  // a finished (or older) install already has a writer: start from it and never overwrite it unless the person changes it
  const existing = Boolean(w.info.state.done || w.info.state.legacy);
  const [initial, setInitial] = useState<Pick | null | undefined>(existing ? undefined : null);

  useEffect(() => {
    if (existing) {
      api<{ writer: string; codex_fallback: boolean }>("/settings/engines").then((e) => {
        const cur: Pick | null = e.writer === "claude" ? (e.codex_fallback ? "both" : "claude") : e.writer === "codex" ? "codex" : null; // manual: no preselection
        setInitial(cur);
        setChoice(cur);
      }).catch(() => setInitial(null));
    }
    api<AiStatus>("/setup/ai").then((s) => {
      setSt(s);
      if (!existing) setChoice((c) => c ?? (s.claude.logged_in && s.codex.logged_in ? "both" : !s.claude.logged_in && s.codex.logged_in ? "codex" : "claude"));
    }).catch(() => setSt(null));
  }, [existing]);

  async function test(which: Which) {
    setTesting(which);
    try {
      const r = await api<AiCheck>("/setup/ai/test", { method: "POST", body: JSON.stringify({ which }) });
      setSt((s) => (s ? { ...s, [which]: r } : s));
    } catch {
      setToast({ msg: t("Não consegui testar agora. A API está ligada?") });
    } finally {
      setTesting(null);
    }
  }

  async function next() {
    if (!choice || (existing && choice === initial)) { w.next(); return; } // unchanged: leave the saved engines alone
    setSaving(true);
    try {
      await api("/setup/ai", { method: "PUT", body: JSON.stringify({ choice }) });
      await w.save({ ai: choice });
      w.next();
    } catch (e) {
      setToast({ msg: t("Não consegui salvar: {err}", { err: apiDetail(e) }) });
    } finally {
      setSaving(false);
    }
  }

  const needs: Which[] = choice === "both" ? ["claude", "codex"] : choice ? [choice] : [];
  const missing = st ? needs.filter((k) => !st[k].logged_in) : [];

  return (
    <>
      <StepHead title={t("Quem escreve os roteiros")}
        lead={t("A fábrica usa uma IA de assinatura para escrever roteiros, títulos e ideias. Pode ser o Claude, o Codex (do ChatGPT) ou os dois. Nada é cobrado à parte: vale a assinatura que você já tem.")} />
      <div role="radiogroup" aria-label={t("Quem escreve os roteiros")} className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-2">
        <Choice on={choice === "claude"} onPick={() => setChoice("claude")} label={t("Só Claude")} hint={t("Quando ele chegar no limite de uso, as etapas esperam ele voltar.")} />
        <Choice on={choice === "both"} onPick={() => setChoice("both")} label={t("Claude e Codex")} hint={t("O Claude escreve; o Codex assume quando o Claude chega no limite.")} />
        <Choice on={choice === "codex"} onPick={() => setChoice("codex")} label={t("Só Codex")} hint={t("O Codex escreve tudo, inclusive as séries infantis.")} />
      </div>
      {st === null && <Note tone="alert">{t("Não consegui verificar as IAs. A API está ligada?")}</Note>}
      {st && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-3">
          <Engine name="Claude" check={st.claude} testing={testing === "claude"} onTest={() => test("claude")}
            how={[t("Abra o Terminal e rode:"), "claude", t("Dentro dele, digite /login e entre com a sua conta do Claude (Pro ou Max). Depois volte aqui e aperte Testar.")]}
            install="npm install -g @anthropic-ai/claude-code" />
          <Engine name="Codex" check={st.codex} testing={testing === "codex"} onTest={() => test("codex")}
            how={[t("Abra o Terminal e rode:"), "codex login", t("Entre com a sua conta do ChatGPT. Depois volte aqui e aperte Testar.")]}
            install="npm install -g @openai/codex" />
        </div>
      )}
      {existing && initial === null && !choice && (
        <Note>{t("Hoje os roteiros começam só quando você manda (modo manual). Escolha uma opção acima para mudar, ou avance para manter assim.")}</Note>
      )}
      {st && missing.length > 0 && (
        <Note tone="warn">{t("Você pode seguir assim. Enquanto a IA escolhida não estiver conectada, os roteiros ficam esperando na fila.")}</Note>
      )}
      <StepFooter onBack={w.back}>
        <button type="button" className="btn btn-go" disabled={saving || initial === undefined || (!choice && !existing)} onClick={next}>{saving ? t("Salvando…") : t("Avançar")}</button>
      </StepFooter>
    </>
  );
}

function Engine({ name, check, testing, onTest, how, install }: {
  name: string; check: AiCheck; testing: boolean; onTest: () => void; how: [string, string, string]; install: string;
}) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const pill = check.logged_in
    ? <span className="pill bg-go-soft text-go-text">{t("conectado ✓")}</span>
    : check.installed
      ? <span className="pill bg-chip text-warn-text">{t("falta entrar")}</span>
      : <span className="pill bg-chip text-warn-text">{t("não instalado")}</span>;
  return (
    <section className="card flex flex-col gap-3 p-5" aria-label={name}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="h2 m-0 flex-1">{name}</h2>
        {pill}
      </div>
      {check.reason && <p className="m-0 text-sm text-alert-text">{t(check.reason)}</p>}
      {!check.logged_in && (
        <>
          <button type="button" className="btn btn-sm w-fit" aria-expanded={open} onClick={() => setOpen(!open)}>{t("Como entrar")}</button>
          {open && (
            <div className="flex flex-col gap-2 rounded-xl bg-bg p-3.5 text-sm text-body">
              {!check.installed && (
                <>
                  <span>{t("Primeiro instale, no Terminal:")}</span>
                  <Copy text={install} />
                </>
              )}
              <span>{how[0]}</span>
              <Copy text={how[1]} />
              <span>{how[2]}</span>
            </div>
          )}
        </>
      )}
      <button type="button" className="btn btn-sm w-fit" disabled={testing} onClick={onTest}>{testing ? t("Testando…") : t("Testar")}</button>
    </section>
  );
}

function Copy({ text }: { text: string }) {
  const { t } = useT();
  const [done, setDone] = useState(false);
  return (
    <span className="flex items-center gap-2">
      <code className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-surface px-3 py-2 text-[13px] text-ink">{text}</code>
      <button type="button" className="btn btn-sm" onClick={() => {
        navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(() => {});
      }}>{done ? t("Copiado") : t("Copiar")}</button>
    </span>
  );
}
