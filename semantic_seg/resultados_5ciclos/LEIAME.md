# resultados_5ciclos — linha de base com o auditor antigo

**Primeira rodada em lote, feita antes de o auditor passar a ver o texto marcado.** Serve como linha de base para medir o que essa mudança trouxe.

- **Data:** 03/11/2026, das 10:53 às 13:27 (`status.txt`).
- **Comando:** `rodar_rodada.py`, com janela de 10 páginas, no máximo 5 ciclos e modelo `gpt-5.6-terra`. São os mesmos parâmetros da rodada de referência.
- **Código:** é o auditor anterior a `marcar_cortes`. Para cada bloco, ele recebia só as páginas, o tamanho e as bordas (300 caracteres do início e 300 do fim), e as diretrizes tinham limite de 5. O prompt ainda checava "granularidade desigual" e "lacuna", e não tinha o critério "texto sem bloco".
- **Documentos:** 29 normas do TCU, 22 leis de 2017 (só 10 têm gabarito e foram avaliadas), o edital Inep 64/2026 e a sec-rules. Ao todo, 41 documentos avaliados, os mesmos da rodada de referência.

| Coleção | Docs avaliados | Aprovados | F1 exato (último ciclo) | F1 exato (melhor ciclo) | Cobertura |
|---|---|---|---|---|---|
| tcu | 29 | 19 | 0,920 | 0,935 | 0,936 |
| lei_2017 | 10 | 10 | 0,726 | 0,726 | 0,999 |
| edital-inep-064 | 1 | 0 | 0,985 | 0,985 | 1,000 |
| sec-rules | 1 | 0 | 0,814 | 0,910 | 0,966 |

Valores médios por coleção, calculados a partir dos `*_avaliacao.json`. Na rodada de referência, o TCU tem F1 de 0,946 e cobertura de 0,997.

**Organização:** `{coleção}/{KEY}/{KEY}*`. Nas coleções há também os gráficos `*_metricas*.png` de `grafico_metricas.py`.
