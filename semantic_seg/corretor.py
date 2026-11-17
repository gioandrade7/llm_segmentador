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
análise. A análise recebe o texto integral com os cortes do ciclo marcados:
custa uma vez o documento, pouco perto das janelas que a segmentação reenvia,
e mostra o que as bordas dos blocos escondem — unidades engolidas no miolo de
um bloco e texto que ficou fora de qualquer bloco.
"""

import re
from collections import Counter

from utils_files import montar_texto_completo
from utils_llm import completar_json, processar_documento_completo, secao_estrutura

# Marcações inseridas no texto enviado ao analisador. O segmentador não as vê.
CORTE      = "───── corte ─────"
SEM_BLOCO  = ("⟪SEM BLOCO⟫", "⟪/SEM BLOCO⟫")
SOBREPOSTO = ("⟪SOBREPOSIÇÃO⟫", "⟪/SOBREPOSIÇÃO⟫")

SISTEMA_ANALISE = (
    "Você é um auditor de segmentação de documentos. Avalia criticamente a "
    "consistência estrutural de blocos já segmentados e escreve diretrizes "
    "corretivas. Responde sempre com um único objeto JSON válido, sem texto adicional."
)


# ──────────────────────────────────────────────────────────────────────────────
# Texto marcado
# ──────────────────────────────────────────────────────────────────────────────

def _tem_texto(trecho: str) -> bool:
    """Se o trecho tem conteúdo além de espaço e marcadores de página."""
    return any(c.isalnum() for c in re.sub(r"<!--.*?-->", "", trecho))


def marcar_cortes(texto_completo: str, blocos: list[dict]) -> tuple[str, list[dict]]:
    """
    O texto do documento com a segmentação do ciclo marcada.

    Usa as posições que o pipeline gravou em cada bloco (`pos_inicio`,
    `pos_fim`), sem refazer a busca das âncoras. Insere `CORTE` no início de
    cada bloco e envolve em `SEM_BLOCO` o texto que nenhum bloco cobre e em
    `SOBREPOSTO` o que mais de um cobre. Espaço e marcadores de página fora de
    bloco não são marcados.

    Retorna `(texto_marcado, blocos_sem_posicao)`: os blocos cujas âncoras não
    foram localizadas não têm onde ser marcados e vão à parte.
    """
    spans = [
        (b["pos_inicio"], b["pos_fim"]) for b in blocos
        if b.get("pos_inicio") is not None and b.get("pos_fim") is not None
    ]
    sem_posicao = [b for b in blocos if b.get("pos_inicio") is None or b.get("pos_fim") is None]

    inicios    = Counter(ini for ini, _ in spans)
    fronteiras = sorted({0, len(texto_completo), *(p for span in spans for p in span)})

    partes, estado = [], None
    for a, b in zip(fronteiras, fronteiras[1:]):
        trecho    = texto_completo[a:b]
        cobertura = sum(1 for ini, fim in spans if ini <= a and b <= fim)
        if cobertura > 1:
            novo = SOBREPOSTO
        elif cobertura == 0 and _tem_texto(trecho):
            novo = SEM_BLOCO
        else:
            novo = None

        if novo is not estado and estado:
            partes.append(estado[1])
        partes.extend([f"\n{CORTE}\n"] * inicios[a])
        if novo is not estado and novo:
            partes.append(novo[0])
        estado = novo
        partes.append(trecho)

    if estado:
        partes.append(estado[1])

    return "".join(partes), sem_posicao


def _listar_sem_posicao(blocos: list[dict]) -> str:
    """Seção do prompt com os blocos que não puderam ser marcados no texto."""
    if not blocos:
        return ""
    linhas = "\n".join(
        f"- págs. {b.get('pagina_inicio', '?')}–{b.get('pagina_fim', '?')} | "
        f"início: {b.get('offset_inicio') or ''} | fim: {b.get('offset_fim') or ''}"
        for b in blocos
    )
    return f"""
        ## Blocos não localizados
        O segmentador devolveu estes blocos, mas suas âncoras não foram encontradas no texto, então eles não aparecem nas marcações acima:

        ```
        {linhas}
        ```
    """


# ──────────────────────────────────────────────────────────────────────────────
# Análise
# ──────────────────────────────────────────────────────────────────────────────

def construir_prompt_analise(
    texto_marcado: str,
    total_blocos: int,
    sem_posicao: list[dict],
    insights_atuais: str,
    padrao: str = "",
) -> str:
    """
    Monta o prompt do analisador a partir do texto marcado com os cortes de um
    ciclo (ver `marcar_cortes`).

    Com `padrao` (vindo do módulo `identificador`, que leu o texto fonte), o
    auditor **não deduz** a estrutura a partir dos blocos: ele confere a
    segmentação contra uma especificação de fonte independente. A hierarquia e
    as regras de corte entram pela mesma `secao_estrutura` do segmentador: o
    auditor julga contra exatamente o que o segmentador recebeu.
    """
    secao_anterior = f"""
        ## Diretrizes usadas no ciclo anterior
        As diretrizes abaixo já estavam no prompt que produziu a segmentação acima. Se um problema que elas tratam não aparece mais, é porque elas funcionaram: mantenha-as. Refine ou substitua só as que não resolveram o problema ou causaram um novo. O campo `insights` da sua resposta deve conter o conjunto **completo** de diretrizes para o próximo ciclo — não apenas o que mudou.

        ```
        {insights_atuais.strip()}
        ```
    """ if insights_atuais.strip() else ""

    return f"""Você é um auditor de segmentação de documentos estruturados.

        Um pipeline automático segmentou um documento em {total_blocos} bloco(s). A estrutura do documento foi identificada de forma independente, a partir do texto fonte, e está descrita abaixo junto com as regras de corte que o segmentador recebeu. Sua tarefa é verificar se os blocos respeitam essa estrutura e essas regras e, se não respeitarem, escrever as diretrizes que corrigirão a próxima segmentação. Se respeitarem, apenas confirme a consistência e não escreva diretrizes.
{secao_estrutura(padrao)}

        ## Documento segmentado
        Abaixo está o texto integral do documento, na ordem em que o segmentador o leu, com a segmentação marcada:
        - `{CORTE}`, numa linha própria, marca o início de cada bloco. O bloco vai até o corte seguinte, ou até um `{SEM_BLOCO[0]}`.
        - `{SEM_BLOCO[0]} … {SEM_BLOCO[1]}` envolve texto que não entrou em nenhum bloco.
        - `{SOBREPOSTO[0]} … {SOBREPOSTO[1]}` envolve texto que entrou em mais de um bloco.
        - `<!-- PÁGINA N -->` marca o início de cada página.

        Essas marcações existem só para você: o segmentador lê o texto sem elas.

        ```
        {texto_marcado}
        ```
{_listar_sem_posicao(sem_posicao)}{secao_anterior}
        ## O que verificar
        1. **Bloco englobante**: dentro de um bloco aparece, sem corte antes dele, o marcador de outra unidade, que deveria ser um bloco próprio.
        2. **Texto sem bloco**: um trecho `{SEM_BLOCO[0]}` com texto do documento — uma unidade pulada, ou texto antes do primeiro bloco ou depois do último. Ruído de página (cabeçalhos, rodapés, números de página) fora de bloco não é problema.
        3. **Sobreposição**: um trecho `{SOBREPOSTO[0]}`.
        4. **Sequência quebrada**: salto na numeração de unidades irmãs (`(3)` logo depois de `(1)`) ou uma lista que termina antes do que o texto anuncia.
        5. **Corte indevido**: corte num ponto que, pelas regras de corte, não é unidade (legenda, item de lista fora da hierarquia, remissão, texto citado de outra norma), ou no meio do texto de uma unidade.

        ## Critério de aprovação
        `consistente: true` somente se você não encontrar nenhum dos problemas acima. Reprove só com evidência concreta no texto marcado; mas, havendo evidência, não aprove — inclusive para um problema que você mesmo já apontou num ciclo anterior e que continua presente.

        ## Erro de extração
        Se o problema vier da extração do PDF — texto fora da ordem de leitura, um cabeçalho emitido longe da sua seção, colunas misturadas —, o segmentador não tem como corrigi-lo: ele lê o texto em ordem, com um ponteiro que só avança, e não pode reordená-lo. Nesse caso marque `erro_extracao: true`, descreva-o em `problemas` e não escreva diretriz sobre ele.

        ## Como escrever os insights, caso necessário
        Os insights serão inseridos literalmente no prompt de segmentação do próximo ciclo, que processa o documento por janelas e identifica **um bloco por vez**. Portanto:

        - Escreva em português, no imperativo, endereçados a quem vai segmentar.
        - Sejam **concretos e específicos deste documento**, não descrições abstratas.
        - Identifique cada unidade pelo marcador e pelo texto que a abre, com a página. **Nunca** pelas marcações (`corte`, `SEM BLOCO`) nem pela posição do bloco na lista: o segmentador não as conhece.
        - **Nunca instruam a agrupar unidades irmãs** num mesmo bloco: o remédio para um bloco englobante é dividi-lo, nunca fundir os blocos vizinhos.
        - Em lista com marcadores, das que afetam mais blocos às que afetam menos.

        ## Resposta
        Retorne exclusivamente este JSON, com as chaves nesta ordem. Liste os problemas antes de decidir: cada diretriz deve corrigir um problema listado.

        ```
        {{
            "problemas": [
                {{"onde": "`(4)`, pág. 7", "tipo": "englobante", "descricao": "o bloco de `(4)` contém também o texto de `(5) Determinations Required`"}}
            ],
            "erro_extracao": false,
            "consistente": false,
            "insights": "- Termine o bloco de `(4)` imediatamente antes de `(5) Determinations Required` (pág. 7) e inicie um bloco novo ali."
        }}
        ```

        `tipo` é um de: `englobante`, `sem_bloco`, `sobreposicao`, `ancora`, `sequencia`, `corte_indevido`. Sem problemas: `"problemas": []`, `"consistente": true`, `"insights": ""`.
        """


def analisar_blocos(
    blocos: list[dict],
    texto_completo: str,
    model: str,
    insights_atuais: str = "",
    padrao: str = "",
) -> dict:
    """Submete o texto marcado com os blocos ao analisador e devolve seu veredito."""
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


    texto_marcado, sem_posicao = marcar_cortes(texto_completo, blocos)
    prompt = construir_prompt_analise(
        texto_marcado, len(blocos), sem_posicao, insights_atuais, padrao
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
    # O mesmo texto que o pipeline monta: as posições gravadas nos blocos
    # apontam para ele.
    texto_completo = montar_texto_completo(paginas)
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
        analise = analisar_blocos(blocos, texto_completo, model, insights, padrao)
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
