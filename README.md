# Rocha Reis Agent

Fundação e simulador local de atendimento em português para o **Salão de Beleza
Rocha Reis**, projeto da agência de **Heitor e Renan**. Fases 0 a 5 e 6A
implementadas e testadas localmente: API com `GET /health`, núcleo de conversas por CLI,
conhecimento versionado, arquitetura de IA com **FakeAIProvider determinístico**,
persistência SQLite sintética e **preparação offline do webhook oficial Meta
WhatsApp Cloud API** com inbox/outbox duráveis e transporte **fake**, e o
**adaptador HTTP Graph API** (Fase 5) implementado porém **bloqueado** por trava de
código. Nenhum modelo generativo real, conta Meta ou envio real está conectado.

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
está desativada. Não foram acrescentadas rotas HTTP de simulação ou administração.

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
`app.state.conversation_repository`; continua expondo somente `/health`. Não há
endpoint de mensagem/administração, servidor remoto, worker ou inbox. `/health`
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
com APIs externas. Nenhum canal externo existe nesta fase.

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
dado são rejeitados. `append_revision` é autoria local confiável, sem endpoint
administrativo ou poder de escrita atribuído ao provedor.

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
profissionais, agendamento, humano, política, FAQ e mensagem desconhecida.
Suporta múltiplas intenções; pedido de humano suspende as demais respostas e
agendamento tem prioridade. Até três intenções de conteúdo são respondidas por
mensagem, com aviso para detalhar as restantes. Não há IA generativa.

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
Para testar outra versão, crie `v2.json`, registre-a em `versions` e selecione
`AI_PROMPT_VERSION=v2`, preservando `v1.json`. Não editar uma versão publicada
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
| `APP_ENV` | `local` | `local`, `test` |
| `APP_NAME` | `rocha-reis-agent` | Identificador técnico de 1 a 64 caracteres; começa por letra minúscula e contém apenas letras minúsculas, números e hífen |
| `SIM_MAX_MESSAGE_CHARS` | `2000` | 1 a 2000 caracteres antes de normalizar espaços |
| `SIM_MAX_HISTORY_ENTRIES` | `100` | 1 a 1000 entradas de histórico e, separadamente, de transições de estado |
| `SIM_MAX_EVENTS_PER_SESSION` | `1000` | 1 a 10000 eventos; no SQLite o limite é por conversa, incluindo resets |
| `SIM_MAX_CONVERSATIONS` | `100` | 1 a 1000 conversas por processo em memória, ou por arquivo SQLite |
| `SIM_CONTEXT_TURNS` | `5` | 1 a 100 turnos automáticos; pausa humana preserva o contexto |
| `AI_PROVIDER` | `fake` | Somente `fake`; nenhum SDK ou credencial |
| `AI_PROMPT_VERSION` | Ausente | Versão ativa do registro; pode selecionar uma versão `vN` registrada |
| `AI_HISTORY_LIMIT` | `4` | 0 a 10 mensagens relevantes, até 500 caracteres cada |
| `AI_TIMEOUT_SECONDS` | `2` | Maior que 0 e até 30; contrato do provedor, timeout simulado no fake |

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
saídas sem suporte factual, ataques, privacidade e falhas do fake. Evidências em
[`STATUS_FASE_2.md`](docs/STATUS_FASE_2.md), [`STATUS_FASE_4.md`](docs/STATUS_FASE_4.md) e nos registros históricos
[`STATUS_FASE_1.md`](docs/STATUS_FASE_1.md) e [`STATUS_FASE_0.md`](docs/STATUS_FASE_0.md).

## Estrutura e evolução

