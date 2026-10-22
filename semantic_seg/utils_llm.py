import json
import os
from dotenv import load_dotenv
from openai import OpenAI
from utils_files import montar_texto_completo, _montar_janela
from anchor import avancar_por_offset, extrair_trecho

load_dotenv()


# ──────────────────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────────────────

# Regras de corte genéricas. O identificador devolve só a hierarquia de
# marcadores; o que fazer com ela (cortar em todo nível, o que não é unidade)
# vale para qualquer documento e entra igual no segmentador e no auditor — se só
# o segmentador as conhecesse, o auditor cobraria cortes que as regras proíbem
# (ex.: itens de uma lista inline).
REGRAS_DE_CORTE = """

        ## O que inicia bloco
        - Todo marcador de um nível da hierarquia inicia bloco, mesmo quando o texto anterior apresenta os itens como continuação da sua frase (termina em `—` ou `:`, como em "among other things—"), e mesmo quando o nível intermediário é pulado (`(1)` direto sob a seção, sem `(a)`; inciso direto sob o artigo, sem parágrafo).

        ## O que NÃO inicia bloco
        - Texto citado de outra norma. Quando uma unidade altera ou acrescenta texto a outra norma ("passa a vigorar com a seguinte redação", "fica acrescido do seguinte", "is amended to read as follows"), todo o texto citado, até o fecho das aspas ou "(NR)", pertence à unidade que altera, mesmo que contenha "Art.", "§" ou incisos.
        - Remissões. A menção a uma unidade no meio de uma frase ("observado o disposto no art. 5º, § 2º", "under section 203(b)") não é marcador, mesmo que uma quebra de linha a coloque no início da linha: um marcador abre o texto da própria unidade; uma remissão continua uma frase que veio antes.
        - Ruído de página: cabeçalhos e rodapés correntes, números de página, carimbos de publicação e os marcadores `<!-- PÁGINA N -->`."""


# Usada quando não há padrão a injetar (o identificador falhou ou devolveu
# vazio): sem ela o prompt ficaria sem nenhuma definição de bloco.
ESTRUTURA_RESERVA = (
    "Não há hierarquia identificada para este documento. Um bloco é a **menor "
    "unidade de texto que o documento delimita**: começa onde o documento abre "
    "uma nova unidade por um marcador (numeração, rótulo, título) e termina "
    "imediatamente antes do início da próxima."
)


def secao_estrutura(padrao: str) -> str:
    """Seção `## Estrutura deste documento`, comum ao segmentador e ao auditor."""
    corpo = (
        "Hierarquia de marcadores identificada numa amostra do texto fonte, do "
        f"nível mais externo ao mais interno:\n\n{padrao.strip()}"
        if padrao.strip() else ESTRUTURA_RESERVA
    )
    return f"""
        ## Estrutura deste documento
        {corpo}
{REGRAS_DE_CORTE}"""


