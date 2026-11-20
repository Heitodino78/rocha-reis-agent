# Relatório técnico — Fase 3

**Escopo:** persistência, confiabilidade e recuperação local, somente dados
sintéticos e FakeAIProvider. Fase 4 e integrações reais não executadas.

## 1. Resumo executivo e diagnóstico inicial

Antes de editar foram lidos o anexo, `AGENTS.md`, documentos/relatórios e os
67 arquivos de código, testes, configuração, fixtures e exemplos existentes.
A implementação confirmou o diagnóstico: conversas, contexto, eventos/resultados
e transições eram somente voláteis; um RLock serializava o processo, incluindo
chamada ao provedor. Não havia durabilidade, concorrência entre processos ou
recuperação após encerramento. A base de conhecimento já tinha aprovação/versões
em JSON e contratos de IA validados; essas regras foram preservadas.

A suíte inicial aprovou **189 testes em 1.95s**, com `pip check` sem conflitos.
Inventário de hashes: `/tmp/rocha-reis-phase3-baseline-j51sxdm0.json`.
Nenhum defeito preexistente foi encontrado nessa suíte. A skill
`.agents/skills/rocha-reis-development/SKILL.md` permanece ausente, inclusive nos
diretórios locais de skills pesquisados. Não se inventaram instruções substitutas.
A pasta continua sem `.git`; não houve commit ou PR.

O plano comunicado antes de editar foi introduzir adaptador SQLite/migrações,
transações/versionamento, eventos/resultados duráveis, auditoria/retenção,
composição CLI/HTTP e testes reais de reinício, concorrência e falhas.

## 2. Arquitetura e escolhas

Usou-se **sqlite3 da biblioteca padrão**, sem novas dependências, SQLAlchemy,
Alembic, Redis, filas, serviços ou contêineres. A interface existente já descrevia
uma unidade de trabalho pequena; o SQL fica inteiramente no adaptador. Dois
arquivos SQL e `PRAGMA user_version` atendem a evolução atual sem uma estrutura
de migração externa. PostgreSQL continua possibilidade futura, não dependência.

```text
CLI / fábrica FastAPI
  -> bootstrap: configuração e adaptadores
  -> AgentService / ConversationManager / HumanHandoffManager
  -> ConversationRepository.transaction(): unidade otimista
       -> leitura consistente e encerramento da conexão SQL
       -> cálculo em cópia isolada / FakeAIProvider / validação factual
       -> BEGIN IMMEDIATE + comparação de versão
       -> evento/resultado + contexto + histórico + estado + auditoria
       -> COMMIT
```

O domínio não importa SQLite. `build_agent` continua acessível pelo módulo do
simulador e aceita repositório explicitamente. Configuração escolhe memória ou
SQLite; falha no banco **não** ativa fallback silencioso para memória.

A aplicação HTTP compõe o repositório em `app.state.conversation_repository`.
Continua expondo somente GET `/health`; não foi criada API de atendimento ou
administração. O endpoint permanece liveness, sem prometer readiness do banco.

## 3. Arquivos criados e modificados

Criados:

```text
rocha_reis_agent/
├── bootstrap.py
├── application/retry.py
└── persistence/
    ├── __init__.py
    ├── sqlite.py
    └── migrations/
        ├── 001_initial.sql
        └── 002_audit_correlation.sql
tests/
├── persistence_worker.py
├── test_sqlite_persistence.py
├── test_sqlite_failures_and_concurrency.py
├── test_sqlite_restart_and_retention.py
└── test_sqlite_ai_regressions.py
examples/
├── phase3.start.demo.txt
└── phase3.resume.demo.txt
docs/STATUS_FASE_3.md
```

Modificados: `.env.example`, `.gitignore`, `README.md`, `CONTEXTO_MESTRE.md`,
`PLANO_DE_EXECUCAO.md`, `ARQUITETURA_E_DECISOES.md`, `examples/phase1.demo.txt`,
`application/conversations.py`, `application/ports.py`, `application/service.py`,
`config.py`, `domain/contracts.py`, `domain/conversations.py`, `main.py`,
`simulator.py`, `tests/conftest.py` e `tests/test_simulator.py`.

O exemplo/teste anterior da CLI foi ajustado somente para confirmar explicitamente
reset destrutivo. Os relatórios anteriores são preservados integralmente, assim
como catálogo, prompts, requisitos, dados pendentes e lock de dependências.
O inventário final por hashes e a contagem de arquivos estão no fechamento.

