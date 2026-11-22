# Arquitetura de referência e decisões

**Observação:** esta é a arquitetura **proposta**, não um inventário de componentes implementados.

**Fundação implementada (Fase 0, 19/11/2026):** pacote `rocha_reis_agent`, fábrica
FastAPI, configuração tipada e camada HTTP com `/health` e erros básicos seguros.
`domain/contracts.py` declara apenas mensagem interna e nomes dos estados; não
implementa atendimento ou transferência humana. Evidências em `STATUS_FASE_0.md`.
**Núcleo implementado (Fase 1, 19/11/2026):** CLI, gerência de conversas e contexto,
detector de regras substituível, respostas determinísticas, conhecimento sintético,
estados de handoff e repositórios em memória. `domain/contracts.py` agora valida
entradas; as transições são responsabilidade de `HumanHandoffManager`. Evidências
em `STATUS_FASE_1.md`. IA real, gateways externos e persistência durável continuam propostos.

**Fase 2 implementada (19/11/2026):** catálogo de revisões/aprovação, projeção
autorizada, contratos `AIProvider`/`AIRequest`/`AIResponse`, fake determinístico,
prompts versionados e validação estrutural/factual no backend. Não há modelo real.
Ver `STATUS_FASE_2.md` para critérios, testes e limitações.

**Fase 4 implementada offline (19/11/2026):** webhook Meta (verificação/assinatura),
normalização desacoplada, inbox e outbox SQLite transacionais, processador e
despachante com transporte fake. Nenhuma conexão real. Ver `INTEGRACAO_META_FASE4.md`
e `STATUS_FASE_4.md`. O componente conceitual `MessageGateway` (seção 3) foi
implementado como `WhatsAppTransport`; `EventStore` do canal, como `channel_inbox`.

## 1. Fluxo pretendido

```text
Cliente WhatsApp
    |
    v
WhatsApp Cloud API (Meta) [futuro, autorizado]
    |
    v
Webhook HTTPS -> autenticação/validação -> deduplicação/eventos
    |
    v
Orquestrador de atendimento -> estado e contexto -> política de handoff
    |                                 |                 |
    |                                 |                 +--> humano/caixa de entrada [a definir]
    |                                 v
    |                           persistência mínima
    v
Classificador de intenção + IA (provedor configurável)
    |
    +--> Dados oficiais do salão (base editável e validada)
    +--> Link configurado do Booksy (sem API/sem scraping)
    |
    v
Validação da resposta -> envio Meta Cloud API -> métricas mínimas
```

## 2. Stack inicial sugerida (passível de revisão)

- **Python 3.11+** e **FastAPI**: API HTTP e endpoints de teste.
- **Pydantic Settings**: configurações e segredos por ambiente.
- **pytest**: testes unitários e de integração local.
- **PostgreSQL**: persistência alvo; em fase inicial é aceitável usar repositórios em memória ou SQLite para facilitar o simulador, mantendo abstração para migrar.
- **SQLAlchemy + Alembic**: opção para persistência e migrações quando banco real entrar no escopo.
- **API de modelo de linguagem**: provedor/modelo ainda não escolhido; criar adaptador substituível e modo simulado (fake).
- **Meta WhatsApp Cloud API**: integração real somente em fase autorizada.

Não instalar toda a stack se a fase atual não precisa dela. Evitar serviços pagos antes de aprovação.

## 3. Interfaces e responsabilidades

- `InboundMessage`: canal, ID do evento, ID pseudonimizado do cliente, texto, timestamp.
- `MessageGateway`: enviar respostas para um canal; implementação `Fake` e implementação Meta futura.
- `KnowledgeRepository`: ler registros aprovados de serviços/horários/políticas.
- `ConversationRepository`: estado, timestamps e contexto limitado por cliente.
- `AgentService`: classificar, decidir resposta ou handoff, sem acoplamento à Meta.
- `AIProvider`: abstração de inferência e saída estruturada com validação.
- `HumanHandoff`: interface com estados `BOT_ACTIVE`, `PENDING_HUMAN`, `HUMAN_ACTIVE` e retorno por evento autorizado.
- `EventStore`: registro de mensagens processadas para idempotência e eventos de estado.

São **nomes conceituais**; o Codex pode propor estrutura equivalente mais simples e explicar a decisão.

## 4. Máquina de estados de handoff (implementada no simulador da Fase 1)

```text
AUTO -- pedido de humano / falha local --> WAITING_HUMAN
WAITING_HUMAN -- operador assume --> HUMAN_ACTIVE
WAITING_HUMAN -- operador resolve --> RESOLVED
HUMAN_ACTIVE -- operador resolve --> RESOLVED
RESOLVED -- operador retoma explicitamente --> AUTO
RESOLVED -- cliente volta a escrever --> WAITING_HUMAN   (ADR-134, 22/11/2026)
RESOLVED -- cortesia isolada ("Obrigada!", 👍) --> RESOLVED   (ADR-136, provisória; só histórico)
```

O pedido atual da Fase 1 tem precedência sobre os nomes anteriores da proposta:
`BOT_ACTIVE` é alias de `AUTO`; `PENDING_HUMAN`, de `WAITING_HUMAN`. Não havia
estado persistido a migrar. Em `WAITING_HUMAN`, `HUMAN_ACTIVE` e `RESOLVED`, não
gerar respostas automáticas de conteúdo. Uma única confirmação explica que a
transferência é simulada e ninguém real foi notificado. Ações de cliente nunca
executam comandos administrativos. A autorização do operador é exclusivamente
local, baseada no acesso à CLI; não é autenticação para integração real.

## 5. Configuração e dados

- Segredos de API e assinaturas: variáveis de ambiente/gerenciador de segredos.
- `BOOKSY_URL`: placeholder obrigatório antes de liberar envio automático.
- Catálogo de serviços e preços: persistência editável com marca de data e estado de aprovação.
- Políticas/horários/endereço/profissionais: não assumir até receber dados.
- Prompts: versionados com limites claros e mecanismos de defesa contra injeção.
- Não enviar à IA mais dados pessoais do que o estritamente necessário.

## 6. Regras de resposta

- Pergunta sobre dado conhecido e aprovado → responder.
- Dado ausente, contraditório ou desatualizado → reconhecer limite; oferecer humano.
- Intenção de agendar → link Booksy configurado; não fazer operação na agenda.
- Pedido de cancelar/remarcar → orientar para Booksy ou humano; não afirmar cancelamento/remarcação.
- Pedido de humano → mudar estado; respeitar pausa do bot.
- Assuntos sensíveis, reclamações ou casos além da autonomia → escalonar.
- Respostas automáticas precisam ser validadas antes do envio quando dependem de dados críticos.

