# Relatório técnico — Fase 4 (auditoria + integração Meta offline)

**Data:** 19/11/2026 · **Executor:** Claude (continuidade do trabalho iniciado com Codex)
**Escopo:** auditoria das Fases 0–3 e preparação da integração WhatsApp Cloud API
**sem** conexão real, credenciais reais ou envio. Fase 5 não iniciada.

| Categoria | Estado |
|---|---|
| PLANEJADO | Adaptador Graph API real, worker contínuo supervisionado, ensaio com número autorizado, Fases 5–6 |
| IMPLEMENTADO | Webhook GET/POST, assinatura, normalização, inbox/outbox, processador, despachante, transporte fake, CLI local, correção do armazenamento |
| TESTADO LOCALMENTE | 372 testes (255 anteriores + 117 novos), 0 falhas, 0 ignorados; demonstração offline em banco durável |
| VALIDADO COM CLIENTE | Não |
| IMPLANTADO | Não |

---

## A. Auditoria geral

### 1. Diagnóstico do projeto recebido

Diretório confirmado: `/media/heitordino/1TB/ROCHA_REIS_WHATSAPP` (sem `.git`, sem
`CLAUDE.md`; a skill `rocha-reis-development` citada em `AGENTS.md` não existe em
nenhum diretório de skills encontrado). Foram lidos integralmente todos os arquivos
de código (`rocha_reis_agent/**`, 2.336 linhas), os 19 arquivos de teste, migrações,
fixtures, prompts, conhecimento, exemplos, configuração, `AGENTS.md`, `README.md` e
os documentos-fonte. Os relatórios `STATUS_FASE_0/1/2.md` foram lidos por seções
(diagnóstico, limitações, estado); `STATUS_FASE_3.md`, nas seções de garantias,
segurança e limites. Não lidos como fonte: `.pytest_cache`, venvs em `/tmp`,
bancos binários. Nenhum `.env` com credenciais existia.

Suíte inicial: **255 aprovados em 8,12 s**, `pip check` sem conflitos.

### 2. Estrutura e arquitetura encontradas

Pacote único com camadas razoavelmente separadas: `domain/` (contratos, estado),
`application/` (orquestração `AgentService`, sessões, handoff, portas), `ai/`
(contratos, fake, prompts, validação), módulos de conhecimento/regras na raiz,
`persistence/sqlite.py` (adaptador) e `api/` (FastAPI só com `/health`).
Fluxo real: `InboundMessage` → `AgentService.handle` (dedup por conversa+evento,
barreira de estado humano, catálogo aprovado, `FakeAIProvider`, validação factual
contra templates) → commit otimista da conversa. A IA roda fora de transação de banco.

### 3. Qualidade das Fases 0–3

Verificado no código e por testes, não só nos relatórios:
- **Fase 0:** configuração tipada que falha sem expor valores; erro 500 genérico; `/health`. Correta.
- **Fase 1:** máquina de estados AUTO → WAITING_HUMAN → HUMAN_ACTIVE → RESOLVED → AUTO só por ação explícita; texto de cliente nunca vira comando. Correta.
- **Fase 2:** saída do provedor é proposta revalidada contra resposta reconstruída por templates; referências e escopo conferidos; Booksy só por link validado. Desenho conservador e bem testado.
- **Fase 3:** snapshots com versão otimista, `BEGIN IMMEDIATE`, WAL, `synchronous=FULL`, constraints STRICT, rollback testado em quatro pontos, `os._exit` entre processos, concorrência entre processos. As afirmações do relatório conferem com o código.

Pontos fracos: `AgentService.handle` é longo; `build_agent` usa `isinstance` para
vincular o catálogo ao SQLite; módulos de domínio na raiz do pacote. Nada disso
justificou refatoração.

### 4. Problemas de segurança/infraestrutura encontrados

Nenhum problema CRÍTICO ou ALTO foi encontrado no código.

