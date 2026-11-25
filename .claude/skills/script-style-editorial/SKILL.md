---
name: script-style-editorial
description: Writing rules for editorial (news-desk) long videos — no presenter on screen; full-screen cards, real footage/photos and highlighted proof screenshots, each scene anchored to one word of the narration (`editorial.cenas`). Use when get_project says `editorial_long` for the script's language.
---
**When:** `get_project` → `editorial_long: true` and the script's language is in `editorial_languages` (YouTube 16:9,
presenter "editorial", pt-BR or en-US, styles explainer, top_list and dark). Then the script MUST carry
`editorial.cenas`; `save_script` refuses it without them or when they break the rules below (it lists each problem:
fix those and save again). Languages outside `editorial_languages` (ar-AE) and Short-only projects do NOT get
`editorial`; if you send it there, it is dropped. A project that renders both the YouTube video and a Short in the
same language has ONE script per language: it carries `editorial.cenas`, the long video uses them and the Short
ignores them (it keeps the vertical editorial header).

**What the viewer sees.** No presenter, no burned captions. The screen is a news desk that changes every 2–4 s:
full-screen cards (question, impact line, number, list), real footage and photos of the subject, and screenshots of
the sources with a highlighter painted over the exact sentence at the moment the narration says it. A section header
(kicker + title) stays on top and changes when a new section opens. Every frame with third-party material carries
the source in the footer. The narration carries the story; the cards carry the evidence.

## The narration is still `scenes`

Write the narration exactly as for any long video (agent rules: hook → promise → short bumper → blocks, spoken
subscribe ask at ~40 % and at the end, `ending`, nothing to show in the last 20 s). One `scene` per paragraph
(≤ 45 s), `intro_end` at the end of the promise, `blocks` as chapters. Each scene's `visual` is only a fallback:
`{type: "broll", search_terms: [...]}`. What the screen shows comes from `editorial.cenas`, a layer over the whole
narration.

