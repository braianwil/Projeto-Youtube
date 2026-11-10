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

    def _atualizar(self):
        """Pede links novos ao YouTube (o link pode vencer ou ser recusado com 401)."""
        novo = LiveYouTube(self.info["webpage_url"])
        self.y, self.info, self.audio, self.video = novo.y, novo.info, novo.audio, novo.video

    def _get(self, f: dict, n: int) -> bytes:
        from yt_dlp.networking import Request
        from yt_dlp.utils import update_url_query
        f = self.audio if f.get("format_id") == self.audio.get("format_id") else self.video  # link atual
        return self.y.urlopen(Request(update_url_query(f["url"], {"sq": n}),
                                      headers=f.get("http_headers") or {})).read()

    def ultimo_pedaco(self) -> int | None:
        """Número do pedaço mais recente (cabeçalho X-Head-Seqnum, como o yt-dlp faz)."""
        from yt_dlp.networking import HEADRequest
        try:
            r = self.y.urlopen(HEADRequest(self.audio["url"], headers=self.audio.get("http_headers") or {}))
            return int(r.headers.get("X-Head-Seqnum"))
        except Exception:
            return None

    def baixar(self, f: dict, a: int, b: int, destino: Path) -> tuple[Path, int]:
        """Baixa os pedaços [a, b). Se um falhar mesmo após renovar o link, para ali
        (mantém a linha do tempo correta). Retorna (arquivo, quantos pedaços baixou)."""
        feitos, renovou = 0, False
        with open(destino, "wb") as out:
            for ini in range(a, b, 16):  # lotes de 16, 4 em paralelo
                lote = list(range(ini, min(b, ini + 16)))
                for tentativa in range(3):
                    try:
                        with ThreadPoolExecutor(4) as ex:
                            dados = list(ex.map(lambda n: self._get(f, n), lote))
                        break
                    except Exception as e:
                        if tentativa == 2:
                            C.log(f"   (parei no pedaço {lote[0]}: {e.__class__.__name__}; usando o que já baixei)")
                            return destino, feitos
                        if not renovou or "401" in str(e) or "403" in str(e):
                            self._atualizar()
                            renovou = True
                        time.sleep(2)
                for d in dados:
                    out.write(d)
                feitos += len(lote)
        return destino, feitos

    def duracao_pedaco(self, pasta: Path, ate: float) -> tuple[float, int]:
        """(segundos por pedaço, último pedaço disponível)."""
        cabeca = self.ultimo_pedaco()
        if cabeca and 0.5 < ate / cabeca < 20:
            self.fs["td"] = ate / cabeca
            return self.fs["td"], cabeca
        p, _ = self.baixar(self.audio, 0, 1, pasta / "sq0.m4a")
        try:
            d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                               "-of", "csv=p=0", str(p)]).strip() or 5)
        except Exception:
            d = 5.0
        p.unlink(missing_ok=True)
        self.fs["td"] = d if 0.5 < d < 20 else 5.0
        return self.fs["td"], (cabeca or int(ate // self.fs["td"])) - 2

    def trecho(self, inicio: float, fim: float, destino: Path) -> Path:
        """Baixa só os pedaços do corte (vídeo + áudio) e corta exato com ffmpeg."""
        td = self.fs["td"]
        a, b = int(inicio // td), int(fim // td) + 1
        v, nv = self.baixar(self.video, a, b, destino.with_suffix(".v"))
        au, na = self.baixar(self.audio, a, b, destino.with_suffix(".a"))
        if min(nv, na) < b - a:
            raise RuntimeError("trecho incompleto no YouTube")
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
    td, n = live.duracao_pedaco(pasta, ate)
    n = max(1, n)
    C.log(f"  [início da live] baixando áudio de {C.hms(n * td)} já transmitidos…")
    audio, n = live.baixar(live.audio, 0, n, pasta / "audio_passado.m4a")
    if n < 10:
        raise RuntimeError("o YouTube não liberou o início desta live (DVR desativado?)")
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
