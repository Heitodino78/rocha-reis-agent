# Rocha Reis Agent

Fundação e simulador local de atendimento em português para o **Salão de Beleza
Rocha Reis**, projeto da agência de **Heitor e Renan**. Fases 0 a 5, 6A, 7, 8, 9A e 9B
implementadas e testadas localmente: API com `GET /health`, núcleo de conversas por CLI,
conhecimento versionado, arquitetura de IA com **FakeAIProvider determinístico**,
persistência SQLite sintética e **preparação offline do webhook oficial Meta
WhatsApp Cloud API** com inbox/outbox duráveis e transporte **fake**, e o
**adaptador HTTP Graph API** (Fase 5) implementado porém **bloqueado** por trava de
código. A Fase 8 acrescentou conhecimento gerido e aprovado pelo painel, o contrato de
IA v2 e um **módulo de avaliações** reproduzível; a Fase 9A homologou a IA **offline**
(orçamento com reserva de pior caso, correções de qualidade, 120 cenários com critérios
pré-registrados). **Na Fase 9B o provedor oficial passou a ser o GOOGLE GEMINI**: adaptador
`GeminiProvider` (SDK oficial `google-genai`) implementado e **bloqueado** por trava de
código própria; a integração Anthropic das Fases 8/9A é histórica e foi removida. Nenhum
modelo generativo real, conta Meta ou envio real está conectado.

**Booksy:** somente orientação por link configurado, sem consultar disponibilidade
ou realizar operações sobre reservas. O link oficial e os dados do salão continuam
pendentes. A demonstração usa `example.invalid`, identificado como fictício.

## Instalar no Linux

Execute os comandos na raiz do projeto. Ambiente de referência: **Python
3.13.16**, com `venv` e `pip`. O lock foi gerado para Python 3.13/Linux; outras
versões/plataformas precisam de validação própria.

```sh
python3 --version
RR_VENV=/tmp/rocha-reis-agent-venv
python3 -m venv "$RR_VENV"
"$RR_VENV/bin/python" -m pip install --disable-pip-version-check --index-url https://pypi.org/simple --require-hashes -r requirements.lock
"$RR_VENV/bin/python" -m pip check
```

O ambiente fica em `/tmp` porque o volume deste projeto recusou os links
simbólicos exigidos pelo `venv`. `/tmp` pode ser limpo pelo sistema: nesse caso,
repita a instalação. Em um sistema de arquivos que suporte links, também é
possível usar `RR_VENV=.venv`. Não é preciso ativar o ambiente nem instalar o
projeto como pacote: os comandos são executados a partir da raiz.

Somente a instalação/atualização de dependências acessa o índice público PyPI.
Executar a aplicação e os testes não requer credenciais nem APIs externas.

## Executar localmente

```sh
RR_VENV=/tmp/rocha-reis-agent-venv
"$RR_VENV/bin/python" -m uvicorn rocha_reis_agent.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Em outro terminal:

```sh
curl --noproxy '*' --fail-with-body -i --max-time 5 http://127.0.0.1:8000/health
```

Resultado esperado: **HTTP 200**, JSON `{"status":"ok"}`. Use `Ctrl+C` para
encerrar o servidor. O endpoint indica apenas que o processo HTTP responde;
não atesta prontidão do agente ou de provedores. A documentação HTTP automática
está desativada. Esta aplicação expõe somente `/health` e, com `WHATSAPP_ENABLED=true`,
o webhook da Fase 4; o painel da Fase 7 é uma aplicação separada. Não há rotas HTTP
de simulação.

## Executar o simulador com FakeAIProvider

Com o mesmo ambiente virtual, execute na raiz:

```sh
# Interativo com dados explicitamente fictícios
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture

# Demonstração completa com dois clientes e handoff
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture < examples/phase1.demo.txt

# Demonstração da Fase 2 com proveniência e tentativas de alterar regras
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture < examples/phase2.demo.txt

# Catálogo versionado com origem e aprovação sintéticas
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --catalog fixtures/salon.versioned.synthetic.json

# Base vazia: dados oficiais e Booksy continuam pendentes
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator

# Compatibilidade com fixture plana da Fase 1; apenas synthetic=true
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --knowledge fixtures/salon.synthetic.json
```

Não precisa iniciar o Uvicorn para usar a CLI. Sem configuração de armazenamento,
os dados ficam em memória e são descartados ao sair. Com SQLite, permanecem no
arquivo configurado. Use apenas identificadores e textos fictícios.

```text
/start cliente-a
/send e1 Quero informações sobre corte.
/send e2 E quanto custa?
/start cliente-b
/send e1 E quanto custa?
/use cliente-a
/send e3 Quero falar com uma pessoa.
/operator assume
/operator reply Resposta humana fictícia.
/operator resolve
/operator resume
/state
/history
/audit
/provenance
/close
/reset CONFIRM_SYNTHETIC_RESET
/quit
```

`/help` lista comandos. `/send EVENTO TEXTO` é a única entrada de cliente.
Escrever `/operator resume` no texto de `/send` não executa ação administrativa.
O operador simulado é `operador-local`; acesso ao terminal é a fronteira de
confiança. Não há autenticação ou autorização remota implementada.

Estados: `AUTO -> WAITING_HUMAN -> HUMAN_ACTIVE -> RESOLVED -> AUTO`.
`WAITING_HUMAN` também pode ir para `RESOLVED`. **RESOLVED permanece pausado** até
`/operator resume`. Mensagens continuam no histórico durante a pausa. O pedido
de humano gera uma única confirmação na transição, sem notificar uma pessoa real.
`BOT_ACTIVE` e `PENDING_HUMAN` são aliases dos dois primeiros estados.

## Persistência sintética local — Fase 3

O SQLite usa a biblioteca padrão Python, sem SQLAlchemy/Alembic ou novos pacotes.
Requer Linux, SQLite com tabelas STRICT/JSON e volume local com locks/WAL e
permissão efetiva `0600`. **O volume deste repositório é vfat/FAT32
(`fmask=0022`): não guarda permissões POSIX, então todo arquivo fica `0644` e o
adaptador recusa esse armazenamento** (desde a Fase 4, antes de criar qualquer
arquivo). `/tmp` neste host é tmpfs: sobrevive ao reinício do processo, mas **não**
ao reinício do sistema. Para um ensaio local durável, use um diretório `0700` no
`$HOME` (ext4), mantendo o código no volume do projeto. Diagnóstico completo em
[`INTEGRACAO_META_FASE4.md`](docs/INTEGRACAO_META_FASE4.md#8-armazenamento-diagnóstico-e-decisão).

```sh
# Escolha um caminho novo; não sobrescreva bancos existentes.
install -d -m 700 "$HOME/.local/share/rocha-reis-agent"
RR_SYNTHETIC_DB="$HOME/.local/share/rocha-reis-agent/dev.synthetic.sqlite3"
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture --database "$RR_SYNTHETIC_DB"
```

Os roteiros [inicial](examples/phase3.start.demo.txt) e
[retomada](examples/phase3.resume.demo.txt) permitem repetir a demonstração em
dois processos, redirecionando cada arquivo para a CLI com o mesmo banco.

Primeira execução:

```text
/start cliente-a
/send servico Informações sobre corte
/send humano Quero um humano
/state
/quit
```

Execute novamente o mesmo comando e caminho:

```text
/use cliente-a
/state
/history
/send pausada E quanto custa?
/send humano Quero um humano
/operator assume
/operator resolve
/operator resume
/send preco E quanto custa?
/provenance
/storage-audit
/quit
```

O estado recuperado é `WAITING_HUMAN`; a mensagem `pausada` não recebe resposta
automática e `humano` é duplicata. Somente após resolução e retomada explícitas,
a pergunta `preco` reutiliza o contexto de corte e responde com preço fictício.
Reenviar `preco` após outro reinício também é duplicata, sem nova resposta.

Alternativa por configuração (para CLI ou fábrica FastAPI):

```sh
STORAGE_BACKEND=sqlite STORAGE_PATH="$HOME/.local/share/rocha-reis-agent/dev.synthetic.sqlite3" /tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture
```

A fábrica HTTP inicializa o repositório configurado em
`app.state.conversation_repository`. Na Fase 3 ela expunha somente `/health`; o
webhook, a inbox/outbox e o worker vieram na Fase 4 e o painel na Fase 7. `/health`
permanece liveness; não consulta o banco a cada requisição nem é readiness.

Cada arquivo é marcado como sintético; bancos desconhecidos e schemas futuros
são recusados. Migrações SQL versionadas são aplicadas automaticamente em transação
na abertura; não há passo manual. O teste de evolução 1 → 2 preserva a auditoria.
Na primeira composição do agente, um hash vincula o catálogo ao arquivo. Reinícios
exigem o mesmo catálogo; não altere a fixture durante um ensaio persistente. Para
outro catálogo, escolha um novo banco sintético. O JSON versionado permanece fonte
de conhecimento; autoria em memória não é persistência de alterações no catálogo.

O commit guarda evento/resultados, contexto, mensagens, estado e auditoria juntos.
A geração ocorre fora de transação SQL. Versão vencida provoca nova leitura, até
três tentativas totais. SQLite serializa commits; a ordem garantida é a dos commits,
sem promessa de FIFO por chegada ou timestamp. Esgotamento de conflitos/bloqueios
retorna erro controlado e exige reenviar o mesmo evento; não descarta silenciosamente.
Não há garantia de entrega de resposta ao terminal após o commit, nem atomicidade
com APIs externas. O canal WhatsApp (Fase 4) acrescenta a outbox transacional
descrita abaixo.

Configurações adicionais (todos os prazos são de teste, sem presunção legal):

| Variável | Padrão | Efeito |
|---|---|---|
| `STORAGE_BACKEND` | `memory` | `memory` ou `sqlite`; sem fallback automático |
| `STORAGE_PATH` | Ausente | Obrigatório no SQLite; sufixo `.synthetic.sqlite3` |
| `STORAGE_BUSY_TIMEOUT_MS` | `1000` | 1 a 5000 ms por bloqueio SQLite/inicialização |
| `STORAGE_MAX_BYTES` | `67108864` | 1 MiB a 1 GiB; limite de páginas e admissão de escrita |
| `STORAGE_RETENTION_DAYS` | `7` | 1 a 365; conteúdo de conversas sem atualização anterior ao corte |
| `STORAGE_AUDIT_DAYS` | `30` | 1 a 365; auditoria antiga podada nas próximas operações auditadas |
| `STORAGE_AUDIT_LIMIT` | `10000` | 1 a 100000 registros técnicos de auditoria por arquivo |

Histórico de IA (`AI_HISTORY_LIMIT`, padrão 4 entradas relacionadas) é distinto
do histórico persistido (`SIM_MAX_HISTORY_ENTRIES`, padrão 100) e da retenção por
data. O histórico antigo é podado ao salvar; resultados de eventos mantêm a
resposta autorizada até a exclusão do conteúdo. Eventos/tombstones não expiram:
isso impede reprocessamento após limpeza. Limites de conversas/eventos por arquivo
recusam novas entradas em vez de abrir espaço removendo idempotência. WAL pode
usar espaço transitório adicional; o limite não é uma quota absoluta do volume.

Comandos destrutivos exigem confirmação explícita e banco identificado:

```text
/reset CONFIRM_SYNTHETIC_RESET
/delete DELETE_SYNTHETIC:cliente-a
/purge PURGE_SYNTHETIC
```

Reset limpa histórico/contexto da sessão; no SQLite preserva eventos/resultados.
A exclusão e o purge removem histórico/transições/contexto e texto das respostas
armazenadas, desativando a conversa. Preservam IDs fictícios, hashes de mensagens,
metadados mínimos dos eventos e auditoria limitada. A conversa excluída não pode
ser reaberta; eventos conhecidos são reconhecidos sem resposta. Não é exclusão
integral/anônima de dados reais ou implementação de obrigações da LGPD.

`/purge` seleciona por última atualização e só roda quando solicitado; não há
agendador ou limpeza automática de conteúdo ao iniciar. Auditoria é limitada por
data/quantidade e não armazena textos/prompts; logs são eventos estáticos.
`secure_delete` está ativo, mas não promete apagamento forense, de backups/WAL ou
do dispositivo. Não há criptografia em repouso, autenticação remota ou backup.
Permissões e acesso ao terminal continuam sendo a fronteira local de confiança.

## Canal WhatsApp offline — Fase 4

Webhook `GET/POST /webhooks/whatsapp` (montado somente com `WHATSAPP_ENABLED=true`),
verificação HMAC `X-Hub-Signature-256`, inbox durável com deduplicação, worker que
usa o núcleo existente e outbox transacional com envio **simulado**. Nada sai da
máquina. Detalhes, garantias e limites: [`INTEGRACAO_META_FASE4.md`](docs/INTEGRACAO_META_FASE4.md).

```sh
install -d -m 700 "$HOME/.local/share/rocha-reis-agent"
export STORAGE_BACKEND=sqlite STORAGE_PATH="$HOME/.local/share/rocha-reis-agent/canal.synthetic.sqlite3"
export WHATSAPP_ENABLED=true WHATSAPP_PHONE_NUMBER_ID=100000000000001 WHATSAPP_BUSINESS_ACCOUNT_ID=200000000000002
# Três segredos locais distintos, gerados agora e não gravados em arquivo:
for name in WHATSAPP_VERIFY_TOKEN WHATSAPP_APP_SECRET WHATSAPP_CONVERSATION_KEY; do
  export "$name=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