## 4. Modelo relacional e contratos

| Tabela | Conteúdo e relações |
|---|---|
| `metadata` | Marca sintética e hash do catálogo vinculado ao ensaio |
| `conversations` | PK fictícia, cliente interno igual à conversa, sessão atual, estado, closed/retired, versão, criação/atualização |
| `context` | PK/FK da conversa, serviços em JSON array e idade não negativa |
| `events` | PK composta conversa/evento, FK da conversa, sessão original, hash, tipo, estado COMPLETED, resultado autorizado JSON e processamento |
| `messages` | ID, FK da conversa e FK composta de evento, ordem única por conversa, papel, texto necessário, data, referências e versão do prompt |
| `transitions` | ID, FK da conversa, ordem, estados anterior/atual, ação, operador fictício e data |
| `audit` | ID, operação, entidade com hash, origem, data, resultado e correlação técnica |

Tabelas STRICT, foreign keys e CHECKs restringem tipos, estados, papéis, JSON,
identificadores, hash e valores mínimos. Índices cobrem consultas por conversa,
ordem de histórico, data de atualização, processamento e auditoria. Não existe
cadastro de clientes reais, tabela de reservas ou tabelas para funções futuras.
A sessão atual é parte da conversa; eventos mantêm a sessão em que foram aceitos.
Histórico/transições da sessão anterior são limpos por reset confirmado.

`ConversationRepository` preserva get/save/count/transaction. Cópias isoladas e
commit na saída bem-sucedida são o contrato; SQLite adiciona detecção de versão
vencida por `ConcurrentUpdate`, derivado do erro operacional seguro `StorageError`.
`Conversation.event_results` armazena `AgentResult` por evento, permitindo recuperar
proveniência, estado/intenção originais e resposta autorizada. Duplicata retorna
metadados originais com `duplicate=true` e `reply=None`, sem repetir efeitos.

## 5. Transações, falhas e garantia de recuperação

O snapshot completo de uma conversa é lido em uma transação de leitura curta e
a conexão é fechada antes de executar o provedor. Alterações ficam numa cópia.
O commit abre `BEGIN IMMEDIATE`, confere a versão persistida e grava todos os
efeitos de negócio juntos. Contagem de conversas é novamente conferida sob lock
de escrita para não exceder capacidade em criações concorrentes.

Falha em conversa, eventos, histórico ou antes do commit provoca rollback.
O resultado de `handle` somente sai para a CLI após o commit da unidade de
trabalho. Exceções de banco/código de escrita viram erro controlado sem SQL,
caminho, conteúdo privado ou traceback para o usuário. Não há resposta de
sucesso fingindo persistência quando a gravação falha.

Eventos não são confirmados em estado STARTED: somente COMPLETED é durável, no
mesmo commit dos efeitos. Se o processo termina abruptamente antes do commit,
a tentativa inteira desaparece e pode ser reenviada com o mesmo ID. Não há
fila de entrada durável ou retomada automática de mensagens nunca confirmadas.
O teste usa `os._exit(73)` depois da escrita dos eventos, antes de mensagens e
commit; outro processo verifica o estado anterior, integrity_check e reentrega.

Após uma falha recuperável, tenta-se registrar auditoria de rollback em transação
separada. Essa auditoria não participa do commit abortado. Se o banco permanece
indisponível/ocupado, registra-se `storage_failure_audit_unavailable`; não há
garantia de auditoria durável de uma falha física ou de um processo já morto.

Não existe atomicidade entre banco e saída de terminal, nem com uma API externa.
Encerrar após commit e antes da impressão pode perder a visualização da resposta;
o resultado fica armazenado e a duplicata não o reenvia. Integrações futuras
precisarão de estratégia de entrega/outbox e confirmação própria, autorizadas
em outra fase. Também dependemos de SQLite/OS/volume funcionando corretamente;
não se alega proteção contra falha física de hardware ou perda do disco.

## 6. Idempotência, concorrência e ordenação

A chave é **conversa + ID de evento** por toda a vida do arquivo. O hash detecta
reuso com texto diferente. Mesmo texto com novo ID é evento distinto; outra
conversa pode usar o mesmo ID. Chaves não são removidas por reset ou exclusão.
Esta é uma evolução deliberada da Fase 1: o modo em memória mantém deduplicação
por sessão; o modo persistente impede replay mesmo depois de limpar conteúdo.

