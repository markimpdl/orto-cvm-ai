"use client";

import Link from "next/link";
import { useState } from "react";
import { useAppData } from "@/components/AppData";
import { Initial, PageHeader } from "@/components/Bits";
import { useT, type T } from "@/lib/i18n";
import { useLanguages } from "@/lib/useLanguages";
import { api, LANG_LABEL, PLATFORM_LABEL, PLATFORMS, STYLE_LABEL, STYLES, type Avatar, type Channel, LANG_FLAG } from "@/lib/api";

const EMPTY = {
  name: "", platform: "youtube_shorts", language: "pt-BR", default_style: "explainer", avatar_id: "", publish_mode: "manual", brief: "", slots: "", presenter: "scenes",
  allow_official_clips: false, allow_article_images: false, made_for_kids: false,
};
type Form = typeof EMPTY;

const BRIEF_HINT = `Público: jovens de 18–30 anos, curiosos por tecnologia e games.
Tom: direto, bem-humorado, sem gírias pesadas; trata o espectador por "você".
Temas: lançamentos, vazamentos, bastidores da indústria.
Evitar: política partidária, palavrões, spoilers sem aviso.
CTA fixo: "Se inscreve pra não perder o próximo."
Hashtags fixas: #games #tecnologia`;

const toForm = (c: Channel): Form => ({
  name: c.name, platform: c.platform, language: c.language, default_style: c.default_style,
  avatar_id: c.avatar_id ? String(c.avatar_id) : "", publish_mode: c.publish_mode, brief: c.brief ?? "", slots: (c.schedule?.slots ?? []).join(", "),
  presenter: c.presenter ?? "scenes",
  allow_official_clips: !!c.allow_official_clips, allow_article_images: !!c.allow_article_images, made_for_kids: !!c.made_for_kids,
});
const toBody = ({ slots, ...f }: Form) => JSON.stringify({
  ...f, avatar_id: f.avatar_id ? Number(f.avatar_id) : null, brief: f.brief.trim() || null,
  schedule: { slots: slots.split(",").map((s) => s.trim()).filter(Boolean) },
});

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1.5 text-sm font-semibold">{label}{children}</label>;
}

