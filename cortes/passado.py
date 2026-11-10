"""
Recupera o que já passou de uma live em andamento (antes de você rodar o aovivo.py).

  YouTube: a live fica disponível desde o início (até 120h) em pedaços numerados (&sq=N).
           Baixamos só o ÁUDIO para analisar e depois só os pedaços dos cortes escolhidos.
  Twitch:  a live já vira VOD enquanto acontece -> usamos o fluxo normal do cortador
           (áudio + chat + clipes da comunidade).
  Kick:    não disponibiliza a live em andamento -> só a partir de agora.
"""
from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cortador as C
from plataformas import plataforma


# ------------------------------------------------------------------ YouTube
class LiveYouTube:
    def __init__(self, url: str):
        self.y = C.ydl({"live_from_start": True, "logger": C._Mudo()})
        self.info = self.y.extract_info(url, download=False)
        fs = [f for f in self.info.get("formats", []) if f.get("url")]
        audios = [f for f in fs if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
        videos = [f for f in fs if f.get("vcodec") not in (None, "none") and (f.get("height") or 0) <= 1080]
        if not audios or not videos:
            raise RuntimeError("formatos 'desde o início' indisponíveis para esta live")
        self.audio = min(audios, key=lambda f: f.get("abr") or f.get("tbr") or 999)  # leve p/ analisar
        self.video = max(videos, key=lambda f: ((f.get("height") or 0), f.get("tbr") or 0))
        self.fs = {}  # duração de cada pedaço (descoberta medindo o pedaço 0)

    def _get(self, f: dict, n: int) -> bytes:
        from yt_dlp.networking import Request
        from yt_dlp.utils import update_url_query
        for tentativa in range(3):
            try:
                return self.y.urlopen(Request(update_url_query(f["url"], {"sq": n}),
                                              headers=f.get("http_headers") or {})).read()
            except Exception:
                if tentativa == 2:
                    raise
                time.sleep(2)

    def baixar(self, f: dict, a: int, b: int, destino: Path) -> Path:
        with ThreadPoolExecutor(8) as ex, open(destino, "wb") as out:
            for dados in ex.map(lambda n: self._get(f, n), range(a, b)):  # map mantém a ordem
                out.write(dados)
        return destino

    def duracao_pedaco(self, pasta: Path) -> float:
        if "td" not in self.fs:
            p = self.baixar(self.audio, 0, 1, pasta / "sq0.m4a")
            d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                               "-of", "csv=p=0", str(p)]).strip() or 5)
            p.unlink(missing_ok=True)
            self.fs["td"] = d if 0.5 < d < 20 else 5.0
        return self.fs["td"]

    def trecho(self, inicio: float, fim: float, destino: Path) -> Path:
        """Baixa só os pedaços do corte (vídeo + áudio) e corta exato com ffmpeg."""
        td = self.fs["td"]
        a, b = int(inicio // td), int(fim // td) + 1
        v = self.baixar(self.video, a, b, destino.with_suffix(".v"))
        au = self.baixar(self.audio, a, b, destino.with_suffix(".a"))
        saida = destino.with_suffix(".mp4")
        off = inicio - a * td
        C.ffmpeg("-ss", str(off), "-i", str(v), "-ss", str(off), "-i", str(au), "-t", str(fim - inicio),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-c:a", "aac", "-b:a", "192k", str(saida))
        v.unlink(missing_ok=True)
        au.unlink(missing_ok=True)
        return saida


def _youtube(args, info: dict, pasta: Path, ate: float):
    live = LiveYouTube(info["webpage_url"])
    td = live.duracao_pedaco(pasta)
    n = max(1, int(ate // td))
    C.log(f"  [início da live] baixando áudio de {C.hms(n * td)} já transmitidos…")
    audio = live.baixar(live.audio, 0, n, pasta / "audio_passado.m4a")
    vinfo = {**info, "duration": int(n * td), "heatmap": None}
    C.processar(args, pasta, info.get("title"), vinfo, audio, None, None, info["webpage_url"],
                baixar=lambda url, a, b, destino: live.trecho(a, b, destino))


# ------------------------------------------------------------------ Twitch
def _twitch(args, info: dict, pasta: Path, ate: float):
    login = (info.get("uploader_id") or "").lower()
    with C.ydl({"extract_flat": True, "playlistend": 3, "logger": C._Mudo()}) as y:
        lista = y.extract_info(f"https://www.twitch.tv/{login}/videos?filter=archives&sort=time", download=False)
    vods = [e for e in lista.get("entries") or [] if e]
    if not vods:
        C.log("  [início da live] canal não salva VODs: só dá para cortar a partir de agora")
        return
    url = vods[0].get("url") or f"https://www.twitch.tv/videos/{str(vods[0]['id']).lstrip('v')}"
    C.log("  [início da live] usando o VOD em andamento da Twitch")
    vinfo, audio, chat = C.baixar_analise(url, pasta)
    vinfo["duration"] = min(int(vinfo.get("duration") or ate), int(ate))
    C.processar(args, pasta, info.get("title"), vinfo, audio, chat, None, url)


def recuperar(args, info: dict, pasta: Path, ate: float):
    """Analisa e corta o trecho [0, ate] da live. Roda numa thread separada."""
    pasta.mkdir(parents=True, exist_ok=True)
    p = plataforma(info.get("webpage_url") or "")
    try:
        if p == "youtube":
            _youtube(args, info, pasta, ate)
        elif p == "twitch":
            _twitch(args, info, pasta, ate)
        else:
            C.log("  [início da live] a Kick não disponibiliza a live em andamento: só a partir de agora")
            return
        C.log(f"  [início da live] ✔ cortes do que já tinha passado em: {pasta}")
    except Exception as e:
        C.log(f"  [início da live] não consegui recuperar ({e.__class__.__name__}: {e})")
