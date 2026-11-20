# Integração Meta WhatsApp Cloud API — preparação offline (Fases 4 e 5)

**Estado:** IMPLEMENTADO e TESTADO LOCALMENTE com payloads sintéticos, transporte
fake e, na Fase 5, adaptador HTTP Graph API exercitado só com `httpx2.MockTransport`.
**Não** conectado à Meta, **não** validado com o cliente, **não** implantado.
Nenhuma mensagem real foi enviada e nenhuma credencial real existe no projeto.
Testes simulados **não** confirmam compatibilidade real com a Meta.

## 1. Fluxo implementado

```text
POST /webhooks/whatsapp (somente se WHATSAPP_ENABLED=true)
  1. limite de tamanho (Content-Length e leitura em streaming) ........ 413
  2. HMAC-SHA256 dos BYTES BRUTOS com o App Secret, compare_digest ...... 401
  3. JSON (UTF-8, profundidade protegida) ............................... 400
  4. parse_webhook: envelope → eventos internos (sem domínio) ............ 400 se estrutura geral inválida
  5. INSERT na channel_inbox, todos os eventos em UMA transação .......... 503 se falhar (Meta reentrega)
  6. 200 {"status":"received"}  ← somente após commit
        (nenhuma IA, nenhum envio na requisição)

Worker (processo separado; CLI local `channels.local_cli work`)
  InboundProcessor.process_one
    claim_inbound (BEGIN IMMEDIATE, lease, FIFO por conversa)
    status   → atualiza outbox (nunca responde)
    unsupported → DONE sem resposta, conteúdo não interpretado
    texto    → AgentService.handle(InboundMessage, effects=…)
                 núcleo existente: dedup, pausa humana, FakeAIProvider,
                 conhecimento aprovado, validação factual, Booksy só link
               effects encena NO MESMO COMMIT da conversa:
                 inbox DONE (verificando posse do lease) + outbox PENDING
  OutboxDispatcher.dispatch_one
    claim_outbound (BEGIN IMMEDIATE): SENDING confirmado ANTES da chamada
      – suprime se estado/sessão da conversa mudou ou janela > 23h
    transport.send_text(...)  → SendAccepted | SendRejected | SendUncertain
    finish_outbound: ACCEPTED | RETRY(backoff) | FAILED | UNKNOWN
```

Arquivos: `channels/contracts.py` (contratos), `channels/meta.py` (verificação,
assinatura, parser, payload Graph futuro), `channels/service.py` (ingestão,
processador, despachante), `channels/fake_transport.py`, `channels/local_cli.py`,
`persistence/sqlite_channel.py`, `persistence/migrations/003_whatsapp_channel.sql`,
`api/whatsapp.py`. O núcleo recebeu um único gancho: `AgentService.handle(..., effects=)`.

## 2. Verificação (GET) e autenticidade (POST)

- GET exige `hub.mode=subscribe`, `hub.verify_token` igual a `WHATSAPP_VERIFY_TOKEN`
  (comparação em tempo constante) e `hub.challenge` em `[A-Za-z0-9_-]{1,128}`;
  responde o challenge em `text/plain`. Qualquer outra combinação → 403.
- **Verify Token ≠ App Secret ≠ token de acesso.** O App Secret só assina o POST;
  o token de acesso (futuro envio) não existe nesta fase.
- POST exige `X-Hub-Signature-256: sha256=<hex>` calculado sobre o corpo bruto.
  Nenhum campo do JSON é aceito como prova de autenticidade. A verificação ocorre
  antes de qualquer parsing ou efeito persistente.
- **Logs de acesso do servidor HTTP devem ficar desligados** (`--no-access-log`):
  a query do GET contém o Verify Token. Os logs da aplicação registram apenas nomes
  estáticos (`whatsapp_webhook_received`, `..._bad_signature` etc.) e contadores.
- Replay de payload assinado é absorvido pela deduplicação durável (seção 4).
  A Meta não assina timestamp; não há janela anti-replay além da deduplicação.

## 3. Normalização (`parse_webhook`)

