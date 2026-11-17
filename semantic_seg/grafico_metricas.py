"""
grafico_metricas.py — média de todas as métricas de `avaliar.py` num conjunto de documentos.

Lê o `{KEY}_avaliacao.json` de cada subpasta de `--resultados` que tenha um (rode
`avaliar.py` antes); com `--manifesto`, só as normas listadas nele. Na amostra TCU o
manifesto padrão (`gabarito/tcu_amostra.json`) entra sozinho, pois há pastas de
normas que saíram da amostra. Gera dois gráficos de P/R/F1 de inícios, de fins e
de blocos exatos, e cobertura:
  - `{saida}`: média das normas no ciclo 1 (sem correção) e no ciclo final (o que o
    pipeline entrega: aprovação, nenhuma diretiva ou o teto), com o mín–máx no final;
  - `{saida}_ciclos`: média das normas em cada ciclo.

Uso:
    python grafico_metricas.py                       # → resultados/tcu/tcu_metricas{,_ciclos}.png
    python grafico_metricas.py --resultados resultados/lei_2017 --titulo "Leis de 2017" \
        --saida resultados/lei_2017/lei_2017_metricas.png
"""

import argparse
import json
import os

DIR = os.path.dirname(os.path.abspath(__file__))
# (grupo, rótulo, função que extrai o valor de um ciclo de `_avaliacao.json`)
METRICAS = [
    ("Inícios", "P", lambda c: c["precisao"]),
    ("Inícios", "R", lambda c: c["revocacao"]),
    ("Inícios", "F1", lambda c: c["f1"]),
    ("Fins", "P", lambda c: c["fins"]["precisao"]),
    ("Fins", "R", lambda c: c["fins"]["revocacao"]),
    ("Fins", "F1", lambda c: c["fins"]["f1"]),
    ("Blocos exatos", "P", lambda c: c["blocos_exatos"]["precisao"]),
    ("Blocos exatos", "R", lambda c: c["blocos_exatos"]["revocacao"]),
    ("Blocos exatos", "F1", lambda c: c["blocos_exatos"]["f1"]),
    ("Cobertura", "", lambda c: c["cobertura"]),
]
# Rampa ordinal de um só tom (azul 250/650): ciclo 1 → final é ordenado, não categórico.
COR_INICIAL, COR_FINAL = "#86b6ef", "#104281"
SUPERFICIE, TINTA, TINTA_2, GRADE, EIXO = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#c3c2b7"


def chaves(dir_resultados: str, manifesto: str | None) -> list[str]:
    """Subpastas com `_avaliacao.json`; com manifesto, só as normas listadas nele."""
    achadas = sorted(k for k in os.listdir(dir_resultados)
                     if os.path.isfile(os.path.join(dir_resultados, k, f"{k}_avaliacao.json")))
    if manifesto:
        with open(manifesto, encoding="utf-8") as f:
            listadas = {n["key"] for n in json.load(f)["normas"]}
        faltam = listadas - set(achadas)
        if faltam:
            raise SystemExit(f"Sem _avaliacao.json: {', '.join(sorted(faltam))}")
        achadas = [k for k in achadas if k in listadas]
    if not achadas:
        raise SystemExit(f"Nenhum _avaliacao.json em {dir_resultados}")
    return achadas


def ler_ciclos(dir_resultados: str, k: str) -> list[dict]:
    with open(os.path.join(dir_resultados, k, f"{k}_avaliacao.json"), encoding="utf-8") as f:
        return json.load(f)["ciclos"]


def coletar(dir_resultados: str, keys: list[str]) -> dict:
    """Por métrica: valores de cada norma no ciclo 1 e no último ciclo."""
    valores = {i: {"inicial": [], "final": []} for i in range(len(METRICAS))}
    ciclos_finais = []
    for k in keys:
        ciclos = ler_ciclos(dir_resultados, k)
        ciclos_finais.append(len(ciclos))
        for i, (_, _, extrair) in enumerate(METRICAS):
            valores[i]["inicial"].append(extrair(ciclos[0]))
            valores[i]["final"].append(extrair(ciclos[-1]))
    return {"valores": valores, "n": len(keys), "ciclos_finais": ciclos_finais}


