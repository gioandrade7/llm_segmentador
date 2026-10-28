"""
dataset_tcu.py — Amostra estratificada de normas do TCU, com páginas e gabarito.

O `docs.csv` (raiz do repositório, fora do git) traz cada norma em HTML na
coluna `TEXTONORMA`. Nele cada dispositivo ocupa um `<p>` próprio, e o
marcador (`Art. 1º`, `§ 2º`, `I -`, `a)`) abre o parágrafo. É isso que permite
gerar o gabarito sem anotação manual: um `<p>` que abre com marcador inicia um
nó; um `<p>` sem marcador continua o nó anterior (a rubrica de um capítulo, por
exemplo).

Para cada norma escolhida o script escreve:

- `text_extraction/out/tcu/<KEY>/page_NNNN.md` — um `<p>` por linha, em páginas
  artificiais de ~`--chars-pagina` chars, quebradas entre linhas;
- `semantic_seg/gabarito/<KEY>.md` — o gabarito no formato do `gabarito.py`.

O manifesto (`gabarito/tcu_amostra.json`) registra as normas escolhidas, com o
estrato, a partição (dev/teste) e a semente, e quantas candidatas cada filtro
descartou.

Regras do gabarito, as mesmas de `utils_llm.REGRAS_DE_CORTE`:
- o que vem antes do primeiro marcador é um só nó [epígrafe] (título, ementa,
  preâmbulo e considerandos);
- o que vem depois do último dispositivo, a partir da primeira linha de
  local/data ou de assinatura, é um nó [fecho];
- texto citado de outra norma — de um `<p>` que abre com aspas até o que as
  fecha (com ou sem "(NR)") — pertence à unidade que o cita: seus marcadores
  não são nós.

Ficam de fora as normas em que o `<p>` não basta para o gabarito ou que não têm
estrutura a segmentar: tabelas, listas `<ol>/<li>` (numeração automática, que
some do texto), texto tachado, notas de rodapé, anexos dentro do texto, textos
compilados (`Art. 15 .....`, "(Revogado)", "(Redação dada…)"), retificações,
atos de pessoal (`Designar`, `Dispensar`…), marcador no meio de um `<p>`,
subseções e subitens (`1.1`, sem nível próprio no `gabarito.py`), aspas que
não fecham e normas sem nenhum nível abaixo do artigo.

Estratos: profundidade (plana: artigo e abaixo; profunda: com título, capítulo
ou seção) × tamanho do texto (pequena, média, grande). Em cada estrato entram
`--por-estrato` normas, ao menos uma com texto citado quando houver, e as
`--dev` primeiras vão para a partição dev. Uma norma só entra se o
`gabarito.py` resolver cada âncora exatamente no início do seu `<p>`.

Uso:
    python dataset_tcu.py                       # 6 estratos × 5 = 30 normas
    python dataset_tcu.py --semente 7 --por-estrato 10
    python dataset_tcu.py --refazer             # mesmas normas, regras atuais
    python gabarito.py gabarito/NORMA-28362.md --trechos
"""

import argparse
import contextlib
import csv
import io
import json
import os
import random
import re
import shutil
import sys
from collections import Counter
from html.parser import HTMLParser

from gabarito import ErroGabarito, carregar_gabarito
from utils_files import carregar_paginas, montar_texto_completo

RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_GABARITO = os.path.join(RAIZ_REPO, "semantic_seg", "gabarito")
DIR_PAGINAS = os.path.join("text_extraction", "out", "tcu")   # relativo à raiz

TAMANHOS = [("pequena", 3_000, 8_000), ("média", 8_000, 20_000), ("grande", 20_000, 50_000)]
PROFUNDIDADES = ["plana", "profunda"]


# ── HTML → parágrafos ──────────────────────────────────────────────────────────

_BLOCOS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
           "table", "br", "body", "ul", "ol", "tbody", "thead", "blockquote"}
_OCULTOS = {"head", "style", "script", "title"}