done
CLI="/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.channels.local_cli"
$CLI post fixtures/meta/text_service.synthetic.json   # assina e entrega em processo (sem socket)
$CLI work --fixture                                    # inbox → núcleo → outbox → envio fake
$CLI post fixtures/meta/text_price_followup.synthetic.json && $CLI work --fixture
$CLI work --fixture --behavior timeout                 # simula envio incerto (UNKNOWN, sem reenvio)
$CLI status                                            # contagens por estado, sem conteúdo
```

Para servir o webhook localmente, use somente `--host 127.0.0.1` e
`--no-access-log`; não exponha a porta publicamente nesta fase.

**Fase 5:** `channels/graph_transport.py` implementa o envio real pela Graph API
(versão explícita, Bearer, classificação de erros), mas `WHATSAPP_TRANSPORT=graph` é
recusado enquanto `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED=False` em `config.py`; a CLI
local nunca usa o transporte real. A janela de atendimento é reavaliada antes de
cada tentativa (`channels/send_policy.py`). O limite de conversas vale para as
**ativas**: ao atingi-lo, a conversa ociosa elegível é arquivada (sem apagar dados), e
`/purge PURGE_SYNTHETIC` anonimiza conversas inativas sem bloquear o cliente.
Detalhes em [`STATUS_FASE_5.md`](docs/STATUS_FASE_5.md).

**Fase 6A (homologação controlada, offline):** `APP_ENV=homologation` com banco e
autorização próprios, allowlist, limites, interrupção emergencial (`local_cli halt`,
`resume-dispatch`), `metrics`, `trace` e `homolog-check`. Guia e runbook da 6B em
[`HOMOLOGACAO_META_FASE6.md`](docs/HOMOLOGACAO_META_FASE6.md). Requisitos da operação
humana em [`OPERACAO_HUMANA_REQUISITOS.md`](docs/OPERACAO_HUMANA_REQUISITOS.md) e
pendências de produção em [`CHECKLIST_PRODUCAO.md`](docs/CHECKLIST_PRODUCAO.md).
**Nenhum teste real com a Meta foi feito (Fase 6B bloqueada).**

## Painel de atendimento humano — Fase 7 (somente local)

App web separada do webhook para a equipe: login (Argon2id), papéis ADMIN/ATENDENTE,
fila, assumir, transferir, responder (pela outbox, envio simulado), finalizar,
retomar a automação, auditoria e atualização ao vivo (SSE). Roteiro completo,
APIs e limitações em [`STATUS_FASE_7.md`](docs/STATUS_FASE_7.md).

```sh
# Variáveis: ver bloco PANEL_* do .env.example e o roteiro em STATUS_FASE_7.md §D
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.panel.admin_cli create-admin --login dona --name "Responsável"
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.panel.admin_cli seed-demo
/tmp/rocha-reis-agent-venv/bin/python -m uvicorn rocha_reis_agent.panel.app:create_panel_app --factory \
    --host 127.0.0.1 --port 8000 --no-access-log --timeout-graceful-shutdown 5
