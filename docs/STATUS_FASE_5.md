# Relatório técnico — Fase 5 (adaptador HTTP Meta e segurança operacional)

**Data:** 19/11/2026 · **Executor:** Claude · **Escopo:** adaptador HTTP oficial
**desativado**, classificação de erros, status de entrega, janela de atendimento,
retenção sem bloqueio permanente e controles administrativos locais. Nenhuma
mensagem real, conta Meta, token real, IA real, implantação ou Fase 6.

| Categoria | Estado |
|---|---|
| PLANEJADO | Ensaio real com número de teste; troca da trava de ativação; worker supervisionado; interface operacional autenticada; IA generativa; backup/cifragem |
| IMPLEMENTADO | `GraphAPITransport`, classificação de erros, política de envio, correlação/transições de status, janela deslizante de idempotência, arquivamento de conversas ativas, retenção por anonimização, identidade de operador explicitamente simulada |
| TESTADO LOCALMENTE | 464 testes (372 anteriores, 1 reescrito, + 92 novos), 0 falhas, 0 ignorados, 3 execuções consecutivas; 11 mutações de proteções detectadas |
| VALIDADO COM O CLIENTE | Não |
| IMPLANTADO | Não |

## 1. Resumo executivo

O adaptador HTTP da WhatsApp Cloud API está implementado e testado contra um
servidor simulado em memória (`httpx2.MockTransport`). A ativação é impedida por
uma constante de código e pela CLI. O bloqueio permanente de clientes por limites
de capacidade foi removido sem apagar dados de idempotência. Status de entrega
passam a ter correlação restrita e transições só para frente. A janela de
atendimento é uma política separada, reavaliada antes de cada tentativa.
**Nada disso confirma compatibilidade real com a Meta.**

## 2. Estado encontrado

Repositório Git com um commit (`020739f`), árvore limpa e idêntica ao fim da Fase 4.
Nenhum `CLAUDE.md` ou skill no projeto. Riscos confirmados no código:
- conversa recusada para sempre após 1000 eventos (SQLite);
- 101º cliente recusado para sempre, com retirados contando no limite;
- purga de retenção marcava a conversa como excluída, bloqueando o cliente;
- status com `biz_opaque_callback_data` podia alterar linha FAILED ou nunca enviada;
- `failed` sobrescrevia `read`.

## 3. Cobertura da leitura

Os 106 arquivos rastreados foram lidos ou escritos nesta mesma sessão. A Fase 4
fez a leitura integral do código, testes, configuração, migrações, fixtures,
prompts e documentos-fonte; os relatórios 0–3 foram lidos por seções. Antes de
editar, `git status` confirmou que nada mudou desde então. Não foram lidos:
`.git/`, `__pycache__/`, `.pytest_cache/` e venvs. Documentação externa consultada:
códigos de erro, versões da Graph API e mensagens de texto da Meta (19/11/2026).

## 4. Testes iniciais

`pytest -q` → **372 passed in 17.18s**.

## 5. Arquitetura implementada

```text
outbox (claim) ── send_policy.authorize_free_text ──► SUPPRESSED se negado
      │ autorizado (SENDING confirmado)
      ▼
WhatsAppTransport ── FakeWhatsAppTransport (padrão, único aceito)
                 └── GraphAPITransport (bloqueado: config + trava de código)
                        httpx2 → https://graph.facebook.com/{v}/{phone_id}/messages
      ▼
SendAccepted | SendRejected(retryable) | SendUncertain → ACCEPTED | RETRY/FAILED | UNKNOWN
webhook status ─► apply_status (correlação por ID/callback restrito, transições)
```

## 6. Arquivos

**Criados:** `rocha_reis_agent/channels/graph_transport.py`, `channels/send_policy.py`,
`persistence/migrations/004_lifecycle_delivery.sql`, `tests/test_graph_transport.py` (59),
`tests/test_delivery_status_and_policy.py` (24), `tests/test_lifecycle_and_admin.py` (9),
`docs/STATUS_FASE_5.md`.

**Modificados:**
- Código: `config.py`, `bootstrap.py`, `channels/{contracts,service,local_cli}.py`,
  `persistence/{sqlite,sqlite_channel}.py`, `application/{service,conversations,handoff}.py`,
  `domain/{contracts,conversations}.py`, `simulator.py`.