Speak like a person explaining to a friend: short sentences, one idea per sentence, numbers spelled out the way the
voice reads them. Every strong claim names its source in the narration ("segundo a XDA…", "according to The
Verge…") and has a `prova` scene.

## `editorial.cenas`: the rules

- **Count and rhythm:** 55–75 scenes for ~5 min of narration (about one every 4–5 s on average, median 2–4 s). A
  card scene lasts at most ~6 s; only `video`/`foto` may sit over a longer passage. At least 8 scenes.
- **Anchor `em`:** ONE word copied from the narration (same spelling; punctuation ignored, "6" = "seis" = "six").
  The scene starts when that word is spoken. Anchors go in narration order: each one is the FIRST occurrence of that
  word after the previous scene's anchor. A common word ("E", "O", "the") is fine only if it does not appear earlier
  between the previous anchor and the place you mean; when in doubt pick a rarer word of the same sentence.
- **Types alternate.** Never 3 scenes of the same `tipo` in a row. Mix cards with `video`/`foto`.
- **Real material ≥ 25 % of the scenes** (`video` or `foto`), spread through the video, always with `busca`.
- **One yellow word per text:** in `texto` and in the `secao` title, exactly one word (or a short name) between
  `*asterisks*`. Card text is UPPERCASE, in the video's language.
- **Sections:** open one (`secao: ["KICKER", "TITLE with *word*"]`) every 20–40 s, on the first scene of each
  `block` and at the subscribe ask. Kicker 1–2 words ("A PERGUNTA", "THE LEAK"); title ≤ 28 characters. Put
  `varre: true` on section openings that land on a card (a yellow panel sweeps the change).
- **Sound:** `som` "riser" on the first scene, "impacto" with `impacto` cards, "pop" with `numero`/`lista`,
  "whoosh" on a section change over footage. `flash: true` at most 2–3 times per video, on real turns.

### Scene types and their fields (text limits come from the card layout)

| tipo | fields | limits / use |
|---|---|---|
| `pergunta` | `texto`, `sub`? | the question or claim of the moment; texto ≤ 30 chars (hard cap 60), sub ≤ 45 |
| `impacto` | `texto`, `pequeno`? | a punch line ("AINDA *NÃO*."); texto ≤ 20 chars (hard cap 60), pequeno ≤ 25 above it |
| `numero` | `valor`, `rotulo`, `fonte` | one number; valor ≤ 9 chars (hard cap 12: "US$ 15 mi", "32,8 mil", "23/11"), rotulo ≤ 70 |
| `lista` | `itens: [[anchor, TEXT], …]`, `estilo`? | 1–5 items, each TEXT ≤ 26 chars; each item appears when its anchor word (one narration word, after the scene's `em`) is spoken. `estilo` "fluxo" = items in a row, "regras" = numbered rules |
| `prova` | `prova: {url, frase, destaque_em, traducao?, lang?}`, `fonte` | screenshot of the source with the highlighter on `frase` |
| `video` | `busca`, `mov`? , `fonte`? | real moving footage of the subject; `mov` in, out, punch, parado, pan-e, pan-d |
| `foto` | `busca`, `mov`? , `fonte`? | a real photo of the subject (people, places, products, documents) |

`fonte` is the footer: "Outlet · dd/mm/yyyy" (≤ 55 chars), in every `numero`, `prova` and third-party `video`/`foto`.

### Proofs (`prova`)

- A `prova` for every strong claim that comes from a source: numbers, dates, quotes, "X confirmed / denied".
- `url` MUST be one of the script's `sources` (save_script checks it). Use the article/page itself, never a
  piracy, warez, "free download" or file-hosting site, even when it is the topic.
- `frase` is the sentence copied EXACTLY from the page, in the page's language (the system searches the page for
  it to paint the highlight). Keep it short: the part that carries the claim.
- `destaque_em`: ONE narration word; the highlighter is painted when it is spoken.
- Page in another language than the video: set `lang` (e.g. "en", "pt") and `traducao`, a short free translation
  in the video's language, in quotes. Same language: leave both out.
- If the source has no quotable sentence, use a `numero` card with `fonte` instead.

### Real footage and photos (`busca`)

- `termos`: 1–3 concrete searches IN ENGLISH, whatever the video's language: the subject + what is seen
  ("GTA 6 trailer 2 Vice City beach", "Dubai Metro red line train", "Apple Vision Pro hands-on"). Never abstract
  words ("technology", "success"); name the thing.
- `preferir`: "oficial" (the subject owner's channel: trailers, launches), "noticia" (news footage/photos),
  "banco" (free stock banks), "meus" (the user's own files in `user_media`).
- `official_channels`: when someone owns the subject, the exact YouTube channel name(s) (["Rockstar Games"],
  ["NASA"], ["Apple"]). The channel's switches decide whether those clips may be used.
- Never ask for AI images, leaked or pirated footage, or other creators' reactions. Flags: name the country in the
  text and let the system pick the official flag; never describe a flag in `termos`.
- When nothing real is found, the system turns the scene into a card with the narration text: write `termos` that
  can really be found.

## Worked example (first 8 scenes of an approved pt-BR video; the same rules apply in en-US)

Narration of the opening paragraph:
"No último vídeo eu disse que não dava pra jogar GTA seis no PC. Continua não dando. Mas em vinte e quatro horas
muita coisa mudou. O executável vazou, um projeto passou de trinta mil estrelas, outro diz que chegou no menu, e eu
errei uma coisa no vídeo passado. Eu sou dev. Bora separar de novo o que é código, o que é hype e o que é golpe.
Primeiro, a correção. Eu falei que o Leonida não abria porque faltava a parte que desenha a imagem. Na verdade, o
que faltava era o próprio executável do jogo. Sem ele, não tinha nem o que rodar."

```json
{"cenas": [
 {"em": "No", "tipo": "video", "mov": "in", "som": "riser", "secao": ["A PERGUNTA", "GTA 6 NO *PC*: E AGORA?"],
  "busca": {"termos": ["GTA 6 trailer 1"], "preferir": "oficial", "official_channels": ["Rockstar Games"]}},
 {"em": "Continua", "tipo": "impacto", "texto": "AINDA *NÃO*.", "som": "impacto"},
 {"em": "Mas", "tipo": "video", "mov": "in", "som": "whoosh", "secao": ["24 HORAS", "MUITA COISA *MUDOU*"],
  "busca": {"termos": ["GTA 6 trailer 2"], "preferir": "oficial", "official_channels": ["Rockstar Games"]}},
 {"em": "O", "tipo": "lista", "estilo": "fluxo", "som": "pop",
  "itens": [["executável", "EXE VAZOU"], ["projeto", "30 MIL ESTRELAS"], ["outro", "\"CHEGOU NO MENU\""], ["errei", "EU ERREI"]]},
 {"em": "sou", "tipo": "lista", "estilo": "fluxo", "som": "pop", "secao": ["QUEM FALA", "UM *DEV* CONFERE"],
  "itens": [["código,", "CÓDIGO"], ["hype", "HYPE"], ["golpe.", "GOLPE"]]},
 {"em": "Primeiro,", "tipo": "pergunta", "texto": "EU *ERREI*", "sub": "uma correção do vídeo passado",
  "secao": ["A CORREÇÃO", "O QUE *FALTAVA*"], "varre": true},
 {"em": "falei", "tipo": "pergunta", "texto": "\"FALTAVA A *IMAGEM*\"", "sub": "o que eu disse no vídeo passado"},
 {"em": "Na", "tipo": "impacto", "texto": "FALTAVA O *EXECUTÁVEL*", "pequeno": "NA VERDADE", "som": "impacto"}
]}
```

A proof from later in the same video (English page, Portuguese video; URL and sentence shown as a pattern, take
yours from the real page):

```json
{"em": "soltou", "tipo": "prova", "fonte": "XDA Developers · 23/11/2026",
 "prova": {"url": "<the article URL, also listed in sources>", "frase": "<the exact sentence on the page>",
           "destaque_em": "graça.", "lang": "en", "traducao": "\"o exe saiu. Sempre de graça.\""}}
```

Why it works: the first word of the video already shows the subject; the answer lands as an impact card one
sentence later; the list items pop on the words that name them; each new section opens with a kicker and a yellow
word; footage and cards alternate.

## Before saving, check

- Every `em`, item anchor and `destaque_em` is one word that is really in the narration, in order.
- No 3 identical `tipo` in a row; `video` + `foto` ≥ 25 %; a section every 20–40 s.
- Every strong claim has a `prova` whose `url` is in `sources`; `traducao` + `lang` when the page's language differs.
- Each card text fits its limit and has exactly one `*yellow*` word.
- The last scene is the comment question ("SUA VEZ" / "YOUR TURN"); nothing is planned for the 20 s after it.
- The subscribe ask at the end is spoken in the narration just before that last card and gets no card of its own;
  the ask at ~40 % opens its own section (e.g. "SE INSCREVE" / "SUBSCRIBE").