def media(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def plotar(dados: dict, titulo: str, caminho: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    valores, n = dados["valores"], dados["n"]
    # Posições: barras agrupadas por métrica, com um respiro entre os grupos.
    xs, grupo_ant, x = [], None, 0.0
    for grupo, _, _ in METRICAS:
        if grupo_ant is not None and grupo != grupo_ant:
            x += 0.6
        xs.append(x)
        x += 1.0
        grupo_ant = grupo
    largura = 0.38

    fig, ax = plt.subplots(figsize=(11, 5), dpi=150)
    fig.patch.set_facecolor(SUPERFICIE)
    ax.set_facecolor(SUPERFICIE)

    for i, x in enumerate(xs):
        ini, fim = valores[i]["inicial"], valores[i]["final"]
        m_ini, m_fim = media(ini), media(fim)
        ax.bar(x - largura / 2 - 0.01, m_ini, largura, color=COR_INICIAL, zorder=2,
               label="ciclo 1" if i == 0 else None)
        ax.bar(x + largura / 2 + 0.01, m_fim, largura, color=COR_FINAL, zorder=2,
               label="ciclo final" if i == 0 else None)
        # Mínimo–máximo das normas no ciclo final.
        ax.vlines(x + largura / 2 + 0.01, min(fim), max(fim), color=TINTA_2, linewidth=1, zorder=3,
                  label="mín–máx das normas (final)" if i == 0 else None)
        # Valor dentro da barra, junto à base: no topo colidiria com a haste.
        ax.annotate(f"{m_fim:.2f}".replace(".", ","), xy=(x + largura / 2 + 0.01, 0),
                    xytext=(0, 4), textcoords="offset points", ha="center", va="bottom",
                    fontsize=7.5, color=SUPERFICIE, zorder=4)
        ax.annotate(f"{m_ini:.2f}".replace(".", ","), xy=(x - largura / 2 - 0.01, 0),
                    xytext=(0, 4), textcoords="offset points", ha="center", va="bottom",
                    fontsize=7.5, color=TINTA, zorder=4)

    ax.set_xticks(xs, [rot for _, rot, _ in METRICAS])
    # Nome do grupo sob os rótulos P/R/F1.
    for grupo in dict.fromkeys(g for g, _, _ in METRICAS):
        pos = [x for x, (g, _, _) in zip(xs, METRICAS) if g == grupo]
        ax.annotate(grupo, xy=(sum(pos) / len(pos), 0), xycoords=("data", "axes fraction"),
                    xytext=(0, -26), textcoords="offset points", ha="center", va="top",
                    fontsize=9, color=TINTA)

    ax.set_ylim(0, 1.08)
    ax.set_ylabel("Média das normas", color=TINTA_2)
    ax.grid(axis="y", color=GRADE, linewidth=0.8, zorder=0)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(EIXO)
    ax.tick_params(colors=TINTA_2, labelsize=8)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=3, frameon=False,
              fontsize=8, labelcolor=TINTA)

    finais = dados["ciclos_finais"]
    fig.suptitle(f"{titulo} ({n} normas) — média de todas as métricas, ciclo 1 vs. ciclo final",
                 x=0.01, ha="left", fontsize=11, color=TINTA)
    fig.text(0.01, 0.915,
             f"Ciclo final = último ciclo de cada norma (mediana {sorted(finais)[len(finais) // 2]}, "
             f"máx. {max(finais)}). Tolerância 0. Haste: mín–máx das normas no ciclo final.",
             fontsize=8, color=TINTA_2)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(caminho, facecolor=fig.get_facecolor())
    plt.close(fig)


def trajetorias(dir_resultados: str, keys: list[str]) -> tuple[dict, list[int], int]:
    """Por métrica: média das normas em cada ciclo, e quantas normas ainda rodavam em cada um.

    Como em `grafico_estratos.py`, uma norma que parou antes entra com o valor do seu
    último ciclo: é a segmentação que o pipeline entregaria se interrompido ali.
    """
    por_norma = [ler_ciclos(dir_resultados, k) for k in keys]
    teto = max(len(c) for c in por_norma)
    ativas = [sum(len(c) > i for c in por_norma) for i in range(teto)]
    medias = {}
    for i, (_, _, extrair) in enumerate(METRICAS):
        serie = [[extrair(c[min(j, len(c) - 1)]) for c in por_norma] for j in range(teto)]
        medias[i] = [media(v) for v in serie]
    return medias, ativas, len(por_norma)


def plotar_por_ciclo(medias: dict, ativas: list[int], n: int, titulo: str, caminho: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Paleta categórica, slots 1–3 (validados em todos os pares para daltonismo).
    # Cobertura é série única, nomeada pelo título do painel: tinta neutra, sem imitar "precisão".
    cores = {"P": "#2a78d6", "R": "#eb6834", "F1": "#1baf7a", "": TINTA_2}
    nomes = {"P": "precisão", "R": "revocação", "F1": "F1"}
    teto = len(ativas)
    ciclos = list(range(1, teto + 1))
    grupos = list(dict.fromkeys(g for g, _, _ in METRICAS))

    fig, eixos = plt.subplots(2, 2, figsize=(11, 8), dpi=150, sharey=True)
    eixos = eixos.ravel()
    fig.patch.set_facecolor(SUPERFICIE)
    piso = min(min(v) for v in medias.values())

    for ax, grupo in zip(eixos, grupos):
        ax.set_facecolor(SUPERFICIE)
        finais = []
        for i, (g, rot, _) in enumerate(METRICAS):
            if g != grupo:
                continue
            ax.plot(ciclos, medias[i], color=cores[rot], linewidth=2, marker="o", markersize=4.5,
                    markeredgecolor=SUPERFICIE, markeredgewidth=1.2, zorder=3,
                    label=nomes.get(rot) if grupo == grupos[0] else None)
            finais.append((medias[i][-1], f"{rot} {medias[i][-1]:.2f}".strip().replace(".", ",")))
            # Valor do ciclo 1 abaixo do ponto (as curvas sobem a partir dele), para ler o ganho.
            ax.annotate(f"{medias[i][0]:.2f}".replace(".", ","), xy=(1, medias[i][0]),
                        xytext=(0, -8), textcoords="offset points", ha="center", va="top",
                        fontsize=7, color=TINTA_2, annotation_clip=False)

        # Rótulo direto no último ciclo, empurrado para baixo se colidir com o de cima.
        y_ant = None
        for y, rotulo in sorted(finais, reverse=True):
            y_txt = y if y_ant is None else min(y, y_ant - 0.022)
            ax.annotate(rotulo, xy=(teto, y_txt), xytext=(7, 0), textcoords="offset points",
                        va="center", fontsize=7.5, color=TINTA, annotation_clip=False)
            y_ant = y_txt

        ax.set_title(grupo, loc="left", fontsize=10, color=TINTA)
        ax.set_xticks(ciclos, [str(c) if n == 1 else f"{c}\n({a})" for c, a in zip(ciclos, ativas)])
        ax.set_xlim(0.4, teto + 0.6)
        ax.grid(axis="y", color=GRADE, linewidth=0.8, zorder=0)
        for lado in ("top", "right"):
            ax.spines[lado].set_visible(False)
        for lado in ("left", "bottom"):
            ax.spines[lado].set_color(EIXO)
        ax.tick_params(colors=TINTA_2, labelsize=7.5)

    # Um documento só: não há média nem normas paradas a explicar.
    unico = n == 1
    for ax in eixos[::2]:
        ax.set_ylabel("Valor" if unico else "Média das normas", color=TINTA_2)
    eixos[0].set_ylim(max(0.0, piso - 0.05), 1.0)
    fig.supxlabel("Ciclo de correção" if unico else "Ciclo de correção (entre parênteses, normas ainda rodando)",
                  fontsize=9, color=TINTA_2, y=0.055)
    fig.legend(loc="lower center", ncol=3, frameon=False, fontsize=8, labelcolor=TINTA)
    fig.suptitle(f"{titulo}: métricas por ciclo" if unico else f"{titulo} ({n} normas): média das métricas por ciclo",
                 x=0.01, ha="left", fontsize=11, color=TINTA)
    fig.text(0.01, 0.945, ("" if unico else "Uma norma que parou antes entra com o valor do seu último ciclo. ")
             + "Tolerância 0. Sob o primeiro ponto, o valor no ciclo 1; à direita, no último.",
             fontsize=8, color=TINTA_2)
    fig.tight_layout(rect=(0, 0.06, 0.97, 0.94), h_pad=2.5, w_pad=3)
    fig.savefig(caminho, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    tcu = os.path.join(DIR, "resultados", "tcu")
    parser = argparse.ArgumentParser(description="Média de todas as métricas num conjunto de documentos.")
    parser.add_argument("--resultados", default=tcu, help="pasta com uma subpasta por documento")
    parser.add_argument("--manifesto", default=None,
                        help="só as normas listadas (padrão: gabarito/tcu_amostra.json na pasta do TCU)")
    parser.add_argument("--titulo", default="Amostra TCU")
    parser.add_argument("--saida", default=os.path.join(tcu, "tcu_metricas.png"))
    args = parser.parse_args()

    if args.manifesto is None and os.path.abspath(args.resultados) == tcu:
        args.manifesto = os.path.join(DIR, "gabarito", "tcu_amostra.json")
    keys = chaves(args.resultados, args.manifesto)
    dados = coletar(args.resultados, keys)
    for i, (grupo, rot, _) in enumerate(METRICAS):
        v = dados["valores"][i]
        print(f"{grupo:14} {rot:3} ciclo 1 {media(v['inicial']):.3f}   final {media(v['final']):.3f}")
    plotar(dados, args.titulo, args.saida)
    print(f"Gráfico salvo em: {args.saida}")

    medias, ativas, n = trajetorias(args.resultados, keys)
    saida_ciclos = os.path.splitext(args.saida)[0] + "_ciclos.png"
    plotar_por_ciclo(medias, ativas, n, args.titulo, saida_ciclos)
    print(f"Gráfico por ciclo salvo em: {saida_ciclos}")


if __name__ == "__main__":
    main()
