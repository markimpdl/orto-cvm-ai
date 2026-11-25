"use client";

import Link from "next/link";
import { useState } from "react";
import { useAppData } from "@/components/AppData";
import { Initial, PageHeader } from "@/components/Bits";
import QueueItem from "@/components/QueueItem";
import SeriesVoices from "@/components/SeriesVoices";
import { api, isProducing, LANG_FLAG, LANG_LABEL, PLATFORM_LABEL, type Channel, type Project } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Kids series = channels with the kids style (or marked made for kids). The brief is the series bible. */
const isKids = (c: Channel) => c.default_style === "kids" || !!c.made_for_kids;
/** A series holds the bible, cast, voices and image seed. Publishing-only channels (kids channels in a group, one per
 *  language, not flagged as a series) are where series episodes go, not series themselves. */
const isSeries = (c: Channel) => !!c.profile?.series || (isKids(c) && !c.group && c.status !== "archived");
const seriesName = (c: Channel, lang: string) => c.profile?.series_names?.[lang] ?? c.name.trim();

function line(c: Channel, key: string): string | null {
  const l = (c.brief ?? "").split("\n").find((x) => x.toLowerCase().startsWith(key.toLowerCase() + ":"));
  return l ? l.split(":").slice(1).join(":").trim() : null;
}

const NEW = { name: "", characters: "", world: "", themes: "amizade, coragem, dividir, ajudar, curiosidade sobre a natureza", opening: "", goodbye: "", avatar_id: "", platform: "youtube", where: "group" };