Suporta `object=whatsapp_business_account`, `field=messages`, número comercial
igual a `WHATSAPP_PHONE_NUMBER_ID` (e WABA igual a `WHATSAPP_BUSINESS_ACCOUNT_ID`,
quando configurado). Produz:

| Contrato | Origem | Efeito |
|---|---|---|
| `InboundTextMessage` | `messages[]` com `type=text` e corpo 1–4096 | vai ao núcleo |
| `InboundUnsupported` | outros tipos, ou texto vazio/longo | registrado, sem interpretação nem resposta |
| `StatusUpdate` | `statuses[]` sent/delivered/read/failed | atualiza outbox, nunca responde |

- Remetente: `from` (wa_id) **ou** `from_user_id` (BSUID, em uso desde abril/2026;
  o telefone pode ser omitido). A identidade da conversa prefere o BSUID.
- Mensagem cujo `from` é o próprio número comercial é descartada; ecos
  (`smb_message_echoes`) e outros campos são ignorados.
- Itens sem ID, timestamp, tipo ou remetente válidos são ignorados individualmente;
  estrutura geral inválida (entry/changes não-lista, value não-objeto) → 400.
- Limites: 100 entries, 100 changes, 100 itens por lista, 1000 eventos por webhook.
- Nome de perfil (`contacts[].profile.name`) nunca é lido ou armazenado.

**Identificadores internos:** `conversation_id = "wa-" + HMAC-SHA256(chave, número +
remetente)[:40]` — pseudônimo estável, sem telefone em claro; `event_id = "wa-" +
SHA-256(wamid)[:40]`, porque o wamid tem caracteres fora do formato interno.

## 4. Inbox e deduplicação

`channel_inbox` tem `UNIQUE(channel, business_number_id, dedup_key)`:
`msg:SHA256(wamid)` para mensagens e `st:SHA256(wamid:status)` para status.
Um digest do conteúdo normalizado diferencia reentrega idêntica (duplicata) de
reuso conflitante de ID (conflito: não sobrescreve nem processa).

Garantias **verificadas por teste**: reentrega idêntica, reentrega após reinício
de processo, entregas concorrentes idênticas (4 threads), várias mensagens no mesmo
payload, status repetidos e texto igual com IDs diferentes (não é duplicata).
Duas camadas: inbox (por wamid) e núcleo (conversa + event_id, da Fase 3).

Processamento: `claim_inbound` marca PROCESSING com lease. Não reivindica um item
se houver item anterior da MESMA conversa pendente/em processamento (ordem de
chegada por conversa). Lease vencido torna o item reivindicável; tentativas são
limitadas (`CHANNEL_INBOX_MAX_ATTEMPTS`) e falhas de armazenamento usam backoff.
Se outro worker assumiu o item, a conclusão do primeiro falha (`LeaseLost`) e
**toda** a tentativa (conversa + outbox) é revertida.

Resultados da inbox: `replied`, `recorded_without_reply` (pausa humana), `duplicate`,
`unsupported_type_ignored`, `invalid_input` (FAILED), `rejected_by_core` (FAILED:
sessão encerrada, limite de eventos), `storage_retry`/`failed_after_retries`, `status_*`.
O texto da inbox é apagado (`payload='{}'`) ao concluir; itens FAILED mantêm o
conteúdo para investigação local até exclusão.

## 5. Outbox transacional

`channel_outbox` é criada pelo efeito encenado no commit da conversa
(`UNIQUE(source_inbox_id)`: no máximo uma resposta por mensagem recebida).
Campos: operação (UUID), destinatário, corpo autorizado, conversa/sessão, evento
de origem, estado exigido, abertura da janela, estado, tentativas, lease, ID do
provedor, status de entrega e datas.

| Estado | Significado |
|---|---|
| PENDING / RETRY | aguardando envio (RETRY após rejeição explícita reprocessável, com backoff) |
| SENDING | reivindicada e confirmada **antes** da chamada ao transporte |
| ACCEPTED | a Meta aceitou a requisição (ID recebido). **Não** significa entregue |
| UNKNOWN | resultado incerto; **nunca** reenviada automaticamente |
| FAILED | rejeição definitiva ou tentativas esgotadas |
| SUPPRESSED | não enviada: estado/sessão mudou, conversa excluída ou janela vencida |

