"""
display.py — Exibição no terminal dos blocos identificados.

Puramente cosmético: nenhuma lógica de pipeline vive aqui.
"""


def exibir_resultado(resultado: dict) -> None:
    """Exibe um único bloco no terminal."""
    print("── Resultado ──────────────────────────────────────────")
    if not resultado.get("offset_inicio"):
        print("  Nenhum bloco semântico identificado no texto.")
        return

    pagina_inicio = resultado.get("pagina_inicio", "?")
    pagina_fim    = resultado.get("pagina_fim", "?")
    paginas_str   = (
        f"pág. {pagina_inicio}"
        if pagina_inicio == pagina_fim
        else f"págs. {pagina_inicio}–{pagina_fim}"
    )

    print(f"  Título         : {resultado.get('titulo', 'N/A')}")
    print(f"  Páginas        : {paginas_str}")
    print(f"  Offset início  : {resultado['offset_inicio']}")
    print(f"  Offset fim     : {resultado['offset_fim']}")
    print(f"  Motivo         : {resultado.get('motivo', '')}")
    texto = resultado.get("texto")
    if texto:
        preview = texto[:300].replace("\n", "↵ ")
        reticencias = "..." if len(texto) > 300 else ""
        print(f"  Texto ({len(texto):,} chars): {preview}{reticencias}")
    else:
        print(f"  Texto          : ⚠ não extraído (âncoras não localizadas)")
    print(f"  {'─' * 50}")


def exibir_padrao(padrao: dict) -> None:
    """Exibe o padrão estrutural identificado — uma vez, antes da segmentação."""
    SEP = "─" * 70
    print(f"\n{SEP}")
    print("  PADRÃO ESTRUTURAL IDENTIFICADO")
    print(SEP)

    pags  = padrao.get("paginas_amostradas") or []
    total = padrao.get("total_paginas")
    if pags and total:
        print(f"  Amostra    : págs. {', '.join(map(str, pags))} de {total}")

    if not padrao.get("tem_padrao"):
        print("  Padrão     : nenhum — segmentação por mudança de assunto")
        print(f"  Confiança  : {padrao.get('confianca', 'N/A')}")
        print(SEP)
        return

    print(f"  Modo       : {padrao.get('modo', 'N/A')}")

    hierarquia = padrao.get("hierarquia") or []
    if hierarquia:
        print("  Hierarquia :")
        for h in hierarquia:
            exemplo = h.get("exemplo")
            sufixo  = f"  (ex.: {exemplo})" if exemplo else ""
            print(f"    nível {h.get('nivel')} — {h.get('nome') or '?'}{sufixo}")

    corte = padrao.get("nivel_de_corte")
    nome  = next((h.get("nome") for h in hierarquia if h.get("nivel") == corte), "")
    print(f"  Corte      : nível {corte}" + (f" ({nome})" if nome else "") + "  [só registro: o prompt corta em todo marcador]")

    justificativa = (padrao.get("justificativa_corte") or "").strip()
    if justificativa:
        print(f"  Motivo     : {justificativa}")

    print(f"  Confiança  : {padrao.get('confianca', 'N/A')}")
    print(SEP)


def exibir_analise(analise: dict, ciclo: int) -> None:
    """Exibe o veredito do corretor sobre os blocos de um ciclo."""
    SEP = "─" * 70
    print(f"\n{SEP}")
    print(f"  ANÁLISE ESTRUTURAL — ciclo {ciclo}")
    print(SEP)
    if analise.get("padrao_identificado"):
        print(f"  Padrão identificado: {analise['padrao_identificado']}")

    inconsistencias = analise.get("inconsistencias") or []
    if inconsistencias:
        print(f"  Inconsistências ({len(inconsistencias)}):")
        for item in inconsistencias:
            print(f"    ✗ {item}")
    else:
        print("  Inconsistências: nenhuma")

    veredito = "consistente" if analise.get("consistente") else "inconsistente"
    print(f"  Veredito: {veredito}")

    insights = (analise.get("insights") or "").strip()
    if insights:
        print("  Diretrizes para o próximo ciclo:")
        for linha in insights.split("\n"):
            print(f"    │ {linha}")
    print(SEP)


def exibir_resumo_blocos(blocos: list[dict]) -> None:
    """Exibe um resumo tabular de todos os blocos identificados."""
    SEP = "─" * 70
    print(f"\n{SEP}")
    print(f"  {'#':>3}  {'Págs.':^12}  Título")
    print(SEP)
    for i, b in enumerate(blocos, 1):
        p_ini   = b.get("pagina_inicio", "?")
        p_fim   = b.get("pagina_fim", "?")
        paginas = f"{p_ini}–{p_fim}" if p_ini != p_fim else str(p_ini)
        titulo  = (b.get("titulo") or "N/A")[:50]
        print(f"  {i:>3}  {paginas:^12}  {titulo}")
    print(SEP)
    print(f"  Total: {len(blocos)} bloco(s)\n")
