# Relatório de implementação — Fase 1

**Data:** 19/11/2026 (America/Sao_Paulo). **Escopo:** núcleo determinístico e CLI local.

> Registro histórico da Fase 1. A continuação foi autorizada depois;
> o estado atual está em [STATUS_FASE_2.md](STATUS_FASE_2.md).

## Leitura e estado inicial

Antes de implementar, foram lidos integralmente os 25 arquivos existentes:
instruções, todos os documentos, código, testes, exemplos de configuração,
dependências e lock. O anexo de missão também foi lido. A skill
`.agents/skills/rocha-reis-development/SKILL.md` continua ausente, inclusive nos
diretórios locais de skills pesquisados. Nenhuma instrução substituta foi inventada.

O ambiente `/tmp/rocha-reis-agent-venv` estava funcional: `pip check` sem conflitos
e **14 testes da Fase 0 aprovados em 0.21s**, antes das alterações. A pasta não
possui `.git`; hashes dos 25 arquivos iniciais foram registrados em
`/tmp/rocha-reis-phase1-baseline-_sdvioda.json` para conferir a lista de mudanças.

Foi identificada e comunicada a diferença entre os estados do anexo atual e a
proposta anterior. Pela precedência do pedido atual, adotaram-se quatro estados,
preservando os nomes anteriores como aliases. Não houve mudança de decisão sobre
Booksy, WhatsApp ou IA. Nenhuma dependência foi adicionada ou atualizada.

## Funcionalidades e arquitetura implementadas

- `ConversationManager`: criação, consulta, encerramento e reinício de sessões,
  histórico, contexto e limites de memória por configuração.
- `IntentDetector` e `RuleIntentDetector`: interpretação por regras explícitas,
  múltiplas intenções, aliases de serviços e FAQs exatas. O protocolo permite
  substituir o detector sem alterar o gerenciador de conversas.
- `KnowledgeBase`/`KnowledgeRepository`: esquema tipado para nome, endereço,
  funcionamento, serviços/preços, profissionais, políticas, Booksy e FAQs.
  A CLI só importa fixtures sintéticas; sem argumentos usa base vazia.
- `ResponseGenerator`: português brasileiro, respostas factuais a partir da
  base carregada, esclarecimento de ambiguidades e informação ausente. Atende
  até três intenções de conteúdo por resposta; humano tem precedência.
- `HumanHandoffManager`: transições validadas e ações do operador local separadas
  das mensagens. O bot permanece em pausa após resolução, até retomada explícita.
- `AgentService`: coordenação, deduplicação, contexto e falha segura dos adaptadores.
- `InMemoryConversationRepository`: cópias isoladas e transação serializada por
  processo; não salva alterações quando uma operação falha.
- CLI: seleção de clientes, mensagem, histórico, estado, auditoria, operador,
  encerramento e reinício. Nenhuma rota HTTP administrativa foi adicionada.

Intenções: saudação, serviço, preço, funcionamento, localização, profissionais,
agendamento, humano, política, FAQ e desconhecida. Há normalização de caixa,
acentos e pontuação para detecção. O texto armazenado preserva acentos, normaliza
espaços e rejeita entradas vazias, grandes ou com controles de terminal.

`InboundMessage` e `AgentResult` padronizam entrada/saída. Histórico e textos de
mensagens não aparecem nos `repr` dos contratos. Logs de falha contêm somente
eventos estáticos, sem exceção, mensagem ou identificador de cliente.

## Estados e autorização local

```text
AUTO -- pedido de humano / falha --> WAITING_HUMAN
WAITING_HUMAN -- assume --> HUMAN_ACTIVE
WAITING_HUMAN -- resolve --> RESOLVED
HUMAN_ACTIVE -- resolve --> RESOLVED
RESOLVED -- resume explícito --> AUTO
```

`BOT_ACTIVE` é alias de `AUTO`; `PENDING_HUMAN`, de `WAITING_HUMAN`. Não havia
persistência anterior a migrar. `WAITING_HUMAN`, `HUMAN_ACTIVE` e `RESOLVED`
registram mensagens sem respostas automáticas de conteúdo. O pedido inicial
produz uma única confirmação dizendo que ninguém real foi notificado.

`operador-local` é uma identidade fictícia permitida pelo gerenciador. A fronteira
de confiança é o acesso ao terminal: não há autenticação remota. Mensagens de
cliente não executam comandos ou retomam conversas. Ações inválidas ou identidade
de operador não permitida são rejeitadas sem alterar a sessão. `/reset` e `/close`
são operações administrativas locais explícitas, separadas de `/send`.

## Conhecimento, Booksy e contexto

