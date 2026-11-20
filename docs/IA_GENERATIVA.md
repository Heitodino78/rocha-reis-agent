# IA generativa — arquitetura, provedor, privacidade e ativação

> **GOOGLE GEMINI É O PROVEDOR OFICIAL ESCOLHIDO PARA O ROCHA REIS AGENT** (decisão do
> responsável, 20/11/2026, Fase 9B). A integração Anthropic das Fases 8 e 9A é uma decisão
> **histórica substituída**: o adaptador e o SDK foram removidos e os relatórios daquelas
> fases continuam como evidência do que foi feito na época (§9). Claude Code é só a
> ferramenta de desenvolvimento; não é o modelo que atende clientes.

**Estado:** adaptador Gemini **IMPLEMENTADO e BLOQUEADO** (trava de código própria).
Nenhuma chamada à API Gemini foi feita, nem gratuita nem paga. Qualidade de modelo real:
**não validada**.

Relatórios: [`STATUS_FASE_8.md`](STATUS_FASE_8.md) e [`STATUS_FASE_9.md`](STATUS_FASE_9.md).
Modelos: [`AVALIACAO_MODELOS_IA.md`](AVALIACAO_MODELOS_IA.md). Homologação real (não
autorizada): [`HOMOLOGACAO_IA_FASE9.md`](HOMOLOGACAO_IA_FASE9.md).

## 1. Fluxo

```text
Mensagem (não confiável)
 → normalização, sessão, deduplicação (núcleo, Fases 1–4)
 → barreira de estado: WAITING_HUMAN / HUMAN_ACTIVE / RESOLVED → nenhuma chamada à IA
 → detector determinístico (controle): pedido humano e temas de saúde decididos SEM modelo
 → catálogo aprovado (arquivo ou banco) → projeção autorizada (só revisões APPROVED)
 → recuperação determinística de fatos relevantes + contexto limitado do MESMO cliente
 → AIRequest (instruções confiáveis | fatos "só conteúdo" | histórico e mensagem NÃO confiáveis)
 → AIProvider (fake | Gemini BLOQUEADO)
     Gemini: system_instruction = instruções; contents = dados rotulados;
     response_json_schema = contrato v2; orçamento reservado ANTES da chamada
 → bloqueio de segurança / truncamento / modelo efetivo inesperado → falha segura
 → validação estrutural (Pydantic, campos extras proibidos)
 → validação de escopo: referências ⊆ fatos recuperados; serviço/contexto/intenção herdada = controle do backend
 → resposta montada pelo BACKEND com templates e campos aprovados
 → abertura do modelo sanitizada; qualquer sinal factual → descartada
 → nova checagem: conhecimento mudou durante a consulta? → falha segura
 → commit otimista (versão da conversa): operador assumiu durante a geração? → conflito,
   reprocessa, estado humano → nenhuma resposta
 → outbox → despacho reconfere estado/sessão/janela/responsável antes de enviar
```

Qualquer falha leva à mesma saída segura: resposta fixa sem fatos, contexto preservado e a
conversa em `WAITING_HUMAN`. São falhas: timeout, rede, 429, chave inválida, bloqueio de
segurança, JSON inválido, referência inventada, limite de consumo e modelo inesperado.

## 2. Provedor: Google Gemini (Gemini Developer API)

| Item | Implementação |
|---|---|
| SDK | **`google-genai` 3.6.1** (oficial), fixo no `requirements.in` e travado com hash. Usa `httpx2`, o mesmo cliente HTTP já travado no projeto |
| Chamada | `client.models.generate_content(model, contents, config=GenerateContentConfig(...))`. A documentação nova recomenda a "Interactions API"; o `generateContent` documentado no README do SDK foi mantido por ter binding estável e saída estruturada por JSON Schema. Reavaliar em revisão futura |
| Cliente | Só por `create_client(api_key, http_client)`: chave **explícita**, `vertexai=False`, `enterprise=False`. `genai.Client()` sem argumentos leria `GEMINI_API_KEY`/`GOOGLE_API_KEY` do ambiente, por isso nunca é usado |
| Saída estruturada | `response_mime_type="application/json"` + `response_json_schema=RESPONSE_SCHEMA_V2`. Recursos usados (object, array, enum, anyOf, null, required, additionalProperties) constam da documentação |
| Raciocínio | `ThinkingConfig(thinking_level=…)`. Padrão do registro: `minimal` nos Flash-Lite e `medium` no 3.8 Flash (que **não** aceita `minimal`). Não existe "desligar" documentado |
| Prazo | `HttpOptions(timeout=ms, retry_options=HttpRetryOptions(attempts=N))` **por requisição**; `AI_TIMEOUT_SECONDS` é dividido entre as tentativas (o SDK também repete timeouts) |
| Erros | `ClientError` (429 → `rate_limited`; 401 ou 400 "API key not valid" → `auth_error`; 403; 404 → `model_not_found`; outros 4xx); `ServerError`/`APIError` → `provider_unavailable`; `httpx2.TimeoutException` → `timeout`; `httpx2.TransportError` → `network_error` |
| Bloqueios | `prompt_feedback.block_reason` → `blocked_prompt`; `finish_reason` SAFETY/PROHIBITED_CONTENT/BLOCKLIST/SPII/RECITATION… → `blocked_output`; MAX_TOKENS → `truncated`; outro diferente de STOP → `unexpected_finish`. Sempre verificados **antes** de ler o conteúdo |
| Modelo efetivo | `model_version` igual ao pedido (ou com sufixo de versão `-NNN`). Diferente → `unexpected_model`, falha segura, custo de pior caso |
| Consumo | `usage_metadata`: `prompt_token_count` (inclui cache), `cached_content_token_count`, `candidates_token_count`, `thoughts_token_count` (cobrado como saída) |

