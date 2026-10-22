# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Research pipeline (master's, IComp/UFAM) for semantic segmentation of **structured documents**. Two sequential, independently runnable modules:

```
text_extraction/ → semantic_seg/
```

The segmentation task is **pure segmentation**: find where each semantic block starts and ends. There is no classification and no per-document-type configuration — the prompts are generic and specialize themselves at runtime with the hierarchy of markers inferred from the document (see *The two nested loops*).

A block is a unit's **own** text, cut at **every** marker of any level down to the finest one the document marks: a caput or a title alone, each subdivision separately.

### Documents in the tree

- `sec-rules` — Investment Advisers Act of 1940 (US federal statute, 38 pages; `TITLE II` / `SEC. 203.` / `(a)` / `(1)` / `(A)` / `(i)` / `(I)`). PDF in `text_extraction/data/pdf/sec-rules.pdf`, pages in `text_extraction/out/sec-rules/paginas/`.
- `amostra-sec-rules` — a 15-page excerpt of the same statute (`amostra-sec-rules.pdf`, `out/amostra-sec-rules/paginas/`).

## Setup

```bash
pip install -r requirements.txt
# create .env with OPENAI_API_KEY=sk-...   (there is no .env.example)
```

`OPENAI_API_KEY` is required by `semantic_seg/` only. `text_extraction/` makes no API calls.

## Running Each Module

**Module 1a — PDF → Markdown:**
```bash
cd text_extraction/
python pdf_extract.py -i data/pdf/sec-rules.pdf -o out/sec-rules/paginas
mv out/sec-rules/paginas/documento_final.md out/sec-rules/   # required, see Known Limitations
```
Other flags: `--workers N` (default: half the CPUs), `--chunk-size N` (pages per worker, default auto), `--no-resume` (reprocess pages already written), `--no-final` (skip `documento_final.md`).
Output: `page_NNNN.md` per page (0-indexed, 4 digits) and `documento_final.md`. Resumes automatically.

**Module 1b — RTF → Text:** `python rtf_txt.py` from `text_extraction/`. It walks `data/rtf/<year>/` (1946–1970) with no CLI arguments; RTFs are read as `cp1252`.

**Module 2 — Semantic Segmentation:**
```bash
cd semantic_seg/
python main.py \
    --diretorio ../text_extraction/out/sec-rules/paginas \
    --saida resultados/sec-rules/sec-rules.json \
    --janela-paginas 10 --max-ciclos 10
```

| Flag | Meaning | Default |
|---|---|---|
| `--diretorio` | folder with the paginated `.md` files | required |
| `--extensao` | page file extension | `.md` |
| `--saida` | JSON file for the block list | none (nothing is saved) |
| `--model` | OpenAI model | `gpt-5.6-terra` |
| `--janela-paginas` | pages per context window (the `--help` text says 20; the real default is 10) | `10` |
| `--paginas-amostra` | pages per region (start/middle/end) sent to the identifier | `2` |
| `--max-ciclos` | cap on correction cycles; `0` runs until the auditor approves | `0` |

The segmenter prints every full prompt it sends; redirect stdout to a log (`*.log` is git-ignored).

Each document's runs live in `resultados/<doc>/`; `main.py` creates the folder of `--saida`. It writes `{saida}.json` (final cycle's block list) plus siblings: `{saida}_padrao.json` (the identifier's raw response), `{saida}_cicloN.json` (each cycle's full extraction) and `{saida}_correcao.json` (per cycle: directives applied and the auditor's full response). Only `{saida}.json` is protected from overwriting (`_v2`, `_v3` appended); the siblings take the final name and so follow it.

## The two nested loops

This is the core of `semantic_seg/` and the thing to understand before changing anything there.

**Before both — the identifier** (`identificador.py`), one LLM call per run. It reads a sample of **source pages** — `n` contiguous pages each from the start, middle and end (`amostrar_paginas`) — never blocks, and returns **only the hierarchy of markers**:

```json
{"instrução": "nível 1: Seção — SEC. 203.\nnível 2: Subseção — (a)\nnível 3: Parágrafo — (1)"}
```

`main.py` passes that string, unchanged, to both the segmenter and the auditor. It must come from the source text because the auditor only sees block *borders*, where the pattern can't be deduced without circularity: a list ending at `(4)` followed by the next letter is indistinguishable from a complete one.

**Cutting rules live in code, not in the identifier's answer.** `utils_llm.REGRAS_DE_CORTE` (cut at every marker; a unit with subdivisions keeps only the text before the first one; markers outside the hierarchy with the same function also cut; each bibliography entry is a unit; page/column/figure breaks don't end a unit) and its exceptions (figure/table captions and table content; **inline lists** whose markers are *not* in the hierarchy, including bold-term items `- **Term** …`, while a bold heading opening a paragraph without a list marker is a unit; text quoted from another statute; cross-references; page noise). `secao_estrutura(padrao)` renders hierarchy + rules and is injected into **both** prompts, so the auditor judges against exactly what the segmenter received. Without a hierarchy, `ESTRUTURA_RESERVA` stands in.

