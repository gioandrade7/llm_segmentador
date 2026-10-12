"""
gabarito.py — Leitura e conferência de um gabarito em esboço indentado.

O gabarito (`gabarito/<doc>.md`) é uma árvore escrita à mão, uma linha por nó:

    - Documento
      - [capítulo] CAPÍTULO I
        - [artigo] Art. 1º O Sistema de Seleção Unificada

Cada nó guarda só o próprio texto: do início da sua âncora até o início da
âncora do nó seguinte, em ordem de documento. A indentação (2 espaços por
nível) dá a hierarquia. O texto-fonte vem da linha `Texto-fonte:` do cabeçalho.

O fim de um nó pode ser dado explicitamente, por uma segunda âncora depois de
` ⟶ ` — o final literal do último trecho de conteúdo do nó:

      - [seção] 1 Introduction ⟶ in the rest of the paper.

O texto entre esse fim e o início do nó seguinte é ruído (número de página,
rodapé, legenda) e não pertence a nó nenhum. Sem ` ⟶ `, o nó vai até o início
do seguinte, como antes. A âncora de fim é buscada entre o início do nó e o do
seguinte, e vale a última ocorrência nesse intervalo.

A âncora de fim é comparada em forma compacta: o texto normalizado do
`anchor.py` sem espaços, diacríticos soltos (`¨ ´ ¸`…) e marcadores `•`. É o
que dois extratores do mesmo PDF escrevem igual — eles espaçam pontuação e
fórmulas de jeitos diferentes (`Maliki)` × `_Maliki_ )`, `0.069` × `0 _._ 069`)
e põem o diacrítico solto em lugares diferentes (`Ju¨rgen` × `J¨urgen`). A
âncora de início usa primeiro o matching do pipeline (`anchor.py`) e, se ele
falhar, a mesma forma compacta. Se a ocorrência achada não abre uma linha e há,
na mesma página, outra que abre, vale esta: um título curto (`COPA`, `SWAG`)
costuma ser citado antes no corpo, numa tabela ou legenda, e o nó começa onde
ele abre a linha.

Uso:
    python gabarito.py gabarito/sisu-rules.md
    python gabarito.py gabarito/sisu-rules.md --trechos   # mostra o texto de cada nó
"""

import argparse
import contextlib
import io
import os
import re
from collections import Counter

from bisect import bisect_left, bisect_right

from anchor import _localizar_ancora, _normalizar, _normalizar_texto_cached
from utils_files import carregar_paginas, montar_texto_completo

RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TIPOS = {
    # normativos (sisu-rules)
    "epígrafe", "ementa", "preâmbulo", "capítulo", "seção", "artigo",
    "parágrafo", "inciso", "alínea", "item", "fecho",
    # artigo científico (conference)
    "título", "resumo", "palavras-chave", "subseção", "subsubseção", "referências", "referência",
    # regras com enumeração por família de marcador (ppb_rules)
    "letra", "número", "romano", "maiúscula", "letra-dupla", "continuação",
}

# Tipos que dizem a família do marcador, não o nível: `(1)` pode estar sob `(a)`
# ou sob `(iii)`, então entre si não há ordem a conferir — só abaixo de seção.
_FAMILIAS = {"letra", "número", "romano", "maiúscula", "letra-dupla", "continuação"}

# Ordem do mais grosso ao mais fino: um filho tem de ser mais fino que o pai.
# Inciso sob artigo ou sob parágrafo é válido; artigo sob parágrafo não. Tipos
# fora daqui (epígrafe, título, resumo…) são folhas diretas da raiz.
_ORDEM = {"capítulo": 1, "seção": 2, "referências": 2, "subseção": 3, "referência": 3, "subsubseção": 4,
          "artigo": 3, "parágrafo": 4, "inciso": 5, "alínea": 6, "item": 7,
          **{f: 8 for f in ("letra", "número", "romano", "maiúscula", "letra-dupla", "continuação")}}

_LINHA_NO = re.compile(r"^(?P<ind> *)- \[(?P<tipo>[^\]]+)\] (?P<ancora>.+?)(?: ⟶ (?P<ancora_fim>.+))?$")
_LINHA_RAIZ = re.compile(r"^- Documento\s*$")
_FONTE = re.compile(r"^Texto-fonte:\s*(?P<caminho>\S+)", re.M)


class ErroGabarito(Exception):
    pass


_compacto_cache: tuple = (None, None)

# Diacríticos soltos (não combinantes) e marcadores de lista: cada extrator os
# põe num lugar diferente, então ficam fora da forma compacta.
_IGNORAR = set("¨ˇ´`˜ˆ¸˚˘˙˝•")


def _chave(ancora: str) -> str:
    """Âncora na forma compacta (ver `_compacto`)."""
    return "".join(c for c in _normalizar(ancora)[0] if not c.isspace() and c not in _IGNORAR)


