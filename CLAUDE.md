# Orto CVM

Local AI video factory (macOS on Apple Silicon; Windows/CUDA in progress): trends → viral score → scripts → approval →
cloned voice + talking avatar → visuals → per-platform renders → AI curation → scheduled publishing (manual kit, or
official APIs). Multi-channel, multi-avatar. Video languages come from `CVM_VIDEO_LANGUAGES` (default here: pt-BR,
en-US); the code also supports ar-AE (RTL captions, Arabic voices) when it is listed there.

## Hard constraints
- **No paid APIs or services by default.** Everything runs locally with open-source models, or uses free official
  APIs (YouTube Data API, Instagram Graph, TikTok Content Posting, Pexels, Pixabay).
- Optional paid integrations the user may already have: vidIQ (MCP) for research and title scores, ElevenLabs for
  voice/music/SFX (REST key in `.env`, never in the DB/logs/git). Automated steps always keep the free local
  fallback, so missing or spent credits never break the queue; show the cost before big generations.
- **Claude only through the user's subscription** (Claude Code CLI, `claude -p`). Never use `ANTHROPIC_API_KEY`;
  `cvm/scripting/claude_runner.py` strips it.
- Approvals (script, render) and publishing are human decisions. The `cvm` MCP refuses agent transitions other than
  handing work back (`awaiting_approval`, `awaiting_publish_review`, `failed`).
- Manual publishing must always remain available, whatever the channel's `publish_mode`.
- Heavy models are used only when fully cached (`cvm/models_registry.py`); never trigger multi-GB downloads from a job.
- Never fork the code per OS: branch on `sys.platform` / `models_registry.MLX` (MLX on the Mac, CUDA elsewhere);
  machine paths go in `.env`.

## Layout
- `backend/` — Python 3.12 (uv). FastAPI (`cvm.main`), SQLModel/SQLite (`cvm.models`), Huey queue (`cvm.workers`),
  job state machine (`cvm.pipeline`), trend collectors (`cvm.trends`), scoring (`cvm.scoring`), MCP server
  (`cvm.mcp_server`), install wizard (`cvm.setup`). ML stages: `voice/ avatar/ visuals/ render/ publish/`.
- `frontend/` — Next.js UI on http://localhost:3010 (talks to the API at :8790): `/fabrica` (queue, new video, your
  turn, publish, series, clips, history), `/em-alta` (trends + Claude ideas), `/canais`, `/avatares` (+ voice studio),
  `/configuracoes`, `/instalar` (setup wizard). Brand tokens in `app/globals.css`; state→step mapping in
  `lib/pipeline.ts`; UI strings in `lib/i18n.tsx` (pt-BR) + `lib/i18n-en.ts`.
- `.claude/agents/` — pipeline agents (scriptwriter, qa-reviewer, seo-optimizer, trend-analyst); `.claude/skills/` —
  per-style writing rules (`script-style-*`).
- `private/` (optional, absent in this copy) — extra per-agent/per-skill/per-channel rules appended when present
  (`cvm/private.py`).
- `render-templates/` — HTML cards for the editorial format; `assets/avatars/` — the ready-made avatar.
- `data/` (SQLite, avatars, channels, outputs) and `models/` (weights) are gitignored.

## Commands
```
cd backend
uv run pytest                        # tests
uv run cvm-api                       # API on 127.0.0.1:8790
uv run cvm-worker                    # queue worker (periodic trend collection, jobs)
uv run python -m cvm.trends.collect  # one-off trend collection
uv run python -m cvm.models_registry # model status / download <id>
cd ../frontend && npm run dev        # UI on :3010
```
`INSTALAR.command` (Mac, `scripts/install/mac.sh`) and `INSTALAR.bat` (Windows 11 + NVIDIA ≥ 8 GB,
`scripts/install/windows.ps1`: winget for uv/Node/FFmpeg/Poppler/Git, ffmpeg path into `.env`) install everything and
open the wizard; both take `restart` (used by Atualizar; Windows also `stop`). On Windows the talking presenter,
animation and kids series are not available yet (24 GB card, part 1B). `scripts/cvm-start.sh` / `cvm-stop.sh` (Mac)
and `INICIAR.bat` / `DESLIGAR.bat` (Windows) start and stop API, worker and UI; `scripts/install-launchd.sh` keeps them
running at login on the Mac.