Formato e exemplo em [salon.synthetic.json](../fixtures/salon.synthetic.json).
`schema_version=1` e `synthetic=true` são exigidos para carregar uma fixture.
Serviços têm ID único, aliases e preço decimal opcional; FAQs usam perguntas
alternativas exatas. Esquema rejeita chaves desconhecidas, preços inválidos,
identificadores duplicados, caracteres de controle e arquivos acima de 100 KB.

A demonstração contém **Corte de teste por R$ 45,00**, **Escova de teste sem preço**,
endereço/horário/profissional fictícios e um link em `example.invalid`. Cada
resposta identifica os dados fictícios. Nada foi registrado como informação real
do Salão Rocha Reis. A execução padrão não carrega esses dados.

Booksy: validação local de HTTPS, domínio e formato, sem DNS ou requisição.
Aceita `booksy.com` e subdomínios, e domínio reservado `example.invalid` em base
sintética. Não verifica existência/titularidade de perfil; o link oficial precisa
ser fornecido e validado pelo responsável no futuro. Link ausente/inválido gera
indisponibilidade e oferta de ajuda humana. Nenhuma reserva é criada, consultada,
alterada ou confirmada, inclusive nas perguntas “Você marcou?” e sobre vagas.

Contexto: um serviço recente pode ser reutilizado por pergunta curta de preço.
Com mais de um serviço, contexto vencido ou ausente, solicita esclarecimento.
Uma pergunta explícita sobre serviço desconhecido limpa o contexto de serviço,
evitando atribuir o preço do anterior. Clientes têm sessões/contextos independentes.

## Limites e idempotência

Valores padrão, ajustáveis pelas variáveis de ambiente `SIM_` do `.env.example`:

| Limite | Padrão |
|---|---|
| Caracteres da mensagem antes da normalização | 2000 |
| Entradas do histórico e, separadamente, auditoria | 100 |
| Eventos únicos por sessão | 1000 |
| Conversas por processo | 100 |
| Turnos automáticos para contexto recente | 5 |

Eventos são deduplicados por conversa/sessão/ID. Mesmo ID e texto retorna
`duplicate=true`, sem resposta nem nova entrada de histórico. Mesmo ID com outro
texto é conflito; texto igual com outro ID é evento novo. IDs não são descartados
para abrir espaço: ao atingir o limite, novos eventos são recusados. Histórico
antigo pode ser descartado independentemente da deduplicação.

`/reset` cria nova sessão e apaga contexto, histórico e deduplicação. Fechar uma
sessão bloqueia novas mensagens; o histórico ainda pode ser consultado. Todas as
conversas ficam em memória até sair; sessões fechadas ocupam sua vaga. Isso não
implementa persistência durável, retenção aprovada ou garantia distribuída.

## Arquivos e decisões

Novos módulos:

```text
rocha_reis_agent/
├── application/
│   ├── __init__.py
│   ├── conversations.py
│   ├── handoff.py
│   ├── ports.py
│   └── service.py
├── domain/conversations.py
├── intents.py
├── knowledge.py
├── memory.py
├── responses.py
└── simulator.py
```

Também criados `fixtures/salon.synthetic.json`, `examples/phase1.demo.txt`, este
relatório e os sete arquivos de testes de atendimento, Booksy, handoff, entradas/
falhas, conhecimento, sessões e CLI. A árvore completa está no README.

Modificados os contratos de domínio, configuração/`.env.example`, docstrings dos
pacotes, fixtures de teste e testes de configuração; README, contexto mestre,
arquitetura, plano e a indicação histórica do relatório da Fase 0 foram atualizados.
API HTTP, teste de saúde, dependências/lock, `AGENTS.md`, requisitos, dados
pendentes e prompt inicial foram preservados. Decisões: ADR-012 a ADR-016 em
`ARQUITETURA_E_DECISOES.md`. Não foram criados commits ou Git nesta pasta.

## Comandos e testes observados

```sh
/tmp/rocha-reis-agent-venv/bin/python -m pip check
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture < examples/phase1.demo.txt
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator
```

A última suíte executada no ambiente de trabalho aprovou **124 testes em 0.87s**.
`pip check`: **No broken requirements found.** As fixtures bloqueiam conexões,
resolução DNS e envio UDP durante os testes. Não houve download de dependências
ou chamada a APIs externas nesta fase.

Foi criada a cópia limpa `/tmp/rocha-reis-phase1-repro-7t1s3zso`, sem `.env` ou
caches, contendo fontes, testes, fixtures, exemplo, configuração pytest e lock.
Ela foi testada com o segundo venv independente instalado na Fase 0. Não foi
necessária nova instalação porque `requirements.in` e `requirements.lock`
permaneceram idênticos aos hashes iniciais. Comandos executados nessa cópia:

```sh
/tmp/rocha-reis-phase0-repro-y3ei0spd/venv/bin/python -m pip check
/tmp/rocha-reis-phase0-repro-y3ei0spd/venv/bin/python -m pytest -q
```

Resultado: **124 testes aprovados em 0.66s**, sem conflitos de dependências,
**`CLEAN_REPRODUCTION=PASS`**. Essa reprodução usa Python 3.13.16/Linux e um venv
independente existente; não representa instalação nova ou teste em outra plataforma.

A auditoria final comparou hashes com o inventário inicial: **21 arquivos
adicionados, 12 modificados e nenhum removido**. Os **46 arquivos** foram
inspecionados quanto à sintaxe Python, destinos de links Markdown relativos e
padrões conhecidos de credenciais, sem encontrar problemas ou `.env` local:
**`SOURCE_AUDIT=PASS`** e **`DEPENDENCY_LOCK_UNCHANGED=PASS`**. Trata-se de inspeção
local pontual, sem alegação de auditoria completa de segurança.

| Cenários obrigatórios | Evidência automatizada |
|---|---|
| Saudação; serviço conhecido/desconhecido; preço presente/ausente; funcionamento | `test_attendance.py` |
| Agendamento; Booksy configurado/ausente/inválido; não confirmar reserva | `test_booking.py` |
| Humano; pausa; retomada autorizada; transições inválidas | `test_handoff.py` |
| Isolamento entre clientes; continuidade/ambiguidade de contexto; intenção desconhecida | `test_attendance.py` e `test_sessions.py` |
| Mensagem vazia/inválida; limites; falhas seguras de adaptadores | `test_inputs_and_failures.py` |
| Eventos repetidos, conflitos, entregas concorrentes, fechamento e reinício | `test_sessions.py` |
| JSON sintético; CLI, histórico e separação de comandos | `test_knowledge.py` e `test_simulator.py` |
| Configuração e HTTP da Fase 0 | `test_config.py` e `test_health.py` |

Um servidor Uvicorn temporário foi iniciado em porta dinâmica de `127.0.0.1`.
Um cliente HTTP da biblioteca padrão, com proxy desativado, confirmou
**`LIVE_HEALTH=200 {'status': 'ok'}`**. O processo foi encerrado no bloco `finally`:
**`LOCAL_SERVER_STOPPED=True`**. Nenhum servidor desta tarefa ficou ativo.

Sem `--fixture`, a CLI real respondeu:

```text
[SIMULAÇÃO LOCAL] O agendamento online ainda não está disponível pelo agente: não há link Booksy válido configurado. Se quiser, peça atendimento humano para registrarmos o pedido nesta simulação.
Estado: AUTO; encerrada=False; contexto=ausente; histórico=2; eventos=1
```

## Demonstração completa e handoff

Entrada reproduzível: [examples/phase1.demo.txt](../examples/phase1.demo.txt).
O processo real terminou com código 0. A sequência observada incluiu:

```text
Cliente A: Olá!
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] Olá! Sou o assistente virtual de Salão Exemplo — DADOS FICTÍCIOS, em simulação determinística. Como posso ajudar?
Cliente A: Quero informações sobre corte.
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] Corte de teste: Serviço fictício para validar o simulador.
Cliente A: E quanto custa?
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] Corte de teste: R$ 45,00. Valor fictício, exclusivo desta demonstração.
Cliente A: Quero agendar um corte para sábado.
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] O agendamento deve ser realizado por você no Booksy: https://example.invalid/booksy-sintetico . Não consultei disponibilidade nem realizei ou confirmei reserva.
Cliente B: E quanto custa?
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] De qual serviço você quer saber o preço? Preciso que indique um serviço.
Cliente A: Quero falar com uma pessoa.
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] Registrei o pedido nesta simulação e pausei as respostas automáticas. Nenhum atendente real foi notificado.
Estado: WAITING_HUMAN
Cliente A: Quanto custa?
[automático pausado; mensagem registrada]
/operator assume
Estado: HUMAN_ACTIVE
/operator reply Esta é uma resposta humana fictícia, registrada apenas no simulador.
Resposta do operador registrada somente nesta simulação.
Cliente A: Olá, você está aí?
[automático pausado; mensagem registrada]
/operator resolve
Estado: RESOLVED
Cliente A: Pode retomar?
[automático pausado; mensagem registrada]
/operator resume
Estado: AUTO
Cliente A: E quanto custa?
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] Corte de teste: R$ 45,00. Valor fictício, exclusivo desta demonstração.
Repetição do evento a9:
[duplicata ignorada]
Cliente B: Vocês fazem escova?
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] Escova de teste: Serviço fictício sem preço cadastrado.
Cliente B: E quanto custa?
Bot: [SIMULAÇÃO — DADOS FICTÍCIOS] Não tenho preço confirmado para Escova de teste. Se quiser, peça atendimento humano para registrarmos o pedido nesta simulação.
/close
Sessão encerrada.
/reset
Nova sessão em AUTO; histórico, contexto e eventos anteriores descartados.
Estado: AUTO; encerrada=False; contexto=ausente; histórico=0; eventos=0
```