A versão é incrementada em cada alteração de negócio. SQLite serializa commits
entre processos; um snapshot vencido é rejeitado integralmente. Serviço e ações
administrativas fazem no máximo **3 tentativas totais**, relendo estado/contexto.
Cada lock SQLite tem timeout configurado (1000 ms padrão); não há retry infinito.
Conflitos esgotados retornam erro explícito para reenviar o mesmo ID. A mensagem
não é descartada como se tivesse sido aceita.

Ordem determinística observável: sequência de commits e ordinais do histórico,
não FIFO por chegada, timestamp ou prioridade de operador. Se a intervenção
humana confirma primeiro, a tentativa automática em andamento é invalidada;
na releitura, registra apenas a mensagem na conversa pausada. Se uma resposta
confirma antes da intervenção, essa resposta pertence ao estado anterior.

WAL, foreign keys e `synchronous=FULL` são configurados. Lock de arquivo Linux
coordena somente criação/migrações, com espera limitada pelo timeout; não protege
os efeitos de atendimento. Não se depende de locks Python para consistência
entre processos. A limitação de um escritor por vez do SQLite permanece.

## 7. Conhecimento, IA, Booksy e pausa humana

Catálogos e prompts continuam em arquivos versionados; não foram duplicados no
banco. O catálogo inicial é vinculado por hash ao ensaio SQLite para impedir
troca silenciosa ao reiniciar. Revisões/aprovação não mudam na migração.
Autoria programática em `VersionedKnowledgeRepository` continua em memória;
não transforma modificações em um catálogo durável. A CLI não oferece autoria.
Um ensaio com outro catálogo requer outro banco sintético. Autoria durável e
migração controlada desses hashes precisam de decisão própria no futuro.

Continuam ativos: dados pendentes/rejeitados/arquivados bloqueados, fatos
relevantes aprovados, validação da candidata contra campos/templates autorizados,
restrições a comandos/injection, prompts versionados e histórico mínimo.
A suíte nova também exercita essas barreiras com commits SQLite reais.

FakeAIProvider é determinístico e offline. Conflito pode repetir cálculo local
antes do commit, limitado a três tentativas. Um adaptador real futuro precisará
considerar custo/timeout e efeitos externos dessas retentativas; não foi testado
um LLM nem criado cliente de rede. O timeout do fake continua simulado.

Estados AUTO, WAITING_HUMAN, HUMAN_ACTIVE e RESOLVED são persistidos, assim como
transições. Nenhum estado pausado chama IA ou responde automaticamente; somente
operação administrativa autorizada permite retomada após RESOLVED. O operador
continua fictício/local; ninguém real é notificado e não existe inbox.
Booksy permanece somente link configurado/aprovado, sem vagas ou reservas.

## 8. Auditoria, retenção e exclusão

Auditoria registra criação, processamento, duplicata, transições, resposta do
operador, reset, exclusão e falha/rollback quando possível. Não contém textos,
prompts ou identificador de cliente em claro; entidade é hash e cada registro
recebe correlação. Hash de um ID não é anonimização resistente a adivinhação.
Logs operacionais de armazenamento contêm apenas eventos estáticos, separados
da auditoria no banco. Transições e provenance também ficam recuperáveis.

Política **de ensaio sintético**, configurável, sem interpretação legal:

- IA: até 4 mensagens relacionadas de 500 caracteres, como na Fase 2.
- Banco: últimas 100 entradas de histórico e 100 transições por conversa.
- Conteúdo de conversa inativa: elegível após 7 dias, pela última atualização.
- Auditoria técnica: 30 dias e no máximo 10000 entradas por arquivo; poda nas
  próximas operações auditadas, sem agendador.
- Capacidade: 100 conversas, 1000 eventos por conversa incluindo resets,
  limite de páginas de 64 MiB e verificação de admissão banco/WAL. A quota não é
  absoluta para todo o volume; WAL pode precisar de espaço transitório adicional.

Exclusão de conteúdo e aplicação da retenção exigem comandos confirmados:

```text
/reset CONFIRM_SYNTHETIC_RESET
/delete DELETE_SYNTHETIC:cliente-a
/purge PURGE_SYNTHETIC
```

