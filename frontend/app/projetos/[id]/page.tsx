"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useAppData } from "@/components/AppData";
import { IconBack } from "@/components/Icons";
import ReviewPanel from "@/components/ReviewPanel";
import SceneTimeline from "@/components/SceneTimeline";
import UserMedia from "@/components/UserMedia";
import VoicePick from "@/components/VoicePick";
import ScriptReview from "@/components/ScriptReview";
import StageChips, { ProgressBar } from "@/components/StageChips";
import { api, deleteProject, LANG_LABEL, STATE_LABEL, STYLE_LABEL, type Job, type Project, type Script } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { effectiveState, estimate, fmtDuration, isFailed, phaseOf, STAGES, STEPS, stepIndex, useTick, type Phase } from "@/lib/pipeline";

type VoiceQA = { checked: boolean; sentences: number; flagged: { scene: number; text: string; heard: string; wer: number }[] };
type Detail = Project & { scripts: Script[]; job: Job | null; voice_qa?: Record<string, VoiceQA> };

const AVATAR_EDITABLE = new Set(["trend_selected", "scripting", "awaiting_approval", "awaiting_publish_review", "failed"]);

function Stepper({ job }: { job: Job | null }) {
  const cur = stepIndex(job);
  const failed = isFailed(job);
  const published = job?.state === "published";
  const { t } = useT();
  return (
    <ol aria-label={t("Andamento")} className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(118px,1fr))] gap-2 p-0">
      {STEPS.map((s, i) => {
        const done = i < cur || published;
        const now = i === cur && !published;
        return (
          <li
            key={s.key}
            aria-current={now ? "step" : undefined}
            className={`flex flex-col gap-0.5 rounded-xl px-3 py-2.5 ${
              done ? "bg-go-soft text-go-text" : now ? (failed ? "border-2 border-alert bg-alert-soft text-alert-text" : "bg-ink text-white") : "border border-line bg-surface text-muted"
            }`}
          >
            <span className="text-sm font-semibold">{done ? "✓" : `${i + 1}.`} {t(s.title)}</span>
            <span className="text-xs opacity-80">{t(s.who)}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Panel({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <section className="card flex flex-col gap-5 p-7">
      <h2 className="m-0 flex items-center gap-2.5 font-display text-2xl leading-[1.2] font-semibold">
        {title}
        {tag && <span className="rounded-full bg-claude-soft px-2.5 py-[3px] font-sans text-[13px] font-semibold text-claude-text">{tag}</span>}
      </h2>
      {children}
    </section>
  );
}

function ClaudePanel({ job, title, text }: { job: Job; title: string; text: string }) {
  const { durations } = useAppData();
  const [cmd, setCmd] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const { t } = useT();
  const est = estimate(job, durations);
  async function rerun() {
    try {
      await api(`/jobs/${job.id}/run-claude`, { method: "POST" });
      setMsg(t("Pedido enviado. Começa assim que houver vaga no Claude."));
    } catch (e) {
      setMsg(String(e).includes("409") ? t("O Claude já está trabalhando nesta etapa.") : String(e));
    }
  }
  return (
    <Panel title={title} tag="Claude">
      <p className="m-0 -mt-3 text-base text-muted">{text}</p>
      <div className="flex flex-col gap-2">
        <ProgressBar fraction={est?.fraction ?? 0.05} height="h-3" />
        {est && <span className="text-sm text-muted">{t("rodando há {elapsed} · faltam ~{left} (estimativa)", { elapsed: fmtDuration(est.elapsedS), left: fmtDuration(est.remainingS) })}</span>}
      </div>
      <div className="flex flex-wrap gap-2.5">
        <button className="btn" onClick={rerun}>{t("Rodar de novo no Claude")}</button>
        <button className="btn" onClick={() => api<{ command: string }>(`/jobs/${job.id}/claude-command`).then((r) => setCmd(r.command)).catch((e) => setMsg(String(e)))}>{t("Ver comando para o terminal")}</button>
      </div>
      {msg && <p role="status" className="m-0 text-sm text-body">{msg}</p>}
      {cmd && <pre className="m-0 overflow-x-auto rounded-[10px] bg-ink p-3 text-xs whitespace-pre-wrap text-line">cd content-video-maker && {cmd}</pre>}
      <p className="m-0 text-[13px] text-muted">{t("Roda sozinho pela sua assinatura (claude -p). Os botões acima só servem se algo travar.")}</p>
    </Panel>
  );
}

function ProductionPanel({ job }: { job: Job }) {
  const { durations } = useAppData();
  const { t } = useT();
  const est = estimate(job, durations);
  const now = t(STAGES.find((s) => s.state === job.state)?.now ?? STATE_LABEL[job.state] ?? job.state);
  const stale = job.progress?.at && Date.now() - new Date(job.progress.at).getTime() > 5 * 60_000;
  return (
    <Panel title={t("Produzindo")}>
      <p className="m-0 -mt-3 text-base text-muted">{t("Não precisa fazer nada. Avisamos em")} <strong>{t("Sua vez")}</strong> {t("quando estiver pronto.")}</p>
      <div className="flex flex-col gap-2">
        <ProgressBar fraction={est?.fraction ?? 0} height="h-3" />
        <div className="flex flex-wrap gap-2 text-sm text-muted">
          <span className="flex-1">{est ? `${Math.round(est.fraction * 100)}% · ` : ""}{now}{job.progress?.label ? ` (${job.progress.label})` : ""}</span>
          {est && <span>{t("rodando há {elapsed} · termina em ~{left} (estimativa)", { elapsed: fmtDuration(est.elapsedS), left: fmtDuration(est.remainingS) })}</span>}
        </div>
        {stale && <span className="text-sm text-warn-text">{t("Sem novidade há {d} (pode ser um modelo lento).", { d: fmtDuration((Date.now() - new Date(job.progress!.at!).getTime()) / 1000) })}</span>}
      </div>
      <StageChips job={job} variant="cards" />
    </Panel>
  );
}

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  useTick();
  const { id } = use(params);
  const router = useRouter();
  const { projects, avatars, channelOf, avatarOf, reload, setToast } = useAppData();
  const { t } = useT();
  const [data, setData] = useState<Detail | null>(null);
  const [missing, setMissing] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const timeline = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<Detail>(`/projects/${id}`));
    } catch (e) {
      if (String(e).startsWith("Error: 404")) setMissing(true);
    }
  }, [id]);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  if (missing) return <p className="text-muted">{t("Projeto não encontrado.")} <Link href="/fabrica">{t("Voltar para a fila")}</Link></p>;
  if (!data) return <p className="text-muted">{t("Carregando…")}</p>;
  const job = data.job;
  const phase: Phase = phaseOf(job);
  const nextOf = (ph: Phase) => projects.find((p) => p.id !== data.id && phaseOf(p.job) === ph);
  const nextApprove = nextOf("approve");
  const nextReview = nextOf("review");

  async function transition(to: string, note?: string) {
    if (!job) return false;
    try {
      await api(`/jobs/${job.id}/transition`, { method: "POST", body: JSON.stringify({ to, note }) });
      setMsg(null);
      await Promise.all([load(), reload()]);
      return true;
    } catch (e) {
      setMsg(String(e));
      return false;
    }
  }

  async function approve() {
    if (!(await transition("voicing"))) return;
    if (nextApprove) {
      setToast({ msg: t("Roteiro aprovado e devolvido à fila. Próximo: “{title}”", { title: nextApprove.title }) });
      router.push(`/projetos/${nextApprove.id}`);
    } else {
      setToast({ msg: t("Roteiro aprovado. O vídeo voltou para a fila.") });
      router.push("/fabrica");
    }
  }

  async function scheduled(whenLabel: string) {
    await Promise.all([load(), reload()]);
    if (nextReview) {
      setToast({ msg: t("Agendado para {when}. Próximo: “{title}”", { when: whenLabel, title: nextReview.title }) });
      router.push(`/projetos/${nextReview.id}`);
    } else {
      setToast({ msg: t("Agendado para {when}", { when: whenLabel }), link: { href: "/fabrica/publicar", label: t("Ver Publicar") } });
      router.push("/fabrica/publicar");
    }
  }

  async function setPresenter(value: string) {
    await api(`/projects/${id}`, { method: "PATCH", body: JSON.stringify({ presenter: value || null }) });
    load();
    setMsg(job?.state === "awaiting_publish_review" ? t("Para aplicar no vídeo pronto, clique em “Remontar vídeos”.") : null);
  }

  async function setMediaMode(media_mode: string) {
    await api(`/projects/${id}`, { method: "PATCH", body: JSON.stringify({ media_mode }) });
    load();
  }

  async function setMediaCut(media_cut: string) {
    await api(`/projects/${id}`, { method: "PATCH", body: JSON.stringify({ media_cut }) });
    load();
  }

  async function setAvatar(value: string) {
    await api(`/projects/${data!.id}`, { method: "PATCH", body: JSON.stringify({ avatar_id: value ? Number(value) : null }) });
    load();
  }

  async function retry() {
    if (!job) return;
    await api(`/jobs/${job.id}/${job.orphaned ? "resume" : "retry"}`, { method: "POST" }).catch((e) => setMsg(String(e)));
    await Promise.all([load(), reload()]);
  }

  const latestScene = data.languages.map((l) => data.scripts.find((s) => s.language === l)).find(Boolean);
  const showTimeline = phase === "producing" || phase === "seo" || phase === "review" || (phase === "failed" && STAGES.findIndex((s) => s.state === effectiveState(job)) >= 2);
  const failedStage = STAGES.find((s) => s.state === effectiveState(job));
  const flagged = Object.entries(data.voice_qa ?? {}).filter(([, q]) => q.flagged.length);

  return (
    <>
      <header className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" onClick={() => (window.history.length > 1 ? router.back() : router.push("/fabrica"))}><IconBack />{t("Voltar")}</button>
          <span className="flex-1" />
          <button className="btn btn-sm btn-danger" onClick={() => deleteProject(data).then((ok) => { if (ok) { reload(); router.push("/fabrica"); } }).catch((e) => setMsg(String(e)))}>{t("Excluir projeto")}</button>
        </div>
        <h1 className="m-0 font-display text-[34px] leading-[1.15] font-bold">{data.title}</h1>
        <p className="m-0 flex flex-wrap items-center gap-x-1.5 gap-y-2 text-[15px] text-muted">
          <span>{channelOf(data.channel_id)?.name ?? t("sem canal")}</span>·
          {job && AVATAR_EDITABLE.has(job.state) ? (
            <label className="inline-flex items-center gap-1.5">
              {t("avatar")}
              <select className="input min-h-9 px-2 text-[15px]" value={data.avatar_id ?? ""} onChange={(e) => setAvatar(e.target.value)} aria-label={t("Avatar e voz")}>
                <option value="">{t("sem avatar (só narração)")}</option>
                {avatars.map((a) => <option key={a.id} value={a.id}>{a.name}{a.status === "ready" ? "" : ` ${t("(gravação incompleta)")}`}</option>)}
              </select>
            </label>
          ) : (
            <span>{t("avatar")} {avatarOf(data.avatar_id)?.name ?? t("nenhum")}</span>
          )}
          {!!data.avatar_id && (
            <VoicePick avatarId={data.avatar_id} value={data.voice_variant ?? null} effective={data.voice_effective ?? null} editable={!!job && AVATAR_EDITABLE.has(job.state)}
              onChange={async (voice_variant) => {
                await api(`/projects/${id}`, { method: "PATCH", body: JSON.stringify({ voice_variant }) });
                load();
              }} />
          )}
          ·
          <label className="inline-flex items-center gap-1.5">
            {t("formato")}
            <select className="input min-h-9 px-2 text-[15px]" value={data.presenter ?? ""} onChange={(e) => setPresenter(e.target.value)} aria-label={t("Formato do apresentador")}>
              <option value="">{t("como o canal ({mode})", { mode: channelOf(data.channel_id)?.presenter === "overlay" ? t("vídeo todo") : channelOf(data.channel_id)?.presenter === "editorial" ? t("editorial, sem avatar na tela") : channelOf(data.channel_id)?.presenter === "podcast" ? t("podcast no estúdio") : t("só nas cenas dele") })}</option>
              <option value="scenes">{t("só nas cenas de avatar")}</option>
              <option value="overlay">{t("o vídeo todo, recortado embaixo")}</option>
              <option value="editorial">{t("editorial (sem avatar na tela, com a voz do canal)")}</option>
              {avatarOf(data.avatar_id)?.podcast_video_dir && <option value="podcast">{t("podcast (janelinha + tela cheia no estúdio)")}</option>}
            </select>
          </label>
          · <span>{t(STYLE_LABEL[data.style] ?? data.style)}</span> · <span>{data.languages.map((l) => t(LANG_LABEL[l] ?? l)).join(", ")}</span>
        </p>
        <Stepper job={job} />
      </header>

      {msg && <p role="alert" className="m-0 text-sm text-alert-text">{msg}</p>}

      {phase === "failed" && job && (
        <section className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3 rounded-xl bg-alert-soft px-4 py-3.5 text-[#7a0011]">
            <span className="flex-[1_1_260px] text-[15px]">
              <strong>{t(job.orphaned ? "Interrompido em {stage}" : "Parou em {stage}", { stage: failedStage ? t(failedStage.label) : effectiveState(job) })}.</strong>{" "}
              {job.orphaned ? t("Nenhum processo está trabalhando nesta etapa (o worker foi reiniciado ou caiu). Ao subir, o worker retoma sozinho.") : job.error}
            </span>
            <button className="btn btn-ink" onClick={retry}>{job.orphaned ? t("Retomar agora") : t("Tentar de novo")}</button>
          </div>
          <StageChips job={job} variant="cards" />
        </section>
      )}

      {phase === "queued" && (
        <Panel title={t("Na fila")}>
          <p className="m-0 -mt-3 text-base text-muted">
            {job?.progress?.waiting === "claude_limit" ? String(job.progress.label ?? t("Limite do Claude: aguardando o reset")) : t(job?.progress?.waiting === "gpu" ? "Roteiro aprovado. Espera a GPU liberar" : job?.progress?.waiting === "prep" ? "Na fila para visuais/montagem" : job?.progress?.waiting === "claude" ? "Aguardando vaga no Claude" : job?.progress?.waiting === "episode" ? "Espera o roteiro do Ep. {n}" : "Aguardando a vez", { n: job?.progress?.after_episode ?? "" })}
            {job?.queue_position ? t(" · posição {n}", { n: job.queue_position }) : ""}
            {job?.eta_s != null ? t(" · começa em ~{d}", { d: fmtDuration(job.eta_s) }) : ""}. {t("Começa sozinho quando chegar a vez.")}
          </p>
        </Panel>
      )}

      {phase === "writing" && job && <ClaudePanel job={job} title={t("O Claude está escrevendo o roteiro")} text={t("Costuma levar 1 a 5 minutos. Avisamos em Sua vez quando ficar pronto.")} />}

      {phase === "approve" && (
        <ScriptReview
          projectId={data.id}
          languages={data.languages}
          scripts={data.scripts}
          onSaved={load}
          onApprove={approve}
          onRewrite={(note) => transition("scripting", note || undefined).then((ok) => ok && setToast({ msg: t("Pedido de nova versão enviado ao Claude.") }))}
          approveLabel={nextApprove ? t("Aprovar e ir para o próximo") : t("Aprovar roteiro")}
        />
      )}

      {phase === "producing" && job && <ProductionPanel job={job} />}

      {phase === "seo" && job && <ClaudePanel job={job} title={t("Títulos e tags")} text={t("Vídeo pronto. O Claude está gerando título, descrição e hashtags (1–5 min). Começou sozinho, não precisa clicar.")} />}

      {phase === "review" && (
        <>
          {flagged.map(([l, q]) => (
            <div key={l} className="rounded-xl bg-[#fff4e0] px-4 py-3 text-sm text-warn-text">
              <strong>{t("Voz {lang}:", { lang: l })}</strong> {t("{n} de {total} frases ainda soam diferentes do roteiro. Ouça com atenção.", { n: q.flagged.length, total: q.sentences })}
              <ul className="mt-1 mb-0 pl-5">{q.flagged.map((f, i) => <li key={i}>{t("Cena {n}", { n: f.scene })}: “{f.text}” → {t("ouvido:")} “{f.heard}”</li>)}</ul>
            </div>
          ))}
          <ReviewPanel
            projectId={data.id}
            channelId={data.channel_id}
            onScheduled={scheduled}
            onSwapImages={() => timeline.current?.scrollIntoView({ behavior: "smooth" })}
            scheduleLabel={nextReview ? t("Aprovar e ir para o próximo") : t("Aprovar e agendar")}
          />
        </>
      )}

      {(phase === "scheduled" || phase === "manual" || phase === "published") && (
        <Panel title={t(phase === "published" ? "Publicado" : phase === "manual" ? "Hora de publicar" : "Agendado")}>
          <p className="m-0 -mt-3 text-base text-muted">
            {t(phase === "published" ? "Pronto! As métricas são atualizadas sozinhas." : phase === "manual" ? "O kit está pronto: poste e cole o link." : "Na hora marcada ele aparece em Publicar com o kit pronto.")}
          </p>
          {phase !== "published" && (
            <div className="flex flex-wrap gap-2.5">
              <Link href="/fabrica/publicar" className="btn">{t("Abrir Publicar")}</Link>
              <button className="btn" onClick={() => confirm(t("Refazer narração, visuais e vídeos? O kit de publicação atual é descartado.")) && transition("voicing", "refazer voz e vídeo")}>{t("Refazer narração e vídeo")}</button>
              <button className="btn" onClick={() => confirm(t("Remontar o vídeo? O kit de publicação atual é descartado.")) && transition("composing", "remontar")}>{t("Remontar vídeos (após trocar imagens)")}</button>
            </div>
          )}
        </Panel>
      )}

      <UserMedia
        projectId={data.id}
        mode={data.media_mode ?? "mix"}
        onMode={setMediaMode}
        cut={data.media_cut ?? "full"}
        onCut={setMediaCut}
        canScript={!job || ["trend_selected", "scripting"].includes(job.state)}
        onRewrite={job && ["awaiting_approval", "awaiting_publish_review"].includes(job.state)
          ? () => confirm(t("Pedir ao Claude um roteiro novo que siga o que seus arquivos mostram?")) &&
            transition("scripting", "Reescreva o roteiro a partir dos arquivos do usuário (user_media): olhe as prévias e faça a narração de cada cena descrever o que aquele arquivo mostra naquele momento. Se houver avatar pronto, mantenha o avatar no gancho e no CTA.")
          : undefined}
      />

      {showTimeline && (
        <div ref={timeline} className="flex flex-col gap-4">
          <SceneTimeline projectId={data.id} scenes={latestScene?.content.scenes ?? []} editable={phase === "review"} refreshKey={job?.state} />
          {phase === "review" && (
            <div className="flex flex-wrap gap-2.5">
              <button className="btn" onClick={() => transition("composing", "remontar")}>{t("Remontar vídeos (após trocar imagens)")}</button>
              <button className="btn" onClick={() => confirm(t("Refazer narração, visuais e vídeos com o avatar/voz escolhidos acima?")) && transition("voicing", "refazer voz e vídeo")}>{t("Refazer narração e vídeo")}</button>
              <button
                className="btn"
                onClick={() => {
                  const note = prompt(t("O que mudar no roteiro?"), data.avatar_id ? "Inclua cenas com o avatar (avatar_full) no gancho e no CTA." : "");
                  if (note !== null) transition("scripting", note);
                }}
              >
                {t("Pedir novo roteiro")}
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
