"""
grafico_estratos.py — F1 de blocos exatos por ciclo, agregado por estrato da amostra TCU.

Lê o manifesto (`gabarito/tcu_amostra.json`) e o `{KEY}_avaliacao.json` de cada
norma em `resultados/tcu/<KEY>/` (rode `avaliar.py` antes). Um painel por
profundidade (plana, profunda); em cada um, a média de cada faixa de tamanho e,
ao fundo, a trajetória de cada norma.

Cada norma para num ciclo diferente (aprovação, nenhuma diretiva ou o teto).
Depois de parar, ela entra na média com o valor do seu último ciclo: é a
segmentação que o pipeline entregaria se fosse interrompido naquele ciclo.

Uso:
    python grafico_estratos.py                       # → resultados/tcu/tcu_estratos.png
    python grafico_estratos.py --saida outro.png
"""

import argparse
import json
import os

DIR = os.path.dirname(os.path.abspath(__file__))
TAMANHOS = ["pequena", "média", "grande"]
PROFUNDIDADES = ["plana", "profunda"]
# Rampa ordinal de um só tom (azul 250/450/650): tamanho é ordenado, não categórico.
CORES = {"pequena": "#86b6ef", "média": "#2a78d6", "grande": "#104281"}
SUPERFICIE, TINTA, TINTA_2, GRADE, EIXO, CONTEXTO = \
    "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#c3c2b7", "#d6d5cf"


def trajetorias(dir_resultados: str, manifesto: dict) -> tuple[list[dict], int]:
    """Por norma: estrato e F1 de blocos exatos por ciclo, estendido até o último ciclo da amostra."""
    normas = []
    for n in manifesto["normas"]:
        k = n["key"]
        with open(os.path.join(dir_resultados, k, f"{k}_avaliacao.json"), encoding="utf-8") as f:
            ciclos = json.load(f)["ciclos"]
        normas.append({"key": k, "profundidade": n["profundidade"], "tamanho": n["tamanho"],
                       "f1": [c["blocos_exatos"]["f1"] for c in ciclos]})
    teto = max(len(n["f1"]) for n in normas)
    for n in normas:
        n["parou"] = len(n["f1"])
        n["f1"] = n["f1"] + [n["f1"][-1]] * (teto - len(n["f1"]))
    return normas, teto


def plotar(normas: list[dict], teto: int, caminho: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ciclos = list(range(1, teto + 1))
    fig, eixos = plt.subplots(1, 2, figsize=(11, 4.8), dpi=150, sharey=True)
    fig.patch.set_facecolor(SUPERFICIE)

    for ax, prof in zip(eixos, PROFUNDIDADES):
        ax.set_facecolor(SUPERFICIE)
        do_painel = [n for n in normas if n["profundidade"] == prof]
        # Contexto: cada norma, recessiva, atrás das médias.
        for n in do_painel:
            ax.plot(ciclos, n["f1"], color=CONTEXTO, linewidth=1, zorder=1)

        finais = []
        for tam in TAMANHOS:
            grupo = [n for n in do_painel if n["tamanho"] == tam]
            if not grupo:
                continue
            media = [sum(n["f1"][i] for n in grupo) / len(grupo) for i in range(teto)]
            ax.plot(ciclos, media, color=CORES[tam], linewidth=2, marker="o", markersize=5,
                    markeredgecolor=SUPERFICIE, markeredgewidth=1.5, zorder=3,
                    label=f"{tam} (n={len(grupo)})")
            finais.append((media[-1], f"{tam} {media[-1]:.2f}".replace(".", ",")))

        # Rótulo direto no último ciclo, empurrado para baixo se colidir com o de cima.
        y_ant = None
        for y, rotulo in sorted(finais, reverse=True):
            y_txt = y if y_ant is None else min(y, y_ant - 0.025)
            ax.annotate(rotulo, xy=(teto, y_txt), xytext=(8, 0), textcoords="offset points",
                        va="center", fontsize=8, color=TINTA, annotation_clip=False)
            y_ant = y_txt

        ax.set_title(f"{prof} (n={len(do_painel)})", loc="left", fontsize=10, color=TINTA)
        ax.set_xticks(ciclos)
        ax.set_xlabel("Ciclo de correção", color=TINTA_2)
        ax.grid(axis="y", color=GRADE, linewidth=0.8)
        for lado in ("top", "right"):
            ax.spines[lado].set_visible(False)
        for lado in ("left", "bottom"):
            ax.spines[lado].set_color(EIXO)
        ax.tick_params(colors=TINTA_2, labelsize=8)
        ax.legend(loc="lower right", frameon=False, fontsize=8, title="tamanho",
                  title_fontsize=8, labelcolor=TINTA)

    eixos[0].set_ylabel("F1 de blocos exatos", color=TINTA_2)
    piso = min(min(n["f1"]) for n in normas)
    eixos[0].set_ylim(max(0.0, piso - 0.05), 1.0)
    fig.suptitle(f"Amostra TCU ({len(normas)} normas) — F1 de blocos exatos por ciclo, média por estrato",
                 x=0.01, ha="left", fontsize=11, color=TINTA)
    fig.text(0.01, 0.915, "Cinza: cada norma. Uma norma que parou antes entra com o valor do seu último ciclo.",
             fontsize=8, color=TINTA_2)
    fig.tight_layout(rect=(0, 0, 0.97, 0.93))
    fig.savefig(caminho, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="F1 por ciclo, agregado por estrato da amostra TCU.")
    parser.add_argument("--manifesto", default=os.path.join(DIR, "gabarito", "tcu_amostra.json"))
    parser.add_argument("--resultados", default=os.path.join(DIR, "resultados", "tcu"))
    parser.add_argument("--saida", default=os.path.join(DIR, "resultados", "tcu", "tcu_estratos.png"))
    args = parser.parse_args()

    with open(args.manifesto, encoding="utf-8") as f:
        manifesto = json.load(f)
    normas, teto = trajetorias(args.resultados, manifesto)
    plotar(normas, teto, args.saida)
    print(f"Gráfico salvo em: {args.saida}")


if __name__ == "__main__":
    main()