A CLI também mostrou histórico do cliente B e auditoria das quatro transições
do cliente A. A transcrição acima identifica os participantes para facilitar a
leitura; o arquivo de entrada contém os comandos exatos executados.

## Problemas corrigidos e limitações

A primeira execução dos novos testes teve **110 aprovados e 2 falhas**. O helper
de testes gerava IDs iguais aos IDs explicitamente usados em alguns cenários.
A implementação corretamente rejeitou o conflito de conteúdo; o helper passou a
usar namespace `auto-evento-*`. Também foram acrescentados controles para rejeitar
JSON com marcadores sintéticos de tipo incorreto e controles de terminal.

O motor é baseado em regras pequenas e aliases, não em linguagem natural geral.
Não resolve paráfrases arbitrárias, negações complexas, referência a vários
serviços, calendário ou disponibilidade. Com incerteza pede esclarecimento ou
oferece ajuda humana. A proteção contra instruções de cliente decorre das ações
permitidas e da base imutável; não é evidência de segurança de um LLM futuro.

Não há IA real, SDK de modelo, banco, WhatsApp, inbox, mensagens externas,
notificação a funcionário ou implantação. Sem autenticação remota, durabilidade,
fila, retry distribuído, ordenação de webhooks ou métricas de produção.

Dados reais ainda pendentes: link oficial Booksy, catálogo/preços, profissionais,
endereço, funcionamento, políticas, FAQs e tom. Inbox, responsáveis, horários de
atendimento humano, orçamento, retenção e autorizações continuam conforme
`DADOS_PENDENTES.md`. Não foram preenchidos por suposição.

## Estado e próxima fase

| Categoria | Estado |
|---|---|
| Planejado | Fases 2 a 6 e produto final |
| Implementado | Núcleo e simulador determinístico da Fase 1 |
| Testado localmente | Suíte, demonstração CLI com dois clientes, handoff e HTTP real |
| Validado com usuário | Pendente de revisão desta entrega; dados/respostas não aprovados pelo salão |
| Implantado | Não |

Interfaces para detector, conhecimento e conversas preparam a próxima fase sem
implementar provedor de IA. O simulador permanece utilizável sem rede. A Fase 2
não foi iniciada e exige novo pedido.

## Prompt sugerido para a Fase 2

```text
Execute somente a Fase 2 — Base de conhecimento e IA do Rocha Reis Agent.
Leia todo o repositório, AGENTS.md e os documentos de referência, incluindo
STATUS_FASE_0.md e STATUS_FASE_1.md. Use a skill do projeto se disponível;
registre sua ausência se necessário. Execute a suíte existente antes de editar.
Apresente plano curto, arquivos afetados e critérios verificáveis.

Evolua o conhecimento para registros com origem, versão/data e aprovação,
preservando a separação explícita entre fixtures sintéticas e dados oficiais.
Não preencha dados desconhecidos por suposição. Introduza AIProvider com saída
estruturada validada e FakeAIProvider determinístico; permita substituir o
detector sem reescrever sessões, handoff, deduplicação ou CLI. Mantenha regras
de negócio e validação factual fora da autonomia do modelo.

Versione prompts com limites de ação e tratamento das mensagens como entrada
não confiável. Teste informação ausente, respostas factuais sem suporte,
instruções maliciosas, saída inválida do provedor, falha/timeout, isolamento
entre clientes e ausência de segredos ou texto privado em logs.
Preserve os testes das fases anteriores, a pausa humana e a retomada explícita.

Use apenas dados sintéticos e fake local por padrão. Não faça chamadas pagas
nem peça credenciais. Uma integração real exige autorização explícita própria.
Booksy continua exclusivamente por link configurado: nenhuma API, scraping,
consulta de vaga ou criação/alteração/cancelamento/confirmação de reserva.
Não integrar WhatsApp, inbox ou banco de produção e não implantar.

Execute testes, registre comandos/resultados reais, decisões, limitações e
pendências. Diferencie planejado, implementado, testado localmente, validado
com usuário e implantado. Não avance automaticamente para a Fase 3.
```
