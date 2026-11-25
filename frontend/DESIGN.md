---
name: Orto CVM
description: Painel local de uma fábrica de vídeos — fila, aprovações e publicação, em pt-BR (e inglês)
colors:
  paper: "#f5f5f2"
  surface: "#ffffff"
  ink: "#111214"
  ink-hover: "#2a2c30"
  body: "#3b3f45"
  muted: "#5b6068"
  line: "#e6e7ea"
  line-soft: "#eff0f2"
  field: "#c9ccd1"
  chip: "#f0f1f3"
  track: "#edeeeb"
  go: "#00843d"
  go-hover: "#006b31"
  go-soft: "#e3f2ea"
  go-tint: "#f2faf5"
  go-text: "#00602c"
  alert: "#d6001c"
  alert-dot: "#ff3b4e"
  alert-soft: "#fde7ea"
  alert-text: "#a10016"
  claude-soft: "#eee8fb"
  claude-text: "#4b2a99"
  warn-text: "#8a4b00"
typography:
  display:
    fontFamily: "Saira, system-ui, sans-serif"
    fontSize: "36px"
    fontWeight: 700
    lineHeight: 1.1
  headline:
    fontFamily: "Saira, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "Figtree, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Figtree, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
  overline:
    fontFamily: "Figtree, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    letterSpacing: "0.4px"
rounded:
  tag: "999px"
  control: "10px"
  field: "12px"
  card: "16px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  section: "28px"
components:
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 18px"
    height: "44px"
  button-hover:
    backgroundColor: "{colors.chip}"
  button-go:
    backgroundColor: "{colors.go}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    height: "44px"
  button-go-hover:
    backgroundColor: "{colors.go-hover}"
  button-ink:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    height: "44px"
  button-sm:
    padding: "0 12px"
    height: "40px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "0 14px"
    height: "44px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "20px"
  pill:
    backgroundColor: "{colors.chip}"
    textColor: "{colors.body}"
    typography: "{typography.label}"
    rounded: "{rounded.tag}"
    padding: "6px 12px"
  pill-ok:
    backgroundColor: "{colors.go-soft}"
    textColor: "{colors.go-text}"
  pill-alert:
    backgroundColor: "{colors.alert-soft}"
    textColor: "{colors.alert-text}"
  tag-claude:
    backgroundColor: "{colors.claude-soft}"
    textColor: "{colors.claude-text}"
    rounded: "{rounded.tag}"
    padding: "1px 6px"
  nav-item-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    height: "44px"
---

# Design System: Orto CVM

## Overview

**Creative North Star: "A Mesa de Produção"**

O Orto CVM é a mesa de controle de uma fábrica de vídeos que roda no próprio Mac: a pessoa entra para ver o que está na fila, aprovar o que é a vez dela e publicar. A interface é uma ferramenta de trabalho (modo Operate), lida em sessões curtas e frequentes. O papel quente-neutro (`paper`) com cartões brancos e tinta quase preta dá um ambiente calmo e de alto contraste; a cor só aparece quando carrega significado de estado.

O verde (`go`) é o sinal de "pode seguir": ação principal, item ativo, sucesso. O vermelho (`alert`) é reservado para erro e para contagens que pedem atenção. O roxo claro marca o que passa pelo Claude. Densidade média: alvos de toque de 44 px, texto de 15 px, cartões com 20 px de respiro, tudo numa coluna central de até 1200 px ao lado de uma barra lateral branca.

O texto da interface é pt-BR por padrão (com tradução para inglês em `lib/i18n-en.ts`), direto e no imperativo curto ("Produzir", "Aprovar", "Baixar thumbnail"), com a explicação em uma linha abaixo quando a ação não é óbvia.

**Key Characteristics:**
- Fundo papel neutro, superfícies brancas planas, bordas finas cinza-claras.
- Uma cor de ação (verde) e uma de alarme (vermelho); o resto é escala de cinzas.
- Duas famílias: Saira para títulos, Figtree para todo o resto.
- Controles grandes (44 px) e cantos suaves de 10–16 px.
- Estados ditos com palavras e pills, não só com cor.

## Colors

Escala de cinzas quente com um verde de ação e um vermelho de alarme; cores extras só para marcar origem (Claude) e aviso.

### Primary
- **Verde Sinal-Aberto** (`go`): botão principal (`.btn-go`), link, ponto do item ativo na navegação, anel de foco. Escurece para `go-hover` no hover. Fundos suaves `go-soft` (toasts, pills de "pronto") e `go-tint` (realces leves), com texto em `go-text`.

