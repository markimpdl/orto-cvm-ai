"use client";

/** "Editar perfil": every Channel.profile field (docs/plano-canais-e-voz.md, Phase 3) plus status, handle, YouTube id,
 *  group and made-for-kids. Saves with PATCH /channels/{id}, keeping profile keys this form does not show. */
import { useState } from "react";
import { formatLabel } from "@/components/ChannelWeek";
import { api, AUDIENCE_TZ, FORMAT_LABEL, timeIn, WEEKDAY_LABEL, WEEKDAYS, type Channel, type ChannelFormat, type PublishSlot } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useHomeTz } from "@/lib/useHomeTz";

const CATEGORIES: [number, string][] = [
  [1, "Filmes e desenhos"], [2, "Autos e veículos"], [10, "Música"], [15, "Animais"], [17, "Esportes"], [19, "Viagens e eventos"],
  [20, "Jogos"], [22, "Pessoas e blogs"], [23, "Comédia"], [24, "Entretenimento"], [25, "Notícias e política"],
  [26, "Guia e estilo"], [27, "Educação"], [28, "Ciência e tecnologia"], [29, "Sem fins lucrativos e ativismo"],
];
const SECONDS_KINDS = new Set(["short", "live_cut"]);
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** Offset of a timezone at an instant (ms), from Intl. */
function tzOffsetMs(utcMs: number, tz: string): number {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date(utcMs)).map((x) => [x.type, x.value]),
  );
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - utcMs;
}

/** The slot's next occurrence as a UTC instant (DST-correct for this week), or null when the time is invalid. */
function nextOccurrence(slot: PublishSlot): number | null {
  const [h, m] = (slot.time || "").split(":").map(Number);
  if (Number.isNaN(h)) return null;
  let tz = slot.tz;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
  } catch {
    return null;
  }
  tz ||= "America/Sao_Paulo";
  const today = new Date().toLocaleDateString("en-CA", { timeZone: tz }).split("-").map(Number);
  for (let i = 0; i < 7; i++) {
    const guess = Date.UTC(today[0], today[1] - 1, today[2] + i, h, m || 0);
    const wd = WEEKDAYS[(new Date(guess).getUTCDay() + 6) % 7];
    if (slot.days.length && !slot.days.includes(wd)) continue;
    const first = guess - tzOffsetMs(guess, tz);
    return guess - tzOffsetMs(first, tz);
  }
  return null;
}

/** The slot's time in the studio's timezone (GET /system/locale), beside the audience's. */
function HomeHint({ slot }: { slot: PublishSlot }) {
  const { t, locale } = useT();
  const home = useHomeTz();
  if (slot.tz === home) return <span className="text-[13px] text-muted">{t("Já é o horário local.")}</span>;
  const at = nextOccurrence(slot);
  if (at == null) return null;
  const iso = new Date(at).toISOString();
  const d = (tz: string) => new Date(at).toLocaleDateString("en-CA", { timeZone: tz });
  const shift = d(home) > d(slot.tz) ? ` ${t("(dia seguinte)")}` : d(home) < d(slot.tz) ? ` ${t("(dia anterior)")}` : "";
  return <span className="text-[13px] text-muted">{t("horário local ({tz})", { tz: home })}: {timeIn(iso, home, locale)}{shift}</span>;
}

type Draft = {
  name: string; status: string; youtube_handle: string; youtube_channel_id: string; group: string; made_for_kids: boolean;
  niche: string; audience: string; persona: string; tone: string;
  content_pillars: string; banned_topics: string; title_patterns: string;
  cta_spoken: string; cta_written: string; description_footer: string; cross_links: string; default_tags: string;
  youtube_category: string; formats: ChannelFormat[]; publish_slots: PublishSlot[];
};

