"""
avaliar.py — Precisão, revocação e F1 de cada ciclo contra o gabarito.

A segmentação é avaliada pelas **fronteiras**: o início de cada bloco previsto
é comparado ao início de cada nó do gabarito. Como cada nó do gabarito vai até
o início do seguinte, o conjunto de inícios define a segmentação inteira.

As posições são comparadas no espaço normalizado do `anchor.py` (sem tags,
markdown, acentos e espaços repetidos), para que `# **<u>Art. 2º` e `Art. 2º`
caiam no mesmo ponto. Com `--tolerancia k`, uma fronteira prevista casa com
uma do gabarito a até `k` chars normalizados — casamento um-para-um, guloso
pela menor distância.

    precisão  = fronteiras previstas que casam / fronteiras previstas
    revocação = fronteiras do gabarito que casam / fronteiras do gabarito

Uso:
    python avaliar.py resultados/sisu-rules resultados/conference_v2
    python avaliar.py resultados/conference_v2 --gabarito gabarito/conference.md

Cada argumento é o prefixo de uma execução (`--saida` sem `.json`); lê todos os
`{prefixo}_cicloN.json`. O gabarito é `gabarito/<nome>.md`, com o sufixo `_vN`
removido do nome. Escreve `{prefixo}_avaliacao.json` e `{prefixo}_avaliacao.png`.
"""

import argparse
import glob
import json
import os
import re
from bisect import bisect_left

from anchor import _localizar_ancora, _normalizar_texto_cached
from gabarito import ErroGabarito, carregar_gabarito

DIR_GABARITO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gabarito")


def _ciclos(prefixo: str) -> list[tuple[int, str]]:
    achados = []
    for caminho in glob.glob(f"{glob.escape(prefixo)}_ciclo*.json"):
        m = re.search(r"_ciclo(\d+)\.json$", caminho)
        if m:
            achados.append((int(m.group(1)), caminho))
    return sorted(achados)


def _gabarito_padrao(prefixo: str) -> str:
    nome = re.sub(r"_v\d+$", "", os.path.basename(prefixo))
    return os.path.join(DIR_GABARITO, f"{nome}.md")


def inicios_previstos(texto: str, blocos: list[dict]) -> tuple[list[int], int]:
    """
    Posição de início de cada bloco no texto-fonte, refazendo o ponteiro do
    pipeline: `offset_inicio` é buscado a partir do fim do bloco anterior
    (com fallback para logo após o início anterior). Devolve `(inícios, perdidos)`.
    """
    inicios, perdidos = [], 0
    pos_fim = pos_ini_ant = 0
    for b in blocos:
        ini, _ = _localizar_ancora(texto, b.get("offset_inicio") or "", pos_fim)
        if ini == -1:
            ini, _ = _localizar_ancora(texto, b.get("offset_inicio") or "", pos_ini_ant)
        if ini == -1:
            perdidos += 1
            continue
        inicios.append(ini)
        pos_ini_ant = ini + 1
        _, fim = _localizar_ancora(texto, b.get("offset_fim") or "", ini, preferir_sufixo=True)
        pos_fim = fim if fim != -1 else ini + 1
    return inicios, perdidos


def _para_normalizado(texto: str, posicoes: list[int]) -> list[int]:
    """
    Índice normalizado do primeiro char alfanumérico em ou após cada posição.

    Pular a pontuação inicial faz `- [1]` e `[1]`, ou `(a)` e `a)`, caírem no
    mesmo ponto — o marcador de lista (`-`) não é removido pela normalização.
    """
    norm, mapa = _normalizar_texto_cached(texto)
    saida = []
    for p in posicoes:
        i = bisect_left(mapa, p)
        while i < len(norm) and not norm[i].isalnum():
            i += 1
        saida.append(i)
    return saida


def casar(previstas: list[int], gabarito: list[int], tolerancia: int) -> int:
    """Número de pares (prevista, gabarito) casados um-para-um a até `tolerancia`."""
    if tolerancia == 0:
        return len(set(previstas) & set(gabarito))
    pares = sorted(
        (abs(p - g), i, j)
        for i, p in enumerate(previstas)
        for j, g in enumerate(gabarito)
        if abs(p - g) <= tolerancia
    )
    usados_p, usados_g = set(), set()
    for _, i, j in pares:
        if i not in usados_p and j not in usados_g:
            usados_p.add(i)
            usados_g.add(j)
    return len(usados_p)


def metricas(acertos: int, n_prev: int, n_gab: int) -> dict:
    p = acertos / n_prev if n_prev else 0.0
    r = acertos / n_gab if n_gab else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precisao": p, "revocacao": r, "f1": f1}


