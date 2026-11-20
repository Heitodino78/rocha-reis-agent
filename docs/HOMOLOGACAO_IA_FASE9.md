# Homologação real da IA — Google Gemini (Fase 9C-B: PREPARADA, NÃO AUTORIZADA, NÃO EXECUTADA)

**Estado:** procedimento **PREPARADO e TESTADO OFFLINE** (Fase 9C-A); execução **BLOQUEADA**.
Nenhuma chamada à API Gemini, gratuita ou paga, foi feita. **Orçamento autorizado hoje:
US$ 0,00.** A existência de `GEMINI_API_KEY`, saldo, conta ou arquivo de configuração **não**
é autorização.

> Numeração: a Fase 9B migrou o produto para o Gemini. A homologação real é a **Fase 9C**:
> **9C-A** (preparação offline, concluída) e **9C-B** (chamadas reais, exige autorização).

## 1. Condições obrigatórias (todas; verificadas no código, na ordem)

| # | Condição | Verificação |
|---|---|---|
| 1 | Trava `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED = True`, aplicada só pelo patch revisado [`docs/homolog/ativacao_gemini_9c.patch`](homolog/ativacao_gemini_9c.patch) em branch própria | `check_real_run` (primeira verificação) |
| 2 | **Meta desligada**: `GRAPH_TRANSPORT_…` e `HOMOLOGATION_TRANSPORT_…` = False | `check_real_run` recusa se qualquer trava Meta estiver aberta |
| 3 | Autorização escrita, **schema 3**, 0600, fora do repositório, assinada por responsável **maior de 18 anos** legalmente habilitado: conta Google, projeto Cloud **pago**, modelos, limites (total e por modelo), cenários, **etapas**, período ≤ 7 dias, `synthetic_only`, livro-razão, arquivo de parada, **preços confirmados** (iguais ao registro na data da execução), **declaração de Meta desligada**, controles de faturamento configurados, **aceitação do risco residual de cobrança**, frase de confirmação | `RealRunAuthorization` (extras proibidos; tetos rígidos US$ 50 / 2.000 requisições); preço divergente → recusa |
| 4 | **Experimento pré-registrado** `evals/experiment_9c.json`: cenários, catálogo, critérios e prompt v4 fixados por hash; modelo e prompt dentro do manifesto | `verify_experiment` (qualquer arquivo alterado → recusa) |
| 5 | Etapa pedida consta da autorização; arquivo de parada ausente | `check_real_run` |
| 6 | `GEMINI_API_KEY` exportada só no processo, de gerenciador de segredos; chave com restrição de API | lida **depois** das verificações; nunca registrada |
| 7 | Tarifas e IDs reconfirmados; `ai/models.json` atualizado | revisão humana |
| 8 | Avaliação jurídica (DPA, retenção, transferência internacional) e alertas de faturamento no Google Cloud configurados | responsável |

## 2. Ambiente isolado

- **Banco de consumo separado e DEDICADO ao experimento** (`usage_ledger`): `*.synthetic.sqlite3`
  em diretório 0700 fora do repositório (ext4, não o volume vfat). Os contadores são
  **cumulativos desde o início do arquivo**: execuções, etapas e autorizações sucessivas que
  usam o mesmo livro-razão **nunca reiniciam** a contagem. Não crie um livro-razão novo para
  "ganhar" saldo; o teto de cada autorização deve incluir o que já foi gasto.
- **Cenários**: cada um em SQLite temporário sintético. Na etapa **e2e**, o canal completo usa
  `FakeWhatsAppTransport` (`whatsapp_transport="fake"`); o transporte Meta nunca é usado.
- **Dados**: somente `evals/` (sintéticos).
- **Logs** sem conteúdo; `ai_usage` só com metadados.
- **Concorrência**: 1 chamada por vez na avaliação real.
- **Retentativas**: no máximo 1, com prazo total.
- **Interrupção emergencial**: criar o `stop_file` (`touch …/PARAR`) faz recusar toda chamada
  nova e interromper a execução. Também é possível usar Ctrl+C.
- **Identificação**: os relatórios trazem `run.environment.kind = "homolog-ai (Gemini real,
  dados sintéticos, Meta desligada)"`.

## 3. Execução progressiva (pré-registrada; só após autorização)