Reset exige confirmação também em memória. No SQLite limpa histórico/contexto,
mas mantém eventos/resultados. Delete/purge removem histórico, transições,
contexto e texto/referências das respostas nos resultados, desativando a conversa.
Mantêm IDs fictícios, hash de mensagens, metadados mínimos/resultados sem texto e
auditoria limitada, protegendo a deduplicação. Conversa excluída não é reaberta.
Não são apagados tombstones para liberar espaço silenciosamente; ao atingir
capacidade, novos dados são recusados. `/purge` só roda sob solicitação explícita.

`secure_delete` fica ligado, mas não há promessa de apagamento forense, exclusão
de backups/WAL ou criptografia nativa em repouso. IDs e hashes residuais significam
que isto não é exclusão integral de dados pessoais. LGPD/base legal/retenção real
continuam pendências do responsável, sem alegação de conformidade jurídica.

## 9. Segurança e armazenamento

Caminho explícito com sufixo `.synthetic.sqlite3`, sem symlinks, marca interna e
application_id identificam o escopo. Bancos estrangeiros, versões futuras,
permissão insegura e configuração inválida são rejeitados. O escopo é revalidado
em novas conexões, inclusive antes de manutenção; trocar a marca após abertura
não autoriza apagar conteúdo. Não se sobrescreve ou migra banco desconhecido.
SQL recebe parâmetros; interpolação só aparece em pragmas numéricos confiáveis.
Banco, WAL, SHM, journal e lock de inicialização são excluídos do versionamento.

Uma verificação real no volume do projeto observou que `chmod 0600` manteve
`0644`. O adaptador passou a recusar esse volume e exige permissão efetiva `0600`
e proprietário atual. Demonstrações/testes usam arquivo privado em `/tmp`.
O volume do código não foi usado para guardar conversas. O ensaio inicial do
volume criou apenas banco vazio temporário, removido com seu diretório de teste.

O ambiente de referência é Linux; há uso explícito de flock/O_NOFOLLOW. Não foi
validado Windows, volume de rede, criptografia, backup/restauração operacional,
alta disponibilidade ou segurança contra desenvolvedor com acesso ao disco.
Arquivo em `/tmp` pode ser limpo pelo sistema; persistência após reinício do
processo não significa proteção contra essa limpeza ou reinicialização do host.

## 10. Execução e migrações reproduzíveis

Consulte também [README](../README.md), com todas as variáveis STORAGE_.
Inicialização/migração é automática ao abrir um banco novo ou reconhecido v1:

```sh
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture --database /tmp/rocha-reis-local/dev.synthetic.sqlite3
```

A execução seguinte usa o mesmo comando e `/use cliente-a`; não repita `/start`
para uma conversa existente. Alternativa de configuração:

```sh
STORAGE_BACKEND=sqlite STORAGE_PATH=/tmp/rocha-reis-local/dev.synthetic.sqlite3 /tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture
```

Os scripts [início](../examples/phase3.start.demo.txt) e
[retomada](../examples/phase3.resume.demo.txt) são executados em processos separados
com o mesmo caminho. Use um caminho novo para repetir o ensaio desde o início.
Não remova banco existente de outra tarefa para executar uma demonstração.

Migrações 001/002 executam declarações individuais numa transação explícita,
sem COMMIT implícito de executescript. O teste cria v1 real com auditoria e
verifica atualização para v2 preservando o registro. Schema futuro não sofre
downgrade. Falha ao criar/migrar impede inicializar; um arquivo incompleto não é
apagado automaticamente. Sua recuperação exige inspeção local do responsável.

## 11. Testes, falhas encontradas e correções

Comandos executados:

```sh
/tmp/rocha-reis-agent-venv/bin/python -m pip check
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q tests/test_sqlite_persistence.py
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q tests/test_sqlite_failures_and_concurrency.py
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q tests/test_sqlite_restart_and_retention.py
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q tests/test_sqlite_ai_regressions.py
```

Resultados intermediários: modelagem/integração **33 aprovados em 1.03s**;
rollback/concorrência **13 aprovados em 0.74s**; conjunto SQLite então existente
**64 aprovados em 8.99s**; suíte completa **245 aprovados em 14.39s** e depois
**254 aprovados em 10.04s**. O fechamento abaixo registra a última suíte, cópia
limpa, CLI e HTTP, após o teste adicional de escopo antes de exclusão.

