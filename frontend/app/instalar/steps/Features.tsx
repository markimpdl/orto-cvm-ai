"use client";

import { useEffect, useState } from "react";
import { useAppData } from "@/components/AppData";
import { api, apiDetail, type SetupFeatures } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { chosenFeatures, type Wizard } from "./types";
import { Field, Note, Section, StepFooter, StepHead, Toggle } from "./ui";

type KeyName = "pexels" | "pixabay" | "youtube" | "twitch_id" | "twitch_secret" | "elevenlabs";
type Region = "BR" | "GLOBAL" | "other";

/** ISO-2 codes offered under "Outro país" (the collectors take any ISO-2; this is the short list). */
const COUNTRIES: [string, string][] = [
  ["AE", "Emirados Árabes"], ["AR", "Argentina"], ["BR", "Brasil"], ["AU", "Austrália"], ["CA", "Canadá"], ["CL", "Chile"], ["CO", "Colômbia"],
  ["DE", "Alemanha"], ["EG", "Egito"], ["ES", "Espanha"], ["FR", "França"], ["GB", "Reino Unido"], ["IN", "Índia"],
  ["IT", "Itália"], ["JP", "Japão"], ["MX", "México"], ["PT", "Portugal"], ["SA", "Arábia Saudita"], ["US", "Estados Unidos"],
];

/** Defaults for a new install (spec §3.4): paid accounts off until the person turns them on. */
/** Comparable form of the saved features (arrays sorted), to PUT only what the person actually changed. */
const norm = (f: SetupFeatures) => JSON.stringify({
  ai_ideas: f.ai_ideas, regions: [...f.regions].sort(), lives: [...f.lives].sort(), vidiq: f.vidiq, follow: f.follow, avatars: f.avatars, elevenlabs: f.elevenlabs,
});

const FRESH: SetupFeatures = { ai_ideas: true, regions: ["BR"], lives: ["youtube", "kick"], vidiq: false, follow: true, avatars: true, elevenlabs: false };

