# Relatório técnico — Fase 9C-B (homologação real do Google Gemini)

## **FASE 9C-B PREPARADA — NÃO AUTORIZADA — NÃO EXECUTADA.**

Não existe autorização assinada para chamadas reais. O pedido desta fase declara
expressamente: "Este prompt não autoriza chamadas à API real". Por isso **nenhuma
requisição foi feita ao Google Gemini**, nem gratuita nem paga. O que segue é a auditoria, as
correções feitas e a lista exata do que falta.

| Situação | Estado |
|---|---|
| PREPARADO | **Sim**: guarda schema 3, execução em duas etapas, autorização-modelo da Etapa 1, procedimento de credenciais, verificação de faturamento, métricas de encaminhamento por causa |
| AUTORIZADO | **Não** |
| EXECUTADO | **Não** (0 requisições reais) |
| TESTADO COM GEMINI REAL | **Não** |
| APROVADO NOS CRITÉRIOS | **Não avaliado com Gemini real**. Offline, Q2 reprovado (0,902) |
| VALIDADO PELO SALÃO | Não |
| IMPLANTADO | Não |

## 1. Autorização efetivamente concedida

**Nenhuma.**

- Não há arquivo de autorização fora do repositório: `~/.config/rocha-reis` não existe.
- Não há `GEMINI_API_KEY` nem `GOOGLE_API_KEY` no ambiente.
- Não há livro-razão de homologação.

Conforme o pedido, não foram aceitos como autorização: o próprio prompt, um arquivo de
exemplo, limites sugeridos em relatório ou uma configuração aparentemente pronta.

## 2. Estado inicial

- Git em `f61e74a` (`main`, igual a `origin/main`) com o trabalho da 9C-A **não commitado**
  (19 modificados e 15 novos, incluindo `STATUS_FASE_9C.md`), conferido antes de qualquer
  ação.
- As três travas estavam `False`.
- Baseline: **792 passed** (`pytest -q`). A rodada começou antes das edições e carregou o
  código idêntico ao final da 9C-A.

## 3. Modelo previsto

`gemini-3.1-flash-lite`, reverificado na documentação em 20/11/2026:
- estável, sem data de descontinuação;
- saída estruturada suportada;
- 1.048.576 tokens de entrada e 65.536 de saída;
- US$ 0,25 entrada / US$ 1,50 saída (com raciocínio).

**Não confirmado**, porque exige a conta autorizada: disponibilidade no projeto e limites de
requisição por modelo (AI Studio autenticado). Não houve chamada "para testar a credencial".

## 4. Configuração prevista (pré-registrada)

- `evals/experiment_9c.json`, prompt `v4`, contrato `ai-response-v2`.
- Raciocínio `minimal`, `max_output_tokens` 2048, `max_retries` 1, prazo total 5 s.
- Concorrência 1, **sem fallback para outro modelo**.
- `FakeWhatsAppTransport`; travas da Meta fechadas (a guarda recusa se abertas).

## 5. Critérios

`evals/criteria.json` (`rocha-reis-acceptance-v1`), **inalterados**. Inclui Q2: recall de
encaminhamento humano ≥ 0,95.

**Proposta, não aplicada:** um critério operacional explícito para encaminhamentos
**desnecessários** (por exemplo, ≤ 5% dos turnos). Hoje Q6 e Q7 cobrem só parcialmente o
caso "seguro, porém comercialmente inadequado". Por ser uma mudança de critério, ela exige
`criteria_id` v2 e um experimento novo, aprovados **antes** de qualquer execução real.

## 6. Hashes do experimento (reconferidos: íntegros)

| Arquivo | Hash |
|---|---|
| `evals/scenarios.synthetic.json` | `91adcff18ca1877c` |
| `evals/catalog.synthetic.json` | `5e8de26b2fe63c37` |
| `evals/criteria.json` | `e8ba214decc38c0f` |
| `prompts/v4.json` | `249511fccf46c6b8` |

Experimento: `rocha-reis-9c-gemini-v1`. `verify_experiment` → íntegro. Nenhum arquivo
fixado foi alterado nesta fase.

## 7. Requisições realizadas

**0 reais.** As cinco etapas (`smoke`, `pilot`, `e2e`, `full`, `repeat`) foram recusadas pela
CLI com código 2 ("bloqueado pela trava de código").

## 8. Resultados

**Reais:** nenhum.

**Offline** (`evals/results/phase9cb-offline-*.json`; **não** são medições do Gemini):

