"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Cfg = { enabled: boolean; max_attempts: number };

/** Configurações > Automação: the validation step after the render (backend Setting "curation", cvm/curation.py). */
export default function CurationSwitch() {
  const { t } = useT();
  const [cfg, setCfg] = useState<Cfg | null>(null);
  useEffect(() => {
    api<Cfg>("/settings/curation").then(setCfg).catch(() => {});
  }, []);
  async function save(change: Partial<Cfg>) {
    setCfg(await api<Cfg>("/settings/curation", { method: "PUT", body: JSON.stringify(change) }));
  }
  const on = !!cfg?.enabled;
  return (
    <div className="card flex flex-wrap items-center gap-x-5 gap-y-3 px-5 py-4">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={t("Conferência automática pela IA")}
        onClick={() => cfg && save({ enabled: !on })}
        disabled={!cfg}
        className={`pick flex h-[30px] w-[52px] flex-none rounded-full border-0 p-[3px] ${on ? "justify-end bg-go" : "justify-start bg-field"}`}
      >
        <span className="block h-6 w-6 rounded-full bg-white" />
      </button>
      <div className="flex flex-[1_1_320px] flex-col gap-0.5">
        <span className="text-base font-semibold">{on ? t("Conferência automática ligada") : t("Conferência automática desligada")}</span>
        <span className="text-sm text-muted">
          {t("Depois do render, a IA confere narração, imagens, bandeiras, volume, tela final, estrutura e fatos. Se achar um problema, manda o vídeo de volta para a etapa certa antes de chegar a você.")}
        </span>
      </div>
      <label className="flex items-center gap-2 text-sm font-semibold">
        {t("Tentativas")}
        <select className="input" disabled={!cfg || !on} value={cfg?.max_attempts ?? 2} onChange={(e) => save({ max_attempts: Number(e.target.value) })}>
          {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
    </div>
  );
}
