"""
corretor.py — Correção iterativa da segmentação por análise de um LLM.

O pipeline de janela deslizante (`utils_llm.processar_documento_completo`)
segmenta o documento com um prompt genérico, sem conhecimento prévio do tipo de
documento. Este módulo fecha o ciclo: um LLM analisa os blocos produzidos,
identifica o padrão estrutural que o documento segue, aponta onde a segmentação
fugiu desse padrão e **escreve as diretrizes** que serão reinjetadas no prompt
de segmentação. O documento é então re-segmentado com o prompt enriquecido, e o
ciclo se repete até o analisador considerar os blocos estruturalmente
consistentes (ou até esgotar `max_ciclos`).

Cada ciclo custa uma re-segmentação completa do documento mais uma chamada de
análise. Por isso a análise recebe apenas um *resumo* dos blocos (título,
extensão e bordas), nunca o texto integral: o que se avalia são limites e
granularidade, não o conteúdo.
"""

from utils_llm import completar_json, processar_documento_completo

# Quanto de cada bloco vai no resumo enviado ao analisador.
INICIO_AMOSTRA = 250
FIM_AMOSTRA    = 120

SISTEMA_ANALISE = (
    "Você é um auditor de segmentação de documentos. Avalia criticamente a "
    "consistência estrutural de blocos já segmentados e escreve diretrizes "
    "corretivas. Responde sempre com um único objeto JSON válido, sem texto adicional."
)


# ──────────────────────────────────────────────────────────────────────────────
# Resumo dos blocos
# ──────────────────────────────────────────────────────────────────────────────

def _achatar(texto: str) -> str:
    """Colapsa quebras de linha para manter uma linha por campo no resumo."""
    return " ⏎ ".join(texto.split("\n")).strip()


def resumir_blocos(blocos: list[dict]) -> str:
    """
    Representação compacta dos blocos para a chamada de análise.

    Enviar o texto integral custaria o mesmo que reenviar o documento inteiro a
    cada ciclo. Como a análise é sobre limites e granularidade, bastam título,
    extensão e as bordas de cada bloco.
    """
    linhas = []
    for i, b in enumerate(blocos, 1):
        texto = b.get("texto") or ""
        if texto:
            tamanho = f"{len(texto):,} chars"
            inicio  = texto[:INICIO_AMOSTRA]
            fim     = texto[-FIM_AMOSTRA:] if len(texto) > INICIO_AMOSTRA + FIM_AMOSTRA else ""
        else:
            tamanho = "⚠ TEXTO NÃO EXTRAÍDO (âncoras não localizadas no documento)"
            inicio  = b.get("offset_inicio") or ""
            fim     = b.get("offset_fim") or ""

        linhas.append(
            f"[{i}] págs. {b.get('pagina_inicio', '?')}–{b.get('pagina_fim', '?')} | {tamanho}\n"
            f"    título: {b.get('titulo') or 'N/A'}\n"
            f"    início: {_achatar(inicio)}\n"
            f"    fim   : {_achatar(fim)}"
        )

    return "\n".join(linhas)


# ──────────────────────────────────────────────────────────────────────────────
# Análise
# ──────────────────────────────────────────────────────────────────────────────

def construir_prompt_analise(
    resumo: str,
    total_blocos: int,
    insights_atuais: str,
    padrao: str = "",
) -> str:
    """
    Monta o prompt do analisador a partir do resumo dos blocos de um ciclo.

    Com `padrao` (vindo do módulo `identificador`, que leu o texto fonte), o
    auditor **não deduz** a estrutura a partir dos blocos: ele confere a
    segmentação contra uma especificação de fonte independente. Sem `padrao`,
    cai no comportamento anterior — deduzir dos próprios blocos —, que é
    circular mas mantém o pipeline funcional com `--sem-identificador`.
    """
    secao_anterior = f"""
        ## Diretrizes usadas no ciclo anterior
        As diretrizes abaixo já estavam no prompt que produziu os blocos acima. Se não resolveram os problemas, refine-as ou substitua-as. O campo `insights` da sua resposta deve conter o conjunto **completo** de diretrizes para o próximo ciclo — não apenas o que mudou.

        ```
        {insights_atuais.strip()}
        ```
    """ if insights_atuais.strip() else ""



    # Com padrão externo, a tarefa é conferir conformidade; sem ele, o auditor
    # precisa deduzir a estrutura dos próprios blocos que está julgando.
    abertura = (
        f"Um pipeline automático segmentou um documento em {total_blocos} bloco(s). "
        "A estrutura do documento já foi identificada de forma independente e está "
        "descrita abaixo. Sua tarefa é avaliar se os blocos a respeitam e, se não "
        "respeitarem, escrever as diretrizes que corrigirão a próxima tentativa de "
        "segmentação."
    )
    instrucao_avaliar = (
        " Confira se a segmentação respeita a estrutura especificada, de forma uniforme. Considere "
        "a segmentação **inconsistente** se qualquer um destes problemas ocorrer:"
    )

    return f"""Você é um auditor de segmentação de documentos estruturados.

        {abertura}
{padrao}
        ## Blocos produzidos
        Cada bloco aparece com suas páginas, tamanho, título e os trechos iniciais e finais do texto capturado (`⏎` marca quebra de linha):

        ```
        {resumo}
        ```
{secao_anterior}
        ## O que avaliar
        {instrucao_avaliar}

        2. **Bloco englobante** — um bloco desproporcionalmente extenso que agrupa várias unidades do padrão identificado, quando cada unidade deveria ser um bloco.
        3. **Sobreposição ou duplicação** — dois blocos cobrindo essencialmente o mesmo trecho do documento.
        4. **Lacuna** — salto evidente entre o fim de um bloco e o início do seguinte, indicando conteúdo não segmentado.
        5. **Falha de âncora** — blocos marcados como "TEXTO NÃO EXTRAÍDO", sinal de que os offsets escolhidos não eram literais ou não eram únicos.

        ## Como escrever os insights
        Os `insights` serão inseridos literalmente no prompt de segmentação do próximo ciclo, que processa o documento por janelas e identifica **um bloco por vez**. Portanto:

        - Escreva em português, no imperativo, endereçados a quem vai segmentar.
        - Sejam **concretos e específicos deste documento**, não descrições abstratas.
        - Se houve falha de âncora, incluam orientação sobre como escolher offsets localizáveis.
        - **Nunca instruam a agrupar unidades irmãs** num mesmo bloco só para deixar o resultado mais uniforme: o remédio para granularidade desigual é dividir o bloco englobante, nunca fundir os blocos menores.
        - Não repitam as regras genéricas que já existem no prompt (literalidade dos offsets, retornar JSON, etc.) — acrescentem apenas o que é específico deste documento.
        - Máximo de 10 diretrizes, em lista com marcadores.

        ## Instruções de resposta
        Retorne **exclusivamente** um objeto JSON válido com as chaves:
        - `"inconsistencias"`     : array de strings — cada problema encontrado, citando os índices dos blocos envolvidos (ex.: "bloco [3] agrupa os subitens (a) a (w)"). Array vazio se não houver nenhum.
        - `"consistente"`         : booleano — `true` somente se nenhum dos cinco problemas acima ocorrer
        - `"insights"`            : string — as diretrizes para o próximo ciclo, em lista com marcadores. String vazia se `consistente` for `true`.
        """