As primeiras execuções dos novos testes tiveram 31 aprovados/2 falhas por
ResourceWarnings de conexões auxiliares não fechadas e 9 aprovados/1 falha por
expectativa incorreta de duplicata. Os helpers passaram a fechar conexões
explicitamente e a expectativa foi corrigida para o evento já existente.
Avisos continuam tratados como erros; não foram silenciados ou testes removidos.

A revisão também identificou e corrigiu: necessidade de preservar chaves no
reset durável, revalidar escopo na manutenção, limitar lock de inicialização,
rejeitar volume sem 0600 e confirmar reset na CLI. Não houve falha na linha de
base nem remoção de cobertura das fases anteriores.

| Requisitos de teste do anexo | Evidência |
|---|---|
| 1–8: criação, mensagens, contexto, estados, histórico, FK, inválidos, unicidade | `test_sqlite_persistence.py` |
| 9–14: recuperação real, contexto, estados humanos, dedup, auditoria, interrupção | `test_sqlite_restart_and_retention.py` e worker em subprocessos |
| 15–20: commit, rollback, escrita parcial, integridade, interrupção e falha | `test_sqlite_failures_and_concurrency.py`, teste abrupto e banco indisponível |
| 21–26: mesma/outra conversa, duplicatas, contexto concorrente, humano, bloqueio | barreiras/events em `test_sqlite_failures_and_concurrency.py`, dois processos em snapshot concorrente |
| 27–32: isolamento, logs, credenciais, exclusão e escopo | testes de persistência/retenção, fixture de rede/ambiente isolados |
| 33–40: fake, factualidade, aprovação, prompt, Booksy, CLI, saúde, pausa/retomada | regressões anteriores preservadas e `test_sqlite_ai_regressions.py`/reinício/FastAPI |

As barreiras/eventos controlam concorrência, sem sleeps como prova de ordenação.
Workers independentes também bloqueiam rede. Há arquivos temporários reais,
integrity_check e foreign_key_check; não se depende somente de mocks.
Timeout de banco ocupado é exercitado com lock SQL real e três tentativas.
Não foi simulado esgotamento físico do disco: teste de limite de admissão e
falhas de escrita injetadas demonstram rollback, sem alegar teste de hardware.

## 12. Demonstração de reinício e deduplicação

O teste de integração executa quatro processos CLI e um processo de inspeção:

1. Cria conversa, consulta corte, pede humano e encerra em WAITING_HUMAN.
2. Outro processo recupera contexto `corte`, quatro entradas, dois eventos e
   auditoria `state_client_request`.
3. Nova CLI registra mensagem pausada sem resposta, reconhece evento humano
   duplicado e assume atendimento; encerra em HUMAN_ACTIVE.
4. Outra CLI preserva pausa em HUMAN_ACTIVE/RESOLVED; somente após resume responde
   à pergunta de preço com R$ 45,00 fictícios e referências/prompt v1.
5. Mais uma execução reenvia o evento de preço: duplicata, sem nova resposta.

O teste de dois workers abre o mesmo snapshot e os libera por pipes: somente
um resultado responde; o segundo detecta conflito, relê e retorna duplicata.
Banco final contém um evento e duas entradas de histórico. Threads independentes
cobrem commits concorrentes na mesma conversa, em conversas distintas e evento
repetido; atualização vencida não sobrescreve contexto já confirmado.

## 13. Avaliação objetiva e limites

| Pergunta | Resposta verificada |
|---|---|
| Preserva conversa após reinício? | Sim, entre processos independentes usando o mesmo arquivo |
| Recupera contexto sem misturar clientes? | Sim, FK/chaves e snapshots por conversa; testes de isolamento |
| Mantém automático suspenso no atendimento humano? | Sim, WAITING/HUMAN_ACTIVE/RESOLVED persistidos até resume explícito |
| Evita duplicidade após reinício? | Sim, evento/resultado persistidos e chave única; sem nova resposta |
| Mantém consistência diante de falhas? | Sim nos pontos de falha testados e interrupção antes do commit; não promete contra perda física do disco |
| Trata concorrência com garantias explícitas? | Sim, commits serializados + versão otimista, três tentativas e erro controlado; não FIFO de chegada |
| Persistência isolada do domínio? | Sim, por ConversationRepository; SQL somente no adaptador |
| Preparado para provedor real futuro? | Fronteiras existem e geração não prende banco; custo, timeout real, avaliação de LLM e entrega externa ainda exigem trabalho/autorização |
| O que impede clientes reais? | Dados não aprovados, ausência de Meta/inbox/operação, auth remota, privacidade/retenção real, backup, observabilidade e homologação |
| Prioridade seguinte? | Planejar integração oficial Meta com autenticidade/idempotência e entrega controlada; somente após autorização específica |