def _compacto(texto: str) -> tuple[str, list[int]]:
    """Texto normalizado sem espaços nem `_IGNORAR`, e a posição de cada char no original."""
    global _compacto_cache
    if _compacto_cache[0] is not texto:
        norm, mapa = _normalizar_texto_cached(texto)
        pares = [(c, mapa[i]) for i, c in enumerate(norm) if not c.isspace() and c not in _IGNORAR]
        _compacto_cache = (texto, ("".join(c for c, _ in pares), [m for _, m in pares]))
    return _compacto_cache[1]


def _localizar_fim(texto: str, ancora: str, inicio: int, limite: int) -> int:
    """
    Fim (exclusivo, no texto original) da última ocorrência de `ancora` dentro
    de `[inicio, limite)`, na forma compacta. -1 se não houver.
    """
    compacto, mapa = _compacto(texto)
    alvo = _chave(ancora)
    if not alvo:
        return -1
    a, b = bisect_left(mapa, inicio), bisect_right(mapa, limite - 1)
    i = compacto.rfind(alvo, a, b)
    return -1 if i == -1 else mapa[i + len(alvo) - 1] + 1


def _sem_comentarios(conteudo: str) -> list[str]:
    """
    Linhas do esboço sem comentários, mantendo a numeração original.

    Um bloco de comentário é aberto e fechado por uma linha contendo só `%%`
    (o cabeçalho); fora dele, `%%` inicia um comentário de fim de linha.
    """
    linhas, em_bloco = [], False
    for linha in conteudo.split("\n"):
        if linha.strip() == "%%":
            em_bloco = not em_bloco
            linhas.append("")
        elif em_bloco:
            linhas.append("")
        else:
            linhas.append(linha.split("%%")[0].rstrip())
    return linhas


def ler_esboco(caminho: str) -> tuple[str, list[dict]]:
    """
    Lê o esboço e devolve `(texto_fonte, nós)`.

    Cada nó: `{linha, tipo, profundidade, pai, ancora}`, em ordem de documento.
    `pai` é o índice do nó pai na lista, ou None para filhos diretos da raiz.
    Erros de sintaxe são acumulados e levantados juntos.
    """
    with open(caminho, encoding="utf-8") as f:
        bruto = f.read()

    m = _FONTE.search(bruto)
    if not m:
        raise ErroGabarito("cabeçalho sem linha 'Texto-fonte: <caminho das páginas>'")
    dir_paginas = os.path.join(RAIZ_REPO, m.group("caminho"))
    with contextlib.redirect_stdout(io.StringIO()):
        texto = montar_texto_completo(carregar_paginas(dir_paginas))

    erros, nos = [], []
    pilha: list[int] = []          # índices dos ancestrais abertos
    viu_raiz = False
    for n_linha, linha in enumerate(_sem_comentarios(bruto), start=1):
        if not linha.strip():
            continue
        if _LINHA_RAIZ.match(linha):
            viu_raiz = True
            continue
        m = _LINHA_NO.match(linha)
        if not m:
            erros.append(f"linha {n_linha}: fora do formato '- [tipo] âncora': {linha.strip()[:60]!r}")
            continue
        if not viu_raiz:
            erros.append(f"linha {n_linha}: nó antes de '- Documento'")

        ind = len(m.group("ind"))
        if ind % 2 or ind == 0:
            erros.append(f"linha {n_linha}: indentação de {ind} espaços (esperado múltiplo de 2, ≥ 2)")
            continue
        prof = ind // 2
        if prof > len(pilha) + 1:
            erros.append(f"linha {n_linha}: pula nível (profundidade {prof} logo após {len(pilha)})")
            continue

        tipo = m.group("tipo").strip()
        if tipo not in TIPOS:
            erros.append(f"linha {n_linha}: tipo desconhecido [{tipo}]")

        del pilha[prof - 1:]
        if pilha:
            tipo_pai = nos[pilha[-1]]["tipo"]
            if tipo_pai == "continuação":
                erros.append(f"linha {n_linha}: [continuação] não pode ter filhos")
            elif tipo in _FAMILIAS and tipo_pai in _FAMILIAS:
                pass
            elif _ORDEM.get(tipo, 0) <= _ORDEM.get(tipo_pai, 0):
                erros.append(f"linha {n_linha}: [{tipo}] não pode ser filho de [{tipo_pai}]")
        nos.append({
            "linha": n_linha,
            "tipo": tipo,
            "profundidade": prof,
            "pai": pilha[-1] if pilha else None,
            "ancora": m.group("ancora").strip(),
            "ancora_fim": (m.group("ancora_fim") or "").strip(),
        })
        pilha.append(len(nos) - 1)

    if erros:
        raise ErroGabarito("\n".join(erros))
    return texto, nos


