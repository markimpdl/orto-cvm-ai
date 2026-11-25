"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, type InstallStatus, type SetupFeatures } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { chosenFeatures, type Wizard } from "./types";
import { Note, StepFooter, StepHead } from "./ui";

const POLL_MS = 3000;

/** Step 7: marks the install done (POST /setup/finish), sums up the choices and follows the models still downloading. */
export default function Done({ w }: { w: Wizard }) {
  const { t, lang } = useT();
  const [finished, setFinished] = useState<boolean | null>(null);
  const [feats, setFeats] = useState<SetupFeatures | null>(null);
  const [status, setStatus] = useState<InstallStatus | null>(null);
  const once = useRef(false);

  useEffect(() => {
    if (once.current) return;
    once.current = true;
    api("/setup/finish", { method: "POST" }).then(() => setFinished(true)).catch(() => setFinished(false));
    api<SetupFeatures>("/setup/features").then(setFeats).catch(() => {});
  }, []);
  useEffect(() => {
    const load = () => api<InstallStatus>("/setup/models/status").then(setStatus).catch(() => {});
    load();
    const i = setInterval(load, POLL_MS);
    return () => clearInterval(i);
  }, []);

  const ai = { claude: t("Só Claude"), codex: t("Só Codex"), both: t("Claude e Codex") }[String(w.choices.ai)] ?? t("não escolhido");
  const on = feats ? [
    feats.ai_ideas && t("Sugestões da IA"),
    feats.lives.length > 0 && t("Lives"),
    feats.follow && t("Seguir canais"),
    feats.avatars && t("Avatares"),
    feats.vidiq && "vidIQ",
    feats.elevenlabs && "ElevenLabs",
  ].filter(Boolean).join(" · ") : "…";
  const models = chosenFeatures(w).map((f) => t(w.info.features[f] ?? f)).join(" · ");
  const items = status?.items ?? [];
  const ready = items.filter((i) => i.state === "ready").length;
  const failed = items.filter((i) => i.state === "failed").length;

  const rows: [string, string][] = [
    [t("Idioma do painel"), lang === "en" ? "English" : "Português"],
    [t("Quem escreve"), ai],
    [t("Recursos ligados"), on || t("nenhum")],
    [t("Modelos"), models || t("nenhum")],
    [t("Canal"), w.choices.channel_name ? String(w.choices.channel_name) : t("criar depois, em Canais")],
  ];

  return (
    <>
      <StepHead title={t("Tudo pronto")}
        lead={t("A fábrica está configurada. Na página Em alta você vê os assuntos do momento e escolhe o primeiro vídeo. Qualquer escolha daqui muda depois em Configurações.")} />
      {finished === false && <Note tone="alert">{t("Não consegui marcar a instalação como concluída. A API está ligada? Volte a este passo para tentar de novo.")}</Note>}
      <section className="card p-5" aria-label={t("Resumo")}>
        <dl className="m-0 grid grid-cols-[minmax(0,max-content)_1fr] gap-x-6 gap-y-3">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-sm text-muted">{k}</dt>
              <dd className="m-0 text-[15px] font-semibold">{v}</dd>
            </div>
          ))}
        </dl>
      </section>
      {items.length > 0 && (
        <section className="card flex flex-col gap-2 p-5" aria-live="polite" aria-labelledby="h-dl">
          <h2 id="h-dl" className="h2 m-0">{status?.running ? t("Ainda baixando") : t("Modelos")}</h2>
          <div className="h-2 overflow-hidden rounded-full bg-track">
            <div className="h-full rounded-full bg-go transition-[width] duration-500" style={{ width: `${Math.round((ready / items.length) * 100)}%` }} />
          </div>
          <span className="text-sm text-body">
            {t("{n} de {total} prontos.", { n: ready, total: items.length })}{" "}
            {status?.running ? t("Pode começar a usar; o que depende deles espera a instalação terminar.") : ""}
            {failed > 0 ? t("{n} com erro: volte ao passo Modelos e aperte Tentar de novo.", { n: failed }) : ""}
          </span>
        </section>
      )}
      <StepFooter onBack={w.back}>
        <Link href="/em-alta" className="btn btn-go">{t("Ir para Em alta")}</Link>
      </StepFooter>
    </>
  );
}
