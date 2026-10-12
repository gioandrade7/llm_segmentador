# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Research pipeline for semantic segmentation of **structured documents**. Two sequential, independently runnable modules:

```
text_extraction/ → semantic_seg/
```

The segmentation task is **pure segmentation**: find where each semantic block starts and ends. There is no classification into predefined categories, and no per-document-type config file — the prompt is generic and specializes itself at runtime (see *The two nested loops* below).

The task is currently defined **only for documents with an identifiable structural pattern** (recurring markers, numbering, heading hierarchy). If the identifier returns `tem_padrao: false`, `main.py` prints `Documento sem estrutura recorrente` and exits without segmenting. Segmentation by topic shift, for documents without markers, no longer exists.

### Scope

Documents are chosen for **structural diversity, not domain**. The corpus deliberately mixes genres so that no single numbering convention can be assumed: a US federal statute (`sec-rules`, `SEC. 203` / `(a)` / `(1)`), municipal procurement rules (`ppb_rules`, `Section 3-02` / `(a)`–`(w)`), a Brazilian ministerial ordinance (`sisu-rules`, `Art. N` / `§` / incisos), a corporate tax-records document (`tax-rules`, unnumbered prose headings that `pymupdf4llm` emits as `#`/`##` Markdown headings — the identifier takes those as its markers), and an academic paper (`conference`, roman-numeral sections).

The point is that different collections are structurally incompatible, which is exactly what makes one general segmentation prompt hard to write — and what the identifier exists to absorb.


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
python pdf_extract.py -i arquivo.pdf -o saida/
python pdf_extract.py -i arquivo.pdf -o saida/ --workers 8   # parallel
python pdf_extract.py -i arquivo.pdf -o saida/ --no-resume   # ignore resume
python pdf_extract.py -i arquivo.pdf -o saida/ --no-final    # skip documento_final.md
python pdf_extract.py -i arquivo.pdf -o saida/ --chunk-size 4 # pages per worker chunk
```
`--workers` defaults to half the CPU count; `--chunk-size` defaults to auto.
Output: `saida/page_NNNN.md` per page (0-indexed, 4 digits) and `saida/documento_final.md`. Resumes automatically (skips already-processed pages).

**Module 1b — RTF → Text** (edit `ano`/`rtf_path` variables at top of file):
```bash
cd text_extraction/
python rtf_txt.py
```

**Module 2 — Semantic Segmentation:**
```bash
cd semantic_seg/
python main.py \
    --diretorio ../text_extraction/out/ppb_rules/paginas \
    --saida resultados/ppb_rules.json \
    --janela-paginas 10