class _Paragrafos(HTMLParser):
    """Texto de cada elemento de bloco, com espaços colapsados; anota as tags vistas."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragrafos, self.tags, self._buf, self._oculto = [], set(), [], 0

    def _fechar(self):
        texto = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if texto:
            self.paragrafos.append(texto)

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag in _OCULTOS:
            self._oculto += 1
        if tag in _BLOCOS:
            self._fechar()

    def handle_endtag(self, tag):
        if tag in _OCULTOS:
            self._oculto = max(0, self._oculto - 1)
        if tag in _BLOCOS:
            self._fechar()

    def handle_data(self, data):
        if not self._oculto:
            self._buf.append(data)


def paragrafos(html_norma: str) -> tuple[list[str], set[str]]:
    p = _Paragrafos()
    p.feed(html_norma)
    p.close()
    p._fechar()
    return p.paragrafos, p.tags


# ── Marcadores ─────────────────────────────────────────────────────────────────

# Nível de cada tipo, do mais grosso ao mais fino — o mesmo `_ORDEM` do
# `gabarito.py` (título fica acima de capítulo).
NIVEL = {"título": 0, "capítulo": 1, "seção": 2, "artigo": 3, "parágrafo": 4,
         "inciso": 5, "alínea": 6, "item": 7}

_ROMANO = r"(?:[IVXLC]+|[ÚU]NIC[OA])"
# Títulos, capítulos e seções: o parágrafo é só o rótulo, com a rubrica
# opcional depois de um travessão ("Seção I - Do Gabinete") ou colada nele,
# como o HTML às vezes traz ("CAPÍTULO IDAS DISPOSIÇÕES GERAIS").
_RUBRICA = r"(?:\b(?:\s*[-–—.].*)?|(?-i:(?=[A-ZÀ-Ú]{2})).*)$"
_ROTULO = re.compile(r"(?:T[ÍI]TULO|CAP[ÍI]TULO|(?:SUB)?SE[ÇC][ÃA]O)\b", re.I)
MARCADORES = [
    ("título", re.compile(rf"T[ÍI]TULO\s+{_ROMANO}{_RUBRICA}", re.I)),
    ("capítulo", re.compile(rf"CAP[ÍI]TULO\s+{_ROMANO}{_RUBRICA}", re.I)),
    ("subseção", re.compile(rf"SUBSE[ÇC][ÃA]O\s+{_ROMANO}{_RUBRICA}", re.I)),
    ("seção", re.compile(rf"SE[ÇC][ÃA]O\s+{_ROMANO}{_RUBRICA}", re.I)),
    ("artigo", re.compile(r"Art\.\s*\d+")),
    ("parágrafo", re.compile(r"§\s*\d+|(?i:Par[áa]grafo\s+[úu]nico)")),
    # Também a tipografia quebrada do HTML: `V I-gerenciar`, `VI ¿ participar`, `III) - Divisão`.
    ("inciso", re.compile(r"[IVXLC]+(?:\s[IVXLC]+)?\s*[-–—¿]\s*\S|[IVXLC]+\)\s*[-–—]?\s*\S")),
    ("alínea", re.compile(r"[a-z]\)\s*\S")),
    ("subitem", re.compile(r"\d{1,2}(?:\.\d{1,2})+\.?\s+\S")),
    ("item", re.compile(r"\d{1,2}(?:[.)]\s+|\s*[-–—]\s*)\S")),
]

# Um segundo marcador depois de `;`/`:`/`.` dentro do mesmo `<p>`: unidades
# coladas, que o `<p>` não separa.
_MARCADOR_INTERNO = re.compile(
    r"[.;:]\s+(?:Art\.\s*\d+[ºo°]?\s+[A-ZÀ-Ú]|§\s*\d+[ºo°]?\s+[A-ZÀ-Ú]|[IVXL]+\s*[-–—]\s|[a-z]\)\s)")
_ATO_DE_PESSOAL = re.compile(
    r"Art\.\s*\d+[ºo°.]*\s*(?:[-–—]\s*)?(Designar|Dispensar|Nomear|Exonerar|Conceder|Aposentar|"
    r"Lotar|Remover|Ceder|Tornar sem efeito|Retificar|Alterar a lota|Autorizar o afastamento)")
_COMPILADO = re.compile(r"\.{5,}|…{2,}|Reda[çc][ãa]o anterior|Revogad[oa] pel[oa]|\((?:Revogad|Alterad|Inclu[íi]d|Acrescid|Acrescentad|Renumerad|"
                        r"Reda[çc][ãa]o dada|Vide|Vigência)", re.I)
_ABRE_ASPAS = tuple("“\"«‘'")
# Só a aspa fecha a citação: um "(NR)" sem aspa marca a redação nova de um rótulo
# no meio do texto citado ("Seção I (NR)"), não o fim dele.
_FECHA_ASPAS = re.compile(r"[”\"»’'](?:\s*\(NR\))?\s*[.;,]?\s*$")
_FECHO = re.compile(
    r"^(?:Bras[íi]lia|TCU,|Sala das Sess|Publique-se|Registre-se|Cumpra-se|Dê-se ciência)"
    r"|,\s*em\s+\d{1,2}[ºo°]?\s+de\s+\w+\s+de\s+\d{4}\.?$", re.I)


def tipo_marcador(paragrafo: str) -> str | None:
    for tipo, padrao in MARCADORES:
        if padrao.match(paragrafo):
            return tipo
    return None


def _assinatura(paragrafo: str) -> bool:
    """Linha curta em caixa alta ou com um nome em caixa alta ("Ministro BRUNO DANTAS")."""
    if len(paragrafo) > 80 or not re.search(r"[A-ZÀ-Ú]{3}", paragrafo):
        return False
    return paragrafo == paragrafo.upper() or re.search(r"[A-ZÀ-Ú]{2,}(?:\s+[A-ZÀ-Ú]{2,})+", paragrafo) is not None


# Dispositivo omitido, como num texto compilado: `Art. 7º (...)`, `Art. 26. ...`, `(...)`.
_OMITIDO = re.compile(r"\(\s*\.{3,}\s*\)|(?:Art\.\s*\d+\S*|§\s*\d+\S*|[IVXLC]+\s*[-–—])\s*\(?\s*\.{2,}\s*\)?\.?")
# Começo que parece marcador mas não casa com nenhum: tipografia que o gabarito não saberia cortar.
_QUASE_MARCADOR = re.compile(r"[IVXLC]+(?:\s[IVXLC]+)?\s*[^\w\s\"“'‘«(]|[IVXLC]+\s+[a-zà-ú]|[a-z]\s*[)\-–—]\s|"
                             r"§|Art\.?\s*\d|(?i:par[áa]g\w*\s+[úu]nico)|\d+\s*$")


# ── Norma → nós ────────────────────────────────────────────────────────────────

def estruturar(paras: list[str]) -> tuple[list[dict], dict]:
    """
    Nós do gabarito (`{tipo, nivel, i}`, `i` = índice do parágrafo onde o nó
    abre) e as características da norma. Levanta ValueError com o motivo de
    exclusão quando o `<p>` não basta para o gabarito.
    """
    nos, em_aspas, inicio_aspas, citacoes = [], False, 0, 0
    citados: set[int] = set()
    for i, p in enumerate(paras):
        if em_aspas:
            citados.add(i)
            if _FECHA_ASPAS.search(p):
                em_aspas = False
            elif i - inicio_aspas > 40:
                raise ValueError("aspas sem fecho")
            continue
        if p.startswith(_ABRE_ASPAS):
            citados.add(i)
            citacoes += 1
            em_aspas, inicio_aspas = not _FECHA_ASPAS.search(p[1:]), i
            continue
        if _MARCADOR_INTERNO.search(p[1:]):
            raise ValueError("marcador no meio de um <p>")
        if _OMITIDO.fullmatch(p):
            raise ValueError("texto compilado")
        tipo = tipo_marcador(p)
        if tipo is None and _ROTULO.match(p) and len(p) <= 200:
            raise ValueError("rótulo de divisão não reconhecido")
        if tipo is None and _QUASE_MARCADOR.match(p):
            raise ValueError("marcador malformado")
        if tipo in ("subseção", "subitem"):
            raise ValueError(f"{tipo} sem nível no gabarito")
        if tipo:
            nos.append({"tipo": tipo, "nivel": NIVEL[tipo], "i": i})
    if em_aspas:
        raise ValueError("aspas sem fecho")

    artigos = [n for n in nos if n["tipo"] == "artigo"]
    if len(artigos) < 10:
        raise ValueError("menos de 10 artigos")
    if not any(n["nivel"] > NIVEL["artigo"] for n in nos):
        raise ValueError("nada abaixo do artigo")

    # Local/data ou assinatura no corpo de um dispositivo, com dispositivos depois:
    # o que segue é outro texto (redação anterior, anexo sem rótulo), não a norma.
    abertos = {n["i"]: n["nivel"] for n in nos}
    nivel_aberto = None
    for i in range(nos[0]["i"], nos[-1]["i"]):
        if i in abertos:
            nivel_aberto = abertos[i]
        elif i not in citados and nivel_aberto >= NIVEL["artigo"] \
                and (_FECHO.search(paras[i]) or _assinatura(paras[i])):
            raise ValueError("dispositivos depois do fecho")

    if nos[0]["i"] > 0:
        nos.insert(0, {"tipo": "epígrafe", "nivel": None, "i": 0})
    # Fecho: a primeira linha de local/data ou assinatura depois do último dispositivo.
    for i in range(nos[-1]["i"] + 1, len(paras)):
        if _FECHO.search(paras[i]) or _assinatura(paras[i]):
            nos.append({"tipo": "fecho", "nivel": None, "i": i})
            break

    tipos = Counter(n["tipo"] for n in nos)
    return nos, {
        "profundidade": "profunda" if tipos["título"] + tipos["capítulo"] + tipos["seção"] else "plana",
        "citacoes": citacoes,
        "nos_por_tipo": dict(tipos),
    }


def excluir_html(html_norma: str) -> str | None:
    """Motivo de exclusão visível já no HTML, antes de separar os parágrafos."""
    h = html_norma.lower()
    for motivo, sinais in [("tabela", ["<table"]), ("lista html", ["<li"]),
                           ("tachado", ["<s>", "<strike", "<del", "line-through"]),
                           ("nota de rodapé", ["_ftn"]), ("caminho de rede", ["sarq_prod"])]:
        if any(s in h for s in sinais):
            return motivo
    return None


def excluir_paragrafos(paras: list[str]) -> str | None:
    """Motivo de exclusão visível nos parágrafos."""
    if any(re.match(r"ANEXO\b", p) for p in paras):
        return "anexo no texto"
    if any(re.match(r"Retifica", p, re.I) for p in paras):
        return "retificação"
    if any(_COMPILADO.search(p) for p in paras):
        return "texto compilado"
    artigos = [p for p in paras if p.startswith("Art.")]
    if artigos and sum(bool(_ATO_DE_PESSOAL.match(p)) for p in artigos) / len(artigos) > 0.5:
        return "ato de pessoal"
    return None


def candidatas(caminho_csv: str, chaves: set[str] | None = None) -> tuple[list[dict], Counter]:
    """Normas que passam por todos os filtros, com parágrafos, nós e estrato; só as `chaves`, se dadas."""
    csv.field_size_limit(sys.maxsize)
    vistas, aceitas, descartes = set(), [], Counter()
    with open(caminho_csv, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if chaves is not None and linha["KEY"] not in chaves:
                continue
            html_norma = linha["TEXTONORMA"] or ""
            # Corte barato antes de qualquer parsing: tamanho e número de artigos.
            if not 3_000 <= len(html_norma) <= 400_000 or html_norma.count("Art.") < 10:
                descartes["tamanho ou menos de 10 artigos"] += 1
                continue
            if html_norma in vistas:
                descartes["duplicata"] += 1
                continue
            vistas.add(html_norma)
            if motivo := excluir_html(html_norma):
                descartes[motivo] += 1
                continue
            paras, _ = paragrafos(html_norma)
            tamanho = sum(len(p) + 1 for p in paras)
            faixa = next((nome for nome, a, b in TAMANHOS if a < tamanho <= b), None)
            if faixa is None:
                descartes["tamanho ou menos de 10 artigos"] += 1
                continue
            if motivo := excluir_paragrafos(paras):
                descartes[motivo] += 1
                continue
            try:
                nos, carac = estruturar(paras)
            except ValueError as e:
                descartes[str(e)] += 1
                continue
            aceitas.append({
                "key": linha["KEY"], "titulo": linha["TITULO"], "tipo_norma": linha["TIPONORMA"],
                "ano": linha["ANONORMA"], "situacao": linha["SITUACAO"],
                "tamanho": faixa, "chars": tamanho, **carac,
                "paragrafos": paras, "nos": nos,
            })
    return aceitas, descartes


# ── Páginas e gabarito ─────────────────────────────────────────────────────────

def paginar(paras: list[str], chars_pagina: int) -> list[list[str]]:
    """Agrupa os parágrafos em páginas de ~`chars_pagina` chars, sem partir linha."""
    paginas, atual, tam = [], [], 0
    for p in paras:
        if atual and tam + len(p) > chars_pagina:
            paginas.append(atual)
            atual, tam = [], 0
        atual.append(p)
        tam += len(p) + 1
    return paginas + [atual] if atual else paginas


def escrever_paginas(norma: dict, chars_pagina: int) -> str:
    rel = os.path.join(DIR_PAGINAS, norma["key"])
    destino = os.path.join(RAIZ_REPO, rel)
    shutil.rmtree(destino, ignore_errors=True)
    os.makedirs(destino)
    for n, linhas in enumerate(paginar(norma["paragrafos"], chars_pagina)):
        with open(os.path.join(destino, f"page_{n:04d}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")
    return rel


def _ancora(paras: list[str], i: int, limite: int, minimo: int) -> str:
    """
    Início do parágrafo `i` com ao menos `minimo` chars, completado pelos
    seguintes até `limite` (o parágrafo do nó seguinte, exclusive), cortado em palavra.
    """
    texto = paras[i]
    j = i + 1
    while len(texto) < minimo and j < limite:
        texto, j = f"{texto} {paras[j]}", j + 1
    if len(texto) > minimo:
        corte = texto.rfind(" ", 0, minimo + 1)
        texto = texto[:corte if corte > minimo // 2 else minimo]
    return texto.replace("%%", "%").replace("⟶", "->").rstrip()


def escrever_gabarito(norma: dict, rel_paginas: str, minimos: list[int], particao: str) -> str:
    paras, linhas = norma["paragrafos"], []
    limites = [no["i"] for no in norma["nos"][1:]] + [len(paras)]
    pilha: list[int] = []      # níveis dos ancestrais abertos
    for k, no in enumerate(norma["nos"]):
        if no["nivel"] is None:
            pilha = []
            prof = 1
        else:
            while pilha and pilha[-1] >= no["nivel"]:
                pilha.pop()
            prof = len(pilha) + 1
            pilha.append(no["nivel"])
        linhas.append(f"{'  ' * prof}- [{no['tipo']}] {_ancora(paras, no['i'], limites[k], minimos[k])}")

    cabecalho = f"""%%
