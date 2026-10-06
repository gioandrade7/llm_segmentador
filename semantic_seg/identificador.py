"""
identificador.py — Inferência do padrão estrutural do documento.

Roda **antes** de qualquer segmentação e lê uma amostra de páginas do **texto
fonte**, nunca os blocos. É essa a diferença que importa: o auditor
(`corretor.py`) só vê um resumo das bordas dos blocos que ele mesmo está
julgando, então deduzir o padrão ali é circular — uma enumeração truncada na
cauda (`(1)…(4)` sem o `(5)`) é indistinguível de uma lista completa. Lendo o
texto fonte, o padrão passa a vir de fonte independente.

O resultado alimenta os dois prompts do pipeline (segmentador e auditor) e é
salvo junto dos resultados, para que um padrão errado fique auditável em vez
de implícito.

Uma única chamada de LLM por rodada — não por ciclo de correção.
"""

from utils_llm import completar_json
from utils_files import montar_texto_completo

SISTEMA_IDENTIFICACAO = (
    "Você é um especialista em estrutura de documentos oficiais e normativos. "
    "Analisa uma amostra de páginas e descreve a organização estrutural do "
    "documento. Responde sempre com um único objeto JSON válido, sem texto adicional."
)


# ──────────────────────────────────────────────────────────────────────────────
# Amostragem
# ──────────────────────────────────────────────────────────────────────────────

def amostrar_paginas(paginas: list[dict], n: int = 2) -> list[dict]:
    """
    Seleciona três regiões **contíguas** de `n` páginas: começo, meio e fim.

    A contiguidade dentro de cada região é o ponto. Para inferir em que nível
    cortar, o modelo precisa ver onde uma unidade termina e a próxima começa —
    uma página isolada mostra o cabeçalho de uma unidade, não sua fronteira.

    As três regiões cobrem a variação ao longo do documento: o começo costuma
    ter títulos de nível alto, o fim pode ter anexos ou material atípico.

    Devolve as páginas deduplicadas e em ordem. Se `3n` cobrir o documento
    inteiro, devolve todas.
    """
    total = len(paginas)
    if n <= 0:
        raise ValueError("n deve ser >= 1")
    if 3 * n >= total:
        return list(paginas)

    meio_ini = (total - n) // 2
    regioes = paginas[:n] + paginas[meio_ini:meio_ini + n] + paginas[total - n:]

    vistos, amostra = set(), []
    for p in regioes:
        if p["pagina"] not in vistos:
            vistos.add(p["pagina"])
            amostra.append(p)

    return sorted(amostra, key=lambda p: p["pagina"])


# ──────────────────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────────────────

def construir_prompt_identificacao(
    texto_amostra: str,
    paginas_amostradas: list[int],
    total_paginas: int,
) -> str:
    """
    Monta o prompt de identificação do padrão.

    A amostra é declarada explicitamente — quais páginas e de quantas — porque
    o modelo precisa saber que há lacunas entre as regiões e que não deve
    assumir ter visto o documento inteiro. Os marcadores `<!-- PÁGINA N -->`
    carregam os números absolutos, então os saltos já são visíveis no texto.
    """
    lista_pags = ", ".join(str(p) for p in paginas_amostradas)

    return f"""Você é um especialista em estrutura de documentos.

        ## O que você está lendo
        Abaixo estão as **páginas {lista_pags} de um documento de {total_paginas} páginas** — uma amostra de três regiões (começo, meio e fim), não o documento inteiro. Os marcadores `<!-- PÁGINA N -->` indicam o número real de cada página, então os saltos entre as regiões são visíveis. **Não assuma que viu todo o documento**: se a amostra não permitir concluir, diga isso na sua resposta.

        ## Sua tarefa
        Determine como este documento está estruturado, para orientar um processo posterior de segmentação em blocos semânticos.

        1. O documento segue um **padrão estrutural recorrente** (marcadores, numeração, hierarquia de títulos)?
        2. Se segue, qual é a **hierarquia** dos níveis, do mais externo ao mais interno?

        ## Amostra do documento
        ```
        {texto_amostra}
        ```

        ## Como responder
        - Cite os **marcadores literais** que você observou, não descrições abstratas como "títulos numerados".
        - Se o documento **não** tiver padrão recorrente — texto corrido, seções sem marcação sistemática — responda `"tem_padrao": false`. Isso é uma resposta legítima e útil; não invente uma hierarquia que você não observou.
        - Se a amostra for insuficiente para decidir com segurança, use `"confianca": "baixa"`.

        ## Instruções de resposta
        Retorne **exclusivamente** um objeto JSON válido com as chaves:

        - `"tem_padrao"`          : booleano — o documento segue um padrão estrutural recorrente
        - `"hierarquia"`         : array de objetos `{{"nivel": inteiro, "nome": string, "exemplo": string}}` — do nível mais externo (1) ao mais interno. `nome` é como o nível se chama neste documento; `exemplo` é um marcador literal observado. Array vazio se `tem_padrao` for `false`
        - `"confianca"`          : string — `"alta"`, `"media"` ou `"baixa"`
        """


# ──────────────────────────────────────────────────────────────────────────────
# Identificação
# ──────────────────────────────────────────────────────────────────────────────

