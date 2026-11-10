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

# Twitch: o link do perfil já basta (pega as transmissões passadas)
python cortador.py "https://www.twitch.tv/CANAL" --filtro "gta" --vertical

# Kick
python cortador.py "https://kick.com/CANAL" --filtro "gta" --vertical

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
- A Twitch apaga os VODs depois de 7 a 60 dias, dependendo do canal. Rode o script com frequência para não perder lives.
- A Kick usa proteção Cloudflare. Por isso o `curl_cffi` faz parte das dependências: ele imita um navegador.
- Use o `link_momento` do relatório para conferir rapidamente o contexto no vídeo original.
- **Direitos autorais:** cortar a live de outro criador sem autorização pode gerar strike ou reivindicação. O ideal é ter a permissão do streamer (muitos têm programa oficial de cortes) ou usar o seu próprio conteúdo.