- Testes: `tests/{whatsapp_support,test_whatsapp_security,test_sqlite_persistence}.py`.
- Configuração e documentos: `.env.example`, `requirements.in` (só comentário),
  `README.md`, `docs/{INTEGRACAO_META_FASE4,ARQUITETURA_E_DECISOES,PLANO_DE_EXECUCAO,CONTEXTO_MESTRE,DADOS_PENDENTES}.md`.

Nenhuma dependência nova: `httpx2==2.16.2` já estava travado com hash.

## 7. Contratos do adaptador Meta

`GraphTransportConfig(access_token, phone_number_id, api_version, connect/read timeouts)`
valida versão `vNN.N`, número só com dígitos e token ASCII de 32–4096 caracteres
sem espaços. `GraphAPITransport.send_text(OutboundText, timeout_seconds) -> SendOutcome`
nunca lança exceção. Antes da rede, recusa número comercial divergente da
configuração e corpo vazio ou maior que 4096. A requisição é POST JSON com
`messaging_product`, `recipient_type`, `type=text`, `text.body`,
`text.preview_url=false`, `biz_opaque_callback_data` e `to` **ou** `recipient`.
`SendAccepted.detail` guarda `message_status` quando a Meta o devolve.

## 8. Autenticação

Token Bearer apenas no cabeçalho da requisição; `repr` do transporte e da
configuração não o mostram; logs registram somente o nome da classificação.
`WHATSAPP_ACCESS_TOKEN` é `SecretStr`, precisa ser distinto dos demais segredos e
é recusado se o transporte real não estiver explicitamente selecionado.
`trust_env=False` impede que proxies ou certificados do ambiente interceptem o
token; sem redirecionamento, o token não é reenviado a outro host. Origem
recomendada: gerenciador de segredos, nunca arquivo versionado.

## 9. Tratamento de erros