| Sev. | Problema | Evidência | Situação |
|---|---|---|---|
| MÉDIO | Documentação recomendava `/tmp` para o banco, mas aqui `/tmp` é tmpfs (sandbox flatpak) e se perde ao reiniciar o sistema. As demonstrações da Fase 3 não provam durabilidade após reboot | `findmnt -T /tmp` → `tmpfs` | **Corrigido** (README, `.env.example`, ADR-040) |
| MÉDIO | Limites duráveis de 1000 eventos por conversa (incluindo resets) e 100 conversas por arquivo. Com o canal real, o cliente que ultrapassar ficaria bloqueado para sempre (`rejected_by_core`) | `sqlite.py:_validate`, `SIM_MAX_*` | **Não corrigido**: exige política de retenção. Registrado em pendências |
| MÉDIO | Autorização de operador é a string fixa `operador-local`, sem autenticação. Aceitável só na CLI local | `handoff.py` | Mantido; teste garante que não há rota administrativa HTTP |
| BAIXO | Ao recusar o volume vfat, o adaptador deixava no volume inseguro um banco já com schema (68 KB) e o `.init.lock` | reprodução manual | **Corrigido**: `fstat` do lock antes de criar o banco; teste |
| BAIXO | O diretório pai do banco é criado com umask padrão (0755). Só o arquivo é verificado como 0600 | `sqlite.py:_initialize` | Recomendação: usar diretório 0700 (documentado) |
| BAIXO | Cada commit reescreve a janela de histórico e carrega todos os eventos da conversa (custo O(n)) | `sqlite.py:_write/_load` | Aceitável no MVP; rever com volume real |
| INFO | Com o canal ativo, logs de acesso do servidor vazariam o Verify Token (vai na query do GET) | protocolo Meta | O comando documentado já usa `--no-access-log`; agora consta como obrigatório |

**Causa do problema de permissões:** `/media/heitordino/1TB` está montado como
**vfat (FAT32)** com `fmask=0022,dmask=0022,uid=1000`. O FAT não armazena modo POSIX
nem dono; o kernel deriva `0644`/`0755` das máscaras, `chmod` retorna sucesso sem
efeito, symlinks não existem e não há journaling. A validação do adaptador **não**
foi enfraquecida. O código continua no volume e o banco durável vai para
`~/.local/share/rocha-reis-agent/` (ext4, diretório `0700`, arquivo `0600`, testado).

### 5. Problemas arquiteturais

- A unidade de trabalho do SQLite cobria só conversas. Sem extensão, não haveria
  como gravar a outbox junto com a resposta. **Resolvido** com `stage_effect` (ADR-033).
- ADR-027 já registrava que não havia fila durável nem retomada de mensagens. **Resolvido para o canal** (inbox).

### 6. Problemas de persistência

Além dos itens acima: retenção/exclusão não cobria dados de canal (não existiam).
`_retire_keys` agora apaga texto e endereço da inbox/outbox da conversa e suprime
envios pendentes. Itens UNKNOWN/SENDING mantêm o endereço para reconciliação.

### 7. Correções realizadas

1. Verificação de permissão antes de criar banco/lock (`persistence/sqlite.py`).
2. Documentação de armazenamento (README, `.env.example`, ADR-040).
3. Exclusão sintética estendida às tabelas de canal.
4. `conftest.py` isola também as variáveis `WHATSAPP_`/`CHANNEL_`.

### 8. Problemas conhecidos não corrigidos

Limites de capacidade (MÉDIO), autenticação de operador inexistente (MÉDIO, fora
do escopo até a Fase 5), permissão do diretório pai (BAIXO), custo O(n) por commit (BAIXO).

---

## B. Desenvolvimento da Fase 4

### 9. Funcionalidades implementadas