const toDraft = (c: Channel): Draft => {
  const p = c.profile ?? {};
  return {
    name: c.name, status: c.status ?? "active", youtube_handle: c.youtube_handle ?? "", youtube_channel_id: c.youtube_channel_id ?? "",
    group: c.group ?? "", made_for_kids: !!c.made_for_kids,
    niche: p.niche ?? "", audience: p.audience ?? "", persona: p.persona ?? "", tone: p.tone ?? "",
    content_pillars: (p.content_pillars ?? []).join("\n"), banned_topics: (p.banned_topics ?? []).join("\n"), title_patterns: (p.title_patterns ?? []).join("\n"),
    cta_spoken: p.cta?.spoken ?? "", cta_written: p.cta?.written ?? "", description_footer: p.description_footer ?? "",
    cross_links: (p.cross_links ?? []).join("\n"), default_tags: (p.default_tags ?? []).join(", "),
    youtube_category: p.youtube_category ? String(p.youtube_category) : "",
    formats: (p.formats ?? []).map((f) => ({ ...f })), publish_slots: (p.publish_slots ?? []).map((s) => ({ ...s, days: [...s.days] })),
  };
};

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-semibold">
      {label}
      {children}
      {hint && <span className="text-[13px] font-normal text-muted">{hint}</span>}
    </label>
  );
}