Mapeamento para a nomenclatura da Fase 5 (sem estados redundantes): IN_PROGRESS =
`SENDING`; CANCELLED = `SUPPRESSED`; DELIVERED/READ = `delivery_status`, campo
separado do estado da requisição, porque aceitar (ACCEPTED) não é entregar.

`delivery_status` vem dos webhooks de status e só avança: `∅→{sent,delivered,read,
failed}`, `sent→{delivered,read,failed}`, `delivered→read`; `read` e `failed` são
finais. Regressões e contradições (ex.: `failed` após `read`) são ignoradas e
contadas em `status_anomalies`; `failed` guarda `delivery_error_code`. Correlação
somente por (1) ID do provedor já registrado ou (2) `biz_opaque_callback_data` igual
ao ID da operação **e** linha em SENDING/UNKNOWN. Nunca por horário ou conteúdo.
Status sem correlação → `status_unmatched`, sem efeito.

### Cenários do intervalo entre commit e envio

| Cenário | Comportamento implementado | Teste |
|---|---|---|
| A. commit local, processo reinicia antes do envio | outbox PENDING durável; outro processo envia | `test_real_process_restart_between_commit_and_send` |
| B. Meta aceita, app falha antes de registrar | linha fica SENDING → lease vence → UNKNOWN; webhook de status com `biz_opaque_callback_data` reconcilia para ACCEPTED | `test_real_process_restarts_..._crash_during_send...`, `test_status_webhook_reconciles...` |
| C. timeout, resultado desconhecido | UNKNOWN, sem retry automático | `test_uncertain_result_is_never_blindly_resent` |
| D. worker reinicia durante pendência | inbox: lease vence e item é reprocessado (núcleo deduplica); outbox SENDING → UNKNOWN | `test_lost_inbound_lease...`, `test_expired_send_lease...` |
| E. duas instâncias | claim sob `BEGIN IMMEDIATE`; só uma obtém a linha | `test_two_instances_never_send_the_same_message` |

**Garantia real:** cada resposta é tentada **no máximo uma vez com resultado incerto**;
retentativas automáticas só ocorrem quando o adaptador informa que a mensagem
garantidamente não foi transmitida ou que houve rejeição explícita reprocessável.
Não há "exatamente uma vez" ponta a ponta: a Meta não documenta idempotência para
envio de mensagens (nenhuma chave de idempotência encontrada na referência
consultada). Reenvio de UNKNOWN exige decisão humana:
`local_cli resend-uncertain OPERACAO RESEND_UNCERTAIN:OPERACAO` (risco de duplicata aceito).

Política de retry: `CHANNEL_OUTBOX_MAX_ATTEMPTS` (padrão 3), backoff exponencial
`base·2^(n-1)` limitado a `CHANNEL_BACKOFF_MAX_SECONDS`. Exceção inesperada do
adaptador é tratada como incerta. Falha ao registrar o resultado deixa SENDING
(→ UNKNOWN), nunca reenvio.

### Corrida com atendimento humano

1. Operador confirma **durante** a geração: versão otimista da Fase 3 invalida a
   tentativa; ao reler, a conversa está pausada/encerrada e nenhuma outbox é criada.
2. Operador confirma **depois** do commit e **antes** do envio: no claim, o estado
   atual difere de `required_state` (ou a sessão mudou) → SUPPRESSED.
3. Janela residual: a mudança ocorre entre o claim (SENDING) e a resposta HTTP da
   Meta (duração de uma chamada, limitada por `WHATSAPP_SEND_TIMEOUT_SECONDS`).
   Sem bloqueio distribuído, essa janela não pode ser eliminada; está documentada.

## 6. Regras da plataforma

