"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { api, STYLE_LABEL, STYLES } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Field = "thumbnail" | "title" | "hook" | "ending" | "bumper";
type Rules = Record<string, Record<Field, string>>;

/** Configurações > Regras por tipo: the user's own rules per video type, handed to the scriptwriter and the SEO
 * agent through get_project (`style_rules`). The skill files stay as they are. */
export default function StyleRules() {
  const { t } = useT();
  const [rules, setRules] = useState<Rules | null | undefined>(undefined);
  useEffect(() => {
    api<{ rules: Rules }>("/settings/style-rules").then((r) => setRules(r.rules)).catch(() => setRules(null));
  }, []);
  if (rules === undefined) return <div className="card p-5 text-muted">{t("Carregando…")}</div>;
  if (rules === null) return <div className="card p-5 text-alert-text">{t("Não consegui falar com a API. Ela está ligada?")}</div>;
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-sm text-body">{t("O que você escrever aqui vai junto para o Claude quando ele escreve o roteiro e o SEO de um vídeo desse tipo. Se bater de frente com a regra padrão, vale a sua. Campo vazio = regra padrão.")}</p>
      {STYLES.filter((s) => rules[s]).map((s) => <StyleCard key={s} style={s} initial={rules[s]} />)}
    </div>
  );
}

function StyleCard({ style, initial }: { style: string; initial: Record<Field, string> }) {
  const { t } = useT();
  const { setToast } = useAppData();
  const [saved, setSaved] = useState(initial);
  const [d, setD] = useState(initial);
  const [busy, setBusy] = useState(false);
  const fields: [Field, string, string][] = [
    ["title", t("Título"), t("Ex.: pergunta curta, sem caixa alta")],
    ["thumbnail", t("Thumbnail"), t("Ex.: no máximo 3 palavras, rosto com emoção")],
    ["hook", t("Gancho"), t("Ex.: abrir com o número mais surpreendente")],
    ["ending", t("Final"), t("Ex.: terminar com uma pergunta para os comentários")],
    ["bumper", t("Vinheta"), t("Ex.: sem vinheta nos vídeos curtos")],
  ];
  const dirty = fields.some(([f]) => (d[f] ?? "") !== (saved[f] ?? ""));
  const id = `rules-${style}`;
  async function save() {
    setBusy(true);
    try {
      const r = await api<{ rules: Record<string, Record<Field, string>> }>("/settings/style-rules", { method: "PUT", body: JSON.stringify({ rules: { [style]: d } }) });
      setSaved(r.rules[style]);
      setD(r.rules[style]);
      setToast({ msg: t("Regras de “{type}” salvas", { type: t(STYLE_LABEL[style] ?? style) }) });
    } catch {
      setToast({ msg: t("Não consegui salvar. A API está ligada?") });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card flex flex-col gap-4 p-5" aria-labelledby={id}>
      <h2 id={id} className="h2 m-0">{t(STYLE_LABEL[style] ?? style)}</h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {fields.map(([f, label, hint]) => (
          <label key={f} className="flex flex-col gap-1.5 text-sm font-semibold">
            {label}
            <textarea rows={2} className="rounded-xl border border-field p-3 text-[14px] font-normal leading-normal" value={d[f] ?? ""}
              placeholder={hint} onChange={(e) => setD({ ...d, [f]: e.target.value })} />
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-go" disabled={!dirty || busy} onClick={save}>{busy ? t("Salvando…") : t("Salvar regras")}</button>
        {dirty && <button className="btn" onClick={() => setD(saved)}>{t("Desfazer")}</button>}
      </div>
    </section>
  );
}
