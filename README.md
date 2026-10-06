# Pipeline de Segmentação Semântica de Documentos Estruturados

Pesquisa de mestrado no **IComp/UFAM**: identificar e extrair automaticamente os blocos semânticos de documentos estruturados — descobrir onde cada unidade independente do documento começa e termina.

## O problema

Documentos estruturados — um estatuto, uma portaria, um regulamento, um artigo — chegam como um PDF contínuo de dezenas de páginas contendo **muitas unidades independentes**: seções numeradas, artigos, itens. Para qualquer uso a jusante (busca, indexação, análise), é preciso primeiro saber onde cada unidade começa e termina.

Fazer isso por regex ou por regra de layout não generaliza entre coleções: cada uma numera e formata de um jeito — `SEC. 203`, `Section 3-02`, `Art. 14`, `III. METHODOLOGY` — e a mesma fonte muda de formato ao longo dos anos. O corpus do projeto é escolhido por **diversidade estrutural, não por domínio**, justamente porque é essa incompatibilidade entre coleções que torna difícil escrever um único segmentador.

A aposta é que um LLM consegue inferir a estrutura do documento **a partir do próprio documento** e depois aplicá-la, sem configuração por tipo.

## Visão geral

A solução é o pipeline de segmentação, em `semantic_seg/`: três componentes, dois deles num laço. Ele recebe o documento já em **texto paginado** — um arquivo por página — e devolve a lista de blocos.

A tarefa é **puramente segmentação**: não há classificação em categorias pré-definidas nem arquivo de configuração por tipo de documento — o prompt é genérico e se especializa em tempo de execução.

> **Pré-processamento.** Converter o documento em formato digital em texto paginado é pré-requisito, não parte da solução. O `text_extraction/` está no repositório apenas para isso: lê a camada de texto nativa do documento. Qualquer extrator que produza um arquivo por página serve no lugar dele — o pipeline não sabe de onde o texto veio.

> A escolha do extrator, ainda assim, não é neutra para o resultado: um erro de linearização de colunas na extração chega ao Segmentador como se fosse a ordem real do documento, e o laço de correção não tem como desfazê-lo.

## Os três componentes

```
          texto paginado  (`page_NNNN.md`)
                    │
                    │  amostra: N páginas contíguas do começo, do meio e do fim
                    ▼
          ┌──────────────────────┐
          │    IDENTIFICADOR     │   1 chamada, antes de qualquer segmentação
          └──────────────────────┘
                    │
                    │  padrão: modo, hierarquia, nível de corte, confiança
          ┌─────────┴──────────────────────────────┐
          ▼                                        ▼
  ┌──────────────────┐                   ┌──────────────────┐
  │   SEGMENTADOR    │─── blocos ───────▶│     AUDITOR      │
  │ janela deslizante│  (resumo das      │ cinco patologias │
  │  1 bloco/chamada │   bordas)         │                  │
  └──────────────────┘                   └──────────────────┘
          ▲                                        │
          │                                   consistente?
          └────────── insights ───────┬────── não │
                    (novo ciclo)      │           │ sim
                                      │           ▼
                                      │     blocos finais
```

Os três componentes são chamadas de LLM — não-determinísticos e com custo por chamada. O que é determinístico no módulo fica fora deles: a montagem do texto paginado e o reencontro das âncoras no documento.

### Identificador — infere o padrão, uma vez

Roda **antes de qualquer segmentação** e recebe o **texto fonte**. É isso que quebra a circularidade do laço: o padrão passa a vir de uma fonte independente daquilo que está sob suspeita.

A amostra é de N páginas **contíguas** do começo, N do meio e N do fim. A contiguidade é deliberada: para decidir em que nível cortar, é preciso ver onde uma unidade termina e a próxima começa — uma página isolada mostra apenas o cabeçalho de uma unidade.

Ele responde a duas perguntas: o documento tem padrão estrutural? Se tem, qual é a hierarquia e **em que nível cortar** os blocos. O contrato de saída:

```json
{
  "tem_padrao": true,
  "modo": "hierarquico",
  "hierarquia": [
    {"nivel": 1, "exemplo": "Section 3-02"},
    {"nivel": 2, "exemplo": "(a)"},
    {"nivel": 3, "exemplo": "(1)"}
  ],
  "nivel_de_corte": 3,
  "confianca": "alta"
}
```