**Outer loop — iterative correction** (`corretor.py:segmentar_com_correcao`): segment the whole document, have an LLM *auditor* judge the result, feed its directives (`insights`) back into the segmentation prompt, repeat. Stops when the auditor approves (`consistente: true`), when it produces no directives, or at `--max-ciclos`. No stagnation detection: with `--max-ciclos 0` a run that is never approved never stops.

**Inner loop — sliding window** (`utils_llm.py:processar_documento_completo`): the whole document is one string with `<!-- PÁGINA N -->` markers; each LLM call is asked for **only the first block** of the current window — the one opened by the first marker in it. The answer is `titulo`, `offset_inicio`, `offset_fim` (literal ~80-char anchors), `pagina_inicio`, `pagina_fim`. A char pointer advances past `offset_fim` (fallbacks: past `offset_inicio`, then to the page after `pagina_fim`). If `pagina_fim` is the window's last page the block may be truncated, so the window doubles **once** and the call is retried. The prompt tells the model, when the window ends before the next marker, to return the block anyway with `pagina_fim` on the last page — that is what triggers the doubling. Invalid JSON is retried up to 3 times.

Prompt precedence, as stated in the prompts: hierarchy + cutting rules are the spec; `insights` are corrections from observed failures and win any conflict.

## Architecture

### text_extraction/
- `pdf_extract.py`: Reads the native text layer — no OCR, no ML, no API calls. Strategy chosen at import time: (1) **pymupdf4llm** (preferred, column/table detection → Markdown), (2) **PyMuPDF blocks + column sort**, (3) plain `get_text()`. Parallel via `ProcessPoolExecutor`. Warns on pages under `MIN_PAGE_CHARS` (100) — likely scanned. Usable as a library: `from text_extraction.pdf_extract import extract_pdf`.
- `rtf_txt.py`: Converts legacy RTF to plain text via `striprtf`.