def avaliar(prefixo: str, caminho_gabarito: str, tolerancia: int) -> dict:
    texto, nos = carregar_gabarito(caminho_gabarito)
    # Dois nós podem cair no mesmo ponto normalizado; fronteira é um conjunto.
    gab = sorted(set(_para_normalizado(texto, [n["inicio"] for n in nos])))

    linhas = []
    for ciclo, caminho in _ciclos(prefixo):
        with open(caminho, encoding="utf-8") as f:
            blocos = json.load(f)
        inicios, perdidos = inicios_previstos(texto, blocos)
        prev = sorted(set(_para_normalizado(texto, inicios)))
        acertos = casar(prev, gab, tolerancia)
        linhas.append({
            "ciclo": ciclo,
            "blocos": len(blocos),
            "ancoras_nao_localizadas": perdidos,
            "fronteiras_previstas": len(prev),
            "fronteiras_gabarito": len(gab),
            "acertos": acertos,
            **metricas(acertos, len(prev), len(gab)),
        })
    return {
        "prefixo": prefixo,
        "gabarito": caminho_gabarito,
        "tolerancia": tolerancia,
        "ciclos": linhas,
    }


# ── Gráfico ────────────────────────────────────────────────────────────────────

# Slots 1–3 da paleta categórica de referência (validada para 3 séries).
_SERIES = [("precisao", "Precisão", "#2a78d6"),
           ("revocacao", "Revocação", "#eb6834"),
           ("f1", "F1", "#1baf7a")]


def plotar(resultado: dict, caminho: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ciclos = [l["ciclo"] for l in resultado["ciclos"]]
    nome = os.path.basename(resultado["prefixo"])

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    for chave, rotulo, cor in _SERIES:
        ys = [l[chave] for l in resultado["ciclos"]]
        ax.plot(ciclos, ys, color=cor, linewidth=2, marker="o", markersize=5,
                markeredgecolor="#fcfcfb", markeredgewidth=1.5, label=rotulo)

    # Rótulo direto só no último ciclo, empurrado para baixo se colidir com o de cima.
    finais = sorted(((resultado["ciclos"][-1][c], r) for c, r, _ in _SERIES), reverse=True)
    y_ant = None
    for y, rotulo in finais:
        y_txt = y if y_ant is None else min(y, y_ant - 0.05)
        ax.annotate(f"{rotulo} {y:.2f}", xy=(ciclos[-1], y_txt), xytext=(8, 0),
                    textcoords="offset points", va="center", fontsize=8,
                    color="#0b0b0b", annotation_clip=False)
        y_ant = y_txt

    ax.set_ylim(0, 1.02)
    ax.set_xticks(ciclos)
    ax.set_xlabel("Ciclo de correção", color="#52514e")
    ax.set_ylabel("Fronteiras", color="#52514e")
    tol = resultado["tolerancia"]
    ax.set_title(f"{nome} — fronteiras vs. gabarito"
                 + (f" (tolerância {tol} chars)" if tol else ""),
                 loc="left", fontsize=11, color="#0b0b0b")
    ax.grid(axis="y", color="#e4e3df", linewidth=0.8)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e", labelsize=8)
    ax.legend(loc="lower left", frameon=False, fontsize=8, ncol=3)

    fig.tight_layout()
    fig.savefig(caminho, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Avalia cada ciclo de uma execução contra o gabarito.")
    parser.add_argument("prefixos", nargs="+", help="Prefixo de cada execução (o --saida sem .json).")
    parser.add_argument("--gabarito", help="Gabarito explícito (só com um prefixo).")
    parser.add_argument("--tolerancia", type=int, default=0,
                        help="Distância máxima, em chars normalizados, para casar duas fronteiras.")
    parser.add_argument("--sem-grafico", action="store_true", help="Não gera o PNG.")
    args = parser.parse_args()

    if args.gabarito and len(args.prefixos) > 1:
        parser.error("--gabarito só vale com um único prefixo")

    for prefixo in args.prefixos:
        prefixo = re.sub(r"\.json$", "", prefixo)
        caminho_gab = args.gabarito or _gabarito_padrao(prefixo)
        if not os.path.exists(caminho_gab):
            print(f"✗ {prefixo}: gabarito não encontrado ({caminho_gab})")
            continue
        if not _ciclos(prefixo):
            print(f"✗ {prefixo}: nenhum {prefixo}_cicloN.json")
            continue
        try:
            resultado = avaliar(prefixo, caminho_gab, args.tolerancia)
        except ErroGabarito as e:
            print(f"✗ {caminho_gab}\n{e}")
            continue

        print(f"\n{prefixo}  ×  {caminho_gab}")
        print(f"  {'ciclo':>5} {'blocos':>6} {'prev':>5} {'gab':>5} {'acert':>5}  {'P':>5} {'R':>5} {'F1':>5}")
        for l in resultado["ciclos"]:
            print(f"  {l['ciclo']:>5} {l['blocos']:>6} {l['fronteiras_previstas']:>5} "
                  f"{l['fronteiras_gabarito']:>5} {l['acertos']:>5}  "
                  f"{l['precisao']:>5.3f} {l['revocacao']:>5.3f} {l['f1']:>5.3f}"
                  + (f"  ({l['ancoras_nao_localizadas']} âncora(s) não localizada(s))"
                     if l["ancoras_nao_localizadas"] else ""))

        with open(f"{prefixo}_avaliacao.json", "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)
        if not args.sem_grafico:
            plotar(resultado, f"{prefixo}_avaliacao.png")
            print(f"  → {prefixo}_avaliacao.json, {prefixo}_avaliacao.png")


if __name__ == "__main__":
    main()