O `modo` pode ser `hierarquico` (níveis encaixados), `sequencia_plana` (unidades independentes de mesmo nível, numa sucessão sem enumeração global que as amarre) ou `topico` (sem padrão; delimitação só por mudança de assunto).

`tem_padrao: false` é a **válvula de escape que o Auditor não tem**: o schema do Auditor o obriga a devolver um padrão e um veredito, então com entrada subdeterminada ele confabula. O Identificador pode dizer que não há padrão, ou declarar `confianca: "baixa"`.

O `nivel_de_corte` é o parâmetro que, sem o Identificador, o laço tateia por tentativa e erro — cada tentativa custando uma re-segmentação completa do documento.

### Segmentador — janela deslizante

Recebe o texto, o padrão do Identificador e os insights do ciclo anterior. O documento é montado como um único string com marcadores de página; uma janela de N páginas vai ao LLM, que identifica **apenas o primeiro bloco completo** dela. O ponteiro de caracteres avança para além do fim desse bloco e a janela desliza.

A posição de cada bloco é devolvida como **âncoras de texto** (`offset_inicio` / `offset_fim`: os primeiros e últimos caracteres do trecho), não como índices. Reencontrá-las no documento é o passo determinístico do módulo, feito em camadas de tolerância crescente, porque o LLM promete copiar literalmente e na prática come marcação markdown, acentos e espaços.

Se o bloco termina na última página da janela, ele pode estar truncado: a janela dobra de tamanho e a chamada é repetida.

### Auditor — o que exige julgamento

Recebe um **resumo das bordas** dos blocos (páginas, tamanho, título, os primeiros e os últimos caracteres de cada um) **mais o padrão vindo do Identificador** — fonte independente, não a própria saída do laço. Nunca recebe o texto fonte: mandar os blocos inteiros a cada ciclo custaria o mesmo que reenviar o documento.

Ele não deduz a estrutura; **confere conformidade** contra a especificação do Identificador e procura cinco patologias:

1. **Granularidade desigual** — blocos de níveis hierárquicos diferentes convivendo no resultado
2. **Bloco englobante** — um bloco que agrupa várias unidades do padrão, onde cada uma deveria ser um bloco
3. **Sobreposição ou duplicação** — dois blocos cobrindo essencialmente o mesmo trecho
4. **Lacuna** — salto entre o fim de um bloco e o início do seguinte, indicando conteúdo não segmentado
5. **Falha de âncora** — bloco cujas âncoras não foram reencontradas no texto, por não serem literais ou não serem únicas

Se reprovar, escreve **diretrizes concretas** que voltam ao prompt do Segmentador, e o documento é re-segmentado do zero.

### O laço

Cada ciclo custa uma re-segmentação completa mais uma chamada de auditoria. Ele para quando o Auditor aprova, quando o Auditor reprova mas não produz diretriz nenhuma (sem diretriz não há o que reinjetar), ou quando um teto de ciclos é atingido.

**Não há detecção de estagnação:** uma segmentação que se repete ciclo após ciclo sem ser aprovada só é interrompida pelo teto, e o padrão é rodar sem teto. Cada ciclo é salvo em disco, porque **o último não é necessariamente o melhor** — houve caso de o ciclo final ter cobertura menor que um intermediário.

## Estado atual

O pipeline roda de ponta a ponta e produz segmentações utilizáveis, mas nenhuma atingiu cobertura total e **não há como medi-las**: não existem anotações de referência nem código de avaliação no repositório.

A consequência é que a aprovação do Auditor é um teste de **auto-consistência, não de correção**: ele compara os blocos com uma especificação estrutural, nunca com o documento, então uma segmentação uniformemente errada é um ponto fixo — é aprovada no primeiro ciclo. A validação externa contra gabarito é o próximo passo necessário, e junto com ela o experimento que ainda falta: verificar se a aprovação do Auditor de fato correlaciona com qualidade maior. Se não correlacionar, o critério de parada do laço é ruído.

Instruções de execução, flags e os modos de falha já observados estão em [CLAUDE.md](CLAUDE.md).