### semantic_seg/
- `main.py`: CLI. Loads pages, runs the identifier (always — no flag to skip it), takes `padrao_dict["instrução"]` as the hierarchy text, runs `segmentar_com_correcao`, persists results. It never stops early for lack of a pattern (that check is commented out).
- `identificador.py`: sampling + identifier prompt. The prompt asks for the levels outermost → innermost down to the finest marked, each with an example **exactly as it appears** (Markdown included); unnumbered `#`/`##` headings count as levels; in normative texts incisos/alíneas/numbered items are levels, in non-normative texts inline lists are not; lists what is *not* a marker. Asks for the hierarchy only — no instructions, rules or comments. The sample's provenance (`paginas_amostradas`, `total_paginas`) is commented out, so `_padrao.json` doesn't record which pages were read.
- `utils_llm.py`: `REGRAS_DE_CORTE`, `ESTRUTURA_RESERVA`, `secao_estrutura()`, `construir_prompt()` (segmenter), the sliding-window pipeline, and `completar_json()` — the single LLM entry point (JSON response format; `temperature=0` except on `gpt-5*`; returns the **raw string** when the JSON doesn't parse).
- `corretor.py`: the auditor. `resumir_blocos()` sends only each block's page range, size and **borders** (300 leading / 300 trailing chars, or the raw anchors flagged `⚠ TEXTO NÃO EXTRAÍDO` when they weren't found) — never the full text, which would cost as much as resending the document each cycle. The prompt lists the pathologies to check (engulfing block, uneven granularity, gap, overlap, broken sequence, improper cut), an explicit approval criterion, an extraction-error clause (never ask the segmenter to reorder text), and how to write directives (imperative Portuguese, identify units by marker + page, never by block index `[n]`, never merge siblings, keep directives that worked, at most 5). Response: `problemas`, `erro_extracao`, `consistente`, `insights`. Only `consistente` and `insights` drive the loop; the rest is recorded in `_correcao.json`.
- `anchor.py`: tolerant anchor matching in **five layers** — exact; exact on the first 80 chars; normalized (HTML tags and page markers removed, Markdown stripped, whitespace collapsed, accents decomposed, lowercased, with a position map back to the original); normalized truncated; and, for `offset_fim` only (`preferir_sufixo`), the last 40 chars — because the model cuts an anchor mid-word and reconstructs its *beginning* wrongly while copying the end correctly. The full text is normalized once per run via an identity cache.
- `utils_files.py`: loads paginated `.md` files sorted by filename (numbered 1..N); assembles the full text; re-injects the page marker when a window starts mid-page.
- `gabarito.py` / `avaliar.py`: read an indented-outline gold (`gabarito/<doc>.md`, format in `gabarito.py`'s docstring) and score each `{prefixo}_cicloN.json` against it (`python avaliar.py resultados/<doc>/<doc>`). Per cycle: P/R/F1 of block **starts**, of block **ends** (an end is right anywhere between the node's end and the next node's start), of **exact blocks**, and **coverage**; `--tolerancia k` in normalized chars. Writes `{prefixo}_avaliacao.json` and `.png`. **`gabarito/` is currently empty**, so nothing can be scored until a gold is written.

## Results in the tree

- `resultados/sec-rules/`: 10 cycles, never approved (116 blocks in cycle 1, then 242–287). Only cycles 1–4 and `_padrao.json` are versioned; the rest is uncommitted. Its `_padrao.json` is a free-text instruction, not a hierarchy.
- `resultados/amostra-sec-rules/` (free-text `_padrao.json`) and `resultados/amostra-sec-rules-2/` (hierarchy-only `_padrao.json`): 10 cycles each, never approved, block counts swinging between cycles (7–84 on `-2`). Both untracked.

All three predate the current segmenter and auditor prompts, so they say nothing about them.

## Known Limitations

- **No evaluation is possible right now**: `gabarito/` is empty.
- **Approval by the auditor is not correctness.** It compares block borders against the structural spec, never against the document, so a uniformly wrong segmentation is a fixed point. Nothing tells it where the document ends (it gets neither the text length nor coverage), so content after the last block is invisible to it. The prompt asks it not to approve a defect it flagged in an earlier cycle; nothing enforces it.
- **The identifier is a single point of failure.** Its hierarchy is the criterion for both segmenter and auditor, so a wrong hierarchy is enforced faithfully. There is no flag to bypass it and no record of the sampled pages.
- **Blocks larger than the window.** The window doubles only once. A block still longer than twice the window comes out truncated, and the pointer resumes mid-unit. If the model returns `offset_inicio: null` instead, the pipeline advances one page and the block is lost. In `sec-rules`, `SEC. 203` alone spans ~12.5 pages: set `--janela-paginas` above the largest expected block.
- **The auditor's JSON is not retried.** If `completar_json` returns a raw string, `segmentar_com_correcao` calls `.get` on it and the run crashes. Only the segmenter retries.
- **`erro_extracao` and `problemas` are not read by code.** An extraction error (e.g. `pymupdf4llm` linearizing columns out of order) can still make the loop oscillate until `--max-ciclos`. `_extract_blocks` (strategy 2) orders such pages differently, but the strategy is chosen at import time with no CLI flag.
- **`pdf_extract.py` writes `documento_final.md` next to the pages**, and `carregar_paginas` loads every `.md` sorted by filename — `documento_final.md` sorts before `page_0000.md` and would enter as page 1 holding the whole document again. Always move it one level up.
- `pdf_extract.py` requires a native text layer; scanned PDFs need OCR.