def identificar_padrao(
    paginas: list[dict],
    model: str,
    n_amostra: int = 2,
    verboso: bool = True,
) -> dict:
    """
    Infere o padrão estrutural do documento a partir de uma amostra de páginas.

    Acrescenta ao dict devolvido pelo LLM a procedência da amostra
    (`paginas_amostradas`, `total_paginas`), calculada em código — assim o
    arquivo salvo diz de onde o padrão saiu.
    """
    amostra = amostrar_paginas(paginas, n=n_amostra)
    nums    = [p["pagina"] for p in amostra]
    total   = len(paginas)

    if verboso:
        print("── Identificando o padrão estrutural... ───────────────")
        print(f"   amostra: págs. {', '.join(map(str, nums))} de {total}\n")

    padrao = completar_json(
        construir_prompt_identificacao(montar_texto_completo(amostra), nums, total),
        model,
        sistema=SISTEMA_IDENTIFICACAO,
    )

    padrao["paginas_amostradas"] = nums
    padrao["total_paginas"]      = total
    return padrao


# ──────────────────────────────────────────────────────────────────────────────
# Renderização para os prompts
# ──────────────────────────────────────────────────────────────────────────────

def formatar_padrao(padrao: dict) -> str:
    """
    Renderiza o padrão como a seção de prompt injetada no segmentador e no
    auditor.

    A renderização é feita **em código**, não por uma segunda chamada de LLM:
    o texto que entra no prompt fica determinístico e inspecionável.

    Devolve string vazia se não houver padrão a injetar (identificação
    desligada ou falha), caso em que os prompts voltam ao comportamento
    genérico.
    """
    if not padrao:
        return ""

    pags  = padrao.get("paginas_amostradas") or []
    total = padrao.get("total_paginas")
    if pags and total:
        procedencia = (
            f"Identificada a partir das págs. {', '.join(map(str, pags))} "
            f"de {total} do texto fonte (amostra)."
        )
    else:
        procedencia = "Identificada a partir de uma amostra do texto fonte."

    ressalva = ""
    if (padrao.get("confianca") or "").lower() == "baixa":
        ressalva = (
            "\n        ⚠ Inferida de amostra pequena, com confiança baixa: trate como "
            "indício forte, não como regra absoluta. Se o texto da janela contradisser "
            "claramente esta estrutura, siga o texto.\n"
        )

    # ── Sem padrão: segmentação por mudança de assunto ────────────────────────
    if not padrao.get("tem_padrao"):
        return (False, f"""
        ## Estrutura deste documento
        {procedencia}

        A análise **não encontrou** padrão estrutural recorrente neste documento: não há hierarquia de marcadores nem numeração sistemática em que se ancorar.""")

    hierarquia = padrao.get("hierarquia") or []
    # modo       = (padrao.get("modo") or "hierarquico").lower()

    # ── Sequência plana: unidades irmãs, sem encaixe ──────────────────────────
#     if modo == "sequencia_plana":
#         unidade = hierarquia[0] if hierarquia else {}
#         exemplo = unidade.get("exemplo") or ""
#         nome    = unidade.get("nome") or "unidade"
#         return f"""
#         ## Estrutura deste documento
#         {procedencia}

#         Este documento **não é hierárquico**: é uma sequência de unidades independentes de mesmo nível, sem enumeração global que as encadeie. Unidade observada: **{nome}**{f' (ex.: `{exemplo}`)' if exemplo else ''}.
# {ressalva}
#         **Cada unidade é um bloco próprio.** Nunca agrupe duas unidades consecutivas num mesmo bloco, ainda que tratem de assunto parecido ou venham do mesmo órgão. Não procure níveis superiores: não existem."""

    # ── Hierárquico ───────────────────────────────────────────────────────────
    partes = []
    for h in hierarquia:
        exemplo = h.get("exemplo")
        sufixo  = f"  (ex.: `{exemplo}`)" if exemplo else ""
        partes.append(f"          nível {h.get('nivel')} — {h.get('nome') or '?'}{sufixo}")
    linhas = "\n".join(partes)

    # `nivel_de_corte` e `justificativa_corte` continuam no JSON do padrão, só
    # para registro: o gabarito é anotado no nível mais fino, então o prompt
    # corta em todo marcador e não usa mais um nível de corte.
    return (True,f"""
        ## Estrutura deste documento
        {procedencia}

        Hierarquia de marcadores observada, do nível mais externo ao mais interno:

{linhas}
{ressalva}
        **Corte em todo marcador desta hierarquia, em qualquer nível.** Cada unidade, de qualquer nível, gera um bloco com o seu texto próprio: começa no seu marcador e termina imediatamente antes do próximo marcador, de qualquer nível.

        - Uma unidade com subdivisões gera um bloco só com o texto que vem antes da primeira delas (o caput de um artigo, o título de um capítulo ou seção). Cada subdivisão é um bloco à parte, até o nível mais fino marcado.
        - O título de uma divisão pertence ao bloco dela, mesmo que esteja na linha seguinte ao marcador e mesmo que o bloco fique só com o título.
        - Parágrafos sem marcador pertencem ao bloco da unidade em que estão. A exceção é um parágrafo sem marcador que fecha uma lista e retoma o texto da unidade que a abriu: ele é um bloco próprio, separado do último item.
        - Se dois ou mais marcadores aparecem na mesma linha, cada um inicia um bloco.
        - A ordem acima é a típica, não uma regra: uma lista pode reaparecer dentro de um nível mais interno (ex.: itens `(1)`, `(2)` dentro de um `(iii)`). O que define o corte é a presença do marcador, não a posição dele na hierarquia.
        - Não agrupe unidades num mesmo bloco, nem irmãs nem uma unidade com as suas subdivisões.
        - Marcadores que não estão na lista acima, mas têm a mesma função (numeração ou rótulo que abre uma unidade), também iniciam um bloco: a hierarquia foi inferida de uma amostra e pode estar incompleta.""")