def localizar_inicio(texto: str, ancora: str, pos: int) -> int:
    """Início da primeira ocorrência de `ancora` a partir de `pos`: `anchor.py`, depois forma compacta."""
    ini, _ = _localizar_ancora(texto, ancora, pos)
    if ini != -1:
        return ini
    compacto, mapa = _compacto(texto)
    alvo = _chave(ancora)
    i = compacto.find(alvo, bisect_left(mapa, pos)) if alvo else -1
    return -1 if i == -1 else mapa[i]


def abre_linha(texto: str, pos: int) -> bool:
    """`pos` abre uma linha, descontada a marcação que a precede (`- `, `#`, `**`, `>`)."""
    j = pos - 1
    while j >= 0 and texto[j] in " \t*#-_>":
        j -= 1
    return j < 0 or texto[j] == "\n"


def _preferir_inicio_de_linha(texto: str, ancora: str, ini: int) -> int:
    """A primeira ocorrência de `ancora` que abre uma linha, na página de `ini`; senão `ini`."""
    if abre_linha(texto, ini):
        return ini
    pagina, p = _pagina(texto, ini), ini + 1
    while (k := localizar_inicio(texto, ancora, p)) != -1 and _pagina(texto, k) == pagina:
        if abre_linha(texto, k):
            return k
        p = k + 1
    return ini


def resolver_ancoras(texto: str, nos: list[dict]) -> None:
    """
    Acrescenta `inicio`/`fim` (posições no texto-fonte) a cada nó.

    Cada âncora é buscada a partir do início da anterior, com o matching
    tolerante do `anchor.py` (ignora `**`, `<u>`, `#`, acentos, espaços). O
    `fim` de um nó é o fim da última ocorrência da sua `ancora_fim` antes do
    início do seguinte; sem `ancora_fim`, é o próprio início do seguinte (o do
    último nó é o fim do texto).
    """
    erros, pos = [], 0
    for no in nos:
        ini = localizar_inicio(texto, no["ancora"], pos)
        if ini == -1:
            erros.append(f"linha {no['linha']}: âncora não encontrada depois do nó anterior: {no['ancora']!r}")
            continue
        ini = _preferir_inicio_de_linha(texto, no["ancora"], ini)
        no["inicio"] = ini
        pos = ini + 1
    if erros:
        raise ErroGabarito("\n".join(erros))

    limites = [seguinte["inicio"] for seguinte in nos[1:]] + [len(texto)]
    for no, limite in zip(nos, limites):
        no["fim"] = limite
        if not no["ancora_fim"]:
            continue
        fim = _localizar_fim(texto, no["ancora_fim"], no["inicio"], limite)
        if fim == -1:
            erros.append(f"linha {no['linha']}: âncora de fim não encontrada antes do nó seguinte: {no['ancora_fim']!r}")
        else:
            no["fim"] = fim
    if erros:
        raise ErroGabarito("\n".join(erros))


def carregar_gabarito(caminho: str) -> tuple[str, list[dict]]:
    """Lê o esboço, confere a sintaxe e resolve as âncoras. Levanta ErroGabarito."""
    texto, nos = ler_esboco(caminho)
    resolver_ancoras(texto, nos)
    return texto, nos


def _pagina(texto: str, pos: int) -> int:
    marcadores = re.findall(r"<!-- PÁGINA (\d+) -->", texto[:pos + 1])
    return int(marcadores[-1]) if marcadores else 1


def main():
    parser = argparse.ArgumentParser(description="Confere um gabarito em esboço indentado contra o texto-fonte.")
    parser.add_argument("esboco", help="Arquivo .md do gabarito.")
    parser.add_argument("--trechos", action="store_true", help="Mostra o início e o fim do texto de cada nó.")
    args = parser.parse_args()

    try:
        texto, nos = carregar_gabarito(args.esboco)
    except ErroGabarito as e:
        print(f"✗ {args.esboco}\n{e}")
        raise SystemExit(1)

    print(f"✓ {args.esboco}: {len(nos)} nós, todas as âncoras encontradas em ordem")
    print(f"  profundidade máxima: {max(n['profundidade'] for n in nos)}")
    for tipo, qtd in Counter(n["tipo"] for n in nos).most_common():
        print(f"  {tipo:<10} {qtd:>4}")

    if args.trechos:
        for n in nos:
            trecho = " ".join(texto[n["inicio"]:n["fim"]].split())
            resumo = trecho if len(trecho) <= 110 else f"{trecho[:60]} … {trecho[-45:]}"
            print(f"\n{'  ' * n['profundidade']}[{n['tipo']}] pág. {_pagina(texto, n['inicio'])} · {n['fim'] - n['inicio']} chars")
            print(f"{'  ' * n['profundidade']}  {resumo}")


if __name__ == "__main__":
    main()
