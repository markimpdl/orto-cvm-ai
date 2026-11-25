"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Autopilot = { enabled: boolean; min_score: number; max_per_day: number; auto_approve_script: boolean; auto_approve_render: boolean };

/** Autopilot (queues strong trends by itself) and the two auto-approval gates; lives in Configurações > Automação. */
export default function AutopilotSwitch({ onCreated, onApproval }: { onCreated: (n: number) => void; onApproval?: (released: number) => void }) {
  const [cfg, setCfg] = useState<Autopilot | null>(null);
  const { t } = useT();
  useEffect(() => {
    api<Autopilot>("/settings/autopilot").then(setCfg).catch(() => {});
  }, []);
  async function toggle() {
    if (!cfg) return;
    const r = await api<Autopilot & { created: number[] }>("/settings/autopilot", { method: "PUT", body: JSON.stringify({ enabled: !cfg.enabled }) });
    setCfg(r);
    if (r.enabled) onCreated(r.created.length);
  }
  async function toggleApproval(key: "auto_approve_script" | "auto_approve_render") {
    if (!cfg) return;
    const r = await api<Autopilot & { released: number[] }>("/settings/autopilot", { method: "PUT", body: JSON.stringify({ [key]: !cfg[key] }) });
    setCfg(r);
    onApproval?.(r.released.length);
  }
  const on = !!cfg?.enabled;
  const approvals: { key: "auto_approve_script" | "auto_approve_render"; label: string; hint: string }[] = [
    { key: "auto_approve_script", label: t("Aprovar roteiros sozinho"), hint: t("o roteiro do Claude segue direto para a narração") },
    { key: "auto_approve_render", label: t("Aprovar vídeos sozinho"), hint: t("o vídeo pronto é agendado na hora no canal do projeto") },
  ];
  return (
    <div className="card flex flex-wrap items-center gap-x-5 gap-y-3 px-5 py-4">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={t("Piloto automático")}
        onClick={toggle}
        disabled={!cfg}
        className={`pick flex h-[30px] w-[52px] flex-none rounded-full border-0 p-[3px] ${on ? "justify-end bg-go" : "justify-start bg-field"}`}
      >
        <span className="block h-6 w-6 rounded-full bg-white" />
      </button>
      <div className="flex flex-[1_1_320px] flex-col gap-0.5">
        <span className="text-base font-semibold">{on ? t("Piloto automático ligado") : t("Piloto automático desligado")}</span>
        <span className="text-sm text-muted">
          {t("Coloca na fila sozinho os temas com potencial {min}+, até {max} por dia em cada canal, no canal sugerido.", { min: cfg?.min_score ?? 75, max: cfg?.max_per_day ?? 3 })}
          {" "}{cfg?.auto_approve_script && cfg?.auto_approve_render ? t("Aprovações automáticas ligadas.") : t("Você aprova o que estiver marcado abaixo.")}
        </span>
      </div>
      <div className="flex w-full flex-wrap gap-x-6 gap-y-2 border-t border-[#e6e7ea] pt-3">
        {approvals.map((a) => {
          const v = !!cfg?.[a.key];
          return (
            <label key={a.key} className="flex flex-[1_1_260px] cursor-pointer items-center gap-3">
              <button
                type="button"
                role="switch"
                aria-checked={v}
                aria-label={a.label}
                onClick={() => toggleApproval(a.key)}
                disabled={!cfg}
                className={`pick flex h-[24px] w-[42px] flex-none rounded-full border-0 p-[3px] ${v ? "justify-end bg-go" : "justify-start bg-field"}`}
              >
                <span className="block h-[18px] w-[18px] rounded-full bg-white" />
              </button>
              <span className="flex flex-col">
                <span className="text-sm font-semibold">{a.label}</span>
                <span className="text-xs text-muted">{a.hint}</span>
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
