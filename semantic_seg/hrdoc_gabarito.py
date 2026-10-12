"""
hrdoc_gabarito.py — Converte a anotação de um documento do HRDoc em gabarito.

O HRDoc (Ma et al., AAAI 2023) anota artigos científicos linha a linha: cada
linha do PDF tem `text`, `class` (`title`, `sec1`, `fstline`, `para`, `equ`…),
`page` e `parent_id`, na ordem de leitura. Este script escreve o mesmo
documento no esboço indentado de `gabarito/`, com as decisões do
`gabarito/conference.md`:

- `title` (1ª linha) → [título]; autores, afiliações e e-mails ficam dentro dele
- `sec1` / `sec2` / `sec3` → [seção] / [subseção] / [subsubseção]; o nível vem
  da classe, não do `parent_id` (que encadeia irmãos via `equality`)
- `Abstract` → [resumo]; `References` → [referências]
- `fstline` sob References → [referência]: cada referência é um nó, com ou sem
  marcador (`[n]` ou autor-ano), como no conference; no PDF o recuo deslocado
  a delimita, e o `pdf_extract.py` a emite como item de lista `- `
- o resto (`para`, `fstline`, `opara`, `equ`, figuras, tabelas, rodapés,
  cabeçalhos) é parágrafo sem marcador ou ruído e não vira nó; legendas de
  figura e de tabela são ruído, como no conference (e o prompt diz o mesmo)

A âncora é o `text` da linha, que vem do parser do HRDoc e não do
`pdf_extract.py`. Os dois transcrevem o mesmo PDF de formas diferentes, então
a âncora é encurtada até um trecho que ambos escrevem igual:

- uma palavra hifenizada no fim da linha (`lexi-`) sai, porque o
  `pdf_extract.py` a emite inteira (`lexicon`);
- diacríticos soltos não precisam de tratamento: o HRDoc escreve `Tu¨r` e o
  `pdf_extract.py` `T¨ur`, mas a busca do `gabarito.py` os ignora;
- a de uma referência vai só até o ano (`Eve V. Clark. 2009.`, `Carpenter, B.
  (1997).`), porque o título que vem depois costuma estar em itálico markdown
  (`_First language…_`); a de um título perde o marcador inicial (`•`, `-`) e
  a pontuação final.

Cada âncora de início é procurada na página que o HRDoc anota (uma ocorrência
em outra página é coincidência) e, se não aparecer inteira, é encurtada
palavra a palavra pelo fim, com o comentário `início encurtado`.

O fim de cada nó (` ⟶ `, ver `gabarito.py`) é o final da última linha de
conteúdo do nó na ordem de leitura do HRDoc. Conteúdo é título, autores,
afiliações, e-mails, títulos de seção e parágrafos (`fstline`, `para`, `opara`);
equações, figuras, tabelas, legendas, rodapés e cabeçalhos no fim de um nó
ficam fora dele, como ruído. A âncora de fim é encurtada pelas mesmas razões:
vai só até ~40 chars do final, para antes de uma palavra terminada em `.,;:`, e perde a pontuação final: o
itálico markdown do `pdf_extract.py` põe um espaço antes da pontuação
(`_Cognition_ ,`, `In _EMNLP_ .`).

Quando o extrator troca as colunas, o final de um nó pode sair *antes* do seu
início no texto extraído (uma referência partida entre colunas). Esse pedaço
deslocado é tratado como ruído, como o `III. METHODOLOGY` do conference: o fim
do nó recua até a última linha de conteúdo que aparece dentro do seu intervalo,
e a linha do gabarito leva o comentário `fim recuado`.

Referências são irmãs, sem hierarquia entre si, então a ordem delas não muda a
partição: são ordenadas pela posição no texto extraído, que pode diferir da
ordem de leitura quando o extrator troca as colunas. Títulos de seção não são
reordenados — um título fora de ordem é erro de extração e o `gabarito.py` o
acusa. Confira sempre o resultado com `gabarito.py`.

Uso:
    python hrdoc_gabarito.py data/hrdoc/hrds-D11-1049.json \
        --texto-fonte text_extraction/out/hrds-D11-1049/paginas \
        --saida gabarito/hrds-D11-1049.md
"""

import argparse
import contextlib
import io
import json
import os
import re

from gabarito import RAIZ_REPO, _chave, _localizar_fim, _pagina, abre_linha, localizar_inicio
from utils_files import carregar_paginas, montar_texto_completo

NIVEL = {"sec1": 1, "sec2": 2, "sec3": 3}
TIPO = {1: "seção", 2: "subseção", 3: "subsubseção"}

_ANO = re.compile(r"\(?\b(19|20)\d\d[a-z]?\)?\.")
_MARCADOR_INICIAL = re.compile(r"^[•◦▪\-–—]\s*")

