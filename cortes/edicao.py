"""
Edição do corte, 100% local (sem API paga):
  - bordas no silêncio  -> o corte não começa/termina no meio da frase
  - enquadramento       -> segue o rosto ou monta "webcam em cima, jogo embaixo"
  - legenda animada     -> palavra por palavra com destaque (faster-whisper + libass)
  - gancho              -> frase de impacto escrita nos 3 primeiros segundos
"""
from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path

import numpy as np

_modelo_whisper = None
_trava = threading.Lock()  # live e "início da live" rodam em paralelo: 1 transcrição por vez


# --------------------------------------------------------------- bordas
def ajustar_bordas(inicio: float, fim: float, db_fino: np.ndarray, dmin: float, dmax: float, antes: float = 6):
    """Move início/fim para a pausa mais próxima, respeitando a duração mín/máx da plataforma."""
    def pausa(a: float, b: float, padrao: float) -> float:
        i, j = max(0, int(a * 10)), min(len(db_fino), int(b * 10))
        if j - i < 3:
            return padrao
        janela = np.convolve(db_fino[i:j], np.ones(3) / 3, mode="same")  # pausa de ~0,3s
        return (i + int(np.argmin(janela))) / 10

    novo_inicio = max(0.0, pausa(inicio - antes, inicio + 2, inicio))
    alvo = fim - inicio
    a = novo_inicio + max(dmin, alvo - 4)
    b = novo_inicio + min(dmax, max(alvo + 4, dmin + 1))
    novo_fim = pausa(a, b, novo_inicio + min(max(alvo, dmin), dmax))
    novo_fim = min(max(novo_fim, novo_inicio + dmin), novo_inicio + dmax)
    return round(novo_inicio, 1), round(novo_fim, 1)


# --------------------------------------------------------------- transcrição
def transcrever(clip: Path, modelo: str = "small", idiomas=("pt",)) -> list[tuple[float, float, str]]:
    with _trava:
        return _transcrever(clip, modelo, idiomas)


def _transcrever(clip: Path, modelo: str, idiomas) -> list[tuple[float, float, str]]:
    """Palavras com tempo [(inicio, fim, palavra)]. Vazio se não houver whisper ou se a fala
    não estiver em um dos `idiomas` (ex.: vídeo em inglês -> sem legenda)."""
    global _modelo_whisper
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    if _modelo_whisper is None:  # carrega uma vez só
        _modelo_whisper = WhisperModel(modelo, compute_type="int8")
    # decodifica com ffmpeg (evita incompatibilidades do PyAV entre versões)
    pcm = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(clip), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    def rodar():
        segs, info = _modelo_whisper.transcribe(audio, language=None, word_timestamps=True, vad_filter=True)
        if info.language not in idiomas:  # detectado nos primeiros 30s, antes de transcrever
            print(f"   (fala em '{info.language}': sem legenda/gancho automático)")
            return []
        return list(segs)

    try:
        segs = rodar()  # a GPU só falha aqui, ao rodar de fato
    except RuntimeError as e:
        if not re.search(r"cublas|cudnn|cuda", str(e), re.IGNORECASE):
            raise
        print("   (GPU sem bibliotecas CUDA; usando o processador para a legenda)")
        _modelo_whisper = WhisperModel(modelo, device="cpu", compute_type="int8")
        segs = rodar()
    return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or []) if w.word.strip()]


def gancho_automatico(palavras, pico: float, maximo: int = 8) -> str:
    """Frase completa dita no pico: começa e termina em pausa/pontuação (não corta no meio)."""
    if not palavras:
        return ""
    i = min(range(len(palavras)), key=lambda k: abs(palavras[k][0] - (pico - 1)))

    def fim_de_frase(k):  # palavra k encerra a frase?
        return (re.search(r"[.!?]$", palavras[k][2]) or k + 1 >= len(palavras)
                or palavras[k + 1][0] - palavras[k][1] > 0.5)

    a = i
    while a > 0 and not fim_de_frase(a - 1) and i - a < maximo - 1:
        a -= 1
    b = i
    while not fim_de_frase(b) and b - a < maximo - 1:
        b += 1
    if not fim_de_frase(b):  # frase longa demais: termina na última pausa possível
        return ""
    texto = re.sub(r"[^\w\sÀ-ÿ!?]", "", " ".join(w for _, _, w in palavras[a:b + 1])).strip().upper()
    return f"“{texto}”" if len(texto.split()) >= 2 else ""


# --------------------------------------------------------------- enquadramento
def dimensoes(video: Path) -> tuple[int, int]:
    s = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height", "-of", "csv=p=0", str(video)]).decode()
    w, h = s.strip().split(",")[:2]
    return int(w), int(h)


