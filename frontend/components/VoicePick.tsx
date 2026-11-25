"use client";

import { effectiveVoiceLabel, useVoiceVariants } from "@/components/VoiceChoice";
import { useT } from "@/lib/i18n";

/** Project-level voice override. Empty means the channel's configured voice remains authoritative. */
export default function VoicePick({ avatarId, value, effective, editable, onChange }: {
  avatarId: number; value: string | null; effective: string | null; editable: boolean; onChange: (v: string | null) => void;
}) {
  const voices = useVoiceVariants(avatarId);
  const { t } = useT();
  if (voices.length < 2) return null;
  const current = voices.find((v) => v.key === value);
  const unavailableOverride = value && !current ? value : null;
  const effectiveVoice = effectiveVoiceLabel(voices, effective);
  const defaultLabel = effectiveVoice ? t("Padrão do canal ({voice})", { voice: t(effectiveVoice) }) : t("Padrão do canal");
  return (
    <>
      ·
      {editable ? (
        <label className="inline-flex items-center gap-1.5">
          {t("voz")}
          <select className="input min-h-9 px-2 text-[15px]" value={current?.key ?? unavailableOverride ?? ""} onChange={(e) => onChange(e.target.value || null)} aria-label={t("Voz do avatar")}>
            {unavailableOverride && <option value={unavailableOverride}>{t("Voz selecionada indisponível ({voice})", { voice: unavailableOverride })}</option>}
            <option value="">{defaultLabel}</option>
            {voices.map((v) => <option key={v.key} value={v.key}>{t(v.label)}</option>)}
          </select>
        </label>
      ) : (
        <span>{t("voz")} {current ? t(current.label) : unavailableOverride ? t("Voz selecionada indisponível ({voice})", { voice: unavailableOverride }) : defaultLabel}</span>
      )}
    </>
  );
}