# Classes cujo texto pertence a um nó; o resto, no fim de um nó, é ruído.
_CONTEUDO = {"title", "author", "affili", "mail", "sec1", "sec2", "sec3", "fstline", "para", "opara"}


def _ancora(texto: str, referencia: bool = False) -> str:
    """Encurta o texto da linha até um trecho que o `pdf_extract.py` escreve igual."""
    curto = _MARCADOR_INICIAL.sub("", texto) or texto
    if curto.endswith("-") and " " in curto:
        curto = curto.rsplit(" ", 1)[0]
    if referencia:
        m = _ANO.search(curto)
        if m:
            curto = curto[:m.end()]
    else:
        # `On 16K vocabularies:` no HRDoc, `On 16K vocabularies,` na extração
        curto = curto.rstrip(".,:;") or curto
    return curto


def _ancora_fim(texto: str) -> str:
    """Final da linha, até ~40 chars, que o `pdf_extract.py` escreve igual."""
    palavras = texto.split()
    if len(palavras) > 1 and palavras[-1].endswith("-"):
        palavras = palavras[:-1]
    if palavras:
        palavras[-1] = palavras[-1].rstrip(".,;:") or palavras[-1]
    final = []
    for p in reversed(palavras):
        if final and (p[-1] in ".,;:" or len(" ".join([p] + final)) > 40):
            break
        final.insert(0, p)
    return " ".join(final)


def _achar(texto: str, ancora: str, pos: int, pagina: int) -> tuple[int, str]:
    """
    Início da âncora a partir de `pos`, **na página que o HRDoc anota** — uma
    ocorrência em outra página é coincidência (um título curto citado no corpo)
    e é pulada. Na página certa, vale a primeira ocorrência que **abre uma
    linha**: um título ou uma referência começa a linha, e uma menção anterior
    no corpo, numa tabela ou legenda (`COPA`, `|SWAG|`, `Table 2: Results`) ou
    na lista de autores de outra referência (`…, Yonatan Belinkov, …`) não. Sem
    ocorrência em início de linha (título embutido que a extração deixou no
    meio da linha), vale a primeira. Se a âncora inteira não aparecer, ela é encurtada palavra a
    palavra pelo fim, até um mínimo de 6 chars compactos: o HRDoc às vezes perde
    um símbolo (`(A B)` onde o PDF tem `(A → B)`). Devolve `(início, âncora usada)`.
    """
    palavras = ancora.split()
    for k in range(len(palavras), 0, -1):
        cand = " ".join(palavras[:k])
        if k < len(palavras) and len(_chave(cand)) < 6:
            break
        p, primeira = pos, -1
        while (ini := localizar_inicio(texto, cand, p)) != -1:
            pg = _pagina(texto, ini)
            if pg > pagina:
                break
            if pg == pagina:
                if abre_linha(texto, ini):
                    return ini, cand
                if primeira == -1:
                    primeira = ini
            p = ini + 1
        if primeira != -1:
            return primeira, cand
    return -1, ancora




def _resolver(nos: list[dict], texto: str) -> list[dict]:
    """
    Localiza o início de cada nó (`inicio`, -1 se não achar), reordena as
    referências pela posição no texto extraído e escolhe a âncora de fim.
    Devolve os nós na ordem do texto extraído.
    """
    pos, inicio_refs = 0, -1
    for no in nos:
        if no["tipo"] == "referência":
            continue
        no["inicio"], usada = _achar(texto, no["ancora"], pos, no["pagina"])
        no["encurtado"] = usada != no["ancora"]
        no["ancora"] = usada
        if no["inicio"] != -1:
            pos = no["inicio"] + 1
        if no["tipo"] == "referências":
            inicio_refs = no["inicio"]

    refs = [no for no in nos if no["tipo"] == "referência"]
    if refs:
        anterior = inicio_refs
        for i, no in enumerate(refs):
            no["inicio"], usada = _achar(texto, no["ancora"], max(inicio_refs, 0), no["pagina"])
            no["encurtado"] = usada != no["ancora"]
            no["ancora"] = usada
            # Âncora não localizada fica junto da anterior; o gabarito.py a acusa.
            anterior = no["inicio"] if no["inicio"] != -1 else anterior
            no["chave"] = (anterior, i)
        refs.sort(key=lambda no: no["chave"])
        primeira = next(i for i, no in enumerate(nos) if no["tipo"] == "referência")
        resto = [no for no in nos if no["tipo"] != "referência"]
        nos = resto[:primeira] + refs + resto[primeira:]

    _escolher_fins(nos, texto)
    return nos