def detectar_layout(video: Path) -> dict:
    """
    rosto   -> pessoa ocupa a tela: corte 9:16 centrado no rosto
    facecam -> webcam pequena num canto (live de jogo): webcam em cima, jogo embaixo
    blur    -> nenhum rosto estável: vídeo inteiro com fundo desfocado
    """
    W, H = dimensoes(video)
    try:
        import cv2
    except ImportError:
        return {"tipo": "blur", "W": W, "H": H}
    cascata = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    escala = 640 / W
    caixas, amostras = [], 0
    for f in range(0, max(total, 1), int(fps)):  # 1 quadro por segundo
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, quadro = cap.read()
        if not ok:
            break
        amostras += 1
        cinza = cv2.cvtColor(cv2.resize(quadro, None, fx=escala, fy=escala), cv2.COLOR_BGR2GRAY)
        rostos = cascata.detectMultiScale(cinza, 1.1, 6, minSize=(24, 24))
        if len(rostos):
            x, y, w, h = max(rostos, key=lambda r: r[2] * r[3])
            caixas.append([v / escala for v in (x, y, w, h)])
    cap.release()
    if amostras == 0 or len(caixas) < 0.6 * amostras:
        return {"tipo": "blur", "W": W, "H": H}
    c = np.array(caixas)
    x, y, w, h = np.median(c, axis=0)
    cx, cy = x + w / 2, y + h / 2
    # rostos de NPC do jogo se mexem; a webcam fica parada
    if np.std(c[:, 0] + c[:, 2] / 2) > 0.05 * W:
        return {"tipo": "blur", "W": W, "H": H}
    if w < 0.13 * W:
        return {"tipo": "facecam", "W": W, "H": H, "cx": cx, "cy": cy, "fw": w}
    if len(caixas) < 0.8 * amostras:  # cortar as laterais só se a pessoa está quase sempre na tela
        return {"tipo": "blur", "W": W, "H": H}
    return {"tipo": "rosto", "W": W, "H": H, "cx": cx}