- **Janela de atendimento** (`channels/send_policy.py`, independente do transporte
  e da IA, reavaliada no claim antes de CADA tentativa, inclusive RETRY): só
  respostas a mensagem do cliente, até `CHANNEL_SERVICE_WINDOW_HOURS` (23h, margem
  sob 24h) contadas do menor entre o timestamp da Meta e o recebimento local.
  Vencida → SUPPRESSED `outside_service_window`. Sem evidência (timestamp ausente,
  no futuro além de 5 min de tolerância, sem fuso, destinatário ou corpo ausentes,
  janela configurada > 24h) → SUPPRESSED `insufficient_evidence`.
  Limitações: usa a mensagem de ORIGEM da resposta (conservador; uma mensagem
  posterior do cliente não estende a janela dessa resposta); depende do relógio
  local e do timestamp da Meta; webhooks entregues com atraso usam o timestamp
  original da Meta (mais antigo), o que pode suprimir respostas tardias.
- **Templates, campanhas, mensagens iniciadas pela empresa, broadcast:** não
  implementados; não há caminho de código para enviar sem mensagem de origem.
- Consentimento/opt-in, limites de taxa por número/par e qualidade da conta
  dependem da conta real; o adaptador futuro deve mapear os códigos de erro da
  Graph API para `SendRejected(retryable=...)`/`SendUncertain` (pendente).
- As respostas ainda carregam rótulos de simulação ("[SIMULAÇÃO — DADOS
  FICTÍCIOS]", "Nenhum atendente real foi notificado"). Isso é proposital:
  impede uso real antes de haver textos aprovados pelo salão.

## 7. Transporte

`WhatsAppTransport.send_text(OutboundText, timeout_seconds) -> SendOutcome`.
`FakeWhatsAppTransport` simula `accept`, `reject`, `throttle`, `not_sent`, `timeout`,
`ambiguous` e `crash`, registra as entregas simuladas e nunca abre socket.
`meta.build_text_payload` monta o corpo de
`POST https://graph.facebook.com/{versão}/{phone_number_id}/messages`
(`preview_url=false`, `biz_opaque_callback_data=<operação>`, `to` para telefone ou
`recipient` para BSUID; se ambos existem, só `to` é enviado, regra documentada pela Meta).

**Fase 5 — `channels/graph_transport.py` (GraphAPITransport):** cliente `httpx2` já
travado no lock; base fixa `https://graph.facebook.com` (constante, não configurável);
versão (`vNN.N`) e número (dígitos) validados; `follow_redirects=False`,
`trust_env=False` (ignora proxies/variáveis do ambiente), TLS verificado; recusa cliente
inseguro; `Authorization: Bearer` só no cabeçalho da requisição, nunca em log/repr;
timeouts separados de conexão e leitura; resposta limitada a 64 KiB. Classificação:

| Situação | Resultado |
|---|---|
| 2xx com `messages[0].id` válido | ACCEPTED (requisição aceita; não é entrega) |
| 2xx sem ID utilizável / JSON inválido | UNKNOWN |
| 3xx (não seguido) | UNKNOWN |
| código 4, 80007, 130429, 131056 (limite) e 131000 ("failed to send… try again"), 2494100 | REJECTED com retry |
| 0/190 (auth), 3/10/200–299/131005 (permissão), 131026/131021/130403/131050/133010 (destinatário), 131047 (janela), 368/130497/131031/130472/131048/131049 (política), 100/131008/131009/131051/135000/131037/131045 (parâmetro), 131057 | REJECTED sem retry |
| código 1, 2, 131016; 5xx sem código conhecido | UNKNOWN |
| 4xx sem código conhecido; 401/403 sem JSON | REJECTED sem retry; 429 sem JSON → retry |
| ConnectTimeout/ConnectError/PoolTimeout (não transmitido) | REJECTED com retry |
| ReadTimeout/WriteTimeout/erro de protocolo após conectar | UNKNOWN |

**Ativação bloqueada:** `WHATSAPP_TRANSPORT=graph` é recusado pela configuração
enquanto a constante de código `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED` for `False`
(variáveis de ambiente sozinhas nunca ativam envio). Mesmo destravado, exige canal
habilitado, versão explícita e token exclusivo; `WHATSAPP_ACCESS_TOKEN` sem
transporte real é recusado. A CLI local recusa qualquer transporte que não seja o fake.
O módulo só é importado de forma tardia pelo `bootstrap.build_transport`.
Versão: v26.0 (29/07/2026) era a mais recente na página oficial de versões em
19/11/2026; exemplos da página de texto citam v27.0, não confirmada. Escolha final
na ativação; v20.0 e anteriores já expiraram.

