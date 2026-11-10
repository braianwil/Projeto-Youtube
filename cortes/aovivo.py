#!/usr/bin/env python3
"""
Modo AO VIVO: acompanha a live enquanto ela acontece e gera os cortes durante a transmissão.

  python cortes/aovivo.py "https://www.twitch.tv/CANAL" --vertical --legenda

Como funciona (tudo local, sem IA paga):
  1. Espera o canal entrar ao vivo (confere a cada --esperar minutos).
  2. Grava a live em pedaços de 30s e lê o chat em tempo real.
  3. A cada --bloco minutos analisa o trecho novo (chat + áudio), escolhe o melhor momento
     e gera o corte com a mesma edição do cortador (pausa, enquadramento, legenda, gancho).
  4. Apaga a gravação antiga para não encher o disco. Quando a live acaba, volta a esperar.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path


import cortador as C
import edicao
import passado
from plataformas import iniciar_chat_ao_vivo, plataforma

SEG = 30  # segundos por pedaço de gravação


def url_da_live(perfil: str) -> str:
    perfil = perfil.rstrip("/")
    if plataforma(perfil) == "youtube" and not ("watch?v=" in perfil or perfil.endswith("/live")):
        return perfil + "/live"
    return perfil


def esperar_live(url: str, minutos: float) -> dict:
    avisou = False
    while True:
        try:
            with C.ydl({"logger": C._Mudo()}) as y:
                info = y.extract_info(url, download=False)
            if info.get("is_live") or info.get("live_status") == "is_live":
                return info
        except Exception:
            pass
        if not avisou:
            C.log(f"Canal offline. Conferindo de novo a cada {minutos:g} min… (Ctrl+C para sair)")
            avisou = True
        time.sleep(minutos * 60)


def iniciar_gravacao(url: str, pasta: Path, fonte_teste: Path | None):
    """yt-dlp entrega a live no stdout e o ffmpeg divide em pedaços de 30s (sem recodificar)."""
    if fonte_teste:  # simula uma live a partir de um arquivo (para testes)
        origem = ["ffmpeg", "-loglevel", "error", "-re", "-i", str(fonte_teste), "-c", "copy", "-f", "mpegts", "-"]
    else:
        origem = [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--no-part",
                  "-f", "b[height<=1080]/bv*[height<=1080]+ba/b", "-o", "-", url]
    p1 = subprocess.Popen(origem, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "-", "-c", "copy", "-map", "0",
                           "-f", "segment", "-segment_time", str(SEG), "-reset_timestamps", "1",
                           "-segment_list", str(pasta / "lista.csv"), "-segment_list_type", "csv",
                           str(pasta / "seg_%05d.ts")], stdin=p1.stdout)
    p1.stdout.close()
    return p1, p2


def segmentos(pasta: Path) -> list[tuple[Path, float, float]]:
    arq = pasta / "lista.csv"
    if not arq.exists():
        return []
    with open(arq, newline="") as f:
        return [(pasta / r[0], float(r[1]), float(r[2])) for r in csv.reader(f) if len(r) >= 3]


def analisar_bloco(args, segs, novo_desde: float, t0: float, msgs: list, ocupado: list, pasta: Path,
                   info: dict, relatorio: list, final: bool, offset: float = 0) -> float:
    """Junta os pedaços, acha o(s) melhor(es) momento(s) do trecho novo e renderiza.
    Retorna até onde (segundo da live) o trecho já foi avaliado."""
    base = segs[0][1]
    bloco = pasta / "bloco.mp4"
    lista = pasta / "bloco.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p, _, _ in segs), encoding="utf-8")
    C.ffmpeg("-f", "concat", "-safe", "0", "-i", lista.name, "-c", "copy", bloco.name, cwd=pasta)
    duracao = int(segs[-1][2] - base)
    audio, db_fino = C.sinal_audio(bloco, duracao)
    rel = [((t - t0) - base, txt, extra) for t, txt, extra in list(msgs)]
    chat = C.sinal_chat(rel, duracao, args.atraso_chat)
    sinais, pesos = {"chat": chat, "audio": audio}, {"chat": 0.7, "audio": 0.3}
    nota = C.calcular_nota(sinais, pesos, max(5, args.duracao // 3))
    nota[: max(0, int(novo_desde - base))] = 0  # só procura pico no trecho novo (o resto é contexto)
    # pico muito perto do fim ainda não tem a "reação": fica para o próximo bloco
    limite = duracao if final else max(int(novo_desde - base) + 1, duracao - int(args.duracao * 0.4))
    nota[limite:] = 0
    antes = [(a - base, b - base) for a, b in ocupado]  # não repete trecho de corte anterior
    for t in C.escolher_trechos(nota, args.cortes_por_bloco, args.duracao, 0, ocupado=antes):
        t["inicio"], t["fim"] = edicao.ajustar_bordas(t["inicio"], t["fim"], db_fino, args.dmin, args.dmax)
        abs_ini = base + t["inicio"]
        nome = time.strftime("%Hh%Mm%Ss", time.gmtime(abs_ini + offset))  # minuto real da live
        destino = pasta.parent / f"corte_{nome}.mp4"
        t["nota"] = int(round(t["nota"] * 100))
        t["nota_global"] = C.nota_global(sinais, pesos, t["inicio"], t["fim"])
        C.log(f"  ✂ corte em {C.hms(abs_ini + offset)} da live (nota global {t['nota_global']}/100)")
        meta = {"canal": (info.get("channel") or info.get("uploader") or "").replace(" ", ""),
                "titulo": info.get("title"), "link": info.get("webpage_url") or ""}
        t.update(C.renderizar(bloco, t["inicio"], t["fim"] - t["inicio"], destino, args, t["pico"] - t["inicio"],
                              meta))
        t.update({"arquivo": destino.name, "minuto_da_live": C.hms(abs_ini + offset),
                  "inicio": round(abs_ini, 1), "fim": round(base + t["fim"], 1), "pico": base + t["pico"]})
        ocupado.append((t["inicio"], t["fim"]))
        relatorio.append(t)
        (pasta.parent / "relatorio.json").write_text(json.dumps(
            {"titulo": info.get("title"), "url": info.get("webpage_url"), "cortes": relatorio},
            indent=2, ensure_ascii=False), encoding="utf-8")
        if t.get("gancho"):
            C.log(f"    gancho: {t['gancho']} | layout: {t['layout']}")
    bloco.unlink(missing_ok=True)
    return base + limite


def acompanhar(args, info: dict, fonte_teste: Path | None = None):
    canal = C.nome_pasta(info.get("channel") or info.get("uploader") or C.nome_do_perfil(args.perfil), 40)
    titulo = C.nome_pasta(f"AO VIVO {time.strftime('%Hh%M')} {info.get('title') or ''}")
    pasta_live = args.saida / date.today().isoformat() / canal / titulo
    pasta = pasta_live / "gravacao"
    pasta.mkdir(parents=True, exist_ok=True)
    C.log(f"🔴 AO VIVO: {info.get('title')}\n  cortes em: {pasta_live}")
    C.avisar_permissao(canal)

    msgs, parar = [], threading.Event()
    if not fonte_teste:
        iniciar_chat_ao_vivo(info, msgs, parar, pasta, C.log)
    p1, p2 = iniciar_gravacao(info.get("webpage_url") or args.perfil, pasta, fonte_teste)
    while not (pasta / "seg_00000.ts").exists() and p2.poll() is None:
        time.sleep(0.5)
    t0 = time.time()  # horário (aprox.) do início da gravação, para alinhar o chat
    inicio_live = info.get("release_timestamp") or info.get("timestamp") or t0
    offset = max(0.0, t0 - inicio_live)  # quanto da live já tinha passado
    if offset > 120 and not args.sem_passado and not fonte_teste:
        C.log(f"  a live já tinha {C.hms(offset)}: cortando esse trecho em paralelo…")
        threading.Thread(target=passado.recuperar, daemon=True,
                         args=(args, info, pasta_live / "inicio-da-live", offset)).start()

    feito, ocupado, relatorio = 0.0, [], []
    bloco_s = args.bloco * 60
    try:
        while True:
            fim_live = p2.poll() is not None
            segs = segmentos(pasta)
            total = segs[-1][2] if segs else 0.0
            if segs and (total - feito >= bloco_s or (fim_live and total - feito >= 5)):
                janela = [s for s in segs if s[2] > feito - 90]  # 90s de contexto antes do trecho novo
                C.log(f"  analisando {C.hms(feito)} → {C.hms(total)} ({len(msgs)} msgs no chat)")
                try:
                    feito = analisar_bloco(args, janela, feito, t0, msgs, ocupado, pasta, info, relatorio,
                                           final=fim_live, offset=offset)
                except Exception as e:
                    C.log(f"  aviso: falha ao analisar o bloco ({e})")
                    feito = total
                for p, _, b in segs:  # apaga gravação que já não serve de contexto
                    if b < feito - 120:
                        p.unlink(missing_ok=True)
            if fim_live:
                break
            time.sleep(5)
    finally:
        parar.set()
        for p in (p1, p2):
            if p.poll() is None:
                p.terminate()
    C.log(f"⏹ Live encerrada: {len(relatorio)} cortes em {pasta_live}")
    if not args.manter_gravacao:
        for p in pasta.glob("*"):
            p.unlink(missing_ok=True)
        pasta.rmdir()


def main():
    ap = C.criar_parser("Corta a live enquanto ela acontece (YouTube, Twitch, Kick).")
    ap.add_argument("--bloco", type=float, default=10, help="minutos entre cada análise (padrão 10)")
    ap.add_argument("--cortes-por-bloco", type=int, default=1)
    ap.add_argument("--esperar", type=float, default=3, help="minutos entre checagens quando offline")
    ap.add_argument("--manter-gravacao", action="store_true", help="não apaga os pedaços gravados no final")
    ap.add_argument("--sem-passado", action="store_true",
                    help="não corta o que já tinha passado da live antes do comando")
    ap.add_argument("--uma-vez", action="store_true", help="sai quando a live acabar (não espera a próxima)")
    ap.add_argument("--fonte-teste", type=Path, help=argparse_oculto())
    args = C.preparar_args(ap.parse_args())
    if not args.perfil:
        ap.error("informe o link do canal")
    if args.fonte_teste:
        acompanhar(args, {"title": "teste", "channel": "teste", "webpage_url": ""}, args.fonte_teste)
        return
    url = url_da_live(args.perfil)
    while True:
        info = esperar_live(url, args.esperar)
        acompanhar(args, info)
        if args.uma_vez:
            break


def argparse_oculto():
    import argparse
    return argparse.SUPPRESS


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        C.log("\nEncerrado.")