```text
.
├── AGENTS.md
├── PROMPT_INICIAL_CODEX.md
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.in
├── requirements.lock
├── examples/
│   ├── phase1.demo.txt
│   ├── phase2.demo.txt
│   ├── phase3.start.demo.txt
│   └── phase3.resume.demo.txt
├── fixtures/
│   ├── salon.synthetic.json
│   └── salon.versioned.synthetic.json
├── knowledge/
│   ├── official.json
│   └── pending.json
├── prompts/
│   ├── registry.json
│   └── v1.json
├── rocha_reis_agent/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py
│   ├── intents.py
│   ├── knowledge.py
│   ├── knowledge_catalog.py
│   ├── knowledge_projection.py
│   ├── catalog_io.py
│   ├── memory.py
│   ├── responses.py
│   ├── simulator.py
│   ├── bootstrap.py
│   ├── persistence/
│   │   ├── __init__.py
│   │   ├── sqlite.py
│   │   ├── sqlite_channel.py        # inbox/outbox do canal (Fase 4)
│   │   └── migrations/
│   │       ├── 001_initial.sql
│   │       ├── 002_audit_correlation.sql
│   │       ├── 003_whatsapp_channel.sql
│   │       ├── 004_lifecycle_delivery.sql
│   │       └── 005_homologation_controls.sql
│   ├── channels/                    # Meta WhatsApp offline (Fase 4)
│   │   ├── __init__.py
│   │   ├── contracts.py
│   │   ├── meta.py
│   │   ├── service.py
│   │   ├── fake_transport.py
│   │   ├── graph_transport.py       # Graph API HTTP, bloqueado (Fase 5)
│   │   ├── send_policy.py           # janela de atendimento (Fase 5)
│   │   ├── homologation.py          # autorização, allowlist, limites (Fase 6A)
│   │   └── local_cli.py
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── contracts.py
│   │   ├── context.py
│   │   ├── fake.py
│   │   ├── prompts.py
│   │   └── validation.py
│   ├── application/
│   │   ├── __init__.py
│   │   ├── conversations.py
│   │   ├── handoff.py
│   │   ├── ports.py
│   │   ├── service.py
│   │   └── retry.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── errors.py
│   │   ├── health.py
│   │   └── whatsapp.py
│   └── domain/
│       ├── __init__.py
│       ├── contracts.py
│       └── conversations.py
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_health.py
│   ├── test_attendance.py
│   ├── test_booking.py
│   ├── test_handoff.py
│   ├── test_inputs_and_failures.py
│   ├── test_knowledge.py
│   ├── test_sessions.py
│   ├── test_simulator.py
│   ├── test_knowledge_versions.py
│   ├── test_ai_contracts.py
│   ├── test_ai_safety.py
│   ├── test_ai_knowledge.py
│   ├── test_ai_context_and_handoff.py
│   ├── test_sqlite_persistence.py
│   ├── test_sqlite_failures_and_concurrency.py
│   ├── test_sqlite_restart_and_retention.py
│   ├── test_sqlite_ai_regressions.py
│   └── persistence_worker.py
└── docs/
    ├── CONTEXTO_MESTRE.md
    ├── REQUISITOS_E_ACEITE.md
    ├── ARQUITETURA_E_DECISOES.md
    ├── PLANO_DE_EXECUCAO.md
    ├── DADOS_PENDENTES.md
    ├── STATUS_FASE_0.md
    ├── STATUS_FASE_1.md
    ├── STATUS_FASE_2.md
    └── STATUS_FASE_3.md
```

`main.py` compõe somente HTTP/configuração. `application/` gerencia atendimento,
sessões e handoff. `domain/` contém mensagens validadas, resultados e estados.
`IntentDetector`, `KnowledgeRepository` e `ConversationRepository` separam as
regras dos adaptadores. O motor de regras poderá ser substituído sem alterar o
gerenciamento de sessões. O contrato `AIProvider` está implementado com fake;
provedor real continua futuro. `persistence/` implementa SQLite sem expor SQL ao domínio.
`bootstrap.py` compõe adaptadores para CLI e aplicação HTTP.

## Dependências e atualização do lock

Execução: **FastAPI**, **Pydantic**, **Pydantic Settings** e **Uvicorn**. Testes:
**pytest** e **httpx2**. `requirements.in` contém as dependências diretas;
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

A skill `.agents/skills/rocha-reis-development/SKILL.md` citada no kit não foi
encontrada no projeto nem nos diretórios locais de skills pesquisados. As
instruções de `AGENTS.md` continuam aplicáveis. A pasta também não possui `.git`;
nenhum commit foi criado. Inicializar/versionar o projeto é uma pendência.

A Fase 3 foi autorizada exclusivamente para persistência/estabilidade local e
está documentada em [STATUS_FASE_3.md](docs/STATUS_FASE_3.md).
A Fase 4 foi executada somente offline (auditoria + preparação da integração
Meta), conforme [STATUS_FASE_4.md](docs/STATUS_FASE_4.md). A Fase 5 (adaptador HTTP
bloqueado, erros, status, janela, retenção) está em [STATUS_FASE_5.md](docs/STATUS_FASE_5.md).
A Fase 6A está em [STATUS_FASE_6.md](docs/STATUS_FASE_6.md); a 6B não foi executada. Conexão real com a Meta, validação com o salão, dados reais e
implantação permanecem pendentes e exigem autorização específica.
