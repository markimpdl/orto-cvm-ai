"use client";

import Link from "next/link";
import { useState } from "react";
import { useAppData } from "@/components/AppData";
import AutopilotSwitch from "@/components/AutopilotSwitch";
import { PageHeader, Tabs } from "@/components/Bits";
import CleanupPanel from "@/components/CleanupPanel";
import CurationSwitch from "@/components/CurationSwitch";
import { useT } from "@/lib/i18n";
import EngineModels from "./EngineModels";
import Integrations from "./Integrations";
import StyleRules from "./StyleRules";
import Updates from "./Updates";

type Tab = "auto" | "keys" | "models" | "rules" | "upkeep";

/** Configurações: autopilot and approvals, who writes + the ElevenLabs key, model per job, rules per video type, cleanup, updates (public installs only). */
export default function SettingsPage() {
  const { t } = useT();
  const [tab, setTab] = useState<Tab>("auto");
  return (
    <>
      <PageHeader title={t("Configurações")} subtitle={t("O que a fábrica faz sozinha, quem escreve, os modelos, as regras de cada tipo de vídeo e a limpeza do disco.")} />
      <Tabs label={t("Seções")} value={tab} onChange={setTab} tabs={[["auto", t("Automação")], ["keys", t("Integrações")], ["models", t("Modelos")], ["rules", t("Regras por tipo")], ["upkeep", t("Manutenção")]]} />
      {tab === "auto" && <Automation />}
      {tab === "keys" && <Integrations />}
      {tab === "models" && <EngineModels />}
      {tab === "rules" && <StyleRules />}
      {tab === "upkeep" && <Upkeep />}
    </>
  );
}

function Automation() {
  const { t } = useT();
  const { reload, setToast } = useAppData();
  return (
    <div className="flex flex-col gap-3">
    <AutopilotSwitch
      onCreated={(n) => { reload(); setToast({ msg: n ? (n > 1 ? t("Piloto automático colocou {n} temas na fila", { n }) : t("Piloto automático colocou {n} tema na fila", { n })) : t("Piloto automático ligado. Nenhum tema novo acima do limite agora.") }); }}
      onApproval={(n) => { reload(); if (n) setToast({ msg: n > 1 ? t("{n} itens aguardando aprovação seguiram sozinhos", { n }) : t("{n} item aguardando aprovação seguiu sozinho", { n }) }); }}
    />
    <CurationSwitch />
    </div>
  );
}

function Upkeep() {
  const { t } = useT();
  const { reload } = useAppData();
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-[28px]">
    <Updates />
    <section className="card flex flex-wrap items-center gap-4 p-5" aria-labelledby="h-cleanup">
      <div className="flex flex-[1_1_320px] flex-col gap-1">
        <h2 id="h-cleanup" className="h2 m-0">{t("Limpar projetos antigos")}</h2>
        <p className="m-0 text-sm text-body">{t("Apaga só os arquivos de vídeo grandes de vídeos já publicados. Roteiro, voz, thumbnail, SEO e o link do YouTube ficam.")}</p>
        <p className="m-0 text-[13px] text-muted">{t("Mostra antes o que seria apagado; nada é apagado sem confirmar.")}</p>
      </div>
      <button className="btn" onClick={() => setOpen(true)}>{t("Ver o que dá para apagar")}</button>
      {open && <CleanupPanel onClose={() => setOpen(false)} onDone={reload} />}
    </section>
    <section className="card flex flex-wrap items-center gap-4 p-5" aria-labelledby="h-wizard">
      <div className="flex flex-[1_1_320px] flex-col gap-1">
        <h2 id="h-wizard" className="h2 m-0">{t("Assistente de instalação")}</h2>
        <p className="m-0 text-sm text-body">{t("Reveja os recursos, os modelos baixados e as chaves. Nada do que já está instalado é apagado.")}</p>
      </div>
      <Link href="/instalar?revisit=1" className="btn">{t("Abrir o assistente")}</Link>
    </section>
    </div>
  );
}
