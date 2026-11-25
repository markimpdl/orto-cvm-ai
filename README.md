# Orto CVM

Fábrica de vídeos com IA que roda no seu computador. Você escolhe um assunto, o app escreve o roteiro, grava a narração, monta as imagens e entrega o vídeo pronto para o YouTube, Shorts, Reels e TikTok. Você aprova o roteiro e o vídeo antes de qualquer publicação.

## O que ele faz

1. Ideias: junta assuntos em alta (YouTube, Google, notícias, Reddit) e dá uma nota de potencial para cada um. Também sugere ideias para o nicho de cada canal.
2. Roteiro: o Claude, pela sua assinatura do Claude Code, escreve o roteiro com gancho, promessa e blocos, e um revisor confere fatos e riscos. Você aprova ou pede ajustes.
3. Narração: voz clonada por IA local (VoxCPM2), conferida frase a frase por transcrição. Dá para treinar a sua própria voz no Estúdio de voz.
4. Imagens: vídeos e fotos de bancos gratuitos (Wikimedia, Openverse, Pexels e Pixabay com chave), imagens geradas por IA e checadas por um juiz visual, e cenas animadas.
5. Apresentador (opcional): um avatar que fala sincronizado com a narração. O avatar Lucas já vem pronto.
6. Vídeos por plataforma: 16:9 para o YouTube e 9:16 para Shorts, Reels e TikTok, com legendas, capítulos, thumbnail e SEO.
7. Conferência pela IA: antes de o vídeo chegar até você, a IA confere o áudio, as imagens, a estrutura e se o vídeo cumpre o que o título promete.
8. Publicação: um kit para publicar à mão (sempre disponível) ou envio pelas APIs oficiais. A agenda segue os horários de cada canal.

Os modelos são abertos e rodam na sua máquina, sem API paga. Para os roteiros, o app chama o Claude Code (`claude -p`) com a sua assinatura e não usa chave de API.

## Do que você precisa

- Mac com chip Apple (M1 ou mais novo) e pelo menos 32 GB de memória, o mínimo para o básico: voz, legendas e imagens de banco.
- 48 GB de memória para o apresentador falando, cenas animadas e séries infantis.
- Espaço em disco: uns 15 GB para o básico, mais 20 GB de folga para os vídeos. Imagens por IA e animação pedem mais. O assistente de instalação mostra a conta para a sua máquina.
- O [Claude Code](https://claude.com/claude-code) com login feito na sua assinatura, para os roteiros.
- Windows com placa NVIDIA: em breve.

## Instalar: dois cliques em INSTALAR.command

1. Baixe ou clone este repositório.
2. Dê dois cliques em `INSTALAR.command`. Se o macOS bloquear por ser um arquivo da internet, clique com o botão direito, escolha Abrir e confirme.
3. Se faltar o Homebrew, o instalador mostra a linha para colar no Terminal (ela pede a sua senha, por isso fica com você). Depois, rode `INSTALAR.command` de novo.
4. O instalador coloca o que faltar (`uv`, Node, `ffmpeg-full`, `poppler`), liga a API, a fila e a interface e abre o assistente em http://localhost:3010/instalar. Lá você escolhe os recursos, baixa os modelos e cria o primeiro canal.

Pode rodar de novo quando quiser. Com tudo instalado, ele só liga o app e abre o navegador.

## Configuração

Copie `.env.example` para `.env`. Tudo é opcional: sem chaves, o app usa só as fontes que não pedem cadastro. As chaves do YouTube, Pexels e Pixabay são gratuitas.

## Para quem desenvolve

```bash
cd backend && uv sync
uv run pytest                 # testes
uv run cvm-api                # API em http://127.0.0.1:8790
uv run cvm-worker             # fila
cd ../frontend && npm install && npm run dev   # interface em http://localhost:3010
```

O `CLAUDE.md` descreve a arquitetura, o pipeline e a fila.

## Licença

[PolyForm Noncommercial 1.0.0](LICENSE). Você pode usar, estudar, modificar e compartilhar o app para fins não comerciais, como uso pessoal, estudo, pesquisa, escolas e entidades sem fins lucrativos. Vender o app ou usá-lo para fins comerciais não é permitido. O texto completo está em `LICENSE`.

Os vídeos que você faz com o app são seus. Os modelos baixados pelo instalador têm licenças próprias; confira cada uma antes de usar um modelo em projeto comercial.

## Créditos

Criado por Marcos Ortolani.

Usa modelos e ferramentas abertos, entre eles VoxCPM2, Whisper, CLIP, LTX-Video, FLUX, Z-Image, SeedVR2, Gemma, Wan, Robust Video Matting, ACE-Step e FFmpeg, cada um com a sua licença.

## English

Orto CVM is an AI video factory that runs on your own Mac. Pick a topic and it writes the script (with Claude, through your Claude Code subscription), narrates it with a local cloned voice, gathers or generates the visuals and renders versions for YouTube, Shorts, Reels and TikTok. You approve the script and the final video before anything is published. No paid APIs.

Requirements: an Apple Silicon Mac (M1 or newer) with at least 32 GB of RAM (48 GB for the talking presenter and animated scenes), and Claude Code signed in. Windows with an NVIDIA GPU is coming.

To install, double-click `INSTALAR.command`. It installs whatever is missing and opens the setup wizard at http://localhost:3010/instalar. The interface is available in Portuguese and English.

License: [PolyForm Noncommercial 1.0.0](LICENSE). You may use, change and share it for noncommercial purposes. Selling it is not allowed.

### Credits

Made by Marcos Ortolani.