Webhook GET de verificação e POST autenticado. Normalização de texto, tipos não
suportados e status, inclusive remetente só com BSUID. Inbox durável com
deduplicação e conflito. Processador integrado ao núcleo existente. Outbox
transacional com estados, leases, retry limitado, UNKNOWN e reconciliação.
Supressão por mudança de estado ou janela de 24h. Transporte fake com 7
comportamentos. CLI local. Configuração tipada com validação de segredos.

### 10. Arquivos

**Criados:** `rocha_reis_agent/channels/{__init__,contracts,meta,service,fake_transport,local_cli}.py`,
`rocha_reis_agent/api/whatsapp.py`, `rocha_reis_agent/persistence/sqlite_channel.py`,
`rocha_reis_agent/persistence/migrations/003_whatsapp_channel.sql`,
`fixtures/meta/*.synthetic.json` (6) + `README.md`,
`tests/{whatsapp_support,channel_worker}.py`,
`tests/test_whatsapp_{webhook,parser,processing,outbox,security}.py`,
`docs/INTEGRACAO_META_FASE4.md`, `docs/STATUS_FASE_4.md`.

**Modificados:** `persistence/sqlite.py` (schema v3, efeitos encenados,
`write_transaction`, checagem de permissão antecipada, exclusão do canal),
`application/service.py` (parâmetro `effects`), `config.py`, `bootstrap.py`,
`main.py`, `.env.example`, `README.md`, `tests/conftest.py`,
`tests/test_sqlite_persistence.py` (versão do schema), `tests/test_sqlite_restart_and_retention.py`
(isolamento de ambiente), `docs/{ARQUITETURA_E_DECISOES,PLANO_DE_EXECUCAO,CONTEXTO_MESTRE,DADOS_PENDENTES}.md`.
Relatórios das Fases 0–3 preservados sem alteração.

### 11. Contratos e interfaces

`InboundTextMessage`, `InboundUnsupported`, `StatusUpdate`, `Sender(wa_id|user_id)`,
`NormalizedBatch`, `IngestReport`, `OutboundText`, `SendAccepted`/`SendRejected(retryable)`/`SendUncertain`,
`WhatsAppTransport` (Protocol). Gancho do núcleo: `AgentService.handle(message, *, effects)`.
Persistência: `SQLiteChannelStore` (ingest, claim/finish/retry inbound,
`stage_conversation_effects`, `apply_status`, claim/finish outbound, `requeue_unknown`).

### 12. Fluxo completo

Ver diagrama na seção 1 de [`INTEGRACAO_META_FASE4.md`](INTEGRACAO_META_FASE4.md).

### 13. Persistência e idempotência

Deduplicação em duas camadas: inbox por `número + hash(wamid)` (e `+status` para
status), com digest para conflito; núcleo por `conversa + event_id`. Conclusão da
inbox, efeitos da conversa e outbox no mesmo commit, com `UNIQUE(source_inbox_id)`.
Garantia: cada mensagem recebida gera **no máximo uma** linha de outbox; processamento
interrompido antes do commit é refeito sem efeito parcial.

### 14. Envio e recuperação

SENDING confirmado antes do transporte. Lease vencido em SENDING → UNKNOWN.
Retry automático só quando o adaptador garante não envio ou há rejeição explícita
reprocessável (backoff exponencial, máximo de 3 tentativas). Incerto → UNKNOWN →
reconciliação por status ou decisão humana confirmada. "ACCEPTED" (requisição aceita
pela Meta) é distinto de `delivery_status` (sent/delivered/read/failed).

### 15. Decisões e justificativas

ADR-033 a ADR-042 em [`ARQUITETURA_E_DECISOES.md`](ARQUITETURA_E_DECISOES.md). As principais:
mesmo arquivo SQLite para ter atomicidade real sem coordenação distribuída;
nenhum reenvio cego, porque a referência da Meta consultada não oferece chave de
idempotência para envio; supressão no despacho, porque o operador pode assumir
depois do commit; somente transporte fake, por restrição explícita da fase.

---

## C. Validação

### 16. Comandos executados (resultados reais)

