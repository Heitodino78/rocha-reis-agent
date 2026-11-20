# Relatório técnico — Fase 8 (IA generativa, conhecimento oficial e qualidade conversacional)

**Data:** 19–20/11/2026 · **Executor:** Claude · **Escopo:** camada de IA generativa
com provedor real implementado e **bloqueado**, gestão do conhecimento com aprovação,
prompts versionados, validação factual, segurança, avaliações reproduzíveis.
Somente dados sintéticos, provedores simulados e transporte fake. Fase 6B e Fase 9
não executadas. Referência técnica permanente: [`IA_GENERATIVA.md`](IA_GENERATIVA.md).

| Classificação | Estado |
|---|---|
| PLANEJADO | Ativação da IA real, medição de modelos, dados oficiais, validação com a equipe |
| IMPLEMENTADO | Adaptador Anthropic (bloqueado), contrato v2, prompt v2 (inativo), conhecimento no banco com aprovação pelo painel (API + interface), limites de consumo, minimização, módulo de avaliações, correções do detector |
| TESTADO LOCALMENTE | **678 testes pytest** e **10 testes de navegador real** (Chromium headless), 0 falhas, 0 ignorados; 5 configurações de avaliação com 0 violações de segurança; demonstração com processos reais |
| VALIDADO COM IA REAL | **Não** — nenhuma chamada externa foi feita |
| VALIDADO PELO SALÃO | Não |
| IMPLANTADO | Não |

---

## 1. Diagnóstico inicial

- Git em `d181bf4` (`main`) com **as Fases 7 e uma Fase 8 parcial não commitadas**:
  24 arquivos rastreados modificados e 34 não rastreados (o relatório da Fase 7 citava
  15 + 10; a diferença é o trabalho parcial da Fase 8). Nada foi revertido.
- O venv documentado (`/tmp/rocha-reis-agent-venv`) tinha sido apagado pela limpeza
  do `/tmp`; recriado a partir de `requirements.lock` com `--require-hashes`.
- **Suíte inicial: 651 aprovados, 1 falha intermitente** (`test_double_click_and_concurrent_duplicates_create_one_message`,
  ~1 em 5 execuções). Os 583 históricos da Fase 7 + 69 testes da Fase 8 parcial (49 + 20).
- **Fase 8 parcial encontrada no working tree, sem relatório:** `ai/anthropic_provider.py`,
  `ai/privacy.py`, `ai/pricing.json`, contrato `AIResponseV2`, validação de abertura,
  `prompts/v2.json`, migração 007 (`knowledge_revisions`, `ai_usage`),
  `persistence/sqlite_knowledge.py`, `persistence/sqlite_ai.py`, `panel/knowledge.py`,
  rotas `/knowledge*` e `/ai/usage`, comandos `knowledge-init`/`knowledge-import-fixture`,
  `tests/test_ai_generative.py` e `tests/test_knowledge_management.py`. Auditei e mantive.
- **Lacunas encontradas:** (1) nenhum módulo de avaliação; (2) painel sem interface de
  conhecimento (só API); (3) detector de regras não reconhecia "Prefiro falar com
  alguém", "Não quero falar com robô", anulava "não quero falar com robô, quero uma
  pessoa", e o padrão de preço `valores?` nunca casava "valor"; (4) sem teste de ponta
  a ponta webhook → IA real simulada → outbox; (5) `.env.example` e documentação sem a
  Fase 8; (6) Haiku 5.5 configurável com parâmetro que ele não aceita.

## 2. Arquitetura generativa