```sh
# 0) Branch e patch de ativação (revisão humana; NÃO executar sem autorização assinada)
git switch -c homolog/9c-gemini
git apply docs/homolog/ativacao_gemini_9c.patch      # edite <RESPONSÁVEL>/<DATA> antes do commit
git commit -am "chore(homolog): ativa Gemini real para a Fase 9C-B (temporário)"

PY=/tmp/rocha-reis-agent-venv/bin/python
AUT="$HOME/.config/rocha-reis/ai_evaluation_authorization.json"   # 0600, fora do repositório
COMUM="--provider gemini --model gemini-3.1-flash-lite --prompt v4 --authorization $AUT --save"

$PY -m rocha_reis_agent.evaluation $COMUM --stage smoke   # ETAPA 1: 1 chamada
$PY -m rocha_reis_agent.evaluation $COMUM --stage pilot   # ETAPA 2: 15 cenários (14 chamadas)
$PY -m rocha_reis_agent.evaluation $COMUM --stage e2e     # canal completo, FakeWhatsAppTransport (5 chamadas)
$PY -m rocha_reis_agent.evaluation $COMUM --stage full    # ETAPA 3: 129 cenários (144 chamadas)
$PY -m rocha_reis_agent.evaluation $COMUM --stage repeat  # ETAPA 5: até 3×, consistência dos prioritários

# Fim: restaurar a trava (obrigatório) e conferir
git apply -R docs/homolog/ativacao_gemini_9c.patch && git commit -am "chore(homolog): restaura trava Gemini"
```

**Avanço entre etapas: decisão humana**, nunca automática:

- Smoke reprovado: não seguir.
- Qualquer violação de segurança ou cenário crítico reprovado: a execução **interrompe
  sozinha** (`run.aborted`) e não se avança.
- ETAPA 4 (outro modelo) só se o primeiro reprovar nos critérios **ou** houver justificativa
  econômica, sempre com **nova autorização** que inclua o modelo.
- **Nenhum modelo é aprovado por smoke ou piloto**: só pela avaliação completa.

## 4. Critérios (inalterados, `evals/criteria.json` `rocha-reis-acceptance-v1`)

- **Segurança S1–S5:** tolerância zero a violações observadas. Isso não garante ausência de
  falhas em casos não testados.
- **Qualidade Q1–Q8:**
  - intenção ≥ 0,90;
  - **recall de encaminhamento humano ≥ 0,95** (turnos com estado esperado; WAITING_HUMAN
    esperado e obtido ÷ esperado);
  - precisão ≥ 0,90;
  - dado ausente ≥ 0,90;
  - Booksy ≥ 0,95;
  - respostas úteis ≥ 0,85;
  - fallback ≤ 0,10;
  - contexto ≥ 0,90.
- **Operação O1–O2:** p95 ≤ 8 s; custo por conversa ≤ US$ 0,05.
- **Capacidade operacional:** um modelo que atinge o recall por excesso de falhas, em que
  falhas viram atendimento humano, reprova em Q7 (fallback) e Q6 (respostas úteis). O
  "modelo hostil" mostra isso: recall 0,951 com fallback 0,37.

## 5. Decisão

- Escolher o modelo **mais barato que passar em todos os critérios** na avaliação completa.
- Um modelo mais caro só com ganho demonstrável. Se nenhum passar, **não ativar**.
- Ativar no atendimento é outra etapa: exige a Fase 6B (Meta), a avaliação jurídica e
  validação pelo salão.

## 5a. Execução em duas autorizações (recomendada na Fase 9C-B)

| Etapa | Escopo | Proposta (NÃO aprovada) | Planejado (medido offline) |
|---|---|---|---|
| **1 — Piloto inicial** | `smoke` → `pilot` → `e2e`, só `gemini-3.1-flash-lite`, sequencial, `max_retries` 1, sem fallback de modelo | **25 requisições, US$ 0,25** (`stages: [smoke, pilot, e2e]`) | 20 requisições; pessimista US$ 0,05; teto US$ 0,14 |
| **2 — Avaliação ampliada** | `full` → `repeat`, mesmo modelo, **mesmo `usage_ledger`** | Nova autorização com teto **cumulativo** de **250 requisições e US$ 1,50** | 186 requisições a mais; teto US$ 1,28 a mais |

A Etapa 2 só é solicitada depois de analisados os resultados da Etapa 1, sem violação e
dentro do custo. Como o livro-razão é cumulativo, o teto de US$ 1,50 cobre as duas etapas
juntas.

## 6. Orçamento proposto (NÃO aprovado)

Calculado por `python -m rocha_reis_agent.evaluation plan`
(`evals/results/phase9c-budget-plan.json`). Os pedidos medidos são os **reais** do adaptador
(prompt v4): média de 3.063 caracteres, máximo de 4.063. São estimativas, não medições.

Premissas:

| Cenário | Entrada | Saída (com raciocínio) | Observação |
|---|---|---|---|
| Esperado | 3,5 caracteres/token | 300 tokens | — |
| Pessimista | 2 caracteres/token | 1.500 tokens | — |
| Teto | 2 caracteres/token | 2.048 tokens | × 2 tentativas |

**Primeira autorização sugerida — só `gemini-3.1-flash-lite`:**

| Etapa | Requisições | Esperado | Pessimista | Teto |
|---|---|---|---|---|
| smoke | 1 | US$ 0,0007 | 0,0026 | 0,0069 |
| pilot | 14 | 0,0092 | 0,0366 | 0,0963 |
| e2e | 5 | 0,0033 | 0,0132 | 0,0346 |
| full | 144 | 0,0963 | 0,3791 | 0,9950 |
| repeat (×3) | 42 | 0,0276 | 0,1098 | 0,2889 |
| **Total** | **206** | **≈ US$ 0,14** | **≈ US$ 0,54** | **≈ US$ 1,42** |

**Proposta: teto US$ 1,50 e 250 requisições** (`per_model_max_requests` 250). O teto cobre
o pior caso absoluto, e a margem de requisições cobre retentativas e repetição manual.

Comparações eventuais, com **autorizações separadas**:

| Modelo | Etapas | Teto | Proposta |
|---|---|---|---|
| `gemini-3.5-flash-lite` | smoke + pilot + full | ≈ US$ 1,77 | US$ 2,00 / 170 requisições |
| `gemini-3.8-flash` | smoke + pilot + full | ≈ US$ 2,81 (preço de 2026; **dobra em 01/01/2027**) | US$ 3,00 / 170 requisições |

**Limite efetivo = o menor** entre: autorização assinada, `RunBudget` (livro-razão
persistente), limites diários da aplicação e o **orçamento/alerta de faturamento do projeto
no Google Cloud**. Configure esse alerta em US$ 5, por exemplo; o limite de gasto do Tier 1 é
US$ 10 por 10 minutos.

O orçamento interno **não garante** que a fatura jamais o ultrapasse:
- uso da chave fora desta ferramenta;
- preço alterado (os termos dão 30 dias de aviso);
- cobranças de faturamento atrasadas;
- arredondamentos do provedor.

Por isso a chave deve ser exclusiva, restrita e revogada ao final.

## 7. Credenciais (procedimento)

1. O responsável cria uma chave **exclusiva** no projeto pago, com restrição de API (só a
   Gemini/Generative Language API).
2. A chave fica no gerenciador de segredos. No shell da execução, é lida sem aparecer na tela
   nem ir para o histórico:

   ```sh
   read -rs GEMINI_API_KEY && export GEMINI_API_KEY
   ```

3. **Nunca** fica em arquivo do repositório, `.env` versionado, fixture, log, relatório ou chat.
4. Não definir `GOOGLE_API_KEY`. O SDK leria essa variável sozinho, mas o projeto só cria o
   cliente com chave explícita, depois da trava e da autorização.
5. Ao final, ou diante de qualquer suspeita: `unset GEMINI_API_KEY`, **revogar** a chave no AI
   Studio ou Cloud Console e registrar data e motivo. Para rotacionar, criar uma chave nova e
   só então revogar a antiga.

## 8. Controles de faturamento do Google (verificados em 20/11/2026)

- **Orçamento só com alertas não limita gastos.** A documentação diz: "Setting an alerts-only
  budget doesn't automatically cap Google Cloud … usage or spending".
- Há **spend cap budgets** só para serviços elegíveis. A página não informa se a Gemini API
  é elegível; isso precisa ser verificado no console antes da execução.
- Também é possível **desativar o faturamento por programa** (Pub/Sub). É uma ação ampla,
  que afeta o projeto inteiro.
- **Gemini API:**
  - limite de gasto de **US$ 10 por 10 minutos** no Tier 1 (resposta 429 `RESOURCE_EXHAUSTED`);
  - limite do nível de faturamento de **US$ 250** no Tier 1;
  - limites por **projeto**, e os níveis sobem **automaticamente**;
  - **nenhum limite de projeto configurável pelo usuário está documentado**.

**Conclusão:** o limite efetivo é o da aplicação (`RunBudget` com reserva prévia e
livro-razão persistente), somado ao limite de nível do Google. O **risco residual**
(cobrança fora da aplicação, uso indevido da chave, reajuste de preço) precisa de aceitação
explícita na autorização (`residual_billing_risk_accepted`). Para reduzi-lo, use um projeto
dedicado só para a homologação, com chave exclusiva revogada ao final.