export default function ProfileForm({ channel, onSaved, onCancel }: { channel: Channel; onSaved: () => void; onCancel: () => void }) {
  const { t } = useT();
  const [d, setD] = useState<Draft>(() => toDraft(channel));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setD({ ...d, [k]: e.target.value });
  const audienceTz = d.publish_slots.find((s) => s.tz)?.tz ?? { "pt-BR": "America/Sao_Paulo", "en-US": "America/New_York", "ar-AE": "Asia/Dubai" }[channel.language] ?? "America/Sao_Paulo";
  const kinds = Array.from(new Set([...Object.keys(FORMAT_LABEL), ...d.formats.map((f) => f.kind)]));

  const setFormat = (i: number, patch: Partial<ChannelFormat>) => setD({ ...d, formats: d.formats.map((f, j) => (j === i ? { ...f, ...patch } : f)) });
  const setSlot = (i: number, patch: Partial<PublishSlot>) => setD({ ...d, publish_slots: d.publish_slots.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  async function save() {
    setErr(null);
    setBusy(true);
    const profile: Record<string, unknown> = { ...(channel.profile ?? {}) };
    const put = (k: string, v: unknown) => {
      if (v === "" || v == null || (Array.isArray(v) && !v.length)) delete profile[k];
      else profile[k] = v;
    };
    put("niche", d.niche.trim());
    put("audience", d.audience.trim());
    put("persona", d.persona.trim());
    put("tone", d.tone.trim());
    put("content_pillars", lines(d.content_pillars));
    put("banned_topics", lines(d.banned_topics));
    put("title_patterns", lines(d.title_patterns));
    put("cta", d.cta_spoken.trim() || d.cta_written.trim() ? { ...(channel.profile?.cta ?? {}), spoken: d.cta_spoken.trim(), written: d.cta_written.trim() } : null);
    put("description_footer", d.description_footer.trim());
    put("cross_links", lines(d.cross_links));
    put("default_tags", csv(d.default_tags));
    put("youtube_category", d.youtube_category ? Number(d.youtube_category) : null);
    put("formats", d.formats.filter((f) => f.kind).map((f) => ({ ...f, min_s: Math.round(f.min_s || 0), max_s: Math.round(f.max_s || 0), per_week: Number(f.per_week) || 0 })));
    put("publish_slots", d.publish_slots.filter((s) => s.format && s.time && s.days.length).map((s) => ({ ...s, tz: s.tz || audienceTz })));
    try {
      await api(`/channels/${channel.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: d.name.trim() || channel.name, status: d.status, made_for_kids: d.made_for_kids,
          youtube_handle: d.youtube_handle.trim() || null, youtube_channel_id: d.youtube_channel_id.trim() || null, group: d.group.trim() || null,
          profile,
        }),
      });
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const ta = "rounded-xl border border-field p-3 text-[14px] font-normal leading-normal";
  return (
    <section className="card flex flex-col gap-5 p-5" aria-label={t("Editar perfil")}>
      <h2 className="h2 m-0">{t("Perfil do canal")}</h2>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(200px,1fr))]">
        <Field label={t("Nome do canal")}><input className="input font-normal" value={d.name} onChange={set("name")} /></Field>
        <Field label={t("Status")}>
          <select className="input font-normal" value={d.status} onChange={set("status")}>
            <option value="active">{t("ativo")}</option>
            <option value="paused">{t("pausado")}</option>
            <option value="archived">{t("arquivado")}</option>
            {d.status === "series" && <option value="series">{t("série (sem canal próprio)")}</option>}
          </select>
        </Field>
        <Field label={t("Handle do YouTube")}><input className="input font-normal" value={d.youtube_handle} onChange={set("youtube_handle")} placeholder="@meucanal" /></Field>
        <Field label={t("ID do canal no YouTube")}><input className="input font-normal" value={d.youtube_channel_id} onChange={set("youtube_channel_id")} placeholder="UC…" /></Field>
        <Field label={t("Grupo (canais irmãos)")} hint={t("O mesmo nome nos canais da mesma persona em outros idiomas.")}><input className="input font-normal" value={d.group} onChange={set("group")} placeholder={t("meu-canal")} /></Field>
        <Field label={t("Categoria do YouTube")}>
          <select className="input font-normal" value={d.youtube_category} onChange={set("youtube_category")}>
            <option value="">{t("automática")}</option>
            {CATEGORIES.map(([id, label]) => <option key={id} value={id}>{t(label)}</option>)}
          </select>
        </Field>
      </div>
      <label className="flex items-start gap-2.5 text-sm">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-go" checked={d.made_for_kids} onChange={(e) => setD({ ...d, made_for_kids: e.target.checked })} />
        <span><strong>{t("Conteúdo para crianças")}</strong> <span className="text-muted">{t("(marca “feito para crianças” no YouTube)")}</span></span>
      </label>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field label={t("Nicho")}><input className="input font-normal" value={d.niche} onChange={set("niche")} /></Field>
        <Field label={t("Público")}><input className="input font-normal" value={d.audience} onChange={set("audience")} /></Field>
        <Field label={t("Persona")}><textarea rows={3} className={ta} value={d.persona} onChange={set("persona")} /></Field>
        <Field label={t("Tom")}><textarea rows={3} className={ta} value={d.tone} onChange={set("tone")} /></Field>
        <Field label={t("Pilares de conteúdo")} hint={t("Um por linha.")}><textarea rows={4} className={ta} value={d.content_pillars} onChange={set("content_pillars")} /></Field>
        <Field label={t("Temas proibidos")} hint={t("Um por linha.")}><textarea rows={4} className={ta} value={d.banned_topics} onChange={set("banned_topics")} /></Field>
        <Field label={t("Padrões de título")} hint={t("Um por linha.")}><textarea rows={3} className={ta} value={d.title_patterns} onChange={set("title_patterns")} /></Field>
        <Field label={t("Links para outros canais")} hint={t("Um por linha (ex.: @MeuCanalEmIngles).")}><textarea rows={3} className={ta} value={d.cross_links} onChange={set("cross_links")} /></Field>
        <Field label={t("Chamada falada (CTA)")}><input className="input font-normal" value={d.cta_spoken} onChange={set("cta_spoken")} /></Field>
        <Field label={t("Chamada escrita (CTA)")}><input className="input font-normal" value={d.cta_written} onChange={set("cta_written")} /></Field>
        <Field label={t("Rodapé da descrição")}><textarea rows={3} className={ta} value={d.description_footer} onChange={set("description_footer")} /></Field>
        <Field label={t("Tags padrão")} hint={t("Separadas por vírgula.")}><input className="input font-normal" value={d.default_tags} onChange={set("default_tags")} /></Field>
      </div>

      <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
        <legend className="mb-2 text-sm font-semibold">{t("Formatos e meta semanal")}</legend>
        {d.formats.map((f, i) => {
          const unit = SECONDS_KINDS.has(f.kind) ? 1 : 60;
          const u = unit === 1 ? "s" : "min";
          return (
            <div key={i} className="flex flex-wrap items-end gap-2 rounded-xl bg-bg p-3">
              <Field label={t("Formato")}>
                <select className="input font-normal" value={f.kind} onChange={(e) => setFormat(i, { kind: e.target.value })}>
                  {kinds.map((k) => <option key={k} value={k}>{formatLabel(k, 1, t)}</option>)}
                </select>
              </Field>
              <Field label={t("Mín. ({u})", { u })}><input type="number" min={0} step={unit === 1 ? 5 : 0.5} className="input w-24 font-normal" value={+(f.min_s / unit).toFixed(1)} onChange={(e) => setFormat(i, { min_s: Number(e.target.value) * unit })} /></Field>
              <Field label={t("Máx. ({u})", { u })}><input type="number" min={0} step={unit === 1 ? 5 : 0.5} className="input w-24 font-normal" value={+(f.max_s / unit).toFixed(1)} onChange={(e) => setFormat(i, { max_s: Number(e.target.value) * unit })} /></Field>
              <Field label={t("Por semana")}><input type="number" min={0} step={0.5} className="input w-24 font-normal" value={f.per_week} onChange={(e) => setFormat(i, { per_week: Number(e.target.value) })} /></Field>
              <button type="button" className="btn btn-sm" onClick={() => setD({ ...d, formats: d.formats.filter((_, j) => j !== i) })}>{t("Remover")}</button>
            </div>
          );
        })}
        <button type="button" className="btn btn-sm self-start" onClick={() => setD({ ...d, formats: [...d.formats, { kind: "long", min_s: 480, max_s: 900, per_week: 1 }] })}>+ {t("Formato")}</button>
      </fieldset>

      <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
        <legend className="mb-1 text-sm font-semibold">{t("Horários de publicação")}</legend>
        <p className="m-0 mb-1 text-[13px] text-muted">{t("No fuso do público. Ao lado, o horário local deste computador.")}</p>
        {d.publish_slots.map((s, i) => (
          <div key={i} className="flex flex-col gap-2 rounded-xl bg-bg p-3">
            <div className="flex flex-wrap items-end gap-2">
              <Field label={t("Formato")}>
                <select className="input font-normal" value={s.format} onChange={(e) => setSlot(i, { format: e.target.value })}>
                  {Array.from(new Set([...d.formats.map((f) => f.kind), s.format])).filter(Boolean).map((k) => <option key={k} value={k}>{formatLabel(k, 1, t)}</option>)}
                </select>
              </Field>
              <Field label={t("Hora")}><input type="time" className="input font-normal" value={s.time} onChange={(e) => setSlot(i, { time: e.target.value })} /></Field>
              <Field label={t("Fuso do público")}>
                <select className="input font-normal" value={s.tz} onChange={(e) => setSlot(i, { tz: e.target.value })}>
                  {Array.from(new Set([...AUDIENCE_TZ, s.tz])).filter(Boolean).map((z) => <option key={z} value={z}>{z}</option>)}
                </select>
              </Field>
              <button type="button" className="btn btn-sm" onClick={() => setD({ ...d, publish_slots: d.publish_slots.filter((_, j) => j !== i) })}>{t("Remover")}</button>
            </div>
            <div role="group" aria-label={t("Dias")} className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((wd) => {
                const on = s.days.includes(wd);
                return (
                  <button key={wd} type="button" aria-pressed={on} onClick={() => setSlot(i, { days: on ? s.days.filter((x) => x !== wd) : WEEKDAYS.filter((x) => x === wd || s.days.includes(x)) })}
                    className={`pick min-h-9 rounded-full px-3 text-[13px] font-semibold capitalize ${on ? "bg-ink text-white" : "bg-chip text-body"}`}>
                    {t(WEEKDAY_LABEL[wd])}
                  </button>
                );
              })}
            </div>
            <HomeHint slot={s} />
          </div>
        ))}
        <button type="button" className="btn btn-sm self-start" onClick={() => setD({ ...d, publish_slots: [...d.publish_slots, { format: d.formats[0]?.kind ?? "long", days: [], time: "19:00", tz: audienceTz }] })}>+ {t("Horário")}</button>
      </fieldset>

      {err && <p className="m-0 text-sm text-alert-text">{err}</p>}
      <div className="flex justify-end gap-3">
        <button className="btn" onClick={onCancel}>{t("Cancelar")}</button>
        <button className="btn btn-go" onClick={save} disabled={busy || !d.name.trim()}>{busy ? t("Salvando…") : t("Salvar perfil")}</button>
      </div>
    </section>
  );
}
