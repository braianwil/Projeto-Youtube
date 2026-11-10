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

## Regra de busca: vídeos MAIS RECENTES e MAIS PROMISSORES
Sempre que buscar vídeos sobre um tema (GTA 6 por padrão), priorizar:
1. **Recentes:** busca por data (`ytsearchdate`); assunto quente rende mais views no corte.
2. **Promissores** (`cortador.ranquear_promissores`). Busca = recentes (`ytsearchdate`) + relevantes
   (`ytsearch`); **sem limite de idade** por padrão (`--max-idade-horas` opcional). Pontuação:
   - **alcance com idade:** views ÷ horas^0,7 → quanto mais antigo, mais views precisa ter;
   - **crescimento gradativo:** cada busca salva views em `saida/views_historico.json`; na seguinte,
     "↑ acelerando" ganha e "↓ esfriando" perde pontos (rodar a busca 2–3x por dia);
   - **joia escondida 💎:** vídeo pouco visto (< 100 mil) mas com engajamento alto (curtidas + 2×comentários
     ≥ 6% das views) ou com mais views que inscritos do canal → potencial que pode render no NOSSO canal.
   - lives e vídeos ≥ 8 min.
3. **Seguros:** só canais de `cortes/permissoes.txt`.
Depois de cortar, destacar os cortes com maior `nota_global` como os primeiros a postar.

## Comando "buscar vídeos no youtube"
Quando o usuário pedir para **buscar vídeos no YouTube** (ex.: sobre GTA 6) e cortar, a ação é
`cortador.py --buscar "<tema>"` (yt-dlp `ytsearchdate`, mais recentes primeiro).
- **Sessão rodando no PC do usuário** (Claude app / Remote Control na pasta Projeto-Youtube): EXECUTAR
  direto, sem pedir confirmação para a busca: `git pull` e depois o comando abaixo (sem o `cd`).
  Mostrar no fim só o resumo: vídeos cortados, nota_global de cada corte e a pasta de saída.
- **Sessão na nuvem** (sem acesso ao YouTube): entregar o comando pronto para o PowerShell:
```
cd $HOME\Documents\Projeto-Youtube; git pull
python cortes\cortador.py --buscar "GTA 6" --max-videos 3 --vertical --legenda
```
- Só corta canais de `cortes/permissoes.txt` (ignorados são listados). `--qualquer-canal` só se o usuário
  pedir, avisando do risco de strike.
- Ignora Shorts/vídeos < `--min-minutos` (8). Lives encontradas viram sugestão de `aovivo.py`.
- Histórico (`saida/processados.json`) evita cortar o mesmo vídeo de novo.

## Nichos (`--nicho`) — um cortador para vários canais
Pedido "buscar vídeos de <tema> no YouTube e cortar" → `cortador.py --buscar "<tema>"` (o tema vira o nicho:
pasta `saida/<nicho>/<data>/…`, hashtags e busca). Presets em `cortador.NICHOS`:
- `gta6` (padrão): criadores BR, legenda em PT.
- `gta6-gringo`: criadores em inglês → `--nicho gta6-gringo --buscar` (sem texto = "GTA 6 gameplay");
  transcreve em inglês e **traduz legenda + gancho para PT** (Argos Translate, local/grátis:
  `pip install argostranslate`; sem ele a legenda fica em inglês). Pula títulos em português.
- Qualquer outro tema (ex.: "filmes de terror") = nicho genérico, legenda PT.
Atenção por nicho: trechos de **filmes/séries/música de estúdio** geram Content ID/strike quase sempre —
cortar só criadores que COMENTAM (reviews, podcasts, reacts) e liberam cortes (`permissoes.txt`).
Exemplos para executar (sessão no PC do usuário):
```
python cortes\cortador.py --nicho gta6-gringo --buscar --max-videos 3 --vertical --legenda
python cortes\cortador.py --buscar "filmes de terror" --max-videos 3 --vertical --legenda
```
