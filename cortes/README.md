# Cortador automático – GTA 6

Você passa o link do perfil e o script "assiste" às lives e aos vídeos do canal. Ele encontra os momentos de **maior engajamento** e gera cortes prontos para postar (16:9 ou 9:16 para Shorts, TikTok e Reels).

## Como ele decide o que é um bom corte

| Sinal | Peso | O que mede |
|---|---|---|
| **"Mais repetidos" do YouTube** (heatmap) | 50% | Trechos que o público mais reassistiu. É o melhor indicador de audiência. |
| **Chat da live** (replay) | 35% | Explosões de mensagens. "KKKK", "CLIP", "MDS", 🔥, 😂 e superchat pesam mais. |
| **Volume do áudio** | 15% | Gritos e reações do streamer acima do volume normal. |

Quando um sinal não existe (vídeo novo sem heatmap, Twitch sem chat), os pesos dos outros sinais são redistribuídos. O pico do momento fica a cerca de 65% do corte, então há contexto antes e reação depois.

Para economizar internet, ele baixa **só o áudio e o chat** para analisar e, depois, **só os trechos escolhidos** em vídeo. Uma live de 4h não é baixada inteira.

## Instalação (no seu PC)

1. Instale o Python 3.10 ou mais novo e o [ffmpeg](https://ffmpeg.org/download.html). No Windows: `winget install ffmpeg`.
2. Instale as dependências:
   ```bash
   cd cortes
   pip install -r requirements.txt
   # opcional, para legenda automática estilo Shorts:
   pip install faster-whisper
   ```

## Uso

```bash
# 3 vídeos/lives mais recentes do canal com "GTA" no título, 5 cortes verticais de 45s cada
python cortador.py "https://www.youtube.com/@CANAL" --filtro "gta" --vertical

# com legenda, 8 cortes de 60s
python cortador.py "https://www.youtube.com/@CANAL" --filtro "gta ?(6|vi)" --cortes 8 --duracao 60 --vertical --legenda

# um vídeo ou live específico
python cortador.py "https://www.youtube.com/watch?v=XXXX" --vertical

# Twitch (VODs)
python cortador.py "https://www.twitch.tv/CANAL/videos" --max-videos 2

# vídeo que você já tem no PC
python cortador.py --arquivo live.mp4 --chat live.live_chat.json
```

Os resultados ficam em `saida/<id> <título>/`:
- `corte_01.mp4`, `corte_02.mp4`… em ordem do melhor para o pior
- `relatorio.json` com a nota, o horário e o link direto para cada momento no vídeo original

O arquivo `saida/processados.json` guarda o que já foi cortado. Se você rodar o comando de novo (por exemplo, todo dia pelo Agendador de Tarefas do Windows ou pelo `cron`), ele só processa as lives novas. Lives que ainda estão ao vivo são puladas até virarem VOD.

### Opções úteis

| Opção | Padrão | Descrição |
|---|---|---|
| `--cortes N` | 5 | Cortes por vídeo |
| `--duracao S` | 45 | Segundos por corte (use até 60 para Shorts) |
| `--max-videos N` | 3 | Quantos vídeos novos processar por execução |
| `--atraso-chat S` | 6 | Quanto tempo o chat leva para reagir ao momento |
| `--ignorar-inicio S` | 60 | Pula a tela de "começando já já" |
| `--reprocessar` | – | Corta de novo vídeos que já estão no histórico |

## Dicas para gerar audiência

- Assista aos 2 ou 3 primeiros cortes antes de postar. A nota alta indica que o momento teve reação, mas o gancho do título é você quem escolhe.
- Use o `link_momento` do relatório para conferir rapidamente o contexto no vídeo original.
- **Direitos autorais:** cortar a live de outro criador sem autorização pode gerar strike ou reivindicação. O ideal é ter a permissão do streamer (muitos têm programa oficial de cortes) ou usar o seu próprio conteúdo.
