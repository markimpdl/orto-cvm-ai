"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { connectTiktok, disconnectTiktok, getTiktok, setTiktokDefault, type TiktokInfo } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** The TikTok account of a YouTube channel (backend cvm/publish/tiktok.py): connect with the @handle, choose whether
 *  approved Shorts go to TikTok too by default, disconnect. Posting stays manual on /publicar. */
export default function TiktokConnect({ channelId }: { channelId: number }) {
  const { t } = useT();
  const { reloadSetup, setToast } = useAppData();
  const [info, setInfo] = useState<TiktokInfo | null>(null);
  const [handle, setHandle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getTiktok(channelId).then(setInfo).catch(() => setInfo(null));
  }, [channelId]);

  async function run(fn: () => Promise<TiktokInfo>, msg?: string) {
    setBusy(true);
    setErr(null);
    try {
      setInfo(await fn());
      reloadSetup();
      if (msg) setToast({ msg });
    } catch (e) {
      setErr(String(e).replace(/^Error: \d+ /, "").replace(/^\{"detail":"(.*)"\}$/, "$1"));
    } finally {
      setBusy(false);
    }
  }

  if (!info) return null;
  return (
    <section className="card flex flex-col gap-3 p-5" aria-labelledby="h-tiktok">
      <h2 id="h-tiktok" className="m-0 text-base font-semibold">{t("TikTok")}</h2>
      {info.connected ? (
        <>
          <p className="m-0 text-sm text-body">
            {t("Conta ligada: @{handle}. Os Shorts aprovados deste canal também aparecem em Publicar, com o kit do TikTok, e você posta por lá.", { handle: info.handle ?? "" })}
          </p>
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-go" checked={info.also} disabled={busy}
              onChange={(e) => run(() => setTiktokDefault(channelId, e.target.checked))} />
            {t("Marcar “Publicar também no TikTok” ao aprovar um Short")}
          </label>
          {info.kids && <p className="m-0 text-[13px] text-warn-text">{t("Canal infantil: o TikTok nunca entra sozinho. Marque na aprovação quando quiser.")}</p>}
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-sm" disabled={busy} onClick={() => {
              if (confirm(t("Desligar a conta do TikTok? O que já está agendado continua na página Publicar."))) run(() => disconnectTiktok(channelId), t("TikTok desligado"));
            }}>{t("Desligar TikTok")}</button>
          </div>
        </>
      ) : (
        <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => {
          e.preventDefault();
          run(() => connectTiktok(channelId, handle), t("TikTok ligado"));
        }}>
          <label className="flex flex-[1_1_220px] flex-col gap-1.5 text-sm font-semibold">
            {t("@ da conta do TikTok")}
            <input className="input font-normal" value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@meucanal" autoComplete="off" spellCheck={false} />
          </label>
          <button type="submit" className="btn btn-go" disabled={busy || !handle.trim()}>{t("Conectar TikTok")}</button>
          <span className="basis-full text-[13px] text-muted">{t("Os Shorts deste canal também poderão ir para o TikTok com o mesmo arquivo. Você posta pelo kit em Publicar.")}</span>
        </form>
      )}
      {err && <p className="m-0 text-sm text-alert-text">{err}</p>}
    </section>
  );
}
