# resultados_fecho — 43 documentos, com a regra do fecho

Rodada de `rodar_rodada.py` sobre os 43 documentos com gabarito: 29 normas do TCU, 10 leis de 2017, o edital Inep 64/2026, a sec-rules, a Lei 13.467/2017 (`L13467`) e a LGPD compilada (`L13709compilado`). As `REGRAS_DE_CORTE` incluem a regra do fecho: local e data, fórmulas de encerramento e assinaturas formam um único bloco, até o fim do documento.

- **Data:** 19/11/2026, em três partes:
  - as leis de 2017, das 13:43 às 13:50;
  - 5 normas do TCU e a sec-rules, das 13:53 às 20:10, com `--paralelo 6`;
  - o resto, das 21:21 às 23:46, com `--paralelo 31`.
  Os 43 documentos terminaram sem erro (`status.txt`).
- **Comando:** `PYTHONUNBUFFERED=1 caffeinate -i -s ../.venv/bin/python rodar_rodada.py --raiz resultados_fecho --paginas-amostra 5`, com `--grupos`/`--documentos` em cada parte.
- **Parâmetros:** janela de 10 páginas, no máximo 5 ciclos, modelo `gpt-5.6-terra`, 5 páginas por região na amostra do identificador.
- **Interrupção:** entre 16:02 e 20:01 o Mac dormiu, com a tampa fechada na bateria. A sec-rules ficou parada no ciclo 5 e continuou sozinha depois que o Mac acordou.

| Coleção | Docs | Aprovados | F1 exato (último ciclo) | F1 exato (melhor ciclo) | Cobertura |
|---|---|---|---|---|---|
| tcu | 29 | 20 | 0,964 | 0,985 | 0,999 |
| lei_2017 | 10 | 10 | 1,000 | 1,000 | 1,000 |
| edital-inep-064 | 1 | 0 | 0,997 | 0,997 | 1,000 |
| sec-rules | 1 | 0 | 0,825 | 0,838 | 0,981 |
| L13467 | 1 | 1 | 1,000 | 1,000 | 1,000 |
| L13709compilado | 1 | 0 | 0,990 | 0,990 | 0,996 |

Valores médios por coleção, calculados a partir dos `*_avaliacao.json`.

- **Erro dominante:** quase todo nó perdido no último ciclo é enumeração engolida.
- **NORMA-5421 (0,664 no último ciclo, 0,994 no ciclo 3):** perde 81 nós porque o `offset_fim` do inciso `VIII` foi localizado numa frase idêntica 11 mil caracteres adiante (camada exata do `anchor.py`).
- **Análise completa:** nota do Vault `Mestrado/Avaliação/Resultados e erros — 43 documentos.md`.

**Organização:** `{grupo}/{KEY}/{KEY}*`. Os `.log` ficam fora do git.