A implementação reescreve a pequena janela de histórico/transições por commit,
carrega os eventos limitados da conversa e usa JSON para contexto/resultado.
É adequada ao ensaio limitado, sem evidência de carga/escala de produção.
SQLite é um único escritor; conflitos podem exigir reentrega explícita.
Tombstones ocupam vagas e são mantidos para segurança de replay; não há limpeza
automática de todo o arquivo ou migração entre arquivos com deduplicação global.

Não há avaliação de LLM real, API paga, credencial, WhatsApp, consulta Booksy,
inbox, conta externa, banco de produção, implantação ou teste com clientes reais.
Dados oficiais, link, políticas, operação humana, orçamento, hospedagem e LGPD
continuam pendentes em `DADOS_PENDENTES.md`, sem valores presumidos.

## 14. Estado atual

| Categoria | Estado |
|---|---|
| PLANEJADO | Fases 4 a 6, avaliação/integração de modelo real, operação e produção |
| IMPLEMENTADO | Persistência SQLite sintética, migrações, transações, idempotência, concorrência, auditoria, manutenção e CLI/composição HTTP |
| TESTADO LOCALMENTE | Arquivos reais, subprocessos, concorrência, falhas, regressões e verificações do fechamento |
| VALIDADO COM O CLIENTE | Não; dados e operação aguardam aprovação do salão |
| IMPLANTADO | Não |

A conclusão local depende dos resultados finais abaixo. Não significa prontidão
para produção nem autorização para iniciar a Fase 4.

## 15. Próximo prompt recomendado — Fase 4

```text
Execute exclusivamente a Fase 4 — Preparação da integração oficial Meta.
Leia todo o repositório, AGENTS.md, documentos e relatórios das Fases 0 a 3.
Execute os testes existentes e apresente diagnóstico, plano curto, arquivos,
garantias de entrega/idempotência e requisitos ainda pendentes.

Implemente primeiro o adaptador oficial e testes locais/offline: contratos de
webhook, verificação e autenticidade, normalização de eventos, limites de entrada,
e isolamento do canal. Consulte documentação oficial vigente ao implementar.
Preserve SQLite por interfaces, catálogo aprovado, FakeAIProvider, prompts,
validação factual, pausa humana, CLI e /health. Considere explicitamente o intervalo
entre commit e envio: implemente entrega persistente/outbox mínima se necessária,
com estados verificáveis, retry limitado e sem afirmar exatamente uma vez externo.

Use fixtures sintéticas e transporte de saída fake. Não envie mensagens reais,
solicite credenciais ou conecte conta/número sem autorização específica do
responsável para esse ensaio e definição dos contatos permitidos. Se faltar essa
autorização, conclua as partes offline e registre o que falta para teste real.
Não integrar IA real, inbox operacional, Booksy API ou produção. Booksy permanece
apenas link aprovado. Não importar dados pessoais, criar contas, comprar serviços,
implantar ou iniciar a Fase 5.

Teste assinatura inválida, replay, duplicata, payload inválido, concorrência,
reinício, falha de envio, limites/retries e pausa humana durante processamento.
Registre arquivos/decisões/comandos/resultados reais, pendências e limitações,
separando planejado, implementado, testado localmente, validado com cliente e
implantado. Não exponha tokens, corpos privados ou prompts em logs.
```

## 16. Fechamento e evidências finais

Após todas as alterações de código, resultados finais observados:

