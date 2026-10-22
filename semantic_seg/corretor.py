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

from utils_llm import completar_json, processar_documento_completo, secao_estrutura

# Quanto de cada bloco vai no resumo enviado ao analisador.
INICIO_AMOSTRA = 300
FIM_AMOSTRA    = 300

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
    segmentação contra uma especificação de fonte independente. A hierarquia e
    as regras de corte entram pela mesma `secao_estrutura` do segmentador: o
    auditor julga contra exatamente o que o segmentador recebeu.
    """
    secao_anterior = f"""
        ## Diretrizes usadas no ciclo anterior
        As diretrizes abaixo já estavam no prompt que produziu os blocos acima. Se um problema que elas tratam não aparece mais, é porque elas funcionaram: mantenha-as. Refine ou substitua só as que não resolveram o problema ou causaram um novo. O campo `insights` da sua resposta deve conter o conjunto **completo** de diretrizes para o próximo ciclo — não apenas o que mudou.

        ```
        {insights_atuais.strip()}
        ```
    """ if insights_atuais.strip() else ""

    return f"""Você é um auditor de segmentação de documentos estruturados.

        Um pipeline automático segmentou um documento em {total_blocos} bloco(s). A estrutura do documento foi identificada de forma independente, a partir do texto fonte, e está descrita abaixo junto com as regras de corte que o segmentador recebeu. Sua tarefa é verificar se os blocos respeitam essa estrutura e essas regras e, se não respeitarem, escrever as diretrizes que corrigirão a próxima segmentação. Se respeitarem, apenas confirme a consistência e não escreva diretrizes.
{secao_estrutura(padrao)}

        ## Blocos produzidos
        Cada bloco aparece com suas páginas, tamanho e os trechos iniciais e finais do texto capturado (`⏎` marca quebra de linha):

        ```
        {resumo}
        ```
{secao_anterior}
        ## O que verificar
        1. **Bloco englobante**: um bloco contém, no início ou no fim, o marcador de outra unidade, que deveria ser um bloco próprio.
        2. **Granularidade desigual**: unidades do mesmo nível são cortadas num trecho do documento e agrupadas em outro.
        3. **Lacuna**: texto do documento fora de qualquer bloco — o fim de um bloco não encosta no início do seguinte, ou páginas sem bloco entre dois blocos.
        4. **Sobreposição**: dois blocos com texto em comum.
        6. **Sequência quebrada**: salto na numeração de unidades irmãs (`(3)` logo depois de `(1)`) ou uma lista que termina antes do que o texto anuncia.
        7. **Corte indevido**: bloco aberto num ponto que, pelas regras de corte, não é unidade (legenda, item de lista fora da hierarquia, remissão, texto citado de outra norma).

        ## Critério de aprovação
        `consistente: true` somente se você não encontrar nenhum dos problemas acima. Reprove só com evidência concreta nas bordas dos blocos; mas, havendo evidência, não aprove — inclusive para um problema que você mesmo já apontou num ciclo anterior e que continua presente.

        ## Erro de extração
        Se o problema vier da extração do PDF — texto fora da ordem de leitura, um cabeçalho emitido longe da sua seção, colunas misturadas —, o segmentador não tem como corrigi-lo: ele lê o texto em ordem, com um ponteiro que só avança, e não pode reordená-lo. Nesse caso marque `erro_extracao: true`, descreva-o em `problemas` e não escreva diretriz sobre ele.

        ## Como escrever os insights, caso necessário
        Os insights serão inseridos literalmente no prompt de segmentação do próximo ciclo, que processa o documento por janelas e identifica **um bloco por vez**. Portanto:

        - Escreva em português, no imperativo, endereçados a quem vai segmentar.
        - Sejam **concretos e específicos deste documento**, não descrições abstratas.
        - Identifique cada unidade pelo marcador e pelo texto que a abre, com a página. **Nunca** pelo número `[n]` do bloco: o segmentador não conhece essa numeração.
        - **Nunca instruam a agrupar unidades irmãs** num mesmo bloco só para deixar o resultado mais uniforme: o remédio para granularidade desigual é dividir o bloco englobante, nunca fundir os blocos menores.
        - Máximo de 5 diretrizes, em lista com marcadores, priorizando as que afetam mais blocos.

        ## Resposta
        Retorne exclusivamente este JSON, com as chaves nesta ordem. Liste os problemas antes de decidir: cada diretriz deve corrigir um problema listado.

        ```
        {{
            "problemas": [
                {{"bloco": 12, "tipo": "englobante", "descricao": "o bloco de `(4)` termina com o texto de `(5) Determinations Required`"}}
            ],
            "erro_extracao": false,
            "consistente": false,
            "insights": "- Termine o bloco de `(4)` imediatamente antes de `(5) Determinations Required` (pág. 7) e inicie um bloco novo ali."
        }}
        ```

        `tipo` é um de: `englobante`, `granularidade`, `lacuna`, `sobreposicao`, `ancora`, `sequencia`, `corte_indevido`. Sem problemas: `"problemas": []`, `"consistente": true`, `"insights": ""`.
        """


def analisar_blocos(
    blocos: list[dict],
    model: str,
    insights_atuais: str = "",
    padrao: str = "",
) -> dict:
    """Submete o resumo dos blocos ao analisador e devolve seu veredito."""
    if not blocos:
        vazio = {
            "consistente": False,
            "insights": "",
        }
        # `padrao_identificado` só existe na resposta quando o auditor deduz a
        # estrutura; com padrão externo, a chave não faz parte do schema.
        if not padrao.strip():
            vazio["padrao_identificado"] = ""
        return vazio


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
    janela_paginas: int = 20,
    max_ciclos: int = 0,
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
            janela_paginas=janela_paginas,
            insights=insights,
            padrao=padrao,
        )
        analise = analisar_blocos(blocos, model, insights, padrao)
        print(f"\n  Auditor: consistente={analise.get('consistente')}")
        print(f"  Diretrizes: {analise.get('insights')}")

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
