"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { API, api, type Channel } from "@/lib/api";
import { useT } from "@/lib/i18n";

type LibVoice = { channel_id: number; channel: string; name: string; description: string | null };

/** A kids series' cast voices: listen, redo from a description, create new ones, or reuse one from another series. */
export default function SeriesVoices({ c }: { c: Channel }) {
  const { t } = useT();
  const { reloadSetup } = useAppData();
  const [lib, setLib] = useState<LibVoice[]>([]);
  const [edit, setEdit] = useState<{ name: string; description: string } | null>(null);
  const [reuse, setReuse] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const cast = Object.entries(c.characters ?? {});
  const pending = cast.some(([, v]) => v.pending || v.sheet_pending);
  const sheets = c.prefs?.image_engine === "klein";

  useEffect(() => {
    api<LibVoice[]>("/channels/meta/voices").then(setLib).catch(() => {});
  }, [c.characters]);
  useEffect(() => {  // a voice is being designed in the worker: refresh until it's ready
    if (!pending) return;
    const id = setInterval(reloadSetup, 8000);
    return () => clearInterval(id);
  }, [pending, reloadSetup]);

  async function design() {
    if (!edit) return;
    setErr(null);
    try {
      await api(`/channels/${c.id}/characters`, { method: "POST", body: JSON.stringify(edit) });
      setEdit(null);
      reloadSetup();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function reuseVoice() {
    const v = lib.find((x) => `${x.channel_id}|${x.name}` === reuse);
    if (!v) return;
    await api(`/channels/${c.id}/characters/reuse`, { method: "POST", body: JSON.stringify({ from_channel_id: v.channel_id, name: v.name }) });
    setReuse("");
    reloadSetup();
  }

  async function redraw(name: string) {
    setErr(null);
    try {
      await api(`/channels/${c.id}/characters/${encodeURIComponent(name)}/sheet`, { method: "POST", body: "{}" });
      reloadSetup();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function remove(name: string) {
    if (!confirm(t("Remover a voz de {name} desta série?", { name }))) return;
    await api(`/channels/${c.id}/characters/${encodeURIComponent(name)}`, { method: "DELETE" });
    reloadSetup();
  }

  const others = lib.filter((v) => v.channel_id !== c.id);
  const hasNarrator = cast.some(([n]) => /^narrador|^narradora|^narrator/i.test(n.trim()));

  return (
    <div className="flex flex-col gap-3">
      <span className="text-sm font-semibold">{sheets ? t("Personagens da série") : t("Vozes da série")}</span>
      {!hasNarrator && (
        <p className="m-0 text-[13px] text-warn-text">{t("Sem voz de narrador: a narração usa a voz do avatar da série. Crie uma “Narradora” para a série ter narração própria.")}</p>
      )}
      <div className="flex flex-wrap gap-3">
        {cast.map(([name, v]) => (
          <div key={name} className="flex flex-[1_1_220px] flex-col gap-1 rounded-xl bg-bg p-3">
            <span className="flex items-center gap-2 text-sm font-semibold">
              <span className="flex-1">{t("Voz de {name}", { name })}</span>
              <button className="btn btn-sm" onClick={() => setEdit({ name, description: v.description ?? "" })}>{t("Refazer")}</button>
              <button className="btn btn-sm" onClick={() => remove(name)} aria-label={t("Remover a voz de {name} desta série?", { name })}>✕</button>
            </span>
            <span className="text-xs text-muted">{v.description}</span>
            {v.pending ? (
              <span className="text-[13px] text-muted">{t("Criando a voz…")}</span>
            ) : v.error ? (
              <span className="text-[13px] text-alert-text">{v.error}</span>
            ) : v.voice ? (
              <audio controls preload="none" src={`${API}/channels/${c.id}/characters/${encodeURIComponent(name)}/voice?v=${encodeURIComponent(v.voice ?? "")}`} className="w-full" />
            ) : (
              <span className="text-[13px] text-muted">{t("Ainda sem voz: ela é criada na primeira fala.")}</span>
            )}
            {sheets && (v.sheet || v.sheet_pending || v.look) && (
              <div className="mt-1 flex items-start gap-3">
                {v.sheet && !v.sheet_pending && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={`${API}/channels/${c.id}/characters/${encodeURIComponent(name)}/sheet?v=${v.sheet_seed ?? 0}`}
                    alt={t("Desenho de referência de {name}", { name })} className="h-28 w-28 rounded-lg object-cover" />
                )}
                <div className="flex flex-1 flex-col items-start gap-1">
                  {v.sheet_pending ? (
                    <span className="text-[13px] text-muted">{t("Desenhando a ficha…")}</span>
                  ) : (
                    <button className="btn btn-sm" onClick={() => redraw(name)}>{t("Redesenhar ficha")}</button>
                  )}
                  {v.sheet_error && <span className="text-[13px] text-alert-text">{v.sheet_error}</span>}
                  {v.sheet_note && !v.sheet_pending && <span className="text-xs text-warn-text">{t("O conferidor apontou: {note}", { note: v.sheet_note })}</span>}
                  <span className="text-xs text-muted">{t("Toda cena nova segue este desenho.")}</span>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
      {edit ? (
        <div className="flex flex-wrap items-end gap-2 rounded-xl bg-bg p-3">
          <label className="flex flex-[1_1_160px] flex-col gap-1.5 text-sm font-semibold">
            {t("Personagem")}
            <input className="input font-normal" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} placeholder="Narradora" />
          </label>
          <label className="flex flex-[3_1_320px] flex-col gap-1.5 text-sm font-semibold">
            {t("Como é a voz (em inglês funciona melhor)")}
            <input className="input font-normal" value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })}
              placeholder="little elf girl, high-pitched, sweet and bright, curious" />
          </label>
          <button className="btn btn-go" disabled={!edit.name.trim() || !edit.description.trim()} onClick={design}>{t("Criar voz")}</button>
          <button className="btn" onClick={() => setEdit(null)}>{t("Cancelar")}</button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-sm" onClick={() => setEdit({ name: hasNarrator ? "" : "Narradora", description: "" })}>{t("+ Nova voz")}</button>
          {!!others.length && (
            <>
              <select className="input min-h-9 text-sm" value={reuse} onChange={(e) => setReuse(e.target.value)} aria-label={t("Reaproveitar voz de outra série")}>
                <option value="">{t("Reaproveitar voz de outra série…")}</option>
                {others.map((v) => <option key={`${v.channel_id}|${v.name}`} value={`${v.channel_id}|${v.name}`}>{v.name} — {v.channel}</option>)}
              </select>
              <button className="btn btn-sm" disabled={!reuse} onClick={reuseVoice}>{t("Usar nesta série")}</button>
            </>
          )}
        </div>
      )}
      {err && <p className="m-0 text-sm text-alert-text">{err}</p>}
    </div>
  );
}
