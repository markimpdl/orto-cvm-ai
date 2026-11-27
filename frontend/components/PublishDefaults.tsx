"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { getPublishDefaults, setPublishDefaults, type PublishDefaults } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** The channel's YouTube upload defaults (backend cvm/api/channels.py publish-defaults → Channel.prefs.publish_defaults,
 *  made_for_kids, youtube_category): what "Preparar no navegador" ticks in YouTube Studio and the kit shows. */
export default function PublishDefaultsCard({ channelId }: { channelId: number }) {
  const { t } = useT();
  const { setToast } = useAppData();
  const [d, setD] = useState<PublishDefaults | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getPublishDefaults(channelId).then(setD).catch(() => setD(null));
  }, [channelId]);

  async function save(body: Partial<PublishDefaults>) {
    setBusy(true);
    setErr(null);
    try {
      setD(await setPublishDefaults(channelId, body));
      setToast({ msg: t("Padrões salvos") });
    } catch (e) {
      setErr(String(e).replace(/^Error: \d+ /, "").replace(/^\{"detail":"(.*)"\}$/, "$1"));
    } finally {
      setBusy(false);
    }
  }

  if (!d) return null;
  const field = "flex flex-col gap-1.5 text-sm font-semibold";
  const boxes: [keyof PublishDefaults, string][] = [
    ["auto_chapters", t("Capítulos automáticos")],
    ["auto_places", t("Permitir a localização automática")],
    ["notify_subscribers", t("Publicar no feed de inscrições e enviar notificações aos inscritos")],
  ];
  return (
    <section className="card flex flex-col gap-4 p-5" aria-labelledby="h-publish-defaults">
      <div className="flex flex-col gap-1">
        <h2 id="h-publish-defaults" className="m-0 text-base font-semibold">{t("Padrões de publicação no YouTube")}</h2>
        <p className="m-0 text-sm text-body">{t("O que o CVM marca no YouTube Studio ao preparar um vídeo deste canal no navegador, e o que o kit mostra.")}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className={field}>
          {t("Conteúdo alterado ou sintético")}
          <select className="input font-normal" disabled={busy} value={d.synthetic}
            onChange={(e) => save({ synthetic: e.target.value as PublishDefaults["synthetic"] })}>
            <option value="auto">{t("Sim só com o avatar na tela")}</option>
            <option value="yes">{t("Sempre sim")}</option>
            <option value="no">{t("Sempre não")}</option>
          </select>
        </label>
        <label className={field}>
          {t("Promoção paga")}
          <select className="input font-normal" disabled={busy} value={d.paid_promotion ? "yes" : "no"}
            onChange={(e) => save({ paid_promotion: e.target.value === "yes" })}>
            <option value="no">{t("Não, meu vídeo não inclui promoção paga")}</option>
            <option value="yes">{t("Sim, inclui promoção paga")}</option>
          </select>
        </label>
        <label className={field}>
          {t("Público")}
          <select className="input font-normal" disabled={busy} value={d.made_for_kids ? "kids" : "no"}
            onChange={(e) => save({ made_for_kids: e.target.value === "kids" })}>
            <option value="no">{t("Não é conteúdo para crianças")}</option>
            <option value="kids">{t("É conteúdo para crianças")}</option>
          </select>
        </label>
        <label className={field}>
          {t("Categoria")}
          <select className="input font-normal" disabled={busy} value={d.category} onChange={(e) => save({ category: e.target.value })}>
            <option value="auto">{t("Automática (pelo tema do vídeo)")}</option>
            {d.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
      </div>
      <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
        {boxes.map(([k, label]) => (
          <label key={k} className="flex min-h-11 items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-go" checked={Boolean(d[k])} disabled={busy}
              onChange={(e) => save({ [k]: e.target.checked })} />
            {label}
          </label>
        ))}
        <span className="text-[13px] text-muted">{t("Caixas marcadas ficam ligadas no Studio; desmarcadas, o CVM desliga.")}</span>
      </fieldset>
      {err && <p className="m-0 text-sm text-alert-text">{err}</p>}
    </section>
  );
}
