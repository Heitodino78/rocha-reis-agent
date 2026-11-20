# IA generativa — arquitetura, provedor, privacidade e ativação (Fase 8)

**Estado:** adaptador real **IMPLEMENTADO e BLOQUEADO**. Nenhuma chamada a API externa
foi feita. Qualidade de modelo real: **não validada**. Este documento é a referência
técnica; o relatório da execução está em [`STATUS_FASE_8.md`](STATUS_FASE_8.md).

## 1. Fluxo

```text
Mensagem (não confiável)
 → normalização, sessão, deduplicação (núcleo, Fases 1–4)
 → barreira de estado: WAITING_HUMAN / HUMAN_ACTIVE / RESOLVED → nenhuma chamada à IA
 → detector determinístico (controle): pedido humano decide SEM modelo
 → catálogo aprovado (arquivo ou banco) → projeção autorizada (só revisões APPROVED)
 → recuperação determinística de fatos relevantes + contexto limitado do MESMO cliente
 → AIRequest (instruções confiáveis | fatos "só conteúdo" | histórico e mensagem NÃO confiáveis)
 → AIProvider (fake | anthropic BLOQUEADO)
 → validação estrutural (Pydantic, campos extras proibidos)
 → validação de escopo: referências ⊆ fatos recuperados; serviço/contexto = controle do backend
 → resposta montada pelo BACKEND com templates e campos aprovados
 → abertura do modelo (só v2) sanitizada; qualquer sinal factual → descartada
 → nova checagem: conhecimento mudou durante a consulta? → falha segura
 → commit otimista (versão da conversa): operador assumiu durante a geração? → conflito,
   reprocessa, estado humano → nenhuma resposta
 → outbox → despacho reconfere estado/sessão/janela/responsável antes de enviar
```

Qualquer falha (timeout, rede, recusa, JSON inválido, referência inventada, limite de
consumo) leva à mesma saída segura: resposta fixa sem fatos, contexto preservado e a
conversa em `WAITING_HUMAN`. Nada de retry infinito: no máximo `AI_MAX_RETRIES` (≤3) no SDK.

## 2. Escolha do provedor inicial: Anthropic (Claude)

| Critério | Anthropic (escolhido) | OpenAI (não implementado) |
|---|---|---|
| API | Messages API, SDK Python oficial `anthropic` 2.x (já no lock com hash) | API própria e SDK Python oficial |
| Saída estruturada | `output_config.format` com JSON Schema | Existe saída estruturada por schema |
| Integração Python | SDK sobre `httpx2` — o mesmo cliente HTTP já travado no projeto; testável com `httpx2.MockTransport` | Exigiria segundo SDK e outra pilha HTTP |
| Falhas | Exceções tipadas (timeout, conexão, 429, 401, 403, 400, 5xx); `stop_reason` `refusal`/`max_tokens` | Equivalentes existem |
| Recusa | `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) em Opus 5.5/Sonnet 5.5; Haiku 5.5 sem fallback no servidor | — |
| Versionamento | ID de modelo explícito (`claude-opus-5-5`), sem sufixo de data | IDs próprios |
| Custos publicados | Tabela oficial abaixo (verificada) | **Não verificados nesta execução** |
| Privacidade | ZDR só por acordo; ver §5 | Não avaliado nesta execução |

**Justificativa:** o SDK já estava no lock e usa o mesmo `httpx2` do adaptador Graph,
o que permite testar o caminho completo sem rede; saída por JSON Schema reduz parsing
frágil; o contrato `AIProvider` continua neutro — trocar de provedor é escrever outro
adaptador que devolva `AIResponseV2`, sem tocar no núcleo. A OpenAI não foi descartada
por qualidade: apenas não há benefício concreto em manter dois SDKs nesta fase, e seus
preços/políticas **não foram conferidos** aqui. Reavaliar antes da ativação se o
salão/agência preferir outro fornecedor.

Fonte consultada: referência oficial da API Claude (cache de 2026-11-03), em 20/11/2026.
Reconfirmar em <https://platform.claude.com/docs/en/about-claude/pricing> antes de ativar.

## 3. Modelos candidatos e custos (estimativa, não medição)

| Modelo | ID | Entrada US$/MTok | Saída US$/MTok | Observações relevantes |
|---|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5` | 4,00 | 20,00 | Padrão da configuração. Thinking sempre ligado; `effort` padrão `medium`; sem `temperature`; fallback de recusa disponível |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2,00 | 10,00 | Effort padrão `high` (recalibrado); fallback `default` |
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0,10 | 0,50 | Preço até 100K tokens de entrada; **sem fallback de recusa no servidor** (configuração exige `AI_REFUSAL_FALLBACK=off`) |

