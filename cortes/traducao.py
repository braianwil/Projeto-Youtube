"""
Tradução LOCAL e gratuita da fala (ex.: criador gringo em inglês -> legenda em português).
Usa Argos Translate (offline). Na primeira vez baixa o pacote do idioma (~100 MB).
  pip install argostranslate
Sem o pacote, a legenda fica no idioma original (o corte não é perdido).
"""
from __future__ import annotations

import re

_pronto: set[tuple[str, str]] = set()


def _garantir(de: str, para: str) -> None:
    import argostranslate.package
    import argostranslate.translate
    if (de, para) in _pronto:
        return
    instalados = {(p.from_code, p.to_code) for p in argostranslate.package.get_installed_packages()}
    if (de, para) not in instalados:
        print(f"   (baixando pacote de tradução {de}→{para}, só na primeira vez…)")
        argostranslate.package.update_package_index()
        pacote = next(p for p in argostranslate.package.get_available_packages()
                      if p.from_code == de and p.to_code == para)
        argostranslate.package.install_from_path(pacote.download())
    _pronto.add((de, para))


def traduzir_texto(texto: str, de: str, para: str) -> str:
    import argostranslate.translate
    _garantir(de, para)
    return argostranslate.translate.translate(texto, de, para)


def traduzir_palavras(palavras: list[tuple[float, float, str]], de: str, para: str):
    """Traduz frase por frase e redistribui as palavras traduzidas no tempo de cada frase
    (mantém a legenda palavra por palavra sincronizada com a fala)."""
    if not palavras or de == para:
        return palavras
    frases, atual = [], []
    for p in palavras:  # frase = até pausa > 0,6s, pontuação ou 14 palavras
        if atual and (p[0] - atual[-1][1] > 0.6 or re.search(r"[.!?]$", atual[-1][2]) or len(atual) >= 14):
            frases.append(atual)
            atual = []
        atual.append(p)
    if atual:
        frases.append(atual)
    saida = []
    for f in frases:
        a, b = f[0][0], f[-1][1]
        trad = traduzir_texto(" ".join(w for _, _, w in f), de, para).split()
        if not trad:
            continue
        passo = (b - a) / len(trad)
        saida += [(a + k * passo, a + (k + 1) * passo, w) for k, w in enumerate(trad)]
    return saida
