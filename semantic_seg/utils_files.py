import os

def carregar_paginas(diretorio: str, extensao: str = ".md") -> list[dict]:
    """
    Carrega todos os arquivos de um diretório como páginas individuais.
    Os arquivos são ordenados pelo nome para preservar a sequência do documento.
 
    Parâmetros
    ----------
    diretorio : caminho para a pasta com os arquivos de página
    extensao  : extensão dos arquivos a considerar (padrão: .md)
 
    Retorna
    -------
    Lista de dicts ordenada pelo nome do arquivo:
        [
            {"pagina": 1, "arquivo": "pagina_001.md", "texto": "..."},
            {"pagina": 2, "arquivo": "pagina_002.md", "texto": "..."},
            ...
        ]
    """
    arquivos = sorted(
        f for f in os.listdir(diretorio) if f.endswith(extensao)
    )
 
    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum arquivo '{extensao}' encontrado em '{diretorio}'."
        )
 
    paginas = []
    for i, nome in enumerate(arquivos, start=1):
        caminho_arquivo = os.path.join(diretorio, nome)
        with open(caminho_arquivo, "r", encoding="utf-8") as f:
            texto = f.read()
        paginas.append({
            "pagina":  i,
            "arquivo": nome,
            "texto":   texto,
        })
 
    print(f"{len(paginas)} página(s) carregada(s) de '{diretorio}':")
    for p in paginas:
        print(f"  [{p['pagina']:>3}] {p['arquivo']}  ({len(p['texto']):,} chars)")
 
    return paginas

def montar_texto_completo(paginas: list[dict]) -> str:
    """
    Concatena a lista de páginas em um único texto contínuo,
    inserindo marcadores explícitos de página entre elas.
    Esses marcadores permitem que o LLM e o código rastreiem
    em qual página cada trecho se encontra.
    """
    partes = [
        f"<!-- PÁGINA {p['pagina']} -->\n{p['texto']}"
        for p in paginas
    ]
    return "\n\n".join(partes)

def _montar_janela(texto_completo: str, posicoes: list[int],
                   idx_pag_atual: int, pos_atual: int, pos_fim_jan: int) -> str:
    """
    Fatia o texto de pos_atual até pos_fim_jan.
    Se pos_atual estiver no meio de uma página (não coincide com o início
    do marcador), injeta o marcador dessa página no topo da janela para
    que o LLM saiba sempre em qual página está lendo.

    `posicoes[idx]` é a posição de caractere do marcador da página `idx + 1`.
    """
    pag_num = idx_pag_atual + 1
    pag_pos = posicoes[idx_pag_atual]
    janela = texto_completo[pos_atual:pos_fim_jan]

    # pos_atual está no meio da página → o marcador ficou para trás
    if pos_atual > pag_pos:
        prefixo = f"<!-- PÁGINA {pag_num} -->\n"
        janela = prefixo + janela

    return janela