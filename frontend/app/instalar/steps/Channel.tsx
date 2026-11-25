"use client";

import { useEffect, useRef, useState } from "react";
import { CheckBox } from "@/components/Bits";
import { API, api, apiDetail, PLATFORM_LABEL, uploadBrand, type BumperPreview, type SetupFeatures } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Wizard } from "./types";
import { Choice, Field, Note, Section, StepFooter, StepHead, Toggle } from "./ui";

type Lucas = { id: number; name: string; status: string };
type Synthetic = "auto" | "yes" | "no";
type Publish = { synthetic: Synthetic; paid_promotion: boolean; auto_chapters: boolean; auto_places: boolean; notify_subscribers: boolean };

const PLATFORMS = ["youtube", "youtube_shorts", "instagram", "tiktok"];
const POLL_MS = 2000;
const EXAMPLE_LOGO = "/brand/logo-icon.png";

/** Step 6: the first channel (language, publish defaults), its logo and banner, and the short bumper preview. */
export default function Channel({ w }: { w: Wizard }) {
  const { t, lang } = useT();
  const channelId = typeof w.choices.channel_id === "number" ? w.choices.channel_id : null;
  const [avatars, setAvatars] = useState<boolean | null>(null);
  const [lucas, setLucas] = useState<Lucas | null>(null);
  const [lucasErr, setLucasErr] = useState("");
  const [useLucas, setUseLucas] = useState(true);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState<"pt-BR" | "en-US">(lang === "en" ? "en-US" : "pt-BR");
  const [brief, setBrief] = useState("");
  const [platforms, setPlatforms] = useState<string[]>(["youtube", "youtube_shorts"]);
  const [kids, setKids] = useState(false);
  const [bumper, setBumper] = useState(true);
  const [publish, setPublish] = useState<Publish>({ synthetic: "auto", paid_promotion: false, auto_chapters: false, auto_places: false, notify_subscribers: false });
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);

  // "Usar avatares" kept on → Lucas is the default presenter. An existing Lucas is just used. A new install gets him
  // right away; a finished/older install (the owner's machine) installs him only when the channel is created with him.
  const fresh = !(w.info.state.done || w.info.state.legacy);
  const [lucasBusy, setLucasBusy] = useState(false);
  const installLucas = async (): Promise<Lucas | null> => {
    setLucasBusy(true);
    setLucasErr("");
    try {
      const l = await api<Lucas>("/setup/avatar/lucas", { method: "POST" });
      setLucas(l);
      return l;
    } catch (e) {
      setLucasErr(apiDetail(e));
      return null;
    } finally {
      setLucasBusy(false);
    }
  };
  useEffect(() => {
    api<SetupFeatures>("/setup/features").then((f) => setAvatars(f.avatars)).catch(() => setAvatars(false));
  }, []);
  useEffect(() => {
    if (!avatars || channelId) return;
    api<Lucas[]>("/avatars").then((list) => {
      const found = list.find((a) => a.name === "Lucas");
      if (found) setLucas(found);
      else if (fresh) installLucas();
    }).catch(() => { if (fresh) installLucas(); });
  }, [avatars, channelId, fresh]);

  async function create() {
    setErr("");
    if (!name.trim()) { setErr(t("Dê um nome ao canal.")); return; }
    setCreating(true);
    try {
      const presenter = avatars && useLucas ? (lucas ?? await installLucas()) : null;
      if (avatars && useLucas && !presenter) return; // the Lucas card shows why; the person can pick "Sem avatar"
      const c = await api<{ id: number; name: string }>("/setup/channel", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(), language, brief: brief.trim(), avatar_id: presenter ? presenter.id : null,
          platforms, made_for_kids: kids, bumper, publish,
        }),
      });
      await w.save({ channel_id: c.id, channel_name: c.name, channel_language: language, bumper });
      window.scrollTo({ top: 0 });
    } catch (e) {
      setErr(apiDetail(e));
    } finally {
      setCreating(false);
    }
  }

  if (channelId) return <Brand w={w} channelId={channelId} />;

  const pub = (p: Partial<Publish>) => setPublish({ ...publish, ...p });
  return (
    <>
      <StepHead title={t("Seu primeiro canal")}
        lead={t("Um canal aqui é um canal do YouTube, em um idioma. A IA lê o nicho para sugerir temas e escrever no tom certo. Você cria outros depois em Canais.")} />

      <Section title={t("Sobre o canal")}>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3">
          <Field label={t("Nome")}>
            <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder={t("ex.: Curiosidades do Espaço")} />
          </Field>
          <Field label={t("Idioma dos vídeos")}>
            <select className="input" value={language} onChange={(e) => setLanguage(e.target.value as "pt-BR" | "en-US")}>
              <option value="pt-BR">{t("Português (Brasil)")}</option>
              <option value="en-US">{t("Inglês (EUA)")}</option>
            </select>
          </Field>
        </div>
        <Field label={t("Nicho e tom")} note={t("Sobre o que é o canal, para quem, e como você quer que soe. Algumas frases bastam.")}>
          <textarea className="input min-h-28 py-3" value={brief} maxLength={4000} onChange={(e) => setBrief(e.target.value)}
            placeholder={t("ex.: Curiosidades sobre o espaço para quem gosta de ciência sem complicação. Tom leve, com humor.")} />
        </Field>
      </Section>

      {avatars && (
        <Section title={t("Apresentador")} hint={t("Quem aparece falando nos vídeos.")}>
          <div role="radiogroup" aria-label={t("Apresentador")} className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-2">
            <Choice on={useLucas} onPick={() => setUseLucas(true)} label="Lucas"
              hint={lucas ? t("Avatar pronto, já instalado.") : lucasErr ? t("Não consegui instalar o Lucas: {err}", { err: lucasErr })
                : lucasBusy ? t("Instalando…") : t("Avatar pronto. Ele é instalado quando você criar o canal.")} />
            <Choice on={!useLucas} onPick={() => setUseLucas(false)} label={t("Sem avatar")} hint={t("Narração sobre imagens e vídeos.")} />
          </div>
        </Section>
      )}

      <Section title={t("Onde publicar")} hint={t("A fábrica prepara uma versão para cada lugar marcado.")}>
        <div className="flex flex-wrap gap-x-5">
          {PLATFORMS.map((p) => (
            <span key={p} className="inline-flex items-center gap-1 text-[15px]">
              <CheckBox checked={platforms.includes(p)} label={PLATFORM_LABEL[p]}
                onChange={() => setPlatforms(platforms.includes(p) ? platforms.filter((x) => x !== p) : [...platforms, p])} />
              {PLATFORM_LABEL[p]}
            </span>
          ))}
        </div>
      </Section>

      <Section title={t("Padrões de publicação")} hint={t("Vêm prontos no kit de cada vídeo. Você muda vídeo a vídeo na hora de publicar.")}>
        <Toggle on={kids} onChange={setKids} label={t("Feito para crianças")} hint={t("Só ligue se o canal for para crianças. O YouTube desliga comentários e anúncios personalizados nesses vídeos.")} />
        <div className="flex flex-col gap-1.5">
          <span className="text-[15px] font-semibold">{t("Conteúdo alterado ou sintético")}</span>
          <span className="text-[13px] text-muted">{t("O aviso do YouTube para pessoas ou cenas feitas por IA.")}</span>
          <select className="input w-fit" value={publish.synthetic} onChange={(e) => pub({ synthetic: e.target.value as Synthetic })}>
            <option value="auto">{t("Automático: sim só quando o avatar aparece")}</option>
            <option value="yes">{t("Sempre sim")}</option>
            <option value="no">{t("Sempre não")}</option>
          </select>
        </div>
        <Toggle on={publish.paid_promotion} onChange={(v) => pub({ paid_promotion: v })} label={t("Promoção paga")} hint={t("Marque só em vídeos patrocinados.")} />
        <Toggle on={publish.auto_chapters} onChange={(v) => pub({ auto_chapters: v })} label={t("Capítulos automáticos do YouTube")} hint={t("A fábrica já escreve os capítulos na descrição.")} />
        <Toggle on={publish.auto_places} onChange={(v) => pub({ auto_places: v })} label={t("Localização automática")} />
        <Toggle on={publish.notify_subscribers} onChange={(v) => pub({ notify_subscribers: v })} label={t("Publicar no feed e notificar inscritos")} />
      </Section>

      <Section title={t("Vinheta")}>
        <Toggle on={bumper} onChange={setBumper} label={t("Vinheta curta depois da abertura")}
          hint={t("Até 3 segundos com o logo do canal e um som, sem fala. Você vê a prévia no próximo passo deste canal.")} />
      </Section>

      {err && <Note tone="alert">{err}</Note>}
      <StepFooter onBack={w.back}>
        <button type="button" className="btn" onClick={w.next}>{t("Criar depois")}</button>
        <button type="button" className="btn btn-go" disabled={creating || lucasBusy || !name.trim()} onClick={create}>
          {creating ? t("Criando…") : t("Criar canal")}
        </button>
      </StepFooter>
    </>
  );
}