# http://127.0.0.1:8000/panel/  — nunca expor em rede nesta fase
```

Testes de navegador (Chromium headless) ficam em `tests_e2e/`, com venv próprio
descrito em `requirements-e2e.txt`.

## IA generativa, conhecimento aprovado e avaliações — Fase 8 (somente local)

- **Contrato `ai-response-v2`:** o modelo só **classifica** e pode propor uma abertura
  curta; todos os fatos (preços, horários, endereço, link Booksy) são escritos pelo backend
  a partir de registros aprovados. Na Fase 8 o provedor real era um adaptador Anthropic,
  **substituído pelo Google Gemini na Fase 9B** (ver abaixo e
  [`IA_GENERATIVA.md`](docs/IA_GENERATIVA.md)).
- **Conhecimento no banco** (`KNOWLEDGE_SOURCE=database`): revisões imutáveis,
  rascunho → aprovação → revogação pela seção **Conhecimento** do painel (somente
  ADMIN), histórico, pendências e prévia. Um rascunho novo suspende o dado anterior.
- **Avaliações:** cenários sintéticos A–T contra o núcleo real, com oráculo
  independente e um "modelo hostil" simulado.

```sh
PY=/tmp/rocha-reis-agent-venv/bin/python
# Base de conhecimento no banco (variáveis do painel + KNOWLEDGE_SOURCE=database)
$PY -m rocha_reis_agent.panel.admin_cli knowledge-init --scope synthetic --confirm KNOWLEDGE_SCOPE:SYNTHETIC
$PY -m rocha_reis_agent.panel.admin_cli knowledge-import-fixture
# Avaliações offline (sem rede): fake | hostile | gemini-simulated; prompt v1 … v4
$PY -m rocha_reis_agent.evaluation --provider fake --prompt v1
$PY -m rocha_reis_agent.evaluation --provider hostile --prompt v2 --output /tmp/avaliacao.json
```

Resultados de referência em `evals/results/`; roteiro completo e métricas em
[`STATUS_FASE_8.md`](docs/STATUS_FASE_8.md). Resultados com provedores simulados
medem o backend; **não validam a qualidade de um modelo generativo real**.

## Homologação offline da IA — Fase 9A (sem chamadas pagas)

- **Registro verificado de modelos** `ai/models.json`: na 9A continha Claude; desde a 9B
  contém só modelos **Gemini** estáveis (ver seção seguinte).
- **Orçamento verificável:** reserva de pior caso antes de cada chamada, tetos diário e por
  execução (`AI_MAX_REQUESTS_PER_RUN`, `AI_MAX_COST_USD_PER_RUN`), timeout/rede/5xx
  contabilizados pelo pior caso.
- **Qualidade:** os 5 casos difíceis da Fase 8 foram corrigidos (correção ortográfica em
  vocabulário fechado, meios de pagamento, intenção `promotion`, pergunta elíptica que herda
  o preço). Quatro novos casos difíceis estão declarados.
- **Prompt `v3`** registrado e inativo (`v1` continua ativo).
- **Avaliação v2:** 120 cenários (77 originais intactos), métricas com denominadores,
  precisão/recall de encaminhamento humano, critérios pré-registrados em
  [`evals/criteria.json`](evals/criteria.json), comparação entre execuções.

Resultados e limitações em [`STATUS_FASE_9.md`](docs/STATUS_FASE_9.md) (parte A).

## Google Gemini — Fase 9B (provedor oficial; adaptador bloqueado, sem chamadas)

- **`ai/gemini_provider.py`** (SDK oficial `google-genai` 3.6.1): `generate_content` com
  saída estruturada (`response_json_schema` = contrato v2), raciocínio por `thinking_level`,
  prazo total dividido entre tentativas, bloqueios de segurança, truncamento e modelo
  efetivo inesperado tratados como falha segura. **Bloqueado** por
  `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED=False` em `config.py`; `GEMINI_API_KEY` sozinha
  nunca ativa nada, e o cliente só é criado com chave explícita (o SDK leria a variável).
- **Modelos** ([`ai/models.json`](rocha_reis_agent/ai/models.json)): `gemini-3.1-flash-lite`,
  `gemini-3.5-flash-lite`, `gemini-3.8-flash` (estáveis, tarifas pagas verificadas em
  20/11/2026). **Sem modelo padrão**: `AI_MODEL` é obrigatório com o provedor real.
  Comparação em [`AVALIACAO_MODELOS_IA.md`](docs/AVALIACAO_MODELOS_IA.md).
- **Consumo:** `ai_usage` registra modelo pedido e efetivo, tokens de raciocínio e de cache
  (migração 008); custo estimado pela tarifa do modelo, com raciocínio cobrado como saída.
- **Prompt `v4`** (Gemini) registrado e inativo; temas de saúde escalam para pessoa.

```sh
PY=/tmp/rocha-reis-agent-venv/bin/python
$PY -m rocha_reis_agent.evaluation --provider gemini-simulated --model gemini-3.1-flash-lite --prompt v4 --save
$PY -m rocha_reis_agent.evaluation compare evals/results/phase9b-fake-v4.json evals/results/phase9b-hostile-v4.json
$PY -m rocha_reis_agent.evaluation --provider gemini --model gemini-3.1-flash-lite --prompt v4   # RECUSADO (trava)
```

Relatório em [`STATUS_FASE_9.md`](docs/STATUS_FASE_9.md) (parte B); homologação real
(não autorizada) em [`HOMOLOGACAO_IA_FASE9.md`](docs/HOMOLOGACAO_IA_FASE9.md).

## Homologação real do Gemini — Fase 9C-A (preparada; 9C-B bloqueada)

Experimento pré-registrado por hash ([`evals/experiment_9c.json`](evals/experiment_9c.json)),
129 cenários (9 prioritários novos), etapas `smoke/pilot/e2e/full/repeat`, livro-razão de
consumo persistente, arquivo de parada, fluxo de ponta a ponta do canal com
`FakeWhatsAppTransport`, orçamento calculado a partir dos pedidos reais e patch de ativação
revisável (não aplicado). Tudo offline; **nenhuma chamada ao Gemini**.

```sh
PY=/tmp/rocha-reis-agent-venv/bin/python
$PY -m rocha_reis_agent.evaluation --provider gemini-simulated --prompt v4 --stage pilot
$PY -m rocha_reis_agent.evaluation --provider gemini-simulated --prompt v4 --stage e2e
$PY -m rocha_reis_agent.evaluation plan                       # orçamento proposto (estimativa)
```

Relatório em [`STATUS_FASE_9C.md`](docs/STATUS_FASE_9C.md). **Fase 9C-B: preparada, não autorizada,
não executada** — guarda schema 3, execução em duas etapas e métricas de encaminhamento por
causa; ver [`STATUS_FASE_9C_B.md`](docs/STATUS_FASE_9C_B.md).

## Consolidação operacional — Fase 10 (somente local, dados fictícios)

Painel com visão geral "precisa de atenção", avisos internos (sem conteúdo, sem
duplicatas, som opcional), urgência configurável, histórico paginado, recuperação de sessão
com rascunho e visão de Sistema; backup criptografado com restauração isolada; demonstração
isolada e iniciador local (sempre `127.0.0.1`).

```sh
sh iniciar.sh                       # cria a demonstração fictícia na 1ª vez e abre o painel
PY=/tmp/rocha-reis-agent-venv/bin/python
$PY -m rocha_reis_agent.ops chave --arquivo ~/.config/rocha-reis-agent/backup.key
$PY -m rocha_reis_agent.ops backup --banco BANCO.synthetic.sqlite3 --destino ~/rr-backups --chave ~/.config/rocha-reis-agent/backup.key
$PY -m rocha_reis_agent.ops verificar ~/rr-backups/ARQUIVO.rrbackup --chave ~/.config/rocha-reis-agent/backup.key
$PY -m rocha_reis_agent.ops restaurar ~/rr-backups/ARQUIVO.rrbackup --para /caminho/novo.synthetic.sqlite3 --chave ...
$PY -m rocha_reis_agent.ops desempenho --conversas 1000 --mensagens 400
```

O volume do repositório não guarda permissão de execução: use `sh iniciar.sh`. Os
**backups são cifrados; o banco ativo não é**. Relatório em
[`STATUS_FASE_10.md`](docs/STATUS_FASE_10.md); manual da equipe em
[`MANUAL_PAINEL.md`](docs/MANUAL_PAINEL.md).

## Finalização funcional — Fase 11A (11B aguardando o salão)

```sh
sh iniciar.sh --nova-demo                         # demonstração limpa (apaga só a demonstração)
sh iniciar.sh --mensagem "Quero falar com uma pessoa"   # cliente FICTÍCIO escreve (painel aberto)
```

Para o salão: formulário de dados oficiais em [`COLETA_DADOS_OFICIAIS.md`](docs/COLETA_DADOS_OFICIAIS.md)
e roteiro da validação com a equipe em [`PLANO_VALIDACAO_SALAO.md`](docs/PLANO_VALIDACAO_SALAO.md).
Relatório (cenários de ponta a ponta, correções, políticas pendentes e análise de
pré-produção) em [`STATUS_FASE_11.md`](docs/STATUS_FASE_11.md).

## Pré-produção preparada — Fase 12 (somente local e sintética)

```sh
PY=/tmp/rocha-reis-agent-venv/bin/python
$PY -m rocha_reis_agent.ops preprod            # perfil de PRÉ-PRODUÇÃO sintético (exige volume cifrado verificado)
$PY -m rocha_reis_agent.ops ambiente --perfil ~/.local/share/rocha-reis-agent/preprod   # diagnóstico
$PY -m rocha_reis_agent.ops metricas --perfil ~/.local/share/rocha-reis-agent/demo     # indicadores sem dados pessoais
$PY -m rocha_reis_agent.ops reter ~/rr-backups --manter 14 --dias 30                   # plano de retenção (não apaga)
```

Conhecimento estruturado (categorias, profissionais, duração estimada, tipos de preço) pelo
painel, com aprovação. Dados reais continuam **bloqueados** (quarta trava de código).
Relatório em [`STATUS_FASE_12.md`](docs/STATUS_FASE_12.md); arquitetura em
[`ARQUITETURA_PRE_PRODUCAO.md`](docs/ARQUITETURA_PRE_PRODUCAO.md); proteção de dados em
[`SEGURANCA_ARMAZENAMENTO.md`](docs/SEGURANCA_ARMAZENAMENTO.md).

## Segurança avançada — Fase 13 (somente local)

```sh
PY=/tmp/rocha-reis-agent-venv/bin/python
$PY -m rocha_reis_agent.ops backup-agendado --perfil ~/.local/share/rocha-reis-agent/demo   # uma rodada
$PY -m rocha_reis_agent.ops teste-restauracao --perfil ~/.local/share/rocha-reis-agent/demo
$PY -m rocha_reis_agent.ops ensaio-recuperacao --perfil ~/.local/share/rocha-reis-agent/demo --destino /tmp/ensaio-vazio
$PY -m rocha_reis_agent.ops mfa-rotacionar --perfil ~/.local/share/rocha-reis-agent/demo
```

Verificação em duas etapas (TOTP) no painel em **Minha conta**; obrigatória em pré-produção, que
sobe em HTTPS com certificado de **desenvolvimento**. Referências de implantação (não instaladas)
em [`deploy/`](deploy/README.md). Documentos: [`STATUS_FASE_13.md`](docs/STATUS_FASE_13.md),
[`SEGURANCA_AUTENTICACAO.md`](docs/SEGURANCA_AUTENTICACAO.md),
[`OPERACAO_BACKUP_AUTOMATICO.md`](docs/OPERACAO_BACKUP_AUTOMATICO.md),
[`PLANO_RECUPERACAO_DESASTRES.md`](docs/PLANO_RECUPERACAO_DESASTRES.md).

## Homologação com o salão — Fase 15

Comece por [`PACOTE_HOMOLOGACAO_SALAO.md`](docs/PACOTE_HOMOLOGACAO_SALAO.md). Antes de cada sessão:
`python -m rocha_reis_agent.ops verificar-rc`; registro dos resultados com `ops registro-11b` e
`ops conferir-registro` (planilha fora do repositório). Situação técnica em
[`RELATORIO_PRONTIDAO_RC1.md`](docs/RELATORIO_PRONTIDAO_RC1.md).

## RC1 Offline — Fase 14 (candidata à homologação com o salão; não é produção)

```sh
sh iniciar.sh --nova-demo                                   # demonstração limpa e isolada
python -m rocha_reis_agent.ops roteiro                      # 13 jornadas automáticas num perfil novo
python -m rocha_reis_agent.ops versao                       # identificação da RC1 (commit base, impressão digital)
```

Documentos: [`RELEASE_CANDIDATE_RC1.md`](docs/RELEASE_CANDIDATE_RC1.md),
[`ROTEIRO_DEMONSTRACAO_SALAO.md`](docs/ROTEIRO_DEMONSTRACAO_SALAO.md),
[`CHECKLIST_HOMOLOGACAO_11B.md`](docs/CHECKLIST_HOMOLOGACAO_11B.md),
[`TREINAMENTO_RAPIDO.md`](docs/TREINAMENTO_RAPIDO.md),
[`DECISOES_PENDENTES_SALAO.md`](docs/DECISOES_PENDENTES_SALAO.md),
[`STATUS_FASE_14.md`](docs/STATUS_FASE_14.md).

## Conhecimento e limites determinísticos

O formato atual está em [salon.versioned.synthetic.json](fixtures/salon.versioned.synthetic.json):
`schema_version=2`, escopo `SYNTHETIC` e revisões imutáveis com ID, categoria em
`data.kind`, conteúdo tipado, origem, data com fuso, versão, revisão anterior,
aprovação e responsável pela revisão. Preço tem aprovação separada do serviço.
Política geral e política de cancelamento também são fatos distintos.

Estados: `DRAFT`, `APPROVED`, `REJECTED`, `ARCHIVED`. Somente a última revisão
`APPROVED` de cada ID pode sustentar resposta. Nova revisão pendente desativa a
anterior até aprovação; não existe retorno silencioso a uma versão antiga.
Conflitos de versão, lacunas no histórico e dois registros ativos para o mesmo
dado são rejeitados. No modo `KNOWLEDGE_SOURCE=file`, `append_revision` é autoria
local confiável, sem poder de escrita atribuído ao provedor; no modo `database`
(Fase 8), revisões são criadas e aprovadas pelo painel, somente por ADMIN.

[knowledge/official.json](knowledge/official.json) está vazio; as pendências sem
valores estão em [knowledge/pending.json](knowledge/pending.json). A CLI recusa
importar catálogo oficial nesta fase. Nenhum dado real foi presumido.

`--fixture` carrega o catálogo v2. O formato plano abaixo permanece compatível
somente como fixture: sua aprovação é explicitamente sintética e nunca oficial.

A fixture [salon.synthetic.json](fixtures/salon.synthetic.json) documenta o formato:
`schema_version`, `synthetic`, `name`, `address`, `opening_hours`, `services`,
`professionals`, `policies`, `booksy_url` e `faqs`. Serviços têm `id`, `name`,
`aliases`, `description`, preço decimal opcional e `price_note`. FAQs contêm
perguntas alternativas e resposta. Campos desconhecidos, dados inválidos e arquivos
acima de 100 KB são rejeitados. A CLI recusa importar base não sintética nesta fase.

O detector reconhece saudação, serviço, preço, funcionamento, localização,
profissionais, agendamento, humano, política (inclui meios de pagamento), promoção
(sempre "não tenho informação confirmada"), FAQ e mensagem desconhecida. Desde a Fase 9A,
tolera um erro de digitação em palavras-chave e nomes do catálogo (vocabulário fechado,
serviço inferido declarado ao cliente) e trata perguntas elípticas ("e a escova?").
Suporta múltiplas intenções; pedido de humano suspende as demais respostas e
agendamento tem prioridade. Até três intenções de conteúdo são respondidas por
mensagem, com aviso para detalhar as restantes. O detector é determinístico; a IA
generativa da Fase 8 só classifica dentro desse escopo e está bloqueada.

O contexto guarda serviços por conversa. Perguntas curtas como “E quanto custa?”
reutilizam um único serviço recente. Contexto ausente, ambíguo ou vencido exige
esclarecimento. Serviço desconhecido explícito não herda preço do anterior.
Não há compreensão geral de linguagem, calendário ou disponibilidade. Também
existe intenção específica de política de cancelamento, sem reaproveitar uma
política genérica quando a informação específica estiver ausente.

## Contrato de IA, validação e prompts

O núcleo recupera fatos aprovados relevantes à consulta, separa instruções
confiáveis de mensagem/histórico não confiáveis e chama o provedor uma única vez.
`AIRequest` não inclui IDs de cliente/evento, mensagens do operador ou metadados
de revisão. O histórico relevante vem só da mesma conversa, no máximo 4 entradas
de 500 caracteres por padrão. Fatos têm autoridade de conteúdo, nunca administrativa.

`AIResponse` propõe intenções, serviços, referências com versão, resposta
candidata, esclarecimento e eventual ajuda humana. Campos administrativos e
campos inesperados são rejeitados. A resposta final é reconstruída pelo backend
com campos aprovados e templates; a candidata precisa corresponder exatamente.
Citar um fato correto não autoriza inventar preço, horário ou confirmação.
Referências fora do snapshot recuperado, desatualizadas ou não aprovadas falham.
Uma mudança de aprovação durante a consulta também invalida a candidata.

Essa estratégia restringe texto livre e mantém a vinculação de serviços/contexto
sob extração determinística. Um modelo real futuro precisará respeitar esse plano
estruturado e passar por avaliação própria de qualidade e segurança. Testes com
o fake não validam o comportamento de um LLM.

Pedido explícito de humano não depende do provedor. Estados pausados não chamam
IA. Uma sugestão válida pode levar somente ao pedido de ajuda permitido pelo
backend; assumir, resolver e retomar continuam administrativos.

Em falha estrutural/factual, comunicação ou timeout simulado, a resposta é segura,
o contexto anterior é preservado e a conversa vai para espera humana simulada.
Não há retry automático. O timeout é parte do contrato do adaptador: não existe
preempção de código bloqueado ou cliente de rede real nesta fase.

[prompts/registry.json](prompts/registry.json) indica a versão ativa; cada arquivo,
como [v1.json](prompts/v1.json), contém objetivo, instruções, contrato e mudanças.
[v2.json](prompts/v2.json) (contrato `ai-response-v2`, Fase 8) e [v3.json](prompts/v3.json)
(Fase 9A) já estão registradas, porém inativas; selecione com `AI_PROMPT_VERSION=v2` ou `v3`. Novas versões seguem o mesmo
padrão (`vN.json` registrado em `versions`), preservando as anteriores. Não editar uma versão publicada
para mudar seu significado. Mensagens de cliente não escolhem versão ou instruções.
`/provenance` mostra referências e versão aplicada, sem imprimir o prompt.

O link só é emitido se configurado, aprovado e válido por formato/domínio HTTPS. São
aceitos `booksy.com` e subdomínios; fixtures também aceitam `example.invalid` e
subdomínios. Isso não verifica existência ou titularidade do perfil. Não há acesso
ao Booksy. Sem link válido, informa indisponibilidade e oferece pedido de humano.

Deduplicação considera conversa + sessão + ID do evento, com hash do texto.
Mesmo ID e texto não repetem resposta/histórico; mesmo ID com outro texto é erro.
Texto igual com novo ID é nova mensagem. Ao atingir o limite de eventos, novos
eventos são recusados sem remover IDs antigos. No modo em memória, `/reset`
descarta também deduplicação e sair perde todos os dados. No modo SQLite,
chaves e resultados persistem entre reinícios e resets; IDs não são reutilizáveis
na mesma conversa durante a vida do arquivo. Duplicatas retornam metadados do
resultado original com `duplicate=true`, sem repetir a resposta.

O adaptador em memória serializa operações no processo; SQLite usa snapshots,
versões e transações curtas entre processos. Histórico e transições são limitados
em entradas; contexto, em turnos automáticos. Conversas fechadas ocupam vaga e
podem ser reutilizadas com reset confirmado. Conversas excluídas no SQLite
permanecem desativadas para impedir reprocessamento.

## Configuração

Não é preciso criar `.env`. Para personalizar a configuração local:

```sh
cp .env.example .env
```

| Variável | Padrão | Valores aceitos |
|---|---|---|
| `APP_ENV` | `local` | `local`, `test`, `homologation` (Fase 6A; exige banco e autorização próprios) |
| `APP_NAME` | `rocha-reis-agent` | Identificador técnico de 1 a 64 caracteres; começa por letra minúscula e contém apenas letras minúsculas, números e hífen |
| `SIM_MAX_MESSAGE_CHARS` | `2000` | 1 a 2000 caracteres antes de normalizar espaços |
| `SIM_MAX_HISTORY_ENTRIES` | `100` | 1 a 1000 entradas de histórico e, separadamente, de transições de estado |
| `SIM_MAX_EVENTS_PER_SESSION` | `1000` | 1 a 10000 eventos; no SQLite o limite é por conversa, incluindo resets |
| `SIM_MAX_CONVERSATIONS` | `100` | 1 a 1000 conversas por processo em memória, ou por arquivo SQLite |
| `SIM_CONTEXT_TURNS` | `5` | 1 a 100 turnos automáticos; pausa humana preserva o contexto |
| `AI_PROVIDER` | `fake` | `fake`; `gemini` é recusado enquanto `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED` for `False` |
| `AI_MODEL` | Ausente | Obrigatório com `gemini`: ID estável do registro `ai/models.json` (sem padrão fixo) |
| `GEMINI_API_KEY` | Ausente | Só com `AI_PROVIDER=gemini` e trava aberta; com o fake, a configuração é recusada |
| `AI_THINKING_LEVEL`, `AI_MAX_INPUT_TOKENS` | padrão do modelo, `4000` | Nível de raciocínio aceito pelo modelo; tokens de entrada estimados recusados antes da chamada |
| `AI_MAX_*`, `AI_MAX_RETRIES` | ver `.env.example` | Somente com o provedor real (bloqueado); limites de tokens, tentativas, concorrência, requisições e custo estimado por dia |
| `KNOWLEDGE_SOURCE` | `file` | `file` (fixtures) ou `database` (gestão pelo painel; exige SQLite) |
| `KNOWLEDGE_REQUIRE_DISTINCT_APPROVER` | `false` | `true` exige outro administrador para aprovar |
| `AI_PROMPT_VERSION` | Ausente | Versão ativa do registro; pode selecionar uma versão `vN` registrada |
| `AI_HISTORY_LIMIT` | `4` | 0 a 10 mensagens relevantes, até 500 caracteres cada |
| `AI_TIMEOUT_SECONDS` | `2` | Maior que 0 e até 30; prazo TOTAL do provedor real (dividido entre as tentativas); timeout simulado no fake |
| `AI_MAX_REQUESTS_PER_RUN`, `AI_MAX_COST_USD_PER_RUN` | Ausente | Fase 9A: tetos por execução do processo, além dos diários (só com o provedor real, bloqueado) |

O `.env` é lido a partir do diretório atual em UTF-8. Variáveis do processo têm
precedência. Chaves desconhecidas no `.env` são rejeitadas para detectar erros de
digitação; outras variáveis do sistema não são consumidas. Uma configuração
inválida impede a inicialização com uma mensagem sem valores privados. A CLI usa
os limites `SIM_`; instâncias de teste também podem receber `SimulationLimits`.

O `.gitignore` exclui `.env`, logs, ambientes e arquivos locais de banco. As
variáveis `WHATSAPP_*`/`CHANNEL_*` da Fase 4 estão descritas no `.env.example` e em
[`INTEGRACAO_META_FASE4.md`](docs/INTEGRACAO_META_FASE4.md#9-configuração-envexample);
o canal fica desligado por padrão e não há campo para token de acesso real. Erros HTTP inesperados antes do início da resposta
retornam HTTP 500 genérico e registram somente `request_failed`. O comando acima
desativa logs de acesso, evitando registrar URLs e parâmetros de clientes.
**Com o canal habilitado isso é obrigatório:** a query do GET de verificação
contém o Verify Token.

## Testar

```sh
RR_VENV=/tmp/rocha-reis-agent-venv
"$RR_VENV/bin/python" -m pytest -q
```

Os testes usam `TestClient` em memória, bloqueiam conexões de rede e isolam o
ambiente e o diretório de trabalho. Cobrem saúde, configuração padrão e `.env`,
precedência, falha de inicialização, erros HTTP e ausência de valores privados
nas respostas e logs de falha. A Fase 3 testa arquivos SQLite reais, migrações,
processos independentes, interrupção abrupta, concorrência sincronizada, rollback,
retenção, isolamento e composição FastAPI. A Fase 4 (`tests/test_whatsapp_*.py`)
testa webhook assinado, normalização, inbox/outbox, incerteza de envio, leases,
concorrência, rollback e reinício real de processos, sempre com transporte fake.
A Fase 5 (`test_graph_transport.py`, `test_delivery_status_and_policy.py`,
`test_lifecycle_and_admin.py`) valida a requisição HTTP com `httpx2.MockTransport`,
classificação de erros, status de entrega, janela, retenção e controles administrativos. Os testes da Fase 1 também cobrem atendimento,
contexto, duas conversas, estados humanos, deduplicação concorrente, limites,
carga de fixtures e CLI. A Fase 2 cobre aprovação, versões, contratos, prompts,
saídas sem suporte factual, ataques, privacidade e falhas do fake. A Fase 8
(`test_ai_generative.py`, `test_knowledge_management.py`, `test_evaluation.py`,
`test_phase8_integration.py`, `tests_e2e/test_knowledge_browser.py`) cobre o adaptador
real (Gemini desde a 9B, com `httpx2.MockTransport`), travas, falhas, limites de consumo,
gestão do conhecimento, avaliações reproduzíveis e o fluxo webhook → IA → outbox.
Evidências por fase em `docs/STATUS_FASE_0.md` a [`STATUS_FASE_9.md`](docs/STATUS_FASE_9.md);
o resultado mais recente registrado (Fase 9B) está no relatório da fase.
`tests/test_phase9.py` bloqueia sockets e cobre adaptador Gemini, orçamento, registro de
modelos, guarda da avaliação real, qualidade, migração 008 e corridas; `tests/gemini_support.py`
fornece respostas REST sintéticas da Gemini API.

## Estrutura e evolução

```text
.
├── AGENTS.md · PROMPT_INICIAL_CODEX.md · README.md
├── .env.example · .gitignore · pyproject.toml
├── requirements.in · requirements.lock · requirements-e2e.txt
├── examples/            # roteiros da CLI (phase1, phase2, phase3.start, phase3.resume)
├── fixtures/
│   ├── salon.synthetic.json · salon.versioned.synthetic.json
│   ├── meta/            # webhooks Meta sintéticos (Fase 4)
│   └── homolog/         # exemplos de autorização: homologação Meta (6A) e avaliação de IA (9B)
├── knowledge/           # official.json (vazio) · pending.json
├── prompts/             # registry.json · v1.json (ativo) · v2 (Fase 8) · v3 (9A) · v4 (9B, Gemini), inativos
├── evals/               # cenários (120) e catálogo sintéticos, criteria.json, results/ de referência
├── rocha_reis_agent/
│   ├── config.py · main.py · bootstrap.py · observability.py
│   ├── intents.py · responses.py · memory.py · simulator.py
│   ├── knowledge.py · knowledge_catalog.py · knowledge_projection.py · catalog_io.py
│   ├── domain/          # contracts.py · conversations.py
│   ├── application/     # service.py · conversations.py · handoff.py · ports.py · retry.py
│   ├── ai/              # contracts · context · fake · prompts · validation · privacy
│   │                    # gemini_provider.py (BLOQUEADO, 9B) · budget.py · models.json + model_registry.py
│   ├── api/             # errors.py · health.py · whatsapp.py (webhook, Fase 4)
│   ├── channels/        # contracts · meta · service · fake_transport · local_cli
│   │                    # graph_transport.py (BLOQUEADO, Fase 5) · send_policy.py
│   │                    # homologation.py (Fase 6A)
│   ├── persistence/     # sqlite.py · sqlite_channel.py · sqlite_ai.py · sqlite_knowledge.py
│   │   └── migrations/  # 001_initial.sql … 011_panel_mfa.sql
│   ├── panel/           # painel (Fase 7) + conhecimento (Fase 8)
│   │                    # security · store · service · api · app · admin_cli · knowledge
│   │   └── static/      # index.html · styles.css · app.js
│   ├── evaluation/      # scenarios · oracle · providers · runner · __main__ · real_run (9B bloqueada)
│   └── ops/             # Fases 10–15: backup · backup_job · disaster · tls · demo · launcher · perf · environments · rehearsal · release · uat · __main__
├── tests/               # suíte pytest (test_*.py) e workers de processos reais
├── tests_e2e/           # testes de navegador (Chromium headless, Fases 7, 8 e 10)
├── iniciar.sh           # inicia o painel local (127.0.0.1) com a demonstração fictícia
└── docs/
    ├── CONTEXTO_MESTRE.md · REQUISITOS_E_ACEITE.md · ARQUITETURA_E_DECISOES.md
    ├── PLANO_DE_EXECUCAO.md · DADOS_PENDENTES.md · CHECKLIST_PRODUCAO.md
    ├── INTEGRACAO_META_FASE4.md · HOMOLOGACAO_META_FASE6.md
    ├── OPERACAO_HUMANA_REQUISITOS.md · IA_GENERATIVA.md
    ├── AVALIACAO_MODELOS_IA.md · HOMOLOGACAO_IA_FASE9.md
    └── STATUS_FASE_0.md … STATUS_FASE_9.md