### Secondary
- **Vermelho Atenção** (`alert`): contagem de itens que pedem ação na barra lateral, erros. `alert-dot` é o ponto de status ao vivo; `alert-soft` + `alert-text` formam a pill de erro e o texto de botões perigosos (`.btn-danger`).

### Tertiary
- **Lilás Claude** (`claude-soft` + `claude-text`): a tag que diz que uma etapa é feita pelo Claude (roteiro, SEO). Nunca usada para ação.
- **Âmbar Aviso** (`warn-text`): texto de estados "pausado"/espera, sobre `chip`.

### Neutral
- **Papel** (`paper`): fundo da página e de blocos de status dentro de cartões.
- **Superfície** (`surface`): cartões, barra lateral, botões padrão, campos.
- **Tinta** (`ink`): títulos, texto forte, botão escuro (`.btn-ink`) e item ativo da navegação; `ink-hover` no hover.
- **Corpo** (`body`): texto corrido dentro de cartões. **Apagado** (`muted`): legendas, metadados, rótulos de seção.
- **Linha** (`line`) e **Linha suave** (`line-soft`): bordas de cartões e divisórias. **Campo** (`field`): borda de inputs e pontos inativos. **Chip** (`chip`): fundo de pills neutras e hover de botões. **Trilho** (`track`): fundo de barras de progresso.

### Named Rules
**A Regra do Sinal Verde.** O verde só aparece onde a pessoa pode seguir: a ação principal da tela, o item ativo, um sucesso. Se tudo é verde, nada é.

**A Regra do Vermelho Contado.** Vermelho é para erro ou para um número que pede ação agora. Nunca decorativo.

## Typography

**Display Font:** Saira (500–700, via `next/font`), fallback system-ui
**Body Font:** Figtree (400–600, via `next/font`), fallback system-ui

**Character:** Saira, técnica e um pouco condensada, dá aos títulos um ar de painel de controle; Figtree, redonda e legível, carrega todo o texto de trabalho.

### Hierarchy
- **Display** (Saira 700, 36px, 1.1): título da página (`.h1`), um por tela.
- **Headline** (Saira 600, 20px, 1.2): título de cartão ou seção (`.h2`).
- **Body** (Figtree 400, 15px): texto de botões, itens de navegação e parágrafos; 16px dentro de campos.
- **Label** (Figtree 600, 13px): pills, chips de etapa, metadados fortes; 13px regular em `muted` para legendas.
- **Overline** (Figtree 600, 12px, 0.4px, caixa alta): rótulo de grupo na barra lateral ("CANAIS").

### Named Rules
**A Regra das Duas Vozes.** Saira só em títulos (`.h1`, `.h2`); qualquer outro texto é Figtree.

## Layout

Barra lateral branca de 240 px à esquerda (vira bloco no topo em telas estreitas, via `flex-wrap`) e área principal com respiro lateral fluido (`clamp(16px, 3.5vw, 48px)`), 36 px no topo, e conteúdo centrado em até 1200 px. As seções de uma página empilham com 28 px entre si. Dentro de cartões o ritmo é 12–16 px entre blocos e 8 px entre itens de uma linha; grades de etapas e listas usam `auto-fit` com mínimo de 140 px por coluna. Tudo quebra linha antes de criar rolagem horizontal.

## Elevation & Depth

Plano por padrão: a profundidade vem do contraste papel → cartão branco → borda `line`, não de sombras. Sombra só aparece em casos pontuais (um toque de 1 px em miniaturas, e a sombra grande de um painel flutuante).

### Shadow Vocabulary
- **Fio** (`box-shadow: 0 1px 2px rgba(0,0,0,0.08)`): miniaturas e pequenos elementos sobre papel.
- **Flutuante** (`shadow-lg` do Tailwind): só em camadas que flutuam sobre o conteúdo.

### Named Rules
**A Regra do Plano.** Cartões não têm sombra; separação é borda de 1 px em `line` sobre o papel.

## Shapes

Cantos suaves e consistentes por tamanho: 10 px em botões e itens de navegação, 12 px em campos e blocos internos, 16 px em cartões, e pílula completa (999 px) em pills, tags e contadores. Bordas sempre de 1 px. Pontos de status são círculos de 8 px.

## Components