O papel do modelo aqui é **classificar** e propor no máximo uma frase de cortesia —
tarefa curta. Estimativa ilustrativa por mensagem (≈1.500 tokens de entrada, ≈600 de
saída incluindo raciocínio; **suposição, não medição**):

| Modelo | Custo/mensagem | 1.000 mensagens |
|---|---|---|
| Opus 5.5 | ≈ US$ 0,018 | ≈ US$ 18 |
| Sonnet 5.5 | ≈ US$ 0,009 | ≈ US$ 9 |
| Haiku 5.5 | ≈ US$ 0,00045 | ≈ US$ 0,45 |

O cache do prompt de sistema (`cache_control`, já enviado) reduz a parte fixa da entrada.
**Exige medição real:** tokens efetivos (inclusive raciocínio), latência p50/p95 no
Brasil, taxa de recusa, acurácia de intenção em mensagens reais anonimizadas ou
sintéticas realistas, e custo por atendimento concluído. O módulo de avaliação já
registra tokens, latência, custo estimado e métricas por provedor e versão de prompt.

**Recomendação:** medir os três modelos no conjunto `evals/` (Opus 5.5 em `low` e
`medium`, Sonnet 5.5 em `low`, Haiku 5.5) assim que houver autorização e orçamento;
a escolha final é do responsável. Não trocar o modelo padrão por custo sem essa medição.

## 4. Contratos

- `AIProvider.generate(request, timeout_seconds)` → `AIResponse | AIResponseV2 | dict | str`
  (o backend revalida tudo; tipo Python não é confiança).
- `AIRequest`: mensagem atual e histórico como `UntrustedText`; fatos como
  `AuthorizedFact` (`authority="content-only"`); `prompt_version`, objetivo,
  instruções, contrato de saída. Sem IDs de cliente/conversa/evento. Limite 64 KB.
- `AIResponseV2` (`extra="forbid"`): `intents`, `service_ids`, `use_context`,
  `catalog_requested`, `faq_reference`, `fact_references`, `needs_clarification`,
  `human_handoff_suggested`, `information_missing`, `preface` (≤240).
  Não há campo de texto factual livre nem de ação administrativa.
- O JSON Schema enviado ao provedor (`RESPONSE_SCHEMA_V2`) é só uma restrição de
  formato; esquema válido **não** significa conteúdo verdadeiro.

## 5. Privacidade e dados (LGPD) — avaliação antes de qualquer ativação

| Pergunta | Situação atual do código | Decisão pendente de |
|---|---|---|
| O que é enviado? | Instruções do prompt; fatos aprovados do catálogo (não pessoais); IDs de serviço do contexto; mensagem atual e até 4 mensagens anteriores **do mesmo cliente** relacionadas ao serviço (≤500 caracteres cada), com telefone, e-mail, CPF/CNPJ e sequências longas de dígitos **removidos** (`ai/privacy.py`) | — |
| O que nunca é enviado | Telefone/BSUID, ID de conversa, nome de perfil do WhatsApp, mensagens de funcionários, dados de outros clientes, metadados de revisão | — |
| Limite da minimização | Nomes próprios e dados livres digitados pelo cliente (ex.: "sou a Maria, estou grávida") **não** são removidos | Salão + jurídico |
| Finalidade | Classificar a intenção da mensagem para responder com dados aprovados | Controlador (salão) |
| Retenção no provedor | **Não assumida.** ZDR só por acordo com a Anthropic e por elegibilidade de recursos (ex.: message threads não são elegíveis; não usados aqui). Confirmar retenção padrão e uso para treinamento nos termos comerciais vigentes | Agência + salão |
| Transferência internacional | Processamento fora do Brasil é provável; exige base do art. 33 da LGPD e cláusulas contratuais | Jurídico |
| Logs locais | Só metadados (`ai_usage`: modelo, versão do prompt, resultado, tokens, latência, custo estimado). Nenhum texto, prompt ou resposta. Testado | — |
| Exclusão | Dados locais seguem a retenção/anonimização das Fases 3–5; exclusão no provedor depende do contrato | Agência |
| Dados reais | **Proibidos** nesta fase; avaliações usam só cenários sintéticos | Autorização + base legal |