/** Step 4: what to collect and show (Em alta, lives, vidIQ, followed channels, avatars, ElevenLabs) and optional keys. */
export default function Features({ w }: { w: Wizard }) {
  const { t } = useT();
  const { setToast } = useAppData();
  const [f, setF] = useState<SetupFeatures | null>(null);
  const [orig, setOrig] = useState<string | null>(null); // what GET returned: unchanged values are never written back
  const [region, setRegion] = useState<Region>("BR");
  const [keySet, setKeySet] = useState<Partial<Record<KeyName, boolean>>>({});
  const [keys, setKeys] = useState<Partial<Record<KeyName, string>>>({});
  const [skipKeys, setSkipKeys] = useState(false);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<SetupFeatures>("/setup/features?keys=1").then((r) => {
      // a finished or older install keeps what it has; only a new one starts from the spec defaults
      const saved = w.choices.features_saved === true || w.info.state.done || Boolean(w.info.state.legacy);
      const v = saved ? r : { ...FRESH, elevenlabs: Boolean(r.keys?.elevenlabs) };
      setF(v);
      setOrig(norm(r));
      setKeySet(r.keys ?? {});
      setRegion(v.regions.includes("GLOBAL") ? "GLOBAL" : v.regions.length === 1 && v.regions[0] === "BR" ? "BR" : "other");
    }).catch(() => setF({ ...FRESH }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!f) return <div className="text-muted">{t("Carregando…")}</div>;
  const set = (p: Partial<SetupFeatures>) => setF({ ...f, ...p });
  const toggleLive = (p: string) => set({ lives: f.lives.includes(p) ? f.lives.filter((x) => x !== p) : [...f.lives, p] });
  const others = f.regions.filter((r) => r !== "GLOBAL");

  async function next() {
    if (!f) return;
    setErr("");
    const regions = region === "BR" ? ["BR"] : region === "GLOBAL" ? ["GLOBAL"] : others;
    if (!regions.length) { setErr(t("Escolha pelo menos um país para as tendências.")); return; }
    const body = Object.fromEntries(Object.entries(keys).filter(([k, v]) => v?.trim() && (k === "elevenlabs" ? f.elevenlabs : k.startsWith("twitch") ? f.lives.includes("twitch") : !skipKeys)));
    setSaving(true);
    try {
      const chosen = { ...f, regions };
      // a machine without the Setting reads "everything on, regions from settings": writing it back would freeze that
      if (norm(chosen) !== orig) await api("/setup/features", { method: "PUT", body: JSON.stringify(chosen) });
      if (Object.keys(body).length) await api("/setup/keys", { method: "PUT", body: JSON.stringify(body) });
      // no avatars → the talking presenter is not suggested in the Models step
      const feats = chosenFeatures(w);
      await w.save({ features_saved: true, ...(f.avatars ? {} : { features: feats.filter((x) => x !== "avatar") }) });
      setKeys({});
      w.next();
    } catch (e) {
      setErr(t("Não consegui salvar: {err}", { err: apiDetail(e) }));
      setToast({ msg: t("Algo não foi salvo. Confira a mensagem no fim da página.") });
    } finally {
      setSaving(false);
    }
  }

  const keyInput = (k: KeyName, label: string, note?: React.ReactNode) => (
    <Field label={label} note={note}>
      <input className="input" type="password" autoComplete="off" spellCheck={false} value={keys[k] ?? ""}
        placeholder={keySet[k] ? t("já guardada (deixe vazio para manter)") : t("cole a chave aqui")}
        onChange={(e) => setKeys({ ...keys, [k]: e.target.value })} />
    </Field>
  );
  const ext = (href: string, text: string) => <a href={href} target="_blank" rel="noreferrer">{text}</a>;

  return (
    <>
      <StepHead title={t("O que você quer usar")}
        lead={t("Ligue só o que faz sentido para você. O que ficar desligado some do menu e não coleta nada. Dá para mudar depois, e nada instalado é apagado.")} />

      <Section title={t("Em alta")} hint={t("Os assuntos que estão crescendo agora, para você escolher sobre o que fazer vídeo.")}>
        <Toggle on={f.ai_ideas} onChange={(v) => set({ ai_ideas: v })} label={t("Sugestões da IA")} hint={t("A IA lê as tendências e sugere temas com um ângulo para o seu canal.")} />
        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-semibold text-body">{t("De onde vêm as tendências")}</span>
          <div role="radiogroup" aria-label={t("De onde vêm as tendências")} className="flex flex-wrap gap-2">
            {([["BR", t("Brasil")], ["GLOBAL", t("Mundo todo")], ["other", t("Outro país")]] as [Region, string][]).map(([k, label]) => (
              <button key={k} type="button" role="radio" aria-checked={region === k} onClick={() => setRegion(k)}
                className={`btn btn-sm ${region === k ? "btn-ink" : ""}`}>{label}</button>
            ))}
          </div>
          {region === "other" && (
            <div className="flex flex-wrap gap-2">
              {[...COUNTRIES].sort((a, b) => t(a[1]).localeCompare(t(b[1]))).map(([code, name]) => {
                const on = f.regions.includes(code);
                return (
                  <button key={code} type="button" aria-pressed={on}
                    onClick={() => set({ regions: on ? f.regions.filter((r) => r !== code) : [...f.regions.filter((r) => r !== "GLOBAL"), code] })}
                    className={`pill pick border-0 ${on ? "bg-ink text-white" : "bg-chip text-body hover:bg-[#e6e7ea]"}`}>
                    {t(name)}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </Section>

      <Section title={t("Lives")} hint={t("Cole o link de uma live (sua ou de outra pessoa) e a fábrica corta os melhores momentos. Escolha de quais sites.")}>
        <div className="flex flex-col gap-3">
          <Toggle on={f.lives.includes("youtube")} onChange={() => toggleLive("youtube")} label="YouTube" />
          <Toggle on={f.lives.includes("twitch")} onChange={() => toggleLive("twitch")} label="Twitch" hint={t("Precisa de duas chaves gratuitas de desenvolvedor da Twitch.")} />
          {f.lives.includes("twitch") && (
            <div className="flex flex-col gap-3 rounded-xl bg-bg p-4">
              <ol className="m-0 flex flex-col gap-1 pl-5 text-sm text-body">
                <li>{t("Entre em")} {ext("https://dev.twitch.tv/console/apps", "dev.twitch.tv/console/apps")} {t("com a sua conta da Twitch.")}</li>
                <li>{t("Clique em Register Your Application. Dê qualquer nome, use http://localhost como OAuth Redirect URL e Other como categoria.")}</li>
                <li>{t("Abra o app criado (Manage), copie o Client ID e clique em New Secret para gerar o Client Secret.")}</li>
              </ol>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                {keyInput("twitch_id", "Client ID")}
                {keyInput("twitch_secret", "Client Secret")}
              </div>
              <span className="text-[13px] text-muted">{t("Sem as chaves, as lives da Twitch ficam de fora. Você pode colocar depois.")}</span>
            </div>
          )}
          <Toggle on={f.lives.includes("kick")} onChange={() => toggleLive("kick")} label="Kick" hint={t("Ainda em teste: alguns vídeos podem não abrir.")} />
        </div>
      </Section>

      <Section title={t("Canais e avatares")}>
        <Toggle on={f.follow} onChange={(v) => set({ follow: v })} label={t("Seguir canais")} hint={t("Acompanhe outros canais do seu nicho e veja o que está dando certo para eles.")} />
        <Toggle on={f.avatars} onChange={(v) => set({ avatars: v })} label={t("Usar avatares")}
          hint={t("Um apresentador que fala nos seus vídeos. O Lucas já vem pronto. Desligado, os vídeos são narrados sobre imagens.")} />
      </Section>

      <Section title={t("Contas pagas (opcional)")} hint={t("Só se você já assina. Sem elas tudo funciona com as alternativas gratuitas.")}>
        <Toggle on={f.vidiq} onChange={(v) => set({ vidiq: v })} label="vidIQ"
          hint={t("Notas de título e miniatura, palavras-chave e vídeos fora da curva.")} />
        {f.vidiq && (
          <Note>{t("O login é feito pelo Claude Code: abra-o na pasta da fábrica, digite /mcp, escolha vidiq e entre com a sua conta. Pode fazer isso depois.")}</Note>
        )}
        <Toggle on={f.elevenlabs} onChange={(v) => set({ elevenlabs: v })} label="ElevenLabs"
          hint={t("Vozes, música e efeitos da ElevenLabs. Gasta créditos do seu plano; a voz local continua como reserva.")} />
        {f.elevenlabs && keyInput("elevenlabs", t("Chave da API da ElevenLabs"),
          <>{t("Pegue em")} {ext("https://elevenlabs.io/app/settings/api-keys", "elevenlabs.io › API Keys")}. {t("Fica guardada no cofre do sistema, nunca no banco de dados.")}</>)}
      </Section>

      <Section title={t("Chaves gratuitas (opcional)")}
        hint={t("Dão mais vídeos e fotos de banco e dados do YouTube. Sem elas a fábrica usa o Wikimedia e o Openverse, que não pedem chave.")}
        aside={<button type="button" className="btn btn-sm" aria-pressed={skipKeys} onClick={() => { setSkipKeys(!skipKeys); setKeys((k) => ({ twitch_id: k.twitch_id, twitch_secret: k.twitch_secret, elevenlabs: k.elevenlabs })); }}>
          {skipKeys ? t("Preencher agora") : t("Pular")}</button>}>
        {skipKeys ? (
          <p className="m-0 text-sm text-muted">{t("Você pode colocar as chaves depois: abra de novo este assistente em Configurações > Manutenção.")}</p>
        ) : (
          <div className="flex flex-col gap-3">
            {keyInput("pexels", "Pexels", <>{t("Grátis em")} {ext("https://www.pexels.com/api/", "pexels.com/api")}</>)}
            {keyInput("pixabay", "Pixabay", <>{t("Grátis em")} {ext("https://pixabay.com/api/docs/", "pixabay.com/api/docs")}</>)}
            {keyInput("youtube", "YouTube Data API", <>{t("Grátis no")} {ext("https://console.cloud.google.com/apis/library/youtube.googleapis.com", "Google Cloud Console")}</>)}
          </div>
        )}
      </Section>

      {err && <Note tone="alert">{err}</Note>}
      <StepFooter onBack={w.back}>
        <button type="button" className="btn btn-go" disabled={saving} onClick={next}>{saving ? t("Salvando…") : t("Avançar")}</button>
      </StepFooter>
    </>
  );
}