def construir_prompt(texto_janela: str, insights: str = "", padrao: str = "") -> str:
    """
    Monta o prompt completo que será enviado ao LLM.

    O prompt é inteiramente **genérico**: não depende de nenhum arquivo de
    configuração por tipo de documento. A tarefa do LLM é puramente de
    **segmentação** (encontrar onde um bloco começa e termina) — não há
    classificação em categorias pré-definidas.

    Duas seções opcionais o especializam, com precedências diferentes:

    `padrao` é a hierarquia de marcadores do documento, produzida pelo módulo
    `identificador` a partir de uma amostra de páginas do **texto fonte**,
    antes de qualquer segmentação. Vem seguida das `REGRAS_DE_CORTE`.
    Sem padrão, entra `ESTRUTURA_RESERVA` no lugar da hierarquia.

    `insights` são diretrizes corretivas escritas pelo módulo `corretor` a
    partir da análise dos blocos de um ciclo anterior. Vêm de falhas
    efetivamente observadas, então são evidência posterior: em caso de
    conflito, prevalecem sobre o padrão e as regras.
    """
    secao_insights = f"""
        ## Correções de ciclos anteriores
        A auditoria dos blocos segmentados em um ciclo anterior produziu as diretrizes abaixo. Elas nascem de erros efetivamente cometidos, então têm **precedência máxima**: se contradisserem a estrutura ou as regras de corte acima, siga as diretrizes.

        {insights.strip()}
    """ if insights.strip() else ""

    return f"""Você é um especialista em segmentação de documentos estruturados.

        ## Sua tarefa
        O texto abaixo é uma janela de um documento maior. Identifique **apenas o primeiro bloco**: que é aberto por um dos marcadores descritos em *Estrutura deste documento* que aparece na janela. Ele termina imediatamente antes do próximo marcador, que pode ser qualquer um a seguir, independentemente da hierarquia.

        O texto é composto por páginas, cada uma precedida por um marcador `<!-- PÁGINA N -->`. Um bloco pode se estender por mais de uma página. Se a janela acabar antes do marcador da unidade seguinte, não descarte o bloco: devolva-o até o fim do texto, com `pagina_fim` igual à última página da janela, e a janela será ampliada.
{secao_estrutura(padrao)}
{secao_insights}
        ## Texto do documento
        ```
        {texto_janela}
        ```

        ## Instruções de resposta
        Retorne **exclusivamente** um objeto JSON válido, sem texto adicional, com as chaves nesta ordem:

        - `"titulo"`        : string — o marcador e o título do bloco (ex.: `§ 2º`, `(b) Definitions`, `3.1 Data`)
        - `"offset_inicio"` : string — as primeiras ~80 chars do bloco, a partir do marcador que o abre, **COPIADAS LITERALMENTE** do texto acima
        - `"offset_fim"`    : string — as últimas ~80 chars do bloco, terminando imediatamente antes do marcador da unidade seguinte, **COPIADAS LITERALMENTE** do texto acima
        - `"pagina_inicio"` : inteiro — número da página onde o bloco começa (conforme os marcadores `<!-- PÁGINA N -->`)
        - `"pagina_fim"`    : inteiro — número da página onde o bloco termina (conforme os marcadores `<!-- PÁGINA N -->`)


        ## Regras dos trechos copiados
        - **Literalidade**: `offset_inicio` e `offset_fim` devem ser trechos **literalmente presentes** no texto acima, **copiados sem qualquer modificação** — incluindo formatação markdown, pontuação, quebras de linha e maiúsculas/minúsculas. **NUNCA parafraseie, descreva, abrevie ou explique** o bloco; os trechos servem apenas para localizá-lo no texto fonte.
        - Não inclua marcadores `<!-- PÁGINA N -->` nos trechos.
        - **Unicidade**: cada trecho deve ser longo e distintivo o bastante para ser localizado sem ambiguidade. Em caso de itens parecidos, escolha trechos mais longos.
        - Se a janela não contiver nenhum marcador que abra uma unidade, retorne `"offset_inicio": null` e todos os valores como `null` ou `0`.
        """


# ──────────────────────────────────────────────────────────────────────────────
# Chamada ao LLM
# ──────────────────────────────────────────────────────────────────────────────

SISTEMA_PADRAO = (
    "Você é um assistente especializado em análise de documentos. "
    "Responda sempre com um único objeto JSON válido, sem texto adicional."
)


