#!/usr/bin/env python3
"""
Cortador automático de lives/vídeos do YouTube, Twitch e Kick (feito pensando em GTA 6).

Fluxo:
  1. Lê o perfil/canal (youtube.com/@x, twitch.tv/x, kick.com/x ou link de um vídeo).
  2. Para cada vídeo/live novo: baixa só o ÁUDIO (leve) + chat replay + clipes da comunidade.
  3. Calcula uma nota de engajamento por segundo combinando:
       - heatmap "Mais repetidos" do YouTube (o que o público mais reassistiu)
       - clipes que os espectadores criaram na Twitch/Kick (pesados pelas views)
       - picos de mensagens no chat da live (+ peso extra para "KKKK", "CLIP", "MDS"...)
       - picos de volume/grito do streamer
  4. Escolhe os N melhores trechos sem sobreposição.
  5. Ajusta início/fim para uma pausa (não corta frase no meio).
  6. Baixa só esses trechos e edita localmente (sem IA paga): 9:16 seguindo o rosto ou
     "webcam em cima + jogo embaixo", legenda animada palavra por palavra e gancho nos 3s iniciais.

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

import edicao
from plataformas import chat_youtube, coletar_extras, urls_do_perfil

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
    """Escala 0..1 mantendo a ordem dos picos (o maior momento continua sendo o maior)."""
    m = x.max() if len(x) else 0
    return x / m if m > 0 else np.zeros_like(x)


# ------------------------------------------------------------------------ sinais
def sinal_audio(arquivo: Path, duracao: int) -> tuple[np.ndarray, np.ndarray]:
    """Volume acima da mediana local (gritos/hype) + volume a cada 0,1s (para achar pausas)."""
    taxa = 8000
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(arquivo),
         "-vn", "-ac", "1", "-ar", str(taxa), "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
    )
    bruto = np.frombuffer(proc.stdout.read(), dtype=np.int16)
    proc.wait()
    n = len(bruto) // 800  # blocos de 0,1s
    energia = (bruto[:n * 800].astype(np.float32).reshape(n, 800) / 32768) ** 2
    db_fino = 10 * np.log10(energia.mean(axis=1) + 1e-12)
    seg = len(db_fino) // 10
    db = 10 * np.log10(energia[:seg * 10].mean(axis=1).reshape(seg, 10).mean(axis=1) + 1e-12)
    db = np.concatenate([db[:duracao], np.full(max(0, duracao - len(db)), -120.0)])
    # mediana móvel de 60s (aproximação rápida em blocos)
    base = np.empty_like(db)
    for i in range(0, len(db), 10):
        base[i:i + 10] = np.median(db[max(0, i - 30):i + 30])
    return normalizar(np.clip(db - base, 0, None)), db_fino


def sinal_heatmap(info: dict, duracao: int) -> np.ndarray | None:
    pontos = info.get("heatmap") or []
    if not pontos:
        return None
    s = np.zeros(duracao)
    for p in pontos:
        a, b = int(p["start_time"]), int(np.ceil(p["end_time"]))
        s[a:b] = p["value"]
    return normalizar(s)


def sinal_chat(msgs: list, duracao: int, atraso: int) -> np.ndarray | None:
    """Mensagens por segundo no chat; risadas/hype/superchat pesam mais."""
    if not msgs:
        return None
    s = np.zeros(duracao)
    for t, texto, extra in msgs:
        t = int(t) - atraso  # o chat reage alguns segundos depois do momento
        if 0 <= t < duracao:
            s[t] += 1.0 + (1.5 if PALAVRAS_HYPE.search(texto or "") else 0) + extra
    if s.sum() == 0:
        return None
    return normalizar(suavizar(s, 8))


def sinal_clipes(clipes: list, duracao: int) -> np.ndarray | None:
    """Trechos que os próprios espectadores clipparam (Twitch/Kick), pesados pelas views."""
    if not clipes:
        return None
    s = np.zeros(duracao)
    for inicio, dur, views in clipes:
        a = max(0, int(inicio))
        s[a:min(duracao, int(inicio + dur))] += 1 + np.log1p(views)
    return normalizar(s) if s.any() else None


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
def renderizar(origem: Path, inicio: float, dur: float, destino: Path, args, pico_rel: float) -> dict:
    """Corta, enquadra, legenda e escreve o gancho. Retorna o que foi feito (vai pro relatório)."""
    bruto = destino.with_suffix(".bruto.mp4")
    ffmpeg("-ss", str(inicio), "-i", str(origem), "-t", str(dur), "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(bruto))
    precisa_texto = args.legenda or args.gancho == "auto"
    try:
        palavras = edicao.transcrever(bruto, args.modelo_whisper) if precisa_texto else []
    except Exception as e:  # legenda é opcional: não perde o corte por causa dela
        log(f"   aviso: transcrição falhou ({e}); seguindo sem legenda")
        palavras = []
    if precisa_texto and not palavras:
        log("   (sem fala detectada neste trecho: corte sai sem legenda/gancho)")
    gancho = edicao.gancho_automatico(palavras, pico_rel) if args.gancho == "auto" else (
        "" if args.gancho == "nenhum" else args.gancho.upper())
    lay = edicao.detectar_layout(bruto) if args.layout == "auto" else {"tipo": args.layout, **dict(
        zip(("W", "H"), edicao.dimensoes(bruto)))}
    if lay["tipo"] in ("rosto", "facecam") and "cx" not in lay:
        lay = {"tipo": "blur", "W": lay["W"], "H": lay["H"]}  # forçado mas sem rosto detectado
    filtro = edicao.filtro_layout(lay, args.vertical)
    if gancho or (args.legenda and palavras):
        ass = destino.with_suffix(".ass")
        edicao.gerar_ass(ass, palavras if args.legenda else [], gancho, args.vertical, lay["tipo"])
        filtro += f",ass={ass.name}"
    ffmpeg("-i", bruto.name, "-filter_complex", filtro + "[v]", "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", destino.name, cwd=destino.parent)
    bruto.unlink(missing_ok=True)
    return {"layout": lay["tipo"], "gancho": gancho,
            "fala": " ".join(w for _, _, w in palavras)[:500] if palavras else ""}


# ------------------------------------------------------------------ yt-dlp
class _Mudo:  # esconde "ERROR: canal não tem aba de lives" (é normal)
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def ydl(opts: dict):
    import yt_dlp
    base = {"quiet": True, "no_warnings": True, "noprogress": True}
    base.update(opts)
    return yt_dlp.YoutubeDL(base)


def listar_videos(perfil: str, limite: int, filtro: str | None) -> list[dict]:
    urls = urls_do_perfil(perfil)
    vistos, videos = set(), []
    for url in urls:
        try:
            with ydl({"extract_flat": True, "playlistend": limite * 3, "logger": _Mudo()}) as y:
                info = y.extract_info(url, download=False)
        except Exception as e:  # aba inexistente, etc.
            if "does not have" not in str(e):
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


def link_momento(info: dict, url: str, seg: int) -> str:
    pagina = info.get("webpage_url") or url
    if "youtu" in pagina:
        return f"https://youtu.be/{info.get('id')}?t={seg}"
    if "twitch.tv" in pagina:
        return f"{pagina}?t={seg // 3600}h{seg % 3600 // 60}m{seg % 60}s"
    return f"{pagina} (pular para {hms(seg)})"


# ------------------------------------------------------------------ principal
def processar(args, pasta: Path, titulo: str, info: dict, audio: Path, chat: Path | None,
              video_local: Path | None, url: str | None) -> dict:
    duracao = int(info.get("duration") or 0)
    if not duracao:
        duracao = int(float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)]
        ).strip()))
    log(f"  duração {hms(duracao)} — analisando sinais…")
    if url:
        msgs, clipes = coletar_extras(url, info, duracao, chat, log)
    else:
        msgs, clipes = (chat_youtube(chat) if chat else []), []
    sinais = {
        "heatmap": sinal_heatmap(info, duracao),
        "clipes": sinal_clipes(clipes, duracao),
        "chat": sinal_chat(msgs, duracao, args.atraso_chat),
        "audio": None,
    }
    sinais["audio"], db_fino = sinal_audio(audio, duracao)
    usados = [k for k, v in sinais.items() if v is not None]
    log(f"  sinais usados: {', '.join(usados)}")
    pesos = {"heatmap": 0.5, "clipes": 0.45, "chat": 0.35, "audio": 0.15}
    nota = calcular_nota(sinais, pesos, max(5, args.duracao // 3))
    trechos = escolher_trechos(nota, args.cortes, args.duracao, args.ignorar_inicio)

    for i, t in enumerate(trechos, 1):
        t["inicio"], t["fim"] = edicao.ajustar_bordas(t["inicio"], t["fim"], db_fino)  # cortar na pausa
        t["nota"] = int(round(t["nota"] * 100))  # 0–100, como no Autoclipper
        dur, pico_rel = t["fim"] - t["inicio"], t["pico"] - t["inicio"]
        destino = pasta / f"corte_{i:02d}.mp4"
        log(f"  corte {i}: {hms(t['inicio'])} → {hms(t['fim'])} (nota {t['nota']}/100)")
        if video_local:
            t.update(renderizar(video_local, t["inicio"], dur, destino, args, pico_rel))
        else:
            bruto = baixar_trecho(url, t["inicio"], t["fim"], pasta / f"trecho_{i:02d}")
            t.update(renderizar(bruto, 0, dur, destino, args, pico_rel))
            bruto.unlink(missing_ok=True)
        if t["gancho"]:
            log(f"    gancho: {t['gancho']}  | layout: {t['layout']}")
        t["arquivo"] = destino.name
        if url:
            t["link_momento"] = link_momento(info, url, int(t["inicio"]))

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
    ap.add_argument("--legenda", action="store_true", help="legenda animada palavra por palavra (faster-whisper)")
    ap.add_argument("--gancho", default="auto",
                    help='texto nos 3 primeiros segundos: "auto" (frase dita no pico), "nenhum" ou um texto seu')
    ap.add_argument("--layout", default="auto", choices=["auto", "facecam", "rosto", "blur"],
                    help="vertical: auto detecta webcam/rosto; facecam = webcam em cima e jogo embaixo")
    ap.add_argument("--modelo-whisper", default="small", help="tiny/base/small/medium (maior = melhor e mais lento)")
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
