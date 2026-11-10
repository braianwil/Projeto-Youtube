"""
Suporte específico de cada plataforma: YouTube, Twitch e Kick.

Para cada VOD buscamos, além do áudio:
  - chat replay  -> lista de (segundo_do_vod, texto, peso)
  - clipes feitos pela comunidade -> lista de (inicio, duracao, views)
Tudo aqui é "melhor esforço": se uma API mudar ou bloquear, o sinal
é só ignorado e o corte continua com os demais.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT_ID = "kimne78kx3ncx3brgijov2ng5bkoct6"  # client-id público do site twitch.tv
TWITCH_HASH_COMENTARIOS = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


# ------------------------------------------------------------------ http
def http_json(url: str, metodo: str = "GET", headers: dict | None = None, corpo=None, params=None):
    """Usa curl_cffi (imita o Chrome, necessário para a Kick/Cloudflare) se instalado."""
    headers = {"Accept": "application/json", **(headers or {})}
    try:
        from curl_cffi import requests as cr
        r = cr.request(metodo, url, headers=headers, json=corpo, params=params,
                       impersonate="chrome", timeout=30)
        r.raise_for_status()
        return r.json()
    except ImportError:
        import urllib.parse
        import urllib.request
        if params:
            url += "?" + urllib.parse.urlencode(params)
        dados = json.dumps(corpo).encode() if corpo is not None else None
        if dados:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=dados, headers={"User-Agent": "Mozilla/5.0", **headers},
                                     method=metodo)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)


def plataforma(url: str) -> str:
    if "twitch.tv" in url:
        return "twitch"
    if "kick.com" in url:
        return "kick"
    return "youtube"


def urls_do_perfil(perfil: str) -> list[str]:
    """Converte o link do perfil nas abas de VODs de cada plataforma."""
    perfil = perfil.rstrip("/")
    p = plataforma(perfil)
    if p == "twitch":
        m = re.match(r"https?://(?:www\.|m\.)?twitch\.tv/([\w]+)/?$", perfil)
        if m:  # perfil puro -> só as transmissões passadas (archives)
            return [f"https://www.twitch.tv/{m.group(1)}/videos?filter=archives&sort=time"]
    elif p == "kick":
        m = re.match(r"https?://(?:www\.)?kick\.com/([\w-]+)/?$", perfil)
        if m:
            return [f"https://kick.com/{m.group(1)}/videos"]
    elif re.search(r"youtube\.com/(@|c/|channel/|user/)", perfil) and not re.search(
            r"/(videos|streams|shorts)$", perfil):
        return [perfil + "/streams", perfil + "/videos"]  # lives primeiro
    return [perfil]


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _faixas(duracao: int, partes: int):
    passo = max(60, duracao // partes + 1)
    return [(a, min(duracao, a + passo)) for a in range(0, duracao, passo)]


# ------------------------------------------------------------------ YouTube
def chat_youtube(arquivo: Path) -> list[tuple[float, str, float]]:
    """Lê o .live_chat.json que o yt-dlp baixa junto com a live."""
    msgs = []
    with open(arquivo, encoding="utf-8") as f:
        for linha in f:
            try:
                replay = json.loads(linha).get("replayChatItemAction", {})
            except json.JSONDecodeError:
                continue
            ms = replay.get("videoOffsetTimeMsec")
            if ms is None:
                continue
            for acao in replay.get("actions", []):
                item = acao.get("addChatItemAction", {}).get("item", {})
                r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if not r:
                    continue
                texto = "".join(run.get("text", "") or run.get("emoji", {}).get("shortcuts", [""])[0]
                                for run in r.get("message", {}).get("runs", []))
                extra = 3.0 if "liveChatPaidMessageRenderer" in item else 0.0
                msgs.append((int(ms) / 1000, texto, extra))
    return msgs


# ------------------------------------------------------------------ Twitch
def _twitch_gql(corpo):
    return http_json(TWITCH_GQL, "POST", {"Client-Id": TWITCH_CLIENT_ID}, corpo)


def _twitch_faixa_chat(video_id: str, inicio: int, fim: int):
    """Começa no segundo `inicio` e segue pelo cursor (não perde mensagens nos picos)."""
    msgs, variaveis = {}, {"videoID": video_id, "contentOffsetSeconds": inicio}
    while True:
        resp = _twitch_gql([{
            "operationName": "VideoCommentsByOffsetOrCursor",
            "variables": variaveis,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": TWITCH_HASH_COMENTARIOS}},
        }])
        comentarios = (((resp[0].get("data") or {}).get("video") or {}).get("comments") or {})
        edges = comentarios.get("edges") or []
        ultimo = inicio
        for e in edges:
            n = e["node"]
            ultimo = off = n["contentOffsetSeconds"]
            if inicio <= off < fim:
                texto = "".join(fr.get("text", "") for fr in (n.get("message") or {}).get("fragments") or [])
                msgs[n["id"]] = (float(off), texto, 0.0)
        cursor = edges[-1].get("cursor") if edges else None
        if not edges or ultimo >= fim or not cursor or not comentarios.get("pageInfo", {}).get("hasNextPage"):
            return msgs
        variaveis = {"videoID": video_id, "cursor": cursor}


def chat_twitch(info: dict, duracao: int, log=print) -> list[tuple[float, str, float]]:
    video_id = str(info.get("id", "")).lstrip("v")
    msgs = {}
    with ThreadPoolExecutor(8) as ex:  # baixa vários pedaços da live ao mesmo tempo
        for parte in ex.map(lambda f: _twitch_faixa_chat(video_id, *f), _faixas(duracao, 8)):
            msgs.update(parte)
    log(f"  chat Twitch: {len(msgs)} mensagens")
    return list(msgs.values())


def clipes_twitch(info: dict, log=print) -> list[tuple[float, float, int]]:
    """Clipes feitos pelos espectadores deste VOD (ótimo sinal de momento bom)."""
    login = info.get("uploader_id") or info.get("channel_id") or ""
    video_id = str(info.get("id", "")).lstrip("v")
    query = """query($login:String!,$period:ClipsPeriod){user(login:$login){clips(first:100,
      criteria:{period:$period,sort:VIEWS_DESC}){edges{node{viewCount durationSeconds
      videoOffsetSeconds video{id}}}}}}"""
    clipes = []
    for periodo in ("LAST_WEEK", "LAST_MONTH", "ALL_TIME"):
        resp = _twitch_gql({"query": query, "variables": {"login": login.lower(), "period": periodo}})
        for e in ((((resp.get("data") or {}).get("user") or {}).get("clips") or {}).get("edges") or []):
            n = e["node"]
            if (n.get("video") or {}).get("id") == video_id and n.get("videoOffsetSeconds") is not None:
                clipes.append((float(n["videoOffsetSeconds"]), float(n["durationSeconds"]), n["viewCount"]))
    clipes = list(set(clipes))
    log(f"  clipes da comunidade (Twitch): {len(clipes)}")
    return clipes


# ------------------------------------------------------------------ Kick
def _kick_canal(info: dict) -> tuple[str, str]:
    slug = info.get("uploader_id") or ""
    if not slug:
        m = re.search(r"kick\.com/([\w-]+)", info.get("webpage_url") or "")
        slug = m.group(1) if m else ""
    canal = http_json(f"https://kick.com/api/v2/channels/{slug.lower()}")
    return slug.lower(), str(canal["id"])


def _kick_faixa_chat(canal_id: str, inicio_vod: float, a: int, b: int):
    msgs, t = {}, inicio_vod + a
    fim = inicio_vod + b
    while t < fim:
        iso = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        resp = http_json(f"https://kick.com/api/v2/channels/{canal_id}/messages", params={"start_time": iso})
        lista = (resp.get("data") or {}).get("messages") or []
        ultimo = t
        for m in lista:
            tm = _ts(m["created_at"])
            ultimo = max(ultimo, tm)
            if tm < fim:
                msgs[m["id"]] = (tm - inicio_vod, m.get("content") or "", 0.0)
        t = ultimo + 0.001 if ultimo > t else t + 5
    return msgs


def chat_kick(info: dict, duracao: int, log=print) -> list[tuple[float, str, float]]:
    inicio = info.get("timestamp") or info.get("release_timestamp")
    if not inicio:
        return []
    _, canal_id = _kick_canal(info)
    msgs = {}
    with ThreadPoolExecutor(8) as ex:
        for parte in ex.map(lambda f: _kick_faixa_chat(canal_id, inicio, *f), _faixas(duracao, 8)):
            msgs.update(parte)
    log(f"  chat Kick: {len(msgs)} mensagens")
    return list(msgs.values())


def clipes_kick(info: dict, duracao: int, log=print) -> list[tuple[float, float, int]]:
    """Clipes da Kick não trazem o minuto do VOD; deduzimos pelo horário em que foram criados."""
    inicio = info.get("timestamp") or info.get("release_timestamp")
    if not inicio:
        return []
    slug, canal_id = _kick_canal(info)
    clipes, cursor = [], None
    for _ in range(5):
        params = {"sort": "views", "time": "month", **({"cursor": cursor} if cursor else {})}
        resp = http_json(f"https://web.kick.com/api/v1/channels/{canal_id}/clips", params=params)
        dados = resp.get("data") or {}
        for c in dados.get("clips") or []:
            dur = float(c.get("duration") or 30)
            criado = c.get("started_at") or c.get("created_at")
            if not criado:
                continue
            # o clipe cobre os N segundos ANTES de ser criado (ou começa em started_at)
            off = _ts(criado) - inicio - (0 if c.get("started_at") else dur)
            views = int(c.get("views_count") or c.get("view_count") or c.get("views") or 0)
            if 0 <= off < duracao:
                clipes.append((off, dur, views))
        cursor = dados.get("cursor")
        if not cursor:
            break
    log(f"  clipes da comunidade (Kick): {len(clipes)}")
    return clipes


# ------------------------------------------------------------------ fachada
def coletar_extras(url: str, info: dict, duracao: int, arquivo_chat_yt: Path | None, log=print):
    """Retorna (mensagens_do_chat, clipes) para o VOD, ignorando o que falhar."""
    p = plataforma(info.get("webpage_url") or url)
    chat, clipes = [], []
    tarefas = {
        "youtube": [("chat", lambda: chat_youtube(arquivo_chat_yt) if arquivo_chat_yt else [])],
        "twitch": [("chat", lambda: chat_twitch(info, duracao, log)), ("clipes", lambda: clipes_twitch(info, log))],
        "kick": [("chat", lambda: chat_kick(info, duracao, log)), ("clipes", lambda: clipes_kick(info, duracao, log))],
    }[p]
    for nome, fn in tarefas:
        try:
            r = fn()
            if nome == "chat":
                chat = r
            else:
                clipes = r
        except Exception as e:
            log(f"  aviso: não consegui pegar {nome} da {p} ({e.__class__.__name__}: {e})")
    return chat, clipes


# ------------------------------------------------------------------ chat AO VIVO
# Cada função roda numa thread e acrescenta (horário_unix, texto, peso_extra) em `msgs`
# até `parar` ser acionado. Tudo gratuito: IRC anônimo da Twitch, websocket público da Kick
# e o leitor de chat do próprio yt-dlp para o YouTube.
def _texto_yt(acao: dict) -> tuple[str, float] | None:
    item = acao.get("addChatItemAction", {}).get("item", {})
    r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
    if not r:
        return None
    texto = "".join(run.get("text", "") or run.get("emoji", {}).get("shortcuts", [""])[0]
                    for run in r.get("message", {}).get("runs", []))
    return texto, 3.0 if "liveChatPaidMessageRenderer" in item else 0.0


def _ao_vivo_youtube(info, msgs, parar, pasta: Path, log):
    import subprocess
    import sys
    import time
    arq = pasta / "chat.live_chat.json"
    proc = subprocess.Popen([sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--skip-download",
                             "--write-subs", "--sub-langs", "live_chat", "-o", str(pasta / "chat"),
                             info["webpage_url"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pos = 0
    try:
        while not parar.is_set() and proc.poll() is None:
            time.sleep(1)
            if not arq.exists():
                continue
            with open(arq, encoding="utf-8") as f:
                f.seek(pos)
                for linha in f:
                    if not linha.endswith("\n"):
                        break
                    pos += len(linha.encode("utf-8"))
                    try:
                        acoes = json.loads(linha).get("replayChatItemAction", {}).get("actions", [])
                    except json.JSONDecodeError:
                        continue
                    for a in acoes:
                        r = _texto_yt(a)
                        if r:
                            msgs.append((time.time(), *r))
    finally:
        proc.terminate()


def _ao_vivo_twitch(info, msgs, parar, pasta, log):
    import random
    import socket
    import time
    canal = (info.get("uploader_id") or info.get("channel") or "").lower()
    while not parar.is_set():
        try:
            s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=5)
            s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{canal}\r\n".encode())
            buf = b""
            while not parar.is_set():
                try:
                    dados = s.recv(8192)
                except socket.timeout:
                    continue
                if not dados:
                    break
                buf += dados
                *linhas, buf = buf.split(b"\r\n")
                for l in linhas:
                    l = l.decode("utf-8", "ignore")
                    if l.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif " PRIVMSG " in l:
                        msgs.append((time.time(), l.split(" PRIVMSG ", 1)[1].split(" :", 1)[-1], 0.0))
            s.close()
        except OSError as e:
            log(f"  aviso: chat da Twitch caiu ({e}); reconectando…")
            time.sleep(5)


def _ao_vivo_kick(info, msgs, parar, pasta, log):
    import time
    from websockets.sync.client import connect
    slug = (info.get("uploader_id") or re.search(r"kick\.com/([\w-]+)", info.get("webpage_url", "")).group(1)).lower()
    sala = http_json(f"https://kick.com/api/v2/channels/{slug}")["chatroom"]["id"]
    url = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
    while not parar.is_set():
        try:
            with connect(url, open_timeout=10) as ws:
                ws.send(json.dumps({"event": "pusher:subscribe",
                                    "data": {"auth": "", "channel": f"chatrooms.{sala}.v2"}}))
                while not parar.is_set():
                    try:
                        ev = json.loads(ws.recv(timeout=5))
                    except TimeoutError:
                        continue
                    if ev.get("event") == "pusher:ping":
                        ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                    elif "ChatMessageEvent" in ev.get("event", ""):
                        d = json.loads(ev["data"])
                        msgs.append((time.time(), d.get("content") or "", 0.0))
        except Exception as e:
            log(f"  aviso: chat da Kick caiu ({e.__class__.__name__}); reconectando…")
            time.sleep(5)


def iniciar_chat_ao_vivo(info: dict, msgs: list, parar, pasta: Path, log=print):
    """Começa a ler o chat da live numa thread (não trava a gravação)."""
    import threading
    fn = {"youtube": _ao_vivo_youtube, "twitch": _ao_vivo_twitch, "kick": _ao_vivo_kick}[
        plataforma(info.get("webpage_url") or "")]

    def rodar():
        try:
            fn(info, msgs, parar, pasta, log)
        except Exception as e:
            log(f"  aviso: sem chat ao vivo ({e.__class__.__name__}: {e}); seguindo só com o áudio")

    t = threading.Thread(target=rodar, daemon=True)
    t.start()
    return t
