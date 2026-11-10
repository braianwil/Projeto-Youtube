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
from pathlib import Path

import numpy as np

_modelo_whisper = None


# --------------------------------------------------------------- bordas
def ajustar_bordas(inicio: float, fim: float, db_fino: np.ndarray, antes: float = 6, depois: float = 6):
    """Move início/fim para a pausa mais silenciosa próxima (db_fino = volume a cada 0,1s)."""
    def pausa(a: float, b: float, padrao: float) -> float:
        i, j = max(0, int(a * 10)), min(len(db_fino), int(b * 10))
        if j - i < 3:
            return padrao
        janela = np.convolve(db_fino[i:j], np.ones(3) / 3, mode="same")  # pausa de ~0,3s
        return (i + int(np.argmin(janela))) / 10

    novo_inicio = pausa(inicio - antes, inicio + 2, inicio)
    novo_fim = pausa(fim - 2, fim + depois, fim)
    return round(novo_inicio, 1), round(max(novo_fim, novo_inicio + 5), 1)


# --------------------------------------------------------------- transcrição
def transcrever(clip: Path, modelo: str = "small") -> list[tuple[float, float, str]]:
    """Palavras com tempo [(inicio, fim, palavra)]. Vazio se o faster-whisper não estiver instalado."""
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
    segs, _ = _modelo_whisper.transcribe(audio, language="pt", word_timestamps=True, vad_filter=True)
    return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or []) if w.word.strip()]


def gancho_automatico(palavras, pico: float) -> str:
    """Frase dita no momento do pico (o que o streamer gritou/falou na hora H)."""
    perto = [w for a, b, w in palavras if pico - 3.5 <= a <= pico + 1.5]
    if len(perto) < 2:  # pega a frase mais próxima do pico
        perto = [w for a, b, w in palavras if abs(a - pico) <= 6]
    perto = perto[-7:]
    texto = re.sub(r"[^\w\sÀ-ÿ!?]", "", " ".join(perto)).strip().upper()
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
    if amostras == 0 or len(caixas) < 0.4 * amostras:
        return {"tipo": "blur", "W": W, "H": H}
    c = np.array(caixas)
    x, y, w, h = np.median(c, axis=0)
    cx, cy = x + w / 2, y + h / 2
    # rostos de NPC do jogo se mexem; a webcam fica parada
    if np.std(c[:, 0] + c[:, 2] / 2) > 0.08 * W:
        return {"tipo": "blur", "W": W, "H": H}
    if w < 0.13 * W:
        return {"tipo": "facecam", "W": W, "H": H, "cx": cx, "cy": cy, "fw": w}
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


def gerar_ass(destino: Path, palavras, gancho: str, vertical: bool, layout: str) -> None:
    rx, ry = (1080, 1920) if vertical else (1920, 1080)
    tam_leg, tam_gancho = (80, 66) if vertical else (64, 56)
    margem_leg = {"blur": 470, "facecam": 360, "rosto": 420}.get(layout, 420) if vertical else 80
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {rx}
PlayResY: {ry}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Leg,Arial,{tam_leg},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,{margem_leg},1
Style: Gancho,Arial,{tam_gancho},&H00000000,&H00000000,&H0000FFFF,&H00000000,-1,0,0,0,100,100,0,0,3,14,0,8,70,70,{110 if vertical else 50},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if gancho:
        ev.append(f"Dialogue: 1,{_t(0)},{_t(3.2)},Gancho,,0,0,0,,{{\\fad(150,300)}}{_limpar(gancho)}")
    # agrupa até 3 palavras; quebra em pausa > 0,6s ou pontuação
    grupos, atual = [], []
    for p in palavras:
        if atual and (len(atual) == 3 or p[0] - atual[-1][1] > 0.6 or re.search(r"[.!?]$", atual[-1][2])):
            grupos.append(atual)
            atual = []
        atual.append(p)
    if atual:
        grupos.append(atual)
    for g in grupos:
        for k, (a, b, _) in enumerate(g):
            fim = g[k + 1][0] if k + 1 < len(g) else b + 0.15
            partes = []
            for m, (_, _, w) in enumerate(g):
                w = _limpar(w).upper()
                if m == k:  # palavra falada agora: amarela e com "pulo"
                    w = (r"{\c&H00FFFF&\fscx90\fscy90\t(0,90,\fscx118\fscy118)\t(90,160,\fscx110\fscy110)}"
                         + w + r"{\r}")
                partes.append(w)
            ev.append(f"Dialogue: 0,{_t(a)},{_t(fim)},Leg,,0,0,0,,{' '.join(partes)}")
    destino.write_text(cab + "\n".join(ev) + "\n", encoding="utf-8")
