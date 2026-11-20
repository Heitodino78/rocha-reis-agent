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