def _escolher_fins(nos: list[dict], texto: str) -> None:
    """
    Define `ancora_fim` de cada nó: a última das suas linhas de conteúdo cuja
    âncora de fim cai entre o início do nó e o do seguinte no texto extraído.
    Cada candidata também é tentada sem as primeiras palavras (até um mínimo de
    4 chars compactos), porque o começo dela é o que mais difere entre os dois
    extratores. Se nenhuma cair, fica a última linha, e o `gabarito.py` acusa.
    """
    inicios = [no["inicio"] for no in nos]
    limites = [next((x for x in inicios[k + 1:] if x != -1), len(texto)) for k in range(len(nos))]
    for no, ini, limite in zip(nos, inicios, limites):
        no["ancora_fim"], no["recuado"] = no["fins"][-1], False
        if ini == -1:
            continue
        achou = False
        for k, cand in enumerate(reversed(no["fins"])):
            palavras = cand.split()
            for j in range(len(palavras)):
                sufixo = " ".join(palavras[j:])
                if j and len(_chave(sufixo)) < 4:
                    break
                if _localizar_fim(texto, sufixo, ini, limite) != -1:
                    no["ancora_fim"], no["recuado"], achou = sufixo, k > 0, True
                    break
            if achou:
                break


def converter(caminho_json: str, texto_fonte: str) -> str:
    """Devolve o gabarito em esboço indentado para o JSON do HRDoc dado."""
    with open(caminho_json, encoding="utf-8") as f:
        linhas = json.load(f)

    nos = []
    viu_titulo = em_referencias = False
    for ln in linhas:
        cls, txt = ln["class"], " ".join(ln["text"].split())
        no = {"pagina": ln["page"] + 1}
        if cls == "title" and not viu_titulo:
            no.update(tipo="título", nivel=1, ancora=_ancora(txt))
            viu_titulo = True
        elif cls in NIVEL:
            em_referencias = txt.lower() == "references"
            if txt.lower() == "abstract":
                no.update(tipo="resumo", nivel=1, ancora=txt)
            elif em_referencias:
                no.update(tipo="referências", nivel=1, ancora=txt)
            else:
                no.update(tipo=TIPO[NIVEL[cls]], nivel=NIVEL[cls], ancora=_ancora(txt))
        elif cls == "fstline" and em_referencias:
            no.update(tipo="referência", nivel=2, ancora=_ancora(txt, referencia=True))
        else:
            # Linha que não abre nó: se for conteúdo, é o novo fim do nó corrente.
            if nos and cls in _CONTEUDO:
                nos[-1]["fins"].append(_ancora_fim(txt))
            continue
        no["fins"] = [_ancora_fim(txt)]
        nos.append(no)

    with contextlib.redirect_stdout(io.StringIO()):
        texto = montar_texto_completo(carregar_paginas(os.path.join(RAIZ_REPO, texto_fonte)))
    nos = _resolver(nos, texto)

    cabecalho = f"""%%
Gabarito de segmentação — {os.path.splitext(os.path.basename(caminho_json))[0]}
Texto-fonte: {texto_fonte}
Status: CONVERTIDO de {os.path.basename(caminho_json)} (HRDoc) por hrdoc_gabarito.py — anotação humana do HRDoc, regras de conversão no docstring do script

- Âncora = início do texto da linha no parser do HRDoc, encurtado; `p. N` = página no PDF (1-indexada)
- ` ⟶ ` = final da última linha de conteúdo do nó; o que vem depois, até o nó seguinte, é ruído
- Cada referência é um nó, com ou sem marcador; ordenadas pela posição no texto extraído
- Parágrafos, equações, figuras, tabelas, legendas, rodapés e cabeçalhos não viram nós
%%
"""
    corpo = [f"{'  ' * no['nivel']}- [{no['tipo']}] {no['ancora']} ⟶ {no['ancora_fim']}  %% p. {no['pagina']}"
             + ("; início encurtado" if no["encurtado"] else "")
             + ("; fim recuado (final fora de ordem na extração)" if no["recuado"] else "")
             for no in nos]
    return cabecalho + "\n- Documento\n" + "\n".join(corpo) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Converte a anotação de um documento do HRDoc em gabarito.")
    parser.add_argument("json", help="Arquivo .json do HRDoc.")
    parser.add_argument("--texto-fonte", required=True, help="Pasta das páginas extraídas, relativa à raiz do repo.")
    parser.add_argument("--saida", required=True, help="Arquivo .md do gabarito a escrever.")
    args = parser.parse_args()

    gabarito = converter(args.json, args.texto_fonte)
    with open(args.saida, "w", encoding="utf-8") as f:
        f.write(gabarito)
    print(f"{args.saida}: {gabarito.count('- [')} nós")


if __name__ == "__main__":
    main()