```text
findmnt -T /media/heitordino/1TB/ROCHA_REIS_WHATSAPP → vfat, fmask=0022,dmask=0022
findmnt -T /tmp → tmpfs ;  findmnt -T $HOME → ext4
/tmp/rocha-reis-agent-venv/bin/python -m pip check → No broken requirements found.
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q → inicial 255 passed; final 372 passed in 17.77s
Mutação temporária de 5 salvaguardas (lease→UNKNOWN, checagem de estado, assinatura,
  exceção→incerto, inbox no mesmo commit) → cada uma derrubou ao menos 1 teste; código restaurado
python -m rocha_reis_agent.channels.local_cli post|work|status (demonstração abaixo)
```

### 17–19. Testes

| Conjunto | Inicial | Final |
|---|---|---|
| Fases 0–3 (inalterados, exceto a versão esperada do schema e o isolamento de ambiente) | 255 aprovados | 255 aprovados |
| Novos da Fase 4: webhook 48, parser 29, processamento 13, outbox 20, segurança 7 | — | 117 aprovados |
| **Total** | 255 / 0 falhas / 0 ignorados | **372 / 0 falhas / 0 ignorados** (3 execuções consecutivas) |

Durante o desenvolvimento, três falhas reais foram corrigidas, todas no código de
teste: uma conexão SQLite não fechada gerava `ResourceWarning` intermitente;
asserções de log incluíam o logger do cliente HTTP de teste; um payload sem
`entry.id` era (corretamente) ignorado, e não rejeitado.

Cobertura dos 44 itens exigidos: webhook 1–10 (`test_whatsapp_webhook.py`);
mensagens 11–17 (webhook/processing/outbox, reinício com subprocesso);
processamento 18–24 (`test_whatsapp_processing.py`); outbox 25–34
(`test_whatsapp_outbox.py`); regressão 35–44 (suíte anterior +
`test_whatsapp_security.py`). Limite: payloads sintéticos baseados na documentação,
não capturados de webhooks reais. Passar nestes testes não valida a segurança
contra todo payload possível.

### 20–25. Demonstração offline (cada comando é um processo novo; banco em `$HOME`, ext4, 0600)

```text
== 1. recebimento + resposta + envio simulado
webhook: HTTP 200
etapas: replied, ACCEPTED
[ENVIO SIMULADO — nada saiu desta máquina] comportamento=accept: [SIMULAÇÃO — DADOS FICTÍCIOS] Corte de teste: Serviço fictício para validar o simulador.
== 2. reentrega idêntica (deduplicação)
webhook: HTTP 200
etapas: nada pendente
== 3. contexto (seguimento)
etapas: replied, ACCEPTED
[ENVIO SIMULADO ...] Corte de teste: R$ 45,00. Valor fictício, exclusivo desta demonstração.
== 4. Booksy só por link
[ENVIO SIMULADO ...] O agendamento deve ser realizado por você no Booksy: https://example.invalid/booksy-sintetico . Não consultei disponibilidade nem realizei ou confirmei reserva.
== 5. tipo não suportado
etapas: unsupported_type_ignored
== 6. pedido humano + mensagem durante pausa
[ENVIO SIMULADO ...] Registrei o pedido nesta simulação e pausei as respostas automáticas. Nenhum atendente real foi notificado.
etapas: recorded_without_reply
== 7. estado
{"inbox": {"DONE": 6}, "outbox": {"ACCEPTED": 4}}
== envio incerto (timeout) e nova execução
etapas: replied, UNKNOWN
etapas: nada pendente
{"inbox": {"DONE": 7}, "outbox": {"ACCEPTED": 4, "UNKNOWN": 1}}
== segredo fraco → "Erro: Configuração inválida..." (exit 2), sem exibir o valor
```