## Settings (`.env`, prefix `CVM_`)
- `CVM_VIDEO_LANGUAGES` — languages offered everywhere (wizard, channels, new video, avatars, filters).
- `CVM_DEFAULT_TZ` — the studio's timezone shown beside the audience's in the calendar; empty = the system's.
- API keys (all optional, free): `CVM_YOUTUBE_API_KEY`, `CVM_PEXELS_API_KEY`, `CVM_PIXABAY_API_KEY`; Google OAuth
  client for YouTube upload/Analytics in `CVM_GOOGLE_CLIENT_ID/SECRET` or `data/secrets/youtube_client_secret.json`.
  Refresh tokens live in the system keyring, never in the DB/logs.

## Pipeline stages (`cvm/workers/tasks.py` handlers)
`trend_selected → scripting (Claude) → awaiting_approval (human) → voicing → sourcing_visuals → lipsyncing → composing
→ rendering (QA) → validating (AI curation) → seo (Claude) → awaiting_publish_review (human) → scheduled →
ready_for_manual_publish (human) | published`
- AI curation (`cvm/curation.py`): reads what the checkers already stored (voice QA, judge, flags, render QA, end
  screen, Shorts rules, structure) plus one light Claude fact/promise check (skipped, never blocking, when Claude is
  off/limited); a hard failure sends the job back to the earliest fixing step with a note (each check at most once,
  Setting `curation.max_attempts`, default 2); then `seo` with the report in `Render.meta.curation`. The user
  approves/rejects the whole video.
- Voice (`cvm/voice`): VoxCPM2 is the local voice (avatars, kids characters, narrators, trained LoRA); ElevenLabs per
  voice profile when configured; macOS `say` only for a voice without a reference. Narration is synthesised per
  sentence (`voice/text.py`: numbers/abbreviations spelled out, script `pronunciation` map, channel
  `prefs.pronunciation`) and checked by a whisper round trip (`voice/qa.py`), retrying bad takes with new seeds. Each
  avatar has a reference per language (`voice/references.py`); a take cloned across languages is sped to a natural
  pace (`narrate._natural_pace`).
- Own voice: `/avatares/estudio-voz` (`cvm/voice/studio.py`) records ~30 min sentence by sentence for a VoxCPM2 LoRA;
  `python -m cvm.voice.finetune` trains it on a CUDA PC and registers the voice "Voz real (treinada)".
- Visuals (`cvm/visuals`): Pexels/Pixabay with keys; Wikimedia Commons video+images and Openverse without keys; CLIP
  check; AI stills by Z-Image-Turbo on the Mac (FLUX schnell for kids series and on CUDA, `sources.image_engine`),
  checked by a Gemma 3 vision judge (`visuals/judge.py`: redraw → stock) and upscaled to 1080 by SeedVR2
  (`visuals/upscale.py`); up to `ai_video_max_clips` hero stills animated (LTX via `models/LTX2MLX`, Wan 2.2 via
  `models/MLXGen`).
- Animated clips are checked for holding their picture (`clip_rank.steadiness`, CLIP of the first frame vs the rest,
  `STEADY` 0.70, one-frame `FLASH` 0.55): a clip whose subject leaves the frame is made once more (other seed, file
  `…_b.mp4`), then the scene keeps its still. The vision judge does not see motion errors.
- Flags are NEVER drawn by AI (`cvm/visuals/flags.py`): a scene/thumbnail about a flag (or `visual.flag` = ISO-2) gets
  the official Wikimedia Commons flag (cached in `data/flags/`), never animated or redrawn; unknown country → real
  stock / video frame.