```

| Flag | Meaning | Default |
|---|---|---|
| `--diretorio` | folder with the paginated `.md` files | required |
| `--extensao` | page file extension | `.md` |
| `--saida` | JSON file for the block list | none |
| `--model` | OpenAI model | `gpt-5.6-terra` |
| `--janela-paginas` | pages per context window | `10` |
| `--paginas-amostra` | pages per region (start/middle/end) sent to the identifier | `2` |
| `--max-ciclos` | cap on correction cycles; `0` runs until the auditor approves | `0` |
| `--so-resultado` | suppress prompt output | off |

Writes `{saida}.json` (block list only), plus siblings: `{saida}_padrao.json` (the identified pattern), `{saida}_cicloN.json` (each cycle's full extraction), `{saida}_correcao.json` (the correction trail). Only `{saida}.json` is protected from overwriting (`_v2`, `_v3` appended); the three siblings are derived from the final name and **overwrite silently**. Copy them aside before re-running if you need them.

## The two nested loops

This is the core of `semantic_seg/` and the thing to understand before changing anything there.

**Outer loop — iterative correction** (`corretor.py:segmentar_com_correcao`): segment the whole document, have an LLM *auditor* judge the result, feed its directives back into the segmentation prompt, repeat. Stops when the auditor approves, when it produces no directives, or at `--max-ciclos`. There is no stagnation detection: a segmentation that repeats without ever being approved runs until the cap, and `--max-ciclos 0` (the default) means no cap.

**Inner loop — sliding window** (`utils_llm.py:processar_documento_completo`): the whole document is one string with `<!-- PÁGINA N -->` markers; each LLM call is asked for **only the first complete block** in the current window; a char pointer advances past each block via its `offset_fim` anchor. If a block's `pagina_fim` lands on the window's last page it may be truncated, so the window doubles and the call is retried.

**Before both** (`identificador.py`): the document's structural pattern is inferred **once**, from a sample of source pages — never from blocks. Its output (hierarchy of markers, confidence) is injected into both the segmenter and the auditor prompts. The segmenter cuts at **every** marker of any level, down to the finest one the document marks (each block is a unit's *own* text: a caput or title alone, each subdivision separately) — the same granularity as the hand-annotated gold in `gabarito/`. This exists because the auditor only ever sees a summary of block *borders*, so deducing the pattern there is circular: a run on `ppb_rules` was approved as consistent while silently dropping item `(5) Determinations Required`, because a list ending at `(4)` followed by the next letter is indistinguishable from a complete one without the source text.

Prompt precedence, as stated in the prompts themselves: `padrao` is the structural spec; `insights` are corrections from observed failures and win any conflict.

## Architecture

### text_extraction/
- `pdf_extract.py`: Reads the native text layer from PDFs directly — no OCR, no ML models, no API calls. Three extraction strategies selected at import time: (1) **pymupdf4llm** (preferred, auto column/table detection → Markdown), (2) **PyMuPDF blocks + column sort** (fallback, heuristic multi-column reordering), (3) **plain `get_text()`** (last resort). Parallel extraction via `ProcessPoolExecutor` (`--workers N`). Warns on pages returning fewer than `MIN_PAGE_CHARS` (100) — likely scanned. Usable as a library: `from text_extraction.pdf_extract import extract_pdf`.
- `rtf_txt.py`: Converts legacy RTF documents to plain text via `striprtf`.

### semantic_seg/
- `main.py`: CLI entry point; loads pages, runs the identifier once (always — there is no flag to skip it), stops if it finds no pattern, otherwise passes the pattern text to `segmentar_com_correcao` and persists results.
- `identificador.py`: Infers the document's structural pattern from `n` contiguous pages each from the start, middle and end (`amostrar_paginas`). The prompt asks only for `tem_padrao`, `hierarquia` (`{nivel, nome, exemplo}` per level) and `confianca`; sample provenance is added in code. (`modo` and the `sequencia_plana` branch are commented out in `formatar_padrao()`.) `formatar_padrao()` renders that dict to prompt prose **in code**, so what reaches the model stays deterministic, and returns a `(tem_padrao, texto)` tuple. `tem_padrao: false` is not an escape hatch into another segmentation mode: `main.py` stops on it. The rendered text ends with generic cutting rules that reach both the segmenter and the auditor: cut at every marker of the hierarchy, with three exceptions — figure/table captions and table content are not units; **inline lists** (`(1)`, `1.`, `(i)`, `•`, `-`) whose markers are *not* in the hierarchy are part of the block they sit in, including bold-term list items (`- **Term** …`), while a bold heading that opens a paragraph *without* a list marker (`**Setting**`) is still a unit; and each **bibliography entry** is a unit, with or without a marker. The list exception is scoped to markers outside the hierarchy so that normative documents, whose `(a)`/`(1)`/incisos *are* the hierarchy, still cut at every item.
- `utils_llm.py`: The sliding-window pipeline plus `completar_json()`, the single shared LLM entry point (JSON response format; `temperature=0` except on `gpt-5*`). `construir_prompt()` is generic, with two optional injected sections (`padrao`, `insights`). Its offset rules include a cut-precision rule: physically, `offset_inicio` starts where the unit's marker or title opens the line (not at a mention of it in a table or caption) and `offset_fim` ends right before the next unit's marker; semantically, a page, column, figure or table break inside a unit does not end it.
- `corretor.py`: The auditor. `resumir_blocos()` sends only each block's page range, size, title and **borders** (250 leading / 120 trailing chars) — never the full text, which would cost as much as resending the document each cycle. The prompt has two branches: with an external `padrao` it checks conformance and drops `padrao_identificado` from its response schema; without one it deduces the pattern from the blocks (the pre-`identificador` behaviour). That second branch is no longer reachable from the CLI, since the identifier always runs and the pipeline only proceeds with a pattern. It looks for five pathologies: uneven granularity, an engulfing block, overlap, gap, anchor failure.
- `anchor.py`: Tolerant anchor matching in **five layers** — exact; exact on the first 80 chars; normalized (HTML tags and page markers removed, markdown stripped, whitespace collapsed, accents decomposed, lowercased, with a position map back to the original); normalized truncated; and, for `offset_fim` only (`preferir_sufixo`), the last 40 chars. That last layer exists because the model cuts an anchor mid-word and reconstructs its *beginning* wrongly, while the end — the position that actually matters — is copied correctly. The full text is normalized once per run via an identity cache.
- `utils_files.py`: Loads paginated `.md` files sorted by filename; assembles the full text string; re-injects the page marker header when a window starts mid-page.
- `display.py`: Terminal rendering only — no pipeline logic.
- `gabarito.py` / `avaliar.py`: read the indented-outline gold in `gabarito/` and score runs against it (`python avaliar.py resultados/sisu-rules resultados/conference_v2`).
- `hrdoc_gabarito.py`: converts one HRDoc annotation JSON (kept in the git-ignored `data/hrdoc/`) into a gabarito with start and end anchors; its docstring lists the conversion rules. It must resolve anchors exactly as `gabarito.py` does (compact matching that ignores whitespace, loose diacritics and `•`; on the expected page, prefer the occurrence that opens a line), or the end anchors it picks fail validation.

## HRDoc evaluation set

Beyond the five hand-annotated collections, `semantic_seg/gabarito/hrds-*.md` are gabaritos converted from **HRDoc** (Ma et al., AAAI 2023; HRDS subset = ACL/EMNLP/NAACL papers with human line-level annotation). Their PDFs come from `https://aclanthology.org/<id>.pdf` (`text_extraction/data/pdf/hrds-<id>.pdf`); the annotation JSONs are not versioned (no stated license). To add one: download the PDF, run `pdf_extract.py` into `text_extraction/out/hrds-<id>/paginas` (move `documento_final.md` up), run `hrdoc_gabarito.py`, then `gabarito.py` — a gabarito that fails there is not evaluable.