function ChannelForm({ initial, avatars, onSave, onCancel }: { initial: Form; avatars: Avatar[]; onSave: (f: Form) => Promise<void>; onCancel: () => void }) {
  const [form, setForm] = useState(initial);
  const { t } = useT();
  const videoLangs = useLanguages();
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });
  const selectedAvatar = avatars.find((a) => String(a.id) === form.avatar_id);
  return (
    <div className="card flex flex-col gap-4 p-5 md:col-span-2">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3">
        <Field label={t("Nome do canal")}><input className="input font-normal" value={form.name} onChange={set("name")} /></Field>
        <Field label={t("Plataforma")}><select className="input font-normal" value={form.platform} onChange={set("platform")}>{PLATFORMS.map((p) => <option key={p} value={p}>{PLATFORM_LABEL[p]}</option>)}</select></Field>
        <Field label={t("Idioma padrão")}><select className="input font-normal" value={form.language} onChange={set("language")}>{(videoLangs.includes(form.language) ? videoLangs : [...videoLangs, form.language]).map((l) => <option key={l} value={l}>{t(LANG_LABEL[l])}</option>)}</select></Field>
        <Field label={t("Estilo padrão")}><select className="input font-normal" value={form.default_style} onChange={set("default_style")}>{STYLES.map((s) => <option key={s} value={s}>{t(STYLE_LABEL[s])}</option>)}</select></Field>
        <Field label={t("Avatar e voz padrão")}>
          <select className="input font-normal" value={form.avatar_id} onChange={set("avatar_id")}>
            <option value="">{t("Sem avatar (só narração)")}</option>
            {avatars.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
        <Field label={t("Como o avatar aparece")}>
          <select className="input font-normal" value={form.presenter} onChange={set("presenter")}>
            <option value="scenes">{t("Só nas cenas de avatar (abertura e fechamento)")}</option>
            <option value="overlay">{t("O vídeo inteiro, recortado embaixo da tela")}</option>
            <option value="editorial">{t("Editorial (sem avatar na tela, com a voz do canal)")}</option>
            {selectedAvatar?.podcast_video_dir && <option value="podcast">{t("Podcast (janelinha + tela cheia no estúdio)")}</option>}
          </select>
        </Field>
        <Field label={t("Publicação")}>
          <select className="input font-normal" value={form.publish_mode} onChange={set("publish_mode")}>
            <option value="manual">{t("Manual (kit)")}</option>
            <option value="api">{t("API oficial")}</option>
            <option value="browser">{t("Automação de navegador")}</option>
          </select>
        </Field>
      </div>
      <Field label={t("Horários de publicação (ex.: Mon 18:00, Thu 12:00)")}>
        <input className="input font-normal" value={form.slots} onChange={set("slots")} placeholder="Mon 18:00, Wed 18:00, Fri 12:00" />
      </Field>
      <div className="flex flex-col gap-2 text-sm">
        <label className="flex items-start gap-2.5">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-go" checked={form.made_for_kids} onChange={(e) => setForm({ ...form, made_for_kids: e.target.checked })} />
          <span><strong>{t("Conteúdo para crianças")}</strong> <span className="text-muted">{t("(marca “feito para crianças” no YouTube: comentários desligados e sem anúncio personalizado; obrigatório para vídeos infantis).")}</span></span>
        </label>
        <label className="flex items-start gap-2.5">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-go" checked={form.allow_official_clips} onChange={(e) => setForm({ ...form, allow_official_clips: e.target.checked })} />
          <span><strong>{t("Trechos de vídeos oficiais")}</strong> <span className="text-muted">{t("(gameplay/trailer do canal dono do assunto, até 8 s com crédito). Pode gerar reivindicação de Content ID (perda de monetização daquele vídeo, não strike). Vídeos Creative Commons são usados sempre.")}</span></span>
        </label>
        <label className="flex items-start gap-2.5">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-go" checked={form.allow_article_images} onChange={(e) => setForm({ ...form, allow_article_images: e.target.checked })} />
          <span><strong>{t("Imagem principal das matérias")}</strong> <span className="text-muted">{t("(foto de destaque das notícias, com crédito do veículo). Risco médio de direitos autorais.")}</span></span>
        </label>
      </div>
      <Field label={t("Briefing do canal: público, tom, temas, o que evitar, CTA e hashtags fixas. O roteirista e o SEO seguem este texto.")}>
        <textarea rows={7} className="rounded-xl border border-field p-3 font-mono text-[13px] font-normal" placeholder={t(BRIEF_HINT)} value={form.brief} onChange={set("brief")} />
      </Field>
      <div className="flex justify-end gap-3">
        <button className="btn" onClick={onCancel}>{t("Cancelar")}</button>
        <button className="btn btn-go" onClick={() => onSave(form)} disabled={!form.name}>{t("Salvar")}</button>
      </div>
    </div>
  );
}

function themesOf(c: Channel, t: T): string {
  const line = (c.brief ?? "").split("\n").find((l) => /^(temas?|topics?):/i.test(l.trim()));
  return line ? line.split(":").slice(1).join(":").trim() : t(STYLE_LABEL[c.default_style] ?? c.default_style);
}

const STATUS_PILL: Record<string, { label: string; cls: string }> = {
  active: { label: "ativo", cls: "bg-go-soft text-go-text" },
  paused: { label: "pausado", cls: "bg-chip text-warn-text" },
  archived: { label: "arquivado", cls: "bg-line-soft text-muted" },
  series: { label: "série (sem canal próprio)", cls: "bg-claude-soft text-claude-text" },
};

