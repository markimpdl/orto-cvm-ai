# Orto CVM

An AI video factory that runs on your own computer. You pick a topic, and the app writes the script, records the narration, puts the visuals together and hands you a video ready for YouTube, Shorts, Reels and TikTok. You approve the script and the finished video before anything is published.

## What it does

1. Ideas: collects trending topics (YouTube, Google, news, Reddit) and scores each one for potential. It also suggests ideas for each channel's niche.
2. Script: Claude, through your Claude Code subscription, writes the script with a hook, a promise and content blocks, and a reviewer checks facts and risks. You approve it or ask for changes.
3. Narration: a voice cloned by a local AI model (VoxCPM2), checked sentence by sentence against a transcription. You can train your own voice in the Voice studio.
4. Visuals: videos and photos from free libraries (Wikimedia, Openverse, and Pexels and Pixabay with a key), AI images checked by a visual judge, and animated scenes.
5. Presenter (optional): an avatar that talks in sync with the narration. The Lucas avatar comes ready to use.
6. Per-platform videos: 16:9 for YouTube and 9:16 for Shorts, Reels and TikTok, with captions, chapters, a thumbnail and SEO.
7. AI review: before a video reaches you, the AI checks the audio, the visuals, the structure and whether the video delivers what its title promises.
8. Publishing: a kit for publishing by hand (always available) or upload through the official APIs. The schedule follows each channel's posting times.

The models are open and run on your machine, with no paid API. For scripts, the app calls Claude Code (`claude -p`) with your subscription and never uses an API key.

## What you need

- A Mac with Apple silicon (M1 or newer) and at least 32 GB of memory. That covers the basics: voice, captions and library footage.
- 48 GB of memory for the talking presenter, animated scenes and kids series.
- Disk space: about 15 GB for the basics, plus 20 GB free for your videos. AI images and animation need more. The setup wizard shows the numbers for your machine.
- [Claude Code](https://claude.com/claude-code), signed in with your subscription, for the scripts.
- Or a Windows 11 PC with an NVIDIA card of 8 GB or more and 32 GB of memory (see below).

## Install: double-click INSTALAR.command

1. Download or clone this repository.
2. Double-click `INSTALAR.command`. If macOS blocks it because it came from the internet, right-click it, choose Open and confirm.
3. If Homebrew is missing, the installer shows the line to paste into Terminal (it asks for your password, so you run it yourself). Then run `INSTALAR.command` again.
4. The installer adds whatever is missing (`uv`, Node, `ffmpeg-full`, `poppler`), starts the API, the queue and the interface, and opens the setup wizard at http://localhost:3010/instalar. There you choose the features, download the models and create your first channel.

You can run it again at any time. Once everything is installed, it just starts the app and opens the browser.

The interface is available in English and Portuguese.

## Install on Windows: double-click INSTALAR.bat

You need Windows 11, an NVIDIA card with at least 8 GB of video memory (with its driver installed) and 32 GB of memory.

1. Download or clone this repository.
2. Double-click `INSTALAR.bat`. If Windows warns that the file came from the internet, choose More info and then Run anyway.
3. If `winget` is missing, install App Installer from the Microsoft Store and run `INSTALAR.bat` again.
4. The installer checks your card and memory first. Then it adds whatever is missing (`uv`, Node, FFmpeg, Poppler, Git), starts the API, the queue and the interface in their own windows, and opens the setup wizard at http://localhost:3010/instalar.

On Windows you get the basics: voice, captions, library footage, and AI images when the card has enough memory. The talking presenter, animated scenes and kids series need a 24 GB card and are not available on Windows yet.

To stop the app, close the three CVM windows or run `INSTALAR.bat stop`.

## Updates

When a new version is tagged here, the app shows an Update button. It waits for the queue to reach a safe point, moves to the new version and restarts. If anything fails, it goes back to the version you had.

## Configuration

Copy `.env.example` to `.env`. Everything in it is optional: without keys, the app only uses sources that need no sign-up. The YouTube, Pexels and Pixabay keys are free.

## For developers

```bash
cd backend && uv sync
uv run pytest                 # tests
uv run cvm-api                # API at http://127.0.0.1:8790
uv run cvm-worker             # queue
cd ../frontend && npm install && npm run dev   # interface at http://localhost:3010
```

`CLAUDE.md` describes the architecture, the pipeline and the queue.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You may use, study, change and share the app for noncommercial purposes, such as personal use, study, research, schools and nonprofits. Selling the app or using it commercially is not allowed. The full text is in `LICENSE`.

The videos you make with the app are yours. The models the installer downloads have their own licenses; check each one before using a model in a commercial project.

## Credits

Made by Marcos Ortolani.

Built on open models and tools, including VoxCPM2, Whisper, CLIP, LTX-Video, FLUX, Z-Image, SeedVR2, Gemma, Wan, Robust Video Matting, ACE-Step and FFmpeg, each under its own license.

## Português

O Orto CVM é uma fábrica de vídeos com IA que roda no seu computador: escreve o roteiro (com o Claude, pela sua assinatura do Claude Code), narra com voz clonada local, junta ou gera as imagens e entrega versões para YouTube, Shorts, Reels e TikTok. Você aprova o roteiro e o vídeo antes de publicar. Para instalar, dê dois cliques em `INSTALAR.command` (Mac) ou `INSTALAR.bat` (Windows 11 com placa NVIDIA de 8 GB ou mais e 32 GB de memória). No Windows, por enquanto, só entra o básico. O apresentador falando, as cenas animadas e as séries infantis precisam de uma placa de 24 GB e ainda não estão disponíveis lá. A interface está em português e inglês.