/** After the channel exists: logo + banner upload with preview, then the bumper preview loop. */
function Brand({ w, channelId }: { w: Wizard; channelId: number }) {
  const { t } = useT();
  const [logo, setLogo] = useState<File | null>(null);
  const [banner, setBanner] = useState<File | null>(null);
  const [preview, setPreview] = useState<{ logo?: string; banner?: string }>({});
  const [sent, setSent] = useState<{ logo: boolean; banner: boolean }>({ logo: Boolean(w.choices.logo_sent), banner: Boolean(w.choices.banner_sent) });
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState("");
  const wantsBumper = w.choices.bumper !== false;
  const [approved, setApproved] = useState(w.choices.bumper_approved === true);
  const finished = sent.logo && (!wantsBumper || approved);

  const urls = useRef<{ logo?: string; banner?: string }>({});
  useEffect(() => () => { Object.values(urls.current).forEach((u) => u && URL.revokeObjectURL(u)); }, []);

  function pick(kind: "logo" | "banner", f: File | null) {
    (kind === "logo" ? setLogo : setBanner)(f);
    const old = urls.current[kind];
    if (old) URL.revokeObjectURL(old);
    urls.current[kind] = f ? URL.createObjectURL(f) : undefined;
    setPreview((p) => ({ ...p, [kind]: urls.current[kind] }));
  }

  async function useExample() {
    try {
      const blob = await (await fetch(EXAMPLE_LOGO)).blob();
      pick("logo", new File([blob], "logo.png", { type: "image/png" }));
    } catch { /* the person can still choose a file */ }
  }

  async function send() {
    setErr("");
    setUploading(true);
    try {
      await uploadBrand(channelId, { logo, banner });
      const next = { logo: sent.logo || Boolean(logo), banner: sent.banner || Boolean(banner) };
      setSent(next);
      setLogo(null);
      setBanner(null);
      await w.save({ logo_sent: next.logo, banner_sent: next.banner });
    } catch (e) {
      setErr(apiDetail(e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <StepHead title={t("Logo e capa de {name}", { name: String(w.choices.channel_name ?? t("seu canal")) })}
        lead={t("O canal foi criado. Agora a foto de perfil e a capa: entram na vinheta e no fundo das miniaturas. Se ainda não tiver, pule e envie depois na página do canal.")} />

      <Section title={t("Imagens do canal")} hint={t("PNG ou JPG. O logo precisa ter pelo menos 400 × 400 px e é cortado em quadrado; a capa é cortada em 16:9.")}>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4">
          <ImagePick label={t("Logo (foto de perfil)")} src={preview.logo} done={sent.logo && !logo} square onPick={(f) => pick("logo", f)}
            extra={!logo && !sent.logo ? <button type="button" className="btn btn-sm w-fit" onClick={useExample}>{t("Usar imagem de exemplo")}</button> : null} />
          <ImagePick label={t("Capa (banner)")} src={preview.banner} done={sent.banner && !banner} onPick={(f) => pick("banner", f)} />
        </div>
        {(logo || banner) && (
          <button type="button" className="btn w-fit" disabled={uploading} onClick={send}>{uploading ? t("Enviando…") : t("Enviar imagens")}</button>
        )}
        {err && <Note tone="alert">{err}</Note>}
      </Section>

      {wantsBumper && <Bumper channelId={channelId} ready={sent.logo} approved={approved} onApproved={async () => { setApproved(true); await w.save({ bumper_approved: true }); }} />}

      <StepFooter onBack={w.back}>
        <button type="button" className={`btn ${finished ? "btn-go" : ""}`} onClick={w.next}>{sent.logo ? t("Avançar") : t("Pular por agora")}</button>
      </StepFooter>
    </>
  );
}

function ImagePick({ label, src, done, square, onPick, extra }: {
  label: string; src?: string; done: boolean; square?: boolean; onPick: (f: File | null) => void; extra?: React.ReactNode;
}) {
  const { t } = useT();
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="flex flex-col gap-2">
      <span className="flex items-center gap-2 text-[13px] font-semibold text-body">
        {label}
        {done && <span className="pill bg-go-soft px-2 py-0.5 text-[12px] text-go-text">{t("enviado ✓")}</span>}
      </span>
      <button type="button" onClick={() => input.current?.click()}
        className={`pick flex items-center justify-center overflow-hidden rounded-xl border border-dashed border-field bg-bg text-sm text-muted hover:bg-chip ${square ? "aspect-square max-w-[200px]" : "aspect-video"}`}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : <span className="px-3 text-center">{t("Escolher imagem")}</span>}
      </button>
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1} aria-label={label}
        onChange={(e) => onPick(e.target.files?.[0] ?? null)} />
      {extra}
    </div>
  );
}