## 7. Segurança e robustez

- Validar autenticidade dos webhooks de acordo com a documentação da Meta **vigente na implementação**.
- Deduplicar por ID de evento e tratar eventos fora de ordem, concorrência e falhas transitórias.
- Impor timeout, política de retentativa/backoff, orçamento de tokens e proteção de abuso.
- Evitar registrar telefone ou texto integral desnecessariamente; logs com identificação minimizada.
- Autorização para comandos administrativos (handoff, retomada e configuração).
- Nenhuma funcionalidade de broadcast ou marketing proativo no MVP.
- Em produção, revisar política atual de janelas de atendimento, templates e cobrança de mensagens da Meta.

## 8. Decisões — log

| ID | Data | Decisão | Estado |
|---|---|---|---|
| ADR-001 | 19/11/2026 | Usar o Booksy já empregado pelo salão, sem criar nova agenda | Vigente |
| ADR-002 | 19/11/2026 | Não usar API Booksy no MVP; encaminhar cliente por link oficial | Vigente |
| ADR-003 | 19/11/2026 | Usar somente canal oficial Meta para integração WhatsApp | Proposta aprovada no planejamento, implantação pendente |
| ADR-004 | 19/11/2026 | Começar com simulação local antes de credenciais reais | Diretriz de desenvolvimento |
| ADR-005 | 19/11/2026 | Python/FastAPI e camada de IA modular | Recomendação técnica, não decisão irrevogável |
| ADR-006 | 19/11/2026 | Adotar FastAPI com fábrica, Pydantic Settings e pacote na raiz; compor HTTP/configuração em `main.py`, manter contratos de domínio sem dependência de HTTP | Implementado na Fase 0; reduz configuração de empacotamento sem impedir futura separação por interfaces |
| ADR-007 | 19/11/2026 | Usar Python 3.13.16/Linux como ambiente de referência; venv em `/tmp` porque o volume do projeto recusou links simbólicos | Testado localmente; outros ambientes ainda não validados |
| ADR-008 | 19/11/2026 | Manter dependências diretas em `requirements.in` e versões transitivas/hashes em `requirements.lock`; pip-tools isolado apenas para manutenção | Instalação limpa verificada; runtime e testes no mesmo lock enquanto o escopo for exclusivamente local |
| ADR-009 | 19/11/2026 | Declarar somente `InboundMessage` e `ConversationState`; criar interfaces e adaptadores de atendimento, IA, canal e persistência nas fases em que forem usados | Contratos implementados; sem simulador, transições, banco ou integrações antecipados |
| ADR-010 | 19/11/2026 | Usar `httpx2` no TestClient, conforme o Starlette instalado; manter avisos como erros nos testes | Testado; transporte legado `httpx` emitia aviso de depreciação |
| ADR-011 | 19/11/2026 | Falhar na configuração sem valores de entrada; responder erros HTTP inesperados antes do início da resposta com 500 genérico e evento estático; desativar logs de acesso no comando local | Testado na Fase 0; não substitui observabilidade e revisão de segurança futuras |
| ADR-012 | 19/11/2026 | Adotar AUTO/WAITING_HUMAN/HUMAN_ACTIVE/RESOLVED conforme novo pedido; preservar aliases antigos e exigir retomada administrativa após resolução | Implementado/testado na Fase 1; atualiza a proposta anterior de estados |
| ADR-013 | 19/11/2026 | Separar ConversationManager, IntentDetector, KnowledgeRepository, ResponseGenerator e HumanHandoffManager; orquestrar em AgentService | Implementado/testado; detector substituível por interface sem construir IA nesta fase |
| ADR-014 | 19/11/2026 | Armazenar cópias em memória com transação serializada por processo; deduplicar ID por conversa/sessão e detectar conflito por hash | Testado inclusive com entregas concorrentes; sem durabilidade ou garantia distribuída |
| ADR-015 | 19/11/2026 | CLI sem argumentos usa base vazia; importação de JSON permite somente synthetic=true; fixture de demonstração usa example.invalid e rotula cada resposta | Implementado/testado; nenhum dado oficial foi presumido |
| ADR-016 | 19/11/2026 | Expor apenas CLI para simulação/administração; configurar limites por SIM_; limitar histórico/contexto e recusar novos eventos após atingir capacidade | Implementado/testado; nenhuma nova rota HTTP, dependência ou integração |
| ADR-017 | 19/11/2026 | Executar somente FakeAIProvider local; aceitar apenas AI_PROVIDER=fake; nenhum SDK, credencial ou chamada externa | Implementado/testado na Fase 2, conforme restrição do anexo atual |
| ADR-018 | 19/11/2026 | Manter revisões imutáveis com origem/data/versão/aprovação/revisor; usar somente a última revisão aprovada e rejeitar conflitos semânticos | Implementado/testado; nova revisão pendente também suspende o dado anterior |
| ADR-019 | 19/11/2026 | Separar catálogo oficial vazio, pendências sem valores e fixtures sintéticas; converter v1 somente como aprovação fictícia de compatibilidade | Implementado/testado; CLI não importa registros oficiais reais |
| ADR-020 | 19/11/2026 | Recuperar só fatos relevantes aprovados e histórico relacionado limitado; não enviar identidades técnicas, operador ou metadados privados de revisão ao provedor | Implementado/testado; conteúdo recuperado não tem autoridade administrativa |
| ADR-021 | 19/11/2026 | Revalidar saída, referências/versões recuperadas, entidades/contexto e candidata contra resposta reconstruída com campos e templates do backend | Implementado/testado; citações sozinhas não provam factualidade; texto livre fica restrito |
| ADR-022 | 19/11/2026 | Versionar prompts por arquivo e manifesto ativo, selecionável só por configuração confiável; registrar versão e referências no resultado/histórico, sem prompt em logs | Implementado/testado; versões anteriores permanecem disponíveis |
| ADR-023 | 19/11/2026 | Fazer uma tentativa de provedor; em falha, preservar contexto e pausar com fallback; revalidar aprovação antes de concluir; timeout real será obrigação de adaptador futuro | Implementado/testado com falhas/timeout simulados; sem preempção de código bloqueado |
| ADR-024 | 19/11/2026 | Tratar política de cancelamento como categoria/intenção distinta da política geral | Implementado/testado; dado específico ausente não é substituído por regra genérica |