| Verificação | Resultado real |
|---|---|
| Suíte completa no ambiente de trabalho | **255 passed in 10.95s**, 0 falhos, 0 ignorados |
| `pip check` no ambiente de trabalho | **No broken requirements found.** |
| Cópia limpa com outro ambiente Python | **255 passed in 9.70s**, 0 falhos, 0 ignorados |
| `pip check` no ambiente independente | **No broken requirements found.** |
| Novos casos da Fase 3 | **66**, somados aos 189 anteriores preservados |
| Scripts CLI de início/retomada em processos distintos | Ambos com código **0**, estado humano recuperado e contexto retomado |
| Terceira CLI reenviando evento de preço | Código **0**, duplicata sem nova resposta |
| HTTP real com SQLite configurado | **200**, corpo `{"status":"ok"}` |
| Encerramento do Uvicorn temporário | Confirmado; nenhum servidor desta tarefa ficou ativo |
| Volume do projeto sem 0600 efetivo | Recusado, conforme verificação real esperada |
| Auditoria de fontes | **PASS**, 81 arquivos; 14 criados, 17 modificados, nenhum removido |

Comandos finais no ambiente de trabalho:

```sh
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q
/tmp/rocha-reis-agent-venv/bin/python -m pip check
```

A cópia limpa ficou em `/tmp/rocha-reis-phase3-final-repro-5trem2ng`, com fontes,
testes, fixtures, exemplos, conhecimento, prompts, documentos e configuração.
Sem `.env`, banco local ou caches copiados. Foi usado o venv independente já
existente da Fase 0; não houve instalação nova ou download:

```sh
# Executados a partir da cópia limpa:
/tmp/rocha-reis-phase0-repro-y3ei0spd/venv/bin/python -m pip check
/tmp/rocha-reis-phase0-repro-y3ei0spd/venv/bin/python -m pytest -q
```

A reprodução cobre Python/Linux/SQLite deste host, sem matriz de outros sistemas
ou teste de nova instalação. Resultado: `FINAL_CLEAN_REPRODUCTION=PASS`.

Além dos testes, os scripts de exemplo foram executados por subprocessos reais,
com banco novo em `/tmp/rocha-reis-phase3-demo-lo4pk3bs/demo.synthetic.sqlite3`:

```sh
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture --database /tmp/rocha-reis-phase3-demo-lo4pk3bs/demo.synthetic.sqlite3 < examples/phase3.start.demo.txt
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.simulator --fixture --database /tmp/rocha-reis-phase3-demo-lo4pk3bs/demo.synthetic.sqlite3 < examples/phase3.resume.demo.txt
```

As transcrições estão nessa pasta temporária, em `phase3.start.demo.txt.output`,
`phase3.resume.demo.txt.output` e `third-restart.output`. Trechos verificados:

```text
Primeiro processo: Estado: WAITING_HUMAN; contexto=corte
Segundo processo: [automático pausado; mensagem registrada]
Segundo processo: [duplicata ignorada]
Segundo processo: Estado: HUMAN_ACTIVE
Segundo processo: Estado: RESOLVED
Segundo processo: Estado: AUTO
Segundo processo: Corte de teste: R$ 45,00. Valor fictício...
Segundo processo: evento=preco; prompt=v1; fatos=price:corte@1,service:corte@1
Segundo processo: state_resume: committed
Terceiro processo, evento preco: [duplicata ignorada], sem preço emitido novamente
```

Um Uvicorn temporário foi iniciado em loopback com `STORAGE_BACKEND=sqlite` e
outro arquivo sintético privado. Consulta HTTP pela biblioteca padrão sem proxy
confirmou saúde; o processo foi encerrado em `finally`.
`LIVE_HEALTH=200`, `SQLITE_CONFIGURED=True`, `LOCAL_SERVER_STOPPED=True`.

A inspeção final conferiu sintaxe AST Python, JSON, destinos de links Markdown,
padrões comuns de credenciais, ausência de `.env`/bancos no projeto e hashes da
linha de base. Confirmou dependências, relatórios das Fases 0–2, requisitos,
dados pendentes, AGENTS.md, catálogo oficial vazio, fixture versionada, prompts e
endpoint de saúde preservados. É inspeção pontual, não auditoria completa de
segurança ou prova de ausência de todas as classes de segredo.

Os critérios locais de persistência, modelo/contratos, recuperação, deduplicação,
concorrência, constraints, transações/rollback, auditoria, manutenção, CLI/HTTP,
conhecimento/fake/validação, execução sem serviços/credenciais e documentação
foram verificados nas evidências acima. **A Fase 3 está concluída localmente**,
com os limites declarados neste relatório. Não validada com clientes, não
implantada e sem executar a Fase 4.