## 6. Limites de consumo implementados

| Limite | Configuração | Efeito |
|---|---|---|
| Tamanho da mensagem | `SIM_MAX_MESSAGE_CHARS` (≤2000) | Recusada antes de qualquer processamento |
| Histórico enviado | `AI_HISTORY_LIMIT` (≤10, padrão 4) × 500 caracteres; só mensagens do cliente relacionadas | — |
| Entrada ao provedor | `AI_MAX_INPUT_CHARS` (padrão 6000) | Recusa **antes** da chamada |
| Saída | `AI_MAX_OUTPUT_TOKENS` (padrão 4096, inclui raciocínio); `max_tokens` → falha segura | — |
| Concorrência | `AI_MAX_CONCURRENCY` (semáforo por processo) | Excesso → falha segura |
| Tentativas | `AI_MAX_RETRIES` (0–3, no SDK); núcleo não repete | Sem retry infinito |
| Requisições/dia | `AI_MAX_REQUESTS_PER_DAY` (janela móvel de 24 h, persistida) | Bloqueio antes da chamada |
| Custo/dia | `AI_MAX_COST_USD_PER_DAY` sobre **estimativa** (`ai/pricing.json`) | Modelo sem tarifa → bloqueia |

O consumo é gravado em transação independente: mesmo uma resposta descartada pela
validação conta para o orçamento.

## 7. Condições para ativar a IA real (todas obrigatórias)

1. Autorização escrita do responsável (salão + agência) para usar um provedor externo,
   com finalidade, orçamento mensal e limites aprovados.
2. Avaliação jurídica de privacidade (§5): retenção, treinamento, transferência
   internacional, contrato de tratamento, texto de transparência ao cliente.
3. Chave de API criada pelo responsável da conta, guardada em gerenciador de segredos
   (nunca no repositório, `.env` versionado ou chat), exclusiva deste uso.
4. Reconfirmar modelo, tarifas e parâmetros na documentação oficial do dia; atualizar
   `ai/pricing.json` com data de verificação.
5. Alteração de código revisada: `AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED = True` em
   `config.py` (a variável de ambiente sozinha nunca basta), em commit próprio.
6. `AI_PROVIDER=anthropic`, `AI_PROMPT_VERSION=v2` explícito, SQLite, limites conservadores.
7. Primeiro uso **apenas** com `python -m rocha_reis_agent.evaluation` adaptado para o
   provedor real sobre os cenários sintéticos, com orçamento de teste aprovado; critérios
   de aceite: zero violações críticas, `unsupported_claim_rate = 0`, acurácia de intenção
   ≥ fake, latência p95 aceitável para o salão.
8. A ativação da IA **não** ativa a Meta e vice-versa (travas independentes, testado).

## 8. Riscos conhecidos

- O backend restringe o modelo a classificar; ganhos de compreensão (gírias, erros de
  digitação) só aparecem onde o controle determinístico não diverge da proposta. Hoje
  serviço, contexto e agendamento são fixados pelo detector de regras — mais seguro,
  menos flexível. Relaxar isso exige nova avaliação.
- A sanitização da abertura é por lista de termos: pode descartar cortesias inocentes
  (falso positivo, aceitável) e, em tese, deixar passar uma frase enganosa sem termo
  listado; o oráculo da avaliação cobre parte disso, não tudo.
- Custos e latência reais desconhecidos; tarifas podem mudar.
- Retenção pelo provedor depende de contrato não firmado.