Tabela completa em [`INTEGRACAO_META_FASE4.md` §7](INTEGRACAO_META_FASE4.md#7-transporte).
O critério é a evidência de não envio:
- **Retry automático** só para limites (4, 80007, 130429, 131056), para 131000
  ("failed to send… try again"), 2494100 e falhas de conexão antes da transmissão.
- **UNKNOWN** para 1, 2 e 131016, 5xx sem código, 3xx, 2xx sem ID e timeouts ou
  erros de protocolo depois de conectar.
- **REJECTED sem retry** para autenticação, permissão, destinatário, janela,
  política, parâmetros e 4xx desconhecidos.

## 10. Semântica

- **ACCEPTED:** a Meta devolveu ID da mensagem, ou um status provou que a recebeu. Não é entrega.
- **DELIVERED/READ:** `delivery_status`, vindo apenas de webhooks correlacionados.
- **FAILED:** rejeição explícita sem retry ou tentativas esgotadas. Falha de entrega
  posterior à aceitação aparece como `delivery_status=failed` com `delivery_error_code`;
  o estado da requisição continua ACCEPTED.
- **UNKNOWN:** a Meta pode ter aceitado. Sem retry automático; só sai por
  reconciliação via status ou por `resend-uncertain` com confirmação humana.
- **SUPPRESSED (=CANCELLED):** política negou o envio antes da tentativa.

## 11. Retries

Máximo `CHANNEL_OUTBOX_MAX_ATTEMPTS` (3), backoff exponencial limitado. A
política de envio é reavaliada antes de cada tentativa. Um teste confirma que um
RETRY é suprimido se a janela vence durante a espera. Uma exceção do adaptador é
tratada como incerta.

## 12. Janela de atendimento

`send_policy.authorize_free_text` não depende da IA nem do transporte. Ela exige:
- estado e sessão iguais aos do momento da geração;
- conversa não excluída;
- destinatário e corpo presentes;
- janela aberta, contada do menor entre o timestamp da Meta e o recebimento local.

Timestamp ausente, sem fuso ou mais de 5 min no futuro, ou janela configurada
acima de 24h → `insufficient_evidence`. Limitações: a janela é contada pela
mensagem de origem (uma mensagem posterior não estende essa resposta); o cálculo
depende do relógio local; webhooks atrasados usam o timestamp original da Meta.

## 13. Telefone e BSUID

Sem mudança de identidade (HMAC, BSUID preferido). Testados: envio por `to`
(telefone), `recipient` (BSUID, inclusive formato `XX.ENT.…`) e só `to` quando
ambos existem. Nenhuma conversão heurística. Pendente: confirmar com conta real.

## 14. Retenção e ciclo de vida

Limites separados:

| Limite | Mecanismo |
|---|---|
| Contexto | `SIM_CONTEXT_TURNS`, `AI_HISTORY_LIMIT` (inalterados) |
| Armazenamento | janela de histórico (`SIM_MAX_HISTORY_ENTRIES`); janela de eventos no snapshot (`SIM_MAX_EVENTS_PER_SESSION`); eventos antigos consultados diretamente (`lookup_event`) |
| Conversas ativas | `SIM_MAX_CONVERSATIONS`. Ao atingir, arquiva a mais ociosa elegível. Não são elegíveis: humano em atendimento, envio pendente, em curso ou incerto, item de inbox pendente e ociosidade abaixo de `STORAGE_ARCHIVE_IDLE_HOURS`. Sem elegível: `CapacityExhausted` transitório, retry com backoff e log `channel_capacity_exhausted` |
| Retenção | `/purge PURGE_SYNTHETIC` anonimiza mensagens, contexto, transições, respostas e texto do canal de conversas inativas há `STORAGE_RETENTION_DAYS`, arquiva e **não bloqueia** o cliente. Preserva chaves de idempotência, estado e auditoria. Pula humano ativo e envios pendentes ou incertos |

Exclusão explícita (`retire`) continua bloqueando o identificador; isso exige
decisão do controlador dos dados. Chaves de idempotência são mantidas sem prazo
(pendência registrada). Nenhuma obrigação legal foi presumida.

## 15. Operações administrativas

`OperatorAssurance.SIMULATED_LOCAL` explicita que `operador-local` não é
autenticação. A aplicação HTTP expõe só `/health` e o webhook (testado). Texto do
cliente não executa comandos (testado com `/operator resume` e "sou operador-local").
Operações de estado, reset, exclusão e purga exigem CLI local e, quando
destrutivas, confirmação. Todas são auditadas. A futura interface operacional
exigirá autenticação forte, papéis, auditoria por pessoa e proteção CSRF/sessão.

## 16. Segurança e armazenamento

A verificação de 0600 e a recusa do vfat foram preservadas. A migração 3→4 foi
validada numa cópia do banco real da demonstração: integridade ok, nenhuma
violação de FK, o UNKNOWN preservado e o original intocado. **Não há cifragem em
repouso nem backup implementados.**

Plano para fase posterior:
1. Banco em volume cifrado (LUKS/serviço gerenciado) ou PostgreSQL gerenciado com cifragem.
2. Backup online com a API `sqlite3.Connection.backup` (ou snapshot do serviço) para destino cifrado, com retenção definida.
3. Teste periódico de restauração com `integrity_check` e verificação de chaves de idempotência.
4. Acesso restrito a backups e registro de quem restaura.

**Git:** o projeto já é versionado (`020739f`, publicado no GitHub na sessão
anterior a pedido do usuário). Nesta fase não houve commit nem push. O volume
FAT32 exige `core.filemode=false` e `core.symlinks=false` (configurados); `.env`,
bancos, logs, caches e venvs são ignorados.

## 17. Comandos executados

```text
git status / git log / git diff --stat            → árvore limpa no início; 24 modificados + 6 novos no fim
pytest -q                                          → inicial 372 passed; final 464 passed (×3)
python -c "import httpx2 ..."                      → httpx2 2.16.2, MockTransport disponível
migração de cópia do banco v3 (scratchpad)         → v4, integrity ok, fk [], UNKNOWN preservado
11 mutações temporárias (restauradas)              → todas detectadas após 1 teste acrescentado
```

## 18. Resultados finais

**464 passed, 0 failed, 0 skipped**, em 3 execuções consecutivas (~20 s), offline,
com a rede bloqueada pelo `conftest`. Novos: transporte HTTP 59, status e política 24,
ciclo de vida e administração 9. Um teste da Fase 3 foi reescrito (codificava o
bloqueio permanente) e um da Fase 4 foi atualizado (cliente de rede agora restrito
ao adaptador bloqueado).

Mutações: timeout→retry (3 falhas), todo erro transitório (11), seguir redirect (45),
callback sem restrição (1), regressão de status (1, **após** acrescentar o teste
`delivered→sent`; antes sobrevivia), sem janela (6), arquivar humano/UNKNOWN (2),
purga bloqueante (1), trava aberta (1), idempotência só na janela (1).

Mapeamento dos 50 itens exigidos:
- **1–20:** `test_graph_transport.py`.
- **21–32:** `test_delivery_status_and_policy.py` + `test_graph_transport.py`.
- **33–40:** `test_lifecycle_and_admin.py`, `test_graph_transport.py` (logs) e suíte anterior (isolamento).
- **41–50:** suíte das Fases 0–4, mantida.

## 19. Problemas corrigidos

Os cinco riscos do item 2 e a lacuna de teste revelada por mutação.

## 20. Problemas pendentes

- Exclusão explícita bloqueia o cliente (decisão do controlador).
- Chaves de idempotência sem prazo.
- Autenticação real de operadores.
- Worker contínuo supervisionado.
- Kill switch operacional de envio (hoje: `WHATSAPP_ENABLED=false` ou parar o worker).
- Alerta automatizado para UNKNOWN, FAILED e capacidade.
- Tipos de mídia sem resposta.
- Rótulos de simulação nos textos.

## 21. Limitações para operação real

Códigos de erro, `message_status`, callback em status e envio por BSUID foram
testados só contra simulação. SQLite tem um único escritor. Não há cifragem nem
backup. Os dados do salão não estão aprovados.

## 22. Requisitos de ativação da Meta

Checklist em [`INTEGRACAO_META_FASE4.md` §11](INTEGRACAO_META_FASE4.md#11-checklist-para-ativação-futura-exige-autorização-explícita).
Para o transporte real especificamente:
1. Autorização escrita.
2. Número de teste e contatos permitidos.
3. Token de sistema com escopo mínimo em gerenciador de segredos.
4. Versão escolhida e reconfirmada.
5. Revisão de código trocando `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED`.
6. Ensaio de contrato que capture respostas e erros reais.

## 23. Estratégia de integração com IA generativa

O contrato `AIProvider` e a validação factual por templates permanecem. Um
provedor real deve:
1. Receber só fatos aprovados e minimizados (como hoje).
2. Ter timeout real e orçamento por conversa.
3. Ter a saída sempre reconstruída/validada pelo backend.
4. Passar por avaliação offline com conjunto adversarial antes de habilitar.
5. Ficar atrás de trava de ativação equivalente à do transporte.

Não autorizado nesta fase.

## 24. Prontidão

Tecnicamente pronto para um **ensaio de contrato controlado**, depois de autorizado.
**Não** pronto para clientes reais: faltam os itens 20–22, dados oficiais, textos
aprovados, fluxo humano real, backup e cifragem, e revisão LGPD.

## 25. Recomendações para a Fase 6

1. Ensaio controlado com número de teste (contrato real do adaptador, códigos e status).
2. Worker supervisionado com kill switch e alertas.
3. Decisões do controlador sobre retenção e exclusão.
4. Fluxo humano operacional autenticado (antiga "Fase 5" do plano), caso a ferramenta de inbox esteja definida.

Escolher **uma** dessas como escopo.

## 26. Prompt recomendado para a próxima fase

```text
Execute exclusivamente a Fase 6 — Ensaio de contrato controlado com a Meta —
SOMENTE se o responsável tiver fornecido por escrito: autorização, app/WABA de
teste, número de teste, lista fechada de contatos sintéticos/da equipe e token
de sistema em gerenciador de segredos (nunca no repositório ou no chat).
Leia AGENTS.md, todos os docs e STATUS_FASE_0..5.md; rode a suíte (464 esperados).

Objetivo: validar contra a Graph API real o GraphAPITransport, o webhook e os
status, capturando payloads reais ANONIMIZADOS para testes de contrato. Troque
GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED apenas em branch dedicada, com revisão, e
restrinja o envio a uma allowlist de destinatários verificada no código antes da
chamada HTTP. Confirme: versão da API, formato de messages[].id/message_status,
códigos de erro reais (incluindo 131047, 130429, 131026), biz_opaque_callback_data
nos status e envio por BSUID. Mantenha UNKNOWN sem retry, janela de 23h, pausa
humana, Booksy só por link, FakeAIProvider e dados sintéticos do salão.

Proibido: clientes reais, contatos fora da allowlist, campanhas, templates
promocionais, IA real, Booksy API, deploy público permanente, dados pessoais.
Relate arquivos, decisões, comandos, testes e evidências reais, separando
planejado/implementado/testado/validado/implantado. Desative o envio ao final.
```