def analisar_blocos(
    blocos: list[dict],
    model: str,
    insights_atuais: str = "",
    verboso: bool = True,
    padrao: str = "",
) -> dict:
    """Submete o resumo dos blocos ao analisador e devolve seu veredito."""
    if not blocos:
        vazio = {
            "inconsistencias": ["Nenhum bloco foi produzido pela segmentação."],
            "consistente": False,
            "insights": "",
        }
        # `padrao_identificado` só existe na resposta quando o auditor deduz a
        # estrutura; com padrão externo, a chave não faz parte do schema.
        if not padrao.strip():
            vazio["padrao_identificado"] = ""
        return vazio

    if verboso:
        print("── Analisando a estrutura dos blocos... ───────────────\n")

    prompt = construir_prompt_analise(
        resumir_blocos(blocos), len(blocos), insights_atuais, padrao
    )
    return completar_json(prompt, model, sistema=SISTEMA_ANALISE)


# ──────────────────────────────────────────────────────────────────────────────
# Loop de correção
# ──────────────────────────────────────────────────────────────────────────────

def segmentar_com_correcao(
    paginas: list[dict],
    model: str = "gpt-4o",
    verboso: bool = True,
    janela_paginas: int = 20,
    max_ciclos: int = 0,
    exibir_analise=None,
    padrao: str = "",
) -> tuple[list[dict], list[dict]]:
    """
    Segmenta o documento e repete o processo enquanto o analisador apontar
    inconsistências estruturais, enriquecendo o prompt a cada ciclo.

    `padrao` é a especificação estrutural já pronta (ver `identificador.py`),
    constante ao longo de todos os ciclos: o módulo roda **uma vez**, fora
    deste laço, porque a estrutura do documento não muda entre ciclos.

    O loop roda até o analisador aprovar o resultado, ou até ele deixar de
    produzir diretrizes — sem diretrizes novas não há o que reinjetar no
    prompt. Não há detecção de estagnação: uma segmentação que se repete
    indefinidamente sem ser aprovada só é interrompida por `max_ciclos`, o
    teto opcional, que em `0` (padrão) significa sem limite.

    Retorna `(blocos_do_ultimo_ciclo, historico)`.
    """
    insights  = ""
    historico = []
    blocos    = []
    ciclo     = 0

    SEP = "█" * 62
    while True:
        ciclo += 1
        de_quantos = f"/{max_ciclos}" if max_ciclos else ""
        print(f"\n{SEP}")
        print(f"  CICLO {ciclo}{de_quantos} — segmentação"
              f"{' (prompt enriquecido)' if insights else ' (prompt genérico)'}")
        print(f"{SEP}")

        blocos  = processar_documento_completo(
            paginas,
            model=model,
            verboso=verboso,
            janela_paginas=janela_paginas,
            insights=insights,
            padrao=padrao,
        )
        analise = analisar_blocos(blocos, model, insights, verboso, padrao)

        # `blocos` guarda a extração íntegra do ciclo, não só a contagem: o
        # último ciclo não é necessariamente o melhor, e comparar as versões
        # exige ter cada uma delas.
        historico.append({
            "ciclo": ciclo,
            "blocos_produzidos": len(blocos),
            "insights_aplicados": insights,
            "analise": analise,
            "blocos": blocos,
        })

        if exibir_analise:
            exibir_analise(analise, ciclo)

        if analise.get("consistente"):
            print(f"\n  ✓ Ciclo {ciclo}: blocos considerados estruturalmente consistentes.\n")
            break

        novos_insights = (analise.get("insights") or "").strip()

        if not novos_insights:
            print(f"\n  ⚠  Ciclo {ciclo}: analisador apontou problemas mas não produziu "
                  f"diretrizes. Encerrando.\n")
            break

        insights = novos_insights

        if max_ciclos and ciclo >= max_ciclos:
            print(f"\n  ⚠  Teto de {max_ciclos} ciclo(s) atingido com inconsistências "
                  f"pendentes.\n")
            break

    return blocos, historico
