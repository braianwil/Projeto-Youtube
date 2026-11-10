---
name: clipador
description: Regras de edição e de segurança do canal de cortes (GTA 6). Use ao mexer em cortes/ (cortador.py, aovivo.py, edicao.py, app) ou ao planejar/publicar cortes, para manter os vídeos transformados, creditados e monetizáveis.
---

# Clipador — regras do canal de cortes

Objetivo: cortes **transformados de verdade**, com crédito ao criador, para passar na política de
"conteúdo reutilizado" e evitar strike. Tudo local e sem IA paga (ver CLAUDE.md).

## 1. Edição dinâmica e estrutural (implementado)
- **Sem partes mortas:** `edicao.cortar_silencios` remove pausas (> 0,45s, relativo ao volume do clipe),
  sem baixar da duração mínima da plataforma. Desligar: `--sem-cortar-silencio`.
- **Zoom na ênfase:** `edicao.momentos_enfase` + `filtro_zoom` aplicam zoom de ~12% por ~1,4s nos picos
  (grito, risada, punchline). Desligar: `--sem-zoom`.
- Não entregar trecho contínuo bruto: sempre corte de pausas + zoom + legenda + gancho.

## 2. Narrativa e contexto próprios
- **Gancho** nos 3s iniciais (frase do pico ou texto do usuário, `--gancho`).
- **Pergunta final** nos 3s finais (`--pergunta`), para fechar o vídeo e puxar comentários.
- **Voz própria (manual):** recomendar ao usuário gravar uma intro de 3–5s contextualizando
  ("Olha o que o @X fez quando…") e comentários rápidos. É o que mais pesa para a plataforma
  ver o vídeo como análise e não como duplicação. (Futuro: campo no app para anexar a narração.)

## 3. Legenda e elementos visuais
- Legenda palavra por palavra, palavra falada em **amarelo**, até ~18 letras por bloco,
  **sem** animação de tamanho (o usuário não gostou), nunca duas legendas ao mesmo tempo.
- Imagens/manchetes/memes ilustrando o assunto: ainda manual (não buscar imagens automaticamente).

## 4. Áudio
- Música de fundo só como **estilo**, com faixa livre de direitos fornecida pelo usuário, em volume baixo.
- **NÃO implementar** mudança de pitch, EQ ou música com o objetivo de enganar Content ID/fingerprint,
  nem picotar o áudio para "escapar da detecção". Isso é burlar o sistema de direitos autorais e
  leva a **encerramento do canal**. Se o usuário pedir, explicar e oferecer o caminho legítimo
  (permissão do criador + transformação real).

## 5. Criadores e créditos
- Cortar só quem libera cortes: lista em `cortes/permissoes.txt`; o cortador avisa se o canal não estiver lá.
- Crédito `@canal` na tela nos 5s iniciais e 5s finais (`--credito`, `--credito-seg`) e arquivo `corte_XX.txt` com título, créditos,
  link do vídeo completo e hashtags, pronto para colar na descrição.

## Duração por plataforma (`--plataforma`)
- Shorts 30–59s (acima de 1 min com reivindicação = bloqueio) · TikTok 61–90s (Recompensas exige > 1 min)
  · Reels 30–90s. O corte de pausas respeita o mínimo.

## Notas
- `nota_global` (0–100, absoluta, `cortador.nota_global`): comparável entre vídeos e canais; é a nota a
  mostrar em destaque no app. `nota` é relativa ao próprio vídeo e só serve para ordenar dentro dele.