export default function ChannelsPage() {
  const { channels: all, avatars, avatarOf, reloadSetup } = useAppData();
  const { t } = useT();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  // series published on the novelinha channels are managed on Fábrica > Séries, not here
  const archived = all.filter((c) => c.status === "archived").length;
  const channels = all.filter((c) => c.status !== "series" && (showArchived || c.status !== "archived"));

  async function setStatus(c: Channel, status: string) {
    await api(`/channels/${c.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    reloadSetup();
  }
  // same rule as the backend: the channel's avatar, else the only ready one
  const ready = avatars.filter((a) => a.status === "ready");
  const defaultAvatar = (c: Channel) => avatarOf(c.avatar_id) ?? (ready.length === 1 ? ready[0] : undefined);

  async function save(id: number | "new", f: Form) {
    await api(id === "new" ? "/channels" : `/channels/${id}`, { method: id === "new" ? "POST" : "PATCH", body: toBody(f) });
    setEditing(null);
    reloadSetup();
  }

  return (
    <>
      <PageHeader title={t("Canais")} subtitle={t("Configure uma vez. Cada tema em alta é enviado sozinho para o canal que combina com ele.")} />
      {!!archived && (
        <label className="mb-3 flex items-center gap-2 text-sm text-body">
          <input type="checkbox" className="h-4 w-4 accent-go" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          {t("Mostrar arquivados ({n})", { n: archived })}
        </label>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-[repeat(auto-fit,minmax(280px,1fr))]">
        {channels.map((c) =>
          editing === c.id ? (
            <ChannelForm key={c.id} initial={toForm(c)} avatars={avatars} onSave={(f) => save(c.id, f)} onCancel={() => setEditing(null)} />
          ) : (
            <article key={c.id} className={`card flex flex-col gap-3 p-5 ${c.status === "archived" ? "opacity-70" : ""}`}>
              <div className="flex items-center gap-3">
                <Initial name={c.name} size={48} />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-[17px] font-semibold"><span aria-hidden>{LANG_FLAG[c.language] ?? ""}</span> {c.name}</span>
                  <span className="text-[13px] text-muted">{c.youtube_handle ? `${c.youtube_handle} · ` : ""}{PLATFORM_LABEL[c.platform] ?? c.platform} · {t("publicação {mode}", { mode: c.publish_mode === "manual" ? t("manual") : c.publish_mode })}</span>
                </span>
                <span className={`pill ml-auto shrink-0 ${STATUS_PILL[c.status ?? "active"].cls}`}>{t(STATUS_PILL[c.status ?? "active"].label)}</span>
              </div>
              <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-body">
                <li>{t("Temas:")} {themesOf(c, t)}</li>
                <li>{t("Avatar:")} {defaultAvatar(c)?.name ?? t("sem avatar (só narração)")}{!c.avatar_id && defaultAvatar(c) ? ` ${t("(automático: seu único avatar pronto)")}` : ""} · {t("Voz:")} {defaultAvatar(c)?.status === "ready" ? t("clonada") : t("padrão do sistema")}</li>
                <li>{t("Idioma padrão:")} {t(LANG_LABEL[c.language] ?? c.language)}</li>
                <li>{t("Avatar aparece:")} {c.presenter === "overlay" ? t("o vídeo inteiro, recortado embaixo") : c.presenter === "editorial" ? t("não aparece (formato editorial, voz do canal)") : c.presenter === "podcast" ? t("podcast no estúdio") : t("só nas cenas de avatar")}</li>
                {c.made_for_kids && <li>{t("Conteúdo para crianças (YouTube “feito para crianças”)")}</li>}
                {!!c.schedule?.slots?.length && <li>{t("Horários:")} {c.schedule.slots.join(", ")}</li>}
                {c.status === "paused" && <li className="text-warn-text">{t("Pausado: aparece no menu, mas não recebe vídeos novos.")}</li>}
                {c.status === "archived" && <li className="text-muted">{t("Arquivado: nada foi apagado; histórico e publicações continuam guardados.")}</li>}
                {!c.brief && !c.profile?.persona && <li className="text-warn-text">{t("Sem briefing: o roteiro segue só o estilo.")}</li>}
              </ul>
              <div className="flex flex-wrap gap-2">
                <Link href={`/canais/${c.id}`} className="btn btn-go btn-sm">{t("Fila do canal")}</Link>
                <button className="btn btn-sm" onClick={() => setEditing(c.id)}>{t("Editar")}</button>
                {(c.status ?? "active") !== "active" && <button className="btn btn-sm" onClick={() => setStatus(c, "active")}>{t("Reativar")}</button>}
                {(c.status ?? "active") === "active" && <button className="btn btn-sm" onClick={() => setStatus(c, "paused")}>{t("Pausar")}</button>}
                {c.status !== "archived" && <button className="btn btn-sm" onClick={() => setStatus(c, "archived")}>{t("Arquivar")}</button>}
              </div>
            </article>
          ),
        )}
        {editing === "new" ? (
          <ChannelForm initial={EMPTY} avatars={avatars} onSave={(f) => save("new", f)} onCancel={() => setEditing(null)} />
        ) : (
          <button className="pick min-h-[120px] rounded-2xl border-2 border-dashed border-field bg-transparent text-center text-base font-semibold text-body hover:border-ink" onClick={() => setEditing("new")}>
            + {t("Novo canal")}
          </button>
        )}
      </div>
    </>
  );
}
