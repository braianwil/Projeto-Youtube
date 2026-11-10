#!/usr/bin/env python3
"""
Cortador automático de lives/vídeos (feito pensando em GTA 6, mas serve pra qualquer jogo).

Fluxo:
  1. Lê o perfil/canal (YouTube, Twitch VOD, Kick... tudo que o yt-dlp suporta).
  2. Para cada vídeo/live novo: baixa só o ÁUDIO (leve) + chat replay + "mais repetidos".
  3. Calcula uma nota de engajamento por segundo combinando:
       - heatmap "Mais repetidos" do YouTube (o que o público mais reassistiu)
       - picos de mensagens no chat da live (+ peso extra para "KKKK", "CLIP", "MDS"...)
       - picos de volume/grito do streamer
  4. Escolhe os N melhores trechos sem sobreposição.
  5. Baixa só esses trechos em vídeo e gera os cortes (16:9 ou 9:16 para Shorts/TikTok),
     opcionalmente com legenda queimada (faster-whisper).

Uso rápido:
  python cortador.py "https://www.youtube.com/@canal" --filtro "gta" --vertical
  python cortador.py --arquivo live.mp4 --chat live.live_chat.json   (vídeo já baixado)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

PALAVRAS_HYPE = re.compile(
    r"(k{3,}|(rs){2,}|ha(ha)+|lol|lmao|clip|corta|cortem|mds|meu deus|caraca|caralho|"
    r"pqp|insano|absurdo|w+t+f+|omg|pog|kekw|lul|🔥|😂|🤣|💀|😱)",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------- util
def log(msg: str) -> None:
    print(msg, flush=True)


def ffmpeg(*args: str, cwd: Path | None = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True, cwd=cwd)


def hms(seg: float) -> str:
    seg = int(seg)
    return f"{seg // 3600:d}:{seg % 3600 // 60:02d}:{seg % 60:02d}"


def suavizar(x: np.ndarray, janela: int) -> np.ndarray:
    if janela <= 1 or len(x) == 0:
        return x
    k = np.ones(janela) / janela
    return np.convolve(x, k, mode="same")


def normalizar(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    ref = np.percentile(x, 99)
    if ref <= 0:
        ref = x.max()
    return np.clip(x / ref, 0, 1) if ref > 0 else np.zeros_like(x)


# ------------------------------------------------------------------------ sinais
def sinal_audio(arquivo: Path, duracao: int) -> np.ndarray:
    """Volume (dB) acima da mediana local -> detecta gritos/momentos de hype."""
    taxa = 8000
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(arquivo),
         "-vn", "-ac", "1", "-ar", str(taxa), "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
    )
    db = []
    bloco = taxa * 2  # 1 segundo de s16
    while True:
        dados = proc.stdout.read(bloco)
        if not dados:
            break
        amostras = np.frombuffer(dados, dtype=np.int16).astype(np.float32) / 32768
        rms = np.sqrt(np.mean(amostras ** 2)) + 1e-6
        db.append(20 * np.log10(rms))
    proc.wait()
    db = np.array(db[:duracao] + [-120.0] * max(0, duracao - len(db)))
    # mediana móvel de 60s (aproximação rápida em blocos)
    base = np.empty_like(db)
    for i in range(0, len(db), 10):
        base[i:i + 10] = np.median(db[max(0, i - 30):i + 30])
    return normalizar(np.clip(db - base, 0, None))


def sinal_heatmap(info: dict, duracao: int) -> np.ndarray | None:
    pontos = info.get("heatmap") or []
    if not pontos:
        return None
    s = np.zeros(duracao)
    for p in pontos:
        a, b = int(p["start_time"]), int(np.ceil(p["end_time"]))
        s[a:b] = p["value"]
    return normalizar(s)


def sinal_chat(arquivo: Path, duracao: int, atraso: int) -> np.ndarray | None:
    """Mensagens por segundo do chat replay (formato .live_chat.json do yt-dlp)."""
    if not arquivo or not arquivo.exists():
        return None
    s = np.zeros(duracao)
    with open(arquivo, encoding="utf-8") as f:
        for linha in f:
            try:
                item = json.loads(linha)
            except json.JSONDecodeError:
                continue
            replay = item.get("replayChatItemAction", {})
            ms = replay.get("videoOffsetTimeMsec")
            if ms is None:
                continue
            t = int(int(ms) / 1000) - atraso  # o chat reage alguns segundos depois
            if not 0 <= t < duracao:
                continue
            for acao in replay.get("actions", []):
                r = acao.get("addChatItemAction", {}).get("item", {})
                msg = r.get("liveChatTextMessageRenderer") or r.get("liveChatPaidMessageRenderer")
                if not msg:
                    continue
                texto = "".join(
                    run.get("text", "") or run.get("emoji", {}).get("shortcuts", [""])[0]
                    for run in msg.get("message", {}).get("runs", [])
                )
                peso = 1.0 + (1.5 if PALAVRAS_HYPE.search(texto) else 0)
                if "liveChatPaidMessageRenderer" in r:
                    peso += 3  # superchat
                s[t] += peso
    if s.sum() == 0:
        return None
    return normalizar(suavizar(s, 8))


# --------------------------------------------------------------------- seleção
def escolher_trechos(nota: np.ndarray, n: int, dur: int, ignorar_inicio: int):
    nota = nota.copy()
    nota[:ignorar_inicio] = 0
    trechos = []
    while len(trechos) < n:
        pico = int(np.argmax(nota))
        if nota[pico] <= 0.05:
            break
        # o pico fica ~65% dentro do corte: tem contexto antes e reação depois
        inicio = max(0, pico - int(dur * 0.65))
        fim = min(len(nota), inicio + dur)
        inicio = max(0, fim - dur)
        trechos.append({"inicio": inicio, "fim": fim, "pico": pico, "nota": round(float(nota[pico]), 3)})
        nota[max(0, inicio - dur // 2):fim + dur // 2] = 0
    return sorted(trechos, key=lambda t: -t["nota"])


def calcular_nota(sinais: dict[str, np.ndarray | None], pesos: dict[str, float], suav: int):
    total, soma_pesos = None, 0.0
    for nome, s in sinais.items():
        if s is None:
            continue
        total = s * pesos[nome] if total is None else total + s * pesos[nome]
        soma_pesos += pesos[nome]
    if total is None:
        return None
    return suavizar(total / soma_pesos, suav)


# -------------------------------------------------------------------- cortes
def filtro_video(vertical: bool, srt: str | None) -> list[str]:
    estilo = ("FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,"
              "OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=60")
    if vertical:
        f = ("[0:v]split=2[a][b];"
             "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
             "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        f = "[0:v]scale=-2:1080"
    if srt:
        f += f",subtitles={srt}:force_style='{estilo}'"
    return ["-filter_complex", f + "[v]", "-map", "[v]", "-map", "0:a?"]


def legendar(clip: Path, destino_srt: Path) -> bool:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("   (faster-whisper não instalado, pulando legenda)")
        return False
    modelo = WhisperModel("small", compute_type="int8")
    segs, _ = modelo.transcribe(str(clip), language="pt", word_timestamps=True)
    linhas, i = [], 1

    def ts(x):
        return f"{int(x // 3600):02d}:{int(x % 3600 // 60):02d}:{int(x % 60):02d},{int(x % 1 * 1000):03d}"

    for seg in segs:  # legenda estilo shorts: blocos de até 4 palavras
        palavras = seg.words or []
        for k in range(0, len(palavras), 4):
            grupo = palavras[k:k + 4]
            texto = " ".join(w.word.strip() for w in grupo).upper()
            linhas.append(f"{i}\n{ts(grupo[0].start)} --> {ts(grupo[-1].end)}\n{texto}\n")
            i += 1
    destino_srt.write_text("\n".join(linhas), encoding="utf-8")
    return bool(linhas)


def renderizar(origem: Path, inicio: float, dur: float, destino: Path, vertical: bool, legenda: bool):
    bruto = destino.with_suffix(".bruto.mp4")
    ffmpeg("-ss", str(inicio), "-i", str(origem), "-t", str(dur), "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(bruto))
    srt = None
    if legenda:
        arq_srt = destino.with_suffix(".srt")
        if legendar(bruto, arq_srt):
            srt = arq_srt.name
    ffmpeg("-i", bruto.name, *filtro_video(vertical, srt), "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", destino.name,
           cwd=destino.parent)
    bruto.unlink(missing_ok=True)


# ------------------------------------------------------------------ yt-dlp
def ydl(opts: dict):
    import yt_dlp
    base = {"quiet": True, "no_warnings": True, "noprogress": True}
    base.update(opts)
    return yt_dlp.YoutubeDL(base)


def listar_videos(perfil: str, limite: int, filtro: str | None) -> list[dict]:
    perfil = perfil.rstrip("/")
    urls = [perfil]
    if re.search(r"youtube\.com/(@|c/|channel/|user/)", perfil) and not re.search(r"/(videos|streams|shorts)$", perfil):
        urls = [perfil + "/streams", perfil + "/videos"]  # lives primeiro
    vistos, videos = set(), []
    for url in urls:
        try:
            with ydl({"extract_flat": True, "playlistend": limite * 3}) as y:
                info = y.extract_info(url, download=False)
        except Exception as e:  # aba inexistente, etc.
            log(f"  aviso: não consegui ler {url}: {e}")
            continue
        for e in info.get("entries") or [info]:
            if not e or e.get("id") in vistos:
                continue
            if filtro and not re.search(filtro, e.get("title") or "", re.IGNORECASE):
                continue
            if e.get("live_status") in ("is_live", "is_upcoming"):
                continue  # live ainda rolando: processa quando virar VOD
            vistos.add(e["id"])
            videos.append({"id": e["id"], "url": e.get("url") or e.get("webpage_url"), "titulo": e.get("title")})
    return videos[:limite]


def baixar_analise(url: str, pasta: Path) -> tuple[dict, Path, Path | None]:
    """Baixa só o áudio + chat replay (muito mais leve que o vídeo inteiro)."""
    opts = {
        "format": "bestaudio[abr<=96]/bestaudio/worst",
        "outtmpl": str(pasta / "audio.%(ext)s"),
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
    }
    with ydl(opts) as y:
        info = y.extract_info(url, download=True)
    audio = next(p for p in pasta.glob("audio.*") if not p.name.endswith(".json"))
    chat = next(iter(pasta.glob("*.live_chat.json")), None)
    return info, audio, chat


def baixar_trecho(url: str, inicio: float, fim: float, destino: Path) -> Path:
    from yt_dlp.utils import download_range_func
    opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(destino.with_suffix(".%(ext)s")),
        "download_ranges": download_range_func(None, [(inicio, fim)]),
        "force_keyframes_at_cuts": True,
    }
    with ydl(opts) as y:
        y.download([url])
    return next(p for p in destino.parent.glob(destino.stem + ".*") if p.suffix in (".mp4", ".mkv", ".webm"))


# ------------------------------------------------------------------ principal
def processar(args, pasta: Path, titulo: str, info: dict, audio: Path, chat: Path | None,
              video_local: Path | None, url: str | None) -> dict:
    duracao = int(info.get("duration") or 0)
    if not duracao:
        duracao = int(float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)]
        ).strip()))
    log(f"  duração {hms(duracao)} — analisando sinais…")
    sinais = {
        "heatmap": sinal_heatmap(info, duracao),
        "chat": sinal_chat(chat, duracao, args.atraso_chat) if chat else None,
        "audio": sinal_audio(audio, duracao),
    }
    usados = [k for k, v in sinais.items() if v is not None]
    log(f"  sinais usados: {', '.join(usados)}")
    nota = calcular_nota(sinais, {"heatmap": 0.5, "chat": 0.35, "audio": 0.15}, max(5, args.duracao // 3))
    trechos = escolher_trechos(nota, args.cortes, args.duracao, args.ignorar_inicio)

    for i, t in enumerate(trechos, 1):
        destino = pasta / f"corte_{i:02d}.mp4"
        log(f"  corte {i}: {hms(t['inicio'])} → {hms(t['fim'])} (nota {t['nota']})")
        if video_local:
            renderizar(video_local, t["inicio"], t["fim"] - t["inicio"], destino, args.vertical, args.legenda)
        else:
            bruto = baixar_trecho(url, t["inicio"], t["fim"], pasta / f"trecho_{i:02d}")
            renderizar(bruto, 0, t["fim"] - t["inicio"], destino, args.vertical, args.legenda)
            bruto.unlink(missing_ok=True)
        t["arquivo"] = destino.name
        if url and "youtu" in url:
            t["link_momento"] = f"https://youtu.be/{info.get('id')}?t={t['inicio']}"

    relatorio = {"titulo": titulo, "url": url, "duracao": duracao, "sinais": usados, "cortes": trechos}
    (pasta / "relatorio.json").write_text(json.dumps(relatorio, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.manter_audio and not video_local:
        audio.unlink(missing_ok=True)
    return relatorio


def main():
    ap = argparse.ArgumentParser(description="Gera cortes automáticos dos momentos de maior engajamento.")
    ap.add_argument("perfil", nargs="?", help="link do canal/perfil ou de um vídeo/live específico")
    ap.add_argument("--arquivo", type=Path, help="usar vídeo já baixado (sem internet)")
    ap.add_argument("--chat", type=Path, help="chat .live_chat.json para usar com --arquivo")
    ap.add_argument("--filtro", default=None, help='regex no título, ex: "gta ?(6|vi)"')
    ap.add_argument("--max-videos", type=int, default=3)
    ap.add_argument("--cortes", type=int, default=5, help="cortes por vídeo")
    ap.add_argument("--duracao", type=int, default=45, help="segundos por corte")
    ap.add_argument("--vertical", action="store_true", help="9:16 para Shorts/TikTok/Reels")
    ap.add_argument("--legenda", action="store_true", help="legenda automática (precisa faster-whisper)")
    ap.add_argument("--atraso-chat", type=int, default=6, help="segundos que o chat demora pra reagir")
    ap.add_argument("--ignorar-inicio", type=int, default=60, help="pula a intro/espera da live")
    ap.add_argument("--saida", type=Path, default=Path("saida"))
    ap.add_argument("--reprocessar", action="store_true", help="ignora o histórico de vídeos já cortados")
    ap.add_argument("--manter-audio", action="store_true")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg não encontrado. Instale: https://ffmpeg.org/download.html")
    args.saida.mkdir(parents=True, exist_ok=True)

    if args.arquivo:
        pasta = args.saida / args.arquivo.stem
        pasta.mkdir(parents=True, exist_ok=True)
        log(f"▶ {args.arquivo.name}")
        rel = processar(args, pasta, args.arquivo.stem, {}, args.arquivo, args.chat, args.arquivo, None)
        log(f"✔ {len(rel['cortes'])} cortes em {pasta}")
        return

    if not args.perfil:
        ap.error("informe o link do perfil ou --arquivo")

    historico_arq = args.saida / "processados.json"
    historico = json.loads(historico_arq.read_text()) if historico_arq.exists() else {}

    log(f"Lendo {args.perfil} …")
    videos = listar_videos(args.perfil, args.max_videos * 4, args.filtro)
    novos = [v for v in videos if args.reprocessar or v["id"] not in historico][: args.max_videos]
    if not novos:
        log("Nenhum vídeo novo para cortar.")
        return

    for v in novos:
        log(f"\n▶ {v['titulo']}")
        pasta = args.saida / re.sub(r"[^\w\- ]", "", f"{v['id']} {v['titulo'] or ''}")[:80].strip()
        pasta.mkdir(parents=True, exist_ok=True)
        try:
            info, audio, chat = baixar_analise(v["url"], pasta)
            rel = processar(args, pasta, v["titulo"], info, audio, chat, None, v["url"])
            historico[v["id"]] = {"titulo": v["titulo"], "cortes": len(rel["cortes"])}
            historico_arq.write_text(json.dumps(historico, indent=2, ensure_ascii=False), encoding="utf-8")
            log(f"✔ {len(rel['cortes'])} cortes em {pasta}")
        except Exception as e:
            log(f"✘ falhou: {e}")


if __name__ == "__main__":
    main()
