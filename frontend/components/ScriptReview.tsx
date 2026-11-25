"use client";

import { useEffect, useMemo, useState } from "react";
import { api, LANG_LABEL, type Script, type ScriptContent } from "@/lib/api";
import { useT } from "@/lib/i18n";

const TYPE_LABEL: Record<string, string> = {
  avatar_full: "Avatar falando", avatar_pip: "Avatar no canto", broll: "Vídeo de apoio", footage: "Vídeo real", headline: "Manchete",
  chart: "Gráfico", source_clip: "Trecho do vídeo original", ai_image: "Imagem IA",
};
const TAB_LABEL: Record<string, string> = { ...LANG_LABEL, "ar-AE": "العربية" };

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** Readable script per language, alternative hooks, JSON editor and the approve/rewrite actions. */
export default function ScriptReview({ projectId, languages, scripts, onSaved, onApprove, onRewrite, approveLabel }: {
  projectId: number;
  languages: string[];
  scripts: Script[];
  onSaved: () => void;
  onApprove: () => void;
  onRewrite: (note: string) => void;
  approveLabel: string;
}) {
  const [lang, setLang] = useState(languages[0]);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const { t } = useT();
  const latest = useMemo(() => scripts.find((s) => s.language === lang), [scripts, lang]);
  useEffect(() => {
    setDraft(latest ? JSON.stringify(latest.content, null, 2) : "");
  }, [latest?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(content: ScriptContent, ok: string) {
    try {
      await api(`/projects/${projectId}/scripts/${lang}`, { method: "PUT", body: JSON.stringify(content) });
      setMsg(ok);
      onSaved();
    } catch (e) {
      setMsg(t("Erro ao salvar: {error}", { error: String(e) }));
    }
  }

  function applyHook(h: string) {
    if (!latest) return;
    const c = latest.content;
    const scenes = c.scenes.map((s, i) => (i === 0 && s.narration.startsWith(c.hook) ? { ...s, narration: h + s.narration.slice(c.hook.length) } : s));
    save({ ...c, hook: h, scenes, hook_variants: [c.hook, ...(c.hook_variants ?? []).filter((x) => x !== h)] }, t("Gancho trocado (nova versão salva)."));
  }

  const c = latest?.content;
  let clock = 0;
  const rtl = lang === "ar-AE";

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-ink px-5 py-4 text-white">
        <span className="h-2.5 w-2.5 rounded-full bg-alert-dot" />
        <span className="flex-[1_1_280px] text-base"><strong>{t("Sua vez:")}</strong> {t("leia o roteiro e aprove. Depois disso ele volta para a fila sozinho.")}</span>
      </div>
      {languages.length > 1 && (
        <div role="tablist" aria-label={t("Idioma")} className="flex gap-2">
          {languages.map((l) => (
            <button key={l} role="tab" aria-selected={l === lang} onClick={() => setLang(l)} className={`btn ${l === lang ? "btn-ink" : ""}`}>{t(TAB_LABEL[l] ?? l)}</button>
          ))}
        </div>
      )}
      {!c ? (
        <p className="text-muted">{t("Sem roteiro em {lang} ainda.", { lang: t(LANG_LABEL[lang] ?? lang) })}</p>
      ) : (
        <>
          {c.writer === "codex" && (
            <p className="m-0 text-sm text-muted">{t("Escrito pelo Codex (o Claude estava no limite de uso). Confira com mais atenção o tom e as regras do canal.")}</p>
          )}
          <div dir={rtl ? "rtl" : "ltr"} className="card overflow-hidden">
            {c.scenes.map((s, i) => {
              const start = clock;
              clock += s.target_seconds ?? 0;
              const kind = i === 0 ? (rtl ? "الافتتاحية" : t("Gancho")) : i === c.scenes.length - 1 && c.call_to_action ? (rtl ? "الختام" : t("Fechamento")) : rtl ? `المشهد ${s.index}` : t("Cena {n}", { n: s.index });
              return (
                <div key={s.index} className="grid grid-cols-[56px_1fr] gap-4 border-b border-line-soft px-5 py-[18px] last:border-b-0">
                  <span className="font-display text-sm leading-[1.6] font-semibold text-muted">{mmss(start)}</span>
                  <div className="flex flex-col gap-1.5">
                    <span className="text-[13px] font-semibold tracking-[0.5px] text-go-text uppercase">{kind}</span>
                    <p className="m-0 text-[17px] leading-[1.55]">{s.narration}</p>
                    {s.on_screen_text && <span className="text-[13px] text-body">{t("Texto na tela:")} {s.on_screen_text}</span>}
                    <span dir="ltr" className="text-[13px] text-muted">
                      {t(TYPE_LABEL[s.visual.type] ?? s.visual.type)}: {s.visual.image_prompt ?? s.visual.search_terms?.join(", ") ?? s.visual.source_url ?? "—"}
                    </span>
                  </div>
                </div>
              );
            })}
            {c.call_to_action && (
              <div className="grid grid-cols-[56px_1fr] gap-4 px-5 py-[18px]">
                <span className="font-display text-sm font-semibold text-muted">CTA</span>
                <p className="m-0 text-[17px] leading-[1.55]">{c.call_to_action}</p>
              </div>
            )}
          </div>
          {!!c.sources.length && (
            <details className="card px-4 py-3.5">
              <summary className="cursor-pointer text-[15px] font-semibold">{t("Fontes ({n})", { n: c.sources.length })}</summary>
              <ul className="mt-2 mb-0 flex flex-col gap-1 pl-5 text-sm">
                {c.sources.map((u) => <li key={u} className="truncate"><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
              </ul>
            </details>
          )}
          {!!c.hook_variants?.length && (
            <div className="card flex flex-col gap-2 px-5 py-4">
              <span className="text-sm font-semibold">{t("Ganchos alternativos")}</span>
              {c.hook_variants.map((h) => (
                <div key={h} dir={rtl ? "rtl" : "ltr"} className="flex flex-wrap items-center gap-2.5 rounded-[10px] bg-bg px-3 py-2.5">
                  <span className="flex-[1_1_240px] text-[15px]">{h}</span>
                  <button className="btn btn-sm min-h-9 text-[13px]" onClick={() => applyHook(h)}>{t("Usar este")}</button>
                </div>
              ))}
            </div>
          )}
          <details className="rounded-xl border border-line bg-surface px-4 py-3.5">
            <summary className="cursor-pointer text-[15px] font-semibold">{t("Avançado: editar JSON do roteiro")}</summary>
            <textarea
              aria-label={t("JSON do roteiro")}
              rows={14}
              className="mt-3 box-border w-full rounded-[10px] border border-field bg-ink p-3 font-mono text-[13px] leading-normal text-line"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <button
              className="btn mt-2.5"
              onClick={() => {
                try {
                  save(JSON.parse(draft) as ScriptContent, t("Nova versão salva (v{n}).", { n: (latest?.version ?? 0) + 1 }));
                } catch (e) {
                  setMsg(t("JSON inválido: {error}", { error: String(e) }));
                }
              }}
            >
              {t("Salvar nova versão")}
            </button>
          </details>
        </>
      )}
      {msg && <p role="status" className="m-0 text-sm text-body">{msg}</p>}
      <label className="flex flex-col gap-2 text-sm font-semibold">
        {t("Quer mudar algo? (opcional)")}
        <textarea rows={2} className="rounded-xl border border-field px-4 py-3 text-[15px] leading-normal font-normal" placeholder={t("Ex.: deixe o gancho mais curto")} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="flex flex-wrap justify-end gap-3">
        <button className="btn" onClick={() => onRewrite(note)}>{t("Pedir nova versão")}</button>
        <button className="btn btn-go min-h-[52px] px-6 text-base" onClick={onApprove} disabled={!c}>{approveLabel}</button>
      </div>
    </section>
  );
}