## 8.1. Organização do núcleo local

`simulator.py` é o adaptador de entrada/saída e a composição do simulador.
`application/service.py` coordena mensagens; `conversations.py` controla sessões;
`handoff.py`, transições. `intents.py` implementa regras explícitas e `responses.py`
usa apenas dados de `knowledge.py`. `memory.py` implementa os repositórios.
Os protocolos usados estão em `application/ports.py`.

Na Fase 1, conhecimento plano e regras estabeleciam o simulador. Na Fase 2,
`knowledge_catalog.py` mantém revisões e aprovação; `knowledge_projection.py`
produz a visão autorizada utilizada pelos mesmos detector e templates.
`VersionedKnowledgeRepository.append_revision` é autoria local confiável,
sem API administrativa, e não elimina o histórico. Conhecimento e conversas
continuam em memória; nenhum SDK ou provedor de IA real foi instalado.

O armazenamento salva somente ao concluir a operação. O histórico e a auditoria
são limitados separadamente; hashes de eventos não são removidos durante a
sessão. Ao atingir capacidade, novas mensagens falham sem reexecutar efeitos
antigos. `/reset` e reinício descartam tudo. Isso não atende à persistência da Fase 3.

## 8.2. Fluxo da Fase 2

```text
Mensagem -> validação/sessão/deduplicação -> barreira de estado humano
  -> catálogo atual aprovado -> extração determinística de escopo
  -> recuperação de fatos e contexto relevante -> AIRequest + prompt versionado
  -> FakeAIProvider -> schema AIResponse -> referências do snapshot recuperado
  -> entidades/contexto autorizados -> reconstrução por campos/templates
  -> comparação factual/política -> nova checagem da aprovação
  -> eventual pedido de humano permitido pelo backend -> resultado e proveniência
```

Pedido explícito de humano e estados pausados não dependem de chamada ao provedor.
Saída de modelo não contém ação administrativa autorizável: campos desse tipo
são rejeitados. Sugestão validada pode apenas solicitar ajuda; assumir/resolver/
retomar continuam pela interface administrativa local existente.

Prompts fornecem orientação de comportamento; `ai/contracts.py` define o formato;
`ai/validation.py` e `AgentService` impõem regras reais. Modelos futuros terão de
respeitar esse plano restrito e passar por avaliação própria. Fake não demonstra
qualidade linguística ou segurança de um modelo generativo.

## 9. Gatilhos para revisão de arquitetura

- Resposta formal do Booksy autorizando uma API e nova decisão do usuário.
- Salão exigir agendamento direto ou consulta de disponibilidade (ampliação de escopo).
- Requisitos de atendimento humano que dependam de plataforma de inbox.
- Volume, custos, segurança e requisitos de disponibilidade acima do MVP.

## Persistência implementada — Fase 3

`bootstrap.py` compõe `ConversationRepository` para CLI e fábrica FastAPI.
`persistence/sqlite.py` implementa o protocolo existente, sem SQLite no domínio.
`transaction()` agora também pode representar uma unidade otimista: leitura de
snapshot completo em transação curta, cópia em memória durante cálculo/IA fake,
e commit posterior com comparação de versão sob `BEGIN IMMEDIATE`.

O adaptador confirma conversa, contexto, mensagens, evento/resultado e auditoria
juntos. Conflito invalida a tentativa inteira; aplicação relê até três vezes,
sem sleep de coordenação de negócio ou retry infinito. Escritas SQLite são
serializadas pelo próprio banco entre processos. A ordem observável é a ordem
de commits, sem FIFO por chegada ou relógio do cliente. Não há atomicidade com
serviços externos e nenhuma operação externa é executada nesta fase.

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-025 | Usar `sqlite3` da biblioteca padrão, SQL parametrizado e duas migrações SQL versionadas via `user_version` | Sem dependências novas; criação e evolução 1 → 2 testadas; Linux/SQLite local |
| ADR-026 | WAL, foreign keys, synchronous FULL, constraints e snapshots/versionamento por conversa | Testes entre threads/processos, pausa humana concorrente e rollback; geração sem transação aberta |
| ADR-027 | Persistir somente eventos COMPLETED junto aos efeitos e resultado autorizado | Falha antes do commit não deixa evento iniciado; recuperação exige reentrega do mesmo ID; não é fila durável |
| ADR-028 | Chave durável por conversa + ID, independente dos resets de sessão | Conflito de conteúdo é recusado; duplicata reutiliza metadados originais sem nova resposta; não garante envio externo exatamente uma vez |
| ADR-029 | Retenção de conteúdo sintético por comando confirmado; tombstones e hashes mantidos com capacidade limitada | Histórico de IA, histórico armazenado, retenção e idempotência têm limites distintos; exclusão não reativa conversa |
| ADR-030 | Conhecimento/prompt continuam em arquivos versionados; vincular catálogo ao ensaio SQLite por hash | Sem duplicar catálogo em SQL ou enfraquecer aprovação; autoria em memória não torna revisão durável |
| ADR-031 | Exigir arquivo sintético reconhecido, caminho sem symlinks e permissão efetiva 0600 | Volume atual do código mantém 0644 e é recusado; testes/demonstrações usam volume Linux privado em /tmp; sem criptografia |
| ADR-032 | Exigir confirmação de reset também na CLI em memória; expor manutenção apenas local | Consumidores/testes/exemplo atualizados; não é autenticação administrativa remota |

O schema contém `metadata`, `conversations`, `context`, `events`, `messages`,
`transitions` e `audit`. `conversations.session_id` representa a sessão atual;
`client_id` é o mesmo identificador interno fictício da conversa, não um cadastro
pessoal. Eventos guardam a sessão de processamento e sobrevivem ao reset.
Não existe tabela de clientes reais, inbox ou agendamento.

Migrações executam em uma transação SQL explícita, sem `executescript`, que faria
commit implícito. Um lock de arquivo Linux limitado pelo timeout coordena só a
inicialização; a consistência do atendimento não depende dele nem de mutex em
memória. Atualização incompleta da mensagem é revertida no reinício pelo SQLite;
não se persiste estado de processamento parcial. Falha de auditoria após rollback
é registrada como evento estático e não é convertida em sucesso.