function Bumper({ channelId, ready, approved, onApproved }: { channelId: number; ready: boolean; approved: boolean; onApproved: () => Promise<void> }) {
  const { t } = useT();
  const [st, setSt] = useState<BumperPreview | null>(null);
  const [prompt, setPrompt] = useState("");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState(approved);
  const load = () => api<BumperPreview>(`/setup/channel/${channelId}/bumper`).then(setSt).catch(() => {});
  useEffect(() => { load(); }, [channelId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (st?.state !== "running") return;
    const i = setInterval(load, POLL_MS);
    return () => clearInterval(i);
  }, [st?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  async function make() {
    setErr("");
    setOk(false);
    try {
      setSt(await api<BumperPreview>(`/setup/channel/${channelId}/bumper`, { method: "POST", body: JSON.stringify({ prompt: prompt.trim() || null }) }));
    } catch (e) {
      setErr(apiDetail(e));
    }
  }
  async function approve() {
    setErr("");
    try {
      await api(`/setup/channel/${channelId}/bumper/approve`, { method: "POST" });
      setOk(true);
      await onApproved();
    } catch (e) {
      setErr(apiDetail(e));
    }
  }

  const running = st?.state === "running";
  const readyVideo = st?.state === "ready" && st.url;
  return (
    <Section title={t("Vinheta")} hint={t("Uma abertura de até 3 segundos com o seu logo, que entra logo depois da promessa de cada vídeo.")}
      aside={ok ? <span className="pill bg-go-soft text-go-text">{t("aprovada ✓")}</span> : null}>
      {!ready && <Note>{t("Envie o logo acima para gerar a vinheta.")}</Note>}
      {readyVideo && (
        <video key={st.url} src={`${API}${st.url}`} controls autoPlay playsInline className="aspect-video w-full rounded-xl bg-ink" />
      )}
      {running && (
        <div className="flex aspect-video w-full items-center justify-center rounded-xl bg-bg text-sm text-muted" aria-live="polite">
          {t("Gerando a vinheta… leva alguns segundos.")}
        </div>
      )}
      {st?.state === "failed" && <Note tone="alert">{t("A vinheta não saiu: {err}", { err: st.error ?? "" })}</Note>}
      {ready && (
        <Field label={readyVideo ? t("Quer outra? Descreva como") : t("Como você imagina a vinheta (opcional)")}
          note={t("ex.: entra deslizando, com brilho azul e um som de impacto")}>
          <input className="input" value={prompt} maxLength={500} onChange={(e) => setPrompt(e.target.value)} />
        </Field>
      )}
      {err && <Note tone="alert">{err}</Note>}
      {ready && (
        <div className="flex flex-wrap gap-3">
          <button type="button" className="btn" disabled={running} onClick={make}>{readyVideo || st?.state === "failed" ? t("Gerar outra") : t("Gerar vinheta")}</button>
          {readyVideo && !ok && <button type="button" className="btn btn-go" onClick={approve}>{t("Aprovar")}</button>}
        </div>
      )}
    </Section>
  );
}
