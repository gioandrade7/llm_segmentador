"""
rodar_rodada.py — Roda `main.py` e `avaliar.py` em lote, um processo por documento.

Cobre as normas de `gabarito/tcu_amostra.json`, as leis de 2017 com gabarito, o edital
e a `sec-rules`, na mesma organização de `resultados_5ciclos/`: `{raiz}/{grupo}/{KEY}/{KEY}`, com a saída de
cada processo em `{KEY}.log` e início e fim de cada documento em `{raiz}/status.txt`.
Os documentos mais longos entram primeiro, porque determinam a duração da rodada.

Uma rodada interrompida retoma de onde parou: documento com `_avaliacao.json` é
pulado, e documento já segmentado só é avaliado. Como `main.py` grava no fim, um
processo que caiu não deixa resultado parcial e recomeça do zero.

Uso:
    python rodar_rodada.py --raiz resultados_rodada --simular    # só lista os comandos
    python rodar_rodada.py --raiz resultados_rodada
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DIR = os.path.dirname(os.path.abspath(__file__))
PAGINAS = os.path.join(DIR, "..", "text_extraction", "out")
_trava = threading.Lock()


def documentos() -> list[tuple[str, str, str]]:
    """`(grupo, key, pasta das páginas)` de cada documento, do mais longo ao mais curto."""
    with open(os.path.join(DIR, "gabarito", "tcu_amostra.json"), encoding="utf-8") as f:
        docs = [("tcu", n["key"], os.path.join(PAGINAS, "tcu", n["key"])) for n in json.load(f)["normas"]]
    for gabarito in glob.glob(os.path.join(DIR, "gabarito", "LEI-2017-*.md")):
        key = os.path.splitext(os.path.basename(gabarito))[0]
        docs.append(("lei_2017", key, os.path.join(PAGINAS, "lei_2017", key)))
    for key in ("edital-inep-064", "sec-rules"):
        docs.append((key, key, os.path.join(PAGINAS, key, "paginas")))
    return sorted(docs, key=lambda d: -len(os.listdir(d[2])))


def registrar(raiz: str, mensagem: str) -> None:
    linha = f"{time.strftime('%H:%M:%S')} {mensagem}"
    print(linha, flush=True)
    with _trava, open(os.path.join(raiz, "status.txt"), "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def comandos(doc: tuple[str, str, str], raiz: str, janela: int, max_ciclos: int, amostra: int) -> tuple[str, list[list[str]]]:
    """Prefixo do documento e o que falta rodar para ele."""
    grupo, key, paginas = doc
    prefixo = os.path.join(raiz, grupo, key, key)
    if os.path.exists(f"{prefixo}_avaliacao.json"):
        return prefixo, []
    segmentar = [sys.executable, "main.py", "--diretorio", paginas, "--saida", f"{prefixo}.json",
                 "--janela-paginas", str(janela), "--max-ciclos", str(max_ciclos),
                 "--paginas-amostra", str(amostra)]
    avaliar = [sys.executable, "avaliar.py", prefixo]
    return prefixo, ([avaliar] if os.path.exists(f"{prefixo}.json") else [segmentar, avaliar])


def rodar(doc: tuple[str, str, str], raiz: str, janela: int, max_ciclos: int, amostra: int) -> int:
    """Roda o que falta do documento e devolve o código de saída do primeiro passo que falhar."""
    prefixo, passos = comandos(doc, raiz, janela, max_ciclos, amostra)
    nome = f"{doc[0]}/{doc[1]}"
    if not passos:
        registrar(raiz, f"pulado {nome}")
        return 0

    os.makedirs(os.path.dirname(prefixo), exist_ok=True)
    registrar(raiz, f"início {nome}")
    codigo = 0
    with open(f"{prefixo}.log", "a", encoding="utf-8") as log:
        for passo in passos:
            codigo = subprocess.run(passo, cwd=DIR, stdout=log, stderr=subprocess.STDOUT).returncode
            if codigo:
                break
    registrar(raiz, f"fim    {nome} exit={codigo}")
    return codigo


def main():
    parser = argparse.ArgumentParser(description="Roda o pipeline e a avaliação em lote.")
    parser.add_argument("--raiz", required=True, help="pasta da rodada, relativa a semantic_seg/")
    parser.add_argument("--janela-paginas", default=10, type=int)
    parser.add_argument("--max-ciclos", default=5, type=int)
    parser.add_argument("--paginas-amostra", default=2, type=int,
                        help="páginas por região (começo/meio/fim) lidas pelo identificador")
    parser.add_argument("--paralelo", default=31, type=int, help="documentos ao mesmo tempo")
    parser.add_argument("--simular", action="store_true", help="lista os comandos sem rodar nada")
    args = parser.parse_args()

    docs = documentos()
    if args.simular:
        for doc in docs:
            _, passos = comandos(doc, args.raiz, args.janela_paginas, args.max_ciclos, args.paginas_amostra)
            print(f"{doc[0]}/{doc[1]}: {' && '.join(' '.join(p[1:]) for p in passos) or 'pulado'}")
        return

    os.makedirs(os.path.join(DIR, args.raiz), exist_ok=True)
    os.chdir(DIR)
    with ThreadPoolExecutor(args.paralelo) as pool:
        codigos = list(pool.map(lambda d: rodar(d, args.raiz, args.janela_paginas, args.max_ciclos, args.paginas_amostra), docs))
    falhas = [f"{d[0]}/{d[1]}" for d, c in zip(docs, codigos) if c]
    registrar(args.raiz, f"TODOS TERMINARAM ({len(docs) - len(falhas)} ok, {len(falhas)} com erro)")
    if falhas:
        print("Com erro:", ", ".join(falhas))
        sys.exit(1)


if __name__ == "__main__":
    main()