## 3. Modelos e custos (estimativa, não medição)

Registro verificado: [`ai/models.json`](../rocha_reis_agent/ai/models.json) (`verified_at`
2026-11-20, nível pago Standard). Detalhes, fontes e não verificados em
[`AVALIACAO_MODELOS_IA.md`](AVALIACAO_MODELOS_IA.md).

| Modelo (estável) | Entrada US$/MTok | Saída (com raciocínio) | Cache (leitura) | Raciocínio |
|---|---|---|---|---|
| `gemini-3.1-flash-lite` | 0,25 | 1,50 | 0,025 | minimal (padrão)–high |
| `gemini-3.5-flash-lite` | 0,30 | 2,50 | 0,03 | minimal (padrão)–high |
| `gemini-3.8-flash` | 0,75 → **1,50 em 01/01/2027** | 3,75 → **7,50** | 0,075 → 0,15 | low–high (padrão medium) |

**Não há modelo padrão fixo** (`AI_MODEL` é obrigatório com o provedor real). A avaliação
offline usa `gemini-3.1-flash-lite` só como ponto de partida mais econômico.

## 4. Contratos (inalterados desde a Fase 8)

- `AIProvider.generate(request, timeout_seconds)` → `AIResponse | AIResponseV2 | dict | str`
  (o backend revalida tudo; tipo Python não é confiança).
- `AIRequest`: mensagem atual e histórico como `UntrustedText`; fatos como
  `AuthorizedFact` (`authority="content-only"`); `prompt_version`, objetivo,
  instruções, contrato de saída. Sem IDs de cliente/conversa/evento. Limite 64 KB.
- `AIResponseV2` (`extra="forbid"`): `intents`, `service_ids`, `use_context`,
  `catalog_requested`, `faq_reference`, `fact_references`, `needs_clarification`,
  `human_handoff_suggested`, `information_missing`, `preface` (≤240).
  Não há campo de texto factual livre nem de ação administrativa.
- O JSON Schema enviado (`RESPONSE_SCHEMA_V2`) é só uma restrição de formato. Esquema
  válido **não** significa conteúdo verdadeiro nem autoriza ação.

## 5. Privacidade e dados (LGPD) — avaliação antes de qualquer ativação

