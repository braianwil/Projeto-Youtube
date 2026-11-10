# Cortador automático – GTA 6

Você passa o link do perfil no **YouTube, Twitch ou Kick** e o script "assiste" às lives e aos vídeos do canal. Ele encontra os momentos de **maior engajamento** e gera cortes prontos para postar (16:9 ou 9:16 para Shorts, TikTok e Reels).

## Como ele decide o que é um bom corte

| Sinal | Peso | O que mede |
|---|---|---|
| **"Mais repetidos" do YouTube** (heatmap) | 50% | Trechos que o público mais reassistiu. É o melhor indicador de audiência. |
| **Clipes da comunidade** (Twitch e Kick) | 45% | Momentos que os próprios espectadores clipparam, com peso pelas views. |
| **Chat da live** (YouTube, Twitch e Kick) | 35% | Explosões de mensagens. "KKKK", "CLIP", "MDS", 🔥, 😂 e superchat pesam mais. |
| **Volume do áudio** | 15% | Gritos e reações do streamer acima do volume normal. |

### O que cada plataforma oferece

| | YouTube | Twitch | Kick |
|---|---|---|---|
| Lista as lives do perfil | ✅ (aba Ao vivo + Vídeos) | ✅ (transmissões passadas) | ✅ (aba Vídeos) |
| Heatmap "Mais repetidos" | ✅ | – | – |
| Chat replay | ✅ | ✅ | ✅ (em picos muito fortes, algumas mensagens se perdem) |
| Clipes da comunidade | – | ✅ | ✅ (minuto estimado pelo horário do clipe) |
| Áudio | ✅ | ✅ | ✅ |

Quando um sinal não existe ou a plataforma bloqueia o acesso, os pesos dos outros sinais são redistribuídos e o corte continua. O pico do momento fica a cerca de 65% do corte, então há contexto antes e reação depois.

## Edição automática (100% local, sem custo de IA)

Inspirada no Autoclipper, mas sem usar nenhuma API paga. Tudo roda no seu PC.

- **Corte na pausa:** início e fim vão para o silêncio mais próximo (até 6s antes ou depois), então a frase não fica cortada.
- **Enquadramento 9:16 automático** (`--layout auto`):
  - **facecam:** webcam pequena num canto (live de jogo) → webcam em cima e jogo embaixo.
  - **rosto:** a pessoa ocupa a tela → corte vertical centrado no rosto.
  - **blur:** sem rosto estável → vídeo inteiro com fundo desfocado.
  - Rostos de NPC do GTA se mexem e são ignorados. Só a webcam parada conta.
- **Legenda animada** (`--legenda`): até 3 palavras por vez, com a palavra falada em amarelo e um "pulo" de destaque.
- **Gancho** (`--gancho auto`): a frase dita no momento do pico aparece em destaque nos 3 primeiros segundos. Você também pode escrever o seu, com `--gancho "ELE NÃO ESPERAVA ISSO"`, ou desligar com `--gancho nenhum`.
- **Nota de 0 a 100** em cada corte, mais a fala transcrita no `relatorio.json`, para ajudar você a escrever o título.

A transcrição usa o faster-whisper e só roda nos trechos escolhidos, nunca na live inteira. O modelo é escolhido com `--modelo-whisper` (padrão: `small`). O `tiny` é bem mais rápido, e o `medium` é mais preciso.

Para economizar internet, ele baixa **só o áudio e o chat** para analisar e, depois, **só os trechos escolhidos** em vídeo. Uma live de 4h não é baixada inteira.

## Instalação (no seu PC)

1. Instale o Python 3.10 ou mais novo e o [ffmpeg](https://ffmpeg.org/download.html). No Windows: `winget install ffmpeg`.
2. Instale as dependências:
   ```bash
   cd cortes
   pip install -r requirements.txt
   ```

## Uso

```bash
# 3 vídeos/lives mais recentes do canal com "GTA" no título, 5 cortes verticais de 45s cada
python cortador.py "https://www.youtube.com/@CANAL" --filtro "gta" --vertical

# o mais completo: legenda animada + gancho + layout webcam/jogo, 8 cortes de 60s
python cortador.py "https://www.youtube.com/@CANAL" --filtro "gta ?(6|vi)" --cortes 8 --duracao 60 --vertical --legenda

# um vídeo ou live específico
python cortador.py "https://www.youtube.com/watch?v=XXXX" --vertical

# Twitch: o link do perfil já basta (pega as transmissões passadas)
python cortador.py "https://www.twitch.tv/CANAL" --filtro "gta" --vertical

# Kick
python cortador.py "https://kick.com/CANAL" --filtro "gta" --vertical

# vídeo que você já tem no PC
python cortador.py --arquivo live.mp4 --chat live.live_chat.json
```

Os resultados ficam em `saida/<data>/<canal>/<id> <título>/`, por exemplo `saida/2026-11-10/DavyJonesGTA6/AmlLv3M5ggE ROCKSTAR REVEALS…/`:
- `corte_01.mp4`, `corte_02.mp4`… em ordem do melhor para o pior
- `relatorio.json` com a nota, o horário e o link direto para cada momento no vídeo original

O arquivo `saida/processados.json` guarda o que já foi cortado. Se você rodar o comando de novo (por exemplo, todo dia pelo Agendador de Tarefas do Windows ou pelo `cron`), ele só processa as lives novas. Lives que ainda estão ao vivo são puladas até virarem VOD.

### Opções úteis

| Opção | Padrão | Descrição |
|---|---|---|
| `--plataforma` | shorts | Define a duração: `shorts` 30–59s, `tiktok` 61–90s, `reels` 30–90s |
| `--cortes N` | 5 | Cortes por vídeo |
| `--duracao S` | – | Duração alvo manual (sobrepõe a da plataforma) |
| `--max-videos N` | 3 | Quantos vídeos novos processar por execução |
| `--atraso-chat S` | 6 | Quanto tempo o chat leva para reagir ao momento |
| `--ignorar-inicio S` | 60 | Pula a tela de "começando já já" |
| `--reprocessar` | – | Corta de novo vídeos que já estão no histórico |

### Duração por plataforma

| Plataforma | Duração | Motivo |
|---|---|---|
| YouTube Shorts | 30–59s | Acima de 1 minuto, um Short com reivindicação de direitos autorais fica bloqueado. |
| TikTok | 61–90s | O Programa de Recompensas para Criadores só paga vídeos com mais de 1 minuto. |
| Instagram Reels | 30–90s | Faixa com melhor alcance. |

O início e o fim do corte sempre caem numa pausa da fala, dentro desses limites.

## Dicas para gerar audiência

- Assista aos 2 ou 3 primeiros cortes antes de postar. A nota alta indica que o momento teve reação, mas o gancho do título é você quem escolhe.
- A Twitch apaga os VODs depois de 7 a 60 dias, dependendo do canal. Rode o script com frequência para não perder lives.
- A Kick usa proteção Cloudflare. Por isso o `curl_cffi` faz parte das dependências: ele imita um navegador.
- Use o `link_momento` do relatório para conferir rapidamente o contexto no vídeo original.
- **Direitos autorais:** cortar a live de outro criador sem autorização pode gerar strike ou reivindicação. O ideal é ter a permissão do streamer (muitos têm programa oficial de cortes) ou usar o seu próprio conteúdo.
