"use client";

import { useT, type Lang } from "@/lib/i18n";
import type { Wizard } from "./types";
import { Choice, StepFooter, StepHead } from "./ui";

/** Step 2: the interface language (the same switch as the sidebar's PT | EN, kept in this browser). */
export default function Language({ w }: { w: Wizard }) {
  const { t, lang, setLang } = useT();
  const pick = (l: Lang) => {
    setLang(l);
    w.save({ ui_language: l }).catch(() => {});
  };
  return (
    <>
      <StepHead title={t("Idioma do painel")} lead={t("Em que idioma você quer ler os menus e botões. Os vídeos têm idioma próprio, escolhido em cada canal. Dá para trocar depois no canto do menu.")} />
      <div role="radiogroup" aria-label={t("Idioma do painel")} className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-2">
        <Choice on={lang === "pt"} onPick={() => pick("pt")} label="Português" hint="Menus e botões em português do Brasil." />
        <Choice on={lang === "en"} onPick={() => pick("en")} label="English" hint="Menus and buttons in English." />
      </div>
      <StepFooter onBack={w.back}>
        <button type="button" className="btn btn-go" onClick={() => { w.save({ ui_language: lang }).catch(() => {}); w.next(); }}>{t("Avançar")}</button>
      </StepFooter>
    </>
  );
}