Termos consultados: Gemini API Additional Terms
(<https://aistudio.google.com/docs/terms>, "Effective March 23, 2026") e a página de
preços, em 20/11/2026. **Não se declara conformidade com a LGPD.**

| Pergunta | Situação | Decisão pendente de |
|---|---|---|
| O que é enviado? | Instruções do prompt; fatos aprovados (não pessoais); IDs de serviço do contexto; mensagem atual e até 4 mensagens anteriores **do mesmo cliente** (≤500 caracteres cada), com telefone, e-mail, CPF/CNPJ e dígitos longos **removidos** (`ai/privacy.py`) | — |
| O que nunca é enviado | Telefone/BSUID, ID de conversa, nome de perfil, mensagens de funcionários, dados de outros clientes, metadados de revisão | — |
| Limite da minimização | Nomes próprios e dados livres digitados pelo cliente **não** são removidos | Salão + jurídico |
| **Nível gratuito** | "Google uses the content you submit … to provide, improve, and develop Google products"; revisores humanos podem ler entradas e saídas; "Do not submit sensitive, confidential, or personal information". **Inadequado para conversas de clientes** | — (vedado pelo projeto) |
| **Nível pago** | Prompts e respostas **não** usados para melhorar produtos; tratados sob o *Data Processing Addendum* (Google como operador). Pago = projeto Cloud com faturamento ativo | Agência + salão (contratação) |
| Retenção (pago) | Registros "for a limited period of time" só para detectar abuso. **O número de dias não aparece nos termos lidos**; confirmar no DPA. ZDR não mencionado | Jurídico |
| Transferência internacional | "may be stored transiently or cached in any country in which Google … maintain[s] facilities". Exige base do art. 33 da LGPD | Jurídico |
| Elegibilidade | 18 anos ou mais; uso profissional (não consumidor); região disponível (Brasil consta); apps voltados a menores de 18 anos vedados; **vedado uso para conselho médico** (por isso temas de saúde vão para pessoa) | Responsável legal |
| Logs locais | Só metadados (`ai_usage`: modelos pedido/efetivo, prompt, resultado, tokens incl. raciocínio, latência, custo estimado). Nenhum texto. Testado | — |
| Dados reais | **Proibidos** nesta fase; avaliações só com cenários sintéticos | Autorização + base legal |

## 6. Limites de consumo implementados (`ai/budget.py`, neutro em relação ao provedor)

| Limite | Configuração | Efeito |
|---|---|---|
| Tamanho da mensagem | `SIM_MAX_MESSAGE_CHARS` (≤2000) | Recusada antes de qualquer processamento |
| Histórico enviado | `AI_HISTORY_LIMIT` (≤10, padrão 4) × 500 caracteres | — |
| Entrada | `AI_MAX_INPUT_CHARS` (6000) e `AI_MAX_INPUT_TOKENS` (4000 **estimados**, caracteres ÷ 2) | Recusa **antes** da chamada |
| Saída | `AI_MAX_OUTPUT_TOKENS` (2048, inclui raciocínio; ≤ limite do modelo); MAX_TOKENS → falha segura | — |
| Concorrência | `AI_MAX_CONCURRENCY` | Excesso → falha segura sem custo |
| Tentativas | `AI_MAX_RETRIES` (0–3, `HttpRetryOptions`); núcleo não repete | Prazo total limitado |
| Por período | `AI_MAX_REQUESTS_PER_DAY`, `AI_MAX_COST_USD_PER_DAY` (janela de 24 h persistida) | Bloqueio antes da chamada |
| Por execução | `AI_MAX_REQUESTS_PER_RUN`, `AI_MAX_COST_USD_PER_RUN` (`RunBudget`) | Bloqueio antes da chamada |
| Por modelo | `RunBudget.per_model_max_requests` (autorização da avaliação real) | Bloqueio antes da chamada |
| Reserva de pior caso | entrada estimada × tarifa + todo `max_output_tokens` × tarifa de saída, × tentativas | Só chama se couber |
| Consumo incerto | timeout, rede, 5xx, erro inesperado, modelo inesperado, consumo ausente | Registra o pior caso (nunca zero) |
| Sem tarifa | Modelo fora do registro | Bloqueia |

## 7. Condições para ativar o Gemini real (todas obrigatórias)

1. Autorização escrita do responsável (salão + agência), maior de 18 anos, para usar a
   Gemini API com conta Google e projeto Cloud **pagos** identificados, finalidade,
   orçamento e limites.
2. Avaliação jurídica de privacidade (§5): DPA, retenção, transferência internacional e
   aviso ao cliente.
3. Chave criada pelo responsável da conta, guardada em gerenciador de segredos (nunca no
   repositório, em `.env` versionado ou em chat), exclusiva deste uso e com restrição de
   API no console.
4. Modelos, tarifas e níveis de raciocínio reconfirmados na documentação do dia; atualizar
   `ai/models.json` (`verified_at`).
5. Alteração de código revisada: `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED = True` em
   commit próprio. A variável de ambiente sozinha nunca basta.
6. Primeiro uso **apenas** na avaliação sintética (`--provider gemini`), com o roteiro de
   [`HOMOLOGACAO_IA_FASE9.md`](HOMOLOGACAO_IA_FASE9.md) e os critérios pré-registrados
   (`evals/criteria.json`).
7. A trava do Gemini **não** abre a Meta e vice-versa (testado nos dois sentidos).

## 8. Riscos conhecidos

- O backend restringe o modelo a classificar. Serviço, contexto, agendamento e intenção
  herdada são fixados pelo detector: mais seguro, menos flexível.
- Pedidos humanos indiretos, gírias de preço e reclamações indiretas (H9, J6, L3) dependem do
  modelo. Com o fake, o recall de encaminhamento humano é 0,947 (< 0,95 do critério Q2).
- A sanitização da abertura é feita por lista de termos.
- Temas de saúde escalam por vocabulário fechado: pode haver falsos positivos (aceitável)
  e omissões de termos não listados.
- Latência, consumo de raciocínio, taxa de bloqueio e custo reais são desconhecidos. O
  reajuste do 3.8 Flash em 01/01/2027 dobra a tarifa.
- A retenção pelo provedor não está quantificada nos termos lidos.
- Rate limits por modelo só aparecem no AI Studio autenticado (não lidos). Há limite de
  gasto de US$ 10 por 10 minutos no Tier 1.
- `generateContent` é chamado de "legado, ainda suportado" em parte da documentação nova:
  pode exigir migração futura para a Interactions API.

## 9. Histórico substituído — Anthropic (Fases 8 e 9A)

Entre 19 e 20/11/2026 o projeto implementou e testou **offline** um adaptador Anthropic
(SDK `anthropic` 2.11, modelos Claude Opus/Sonnet/Haiku 5.5, `pricing.json` e depois
`models.json`). **Nenhuma chamada real foi feita.** Na Fase 9B, por decisão do
responsável, esse adaptador, o SDK, a trava `AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED` e o
registro de modelos Claude foram removidos. Os relatórios das Fases 8 e 9A e os arquivos
`evals/results/baseline-*.json` e `evals/results/phase9-*.json` permanecem como evidência
histórica. Os resultados `anthropic-simulated` neles **não** dizem nada sobre o Gemini.