Gabarito de segmentação — {norma['key']} ({norma['titulo']})
Texto-fonte: {rel_paginas}
Status: gerado por semantic_seg/dataset_tcu.py a partir do HTML de docs.csv (um <p> por linha); não conferido à mão.
Estrato: {norma['profundidade']} × {norma['tamanho']} ({norma['chars']} chars) · partição: {particao}

Sintaxe:
- Uma linha por nó: `- [tipo] início do trecho`; indentação de 2 espaços por nível
- Tipos: epígrafe (do início até o primeiro dispositivo: título, ementa, preâmbulo e considerandos), título,
  capítulo e seção (com a rubrica), artigo, parágrafo (§ N / Parágrafo único), inciso (I -), alínea (a)),
  item (1.), fecho (local/data e assinaturas)
- Cada <p> que abre com marcador é nó; um <p> sem marcador pertence ao nó anterior
- Texto citado de outra norma (do <p> que abre aspas ao que as fecha, ou "(NR)") pertence à unidade que o
  cita: seus Art./§/incisos não são nós
%%

- Documento
"""
    caminho = os.path.join(DIR_GABARITO, f"{norma['key']}.md")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(cabecalho + "\n".join(linhas) + "\n")
    return caminho


def inicios_esperados(rel_paginas: str, norma: dict) -> list[int]:
    """Posição, no texto montado pelo pipeline, do `<p>` que abre cada nó."""
    with contextlib.redirect_stdout(io.StringIO()):
        texto = montar_texto_completo(carregar_paginas(os.path.join(RAIZ_REPO, rel_paginas)))
    posicoes, cursor, abre = [], 0, {no["i"] for no in norma["nos"]}
    for i, p in enumerate(norma["paragrafos"]):
        cursor = texto.index(f"\n{p}\n", cursor) + 1
        if i in abre:
            posicoes.append(cursor)
        cursor += len(p)
    return posicoes


def materializar(norma: dict, chars_pagina: int, particao: str) -> str | None:
    """
    Escreve páginas e gabarito e confere que o `gabarito.py` resolve cada nó no
    início do seu `<p>`. Âncoras que caem em outro lugar são alongadas e a
    conferência é refeita. Devolve o motivo da falha, ou None.
    """
    rel = escrever_paginas(norma, chars_pagina)
    esperados = inicios_esperados(rel, norma)
    minimos = [50] * len(norma["nos"])
    for _ in range(4):
        caminho = escrever_gabarito(norma, rel, minimos, particao)
        try:
            _, nos = carregar_gabarito(caminho)
            errados = [k for k, (no, e) in enumerate(zip(nos, esperados)) if no["inicio"] != e]
        except ErroGabarito as e:
            errados, motivo = list(range(len(minimos))), str(e).split("\n")[0]
        else:
            motivo = f"{len(errados)} âncora(s) fora do lugar"
        if not errados:
            return None
        for k in errados:
            minimos[k] *= 2
    os.remove(caminho)
    shutil.rmtree(os.path.join(RAIZ_REPO, rel), ignore_errors=True)
    return motivo


# ── Amostragem ─────────────────────────────────────────────────────────────────

def refazer(caminho_csv: str, caminho_manifesto: str) -> None:
    """
    Regera páginas e gabarito das normas do manifesto, com as regras atuais,
    sem sortear de novo: as execuções já feitas sobre essas páginas continuam
    valendo. Uma norma que as regras atuais descartam sai do manifesto.
    """
    with open(caminho_manifesto, encoding="utf-8") as f:
        manifesto = json.load(f)
    antigas = {n["key"]: n for n in manifesto["normas"]}
    normas, descartes = candidatas(caminho_csv, set(antigas))
    if descartes:
        print(f"descartadas pelas regras atuais: {dict(descartes)}")
    novas = {n["key"]: n for n in normas}
    mantidas = []
    for key, antiga in antigas.items():
        norma = novas.get(key)
        motivo = materializar(norma, manifesto["chars_pagina"], antiga["particao"]) if norma else "descartada"
        if motivo:
            print(f"  ✗ {key}: {motivo}")
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(DIR_GABARITO, f"{key}.md"))
            continue
        nova = {**{k: v for k, v in norma.items() if k not in ("paragrafos", "nos")},
                "particao": antiga["particao"], "nos": len(norma["nos"])}
        if (nova["profundidade"], nova["tamanho"]) != (antiga["profundidade"], antiga["tamanho"]):
            print(f"  ! {key}: estrato mudou de {antiga['profundidade']} × {antiga['tamanho']}"
                  f" para {nova['profundidade']} × {nova['tamanho']}")
        if nova["nos"] != antiga["nos"]:
            print(f"  ~ {key}: {antiga['nos']} → {nova['nos']} nós")
        mantidas.append(nova)
    manifesto["normas"] = mantidas
    with open(caminho_manifesto, "w", encoding="utf-8") as f:
        json.dump(manifesto, f, ensure_ascii=False, indent=2)
    print(f"{len(mantidas)}/{len(antigas)} normas regeradas")


def main():
    parser = argparse.ArgumentParser(description="Amostra estratificada de normas do TCU, com páginas e gabarito.")
    parser.add_argument("--csv", default=os.path.join(RAIZ_REPO, "docs.csv"))
    parser.add_argument("--por-estrato", type=int, default=5, help="Normas por estrato (6 estratos).")
    parser.add_argument("--dev", type=int, default=2, help="Normas de cada estrato na partição dev.")
    parser.add_argument("--semente", type=int, default=42)
    parser.add_argument("--chars-pagina", type=int, default=3000, help="Tamanho aproximado de cada página.")
    parser.add_argument("--refazer", action="store_true",
                        help="Regera as normas do manifesto atual com as regras atuais, sem sortear.")
    args = parser.parse_args()

    caminho_manifesto = os.path.join(DIR_GABARITO, "tcu_amostra.json")
    if args.refazer:
        refazer(args.csv, caminho_manifesto)
        return

    # A amostra anterior sai inteira: uma norma que não for sorteada de novo não pode deixar gabarito órfão.
    if os.path.exists(caminho_manifesto):
        with open(caminho_manifesto, encoding="utf-8") as f:
            for antiga in json.load(f)["normas"]:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(DIR_GABARITO, f"{antiga['key']}.md"))
                shutil.rmtree(os.path.join(RAIZ_REPO, DIR_PAGINAS, antiga["key"]), ignore_errors=True)

    print(f"Lendo {args.csv} …")
    normas, descartes = candidatas(args.csv)
    print(f"{len(normas)} candidatas; descartes: {dict(descartes.most_common())}")

    rng = random.Random(args.semente)
    escolhidas, falhas = [], Counter()
    for prof in PROFUNDIDADES:
        for faixa, _, _ in TAMANHOS:
            estrato = [n for n in normas if n["profundidade"] == prof and n["tamanho"] == faixa]
            rng.shuffle(estrato)
            # Ao menos uma com texto citado, quando houver: é o caso que mais desafia as regras de corte.
            estrato.sort(key=lambda n: n["citacoes"] == 0)
            estrato[1:] = rng.sample(estrato[1:], len(estrato) - 1)
            aceitas = []
            for norma in estrato:
                if len(aceitas) == args.por_estrato:
                    break
                particao = "dev" if len(aceitas) < args.dev else "teste"
                if motivo := materializar(norma, args.chars_pagina, particao):
                    falhas[motivo] += 1
                    print(f"  ✗ {norma['key']}: {motivo}")
                    continue
                aceitas.append({**{k: v for k, v in norma.items() if k not in ("paragrafos", "nos")},
                                "particao": particao, "nos": len(norma["nos"])})
            print(f"{prof:>8} × {faixa:<7}: {len(aceitas)}/{args.por_estrato} de {len(estrato)} candidatas")
            escolhidas += aceitas

    manifesto = {
        "semente": args.semente, "por_estrato": args.por_estrato, "dev": args.dev,
        "chars_pagina": args.chars_pagina, "candidatas": len(normas),
        "descartes": dict(descartes.most_common()), "falhas_de_ancora": dict(falhas),
        "normas": escolhidas,
    }
    with open(caminho_manifesto, "w", encoding="utf-8") as f:
        json.dump(manifesto, f, ensure_ascii=False, indent=2)
    print(f"\n{len(escolhidas)} normas → {DIR_PAGINAS}/ e gabarito/; manifesto em gabarito/tcu_amostra.json")


if __name__ == "__main__":
    main()