- Talking presenter = LTX audio-to-video (`cvm/avatar/talking.py`, audio chunks padded to the 8k+1 frame count, LTX
  stderr logged); a failed LTX take fails the step. LTX pack (`settings.ltx_pack`): LTX-2.5 when downloaded, else
  LTX-2.3; on 2.5 each presenter chunk also goes through the Refine-Details IC-LoRA (`settings.presenter_refine`,
  slower; per-project opt-out in Setting `presenter_refine_off`). The cut-out (RVM) runs in
  `models/RobustVideoMatting/.venv`.
- Composer: `cvm/render/compose.py` (needs `brew install ffmpeg-full`, path in `settings.ffmpeg_bin`). Music:
  `cvm/render/music.py` (local folders first, else free CC music from Openverse, cached per style, credited). Render QA
  (`cvm/render/qa.py`): black/frozen/silence/loudness analysis and a contact sheet. Render targets: keys of
  `cvm/render/profiles.yaml`, plus `<profile>_cuts` for splitting into several shorts.
- Vertical renders: the presenter stays small at the side (`compose.SHORT_PRESENTER`), only in the first scene and the
  script's avatar scenes; no full-screen avatar scene in Shorts. Style `live` + `Channel.prefs.facecam` stacks the
  camera above the game (`cvm/render/stack.py`).
- Cleanup (`cvm/cleanup.py`): deletes only video files ≥1 MB of renders published ≥ N days ago; keeps scripts, voices,
  thumbnails, SEO and metrics and writes `<project>/historico.json`.
- Worker log: `data/logs/worker.log`.

## Channels
- **One channel = one language = one YouTube channel.** `Channel.status` (active | paused | archived: only active ones
  get new videos, `cvm/channels.py` → 409), `group` (sibling channels in other languages), `profile` (persona,
  pillars, formats, `publish_slots` in the audience's timezone…); the agents read `channels.brief_text()`.
- Channel ideas: `POST /channels/{id}/ideas` (Claude, niche ideas) and `POST /channels/{id}/ideas/produce`.
- Calendar (`cvm/schedule.py`): week goal per format (`formats[].per_week`), week calendar and next free slots in the
  audience's timezone, studio time beside it (`config.home_tz()`).
- Group channels (`channel_ids` on `POST /projects`, `/projects/batch`, `ideas/produce`): one project per channel,
  topic + adaptation note (never a translation). `cvm/guards.py` checks at creation (closed channel/language; similar
  video in 30 days → 409 `{code:"similar"}` unless `force_similar`) and at scheduling (language = channel's, kids
  series only on made-for-kids channels).
- Kids series (`style=kids`, skill `script-style-kids`): ~5-min, fully animated episodes; characters speak with voices
  saved on the channel (`cvm/voice/characters.py`); the channel brief is the series bible. New series draw with FLUX.2
  Klein from one reference sheet per character (`cvm/visuals/cast.py`). Episode numbering never goes backwards:
  `channels.number_episode` titles every episode "<série> – Ep. N: …" (`prefs.last_episode` to continue a series made
  elsewhere) and `tasks.previous_episode_pending` holds an episode's script until Ep. N-1 has one.
- Own-material styles: `lesson` (slides from an uploaded PDF, one image per page, needs poppler) and `review`; a user
  video marked `keep_audio` plays its own sound in its scenes.
- Clips and growth (`cvm/lives/`, `cvm/grow/`): the user pastes a link (own video/live or another creator's, via
  yt-dlp: YouTube, Twitch, Kick, TikTok, Instagram) → one "melhores momentos" project + Shorts from the strongest
  moments (`lives/viral.py`). Another creator's material is style `react`, credited. Research runs that read
  third-party content never get write tools (`viral_search.DENY`). All this background work runs in the worker.
- YouTube Analytics per channel (`cvm/publish/analytics.py`) → `GET /channels/{id}/stats`; a weekly cycle
  (`cvm/publish/weekly.py`) turns the best videos into new ideas and a pending title pattern.

## Video structure (product rule)
Every long video: **hook → promise → short bumper (≤3 s) → content in blocks; spoken subscribe ask at ~40% and at the
end; last 20 s clean for the end screen**.
- Script (`cvm/scripting/schema.py`, rules in `.claude/agents/scriptwriter.md`): `hook` + `hook_type`, `promise`,
  `intro_end`, `blocks` (60–90 s, title = chapter), scenes ≤ 45 s, `cta_points` (none for kids), `ending`,
  `packaging` (title, thumbnail ≤ 3 words not repeating the title, 2 alternatives).
- Bumper (`cvm/render/bumper.py`): default `prefs.bumper_mode` "short" = channel logo + a short sound, ≤ 3 s, no
  words; "full" = the older spoken bumper; none in Shorts or without brand art.
- Compose: long YouTube renders get a clean tail (`Render.meta.end_screen_s`) and chapters from the blocks
  (`cvm/render/chapters.py`). Vertical renders: the 9:16 cover is the first 0.5 s, the strongest line big at the
  start, word-pop captions.
- Shorts are written as Shorts (own script, never a vertical cut of the long one): no subscribe ask, loop ending,
  plain titles, payoff in the first second.
- Thumbnails (`cvm/render/thumbnail.py`): AI background + presenter cut-out filling 40–60% of the frame, ≤ 3 words;
  kids thumbnails must pass the judge's `kids_thumbnail` rule.
- `pipeline.send_to_revision(job, note)` re-writes a queued script only when the user asks.

## Queue
- Claude steps (`scripting`, `seo`) start on their own (`settings.claude_auto_run`), at most `CLAUDE_SLOTS` (2) at
  once. Pipeline `claude -p` runs are isolated from the user's global setup (`claude_runner.isolation_args`: project
  settings and project MCP only); model per step: `claude_model` for scripts, `claude_model_light` for SEO/ideas.
