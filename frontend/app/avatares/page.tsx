"use client";

import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/Bits";
import { useAppData } from "@/components/AppData";
import { API, api, type Avatar } from "@/lib/api";
import { useLanguages } from "@/lib/useLanguages";
import { useT } from "@/lib/i18n";

type Voice = { id: number; language: string; ref_audio_path: string | null };
type Detail = Avatar & { base_video_dir: string | null; voices: Voice[] };

async function upload(path: string, blob: Blob, filename: string) {
  const fd = new FormData();
  fd.append("file", blob, filename);
  const res = await fetch(`${API}${path}`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function Recorder({ kind, onDone }: { kind: "video" | "audio"; onDone: (b: Blob) => void }) {
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [secs, setSecs] = useState(0);
  const preview = useRef<HTMLVideoElement>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const { t } = useT();

  async function start() {
    const stream = await navigator.mediaDevices.getUserMedia(
      kind === "video" ? { video: { width: 1920, height: 1080 }, audio: true } : { audio: { echoCancellation: false, noiseSuppression: true } },
    );
    if (preview.current && kind === "video") {
      preview.current.srcObject = stream;
      preview.current.play();
    }
    const chunks: Blob[] = [];
    const r = new MediaRecorder(stream);
    r.ondataavailable = (e) => chunks.push(e.data);
    r.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      onDone(new Blob(chunks, { type: r.mimeType }));
    };
    r.start();
    setRec(r);
    setSecs(0);
    timer.current = setInterval(() => setSecs((s) => s + 1), 1000);
  }

  function stop() {
    rec?.stop();
    setRec(null);
    if (timer.current) clearInterval(timer.current);
  }

  return (
    <div className="space-y-2">
      {kind === "video" && <video ref={preview} muted className="aspect-video w-full max-w-md rounded-xl bg-ink" />}
      {rec ? (
        <button className="btn border-alert bg-alert text-white hover:bg-alert-text hover:text-white" onClick={stop}>{t("Parar ({n}s)", { n: secs })}</button>
      ) : (
        <button className="btn" onClick={start}><span className="h-2.5 w-2.5 rounded-full bg-alert" />{kind === "video" ? t("Gravar vídeo") : t("Gravar voz")}</button>
      )}
    </div>
  );
}

function AvatarCard({ a, reload }: { a: Avatar; reload: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [lang, setLang] = useState("pt-BR");
  const [text, setText] = useState("");
  const [sections, setSections] = useState<{ title: string; direction: string; lines: { text: string }[] }[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const { t } = useT();
  const videoLangs = useLanguages();

  const load = () => api<Detail>(`/avatars/${a.id}/detail`).then(setD);
  useEffect(() => {
    load();
  }, [a.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<{ text: string; sections: typeof sections }>(`/avatars/meta/reading-text/${lang}`).then((r) => { setText(r.text); setSections(r.sections); }).catch(() => {});
  }, [lang]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg(t("Processando…"));
    try {
      const r = (await fn()) as { problems?: string[] };
      setMsg(r?.problems?.length ? `${t("Atenção:")} ${r.problems.join("; ")}` : ok);
      load();
      reload();
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function preview() {
    setBusy(true);
    const r = await api<{ engine: string; url: string }>(`/avatars/${a.id}/voice/${lang}/preview`, {
      method: "POST",
      body: JSON.stringify({ text: text.split(/[.!?؟]/)[0] }),
    });
    setPreviewUrl(`${API}${r.url}?t=${Date.now()}`);
    setMsg(r.engine === "voxcpm2" ? t("Prévia com sua voz clonada.") : t("Prévia com voz do macOS (grave sua voz e baixe o modelo de clonagem)."));
    setBusy(false);
  }

  const hasVoice = (l: string) => d?.voices.some((v) => v.language === l && v.ref_audio_path);

  return (
    <div className="card space-y-4 p-5">
      <div className="flex items-center gap-3">
        {d?.base_video_dir && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={`${API}/avatars/${a.id}/still?t=${Date.now()}`} alt="" className="h-16 w-16 rounded-full object-cover" />
        )}
        <div>
          <div className="text-[17px] font-semibold">{a.name}</div>
          <div className="text-[13px] text-muted">{a.status === "ready" ? t("pronto") : a.status === "new" ? t("falta gravar") : a.status === "processing" ? t("animando a foto…") : a.status} {a.notes && `· ${a.notes}`}</div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <h3 className="m-0 text-[15px] font-semibold">{t("1. Imagem (vídeo-base)")}</h3>
          <p className="text-xs text-muted">{t("Cerca de 60 s em silêncio seguindo o roteiro (boca fechada; a boca é recriada pela sua voz depois). Rosto sempre visível e grande no quadro, luz de frente, fundo neutro.")}</p>
          <a className="btn" href="/teleprompter?kind=video" target="_blank" rel="noreferrer">{t("Abrir teleprompter do vídeo")}</a>
          <Recorder kind="video" onDone={(b) => run(() => upload(`/avatars/${a.id}/base-video`, b, "base.webm"), t("Vídeo-base salvo."))} />
          <label className="block text-xs text-muted">{t("ou enviar arquivo")}
            <input type="file" accept="video/*" disabled={busy} className="mt-1 block text-xs"
              onChange={(e) => e.target.files?.[0] && run(() => upload(`/avatars/${a.id}/base-video`, e.target.files![0], e.target.files![0].name), t("Vídeo-base salvo."))} />
          </label>
          <label className="block rounded-xl bg-bg p-3 text-xs text-muted">
            <strong className="text-sm text-ink">{t("ou criar a partir de uma foto")}</strong>
            <span className="mt-0.5 block">{t("Foto de frente, olhando para a câmera, da cintura para cima, com espaço acima da cabeça. A IA anima (pisca, respira, olhar fixo) em ~3 min.")}</span>
            <input type="file" accept="image/*" disabled={busy || a.status === "processing"} className="mt-1.5 block text-xs"
              onChange={(e) => e.target.files?.[0] && run(() => upload(`/avatars/${a.id}/photo`, e.target.files![0], e.target.files![0].name), t("Foto enviada. Animando… (a imagem atualiza sozinha quando terminar)"))} />
          </label>
        </div>

        <div className="space-y-2">
          <h3 className="m-0 text-[15px] font-semibold">{t("2. Voz por idioma")}</h3>
          <div className="flex gap-2">
            {videoLangs.map((l) => (
              <button key={l} aria-pressed={l === lang} className={`btn btn-sm ${l === lang ? "btn-ink" : ""}`} onClick={() => setLang(l)}>{l} {hasVoice(l) ? "✓" : ""}</button>
            ))}
          </div>
          <div dir={lang === "ar-AE" ? "rtl" : "ltr"} className="max-h-64 space-y-2 overflow-y-auto rounded-xl bg-bg p-3 text-sm leading-relaxed">
            {sections.map((s, i) => (
              <div key={i}>
                <div className="text-xs font-semibold text-go-text">{s.title}{s.direction && <span className="font-normal text-muted"> · {s.direction}</span>}</div>
                {s.lines.map((l, j) => <p key={j}>{l.text}</p>)}
              </div>
            ))}
          </div>
          <p className="text-xs text-muted">
            {t("Leia tudo de uma vez (~75 s), mudando o tom a cada bloco, em ambiente silencioso e com o microfone a ~20 cm. O sistema guarda a gravação inteira e escolhe sozinho o melhor trecho para a clonagem. Sem árabe? A clonagem usa sua gravação em outro idioma.")}{" "}
            <a href={`/teleprompter?kind=${lang}`} target="_blank" rel="noreferrer">{t("Abrir no teleprompter")}</a>
          </p>
          <Recorder kind="audio" onDone={(b) => run(() => upload(`/avatars/${a.id}/voice/${lang}`, b, `voice_${lang}.webm`), t("Voz {lang} salva.", { lang }))} />
          <button className="btn" disabled={busy} onClick={preview}>{t("Ouvir prévia")}</button>
          {previewUrl && <audio src={previewUrl} controls autoPlay className="w-full" />}
          <div className="rounded-xl bg-bg p-3 text-xs text-muted">
            <strong className="text-sm text-ink">{t("3. Voz real (treino, ~30 min)")}</strong>
            <span className="mt-0.5 block">{t("Leia ~350 frases curtas, uma de cada vez. Com elas a voz do avatar é treinada para soar como você de verdade (ritmo, pausas, sotaque), não só o timbre.")}</span>
            <a className="btn btn-sm mt-1.5" href={`/avatares/estudio-voz?avatar=${a.id}`}>{t("Abrir estúdio de voz")}</a>
          </div>
        </div>
      </div>
      {msg && <p role="status" className="text-sm text-body">{msg}</p>}
    </div>
  );
}

export default function AvatarsPage() {
  const [avatars, setAvatars] = useState<Avatar[]>([]);
  const [name, setName] = useState("");

  const { reloadSetup } = useAppData();
  const { t } = useT();
  const load = () => {
    api<Avatar[]>("/avatars").then(setAvatars).catch(() => {});
    reloadSetup();
  };
  const processing = avatars.some((a) => a.status === "processing");
  useEffect(() => {  // a photo is being animated in the worker: refresh until it's done
    if (!processing) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [processing]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function create() {
    await api("/avatars", { method: "POST", body: JSON.stringify({ name }) });
    setName("");
    load();
  }

  return (
    <>
      <PageHeader title={t("Avatares")} subtitle={t("Grave uma vez: o vídeo-base dá o rosto e a gravação de voz dá a voz clonada em cada idioma.")} />
      <div className="card flex flex-wrap gap-2 p-4">
        <input className="input flex-1" aria-label={t("Nome do novo avatar")} placeholder={t("Nome do avatar (ex.: Ana)")} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn btn-go" onClick={create} disabled={!name}>{t("Criar avatar")}</button>
      </div>
      {avatars.map((a) => <AvatarCard key={a.id} a={a} reload={load} />)}
    </>
  );
}
