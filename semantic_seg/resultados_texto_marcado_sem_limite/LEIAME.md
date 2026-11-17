# resultados_texto_marcado_sem_limite — segunda execução da configuração atual

**Mesmo código e mesmos parâmetros de `../resultados_rodada_sem_limite/`, numa amostra de 8 documentos.** Serviu de teste antes da rodada completa. Hoje é a segunda amostra da mesma configuração.

- **Data:** 12/11/2026 (normas do TCU) e 15/11/2026 (sec-rules às 17:12, edital às 17:46). Ver `tcu/status.txt` e `status_longos.txt`.
- **Parâmetros:** janela de 10 páginas, no máximo 5 ciclos, modelo `gpt-5.6-terra`.
- **Código:** o auditor recebe o texto marcado e as diretrizes não têm limite, como na rodada de referência.
- **Documentos:** TCU `NORMA-6140`, `NORMA-18201`, `NORMA-20055`, `NORMA-20182`, `NORMA-21260`, `NORMA-21767`, o edital Inep 64/2026 e a sec-rules.

| Coleção | Docs | Aprovados | F1 exato (último ciclo) | F1 exato (melhor ciclo) | Cobertura |
|---|---|---|---|---|---|
| tcu | 6 | 4 | 0,947 | 0,969 | 0,997 |
| edital-inep-064 | 1 | 0 | 0,981 | 0,981 | 0,995 |
| sec-rules | 1 | 0 | 0,788 | 0,873 | 0,990 |

**Para que serve:** comparada com a rodada de referência, mostra quanto a amostragem do modelo faz um mesmo documento variar entre execuções. A NORMA-20182 tirou 0,845 numa execução e 0,989 na outra; a NORMA-21260 tirou 0,947 e 0,776. A nota do Vault usa esses números, por isso esta pasta não deve ser apagada.

**Organização:** `{coleção}/{KEY}/{KEY}*`, como nas outras rodadas.