- Narration and lipsync hold the heavy-model lock (`cvm/gpu.py`) for the whole step; visuals and composing take a
  "prep" slot (`PREP_SLOTS`) and borrow the lock only around big models (`gpu.section()`), so two big models never
  run together. Waiting jobs store `progress.waiting/since` and retry every `RETRY_S`; videos further along go first.
  `cvm/queueing.py` exposes position/ETA on `GET /projects`.
- Memory guard: on the Mac a fresh GPU-lock take waits until `settings.gpu_min_free_gb` of RAM is free. Never run heavy
  scratch work (whisper on long audio, vision/LLM) beside a GPU step: pause the queue or wrap it in `gpu.section()`.
- Claude usage limit (`cvm/claude_limit.py`): the reset time is parsed from the message; Claude steps wait with
  `progress.waiting = "claude_limit"`, the rest of the queue goes on, `claude_limit_tick` resumes them. Optional Codex
  fallback (`cvm/scripting/codex_runner.py`, ChatGPT subscription via `codex exec`, never an API key) while Claude is
  limited; off with `CVM_CODEX_FALLBACK=false`.
- Auto-approvals are opt-in (`auto_approve_script`, `auto_approve_render` in the "autopilot" setting). Autopilot
  (`cvm/autopilot.py`) limit is per channel and skips kids channels.
- Pause: `Job.paused` (POST /jobs/{id}/pause, /jobs/pause-all) stops a job at the next step boundary.
- The worker resumes interrupted jobs on start and flushes stale locks; never restart it mid-step without checking
  `GET /jobs`.
- Tests must never touch the live Huey storage (`data/queue`): patch `cvm.gpu.lock` with a `MemoryHuey` lock.

## Adding a pipeline stage
Register a handler in `cvm/workers/tasks.py` with `@handler("<state>")` returning the next state (or `None` to wait).
States that run a big model end to end go in `GPU_STATES`; mostly-IO states go in `PREP_STATES` and wrap any heavy
call in `gpu.section()`.
