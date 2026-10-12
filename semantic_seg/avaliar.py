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

Os inícios sozinhos não veem um bloco que termina cedo demais: o texto entre o
fim dele e o início do seguinte fica sem bloco e nada acusa. Por isso há
também três medidas pelo fim:

- **fins** — P/R/F1 do fim de cada bloco (`offset_fim`) contra o fim de cada
  nó. O fim previsto casa se cair entre o fim do nó e o início do seguinte: o
  que há nesse intervalo é ruído (número de página, rodapé), e terminar antes
  ou depois dele é igualmente certo. Os fins são comparados no último char
  alfanumérico, e a tolerância alarga o intervalo nos dois lados. Nós sem
  âncora de fim no gabarito (` ⟶ `) terminam no início do seguinte.
- **blocos exatos** — P/R/F1 de blocos cujo início e fim casam com o mesmo nó.
- **cobertura** — fração dos chars alfanuméricos de conteúdo do gabarito (o
  texto dentro dos nós, sem o ruído) que caem dentro de algum bloco previsto.

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


def spans_previstos(texto: str, blocos: list[dict]) -> tuple[list[tuple[int, int]], int]:
    """
    Posições `(início, fim)` de cada bloco no texto-fonte, refazendo o ponteiro
    do pipeline: `offset_inicio` é buscado a partir do fim do bloco anterior
    (com fallback para logo após o início anterior) e `offset_fim` a partir do
    início. `fim` é -1 quando o `offset_fim` não é localizado. Devolve
    `(spans, perdidos)`, com os blocos cujo início não é localizado contados
    em `perdidos`.
    """
    spans, perdidos = [], 0
    pos_fim = pos_ini_ant = 0
    for b in blocos:
        ini, _ = _localizar_ancora(texto, b.get("offset_inicio") or "", pos_fim)
        if ini == -1:
            ini, _ = _localizar_ancora(texto, b.get("offset_inicio") or "", pos_ini_ant)
        if ini == -1:
            perdidos += 1
            continue
        pos_ini_ant = ini + 1
        _, fim = _localizar_ancora(texto, b.get("offset_fim") or "", ini, preferir_sufixo=True)
        spans.append((ini, fim))
        pos_fim = fim if fim != -1 else ini + 1
    return spans, perdidos


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


def _fim_normalizado(texto: str, posicoes: list[int]) -> list[int]:
    """
    Índice normalizado do último char alfanumérico antes de cada posição (fim
    exclusivo) — o espelho de `_para_normalizado`: `Art. 2º.` e `Art. 2º`
    terminam no mesmo ponto. -1 se não houver nenhum.
    """
    norm, mapa = _normalizar_texto_cached(texto)
    saida = []
    for p in posicoes:
        i = bisect_left(mapa, p) - 1
        while i >= 0 and not norm[i].isalnum():
            i -= 1
        saida.append(i)
    return saida


def _intervalos_de_fim(texto: str, nos: list[dict]) -> list[tuple[int, int]]:
    """
    Para cada nó, o intervalo normalizado `[fim, limite]` em que um fim previsto
    acerta: do último char de conteúdo do nó até o último char antes do início
    do nó seguinte (o ruído entre os dois).
    """
    norm, _ = _normalizar_texto_cached(texto)
    fins = _fim_normalizado(texto, [n["fim"] for n in nos])
    proximos = _para_normalizado(texto, [n["inicio"] for n in nos[1:]]) + [len(norm)]
    return [(f, max(f, prox - 1)) for f, prox in zip(fins, proximos)]


def _distancia(p: int, intervalo: tuple[int, int]) -> int:
    a, b = intervalo
    return 0 if a <= p <= b else min(abs(p - a), abs(p - b))


def casar_fins(previstos: list[int], intervalos: list[tuple[int, int]], tolerancia: int) -> dict[int, int]:
    """Casamento um-para-um, guloso pela menor distância: {índice previsto: índice do nó}."""
    pares = sorted(
        (d, i, j)
        for i, p in enumerate(previstos)
        for j, iv in enumerate(intervalos)
        if (d := _distancia(p, iv)) <= tolerancia
    )
    casados, usados_g = {}, set()
    for _, i, j in pares:
        if i not in casados and j not in usados_g:
            casados[i] = j
            usados_g.add(j)
    return casados


