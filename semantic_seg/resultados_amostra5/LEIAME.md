# resultados_amostra5 — identificador com amostra de 5 páginas por região

**Mesmo código e mesmos documentos de `resultados_rodada_sem_limite/` (rodada de referência anterior, fora da árvore desde 17/11/2026; está no commit `73a7100`), mudando só a amostra do identificador:** 5 páginas do começo, 5 do meio e 5 do fim, em vez de 2. Como as normas do TCU e as leis têm até 12 páginas, nelas o identificador lê o documento inteiro.

- **Data:** 16/11/2026, das 12:21 às 17:26. 41 documentos terminaram sem erro (`status.txt`).
- **Comando:** `PYTHONUNBUFFERED=1 ../.venv/bin/python rodar_rodada.py --raiz resultados_amostra5 --paginas-amostra 5 --paralelo 8`, rodado sob `caffeinate`.
- **Parâmetros:** janela de 10 páginas, no máximo 5 ciclos, modelo `gpt-5.6-terra`. Os 8 documentos em paralelo não mudam o resultado, só o tempo.
- **Interrupções:** o Mac dormiu duas vezes durante a rodada (às 12:31 por ociosidade e entre 15:21 e 16:45 com a tampa fechada). As chamadas em andamento foram refeitas pelo próprio cliente da API depois do timeout, e os resultados não são afetados.

| Coleção | Docs | Aprovados | F1 exato (último ciclo) | F1 exato (melhor ciclo) | Cobertura |
|---|---|---|---|---|---|
| tcu | 29 | 18 | 0,954 | 0,959 | 0,998 |
| lei_2017 | 10 | 9 | 0,723 | 0,723 | 1,000 |
| edital-inep-064 | 1 | 0 | 0,985 | 0,993 | 0,999 |
| sec-rules | 1 | 0 | 0,761 | 0,808 | 0,973 |

Valores médios por coleção, calculados a partir dos `*_avaliacao.json`. Na rodada de referência, o TCU tem F1 de 0,946 e cobertura de 0,997.

**O que a amostra maior mudou na hierarquia** (comparado com a rodada de referência):

- **Corrigiu os três níveis que faltavam:** os itens da NORMA-6140 e as alíneas das NORMA-6257 e NORMA-21767. Os nós desses níveis que eram engolidos caíram de 29 para 0.
- **Tirou níveis inventados:** o título de capítulo da NORMA-21260 (F1 de 0,776 para 0,981), a ementa e o preâmbulo da LEI-2017-13421 (de 0,364 para 0,667) e os considerandos das NORMA-20797 e NORMA-20901.
- **Criou níveis inventados novos:**
  - os considerandos da NORMA-19297, com 7 cortes espúrios (F1 de 0,975 para 0,905);
  - a rubrica da seção como nível próprio na sec-rules, separada de `SEC.`, com 27 fins errados e 21 cortes espúrios (F1 de 0,834 para 0,761).

**Organização:** `{coleção}/{KEY}/{KEY}*`.