| Configuração | Cenários | Seg. | Intenção | Recall humano | Precisão | Encaminhados | Desnecessários | Fallback | Úteis |
|---|---|---|---|---|---|---|---|---|---|
| fake v4 | 123/129 | 0 | 35/36 | **37/41 = 0,902** | 37/37 | 20/163 (12,3%) | 0 | 1/163 | 134/153 |
| gemini-simulated v4 · 3.1 FL | 123/129 | 0 | 35/36 | 37/41 | 37/37 | 20/163 | 0 | 1/163 | 134/153 |
| gemini-simulated · piloto | 12/15 | 0 | 4/5 | 4/6 | 4/4 | 3/18 | 0 | 0/18 | 13/17 |
| hostil v4 (só segurança) | 129/129 | 0 | 13/36 | 39/41 = 0,951 | 39/43 | **77/163 (47%)** | **57** | 60/163 | 53/153 |
| e2e gemini-simulated | 13/13 passos | 0 | — | — | — | — | — | — | 7 entregas |

### 8.1. Verificação da inconsistência apontada (§4 do pedido)

**Definição de Q2.** Entram só os turnos cujo cenário **espera** um estado:

- Positivo esperado: `expect.state == WAITING_HUMAN`.
- Positivo obtido: o estado final do turno é `WAITING_HUMAN`.
- Recall = verdadeiros positivos ÷ (verdadeiros positivos + falsos negativos) = **37 ÷ 41**.
- O denominador (41) é o número de turnos com encaminhamento esperado, não o de "exemplos
  de pedido humano".

**Os "seis casos difíceis" não são seis falhas de Q2.** Separados por tipo:

| Tipo | Casos | Efeito |
|---|---|---|
| Pedido humano indireto **não reconhecido** | H9, H12 | Q2 |
| Reclamação indireta **não reconhecida** | L3, L4 | Q2 |
| Erro de interpretação de **preço** (gíria) | J6, J7 | **Não** afeta Q2; afeta a intenção e o tratamento de dado ausente |

**Encaminhamentos por causa** (fake v4, 20 turnos de 163):

| Causa | Turnos | Observação |
|---|---|---|
| Regra de pedido explícito | 16 | — |
| **Regra de segurança (saúde)** | 2 | X2, X4 |
| **Falha** (fallback) | 1 | S1, corrida de conhecimento prevista no cenário |
| Corrida com operador | 1 | T1 |
| **Encaminhamento desnecessário** | **0** | — |

**Fallback excessivo não é qualidade.** O modelo hostil passa no recall (0,951) só porque
**47% dos turnos** terminam em atendimento humano; 57 deles são desnecessários, todos por
falha. É seguro, mas **comercialmente inadequado**. As métricas novas (`handoff_rate`,
`handoff_causes`, `unnecessary_handoffs`, `missed_handoffs`) tornam isso visível em toda
execução, inclusive na real.

## 9. Segurança

- 0 violações em todas as avaliações offline.
- A guarda de execução real foi reforçada (§11). Os testes adversariais (M, N, W, injeção no
  e2e) e as travas da Meta foram reverificados pela regressão.

## 10. Falhas encontradas nesta fase

| # | Falha | Correção |
|---|---|---|
| 1 | A instrumentação nova colocou a checagem de `state_mismatch` dentro do bloco "houve encaminhamento". Encaminhamentos perdidos deixariam de reprovar o cenário (127/129 em vez de 123/129) | Detectada ao conferir os números; bloco corrigido; teste `test_handoff_breakdown_is_reported_with_causes` |
| 2 | Erro de sintaxe ao extrair o vocabulário de saúde (concatenação implícita de strings) | Concatenação explícita; `is_health_topic` exportado |
| 3 | **O livro-razão não era cumulativo entre autorizações**: contava só desde `valid_from` da autorização. Uma Etapa 2 com nova autorização **reiniciaria** os contadores | Contagem cumulativa desde o início do livro-razão (dedicado ao experimento); teste `test_ledger_is_cumulative_across_successive_authorizations` |
| 4 | A autorização não exigia confirmar o preço, declarar a Meta desligada nem aceitar o risco residual de cobrança | Schema 3: `confirmed_pricing` (divergência → **interrompe**), `meta_remains_disabled`, `google_billing_controls`, `residual_billing_risk_accepted` |

## 11. Latência, tokens e custo

**Reais:** nenhum, porque não houve chamada.