def cobertura(texto: str, nos: list[dict], spans: list[tuple[int, int]]) -> float:
    """Fração dos chars alfanuméricos de conteúdo do gabarito dentro de algum bloco."""
    norm, mapa = _normalizar_texto_cached(texto)

    def marcar(intervalos):
        dentro = bytearray(len(norm))
        for ini, fim in intervalos:
            dentro[bisect_left(mapa, ini):bisect_left(mapa, fim)] = b"\x01" * (
                bisect_left(mapa, fim) - bisect_left(mapa, ini))
        return dentro

    conteudo = marcar((n["inicio"], n["fim"]) for n in nos)
    coberto = marcar((ini, fim) for ini, fim in spans if fim != -1)
    total = acertos = 0
    for i, c in enumerate(norm):
        if conteudo[i] and c.isalnum():
            total += 1
            acertos += coberto[i]
    return acertos / total if total else 0.0


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
    inicios_gab = _para_normalizado(texto, [n["inicio"] for n in nos])
    gab = sorted(set(inicios_gab))
    intervalos = _intervalos_de_fim(texto, nos)

    linhas = []
    for ciclo, caminho in _ciclos(prefixo):
        with open(caminho, encoding="utf-8") as f:
            blocos = json.load(f)
        spans, perdidos = spans_previstos(texto, blocos)
        inicios = _para_normalizado(texto, [ini for ini, _ in spans])
        prev = sorted(set(inicios))
        acertos = casar(prev, gab, tolerancia)

        # Fins: só os blocos cujo `offset_fim` foi localizado concorrem.
        com_fim = [k for k, (_, fim) in enumerate(spans) if fim != -1]
        fins = _fim_normalizado(texto, [spans[k][1] for k in com_fim])
        casados = casar_fins(fins, intervalos, tolerancia)
        # Bloco exato: o fim casou com o nó j e o início coincide com o de j.
        exatos = sum(1 for i, j in casados.items()
                     if abs(inicios[com_fim[i]] - inicios_gab[j]) <= tolerancia)

        linhas.append({
            "ciclo": ciclo,
            "blocos": len(blocos),
            "ancoras_nao_localizadas": perdidos,
            "fronteiras_previstas": len(prev),
            "fronteiras_gabarito": len(gab),
            "acertos": acertos,
            **metricas(acertos, len(prev), len(gab)),
            "fins": {"previstos": len(fins), "gabarito": len(nos),
                     "fins_nao_localizados": len(spans) - len(com_fim),
                     "acertos": len(casados), **metricas(len(casados), len(fins), len(nos))},
            "blocos_exatos": {"acertos": exatos, **metricas(exatos, len(spans), len(nos))},
            "cobertura": cobertura(texto, nos, spans),
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
        print(f"  {'':>5} {'':>6} {'':>5} {'':>5} {'início':^23}  {'fim':^17}  {'bloco':^5}")
        print(f"  {'ciclo':>5} {'blocos':>6} {'prev':>5} {'gab':>5} {'acert':>5}  {'P':>5} {'R':>5} {'F1':>5}"
              f"  {'P':>5} {'R':>5} {'F1':>5}  {'F1':>5}  {'cobert':>6}")
        for l in resultado["ciclos"]:
            fi, bl = l["fins"], l["blocos_exatos"]
            print(f"  {l['ciclo']:>5} {l['blocos']:>6} {l['fronteiras_previstas']:>5} "
                  f"{l['fronteiras_gabarito']:>5} {l['acertos']:>5}  "
                  f"{l['precisao']:>5.3f} {l['revocacao']:>5.3f} {l['f1']:>5.3f}  "
                  f"{fi['precisao']:>5.3f} {fi['revocacao']:>5.3f} {fi['f1']:>5.3f}  "
                  f"{bl['f1']:>5.3f}  {l['cobertura']:>6.1%}"
                  + (f"  ({l['ancoras_nao_localizadas']} âncora(s) não localizada(s))"
                     if l["ancoras_nao_localizadas"] else "")
                  + (f"  ({fi['fins_nao_localizados']} fim(ns) não localizado(s))"
                     if fi["fins_nao_localizados"] else ""))

        with open(f"{prefixo}_avaliacao.json", "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)
        if not args.sem_grafico:
            plotar(resultado, f"{prefixo}_avaliacao.png")
            print(f"  → {prefixo}_avaliacao.json, {prefixo}_avaliacao.png")


if __name__ == "__main__":
    main()