Capacidades são locais e sintéticas. O servidor HTTP apenas inicializa o
repositório e preserva `/health` como liveness. Nenhuma rota administrativa,
integração real, implantação ou Fase 4 foi implementada. Ver `STATUS_FASE_3.md`
para comandos, garantias precisas e limitações de retenção/privacidade.


## Integração Meta offline — Fase 4

A integração fica em `channels/` (contratos, adaptador Meta puro, orquestração,
transporte fake) e `persistence/sqlite_channel.py`. O domínio não conhece o JSON
da Meta; o núcleo recebeu apenas `AgentService.handle(..., effects=)`, que executa
callbacks DENTRO da unidade de trabalho da conversa. O repositório SQLite passou a
aceitar efeitos encenados (`stage_effect`) gravados no mesmo `BEGIN IMMEDIATE` do
commit da conversa, e transações curtas independentes (`write_transaction`) para
ingestão e filas. A outbox resolve a limitação registrada no ADR-027 ("não é fila
durável") para o canal WhatsApp.

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-033 | Inbox/outbox no mesmo arquivo SQLite das conversas; conclusão da inbox + efeitos da conversa + outbox em um único commit | Rollback/LeaseLost testados; SQLite segue de um único escritor |
| ADR-034 | Webhook: 413 (tamanho) → 401 (assinatura sobre bytes brutos) → 400 (JSON/estrutura) → commit → 200; 503 se a gravação falhar; itens não suportados/de outro número ignorados com 200 | Testes HTTP; nenhuma IA na requisição |
| ADR-035 | Conversa = `wa-` + HMAC(chave dedicada, número + remetente); BSUID preferido ao wa_id; evento = hash do wamid | Telefone não aparece em IDs/logs; trocar a chave separa conversas |
| ADR-036 | Envio: SENDING confirmado antes do transporte; incerto/lease vencido → UNKNOWN sem retry automático; retry só para rejeição explícita reprocessável; reconciliação por `biz_opaque_callback_data`; reenvio de UNKNOWN só com confirmação humana | Sem "exatamente uma vez" externo; formato real do callback a confirmar |
| ADR-037 | No claim, suprimir resposta se estado/sessão da conversa mudou, conversa excluída ou janela (23h) vencida | Janela residual = duração de uma chamada de envio |
| ADR-038 | Somente `WHATSAPP_TRANSPORT=fake`; nenhum cliente HTTP; apenas `build_text_payload` para a futura Graph API | Teste garante ausência de cliente de rede no pacote |
| ADR-039 | Tipos não-texto registrados sem interpretação e sem resposta automática | Pendente decisão de produto sobre aviso ao cliente |
| ADR-040 | Banco local durável no `$HOME` (ext4, diretório 0700); volume vfat recusado antes de criar arquivos; `/tmp` (tmpfs) não é durável | Diagnóstico por `findmnt`; teste simula vfat |
| ADR-041 | Canal desligado por padrão; rotas montadas só com configuração validada (SQLite, número, três segredos fortes e distintos) | Erros sem valores; `SecretStr` |
| ADR-042 | Ordem por conversa: item da inbox/outbox não é reivindicado enquanto houver item anterior da mesma conversa pendente | FIFO de chegada por conversa; entre conversas não há ordem |


## Adaptador HTTP, ciclo de vida e autorização de envio — Fase 5

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-043 | `GraphAPITransport` com `httpx2` (já travado), base `https://graph.facebook.com` constante, sem redirecionamento, sem proxies do ambiente, TLS verificado | Testado só com `MockTransport`; compatibilidade real não confirmada |
| ADR-044 | Transporte real bloqueado por constante de código `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED=False`, além da configuração; CLI local só aceita fake; importação tardia | Variáveis de ambiente sozinhas nunca ativam envio |
| ADR-045 | Classificação por `error.code`: retry só onde a Meta indica tentativa recusada/"failed to send"; códigos de indisponibilidade ambígua e 5xx → UNKNOWN; falha de conexão antes de transmitir → retry | Tabela em `INTEGRACAO_META_FASE4.md` §7; códigos reais pendentes de ensaio |
| ADR-046 | Nomenclatura: IN_PROGRESS=SENDING, CANCELLED=SUPPRESSED; DELIVERED/READ em `delivery_status` separado; transições só para frente; anomalias contadas | Sem estados redundantes nem migração destrutiva |
| ADR-047 | Correlação de status por ID do provedor ou callback restrito a SENDING/UNKNOWN | Status forjado/atrasado não reabre envio rejeitado |
| ADR-048 | Autorização de envio em `send_policy.py`, pura e reavaliada antes de cada tentativa; falta de evidência bloqueia | Janela conservadora pela mensagem de origem |
| ADR-049 | Limite de eventos vira janela do snapshot; idempotência antiga por consulta durável (`lookup_event`) | Sem bloqueio permanente por volume; chaves nunca apagadas |
| ADR-050 | Limite de conversas aplica-se às ATIVAS; ao atingir, arquiva a mais ociosa elegível (não humana ativa, sem envio pendente/incerto, ociosa ≥ `STORAGE_ARCHIVE_IDLE_HOURS`); sem elegível → erro transitório com retry/alerta | Arquivar não apaga dados nem muda estado; nova mensagem reativa |
| ADR-051 | Retenção (`/purge`) anonimiza e arquiva sem bloquear; preserva chaves, estado e auditoria; não toca humano ativo nem envio pendente/incerto | Exclusão explícita (`retire`) continua bloqueando o identificador: decisão do controlador pendente |
| ADR-052 | `OperatorAssurance.SIMULATED_LOCAL` explicita que `operador-local` não é autenticação | Nenhuma rota HTTP administrativa; interface operacional futura exigirá nível verificado |


## Homologação controlada — Fase 6A

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-053 | Ambiente `APP_ENV=homologation` exige banco `*.homolog.synthetic.sqlite3` e arquivo de autorização; ambos proibidos fora dele | Isolamento testado |
| ADR-054 | Autorização em arquivo privado (0600, sem symlink, esquema, frase de confirmação, período ≤72h, tetos rígidos de limites), não em variável de ambiente | Inválida → aplicação não inicia |
| ADR-055 | Trava separada `HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED=False`; trava de produção intacta; uma não abre a outra | Testado com monkeypatch |
| ADR-056 | Guarda no claim: período/operação/limites → aguardar sem mexer na outbox; número/allowlist → SUPPRESSED; tentativas (inclusive retries) registradas em `channel_send_ledger` com hash do destinatário | Limites persistentes entre processos |
| ADR-057 | Em homologação, remetentes fora da allowlist são descartados no webhook sem persistir | Minimiza dados de terceiros |
| ADR-058 | Interrupção emergencial persistida (`channel_control`), auditada, válida em todos os ambientes; `CHANNEL_DISPATCH_HALTED` só interrompe | Retomada exige `RESUME_DISPATCH` |
| ADR-059 | Métricas em `channel_metrics` + agregados derivados; rastreamento por IDs técnicos; log privado 0600 com campos permitidos | Sem PII (testado) |
| ADR-060 | Graph API v26.0 mantida; v27.0 citada no guia sem changelog acessível → reavaliar na 6B | Página de versões consultada em 19/11/2026 |


## Painel de atendimento humano — Fase 7

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-061 | Painel em app FastAPI separada do webhook; mesmo SQLite como fonte única | Rotas cruzadas inexistentes (testado nos dois sentidos) |
| ADR-062 | Frontend em JS puro (ES modules) + CSS, sem build/npm; CSP `'self'` sem inline; DOM só por `textContent` | XSS testado em Chromium real |
| ADR-063 | Senhas Argon2id (`argon2-cffi`); sessões com token aleatório (só hash no banco); CSRF derivado por HMAC + Origin | Força bruta 5/15 min por login e IP |
| ADR-064 | RBAC ADMIN/OPERATOR no backend; visibilidade no SQL (fila sem responsável + próprias para OPERATOR) | Inexistente e proibido respondem 404 |
| ADR-065 | Ações humanas via ConversationManager (versão otimista) com atribuição/outbox/auditoria no mesmo commit; `VerifiedOperator` substitui `operador-local` no painel | Corridas de assumir/transferir/responder testadas |
| ADR-066 | Outbox reconstruída (migração 6) para `origin=operator` com chave de idempotência; claim cancela se o responsável mudou | Migração 5→6 real testada; FKs conferidas |
| ADR-067 | `channel_contacts`: endereço e última mensagem do cliente por conversa (janela da resposta humana); removido em exclusão/anonimização | Minimização: nunca exibido no painel |
| ADR-068 | Tempo real por SSE + `change_log` por triggers; stream só com IDs visíveis; reconexão por `Last-Event-ID`; rotação 60 s | Sem estado em memória como fonte de verdade |
| ADR-069 | Auditoria do painel somente inclusão (triggers abortam UPDATE/DELETE) | Retenção da auditoria a definir |
| ADR-070 | Migrações com `sqlite3.complete_statement` e procedimento oficial de reconstrução (FKs off + `foreign_key_check`) | Triggers suportados |


## IA generativa, conhecimento aprovado e avaliações — Fase 8

Referência técnica completa em [`IA_GENERATIVA.md`](IA_GENERATIVA.md); evidências em
[`STATUS_FASE_8.md`](STATUS_FASE_8.md). O ADR-017 ("aceitar apenas AI_PROVIDER=fake")
passa a valer como **efeito da trava de código** do ADR-073: `anthropic` existe na
configuração, mas é recusado enquanto a trava for `False`.

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-071 | Provedor inicial **Anthropic** (SDK oficial `anthropic` 2.11, já no lock, sobre `httpx2`); modelo configurável, padrão `claude-opus-5-5`; sem segundo SDK (OpenAI não implementado) | Parâmetros conferidos na referência oficial (20/11/2026): sem `temperature`, `effort` em `output_config`, `fallbacks: "default"`; Haiku 5.5 exige fallback desligado |
| ADR-072 | Contrato `ai-response-v2`: o modelo classifica e propõe só uma abertura cordial; **todos os fatos são montados pelo backend**; abertura sanitizada por regras e descartada ao menor sinal factual | Modelo hostil simulado: 0 violações; aberturas maliciosas descartadas sem perder a resposta |
| ADR-073 | Trava `AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED=False` independente das travas Meta; SDK importado só quando autorizado; chave sozinha nunca ativa | Testes cruzados: abrir uma trava não abre a outra |
| ADR-074 | Prompt `v2` registrado e **inativo**; `v1` segue ativo no manifesto; troca só por configuração confiável (`AI_PROMPT_VERSION`) e comparação pela avaliação | `test_fake_provider_unchanged_for_v1_and_offline` |
| ADR-075 | Recuperação **determinística** por intenção/serviço; sem embeddings nem banco vetorial | Catálogo ≤128 registros ativos e estruturado; RAG vetorial só se o volume/forma mudar |
| ADR-076 | Conhecimento no SQLite (`knowledge_revisions`, somente inclusão, escopo SYNTHETIC/OFFICIAL fixado uma vez); rascunho → aprovação → revogação pelo painel, só ADMIN; opção de aprovador distinto | Os mesmos validadores da Fase 2 rodam a cada inclusão; conflito de preço aprovado é impossível |
| ADR-077 | Consumo em `ai_usage` (só metadados); orçamento diário por requisições e custo **estimado**; modelo sem tarifa bloqueia; gravação independente da conversa | Estimativa ≠ fatura |
| ADR-078 | Minimização antes do provedor externo (telefone, e-mail, CPF/CNPJ, dígitos longos) e nenhum identificador interno no pedido | Nomes e dados livres não são removidos: decisão jurídica pendente |
| ADR-079 | Avaliação offline (`rocha_reis_agent/evaluation`): cenários A–T sobre o núcleo real, oráculo de segurança **independente** do validador, modelo hostil em rodízio, adaptador real sobre HTTP simulado; resultados por provedor/prompt em `evals/results/` | Teste de mutação: validador quebrado → oráculo acusa |
| ADR-080 | Detector de regras: pedido humano inclui "falar com alguém", recusa ao robô e reclamação explícita; negação só anula quando nega diretamente a pessoa; pedido de confirmação de agenda vira orientação Booksy; `valor` reconhecido como preço | Falhas encontradas pela avaliação; casos difíceis restantes documentados |
| ADR-081 | Resposta do operador: se as retentativas de conflito do SQLite se esgotam, a chave de idempotência é conferida antes de devolver erro | Corrige falha intermitente do clique quádruplo (Fase 7) |


## Homologação offline da IA generativa — Fase 9A

Relatório em [`STATUS_FASE_9.md`](STATUS_FASE_9.md); modelos em
[`AVALIACAO_MODELOS_IA.md`](AVALIACAO_MODELOS_IA.md); roteiro 9B em
[`HOMOLOGACAO_IA_FASE9.md`](HOMOLOGACAO_IA_FASE9.md). O contrato v2 (ADR-072) foi preservado.

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-082 | Registro verificado de modelos (`ai/models.json`, substitui `pricing.json`): ID, ciclo de vida, contexto, saída estruturada, esforços, fallback, tarifas (inclui segunda tabela do Haiku); `AI_MODEL` fora dele, descontinuado ou sem saída estruturada é recusado | Conferido nas páginas oficiais em 20/11/2026; `test_registry_matches_verified_documentation` |
| ADR-083 | Orçamento verificável: reserva de **pior caso** antes de cada chamada (entrada a 2 caracteres/token + `max_tokens`, × tentativas, × 2 com fallback, maior tarifa); tetos diário e por execução (`RunBudget` compartilhável); timeout/rede/5xx registram o pior caso | Recusa antes de sair da máquina (testado); estimativa ≠ fatura |
| ADR-084 | Custo calculado por tentativa em `usage.iterations` com a tarifa do modelo que serviu; `ai_usage.model` registra o modelo que serviu; fallback sem tarifa bloqueia o orçamento | `test_cost_uses_each_attempt_and_the_model_that_served` |
| ADR-085 | Prazo total: `AI_TIMEOUT_SECONDS` dividido entre as tentativas do SDK; 404 → `model_not_found`; `stop_reason` diferente de `end_turn`/`stop_sequence` → falha segura | Extensão de timeout conferida no transporte simulado |
| ADR-086 | Correção ortográfica do detector só contra vocabulário fechado (palavras-chave + nomes do catálogo), ≥ 7 letras, 1 edição (Damerau), candidata única; serviço inferido é declarado ao cliente | Casos negativos testados ("marcas", "atendimento", "colocação") |
| ADR-087 | Intenção `promotion` (sem tipo de registro aprovado → ausência sempre declarada); meios de pagamento → `policy`; passado de reserva ("reservei") → `booking` | CONTEXTO_MESTRE §4.4; cenários D2, D4–D7, U2 |
| ADR-088 | Pergunta elíptica herda **preço** da pergunta anterior do mesmo cliente, decidida pelo backend (`Detection.inherit_intents`), dentro da janela de contexto; a pergunta anterior vai ao modelo como histórico não confiável; proposta sem a intenção herdada → falha segura | F2, F5, F6; sem herança com `AI_HISTORY_LIMIT=0` |
| ADR-089 | Prompt `v3` registrado e **inativo** (v1 continua ativo); oráculo de vazamento cobre todas as versões registradas | `test_prompt_versions_are_preserved_and_new_one_is_inactive` |
| ADR-090 | Avaliação v2: 120 cenários (77 originais intactos), categorias U–X, modos de falha HTTP pelo adaptador real, métricas com numerador/denominador, precisão/recall de encaminhamento, critérios **pré-registrados** (`evals/criteria.json`) e metadados de reprodutibilidade (conjunto, hashes, modelo, prompt, contrato, commit) | `compare` avisa quando conjunto/catálogo/critérios diferem; `--save` nunca sobrescreve |
| ADR-091 | Avaliação real (9B) implementada atrás da trava de código + arquivo de autorização 0600 (tetos rígidos US$ 50 / 2.000 requisições, ≤ 7 dias, só dados sintéticos); `--smoke` = 1 requisição; violação de segurança interrompe | Recusada na Fase 9A (testado); ensaio só com HTTP simulado |


## Migração para Google Gemini — Fase 9B

**GOOGLE GEMINI É O PROVEDOR OFICIAL ESCOLHIDO PARA O ROCHA REIS AGENT.** As decisões ADR-071
(provedor inicial Anthropic), ADR-073 (trava `AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED`), ADR-082
(registro de modelos Claude), ADR-084 (custo por tentativa de fallback) e as partes Anthropic do
ADR-091 ficam **substituídas** (histórico preservado acima). O contrato v2 (ADR-072), a
recuperação determinística (ADR-075), o conhecimento aprovado (ADR-076), o registro de consumo
(ADR-077), a minimização (ADR-078) e a avaliação (ADR-079/090) continuam vigentes.

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-092 | Provedor oficial **Google Gemini** pela Gemini Developer API, SDK oficial `google-genai` **3.6.1** fixo (usa `httpx2`, já travado); SDK `anthropic` e `anthropic_provider.py` removidos | Lock regenerado com hashes; só acréscimos/remoções do SDK trocado |
| ADR-093 | `models.generate_content` com `system_instruction`, `response_mime_type=application/json`, `response_json_schema=RESPONSE_SCHEMA_V2`, `ThinkingConfig(thinking_level)`, `HttpOptions` por requisição (timeout em ms, `HttpRetryOptions`) | Binding conferido no SDK instalado; Interactions API (recomendada para projetos novos) a reavaliar |
| ADR-094 | Cliente só via `create_client`: chave **explícita**, `vertexai=False`, `enterprise=False` (o SDK leria GEMINI_API_KEY/GOOGLE_API_KEY sozinho); trava própria `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED=False`; SDK nunca importado no caminho offline | `test_genai_sdk_is_not_loaded_by_the_offline_application`, confinamento de rede |
| ADR-095 | Registro de modelos Gemini (schema 2): só **estáveis** com tarifa lida verbatim — `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-3.8-flash`; reajuste datado (`schedule`); níveis de raciocínio por modelo; **sem modelo padrão** (`AI_MODEL` obrigatório) | Preview/descontinuados recusados; 2.5 Flash-Lite e 3.1 Pro pendentes de verificação |
| ADR-096 | Bloqueios (`prompt_feedback.block_reason`, `finish_reason` de segurança), truncamento (MAX_TOKENS), encerramento inesperado e **modelo efetivo** diferente do pedido (`model_version`) → falha segura; erros por `ClientError.code/status` e exceções do `httpx2` | Testes parametrizados de cada caso |
| ADR-097 | Orçamento neutro (`ai/budget.py`): reserva de pior caso, tetos diário/por execução/**por modelo**; custo = entrada não cacheada × tarifa + cache × tarifa de cache + (saída + raciocínio) × tarifa de saída; incerteza registrada pelo pior caso | Migração 008: `reasoning_tokens`, `requested_model` em `ai_usage` |
| ADR-098 | Temas de saúde/gravidez/alergia/medicamentos escalam para pessoa por vocabulário fechado (regra de **segurança**, não de compreensão; termos do Gemini vedam conselho médico) | X2 aprovado; H9/J6/L3 continuam dependendo do modelo |
| ADR-099 | Prompt `v4` (Gemini) registrado e **inativo**: não repete o esquema no prompt (orientação do SDK), prioriza os casos difíceis | v1 continua ativo |
| ADR-100 | Avaliação: `gemini-simulated` (adaptador real sobre REST simulado) e `gemini` real atrás da trava + autorização com conta/projeto **pagos**; W2 remapeado de "fallback" para "modelo efetivo inesperado" (mesmo ID e expectativas) | 120 cenários e critérios inalterados |


## Consolidação operacional offline — Fase 10

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-107 | Avisos do painel **derivados do estado do banco** (`GET /notifications`), com chave estável por ocorrência (`hr:<conversa>:<transição>`, `sf:<envio>`…), sem texto de mensagem; o cliente só guarda as chaves já mostradas. Reconexão, recarga e eventos perdidos não criam duplicatas nem lacunas | `test_human_request_notification_has_stable_key_and_no_content`, e2e de recarga |
| ADR-108 | Falha, resultado incerto, interrupção de envios e falha de entrada são avisos **obrigatórios** (preferências do navegador não os ocultam); escopo do atendente limitado às próprias conversas/envios | `test_send_failure_and_unknown_are_mandatory_and_scoped` |
| ADR-109 | Migração 009: `panel_settings` tipado (`urgent_after_minutes` 1–1440, `retired_contact_policy`), validado no esquema Pydantic estrito **e** no store; travas reais seguem só no código | `test_typed_settings_are_validated_in_backend_and_audited`, `test_migration_009_preserves_existing_settings` |
| ADR-110 | Contato excluído que volta a escrever: padrão `block` = inbox `DONE` com payload redigido e `retired_contact_blocked` (antes: `FAILED rejected_by_core`, conteúdo guardado e invisível); `allow_new_conversation` e reativação individual recomeçam sessão vazia, mantendo chaves de idempotência | `test_retired_contact_is_blocked_visibly…`, `test_allow_policy…`, `test_manual_reinstatement…` |
| ADR-111 | Histórico paginado por `ordinal` com situação de envio buscada em lote (sem N+1); página antiga preservada nas atualizações ao vivo | `test_history_is_paginated…`, medição em `STATUS_FASE_10.md` |
| ADR-112 | Backup: API online do SQLite em leitura → `integrity_check` + `foreign_key_check` → AES-256-GCM (`cryptography`) com manifesto como dado associado; chave em arquivo 0600 fora do diretório de backups ou frase-senha (scrypt); escrita `.partial` + rename | `tests/test_phase10_backup.py` |
| ADR-113 | Restauração só para arquivo **novo**; substituir o banco ativo é passo separado (`promover`) com frase `SUBSTITUIR:<arquivo>`, lock exclusivo (falha com o app aberto) e cópia anterior | `test_promote_requires_confirmation…` |
| ADR-114 | Demonstração em perfil privado fora do repositório (`~/.local/share/rocha-reis-agent/demo`, marcado; `--recriar` só apaga diretório marcado); iniciador fixa `127.0.0.1`, recusa travas abertas, transporte/IA reais e permissões frouxas | `test_demo_profile_is_isolated…`, `test_launcher_preflight_checks` |

## Segurança avançada — Fase 13

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-127 | TOTP (RFC 6238) com a implementação da `cryptography` (sem dependência nova); ±1 passo; último passo gravado contra reutilização | `tests/test_phase13_mfa.py` |
| ADR-128 | Segredo TOTP cifrado (AES-GCM, AAD = id do usuário) com chave em arquivo 0600 fora do banco e dos backups; app não inicia com chave ausente/frouxa; rotação recifra tudo numa transação | idem; `test_mfa_key_rotation_command…` |
| ADR-129 | 1ª etapa do login com segundo fator ativo NÃO cria sessão: etapa de 5 min/5 tentativas em tabela própria + cookie separado; nenhuma rota aceita esse cookie | `test_two_step_login_and_partial_state_reaches_nothing` (API e navegador) |
| ADR-130 | Códigos de recuperação: 10 × 60 bits, HMAC-SHA-256 por usuário, uso único; redefinição administrativa com justificativa, confirmação e auditoria | testes de recuperação e redefinição |
| ADR-131 | Rotação de sessão em troca de senha e ativação do segundo fator; painel reabre o SSE após rotação e confere a sessão antes de deslogar | e2e Fase 13 (defeito encontrado e corrigido) |
| ADR-132 | `X-Forwarded-*` só de proxies declarados (`PANEL_TRUSTED_PROXIES`); HSTS com HTTPS; pré-produção exige 2FA, HTTPS, cookie Secure e ≤ 30 min de inatividade | `test_forwarded_headers…`, `test_preproduction_panel_refuses_weak_settings` |
| ADR-133 | Backup agendável com lock, verificação, destino secundário substituível (`LocalDirectory`), retenção e estado sem segredos; ensaio de desastre isolado | `tests/test_phase13_ops.py` |

## Preparação de pré-produção — Fase 12

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-120 | Categoria e profissional como fatos próprios (`category:*`, `professional:*`) com `active`; serviço referencia por id; duração estimada (valor ou faixa, 5–720 min) e tipo de preço (fixo, a partir de, faixa, avaliação) no documento JSON — **sem mudança de tabela**; documentos antigos válidos por padrões | `tests/test_phase12_knowledge.py` |
| ADR-121 | Serviço só é aprovado com categoria/profissionais aprovados e ativos; inativos somem das respostas e viram pendência; nomes duplicados bloqueiam | idem |
| ADR-122 | Texto das respostas idêntico quando os campos novos estão vazios (avaliações fixadas por hash preservadas); oráculo aceita o máximo de faixa aprovada | regressão completa + `test_service_without_new_fields…` |
| ADR-123 | Perfil de ambiente gravado no banco; banco `demo`/`preproduction` não abre em outro perfil; banco antigo não vira pré-produção | `tests/test_phase12_environment.py` |
| ADR-124 | Proteção em repouso **constatada** por dm-crypt no `/sys` (nunca presumida); exigida em pré-produção; SQLCipher não adotado | idem; `SEGURANCA_ARMAZENAMENTO.md` §2 |
| ADR-125 | Quarta trava de código `REAL_DATA_ACTIVATION_AUTHORIZED=False` + `metadata.data_classification` (migração 010); posicionada sem alterar o contexto do patch de ativação do Gemini | `test_real_data_stays_blocked_everywhere`; `git apply --check` do patch |
| ADR-126 | Snapshot e verificação de backup em memória (`serialize`/`deserialize`); `temp_store=MEMORY` | `test_backup_and_verify_leave_no_plaintext…` |

## Finalização funcional offline — Fase 11A

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-115 | Aprovação de conhecimento recusada (409, auditada) quando nome/apelido de serviço ou pergunta frequente normalizados colidem com **outro** registro aprovado; conflitos herdados aparecem em Pendências | `tests/test_phase11.py` (conflitos) |
| ADR-116 | Erros do framework na API do painel normalizados em português (422/404/405) sem ecoar a entrada; a interface só exibe `detail` textual e substitui 5xx por orientação | `test_framework_errors_are_portuguese…` |
| ADR-117 | Ajuda estática dentro do painel (resumo do manual), sem novo módulo nem conteúdo dinâmico | e2e `test_help_is_reachable_by_keyboard…` |
| ADR-119 | Envio da equipe: `UNIQUE(idempotency_key)` violado por pedido concorrente com a mesma chave é resposta idempotente (mesmo `operation_id`, `duplicate`), não erro | `test_same_key_race_after_version_check…` (falha sem a correção) |
| ADR-118 | `ops mensagem` / `iniciar.sh --mensagem|--nova-demo` só operam em perfil com marcador de demonstração e números `55119000…`; mensagem entra pelo caminho do webhook assinado | `test_fictitious_message_command_only_works…` |

## Preparação da homologação real — Fase 9C-A

| ID | Decisão | Evidência/limite |
|---|---|---|
| ADR-101 | Contexto **oferecido** pelo backend ao modelo: só com um serviço recente único, mensagem sem outro serviço e regras sem outro assunto (desconhecida ou preço sem serviço); o modelo só confirma a referência, o backend fixa o serviço | `test_model_can_resolve_a_followup…`, `test_context_cannot_be_invented…`, `test_context_offer_is_minimized` |
| ADR-102 | Experimento pré-registrado (`evals/experiment_9c.json`) fixa cenários, catálogo, critérios e prompt por hash; execução real recusada se algo mudar | `test_experiment_is_pinned_by_hash_and_prompt` |
| ADR-103 | Livro-razão de consumo **persistente** por autorização (fora do repositório): o teto vale para todas as execuções do período; `RunBudget` semeado com o consumo anterior | `test_persistent_ledger_caps_all_runs_of_the_authorization` |
| ADR-104 | Interrupção emergencial por arquivo (`stop_file`) e interrupção automática em violação de segurança ou cenário crítico reprovado | `test_stop_file_blocks…`, `test_critical_failure_aborts_the_real_run` |
| ADR-105 | Avaliação de IA real recusada se qualquer trava da Meta estiver aberta (homologação não alcança clientes); etapa e2e usa o canal real com `FakeWhatsAppTransport` | `test_gemini_run_refused_if_any_meta_lock_is_open`, `test_end_to_end_channel_flow` |
| ADR-134 | (22/11/2026, decisão do responsável na revisão funcional) Cliente que volta a escrever num atendimento **encerrado** reabre a conversa na fila humana (RESOLVED → WAITING_HUMAN, motivo `client_returned`, responsável anterior removido). Antes a mensagem era gravada sem resposta, fora da fila e sem aviso. A automação continua suspensa: só a retomada explícita leva a AUTO (ADR-012 preservado). **Consequência conferida na revisão:** depois da reabertura a retomada exige re-encerrar (WAITING_HUMAN → RESOLVED → AUTO); qualquer mensagem pós-encerramento (inclusive um "obrigada") volta à fila com aviso — avaliar com o salão (ver `STATUS_REVISAO_FUNCIONAL.md`). Numerado 107 por engano na primeira versão (107 já existia); `evals/scenarios.synthetic.json` e `evals/experiment_9c.json` mantêm "ADR-107" porque são fixados por hash no pré-registro do experimento 9C | `test_client_writing_after_resolution_goes_back_to_the_human_queue`, `test_complete_attendance_cycle_survives_restart` |
| ADR-135 | (22/11/2026, revisão funcional) As cinco travas de ativação são lidas de **uma única função** (`config.activation_locks()`) por instalação, diagnósticos, manifesto, iniciador e ensaio. As listas copiadas à mão eram a causa de a 4ª e a 5ª travas faltarem em vários relatórios. Os valores continuam constantes `Final = False`; a função só os lê | `test_single_source_of_locks_sees_each_of_the_five`, `test_five_locks_exist_are_false_and_are_reported_everywhere` |
| ADR-136 | (22/11/2026, U1 — **REGRA PROVISÓRIA DE PRODUTO**, sujeita à aprovação do responsável pelo salão na homologação 11B; **não** é decisão comercial aprovada) Refina o ADR-134: mensagem que seja **inteiramente** agradecimento/confirmação de uma lista fechada ("Obrigada!", "Valeu", "Ok", "Tudo certo", 👍, 🙏, "Obrigado 😊"; maiúsculas, acentos, pontuação simples e letras repetidas toleradas) fica só no histórico: a conversa continua `RESOLVED`, sem aviso, sem resposta e sem transição. Todo o resto reabre: pedido, reclamação, mensagem **mista** ("Obrigado, mas preciso de ajuda"), **ambígua** ("Então...", "Ainda não", "?", "Ok?"), negação ("Não está tudo certo"), emoji fora da lista, texto longo e **qualquer mídia** (áudio, imagem, documento, localização, interativa, tipo desconhecido — que passa a entrar no histórico como aviso sem conteúdo quando há atendimento humano ou a conversa está encerrada; em conversa automática o tratamento anterior foi mantido). Determinística (sem IA), em **um único módulo** (`application/courtesy.py`) chamado **num único ponto** (`AgentService`), dentro da transação versionada da conversa e depois da deduplicação | `tests/test_u1_cortesia.py`, `tests_e2e/test_u1_u3_browser.py` |
| ADR-106 | Ativação futura como **patch revisável** (`docs/homolog/ativacao_gemini_9c.patch`) em vez de branch com commit (commits não autorizados nesta fase); patch só altera a trava | `test_activation_patch_is_minimal_reversible_and_not_applied` |