Fluxo completo em [`IA_GENERATIVA.md` §1](IA_GENERATIVA.md#1-fluxo). Princípio: **o
modelo propõe, o backend decide.** O modelo classifica a mensagem (intenções,
serviços citados, referências aos fatos fornecidos, pedido de humano, necessidade de
esclarecimento) e pode sugerir uma frase de cortesia. Toda informação comercial
(preço, horário, endereço, serviço, política, link Booksy) é escrita pelo backend a
partir de campos aprovados.

## 3–4. Provedor escolhido e justificativa

**Anthropic (Claude), SDK oficial `anthropic` 2.11** — já estava no lock, usa o mesmo
`httpx2` do projeto (testável com `MockTransport`), saída por JSON Schema, exceções
tipadas, recusa explícita (`stop_reason: refusal`) e fallback no servidor. Modelo
padrão `claude-opus-5-5`; parâmetros conferidos na referência oficial em 20/11/2026
(sem `temperature`; thinking sempre ligado; `effort` em `output_config`, padrão
`medium`). OpenAI avaliada só em critérios gerais; preços e políticas dela **não foram
verificados** e nenhum segundo SDK foi adicionado. Comparação e custos:
[`IA_GENERATIVA.md` §2–3](IA_GENERATIVA.md#2-escolha-do-provedor-inicial-anthropic-claude).

## 5. Contratos AIProvider

`AIProvider.generate(AIRequest, timeout_seconds)` preservado. `AIRequest` separa
instruções confiáveis, fatos (`authority="content-only"`), histórico e mensagem
(`UntrustedText`). `AIResponse` (v1, Fase 2) e `AIResponseV2` (Fase 8) com
`extra="forbid"`. `FakeAIProvider` preservado: mesmo comportamento em v1, e produz v2
sem abertura. Detalhes em [`IA_GENERATIVA.md` §4](IA_GENERATIVA.md#4-contratos).

## 6. Estrutura dos prompts

`prompts/registry.json` (ativo: **v1**, inalterado) e `prompts/v2.json` (registrado,
inativo; contém identificador, objetivo, data de criação, instruções, mudanças e
contrato). v2 explicita a hierarquia de confiança (instruções > regras do backend >
fatos > histórico > mensagem), a lista fechada de intenções e a proibição de fatos,
números, links, promessas e ações da equipe na abertura. Seleção só por
`AI_PROMPT_VERSION`; o provedor real exige v2. Comparação entre versões: rodar
`python -m rocha_reis_agent.evaluation --prompt v1` e `--prompt v2` (seção 15–16).

## 7. Recuperação de conhecimento

Determinística, sem embeddings nem banco vetorial (ADR-075): a intenção e o serviço
detectados selecionam exatamente os registros aprovados necessários; definições de
serviços citados entram para interpretar nomes. Volume esperado (dezenas de registros
estruturados) não justifica RAG vetorial; reavaliar se surgirem documentos longos.

## 8. Validação factual

1. Estrutura: Pydantic estrito, campos extras/ausentes, saída vazia, >32 KB, JSON truncado → falha.
2. Escopo: referências ⊆ fatos recuperados (versão incluída); FAQ coerente com intenção;
   serviço, contexto e catálogo **iguais** aos do controle determinístico; agendamento
   detectado pelo backend não pode ser removido pelo modelo.
3. Coerência: referências citadas = exatamente as que o backend usará.
4. Resposta montada por templates e campos aprovados; a abertura passa por filtro
   (números, links, marcação, termos de compromisso/oferta/equipe/instrução, nomes do
   catálogo, emojis) e é descartada se reprovada — a resposta factual continua.
5. Revalidação do catálogo ao final: mudou durante a geração → falha segura.
6. Dado ausente → "Não tenho … confirmado" + oferta de atendimento humano; nunca estimativa.

## 9. Segurança

- Travas independentes (IA × Meta), testadas nos dois sentidos; chave sozinha nunca ativa.
- Prompt injection: mensagem e histórico rotulados como não confiáveis; o backend não
  depende do prompt — saídas adulteradas são contidas pela validação (seção 16).
- Pedido humano decidido sem o modelo; estados humanos não chamam o modelo; corrida
  operador × geração resolvida pelo commit otimista e reconferida no despacho.
- O modelo não tem ferramentas nem campos administrativos; não aprova nem altera conhecimento.
- Isolamento: pedidos ao provedor só contêm texto do próprio cliente (verificado
  automaticamente em todos os cenários da avaliação).
- Logs e `ai_usage` sem conteúdo, prompt, chave ou identificador (testado).

## 10. Contexto conversacional

Último serviço mencionado (`service_context`) com idade em turnos (`SIM_CONTEXT_TURNS=5`);
pergunta de seguimento ("E quanto custa?") usa o contexto só com um serviço único e
dentro do limite; preço de serviço desconhecido limpa o contexto (não herda preço
anterior); histórico enviado: até 4 mensagens anteriores do mesmo cliente,
relacionadas ao serviço em contexto, 500 caracteres cada. Sem resumos de conversa.

## 11. Gestão do conhecimento

`knowledge_revisions` somente inclusão (triggers bloqueiam UPDATE/DELETE); cada ação
é uma nova revisão: rascunho (DRAFT) → aprovado (APPROVED) → revogado (ARCHIVED), ou
rejeitado (REJECTED). Escopo SYNTHETIC/OFFICIAL fixado uma vez — fixtures nunca entram
em base oficial; link `example.invalid` recusado em base oficial. Versão esperada contra
edição concorrente. Os validadores da Fase 2 rodam a cada inclusão: dois preços ativos
para o mesmo serviço são impossíveis; **rascunho novo suspende o dado anterior** (preço
em revisão nunca é informado pelo valor antigo). Opção de aprovador distinto.

## 12. Integração com o painel

Nova seção **Conhecimento** (só ADMIN, separada das conversas): listagem com busca e
filtros, pendências, cadastro e nova versão por tipo, aprovar/rejeitar/revogar com
confirmação, histórico de versões com origem e revisor, prévia do trecho factual (sem
IA e sem envio) e quadro da IA (provedor, trava, limites, consumo 24 h). Autorização
no backend (atendente recebe 403 mesmo chamando a API direto — testado no navegador).
Todo conteúdo via `textContent`. Fluxos da Fase 7 intactos (8 testes de navegador).

## 13. Limites de consumo

Tamanho da mensagem, histórico, entrada ao provedor, tokens de saída, concorrência,
tentativas, requisições/dia e custo estimado/dia — tabela em
[`IA_GENERATIVA.md` §6](IA_GENERATIVA.md#6-limites-de-consumo-implementados).

## 14. Política de dados

[`IA_GENERATIVA.md` §5](IA_GENERATIVA.md#5-privacidade-e-dados-lgpd--avaliação-antes-de-qualquer-ativação):
o que vai e o que nunca vai ao provedor, limites da minimização, decisões pendentes
(retenção, treinamento, transferência internacional, base legal). Nenhum dado real usado.

## 15. Estrutura de avaliações

`rocha_reis_agent/evaluation/` + `evals/`:

- `evals/scenarios.synthetic.json`: **77 cenários, 117 turnos (114 mensagens + 3 ações de operador), categorias A–T**, 54 críticos;
  inclui casos difíceis que o detector **não** resolve (para não maquiar métricas).
- `evals/catalog.synthetic.json`: catálogo fictício com preço condicionado à avaliação
  (luzes), preço com **rascunho pendente** (manicure), serviço **revogado** (progressiva)
  e serviço sem preço (escova).
- Execução sobre o núcleo **real**: SQLite temporário, estados, handoff, deduplicação.
- **Oráculo independente** do validador: preços fora dos aprovados, links diferentes do
  Booksy aprovado, afirmações de reserva/vaga/notificação (negações permitidas), ofertas
  sem registro, trechos do prompt, sequências de dígitos.
- Isolamento verificado em todos os pedidos enviados ao provedor.
- Provedores: `fake`, `hostile` (8 mutações em rodízio: JSON inválido, vazio, campo extra,
  referência inexistente, troca de serviço, preço na abertura, promessa, vazamento) e
  `anthropic-simulated` (adaptador real sobre HTTP em memória; tokens sintéticos).
- Corridas: operador assume **durante** a geração; catálogo muda durante a geração.
- Relatório JSON por provedor e versão de prompt; saída ≠ 0 com violação crítica.

## 16. Métricas (resultados reais, `evals/results/baseline-*.json`)

| Métrica | fake v1 | fake v2 | anthropic-simulated v2 | hostil v1 | hostil v2 |
|---|---|---|---|---|---|
| Cenários aprovados | 72/77 | 72/77 | 72/77 | 77/77 (só segurança) | 77/77 (só segurança) |
| Cenários críticos com falha | 0 | 0 | 0 | 0 | 0 |
| Violações de segurança | 0 | 0 | 0 | 0 | 0 |
| Acurácia de intenção | 0,963 | 0,963 | 0,963 | 0,037 | 0,333 |
| Aderência ao conhecimento (turnos sem afirmação sem suporte) | 1,0 | 1,0 | 1,0 | 1,0 | 1,0 |
| Taxa de afirmações sem suporte | 0,0 | 0,0 | 0,0 | 0,0 | 0,0 |
| Tratamento de dados ausentes | 0,8 | 0,8 | 0,8 | 0,0 | 0,2 |
| Encaminhamento humano correto | 0,970 | 0,970 | 0,970 | 0,788 | 0,879 |
| Silêncio em pausa humana | 1,0 | 1,0 | 1,0 | 1,0 | 1,0 |
| Regras do Booksy | 1,0 | 1,0 | 1,0 | 0,1 | 0,4 |
| Robustez a ataques (M, N) | 1,0 | 1,0 | 1,0 | 1,0 | 1,0 |
| Consistência de contexto (F, O, P) | 0,857 | 0,857 | 0,857 | 1,0 | 1,0 |
| Latência local p50 / p95 (ms) | 9 / 15 | 9 / 13 | 14 / 17 | 7 / 11 | 8 / 11 |
| Custo estimado | n/a | n/a | US$ 0,52 (tokens **sintéticos**) | n/a | n/a |

Leitura honesta:
- Com o modelo hostil, a qualidade despenca (as falhas viram encaminhamento humano),
  mas **nada falso chega ao cliente**. Em v2, aberturas maliciosas são descartadas e a
  resposta factual segue (28 descartes), por isso v2 preserva mais qualidade que v1.
- As 5 falhas restantes com o fake são **casos difíceis declarados**, todos de qualidade:
  D2 "promoção" não reconhecida; D4 "pix" não mapeado a política; F2 "e a escova?" após
  preço não herda a intenção de preço; K1 "idratação" (erro de digitação) não casa com
  o serviço; K3 "atendemte" (erro de digitação) não aciona humano. São limites do
  detector de regras — exatamente onde um modelo real pode ajudar, **desde que** o
  controle determinístico seja relaxado com nova avaliação (risco em IA_GENERATIVA §8).
- Latência e custo não representam um provedor real.
- **Nada aqui valida a qualidade de um modelo generativo real.**

## 17. Testes realizados

```text
pytest (início)                             → 651 passed, 1 failed (intermitente)
pytest (final)                              → 678 passed, 0 failed, 0 skipped
pytest tests_e2e (Chromium headless)        → 10 passed (8 da Fase 7 + 2 novos)
avaliações (5 configurações)                → 0 violações de segurança, 0 críticos com falha
teste de mutação do oráculo                 → validador quebrado ⇒ violações detectadas
```

Novos nesta execução: `tests/test_evaluation.py` (20), `tests/test_phase8_integration.py` (5),
`tests/test_ai_generative.py` (+1: parâmetros × capacidade do modelo),
`tests_e2e/test_knowledge_browser.py` (2). Já existentes da Fase 8 parcial:
`test_ai_generative.py` (49 casos com parametrização) e `test_knowledge_management.py` (20).

Mapa dos 40 itens pedidos: 1–5 `test_ai_generative` (offline, seleção, trava, sem
credencial, fake preservado); 6–12 `test_ai_generative` (requisição, resposta válida,
inválida, timeout, rede, recusa, referência inexistente) + avaliação R1–R8, Q1–Q2;
13–15 `test_knowledge_management` + C4/D1 + integração (rascunho suspende preço,
conflito impossível); 16–19 avaliação F/O/P + `test_ai_context_and_handoff`; 20–22
avaliação M/N + `test_ai_safety` + hostil; 23–24 E1–E6; 25–27 H1–H8,
`test_human_states_never_call_the_model…`; 28 `test_operator_takeover_during_slow_generation…`
+ T1; 29–33 `test_phase8_integration` (deduplicação, persistência, reinício, outbox) +
`test_whatsapp_*` (janela); 34 `test_ai_and_meta_locks_are_independent` +
`test_network_client_is_confined…`; 35–36 suíte do painel + e2e; 37
`test_logs_and_usage_never_contain_content_or_key` + log da demonstração; 38–39
`test_daily_request_budget…`, `test_cost_budget…`, `test_budget_exhaustion…`; 40 `test_evaluation`.

## 18. Resultados reais

Ver seções 16–17. Demonstração com processos reais (seção D do relatório final
abaixo) concluída em 20/11/2026: 13 etapas, log do servidor com 4 linhas de
inicialização e **nenhum** conteúdo de cliente; banco 0600 em diretório 0700; dados
da demonstração apagados ao final.

## 19–20. Falhas encontradas e correções

| # | Falha | Causa | Correção |
|---|---|---|---|
| 1 | Clique quádruplo na resposta do operador às vezes devolvia "tente novamente" | Retentativas de conflito do SQLite esgotadas enquanto outro pedido com a mesma chave gravava | Conferir a chave de idempotência antes de devolver erro (ADR-081); 25/25 execuções aprovadas |
| 2 | "Qual o valor da hidratação?" não era pergunta de preço | Regex `valores?` casa "valore(s)", nunca "valor" (pré-existente desde a Fase 1) | `valor(es)?` |
| 3 | "Prefiro falar com alguém", "Não quero falar com robô" não acionavam humano | Vocabulário do detector | Padrões novos, restritos ("falar com alguém", recusa ao robô) |
| 4 | "Não quero falar com robô, quero uma pessoa" era ANULADO | Negação com janela de 40 caracteres | Negação só quando nega diretamente a pessoa |
| 5 | Reclamação sem a palavra "reclamação" não escalava | Vocabulário | Sinais explícitos ("péssimo", "insatisfeita", "horrível", "absurdo") |
| 6 | "Confirme meu horário" virava "não compreendi" | Vocabulário | Pedido de confirmação vira orientação Booksy (nunca confirmação) |
| 7 | Haiku 5.5 aceitava `fallbacks` que ele não suporta | Validação de configuração | Exige `AI_REFUSAL_FALLBACK=off` |
| 8 | Avaliação hostil só exercitava "JSON inválido" | Rodízio reiniciado por cenário | Rodízio deslocado por cenário; todas as 8 mutações aplicadas |
| 9 | Ações da tabela de conhecimento empilhadas; filtros em largura total | CSS flex na célula | Contêiner interno; filtros flexíveis (inspeção visual desktop/celular) |
| 10 | Teste de confinamento de rede acusou `import httpx2` no módulo de avaliação | Uso legítimo de `MockTransport` | Exceção **estreita** no teste (só `MockTransport/Request/Response`, sem `base_url`) — não contornado |

## 21. Limitações

- Nenhum modelo real executado: compreensão, tom, latência e custo reais desconhecidos.
- Serviço/contexto/agendamento continuam fixados pelo detector de regras (segurança > flexibilidade).
- Sanitização da abertura por lista de termos; minimização não remove nomes/dados livres.
- Custos são estimativas por tabela; o `anthropic-simulated` usa tokens sintéticos.
- Interface de conhecimento sem edição em lote nem importação oficial (deliberado).
- No celular, a tabela rola dentro do próprio contêiner (mesmo padrão da Equipe, Fase 7).
- Sem leitor de tela nem Firefox/Safari nos testes.

## 22. Pendências de dados oficiais

Checklist de coleta em [`DADOS_PENDENTES.md`](DADOS_PENDENTES.md#checklist-de-coleta-junto-ao-salão-rocha-reis-para-a-base-de-conhecimento).
Nenhum dado foi marcado como confirmado; a base oficial (`knowledge/official.json` e
escopo OFFICIAL no banco) continua vazia.

## 23. Condições de ativação da IA real

[`IA_GENERATIVA.md` §7](IA_GENERATIVA.md#7-condições-para-ativar-a-ia-real-todas-obrigatórias):
autorização escrita, avaliação jurídica, chave em gerenciador de segredos, tarifas
reconfirmadas, troca revisada da trava de código, prompt v2 explícito, primeira
execução só sobre os cenários sintéticos com critérios de aceite.

## 24. Riscos para produção

Todos os da Fase 7 (HTTP local, sem 2FA, SQLite de um escritor, sem notificação
externa) e mais: dependência de fornecedor externo (indisponibilidade → encaminhamento
humano em massa), custo variável, retenção no provedor, falsos negativos do detector
em linguagem informal, e o risco de relaxar o controle determinístico sem nova avaliação.

## 25. Recomendação para a próxima fase

1. Coletar e aprovar os **dados oficiais** pela seção Conhecimento (base OFFICIAL), com
   a checklist do salão, e validar o painel com a equipe (sem dados de clientes).
2. Com autorização e orçamento de teste, rodar a avaliação com o provedor real nos três
   modelos candidatos (IA_GENERATIVA §3) e decidir modelo, esforço e ativação do prompt v2.
3. Só depois disso considerar a Fase 6B (Meta) — as travas continuam independentes.

---

## A. Diagnóstico

Seção 1. Arquitetura encontrada: núcleo determinístico das Fases 1–4 com contrato de
IA restrito, persistência SQLite, canal Meta offline, painel da Fase 7 e uma Fase 8
parcial coerente com esse desenho (modelo classifica, backend escreve fatos).

## B. Implementação

**Criados nesta execução:**
- `rocha_reis_agent/evaluation/{__init__,scenarios,oracle,providers,runner,__main__}.py`
- `evals/catalog.synthetic.json`, `evals/scenarios.synthetic.json`, `evals/results/baseline-*.json` (5)
- `tests/test_evaluation.py`, `tests/test_phase8_integration.py`, `tests_e2e/test_knowledge_browser.py`
- `docs/IA_GENERATIVA.md`, `docs/STATUS_FASE_8.md`

**Modificados nesta execução:**
- `rocha_reis_agent/intents.py` (correções 2–6), `rocha_reis_agent/config.py` (Haiku),
  `rocha_reis_agent/panel/service.py` (idempotência), `rocha_reis_agent/panel/static/{index.html,app.js,styles.css}` (seção Conhecimento)
- `tests/test_ai_generative.py` (+1 teste), `tests/test_whatsapp_security.py` (exceção estreita)
- `.env.example`, `README.md`, `docs/{ARQUITETURA_E_DECISOES,PLANO_DE_EXECUCAO,CONTEXTO_MESTRE,DADOS_PENDENTES,CHECKLIST_PRODUCAO}.md`

**Fase 8 parcial auditada e mantida (já estava no working tree):** listada na seção 1.

## C. Qualidade

Seções 16–20. Testes ignorados: nenhum. Problemas de segurança encontrados: nenhuma
violação nas avaliações; a falha 4 (pedido humano anulado) era o defeito de maior
impacto no atendimento e foi corrigida.

## D. Demonstração reproduzível

Executada em 20/11/2026 com processos reais (CLI + uvicorn + API HTTP do painel).
Use um diretório 0700 fora do volume vfat (ex.: `$HOME/.local/share/rocha-reis-agent/fase8-demo`).

```sh
D="$HOME/.local/share/rocha-reis-agent/fase8-demo"; install -d -m 700 "$D"
export APP_ENV=local STORAGE_BACKEND=sqlite STORAGE_PATH="$D/painel.synthetic.sqlite3" \
  WHATSAPP_ENABLED=true WHATSAPP_PHONE_NUMBER_ID=100000000000001 WHATSAPP_BUSINESS_ACCOUNT_ID=200000000000002 \
  PANEL_ENABLED=true PANEL_ORIGIN=http://127.0.0.1:8000 PANEL_COOKIE_SECURE=false PANEL_EMBEDDED_WORKER=true \
  KNOWLEDGE_SOURCE=database AI_PROVIDER=fake AI_PROMPT_VERSION=v2          # 2. FakeAIProvider
for n in WHATSAPP_VERIFY_TOKEN WHATSAPP_APP_SECRET WHATSAPP_CONVERSATION_KEY; do
  export "$n=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"; done
PY=/tmp/rocha-reis-agent-venv/bin/python
$PY -m rocha_reis_agent.panel.admin_cli create-admin --login dona --name "Responsável"
$PY -m rocha_reis_agent.panel.admin_cli knowledge-init --scope synthetic --confirm KNOWLEDGE_SCOPE:SYNTHETIC
$PY -m rocha_reis_agent.panel.admin_cli knowledge-import-fixture
$PY -m uvicorn rocha_reis_agent.panel.app:create_panel_app --factory --host 127.0.0.1 --port 8000 \
    --no-access-log --timeout-graceful-shutdown 5                          # 1. iniciar
# 3–4. Painel → Conhecimento: cadastrar serviço "hidratacao" e preço 80,00 → Aprovar
# 5–9. Em outro terminal (mesmas variáveis):
$PY -m rocha_reis_agent.panel.admin_cli say --from 5511900000801 "Vocês fazem hidratação?" --process
$PY -m rocha_reis_agent.panel.admin_cli say --from 5511900000801 "E quanto custa?" --process
$PY -m rocha_reis_agent.panel.admin_cli say --from 5511900000801 "Quanto custa uma coloração?" --process
$PY -m rocha_reis_agent.panel.admin_cli say --from 5511900000801 "Quero marcar para sexta" --process
$PY -m rocha_reis_agent.panel.admin_cli say --from 5511900000801 "Prefiro falar com alguém" --process
# 10–11. Painel → Conversas: abrir, Assumir, Finalizar, Retomar automação (admin)
$PY -m rocha_reis_agent.evaluation --provider fake --prompt v2           # 12. avaliações
# 13. Painel → Conhecimento → "Inteligência artificial" (consumo) e Auditoria
```

Execução registrada (mesmo roteiro, porta 8765, via API do painel):

```text
4.  service:hidratacao v1 DRAFT → v2 APPROVED; price:hidratacao v1 DRAFT → v2 APPROVED
    prévia: "Hidratação de teste: R$ 80,00. Valor fictício da demonstração."
5-6. "Vocês fazem hidratação?" → descrição do serviço; "E quanto custa?" → "R$ 80,00" (contexto)
7.  "Quanto custa uma coloração?" → "Não tenho preço confirmado para o serviço informado…"
8.  "Quero marcar para sexta" → link Booksy fictício + "Não consultei disponibilidade nem … confirmei reserva."
9.  "Prefiro falar com alguém" → WAITING_HUMAN; "Alô?" → recorded_without_reply (nenhuma resposta automática)
10. assumir pelo painel → HUMAN_ACTIVE
11. finalizar + retomar → AUTO; "Quanto custa o corte?" → "R$ 45,00"
13. /ai/usage: provider=fake, prompt=v2, trava real=False, uso 24h vazio (fake não consome)
    auditoria: login, user_created, knowledge_drafted, knowledge_approved, conversation_assumed,
               conversation_resolved, automation_resumed
    log do servidor: 4 linhas de inicialização, 0 ocorrências de texto de cliente
```

## E. Prontidão

| | Estado |
|---|---|
| Implementado | Seções 2–15 |
| Testado localmente | 678 pytest + 10 navegador + 5 avaliações + demonstração |
| Não testado | Qualquer modelo real; carga; leitores de tela; outros navegadores; dados oficiais |
| Exige aprovação | Provedor de IA, orçamento, política de dados, dados oficiais, ativação do prompt v2 |
| Falta para IA real | IA_GENERATIVA §7 (autorização, jurídico, chave, trava, medição) |
| Falta para Meta real | Fase 6B (autorização, número de teste, HTTPS, token) — inalterado |
| Falta para produção | CHECKLIST_PRODUCAO.md (infraestrutura, LGPD, backup, 2FA/HTTPS, operação) |

**Git:** nenhum commit nem push (proibidos sem autorização). Mensagem sugerida, se
autorizado (inclui o trabalho não commitado da Fase 7):

```text
feat: Fases 7 e 8 — painel humano; IA generativa bloqueada, conhecimento aprovado e avaliações
```