def _par(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def filtro_layout(lay: dict, vertical: bool) -> str:
    W, H = lay["W"], lay["H"]
    if not vertical:
        return ("[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2")
    if lay["tipo"] == "rosto":
        cw = _par(min(W, H * 9 / 16))
        x = _par(min(max(0, lay["cx"] - cw / 2), W - cw))
        return f"[0:v]crop={cw}:{H}:{x}:0,scale=1080:1920"
    if lay["tipo"] == "facecam":
        # topo 1080x640 com a webcam, base 1080x1280 com o centro do jogo
        camw = _par(min(W, lay["fw"] * 3.4))
        camh = _par(min(H, camw * 640 / 1080))
        camw = _par(camh * 1080 / 640)
        camx = _par(min(max(0, lay["cx"] - camw / 2), W - camw))
        camy = _par(min(max(0, lay["cy"] - camh / 2), H - camh))
        gw = _par(min(W, H * 1080 / 1280))
        gx = _par((W - gw) / 2)
        return (f"[0:v]split=2[c][g];[c]crop={camw}:{camh}:{camx}:{camy},scale=1080:640,setsar=1[cam];"
                f"[g]crop={gw}:{H}:{gx}:0,scale=1080:1280,setsar=1[jogo];[cam][jogo]vstack")
    return ("[0:v]split=2[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
            "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")


# --------------------------------------------------------------- legenda + gancho (ASS)
def _t(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def _limpar(txt: str) -> str:
    return txt.replace("{", "").replace("}", "").replace("\\", "")


def gerar_ass(destino: Path, palavras, gancho: str, vertical: bool, layout: str,
              credito: str = "", pergunta: str = "", dur: float = 0) -> None:
    rx, ry = (1080, 1920) if vertical else (1920, 1080)
    tam_leg, tam_gancho = (74, 66) if vertical else (64, 56)
    margem_leg = {"blur": 470, "facecam": 360, "rosto": 420}.get(layout, 420) if vertical else 80
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {rx}
PlayResY: {ry}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Leg,Arial,{tam_leg},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,{margem_leg},1
Style: Credito,Arial,{34 if vertical else 30},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,6,0,9,30,30,30,1
Style: Pergunta,Arial,{tam_gancho},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,16,0,5,80,80,0,1
Style: Gancho,Arial,{tam_gancho},&H00000000,&H00000000,&H0000FFFF,&H00000000,-1,0,0,0,100,100,0,0,3,14,0,8,70,70,{110 if vertical else 50},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if credito and dur:  # crédito do criador visível o vídeo todo
        ev.append(f"Dialogue: 2,{_t(0)},{_t(dur)},Credito,,0,0,0,,{_limpar(credito)}")
    if pergunta and dur > 8:  # desfecho: pergunta para o público nos 3s finais
        ev.append(f"Dialogue: 2,{_t(dur - 3)},{_t(dur)},Pergunta,,0,0,0,,{{\\fad(200,0)}}{_limpar(pergunta).upper()}")
    if gancho:
        ev.append(f"Dialogue: 1,{_t(0)},{_t(3.2)},Gancho,,0,0,0,,{{\\fad(150,300)}}{_limpar(gancho)}")
    # agrupa até 3 palavras; quebra em pausa > 0,6s ou pontuação
    grupos, atual = [], []
    for p in palavras:
        letras = sum(len(w) + 1 for _, _, w in atual) + len(p[2])
        if atual and (len(atual) == 3 or letras > 18 or p[0] - atual[-1][1] > 0.6
                      or re.search(r"[.!?]$", atual[-1][2])):
            grupos.append(atual)
            atual = []
        atual.append(p)
    if atual:
        grupos.append(atual)
    legs = []
    for g in grupos:
        for k, (a, b, _) in enumerate(g):
            fim = g[k + 1][0] if k + 1 < len(g) else b + 0.15
            partes = [(r"{\c&H00FFFF&}" + _limpar(w).upper() + r"{\r}") if m == k else _limpar(w).upper()
                      for m, (_, _, w) in enumerate(g)]  # palavra falada agora em amarelo
            legs.append([a, fim, " ".join(partes)])
    legs.sort(key=lambda x: x[0])
    for n, (a, fim, txt) in enumerate(legs):  # nunca duas legendas ao mesmo tempo
        if n + 1 < len(legs):
            fim = min(fim, legs[n + 1][0])
        if fim - a >= 0.04:
            ev.append(f"Dialogue: 0,{_t(a)},{_t(fim)},Leg,,0,0,0,,{txt}")
    destino.write_text(cab + "\n".join(ev) + "\n", encoding="utf-8")


# --------------------------------------------------------------- edição dinâmica
def _energia(arquivo: Path, passo: float = 0.05) -> np.ndarray:
    """Volume em dB a cada `passo` segundos."""
    taxa = 16000
    pcm = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(arquivo), "-vn", "-ac", "1",
                          "-ar", str(taxa), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    n = int(taxa * passo)
    k = len(x) // n
    return 10 * np.log10((x[:k * n].reshape(k, n) ** 2).mean(axis=1) + 1e-10)


def cortar_silencios(bruto: Path, destino: Path, pico: float, dmin: float,
                     minimo: float = 0.45, margem: float = 0.12) -> tuple[Path, float, float]:
    """Remove pausas/partes mortas (jump cut) sem deixar o corte abaixo da duração mínima.
    Retorna (arquivo, novo_pico, segundos_removidos)."""
    passo = 0.05
    db = _energia(bruto, passo)
    total = len(db) * passo
    silencio = db < np.median(db) - 10  # relativo ao próprio clipe (jogo tem som de fundo)
    pausas, i = [], 0
    while i < len(silencio):
        if silencio[i]:
            j = i
            while j < len(silencio) and silencio[j]:
                j += 1
            a, b = i * passo + margem, j * passo - margem
            if b - a >= minimo and a > 0.3 and b < total - 0.3:
                pausas.append((a, b))
            i = j
        else:
            i += 1
    # remove as maiores pausas primeiro, sem passar do mínimo da plataforma
    escolhidas, removido = [], 0.0
    for a, b in sorted(pausas, key=lambda p: p[0] - p[1]):
        if total - removido - (b - a) >= dmin + 0.5:
            escolhidas.append((a, b))
            removido += b - a
    if removido < 0.8:
        return bruto, pico, 0.0
    escolhidas.sort()
    manter, t = [], 0.0
    for a, b in escolhidas:
        manter.append((t, a))
        t = b
    manter.append((t, total))
    partes = "".join(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{k}];"
                     f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{k}];" for k, (a, b) in enumerate(manter))
    juntar = "".join(f"[v{k}][a{k}]" for k in range(len(manter))) + f"concat=n={len(manter)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(bruto),
                    "-filter_complex", partes + juntar, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(destino)], check=True)
    novo_pico = pico - sum(min(b, pico) - a for a, b in escolhidas if a < pico)
    return destino, novo_pico, removido


def momentos_enfase(arquivo: Path, n: int = 3, separacao: float = 5.0) -> list[float]:
    """Picos de volume (grito/risada/punchline) para dar zoom."""
    passo = 0.1
    db = np.convolve(_energia(arquivo, passo), np.ones(5) / 5, mode="same")
    base = np.median(db)
    tempos = []
    for i in np.argsort(-db):
        if db[i] < base + 8 or len(tempos) >= n:
            break
        t = i * passo
        if all(abs(t - u) >= separacao for u in tempos) and t > 0.5:
            tempos.append(t)
    return sorted(tempos)


def filtro_zoom(tempos: list[float], vertical: bool, fps: int = 30, forca: float = 0.12,
                dur: float = 1.4) -> str:
    """Zoom-in suave (~12%) por ~1,4s em cada momento de ênfase."""
    if not tempos:
        return ""
    w, h = (1080, 1920) if vertical else (1920, 1080)
    rampa = 0.15
    termos = "+".join(f"min(1,max(0,(it-{t - 0.2:.2f})/{rampa}))*min(1,max(0,({t - 0.2 + dur:.2f}-it)/{rampa}))"
                      for t in tempos)
    z = f"1+{forca}*min(1,{termos})"
    return (f",zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={w}x{h}:fps={fps}")
