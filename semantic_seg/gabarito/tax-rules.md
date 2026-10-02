%%
Gabarito de segmentação — tax-rules
Texto-fonte: text_extraction/out/tax-rules/paginas
Status: RASCUNHO escrito a partir dos cabeçalhos `##` do texto-fonte — revisar a definição de unidade

Sintaxe:
- Uma linha por nó: `- [tipo] início do trecho`
- Indentação de 2 espaços por nível (filho 2 espaços à direita do pai)
- Tipos: título, seção
- Início do trecho copiado do texto extraído, a partir do marcador; formatação (**, #) pode ser omitida
- Ordem das linhas = ordem do documento; o trecho de um nó vai até o início da próxima linha
- Cada nó guarda só o próprio texto
- Parágrafos fazem parte do nó da seção em que estão; não viram nós próprios (não têm marcador), como no conference
- As frases em negrito (as regras de integridade) ficam dentro do parágrafo; não são marcadores
- Ruído (não anotado): separadores `---` de quebra de página
%%

- Documento
  - [título] Tax Record Management System
  - [seção] Overview and Purpose
  - [seção] Data Structure and Key Identifiers
  - [seção] Geographic Data Integrity
  - [seção] Financial Data Relationships
  - [seção] Regional Processing Standards
  - [seção] Common Operational Scenarios
  - [seção] Quality Assurance and Monitoring
