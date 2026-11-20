"""
pdf_extract.py — Extrator rápido de PDF → Markdown por página.

Por que existe
══════════════
Substitui o pipeline `marker_test.py` (Marker DL + GPT-4o repair), que era
lento e perdia conteúdo em layouts multi-coluna densos do DOU. Em testes,
até 13% das publicações de uma edição do DOU sumiam do markdown gerado.

Estratégia
══════════
PDFs do DOU (e a grande maioria dos PDFs oficiais) possuem **camada de
texto nativa** — não precisam de OCR. Esse extrator lê o texto direto da
camada, evitando o gargalo (e os erros) do OCR.

Três caminhos de extração, em ordem de preferência:
  1. **pymupdf4llm** — converte cada página para Markdown com detecção
     automática de colunas, listas e tabelas. Melhor qualidade.
  2. **PyMuPDF puro + sort por colunas** — extrai blocos com coordenadas
     e os reordena. Fallback se pymupdf4llm não estiver instalado.
  3. **PyMuPDF puro `get_text()`** — fallback do fallback, ordem de stream
     do PDF (geralmente já correta).

Saída
═════
Idêntica à do `marker_test.py`:
  - {output_dir}/page_NNNN.md     (um arquivo por página, 0-indexado, 4 dígitos)
  - {output_dir}/documento_final.md  (concatenação com '\n\n---\n\n' entre páginas)

Características
═══════════════
  - **Resume automático**: pula páginas já presentes em output_dir.
  - **Paralelização opcional**: --workers N usa ProcessPoolExecutor.
  - **Sem chamadas de rede, sem GPU, sem custo de API.**
  - **Detecção de páginas escaneadas**: avisa quando uma página retorna
    < MIN_PAGE_CHARS — sinal de que precisaria de OCR.

Requisitos
══════════
    pip install pymupdf4llm
    (pymupdf é dependência transitiva; já presente no projeto.)

Uso
═══
    # Modo padrão (1 worker, resume ativado)
    python pdf_extract.py -i arquivo.pdf -o saida/

    # Paralelo
    python pdf_extract.py -i arquivo.pdf -o saida/ --workers 8

    # Reprocessar do zero
    python pdf_extract.py -i arquivo.pdf -o saida/ --no-resume

    # Apenas as páginas, sem documento_final.md
    python pdf_extract.py -i arquivo.pdf -o saida/ --no-final

API
═══
    from text_extraction.pdf_extract import extract_pdf
    resultados = extract_pdf("arquivo.pdf", "saida/", workers=4)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import fitz  # PyMuPDF

try:
    import pymupdf4llm
    _HAS_PYMUPDF4LLM = True
except ImportError:
    _HAS_PYMUPDF4LLM = False


# ── Constantes ────────────────────────────────────────────────────────────────

MIN_PAGE_CHARS = 100  # avisa se uma página extrai menos que isso (provável OCR-needed)
PAGE_SEPARATOR = "\n\n---\n\n"  # mesmo separador usado pelo marker_test.py


# ── Estratégias de extração ──────────────────────────────────────────────────

def _extract_pymupdf4llm(pdf_path: str, page_nums: list[int]) -> dict[int, str]:
    """
    Extrai um conjunto de páginas usando pymupdf4llm.

    Esta é a estratégia preferida: converte cada página para Markdown limpo,
    com detecção automática de colunas, listas e tabelas. Uma única chamada
    processa todas as páginas do chunk (mais eficiente que uma por uma).
    """
    chunks = pymupdf4llm.to_markdown(
        pdf_path,
        pages=page_nums,
        page_chunks=True,
        show_progress=False,
        write_images=False,
        embed_images=False,
    )
    # `page_chunks=True` retorna lista de dicts {text, metadata, ...}
    # alinhada com a ordem de `page_nums`.
    return {pn: (c.get("text") or "").strip() for pn, c in zip(page_nums, chunks)}


def _extract_blocks(pdf_path: str, page_nums: list[int]) -> dict[int, str]:
    """
    Fallback: PyMuPDF puro com ordenação de blocos por coluna e linha.

    Para layouts multi-coluna comuns no DOU (2-3 colunas), ordena os blocos
    de texto da esquerda para a direita, depois de cima para baixo.
    A heurística de coluna usa o centróide x de cada bloco contra divisões
    igualmente espaçadas da largura da página.
    """
    out: dict[int, str] = {}
    with fitz.open(pdf_path) as doc:
        for pn in page_nums:
            page = doc[pn]
            blocks = [b for b in page.get_text("blocks") if b[6] == 0]

            if not blocks:
                out[pn] = ""
                continue

            # Detecta nº de colunas via clustering simples dos x0 (centróides)
            n_cols = _estimar_colunas([b[0] for b in blocks], page.rect.width)
            col_width = page.rect.width / n_cols

            def chave(b):
                x0, y0 = b[0], b[1]
                col_idx = min(int(x0 / col_width), n_cols - 1)
                # quantiza y dentro da coluna para tolerar pequenas variações
                return (col_idx, round(y0))

            blocks.sort(key=chave)
            out[pn] = "\n\n".join(b[4].strip() for b in blocks if b[4].strip())

    return out


def _estimar_colunas(xs: list[float], page_width: float) -> int:
    """
    Estima nº de colunas via dispersão dos x0 dos blocos.

    Heurística simples: agrupa x0 em buckets de 50px e conta picos.
    Tipicamente retorna 1, 2 ou 3 para PDFs do DOU.
    """
    if not xs:
        return 1
    bucket_w = 50
    buckets: dict[int, int] = {}
    for x in xs:
        b = int(x // bucket_w)
        buckets[b] = buckets.get(b, 0) + 1

    # Picos = buckets com pelo menos 2 blocos, separados por gaps
    picos = sorted(b for b, n in buckets.items() if n >= 2)
    if len(picos) <= 1:
        return 1
    # Conta agrupamentos com gap entre eles
    cols = 1
    for i in range(1, len(picos)):
        if picos[i] - picos[i - 1] >= 2:  # gap mínimo de 2 buckets (~100px)
            cols += 1
    # DOU tem no máximo ~4 colunas
    return min(cols, 4)


def _extract_plain(pdf_path: str, page_nums: list[int]) -> dict[int, str]:
    """
    Fallback do fallback: `get_text()` puro, sem ordenação manual.
    Funciona bem quando a ordem do stream do PDF já é correta.
    """
    out: dict[int, str] = {}
    with fitz.open(pdf_path) as doc:
        for pn in page_nums:
            out[pn] = doc[pn].get_text().strip()
    return out


# Estratégias por nome. `auto` é a escolha da inicialização: pymupdf4llm se
# instalado, senão blocos. As outras forçam uma estratégia — `texto` serve, por
# exemplo, a páginas que o pymupdf4llm lê como "picture text" e cola numa linha só.
ESTRATEGIAS = {
    "markdown": _extract_pymupdf4llm,
    "blocos": _extract_blocks,
    "texto": _extract_plain,
}


def _resolver_estrategia(estrategia: str) -> str:
    if estrategia == "auto":
        return "markdown" if _HAS_PYMUPDF4LLM else "blocos"
    if estrategia == "markdown" and not _HAS_PYMUPDF4LLM:
        raise RuntimeError("estratégia 'markdown' pedida, mas pymupdf4llm não está instalado")
    return estrategia


def _worker(args: tuple[str, list[int], str]) -> dict[int, str]:
    """Entrypoint para ProcessPoolExecutor."""
    pdf_path, page_nums, estrategia = args
    return ESTRATEGIAS[estrategia](pdf_path, page_nums)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def extract_pdf(
    pdf_path: str | Path,
    output_dir: str | Path,
    workers: int = 1,
    resume: bool = True,
    write_final: bool = True,
    chunk_size: int | None = None,
    estrategia: str = "auto",
) -> dict[int, str]:
    """
    Extrai todas as páginas de um PDF para arquivos Markdown.

    Parâmetros
    ──────────
    pdf_path    : caminho do PDF de entrada
    output_dir  : diretório onde salvar page_NNNN.md (criado se não existir)
    workers     : nº de processos paralelos (1 = sequencial, default)
    resume      : se True, pula páginas já existentes em output_dir
    write_final : se True, gera documento_final.md com tudo concatenado
    chunk_size  : nº de páginas por chunk de worker (None = auto)
    estrategia  : auto | markdown | blocos | texto (ver `ESTRATEGIAS`)

    Retorna
    ───────
    dict {page_num: markdown} com todas as páginas (já processadas + novas).
    """
    pdf_path = str(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    estrategia = _resolver_estrategia(estrategia)
    if not _HAS_PYMUPDF4LLM and estrategia == "blocos":
        print("⚠  pymupdf4llm não instalado — usando fallback de blocos puros.")
        print("   Recomendado: pip install pymupdf4llm")

    with fitz.open(pdf_path) as doc:
        total = len(doc)

    # ── Resume ────────────────────────────────────────────────────────────────
    if resume:
        ja = {
            int(f.stem.replace("page_", ""))
            for f in output_dir.glob("page_????.md")
        }
        pendentes = [p for p in range(total) if p not in ja]
        if ja:
            print(f"  Resume: {len(ja)}/{total} páginas já processadas → "
                  f"{len(pendentes)} pendentes")
    else:
        ja = set()
        pendentes = list(range(total))

    results: dict[int, str] = {}
    if write_final:
        for p in ja:
            results[p] = (output_dir / f"page_{p:04d}.md").read_text(encoding="utf-8")

    if not pendentes:
        print(f"  Tudo já processado ({total} páginas).")
    else:
        print(f"  Extraindo {len(pendentes)} páginas | estratégia={estrategia} | "
              f"workers={workers}")

        t0 = time.perf_counter()
        mds = _executar_extracao(pdf_path, pendentes, workers, chunk_size, estrategia)
        elapsed = time.perf_counter() - t0

        avisos: list[tuple[int, int]] = []
        for pn in pendentes:
            md = mds.get(pn, "")
            (output_dir / f"page_{pn:04d}.md").write_text(md, encoding="utf-8")
            results[pn] = md
            if len(md) < MIN_PAGE_CHARS:
                avisos.append((pn, len(md)))

        print(f"  Concluído em {elapsed:.1f}s "
              f"({elapsed / max(1, len(pendentes)):.2f}s/pág)")

        if avisos:
            print(f"\n  ⚠  {len(avisos)} página(s) com < {MIN_PAGE_CHARS} chars "
                  f"(possíveis páginas escaneadas/imagens):")
            for pn, l in avisos[:10]:
                print(f"    pág {pn:>4}: {l:>4} chars")
            if len(avisos) > 10:
                print(f"    ... e mais {len(avisos) - 10}")

    # ── documento_final.md ────────────────────────────────────────────────────
    if write_final and results:
        final = PAGE_SEPARATOR.join(results[i] for i in sorted(results))
        (output_dir / "documento_final.md").write_text(final, encoding="utf-8")
        print(f"  documento_final.md gerado ({len(final):,} chars)")

    return results


def _executar_extracao(
    pdf_path: str,
    pendentes: list[int],
    workers: int,
    chunk_size: int | None,
    estrategia: str,
) -> dict[int, str]:
    """Despacha a extração — sequencial (workers≤1) ou paralela."""
    if workers <= 1 or len(pendentes) <= 4:
        # Sequencial — uma única chamada cobre todas as páginas
        return ESTRATEGIAS[estrategia](pdf_path, pendentes)

    if chunk_size is None:
        chunk_size = max(1, len(pendentes) // workers)
    chunks = [pendentes[i:i + chunk_size]
              for i in range(0, len(pendentes), chunk_size)]

    mds: dict[int, str] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for partial in ex.map(_worker, [(pdf_path, ch, estrategia) for ch in chunks]):
            mds.update(partial)
    return mds


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-i", "--input", required=True,
                        help="Caminho do PDF de entrada.")
    parser.add_argument("-o", "--output", required=True,
                        help="Diretório onde salvar page_NNNN.md.")
    parser.add_argument("-w", "--workers", type=int,
                        default=max(1, (os.cpu_count() or 2) // 2),
                        help="Processos paralelos (default: metade dos CPUs).")
    parser.add_argument("--chunk-size", type=int, default=None,
                        help="Páginas por chunk de worker (default: auto).")
    parser.add_argument("--no-resume", action="store_true",
                        help="Reprocessa todas as páginas, mesmo as já existentes.")
    parser.add_argument("--no-final", action="store_true",
                        help="Não gera documento_final.md, apenas as páginas.")
    parser.add_argument("--estrategia", choices=["auto", *ESTRATEGIAS], default="auto",
                        help="auto (pymupdf4llm se instalado, senão blocos), markdown, "
                             "blocos ou texto (get_text puro).")
    args = parser.parse_args(argv)

    if not Path(args.input).is_file():
        print(f"Erro: arquivo de entrada não encontrado: {args.input}",
              file=sys.stderr)
        return 1

    t0 = time.perf_counter()
    extract_pdf(
        pdf_path=args.input,
        output_dir=args.output,
        workers=args.workers,
        resume=not args.no_resume,
        write_final=not args.no_final,
        chunk_size=args.chunk_size,
        estrategia=args.estrategia,
    )
    print(f"\nTempo total: {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