def completar_json(prompt: str, model: str, sistema: str = SISTEMA_PADRAO) -> dict:
    """Envia um prompt ao LLM e devolve o JSON parseado da resposta."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    kwargs = {}
    if not model.startswith("gpt-5"):
        kwargs["temperature"] = 0

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": sistema},
            {"role": "user", "content": prompt},
        ],
        **kwargs,
    )

    raw = response.choices[0].message.content.strip()

    # Remove blocos de código markdown caso o modelo os inclua
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _chamar_llm(
    janela_texto: str,
    model: str,
    insights: str = "",
    padrao: str = "",
) -> dict:
    """Envia uma janela de texto ao LLM e retorna o dict JSON parseado."""

    prompt = construir_prompt(janela_texto, insights, padrao)
    # print(f"  ── Prompt enviado ao LLM ({len(prompt):,} chars) ──\n{prompt}\n")
    # O modelo às vezes devolve JSON malformado; completar_json() entrega a
    # string crua, então o segmentador repete a chamada em vez de quebrar.
    for tentativa in range(1, 4):
        resultado = completar_json(prompt, model)
        if isinstance(resultado, dict):
            return resultado
        print(f"  ⚠  JSON inválido na resposta (tentativa {tentativa}/3)")
    raise ValueError(f"LLM não devolveu JSON válido após 3 tentativas:\n{resultado}")


# ──────────────────────────────────────────────────────────────────────────────
# Mapa de páginas
#
# `carregar_paginas` numera as páginas sequencialmente (1..N), então a página
# de número K está SEMPRE no índice K-1. Basta, portanto, guardar a posição de
# caractere do marcador de cada página numa lista simples: `posicoes[idx]` é o
# início da página `idx + 1`. (Antes isto era uma lista de tuplas
# (num_pagina, posicao), o que forçava conversões constantes entre índice
# 0-based e número de página 1-based.)
# ──────────────────────────────────────────────────────────────────────────────

def _mapear_paginas(texto_completo: str, total_paginas: int) -> list[int]:
    """Posição de caractere do marcador `<!-- PÁGINA K -->` de cada página."""
    return [
        texto_completo.find(f"<!-- PÁGINA {pagina} -->")
        for pagina in range(1, total_paginas + 1)
    ]


def _pos_de_idx(posicoes: list[int], idx: int, len_texto: int) -> int:
    """Posição de início da página no índice `idx`, ou o fim do texto se `idx` estourar."""
    return posicoes[idx] if idx < len(posicoes) else len_texto


def _idx_pagina_de_pos(posicoes: list[int], pos: int) -> int:
    """Índice (0-based) da página que contém a posição de caractere `pos`."""
    resultado = 0
    for i, ppos in enumerate(posicoes):
        if ppos <= pos:
            resultado = i
        else:
            break
    return resultado


def _avancar_apos_bloco(
    texto_completo: str,
    resultado: dict,
    posicoes: list[int],
    pos_antes_do_bloco: int,
    total_paginas: int,
    len_texto: int,
) -> tuple[int, int]:
    """
    Determina a nova posição do ponteiro após um bloco identificado.

    Cascata de fallback (do mais preciso ao mais grosseiro):
      1. Logo após `offset_fim` (caso normal).
      2. Logo após `offset_inicio` — progresso mínimo dentro da mesma página,
         evitando pular blocos curtos que seguem o bloco identificado.
      3. Início da página seguinte a `pagina_fim` — último recurso quando
         nenhuma âncora é localizada.

    Retorna `(pos_atual, idx_pag_atual)`.
    """
    # Fallback 0 (normal): após offset_fim
    nova_pos = avancar_por_offset(texto_completo, resultado.get("offset_fim", ""), pos_antes_do_bloco)
    if nova_pos != -1:
        return nova_pos, _idx_pagina_de_pos(posicoes, nova_pos)

    # Fallback 1: após offset_inicio
    pos_apos_inicio = avancar_por_offset(
        texto_completo, resultado.get("offset_inicio", ""), pos_antes_do_bloco
    )
    if pos_apos_inicio != -1:
        print("  ⚠  offset_fim não localizado. Avançando para após offset_inicio.")
        return pos_apos_inicio, _idx_pagina_de_pos(posicoes, pos_apos_inicio)

    # Fallback 2: página seguinte a pagina_fim.
    # Página K está no índice K-1, logo a página seguinte a `pagina_fim` está
    # no índice `pagina_fim`.
    pag_fim_resultado = resultado.get("pagina_fim", 0)
    print(
        f"  ⚠  offset_fim e offset_inicio não localizados. "
        f"Avançando até após a pág. {pag_fim_resultado}."
    )
    idx_apos_bloco = pag_fim_resultado
    if 0 < idx_apos_bloco < total_paginas:
        return posicoes[idx_apos_bloco], idx_apos_bloco
    return len_texto, total_paginas


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline principal — janela deslizante
# ──────────────────────────────────────────────────────────────────────────────

def processar_documento_completo(
    paginas: list[dict],
    model: str = "gpt-4o",
    janela_paginas: int = 20,
    insights: str = "",
    padrao: str = "",
) -> list[dict]:
    """
    Percorre o documento inteiro via janela deslizante, identificando
    todos os blocos semânticos de forma iterativa.

    Estratégia
    ----------
    1. Monta o texto completo uma única vez, com marcadores <!-- PÁGINA N -->.
       Os números de página nos marcadores são sempre absolutos (originais),
       então os valores retornados pelo LLM são diretamente comparáveis ao
       documento fonte.

    2. Mantém `pos_atual` como ponteiro de caractere no texto completo.
       Após cada bloco identificado, localiza `offset_fim` no texto e avança
       `pos_atual` para imediatamente após esse trecho — preservando qualquer
       conteúdo que exista entre o fim do bloco e o fim da página.

    3. Detecção de truncamento: se `pagina_fim` do resultado coincidir com a
       última página da janela atual, o bloco pode estar incompleto. Nesse
       caso, a janela é estendida automaticamente até o dobro do tamanho
       original antes de uma nova tentativa.
    """
    texto_completo = montar_texto_completo(paginas)
    total_paginas  = len(paginas)
    posicoes       = _mapear_paginas(texto_completo, total_paginas)
    len_texto      = len(texto_completo)

    blocos        = []
    pos_atual     = 0       # ponteiro de char no texto_completo
    idx_pag_atual = 0       # índice 0-based da página atual (página = idx + 1)
    iteracao      = 0
    MAX_ITER      = total_paginas * 15  # teto de segurança anti-loop infinito

    SEP = "═" * 62
    print(f"\n{SEP}")
    print(f"  Pipeline — janela deslizante")
    print(f"  {total_paginas} página(s) | janela = {janela_paginas} págs. | modelo = {model}")
    print(f"{SEP}\n")

    while idx_pag_atual < total_paginas and iteracao < MAX_ITER:
        iteracao += 1

        # ── Define os limites da janela atual ─────────────────────────────
        idx_pag_fim  = min(idx_pag_atual + janela_paginas, total_paginas)
        pos_fim_jan  = _pos_de_idx(posicoes, idx_pag_fim, len_texto)
        janela_texto = _montar_janela(texto_completo, posicoes, idx_pag_atual, pos_atual, pos_fim_jan)

        if not janela_texto.strip():
            print("  ⚠  Janela vazia — fim do documento alcançado.")
            break

        # Página K está no índice K-1: a janela cobre as páginas
        # (idx_pag_atual + 1) até idx_pag_fim.
        print(
            f"── Iteração {iteracao:>3} │ págs. {idx_pag_atual + 1}–{idx_pag_fim} "
            f"│ {len(janela_texto):>8,} chars ──"
        )

        # ── Chama o LLM ───────────────────────────────────────────────────
        resultado = _chamar_llm(janela_texto, model, insights, padrao)
        print(resultado)

        # ── Bloco não encontrado — avança 1 página e continua ────────────
        if not resultado.get("offset_inicio"):
            print("  ⚠  Nenhum bloco identificado. Avançando 1 página...")
            idx_pag_atual = min(idx_pag_atual + 1, total_paginas)
            pos_atual = _pos_de_idx(posicoes, idx_pag_atual, len_texto)
            continue

        # ── Verificação de truncamento ─────────────────────────────────────
        # A última página da janela tem número `idx_pag_fim` (página = índice+1).
        # Se pagina_fim bate com ela, o bloco pode ter sido cortado: expande a
        # janela e tenta de novo.
        if resultado.get("pagina_fim", 0) >= idx_pag_fim and idx_pag_fim < total_paginas:
            janela_expandida = min(janela_paginas * 2, total_paginas - idx_pag_atual)
            print(
                f"  ⚠  Bloco pode estar truncado (pagina_fim={resultado.get('pagina_fim')} "
                f"= última pág. da janela). Expandindo para {janela_expandida} págs..."
            )
            idx_pag_fim_exp  = min(idx_pag_atual + janela_expandida, total_paginas)
            pos_fim_jan_exp  = _pos_de_idx(posicoes, idx_pag_fim_exp, len_texto)
            resultado = _chamar_llm(
                texto_completo[pos_atual:pos_fim_jan_exp], model, insights, padrao
            )

            if not resultado.get("offset_inicio"):
                print("  ⚠  Ainda sem bloco após expansão. Avançando meia janela.")
                idx_pag_atual = min(idx_pag_atual + max(1, janela_paginas // 2), total_paginas)
                pos_atual     = _pos_de_idx(posicoes, idx_pag_atual, len_texto)
                continue

        # ── Avança posição para depois do bloco ───────────────────────────
        pos_antes_do_bloco = pos_atual
        pos_atual, idx_pag_atual = _avancar_apos_bloco(
            texto_completo, resultado, posicoes, pos_antes_do_bloco,
            total_paginas, len_texto,
        )

        # ── Extrai o trecho completo do documento original ────────────────
        # pos_busca_inicio aponta para antes do início da janela atual,
        # evitando que o match recaia sobre uma ocorrência anterior idêntica.
        resultado["texto"] = extrair_trecho(
            texto_completo,
            resultado.get("offset_inicio", ""),
            resultado.get("offset_fim", ""),
            pos_busca_inicio=pos_antes_do_bloco,
        )

        # ── Registra o bloco ───────────────────────────────────────────────
        blocos.append(resultado)

        pct = pos_atual / len_texto * 100
        print(
            f"  ✓ Bloco {len(blocos):>3}: {resultado.get('titulo', 'N/A')}\n"
            f"           págs. {resultado.get('pagina_inicio', '?')}–{resultado.get('pagina_fim', '?')} │ "
            f"nova pos = char {pos_atual:,} ({pct:.1f}% do doc)"
        )

    print(f"\n{SEP}")
    print(f"  Concluído: {len(blocos)} bloco(s) em {iteracao} iteração(ões).")
    print(f"{SEP}\n")

    return blocos
