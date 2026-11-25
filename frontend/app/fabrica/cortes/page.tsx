"use client";

import Link from "next/link";
import { useState } from "react";
import { useAppData } from "@/components/AppData";
import { PageHeader, Tabs } from "@/components/Bits";
import { ChannelSelect, CutsTab, LivesTab, PendingCutReviews, useGrowChannel } from "@/components/Grow";
import { useT } from "@/lib/i18n";

type Tab = "lives" | "cortes";

/** Fábrica > Clips e cortes: a live or video link turned into best moments + Shorts, and the cuts that are working. */
export default function CutsPage() {
  const { t } = useT();
  const { channelsLoaded } = useAppData();
  const [channelId, pick] = useGrowChannel();
  const [tab, setTab] = useState<Tab>("lives");
  return (
    <>
      <PageHeader title={t("Clips e cortes")} subtitle={t("Cole o link de uma live ou vídeo, revise os cortes que a IA marcou e produza os Shorts.")}>
        {channelId != null && <ChannelSelect value={channelId} onChange={pick} />}
      </PageHeader>
      <PendingCutReviews />
      {channelId == null ? (
        <div className="card p-5 text-muted">
          {channelsLoaded ? (
            <>{t("Nenhum canal ativo ainda.")} <Link href="/canais" className="underline">{t("Criar um canal")}</Link></>
          ) : t("Carregando…")}
        </div>
      ) : (
        <>
          <Tabs label={t("Seções")} value={tab} onChange={setTab} tabs={[["lives", t("Lives")], ["cortes", t("Cortes")]]} />
          {tab === "lives" && <LivesTab channelId={channelId} />}
          {tab === "cortes" && <CutsTab channelId={channelId} onCut={() => setTab("lives")} />}
        </>
      )}
    </>
  );
}