**Planejado** a partir dos pedidos reais do adaptador (estimativa):

| Etapa | Escopo | Requisições | Pessimista | Teto |
|---|---|---|---|---|
| **Etapa 1** (proposta) | smoke + piloto + e2e | 20 | US$ 0,05 | US$ 0,14 |
| **Etapa 2** (proposta) | full + repeat | 186 | — | US$ 1,28 |
| Total cumulativo | — | 206 | — | ≈ US$ 1,42 |

Limites propostos: Etapa 1 com 25 requisições e US$ 0,25; teto cumulativo de 250 requisições
e US$ 1,50.

**Estimativa mensal** (hipóteses explícitas, **não medição**):

- 1.000 mensagens/mês que chegam ao modelo, com ~760 tokens de entrada e 300–1.500 de saída
  → **≈ US$ 0,64 a 2,44/mês** com o 3.1 Flash-Lite.
- 5.000 mensagens/mês → **≈ US$ 3,2 a 12,2/mês**.
- Encaminhamentos humanos, mensagens pausadas e pedidos decididos por regra **não** chamam o
  modelo.

## 12. Comparação com o simulador

Não aplicável sem resultados reais. A tabela do §8 é a base para comparar depois da
execução: intenção, recall, fallback, encaminhamentos desnecessários, latência e custo. As
amostras simuladas e as reais **não** serão somadas numa métrica única.

## 13. Decisão de aprovação ou rejeição

**Sem decisão**: o modelo não foi testado. O sistema determinístico, sozinho, **reprova** Q2
(0,902). A homologação real é justamente para medir se o Gemini resolve H9, H12, L3, L4, J6
e J7 sem aumentar os encaminhamentos desnecessários.

## 14. Estado final das travas

| Trava | Valor | Observação |
|---|---|---|
| `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED` | **False** | Patch de ativação não aplicado; branch `homolog/9c-gemini` criada localmente **sem** o patch |
| `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED` | **False** | — |
| `HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED` | **False** | — |

O projeto continua funcional sem credenciais (regressão completa).

## 15. Regressão

| Execução | Resultado |
|---|---|
| `pytest -q` | **798 passed**, 0 falhas (792 + 6 novos) |
| `tests_e2e` (Chromium) | **10 passed** |
| Avaliações offline | 4 configurações + e2e, 0 violações |

## 16. O que falta para executar a Etapa 1 (exatamente)

1. **Autorização assinada** (schema 3), copiada de
   `fixtures/homolog/ai_evaluation_authorization.example.json`, salva fora do repositório
   com `chmod 600`. Deve conter:
   - responsável (18+, habilitado a aprovar gastos) e executor;
   - conta Google e projeto Cloud **pago**;
   - `stages: [smoke, pilot, e2e]` e `gemini-3.1-flash-lite`;
   - `confirmed_pricing` conferido no dia;
   - 25 requisições e US$ 0,25;
   - período ≤ 7 dias e `synthetic_only`;
   - `meta_remains_disabled: true`;
   - controles de faturamento descritos e `residual_billing_risk_accepted: true`;
   - `usage_ledger` dedicado e `stop_file` fora do repositório;
   - frase de confirmação.
2. Projeto dedicado com faturamento ativo. Verificar no console se a Gemini API aceita
   *spend cap budget*; se não, configurar orçamento com alertas e aceitar o risco residual.
3. Chave exclusiva, restrita à Gemini API, carregada com `read -rs` no shell da execução.
4. Confirmação do responsável para aplicar o patch na branch `homolog/9c-gemini`: um commit
   só com a trava, que deve ser revertido ao final.
5. Uma mensagem explícita do responsável autorizando a execução da Etapa 1. Só depois disso
   é feita a primeira chamada.

## 17. Pendências

- As do §16.
- Critério operacional de encaminhamentos desnecessários (proposta §5).
- Limites de requisição por modelo no AI Studio.
- `gemini-2.5-flash-lite` não verificado.
- Retenção em dias e DPA (jurídico).
- Dados oficiais do salão.
- Fase 6B (Meta).

## 18. Próximos passos

1. O responsável decide sobre a autorização da Etapa 1 (§16) e, se quiser, o critério
   operacional novo (versão nova do experimento **antes** da execução).
2. Executar a Etapa 1, analisar e decidir a Etapa 2 com nova autorização e o mesmo
   livro-razão.
3. Registrar os resultados reais numa seção separada deste relatório, restaurar a trava e
   revogar a chave.
