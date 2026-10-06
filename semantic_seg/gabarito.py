"""
gabarito.py — Leitura e conferência de um gabarito em esboço indentado.

O gabarito (`gabarito/<doc>.md`) é uma árvore escrita à mão, uma linha por nó:

    - Documento
      - [capítulo] CAPÍTULO I
        - [artigo] Art. 1º O Sistema de Seleção Unificada

Cada nó guarda só o próprio texto: do início da sua âncora até o início da
âncora do nó seguinte, em ordem de documento. A indentação (2 espaços por
nível) dá a hierarquia. O texto-fonte vem da linha `Texto-fonte:` do cabeçalho.

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

from anchor import _localizar_ancora
from utils_files import carregar_paginas, montar_texto_completo

RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TIPOS = {
    # normativos (sisu-rules)
    "epígrafe", "ementa", "preâmbulo", "capítulo", "seção", "artigo",
    "parágrafo", "inciso", "alínea", "item", "fecho",
    # artigo científico (conference)
    "título", "resumo", "palavras-chave", "subseção", "referências", "referência",
    # regras com enumeração por família de marcador (ppb_rules)
    "letra", "número", "romano", "maiúscula", "letra-dupla", "continuação",
}

# Tipos que dizem a família do marcador, não o nível: `(1)` pode estar sob `(a)`
# ou sob `(iii)`, então entre si não há ordem a conferir — só abaixo de seção.
_FAMILIAS = {"letra", "número", "romano", "maiúscula", "letra-dupla", "continuação"}

# Ordem do mais grosso ao mais fino: um filho tem de ser mais fino que o pai.
# Inciso sob artigo ou sob parágrafo é válido; artigo sob parágrafo não. Tipos
# fora daqui (epígrafe, título, resumo…) são folhas diretas da raiz.
_ORDEM = {"capítulo": 1, "seção": 2, "referências": 2, "subseção": 3, "referência": 3,
          "artigo": 3, "parágrafo": 4, "inciso": 5, "alínea": 6, "item": 7,
          **{f: 8 for f in ("letra", "número", "romano", "maiúscula", "letra-dupla", "continuação")}}

_LINHA_NO = re.compile(r"^(?P<ind> *)- \[(?P<tipo>[^\]]+)\] (?P<ancora>.+)$")
_LINHA_RAIZ = re.compile(r"^- Documento\s*$")
_FONTE = re.compile(r"^Texto-fonte:\s*(?P<caminho>\S+)", re.M)


class ErroGabarito(Exception):
    pass


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
        })
        pilha.append(len(nos) - 1)

    if erros:
        raise ErroGabarito("\n".join(erros))
    return texto, nos


def resolver_ancoras(texto: str, nos: list[dict]) -> None:
    """
    Acrescenta `inicio`/`fim` (posições no texto-fonte) a cada nó.

    Cada âncora é buscada a partir do início da anterior, com o matching
    tolerante do `anchor.py` (ignora `**`, `<u>`, `#`, acentos, espaços). O
    `fim` de um nó é o `inicio` do seguinte; o do último é o fim do texto.
    """
    erros, pos = [], 0
    for no in nos:
        ini, _ = _localizar_ancora(texto, no["ancora"], pos)
        if ini == -1:
            erros.append(f"linha {no['linha']}: âncora não encontrada depois do nó anterior: {no['ancora']!r}")
            continue
        no["inicio"] = ini
        pos = ini + 1
    if erros:
        raise ErroGabarito("\n".join(erros))

    for atual, seguinte in zip(nos, nos[1:]):
        atual["fim"] = seguinte["inicio"]
    if nos:
        nos[-1]["fim"] = len(texto)


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
