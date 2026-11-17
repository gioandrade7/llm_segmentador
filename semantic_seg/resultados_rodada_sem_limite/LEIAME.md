# resultados_rodada_sem_limite — rodada de referência

**Rodada principal, com o código atual.** É a rodada registrada no Vault (`Mestrado/Avaliação/Avaliação da segmentação.md`).

- **Data:** 15/11/2026, das 18:31 às 20:47. 41 documentos terminaram sem erro (`status.txt`).
- **Comando:** `python rodar_rodada.py --raiz resultados_rodada_sem_limite`, com 31 documentos em paralelo.
- **Parâmetros:** janela de 10 páginas, no máximo 5 ciclos, modelo `gpt-5.6-terra`.
- **Código:** o auditor recebe o texto inteiro com a segmentação marcada (`marcar_cortes`: `───── corte ─────`, `⟪SEM BLOCO⟫`, `⟪SOBREPOSIÇÃO⟫`). Não há limite de diretrizes: o auditor devolve o conjunto completo a cada ciclo. Esse código não está commitado; a diferença em relação ao commit `5b70291` está descrita na nota do Vault.
- **Documentos:** os 41 que têm gabarito: 29 normas do TCU, 10 leis de 2017, o edital Inep 64/2026 e a sec-rules.

| Coleção | Docs | Aprovados | F1 exato (último ciclo) | F1 exato (melhor ciclo) | Cobertura |
|---|---|---|---|---|---|
| tcu | 29 | 17 | 0,946 | 0,955 | 0,997 |
| lei_2017 | 10 | 10 | 0,698 | 0,698 | 1,000 |
| edital-inep-064 | 1 | 0 | 0,965 | 0,987 | 0,994 |
| sec-rules | 1 | 0 | 0,834 | 0,865 | 0,980 |

Valores médios por coleção, calculados a partir dos `*_avaliacao.json`.

**Organização:** `{coleção}/{KEY}/{KEY}*`, com `_cicloN.json`, `_correcao.json`, `_padrao.json`, `_avaliacao.json/.png` e `{KEY}.log`.

**Rodadas relacionadas:**
- `../resultados_texto_marcado_sem_limite/`: o mesmo código, executado de novo em 8 destes documentos (mede a variação entre execuções).
- `../resultados_5ciclos/`: linha de base com o auditor antigo.