- `gabarito/hrds_amostra_test50.txt`: the 50 docs drawn from `HRDS/test` (stratified by venue, `random.Random(42)`). `gabarito/hrds_excluidos_ordem.txt`: 16 of them excluded because `pymupdf4llm` emits headings or references out of reading order, so no gabarito can be a partition of the extracted text. Of the rest, 32 are evaluable plus `N19-1154` (valid gold, never run); `N10-1074` lost a heading in extraction.
- `resultados/hrds-*` is versioned (exception in `.gitignore`; logs are not): `<doc>` = baseline prompt, `_v2` = + inline-list rule, `_v3` = + bold-heading carve-out (current prompt). On the 32 evaluable docs, final-cycle block F1 went 0.917 → 0.928 → 0.957 and coverage 99.0% → 99.4%; extra cuts 57 → 5, missed nodes 121 → 71. One run per doc and version, papers only — the prompt changes have not been re-run on the normative collections.
- The six `hrds-*` docs outside `hrds_amostra_test50.txt` came first, from the HRDoc repo's `examples/`; `naacl-main.2` has the same out-of-order extraction and is not evaluable.

## Known Limitations

- **Evaluation is boundary-only and most gabaritos are drafts.** `avaliar.py` scores each `{saida}_cicloN.json` against `gabarito/<doc>.md` (`_vN` stripped from the name), in `anchor.py`'s normalized space; `--tolerancia k` allows k normalized chars. Per cycle it reports P/R/F1 of block **starts** (snapped to the first alphanumeric char, so `- [1]` = `[1]`), P/R/F1 of block **ends** (snapped to the last alphanumeric char; an end is right anywhere between the node's end and the next node's start, i.e. inside the trailing noise), F1 of **exact blocks** (start and end on the same node) and **coverage** (share of the gold's content chars inside some block). Ends and coverage exist because starts alone can't see a block that stops early: on `hrds-2020.acl-main.1` a run scored start-F1 1.0 while a whole paragraph of 2.1 sat in no block. A gabarito line may carry an end anchor after ` ⟶ `; without it the node ends at the next start, so only the HRDoc-converted gabaritos (`hrds-*`, from `hrdoc_gabarito.py`) have real ends. It writes `{prefixo}_avaliacao.json` and `.png` (the plot still shows start P/R/F1 only). It does not score labels or hierarchy, and the hand-made gabaritos' headers say `RASCUNHO gerado por regex` — unreviewed lines count as truth.
- **Approval by the auditor is not correctness.** Running the loop until `consistente=true` is a **self-consistency** check: the auditor compares blocks against a structural spec, never against the document, so a uniformly wrong segmentation is a fixed point. Observed directly — see the three auditor limitations below.
- **The identifier is a single point of failure.** Its pattern is injected as the structural criterion for both the segmenter and the auditor, so a wrong pattern is enforced faithfully and with more authority than before. `{saida}_padrao.json` exists to keep that auditable. There is no CLI flag to bypass the identifier, so it can't be A/B tested from the command line.
- **Documents without a structural pattern are not segmented.** When the identifier returns `tem_padrao: false` (or misclassifies a structured document that way), the run ends with no blocks. None of the current collections hits this: even `tax-rules`, whose headings are unnumbered, reaches the segmenter through its `##` Markdown headings.
- `pdf_extract.py` requires a native text layer; scanned/image-only PDFs need OCR.
- **A block larger than the window is dropped silently.** When no *complete* block fits, the LLM returns `offset_inicio: null` and the pipeline advances **one page** without ever expanding the window — the window-doubling guard only fires when a block *was* returned. On `sec-rules`, `SEC. 203` (37,725 chars, ~12.5 pages, window of 10) was lost in 9 of 10 cycles: 41% of the document. Set `--janela-paginas` above the largest expected block.
- **Content after the last block is invisible to the auditor.** It detects gaps *between* blocks from the page ranges in `resumir_blocos()`, but nothing tells it where the document ends — it gets neither the text length nor coverage. On `ppb_rules` it approved a result missing all of `Section 3-03`.
- **The auditor can approve a defect it diagnosed itself.** On `sisu-rules` it flagged `Art. 20` as truncated in three consecutive cycles, was not obeyed, and approved on the fourth with the defect still present.
- **An extraction error can masquerade as a segmentation error.** On `conference` page 2, `pymupdf4llm` linearized the columns out of order, emitting the `III. METHODOLOGY` header 3,289 chars *before* its own section. The auditor diagnosed it correctly but its directives asked the segmenter to reorder text, which the monotonic char pointer and literal anchors forbid — 10 cycles oscillating between two wrong outputs. `_extract_blocks` (strategy 2) orders that page correctly, but the strategy is chosen at import time with no CLI flag.
- **`pdf_extract.py` writes `documento_final.md` into the same directory as the pages**, and `carregar_paginas` loads every `.md` sorted by filename — `documento_final.md` sorts *before* `page_0000.md` and would enter as "page 1" holding the whole document again. Extract to `out/<doc>/paginas/` and move `documento_final.md` one level up, as in `out/ppb_rules/`.
