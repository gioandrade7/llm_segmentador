%%
Gabarito de segmentação — conference
Texto-fonte: text_extraction/out/conference/paginas
Status: RASCUNHO gerado por regex a partir do texto-fonte — revisar linha a linha

Sintaxe:
- Uma linha por nó: `- [tipo] início do trecho`
- Indentação de 2 espaços por nível (filho 2 espaços à direita do pai)
- Tipos: título, resumo, palavras-chave, seção, subseção, referências, referência
- Início do trecho copiado do texto extraído, a partir do marcador; formatação (**, _, <u>, #) pode ser omitida
- Ordem das linhas = ordem do documento; o trecho de um nó vai até o início da próxima linha
- Cada nó guarda só o próprio texto; o texto dos filhos pertence aos filhos
- Parágrafos fazem parte do nó da seção/subseção em que estão; não viram nós próprios (não têm marcador)
- Cada referência [n] é um nó, filho de REFERENCES
- Ruído (não anotado): legendas de figura (Fig. 1–6), tabelas (TABLE I e II, com o conteúdo) e o título "III. METHODOLOGY", extraído fora de lugar no topo da pág. 2
- Comentários no fim da linha, após %%
%%

- Documento
  - [título] Enhancing Anti-Money Laundering: Development of a Synthetic  %% inclui autores e afiliações
  - [resumo] Abstract —Money laundering remains a continuous global
  - [palavras-chave] Index Terms —Anti-Money Laundering (AML), Transaction Monitoring,
  - [seção] I. INTRODUCTION
  - [seção] II. RELATED WORK
  - [seção] The framework employed in creating the synthetic  %% III. METHODOLOGY: o título foi extraído fora de lugar (topo da pág. 2) e é ruído; a seção começa aqui
  - [seção] IV. DATASET DESCRIPTION
    - [subseção] A. Typologies
  - [seção] V. RESULTS
    - [subseção] A. Comparison
    - [subseção] B. Experiment
  - [seção] VI. CONCLUSION
  - [referências] REFERENCES
    - [referência] [1] M. S. Korejo, R. Rajamanickam, and
    - [referência] [2] M. A. Naheem, ”Money laundering: A
    - [referência] [3] E. Eifrem, ”How graph technology can
    - [referência] [4] A. I. Canhoto, ”Leveraging machine learning
    - [referência] [5] M. Jullum, A. Løland, R. B.
    - [referência] [6] X. Cheng et al., ”Combating emerging
    - [referência] [7] Europol, ”From Suspicion to Action: Converting
    - [referência] [8] B. Oztas, D. Cetinkaya, F. Adedoyin,
    - [referência] [9] T. Suzumura and H. Kanezashi, ”Anti-Money
    - [referência] [10] A. Tundis, S. Nemalikanti, and M.
    - [referência] [11] M. Shokry, A. Ehab, M. A.
    - [referência] [12] E. Altman et al., ”Realistic Synthetic
    - [referência] [13] M. Mahootiha, ”Money laundering data,” 2020.
    - [referência] [14] E. A. Lopez-Rojas and S. Axelsson,
    - [referência] [15] Y. A. Le Borgne, W. Siblini,
    - [referência] [16] S. H. Chen and R. Venkatachalam,
    - [referência] [17] D. Valbuena, P. H. Verburg, and
    - [referência] [18] K. Plaksiy, A. Nikiforov, and N.
    - [referência] [19] Ping. He, “A typological study on
    - [referência] [20] S. M. Irwin, A. Raymond Choo,
    - [referência] [21] “National risk assessment of money laundering
    - [referência] [22] “High-risk and other monitored jurisdictions -
    - [referência] [23] M. C. Johnson, and S. R.
    - [referência] [24] J. de J. Rocha-Salazar, M. J.
    - [referência] [25] R. Desrousseaux, G. Bernard and J.
    - [referência] [26] Z. Rouhollahi, A. Beheshti, S. Mousaeirad,
    - [referência] [27] M. Betron, “The state of anti-fraud
    - [referência] [28] J. Simser, “Money laundering: emerging threats
    - [referência] [29] H. Heinrich-B¨oll-Stiftung and R. Sch¨onenberg, Eds.,
    - [referência] [30] M. Riccardi and M. Levi, “Cash,
    - [referência] [31] M. Starnini et al., “Smurf-Based Anti-money
    - [referência] [32] M. M. El-Banna, M. H. Khafagy
    - [referência] [33] B. Unger and E. M. Busuioc,
    - [referência] [34] J. McDowell and G. Novis, “The
    - [referência] [35] S. Gao, D. Xu, H. Wang
    - [referência] [36] M. A. Naheem, “Money laundering: A
    - [referência] [37] R. M. Suresh and R. Padmajavalli,
    - [referência] [38] A. A. S. Alsuwailem and A.