```

`main.py` compõe HTTP/configuração (`/health` e webhook opcional); `panel/app.py` é a
aplicação separada do painel. `application/` gerencia atendimento, sessões e handoff.
`domain/` contém mensagens validadas, resultados e estados. `IntentDetector`,
`KnowledgeRepository` e `ConversationRepository` separam as regras dos adaptadores.
O contrato `AIProvider` tem o fake determinístico e o adaptador **Google Gemini** (bloqueado
por trava de código), que só aceita modelos estáveis do registro verificado `ai/models.json`. `persistence/` implementa SQLite sem expor SQL ao domínio.
`bootstrap.py` compõe adaptadores para CLI e aplicação HTTP.

## Dependências e atualização do lock

Execução: **FastAPI**, **Pydantic**, **Pydantic Settings**, **Uvicorn**,
**argon2-cffi** (senhas do painel, Fase 7) e **google-genai** 3.6.1 (SDK oficial da Gemini
API, Fase 9B; importado só com a trava aberta; traz google-auth, requests, websockets e
tenacity como dependências transitivas). O SDK `anthropic` (Fase 8) foi removido.
**httpx2** serve ao `TestClient`, ao adaptador Graph API e ao SDK Gemini. Testes: **pytest**; testes de navegador usam **Playwright** em venv próprio
(`requirements-e2e.txt`). `requirements.in` contém as dependências diretas;
`requirements.lock` fixa todas as versões e hashes, incluindo transitivas.
`pyproject.toml` contém somente a configuração do pytest.

Para manutenção do lock, em um ambiente separado com Python 3.13:

```sh
python3 -m venv /tmp/rocha-reis-lock-tools
/tmp/rocha-reis-lock-tools/bin/python -m pip install --index-url https://pypi.org/simple 'pip-tools==7.7.0'
/tmp/rocha-reis-lock-tools/bin/python -m piptools compile --resolver=backtracking --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --index-url https://pypi.org/simple --output-file requirements.lock requirements.in
```

Para atualizar versões já travadas, acrescente `--upgrade` ao último comando e
repita a instalação e os testes em ambiente limpo. `pip-tools` é uma ferramenta
de manutenção e não integra as dependências da aplicação.

## Fontes de verdade e próximos passos

Leia [`AGENTS.md`](AGENTS.md), [`CONTEXTO_MESTRE.md`](docs/CONTEXTO_MESTRE.md),
[`REQUISITOS_E_ACEITE.md`](docs/REQUISITOS_E_ACEITE.md),
[`ARQUITETURA_E_DECISOES.md`](docs/ARQUITETURA_E_DECISOES.md),
[`PLANO_DE_EXECUCAO.md`](docs/PLANO_DE_EXECUCAO.md) e
[`DADOS_PENDENTES.md`](docs/DADOS_PENDENTES.md).

A skill `.agents/skills/rocha-reis-development/SKILL.md` citada no `AGENTS.md`
(“quando disponível”) ainda não existe no repositório; as instruções de `AGENTS.md`
continuam aplicáveis. O projeto é versionado em Git na branch `main`.

A Fase 3 foi autorizada exclusivamente para persistência/estabilidade local e
está documentada em [STATUS_FASE_3.md](docs/STATUS_FASE_3.md).
A Fase 4 foi executada somente offline (auditoria + preparação da integração
Meta), conforme [STATUS_FASE_4.md](docs/STATUS_FASE_4.md). A Fase 5 (adaptador HTTP
bloqueado, erros, status, janela, retenção) está em [STATUS_FASE_5.md](docs/STATUS_FASE_5.md).
A Fase 6A está em [STATUS_FASE_6.md](docs/STATUS_FASE_6.md); a 6B não foi executada.
A Fase 7 (painel) está em [STATUS_FASE_7.md](docs/STATUS_FASE_7.md) e a Fase 8 (IA
generativa bloqueada, conhecimento aprovado, avaliações) em [STATUS_FASE_8.md](docs/STATUS_FASE_8.md).
A Fase 9A (homologação offline da IA) está em [STATUS_FASE_9.md](docs/STATUS_FASE_9.md); a 9B
(avaliação com IA real) não foi executada. Conexão real com a Meta, validação com o salão, dados reais e
implantação permanecem pendentes e exigem autorização específica.