## 8. Armazenamento: diagnóstico e decisão

- `/media/heitordino/1TB` é **vfat (FAT32)** montado com `fmask=0022,dmask=0022`.
  O FAT não armazena modo POSIX nem dono: `chmod` é aceito sem efeito e todo
  arquivo aparece `0644`; também não há links simbólicos nem journaling. **Não é
  adequado para dados de conversas.** O adaptador continua recusando esse volume;
  a verificação agora ocorre **antes** de criar o banco (antes, deixava um arquivo
  com schema e um lock no volume).
- `/tmp` neste host é **tmpfs** (sandbox flatpak do editor): sobrevive ao reinício
  do processo, **não** ao reinício do sistema. Demonstrações anteriores em `/tmp`
  não provam durabilidade.
- `$HOME` (`/home`, ext4) aceita `0600`. Caminho recomendado para ensaio local
  durável: `~/.local/share/rocha-reis-agent/*.synthetic.sqlite3`, diretório `0700`.
  O código permanece no volume de 1 TB; somente o banco fica no `$HOME`.
- SQLite é local, de um único escritor, sem criptografia em repouso nem backup.
  Produção exige banco gerenciado/volume cifrado e backups (pendente).

## 9. Configuração (`.env.example`)

`WHATSAPP_ENABLED=false` por padrão: sem rotas de webhook. Habilitar exige
`STORAGE_BACKEND=sqlite`, `WHATSAPP_PHONE_NUMBER_ID` e três segredos **distintos**
(Verify Token, App Secret, chave de pseudonimização) com 32+ caracteres, sem
palavras de placeholder. Os segredos são `SecretStr` (mascarados em `repr`) e
erros de configuração não exibem valores. Limites: corpo (256 KiB), timeout de envio,
leases, tentativas, backoff e janela.

## 10. Testes

`tests/test_whatsapp_webhook.py` (GET/POST, assinatura, adulteração, limites,
estrutura, múltiplos eventos, deduplicação concorrente, 503, logs, configuração),
`test_whatsapp_parser.py`, `test_whatsapp_processing.py` (núcleo, contexto,
isolamento, handoff, Booksy, corridas), `test_whatsapp_outbox.py` (estados,
retries, incerteza, leases, concorrência, rollback, janela, processos reais com
`os._exit`), `test_whatsapp_security.py` (vfat simulado, credenciais, rede,
superfície HTTP, CLI). A rede é bloqueada em todos os testes e workers.

Limite do que os testes provam: os payloads são sintéticos e baseados na
documentação; nenhum foi capturado de um webhook real. Semântica de erros da Graph
API, formato exato de `biz_opaque_callback_data` nos status e envio para BSUID
precisam ser confirmados com uma conta de teste autorizada.

## 11. Checklist para ativação futura (exige autorização explícita)

1. Conta Meta Business, app, número de teste e contatos autorizados definidos pelo responsável.
2. Segredos em gerenciador de segredos; App Secret e Verify Token reais nunca em arquivo versionado.
3. ~~Implementar `GraphAPITransport`~~ (Fase 5, testado só com mock). Falta: ensaio de contrato com número de teste, confirmar códigos de erro reais, token de sistema com escopo mínimo e trocar a trava de código mediante revisão.
4. Capturar e versionar payloads reais anonimizados para testes de contrato do parser.
5. Endpoint HTTPS público atrás de proxy com limite de taxa; logs de acesso sem query string.
6. Worker contínuo supervisionado (hoje há apenas a CLI de execução única) e alerta para UNKNOWN/FAILED.
7. Textos aprovados pelo salão, sem rótulos de simulação; dados oficiais aprovados e link Booksy validado.
8. Fluxo humano real (Fase 5) antes de anunciar transferência a um atendente.
9. Política de retenção/LGPD aprovada; banco durável cifrado com backup; revisão dos limites de capacidade.
10. Teste ponta a ponta com número autorizado e plano de desligamento (`WHATSAPP_ENABLED=false`).