### Buttons
Grandes, claros e sem ornamento.
- **Shape:** cantos suaves (10px), altura mínima 44px (40px no `.btn-sm`), padding lateral 18px.
- **Padrão:** fundo branco, borda `#d9dbdf`, texto tinta 15px semibold; hover em `chip`.
- **Go (principal):** fundo e borda `go`, texto branco; hover `go-hover`. Um por área de decisão.
- **Ink:** fundo tinta, texto branco; seleção ativa (ex.: idioma escolhido) e ações secundárias fortes.
- **Danger:** botão padrão com texto `alert-text`.
- **Disabled:** 45% de opacidade, cursor padrão. **Focus:** anel verde de 3px com 2px de afastamento (global).

### Chips / Pills
- **Style:** pílula, 13px semibold, padding 6×12px; versão compacta 2×8px com 11px.
- **Estados:** neutra (`chip` + `body`/`muted`), pronta (`go-soft` + `go-text`), pausada (`chip` + `warn-text`), erro (`alert-soft` + `alert-text`), arquivada (`line-soft` + `muted`).
- **Tag Claude:** pílula mínima 11px em lilás, ao lado do nome da etapa.

### Cards / Containers
- **Corner Style:** 16px.
- **Background:** branco sobre papel; blocos de status internos em papel com 12px de canto.
- **Shadow Strategy:** nenhuma (A Regra do Plano).
- **Border:** 1px `line`.
- **Internal Padding:** 20px (24px em cartões de texto longo; 16×20px em faixas).

### Inputs / Fields
- **Style:** branco, borda 1px `field`, canto 12px, altura 44px, padding 14px, texto 16px.
- **Focus:** o anel verde global.

### Navigation
- **Barra lateral:** logo no topo; itens de 44px com canto 10px, 15px medium, um ponto de 8px (verde quando ativo, `field` quando não).
- **Ativo:** fundo tinta e texto branco. **Hover:** `#ecedea`.
- **Contador:** pílula de 22px à direita, `line` + tinta, ou `alert` + branco quando pede ação.
- **Menu principal:** cinco itens, nesta ordem: Fábrica, Em alta, Canais, Avatares, Configurações. "/" abre Fábrica > Produção. Abaixo do menu fica o botão "+ Novo vídeo" (`.btn`, não verde).
- **Subitens:** ficam sempre visíveis logo abaixo do item principal, recuados 18px, com um fio de 1px `line` à esquerda (no lugar do ponto). Altura mínima 40px, canto 10px, 14px medium. O subitem da página atual leva o fundo tinta; o item principal do grupo fica sem fundo, com o ponto verde e o texto em semibold. Só um elemento da barra tem fundo tinta por vez.
- **Contadores nos subitens:** Produção (vídeos andando, neutro), Sua vez (vermelho, pede ação) e Publicar (posts do dia, neutro). O resto não leva número.
- **Grupo "Canais":** os subitens são os canais, em duas linhas (nome + uma linha `muted` com a meta da semana), com "Mostrar arquivados" no fim.

### Abas dentro da página
Quando uma tela se divide em seções (Configurações, Clips e cortes, a página do canal), use `Tabs` de `components/Bits.tsx`: trilho `#e9eae6` com canto 12px, abas de 40px, a ativa em branco com o Fio. Abas não mudam a URL; o menu lateral não repete as abas.

### Etapas da produção (signature)
Linha do tempo do pipeline em chips: versão grande em grade (`auto-fit`, mínimo 140px) com nome da etapa em 15px e detalhe em 13px, e versão compacta em pílulas de 13px. A cor do chip diz o estado (feito, agora, esperando), e a tag Claude marca as etapas do Claude.

### Toast
Faixa `go-soft` com canto 12px, texto 15px semibold verde-escuro, um link de ação e botão de fechar à direita.

## Do's and Don'ts

### Do:
- **Do** usar `.btn-go` para a única ação principal de cada área, e `.btn` para o resto.
- **Do** dizer o estado em palavras numa pill (`pausado`, `pronto`, `erro`) além da cor.
- **Do** manter alvos de toque com 44px de altura mínima (40px só no `.btn-sm`).
- **Do** usar os tokens (`bg-surface`, `text-muted`, `border-line`, `bg-go`) em vez de hex soltos.
- **Do** passar todo texto novo por `t()` e acrescentar a tradução em `lib/i18n-en.ts`.

### Don't:
- **Don't** pôr sombra em cartões; a separação é a borda `line`.
- **Don't** usar verde ou vermelho como decoração.
- **Don't** usar Saira fora de títulos.
- **Don't** usar o lilás do Claude para outra coisa que não seja marcar etapas do Claude.