A recuperação após morte abrupta do processo **durante** o envio é demonstrada por
`test_real_process_restarts_webhook_processing_crash_during_send_and_recovery`
(`os._exit(75)` → outro processo → `UNKNOWN`, 0 reenvios, `integrity_check=ok`).
O banco de demonstração ficou em `~/.local/share/rocha-reis-agent/phase4-demo.synthetic.sqlite3`
(dados fictícios; pode ser apagado). Persistência após **reboot do sistema** não
foi executada nesta sessão; o que muda em relação à Fase 3 é que o arquivo agora
está em ext4 persistente, não em tmpfs.

---

## D. Estado do produto

### 26–28. Funcionalidades

- **Concluídas (local):** tudo da seção 9; Fases 0–3 preservadas.
- **Parciais:** reconciliação por `biz_opaque_callback_data` (formato real a
  confirmar); envio para BSUID (só o payload); worker apenas sob demanda (CLI), sem daemon.
- **Não implementadas:** adaptador Graph API real; templates e mensagens fora da
  janela; mídia; marcação como lida; limite de taxa no webhook; painel/inbox humano;
  alertas; IA real.

### 29. Pendências de dados oficiais do salão

Inalteradas: link Booksy, serviços/preços, endereço, horários, profissionais,
políticas, FAQs, tom de voz. Novas pendências Meta em [`DADOS_PENDENTES.md`](DADOS_PENDENTES.md).

### 30–31. Riscos e limitações de infraestrutura

Volume de 1 TB impróprio para dados (vfat). SQLite com um único escritor, sem
cifragem nem backup. Limites de capacidade sintéticos. Janela residual de corrida
humano×envio (uma chamada HTTP). Comportamento real da Graph API não verificado.
Respostas com rótulos de simulação (proposital). Ausência de `.git`, ou seja, sem
histórico de mudanças.

### 32. Requisitos para conectar a Meta

Checklist da seção 11 de [`INTEGRACAO_META_FASE4.md`](INTEGRACAO_META_FASE4.md).

### 33. Prontidão técnica

Pronto para um **ensaio controlado** depois de implementar o adaptador Graph,
com número de teste autorizado e contatos restritos. **Não** pronto para clientes
reais: faltam dados aprovados, textos finais, fluxo humano real, segredos geridos,
hospedagem durável e revisão LGPD.

### 34. Recomendação

Antes da Fase 5: (1) inicializar Git; (2) obter autorização para um ensaio Meta
com número de teste e implementar/testar `GraphAPITransport` como etapa 4b; (3)
definir retenção e capacidade. A Fase 5 (atendimento humano operacional) depende
de escolher a ferramenta de inbox do salão, pendência listada em `DADOS_PENDENTES.md`.

### 35. Prompt sugerido para a próxima fase

```text
Execute exclusivamente a Fase 5 — Atendimento humano operacional — somente se o
responsável tiver definido a ferramenta/processo de inbox humano do salão.
Leia AGENTS.md, todos os documentos e STATUS_FASE_0..4.md; rode a suíte (372
testes esperados) e relate o resultado antes de editar.

Objetivo: fazer o handoff deixar de ser só estado no banco. Integre a transição
WAITING_HUMAN a um canal de notificação do responsável definido pelo usuário,
com autenticação real de operadores (substituindo "operador-local"), trilha de
auditoria por operador, e ações assumir/responder/resolver/retomar autorizadas.
Respostas do operador devem sair pela outbox existente, com a mesma política de
janela, UNKNOWN e supressão. Preserve: pausa do bot até retomada explícita,
Booksy apenas por link, FakeAIProvider, deduplicação, inbox/outbox e testes.

Restrições: sem envio real ao WhatsApp, a menos que exista autorização explícita
para um ensaio com número de teste e contatos listados; sem IA real; sem Booksy
API; sem implantação; sem dados reais de clientes. Não declare o handoff
operacional sem demonstrar um humano recebendo a notificação por meio real e
autorizado. Relate arquivos, decisões, comandos, testes reais e pendências,
separando planejado/implementado/testado/validado/implantado. Não inicie a Fase 6.
```