function NewSeries({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { avatars, channels } = useAppData();
  const [f, setF] = useState(NEW);
  // the publishing group the existing series go out through (kids channels sharing a group, one per language)
  const group = channels.map((c) => c.profile?.publish_group).find(Boolean)
    ?? channels.find((c) => isKids(c) && c.group && !c.profile?.series)?.group ?? null;
  const where = group ? f.where : "own";
  const [err, setErr] = useState<string | null>(null);
  const { t } = useT();
  const set = (k: keyof typeof NEW) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  async function save() {
    const brief = [
      `Série: ${f.name}`,
      "Público: crianças de 3 a 8 anos e os pais que assistem junto.",
      "Tom: carinhoso, divertido, calmo; a narração conta a história e dá vozes aos personagens.",
      `Personagens: ${f.characters}`,
      f.world && `Mundo: ${f.world}`,
      `Temas: ${f.themes}`,
      "Evitar: violência, sustos, vilões malvados, comida ou remédio de forma perigosa, marcas, pedidos para comentar ou se inscrever.",
      f.opening && `Abertura fixa: "${f.opening}"`,
      f.goodbye && `Despedida fixa: "${f.goodbye}"`,
    ].filter(Boolean).join("\n");
    try {
      await api("/channels", {
        method: "POST",
        body: JSON.stringify({ name: f.name, platform: f.platform, language: "pt-BR", default_style: "kids", made_for_kids: true, presenter: "scenes",
          publish_mode: "manual", avatar_id: f.avatar_id ? Number(f.avatar_id) : null, brief, schedule: { slots: [] },
          // a new series draws from its characters' reference sheets (FLUX.2 Klein); older series keep FLUX.1
          prefs: { image_engine: "klein" },
          // a group series has no YouTube channel of its own: its episodes go to the group's channels (one per language)
          ...(where === "group" ? { status: "series", profile: { series: true, publish_group: group, series_names: { "pt-BR": f.name } } } : {}) }),
      });
      onDone();
    } catch (e) {
      setErr(String(e));
    }
  }

  const label = "flex flex-col gap-1.5 text-sm font-semibold";
  return (
    <div className="card flex flex-col gap-3 p-5">
      <h2 className="h2 m-0">{t("Nova série")}</h2>
      <label className={label}>
        {t("Onde publica")}
        <select className="input font-normal" value={where} onChange={set("where")}>
          {group && <option value="group">{t("Nos canais do grupo “{group}” (um por idioma)", { group })}</option>}
          <option value="own">{t("Em um canal próprio do YouTube")}</option>
        </select>
      </label>
      <label className={label}>{t("Nome da série")}<input className="input font-normal" value={f.name} onChange={set("name")} placeholder={t("Amigos do Cacau")} /></label>
      <label className={label}>
        {t("Personagens (aparência fixa e jeito de cada um)")}
        <textarea rows={4} className="rounded-xl border border-field p-3 text-sm font-normal" value={f.characters} onChange={set("characters")}
          placeholder={t("Cacau, uma tartaruguinha amarela de boné vermelho — curiosa, bordão “E se a gente tentar?”. Gil, um ouriço cinza de óculos redondos — calmo e comilão.")} />
      </label>
      <label className={label}>{t("Mundo (onde vivem)")}<input className="input font-normal" value={f.world} onChange={set("world")} placeholder={t("a Vila dos Ventos, com um moinho colorido e uma ponte de cordas")} /></label>
      <label className={label}>{t("Temas e lições")}<input className="input font-normal" value={f.themes} onChange={set("themes")} /></label>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className={label}>{t("Abertura fixa")}<input className="input font-normal" value={f.opening} onChange={set("opening")} placeholder={t("Olá, exploradores!…")} /></label>
        <label className={label}>{t("Despedida fixa")}<input className="input font-normal" value={f.goodbye} onChange={set("goodbye")} placeholder={t("Tchau, exploradores! Até o próximo episódio!")} /></label>
        <label className={label}>
          {t("Voz da narração")}
          <select className="input font-normal" value={f.avatar_id} onChange={set("avatar_id")}>
            <option value="">{t("Voz padrão do sistema")}</option>
            {avatars.filter((a) => a.status === "ready").map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label className={label}>
          {t("Onde publicar")}
          <select className="input font-normal" value={f.platform} onChange={set("platform")}>
            <option value="youtube">{t("YouTube (episódios horizontais)")}</option>
            <option value="youtube_shorts">YouTube Shorts</option>
          </select>
        </label>
      </div>
      {err && <p className="m-0 text-alert-text">{err}</p>}
      <div className="flex gap-2">
        <button className="btn btn-go" disabled={!f.name || !f.characters} onClick={save}>{t("Criar série")}</button>
        <button className="btn" onClick={onCancel}>{t("Cancelar")}</button>
      </div>
    </div>
  );
}

/** One card per channel: a novelinha channel picks which series the next episode belongs to
 *  (the series keeps its cast, voices and sequence); a standalone series is its own channel. */
function Series({ channel, options, projects }: { channel: Channel; options: Channel[]; projects: Project[] }) {
  const { avatarOf, channelOf, reload, setToast } = useAppData();
  const { t } = useT();
  const [premise, setPremise] = useState("");
  const [busy, setBusy] = useState(false);
  const [seriesId, setSeriesId] = useState<number>((options.find((x) => x.id === channel.id) ?? options[0]).id);
  const c = options.find((x) => x.id === seriesId) ?? options[0]; // the series: bible, cast, voices
  const target = channel;
  const lang = target.language;
  const episodes = projects.filter((p) => p.channel_id === channel.id);
  // the sequence goes on per series and language, whatever channel the earlier episodes went to
  const ofSeries = projects.filter((p) => p.series_id === c.id || (!p.series_id && p.channel_id === c.id));
  const inLang = ofSeries.filter((e) => (e.languages?.[0] ?? channelOf(e.channel_id ?? undefined)?.language) === lang);
  const numbered = inLang.map((e) => Number(/Ep\.? ?(\d+)/i.exec(e.title)?.[1] || /الحلقة (\d+)/.exec(e.title)?.[1] || 0));
  // episodes made on another machine count through the series' prefs: episode_max[lang] (highest number on any
  // machine, set by the central sync) and last_episode (set by hand, the series' own language)
  // (numbers only, not the episode count: an unnumbered episode would make the sequence skip numbers; same rule as
  // the backend's channels.next_episode_number)
  const suggested = Math.max(0, c.prefs?.episode_max?.[lang] || 0, lang === c.language ? c.prefs?.last_episode || 0 : 0, ...numbered) + 1;
  // the user can set the number by hand (episodes may also be made on another machine)
  const [num, setNum] = useState("");
  const next = Number(num) > 0 ? Math.floor(Number(num)) : suggested;
  const taken = numbered.includes(next);

  async function newEpisode() {
    setBusy(true);
    const p = premise.trim();
    try {
      await api("/projects", {
        method: "POST",
        body: JSON.stringify({
          title: `${seriesName(c, lang)} – Ep. ${next}${p ? `: ${p}` : ""}`,
          // duration and audience come from the series bible (brief); kids series default to ~5 min
          topic: p
            ? `Episódio ${next} da série animada ${seriesName(c, lang)}. Premissa: ${p}`
            : `Episódio ${next} da série animada ${seriesName(c, lang)}. Crie uma aventura nova, seguindo a bíblia da série, continuando o gancho do episódio anterior e sem repetir os episódios anteriores.`,
          style: "kids", languages: [lang], platforms: [target.platform], channel_id: target.id,
          series_id: target.id === c.id ? null : c.id, avatar_id: c.avatar_id,
        }),
      });
      setPremise("");
      setNum("");
      await reload();
      setToast({ msg: t("Ep. {n} de “{name}” entrou na fila", { n: next, name: seriesName(c, lang) }) });
    } finally {
      setBusy(false);
    }
  }

  const sorted = [...episodes].sort((a, b) => b.id - a.id);
  return (
    <section className="card flex flex-col gap-4 p-5">
      <div className="flex flex-wrap items-center gap-3">
        <Initial name={channel.name} size={48} />
        <span className="flex flex-[1_1_240px] flex-col gap-0.5">
          <span className="text-[17px] font-semibold" dir="auto">{options.length > 1 ? `${LANG_FLAG[lang] ?? ""} ` : ""}{channel.name}</span>
          <span className="text-[13px] text-muted">
            {PLATFORM_LABEL[channel.platform] ?? channel.platform} · {t(LANG_LABEL[lang] ?? lang)} · {t("narração")} {avatarOf(c.avatar_id)?.name ?? t("voz padrão")} · {episodes.length === 1 ? t("{n} episódio", { n: 1 }) : t("{n} episódios", { n: episodes.length })}
            {channel.made_for_kids ? ` · ${t("feito para crianças")}` : ""}
          </span>
        </span>
        <Link href={`/canais/${channel.id}`} className="btn btn-sm">{t("Sugestões do Claude")}</Link>
      </div>
      {options.length > 1 && (
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("Série")}>
          {options.map((o) => (
            <button key={o.id} type="button" role="radio" aria-checked={o.id === c.id} onClick={() => { setSeriesId(o.id); setNum(""); }}
              className={`pick min-h-10 rounded-full px-4 text-sm font-semibold text-ink ${o.id === c.id ? "border-2 border-go bg-go-tint" : "border border-[#d9dbdf] bg-surface"}`}>
              <span dir="auto">{seriesName(o, lang)}</span>
            </button>
          ))}
        </div>
      )}
      {line(c, "Personagens") && <p className="m-0 text-sm leading-normal text-body"><strong>{t("Personagens:")}</strong> {line(c, "Personagens")}</p>}
      {line(c, "Mundo") && <p className="m-0 text-sm text-muted"><strong>{t("Mundo:")}</strong> {line(c, "Mundo")}</p>}
      <SeriesVoices c={c} />
      <div className="flex flex-wrap items-end gap-2 rounded-xl bg-bg p-3">
        <label className="flex w-[110px] flex-col gap-1.5 text-sm font-semibold">
          {t("Nº do episódio")}
          <input className="input font-normal" type="number" min={1} value={num} placeholder={String(suggested)}
                 onChange={(e) => setNum(e.target.value)} />
        </label>
        <label className="flex flex-[1_1_320px] flex-col gap-1.5 text-sm font-semibold">
          {t("Ep. {n}: premissa (opcional)", { n: next })}
          <input className="input font-normal" value={premise} onChange={(e) => setPremise(e.target.value)} placeholder={t("Deixe vazio e o Claude cria a próxima aventura")} />
        </label>
        <button className="btn btn-go" disabled={busy || taken} onClick={newEpisode}>{t("Produzir Ep. {n}", { n: next })}</button>
      </div>
      {taken && <p className="m-0 text-sm text-alert-text">{t("O Ep. {n} já existe nesta série neste idioma. Escolha outro número.", { n: next })}</p>}
      <p className="m-0 text-[13px] text-muted">{t("Episódio 100% animado (LTX-2.3), com personagens falando com voz própria, na duração do canal. Leva ~2–3 h de GPU a cada 5 min de episódio depois que o roteiro for aprovado.")}</p>
      <div className="overflow-hidden rounded-xl border border-line">
        {sorted.map((p) => <QueueItem key={p.id} p={p} />)}
        {!sorted.length && <p className="m-0 px-5 py-4 text-muted">{t("Nenhum episódio ainda.")}</p>}
      </div>
    </section>
  );
}

export default function KidsPage() {
  const { channels, projects, reloadSetup } = useAppData();
  const [creating, setCreating] = useState(false);
  const { t } = useT();
  const series = channels.filter(isSeries);
  // the channels of a publishing group get a card each, offering every series published there;
  // a series outside any group is its own channel
  const groupOf = (c: Channel) => c.profile?.publish_group ?? c.group;
  const groups = [...new Set(series.map(groupOf).filter(Boolean))] as string[];
  const cards = [
    ...groups.flatMap((g) => channels.filter((c) => c.group === g && isProducing(c) && isKids(c)).map((channel) => ({
      channel,
      options: [...series.filter((x) => x.id === channel.id), ...series.filter((x) => x.id !== channel.id && groupOf(x) === g)],
    }))).filter((x) => x.options.length),
    ...series.filter((x) => !groupOf(x)).map((channel) => ({ channel, options: [channel] })),
  ];
  return (
    <>
      <PageHeader title={t("Séries")} subtitle={t("Séries de animação com personagens fixos. Cada episódio segue o mesmo pipeline: roteiro, aprovação, voz, imagens animadas e publicação.")}>
        {!creating && <button className="btn btn-go" onClick={() => setCreating(true)}>+ {t("Nova série")}</button>}
      </PageHeader>
      {creating && <NewSeries onDone={() => { setCreating(false); reloadSetup(); }} onCancel={() => setCreating(false)} />}
      {cards.map(({ channel, options }) => <Series key={channel.id} channel={channel} options={options} projects={projects} />)}
      {!cards.length && !creating && <div className="card p-6 text-body">{t("Nenhuma série ainda. Clique em “Nova série”.")}</div>}
    </>
  );
}
