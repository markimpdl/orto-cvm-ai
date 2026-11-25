"use client";

import { useT } from "@/lib/i18n";
import { chosenFeatures, type Wizard } from "./types";
import { Note, StepFooter, StepHead, gbText } from "./ui";

/** Step 1: what was detected on this computer, and whether the suggested models fit on the disk (warning only). */
export default function System({ w }: { w: Wizard }) {
  const { t } = useT();
  const hw = w.info.hardware;
  const feats = chosenFeatures(w);

  const os = { mac: t("macOS (Apple Silicon)"), windows: t("Windows"), other: t("Outro sistema") }[hw.platform];
  const rows: [string, string][] = [
    [t("Sistema"), os],
    [t("Processador"), hw.chip || t("não identificado")],
    [t("Memória (RAM)"), hw.ram_gb ? `${hw.ram_gb} GB` : t("não identificada")],
    ...(hw.platform === "windows" ? [[t("Memória da placa de vídeo"), hw.vram_gb ? `${hw.vram_gb} GB` : t("nenhuma placa NVIDIA encontrada")] as [string, string]] : []),
    [t("Disco livre"), gbText(hw.disk_free_gb)],
  ];
  // one number for the whole wizard: GET /setup's disk.need_gb is what the disk blocker compared
  const disk = w.info.blockers.find((b) => b.feature === "disk");
  const need = w.info.disk?.need_gb ?? null;
  const labels = feats.map((f) => t(w.info.features[f] ?? f));

  return (
    <>
      <StepHead title={t("Este computador")} lead={t("O que encontramos neste computador decide quais recursos cabem nele. Este passo não instala nada.")} />
      <section className="card p-5" aria-label={t("Hardware detectado")}>
        <dl className="m-0 grid grid-cols-[minmax(0,max-content)_1fr] gap-x-6 gap-y-3">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-sm text-muted">{k}</dt>
              <dd className="m-0 text-[15px] font-semibold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      </section>
      {disk ? (
        <Note tone="warn">{disk.reason}. {t("Vale liberar espaço ou escolher menos recursos no passo Modelos.")}</Note>
      ) : need !== null && (
        <Note tone="ok">
          {need > 0
            ? t("Cabe tudo: os modelos sugeridos ocupam {need} e o disco tem {free} livres.", { need: gbText(need), free: gbText(hw.disk_free_gb) })
            : t("Os modelos sugeridos já estão nesta máquina.")}
        </Note>
      )}
      {labels.length > 0 && (
        <p className="m-0 text-sm text-body">
          {t("Sugerido para este computador:")} <span className="font-semibold">{labels.join(" · ")}</span>. {t("Você muda isso no passo Modelos.")}
        </p>
      )}
      <StepFooter>
        <button type="button" className="btn btn-go" onClick={w.next}>{t("Avançar")}</button>
      </StepFooter>
    </>
  );
}
