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
    "Você é um especialista em estrutura de documentos. O documento é estruturado: "
    "organizado em unidades abertas por marcadores (numeração, rótulos, títulos). "
    "Responde sempre com um único objeto JSON válido, sem texto adicional."
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

    return f"""
        ## O que você está lendo
        Abaixo estão as **páginas {lista_pags} de um documento de {total_paginas} páginas** — uma amostra de três regiões (começo, meio e fim), não o documento inteiro. Os marcadores `<!-- PÁGINA N -->` indicam o número real de cada página, então os saltos entre as regiões são visíveis.

        ## Sua tarefa
        Identifique a **hierarquia de marcadores** que abrem as unidades deste documento, para orientar um processo posterior de segmentação em blocos semânticos. Cada unidade, de qualquer nível, será um bloco com apenas o seu texto próprio: começa no seu marcador e termina imediatamente antes do próximo marcador, de qualquer nível.

        - Liste os níveis do mais externo ao mais interno, até o mais fino que o documento marca.
        - Para cada nível, dê um exemplo do marcador **exatamente como aparece no texto**, com a formatação markdown que o acompanha.
        - Títulos de seção sem numeração que o texto emite como cabeçalho markdown (`#`, `##`) ou em negrito numa linha própria também são marcadores: cada nível de cabeçalho é um nível da hierarquia.
        - Inclua um nível que aparece em só uma parte da amostra, se o marcador for claro: o documento inteiro não está visível.
        - Em textos normativos, incisos, alíneas e itens numerados são níveis da hierarquia. Em textos não normativos (artigos científicos, relatórios), listas dentro de um parágrafo não são níveis.
        - Uma lista de referências bibliográficas, se houver, é um nível: cada entrada é uma unidade.

        ## O que NÃO é marcador
        - Texto citado de outra norma. Quando uma unidade altera ou acrescenta texto a outra norma ("passa a vigorar com a seguinte redação", "is amended to read as follows"), o texto citado pertence à unidade que altera, mesmo que contenha "Art.", "§" ou incisos.
        - Remissões: a menção a uma unidade no meio de uma frase ("observado o disposto no art. 5º, § 2º", "under section 203(b)"), mesmo que uma quebra de linha a coloque no início da linha.
        - Legendas de figura e de tabela e o conteúdo de tabelas.
        - Ruído de página: cabeçalhos e rodapés correntes, números de página e carimbos de publicação.

        ## Amostra do documento
        ```
        {texto_amostra}
        ```

        ## Como responder
        Devolva **somente** a hierarquia: sem instruções de como segmentar, regras de corte, justificativas nem comentários sobre a amostra. Responda **em JSON**, com a chave abaixo, cujo valor tem um nível por linha, do mais externo ao mais interno, no formato `nível N: <nome> — <exemplo do marcador>`:
        ```
        {{
            "instrução": "nível 1: Seção — SEC. 203.\\nnível 2: Subseção — (a)\\nnível 3: Parágrafo — (1)"
        }}
        ```
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


    padrao = completar_json(
        construir_prompt_identificacao(montar_texto_completo(amostra), nums, total),
        model,
        sistema=SISTEMA_IDENTIFICACAO,
    )

    # padrao["paginas_amostradas"] = nums
    # padrao["total_paginas"]      = total
    return padrao

