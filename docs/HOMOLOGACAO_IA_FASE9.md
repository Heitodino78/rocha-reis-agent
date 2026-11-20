# Homologação real da IA — Google Gemini (NÃO AUTORIZADA)

**Estado:** procedimento **PREPARADO e TESTADO OFFLINE**; execução **BLOQUEADA**. A Fase
9B migrou o produto para o Google Gemini e não autoriza nenhuma chamada, gratuita ou paga.
A existência de `GEMINI_API_KEY` ou `GOOGLE_API_KEY` não é autorização.

> Numeração: a Fase 9A preparou uma "9B" de avaliação real com Anthropic. Por decisão do
> responsável, a Fase 9B passou a ser a **migração para Gemini** (executada offline). A
> avaliação real com Gemini descrita aqui fica pendente de autorização (sugestão: Fase 9C).

## 1. Condições obrigatórias (todas)

| # | Condição | Como é verificada |
|---|---|---|
| 1 | Autorização escrita específica para a avaliação, assinada por responsável **maior de 18 anos** e legalmente habilitado | Arquivo privado no formato de [`fixtures/homolog/ai_evaluation_authorization.example.json`](../fixtures/homolog/ai_evaluation_authorization.example.json) |
| 2 | Conta Google, **projeto Cloud com faturamento** (`billing: paid_cloud_project`; nível gratuito vedado), modelos, orçamento, nº de requisições (total e por modelo), cenários, período (≤ 7 dias), dados `synthetic_only`, armazenamento, interrupção, frase de confirmação | `RealRunAuthorization` (Pydantic, extras proibidos; tetos rígidos US$ 50 / 2.000 requisições) |
| 3 | Arquivo fora do repositório, 0600, sem link simbólico | `load_authorization` (padrões de nome no `.gitignore`) |
| 4 | Trava `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED = True` em commit próprio e revisado | `check_real_run`: é a **primeira** verificação |
| 5 | Modelo autorizado, presente no registro verificado, **estável** e com saída estruturada | `check_real_run` + `require_model` |
| 6 | `GEMINI_API_KEY` exportada só no processo, a partir de gerenciador de segredos; chave com restrição de API no console | `_real_factory` lê a chave **depois** da trava e da autorização; nunca a registra |
| 7 | Tarifas, IDs e níveis de raciocínio reconfirmados; `ai/models.json` atualizado | Revisão humana |
| 8 | Avaliação jurídica (DPA do Google, retenção, transferência internacional) | `IA_GENERATIVA.md` §5 |

Somente dados **sintéticos** de `evals/`: nada de conversas, telefones ou nomes reais do salão.

## 2. Execução progressiva (piloto com o modelo mais econômico)

```sh
PY=/tmp/rocha-reis-agent-venv/bin/python
AUT="$HOME/.config/rocha-reis/ai_evaluation_authorization.json"   # 0600, fora do repositório
# GEMINI_API_KEY carregada do gerenciador de segredos SOMENTE neste shell; nunca colar em chat ou arquivo versionado.

# Passo 1 — uma única requisição sintética (cenário A1)
$PY -m rocha_reis_agent.evaluation --provider gemini --model gemini-3.1-flash-lite --prompt v4 \
    --authorization "$AUT" --smoke --save
# Conferir: JSON válido, linha em ai_usage (modelos pedido/efetivo, tokens incl. raciocínio, custo),
# nenhum texto no log, latência, desligamento (Ctrl+C) e o painel de faturamento do projeto.

# Passo 2 — conjunto completo do piloto, dentro do teto do arquivo
$PY -m rocha_reis_agent.evaluation --provider gemini --model gemini-3.1-flash-lite --prompt v4 \
    --authorization "$AUT" --save

# Passo 3 — comparação com os outros modelos autorizados (um por vez) e com a linha de base offline
$PY -m rocha_reis_agent.evaluation compare evals/results/phase9b-fake-v4.json evals/results/runs/<gemini>.json
```

Interrupções automáticas:
- **Orçamento:** um `RunBudget` com `max_requests`, `max_cost_usd` e
  `per_model_max_requests` da autorização vale para todos os cenários. Antes de cada
  chamada reserva-se o **pior caso**: entrada estimada a 2 caracteres/token mais todo o
  `max_output_tokens`, × tentativas. Atingido o teto, a chamada é recusada **antes** de
  sair da máquina.
- **Segurança:** a primeira violação de segurança encerra a execução.
- **Consumo desconhecido** (timeout, rede, 5xx, modelo inesperado): o pior caso é
  contabilizado.

Manual: `Ctrl+C`; revogar a chave no AI Studio ou no Cloud Console; registrar o motivo.

## 3. Comparação justa

- Mesmo conjunto (`scenarios_sha256_16`), catálogo e critérios (o `compare` avisa se diferirem).
- Mesmo prompt (v4 é o candidato; v2/v3 são referências).
- Parâmetros por modelo **não** são forçados a ficar iguais: o 3.8 Flash não aceita
  raciocínio `minimal` e usa `low`. As diferenças ficam registradas no relatório.
- Variabilidade: até 3 repetições dos finalistas, dentro do orçamento.

## 4. Critérios (pré-registrados em `evals/criteria.json`, inalterados)

- **Segurança (S1–S5): tolerância zero** a violações observadas. Isso não prova segurança
  absoluta.
- **Qualidade (Q1–Q8):** intenção ≥ 0,90; recall humano ≥ **0,95**; precisão humana ≥ 0,90;
  dado ausente ≥ 0,90; Booksy ≥ 0,95; respostas úteis ≥ 0,85; fallback ≤ 0,10; contexto
  ≥ 0,90.
- **Operação (O1–O2):** p95 ≤ 8 s; custo estimado por conversa ≤ US$ 0,05.
- Prioridade de análise: os casos difíceis H9 (humano indireto), J6 (gíria de preço) e L3
  (reclamação indireta). X2 (saúde) já escala por regra de segurança.

## 5. Decisão

Escolher o modelo **mais barato que passar em todos os critérios**. Um modelo mais caro
só com ganho relevante e demonstrável. Considerar o reajuste do 3.8 Flash em 01/01/2027.
Se nenhum passar, **não ativar**. Ativar no atendimento é outra etapa e exige também a
Fase 6B (Meta) e a avaliação jurídica.

## 6. Orçamento técnico sugerido (a aprovar)

Cerca de 140 requisições por execução completa. As estimativas abaixo usam o cenário
pessimista (1.500 tokens de saída), em [`AVALIACAO_MODELOS_IA.md` §5](AVALIACAO_MODELOS_IA.md#5-custos-estimados-premissas-explícitas-não-medições).

| Etapa | Requisições | Estimativa pessimista |
|---|---|---|
| Smoke (3 modelos × 1) | 3 | < US$ 0,02 |
| 1 execução completa por modelo | ≈ 420 | 3.1 FL ≈ US$ 0,34 · 3.5 FL ≈ US$ 0,56 · 3.8 F ≈ US$ 0,87 |
| Até 3 repetições dos 2 finalistas | ≤ 840 | ≤ US$ 3 |

**Sugestão:** teto de US$ 3 e 900 requisições (300 por modelo). O orçamento pode ser
ampliado após o piloto. É uma estimativa; o faturado pode diferir. O limite de gasto do
Tier 1 (US$ 10 por 10 minutos) não é atingido nesse ritmo.
