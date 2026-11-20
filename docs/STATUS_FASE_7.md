# Relatório técnico — Fase 7 (painel de atendimento humano)

**Data:** 19–20/11/2026 · **Executor:** Claude · **Escopo:** painel web local com
autenticação, RBAC, gestão de conversas, atendimento humano, respostas pela outbox,
tempo real e auditoria. Somente dados sintéticos e transporte simulado. Fase 6B e
Fase 8 não executadas.

| Classificação | Estado |
|---|---|
| PLANEJADO | 2FA/SSO, notificação externa de funcionários, HTTPS e implantação, templates fora da janela, IA real |
| IMPLEMENTADO | Painel (backend + frontend), usuários Argon2id, sessões, CSRF, RBAC, conversas, assumir/transferir/finalizar/retomar, resposta humana pela outbox, SSE, auditoria, CLI local |
| TESTADO LOCALMENTE | **583 testes pytest** (512 anteriores + 71 novos) e **8 testes de navegador real** (Chromium headless); 0 falhas, 0 ignorados; 12 mutações de proteções detectadas; demonstração de 14 etapas com reinício |
| HOMOLOGADO (Meta) | Não |
| VALIDADO PELO SALÃO | Não |
| IMPLANTADO | Não |

---

## A. Estado inicial

- Git em `d181bf4` (`main`), árvore limpa e sincronizada com `origin/main`, sem arquivos sensíveis rastreados.
- Sem `CLAUDE.md` nem skills no projeto.
- Suíte: **512 aprovados**.
- Todo o repositório tinha sido lido ou escrito nesta sessão (Fases 4–6A) e estava inalterado desde o commit.

**Estado real do atendimento humano:** a máquina de estados AUTO/WAITING_HUMAN/
HUMAN_ACTIVE/RESOLVED existia no núcleo. As ações de operador só existiam na CLI,
com a identidade simulada `operador-local`. A resposta humana ficava apenas no
histórico e nunca chegava à outbox. Não havia interface.

**Problemas estruturais encontrados antes de implementar:**
1. A outbox exigia `source_inbox_id` em toda mensagem, o que tornava impossível representar uma resposta humana.
2. O executor de migrações dividia o SQL por `;`, o que quebraria triggers.
3. O endereço do cliente era apagado após o processamento (minimização correta), sem outra fonte para a resposta humana.
4. O handoff só aceitava a identidade simulada.

## B. Implementação

### Arquitetura

```text
Navegador (HTML + CSS + JS em módulo, sem build)
   │  cookie HttpOnly SameSite=Strict · X-CSRF-Token (derivado) · Origin verificado
   ▼
create_panel_app (FastAPI, SEPARADA do app do webhook, TrustedHost, CSP 'self')
   ├─ /api/panel/*  → PanelService (permissões no backend)
   │                    └─ ConversationManager.edit (versão otimista do núcleo)
   │                         + efeitos no MESMO commit: atribuição, outbox, auditoria
   ├─ /api/panel/events (SSE) → change_log (triggers SQLite) filtrado por visibilidade
   └─ worker embutido opcional (somente transporte fake) → inbox/outbox existentes
Mesmo SQLite: conversas, inbox, outbox, contatos, usuários, sessões, auditoria.
```

O painel é uma camada nova. O núcleo, o webhook e o despachante são os mesmos.
Não existe um segundo sistema de mensagens.

### Tecnologias e decisões

| Decisão | Justificativa |
|---|---|
| Frontend em **JS puro (ES modules) + CSS**, sem build e sem npm | Não há Node no ambiente do projeto; zero dependências de frontend; CSP estrita sem `unsafe-inline`. React/Vite era preferência, não obrigação |
| **Argon2id** via `argon2-cffi==25.1.0` (parâmetros padrão RFC 9106) | Biblioteca consolidada; única dependência de runtime nova, travada com hash |
| **SSE** com cursor persistido (`change_log` por triggers) | Mais simples que WebSocket para fluxo servidor→cliente; reconexão nativa via `Last-Event-ID`; nada depende de memória; o stream leva só IDs e o conteúdo é buscado pela API autorizada |
| Painel em app separada | O endpoint público futuro do webhook nunca expõe rotas administrativas (testado nos dois sentidos) |
| Playwright + Chromium em venv separado (`requirements-e2e.txt`) | Testes reais de navegador sem contaminar o lock de produção |

### Arquivos

**Criados:**
- `rocha_reis_agent/panel/{__init__,security,store,service,api,app,admin_cli}.py`
- `rocha_reis_agent/panel/static/{index.html,styles.css,app.js}`
- `rocha_reis_agent/persistence/migrations/006_operator_panel.sql`
- `tests/{panel_support,test_panel_auth_security,test_panel_operations,test_panel_realtime,test_panel_cli_and_migration}.py`
- `tests_e2e/{harness,test_panel_browser}.py`, `requirements-e2e.txt`, `docs/STATUS_FASE_7.md`

**Modificados:**
- `persistence/sqlite.py`: schema 6; divisão de SQL compatível com triggers; migração com FKs desligadas e `foreign_key_check`; `read()`; vínculo da mensagem com a operação; limpeza do painel na exclusão.
- `persistence/sqlite_channel.py`: contatos, mensagem do operador, conferência de responsável no claim.
- `application/handoff.py`: `VerifiedOperator`, `act_verified`, `request_by_operator`, `record_transfer`.
- `domain/conversations.py`: `HistoryEntry.operation_id`.
- `config.py` (`PANEL_*`), `requirements.in`/`requirements.lock` (argon2-cffi), `.env.example`, documentação.

### APIs (`/api/panel`)

| Método e rota | Quem |
|---|---|
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/session` · `POST /auth/password` | público (login) / autenticado |
| `GET /overview` · `GET /conversations?filter=&q=&cursor=` · `GET /conversations/{id}` · `POST /conversations/{id}/read` | autenticado, com visibilidade |
| `POST /conversations/{id}/assume\|resolve\|resume\|request-human\|transfer\|reply` | conforme as regras abaixo |
| `GET /operators` | autenticado (id, nome e papel dos ativos) |
| `GET/POST /users`, `POST /users/{id}/deactivate\|activate\|reset-password` | ADMIN |
| `GET /audit`, `GET/PUT /settings`, `POST /dispatch/halt\|resume` | ADMIN |
| `GET /events` (SSE) | autenticado |

Erros sempre como `{"detail": mensagem segura}`. Conversa inexistente e conversa
proibida respondem igual (404), para não revelar existência.

### Usuários, papéis e sessões

- `panel_users`: id, login, nome, hash Argon2id (o CHECK exige o prefixo `$argon2id$`), papel, ativo, troca de senha obrigatória, datas e último acesso.
- **ADMIN:** vê todas as conversas, gerencia a equipe, a auditoria, as permissões e a interrupção de envios. Retoma a automação e pode enviar uma conversa automática para a fila.
- **OPERATOR:** vê a fila (aguardando, sem responsável) e as próprias conversas. Assume, responde, finaliza e transfere as próprias conversas (se permitido). Retoma a automação só se o admin liberar.
- **Sessão:** token aleatório em cookie `HttpOnly`, `SameSite=Strict`, `Path=/`. Com `Secure`, o nome tem prefixo `__Host-`. O banco guarda só o hash do token.
- **CSRF:** HMAC derivado do token, nunca armazenado. É exigido junto com `Origin` igual à origem configurada em toda requisição que altera estado.
- **Expiração:** 30 min de ociosidade e 12 h absolutas. O SSE não renova a ociosidade.
- **Logout e revogação:** o logout revoga no servidor; um novo login revoga a sessão anterior (proteção contra fixação); desativar o usuário revoga todas as sessões.
- **Força bruta:** 5 falhas em 15 min, por login e por IP, bloqueiam com 429, mesmo com a senha certa.
- **Mensagem única** para usuário inexistente e senha errada, e verificação com hash fictício para reduzir enumeração por tempo.
- **Primeiro administrador:** `admin_cli create-admin`, com senha digitada (sem eco), permitido só com o banco sem usuários. Os demais acessos são criados pelo painel com senha temporária de troca obrigatória.
- **Política de senha:** 12–128 caracteres, sem conter o login, variedade mínima.
- **Limitação:** em HTTP local o cookie fica sem `Secure`, o que só é aceito em 127.0.0.1/localhost. Fora disso a configuração é recusada.

### Fluxo de atendimento e integração com o núcleo

1. O cliente pede atendimento humano. O núcleo leva a conversa a WAITING_HUMAN e ela entra na fila; o SSE avisa os operadores.
2. **Assumir:**
   - é feito pelo ConversationManager, sob versão otimista;
   - a atribuição é gravada no mesmo commit, e só se ainda não houver responsável;
   - a auditoria também vai no mesmo commit;
   - dois operadores concorrentes: só um consegue, o outro recebe 409.
3. **Transferir:** fica registrada como transição `transfer` (versionada). O efeito de commit confere o responsável esperado e o operador anterior perde o acesso.
4. **Responder:**
   - idempotência pela chave do cliente;
   - texto de 1 a 2000 caracteres sem caracteres de controle;
   - estado HUMAN_ACTIVE, responsável igual ao autor;
   - janela de 24h contada da **última mensagem do cliente** (`channel_contacts`);
   - mensagem no histórico + outbox (`origin=operator`) + auditoria, no mesmo commit;
   - o commit reconfere o responsável.
5. **Envio:** o despachante existente reaplica a política (estado, sessão, janela) e **confere o responsável**. Se mudou, a mensagem é cancelada (SUPPRESSED `operator_assignment_changed`). UNKNOWN continua sem reenvio.
6. **Finalizar:** RESOLVED; a automação **continua suspensa**.
7. **Retomar:** ação explícita (ADMIN, ou operador se liberado), RESOLVED → AUTO; remove o responsável.

Status exibidos com honestidade: Na fila de envio, Enviando, **Aceita pela plataforma
(envio simulado)**, Enviada, Entregue, Lida, Falhou, Resultado incerto, Não enviada.
Criar uma mensagem na outbox nunca aparece como "entregue".

### Tempo real

- Triggers SQLite gravam `change_log` em toda escrita de conversa, outbox e atribuição, venha ela de qualquer processo: painel, worker, CLI ou webhook.
- O SSE consulta o log a cada 1 s e envia `id: cursor`, só com IDs de conversas **visíveis ao usuário**, e `other: true` sem IDs quando houve mudanças invisíveis a ele.
- O cliente então busca os dados pela API autorizada.
- Reconexão por `Last-Event-ID`; um cursor já podado gera `reset` (ressincronização completa).
- A sessão é revalidada a cada ciclo: revogada ou expirada → evento `auth` e fim do stream.
- O stream rotaciona a cada 60 s, o que limita conexões presas no desligamento.

### Auditoria

`panel_audit` é somente inclusão: triggers abortam UPDATE e DELETE. Registra login, falha
de login, logout, criação e desativação de usuário, troca e redefinição de senha,
assumir, transferir, mensagem, finalizar, retomar, enviar para a fila, permissões,
interrupção e retomada de envios, e acessos negados. Cada registro tem ID da operação,
autor, ação, entidade, momento e resultado (sucesso/negado/falhou). Sem senha, token
ou conteúdo de mensagem. As transições do núcleo também levam `panel:<id>` do autor.

### Segurança (resumo)

- CSP `'self'` sem inline, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`.
- TrustedHost contra DNS rebinding; sem `/docs` nem OpenAPI.
- Todo conteúdo é inserido no DOM via `textContent` (XSS testado no navegador).
- SQL parametrizado; a busca com `LIKE` escapa curingas.
- IDOR/BOLA: visibilidade aplicada no próprio SQL de cada consulta e ação.
- Erros sem detalhes internos.
- Travas do transporte real intactas; o worker embutido só aceita o transporte fake.

## C. Validação

```text
pytest -q (início)                       → 512 passed
pytest -q (final, ×2 após ajustes)       → 583 passed, 0 failed, 0 skipped (~100 s)
pytest tests_e2e (Chromium headless)     → 8 passed (~95 s)
pip check (dois venvs)                   → sem conflitos
mutações (12, todas restauradas)         → todas detectadas (4 só depois de novos testes)
demonstração com processos reais         → 14 etapas OK, inclusive reinício (abaixo)
```

**Testes novos:**
- autenticação e segurança: 31;
- operações e concorrência: 30;
- tempo real: 6;
- CLI e migração: 4;
- navegador: 8.

Eles cobrem os 72 cenários mínimos, mapeados nos nomes dos testes:
- **A1–A10:** `test_first_admin…`, `test_valid_login…`, `test_invalid_login…`, `test_deactivated…`, `test_logout…`, `test_idle_and_absolute…`, `test_brute_force…`, `test_csrf…`.
- **B11–B18:** `test_admin_and_operator_permissions…`, `test_unauthenticated…`, `test_operator_cannot_reach…idor`, `test_operator_cannot_transfer_or_resume…`, `test_admin_cannot_reply…`.
- **C19–C26:** `test_listing_filters_pagination_and_search`, `test_history_roles_responsible_and_unread`, `test_state_and_assignment_survive_restart`.
- **D27–D36:** `test_full_human_flow…`, `test_two_operators_cannot_assume…`, `test_transfer…`, `test_invalid_transitions…`.
- **E37–E46:** `test_invalid_reply_text…`, `test_reply_goes_through_outbox…`, `test_double_click…`, `test_send_failure_and_unknown…`, `test_pending_human_message_survives_restart`, `test_reply_blocked_when_service_window_expired`.
- **F47–F52:** `test_panel_realtime.py` e navegador (`test_full_operator_flow_with_live_updates`, `test_stream_reconnects_and_resyncs`).
- **G53–G60:** XSS no navegador, CSRF, escalada, IDOR, `test_session_revoked_during_stream…`, logs, comando por mensagem e configuração insegura recusada.
- **H61–H72:** suíte das Fases 0–6A, mantida integralmente.

**Concorrência:**
- 4 threads assumindo a mesma conversa ao mesmo tempo;
- 4 cliques simultâneos com a mesma chave;
- posse alterada por outro processo entre a verificação e o commit;
- resposta do agente pendente quando o operador assume;
- transferência com a mensagem já na outbox.

**Navegador real (Chromium headless, desktop 1366×860 e celular 390×844):**
- login, erro de login e logout pelo teclado;
- fluxo completo entre dois navegadores com atualização ao vivo;
- clique duplo sem duplicar a mensagem;
- transferência entre navegadores;
- XSS renderizado como texto;
- celular: lista → conversa → voltar, menu e Esc;
- operador desativado é deslogado ao vivo;
- reconexão do stream;
- criação de operador com troca obrigatória de senha.

Também inspecionei visualmente capturas de tela (desktop e celular). Não testei
leitores de tela nem outros navegadores (Firefox/Safari).

**Correções feitas durante a validação:**
1. Leituras do painel dentro da unidade de trabalho falhavam por transação aninhada. Criei `repository.read()`.
2. `PanelError` dentro da transação virava erro genérico. Agora herda de `SimulationError`.
3. O token CSRF estava armazenado em texto. Agora é derivado por HMAC.
4. Confirmação errada na retomada de envios gerava 500. Agora gera 409.
5. A prévia da fila mostrava a resposta padrão do assistente, igual para todos. Agora mostra a última mensagem do cliente.
6. O desligamento do servidor ficava preso pelas conexões SSE. Rotação reduzida para 60 s e `--timeout-graceful-shutdown 5`.
7. O indicador "Ao vivo" ficava invisível no desktop.
8. Quatro mutações sobreviventes revelaram camadas de defesa sem teste próprio. Foram criados testes isolados para cada uma.

## D. Demonstração reproduzível

Comandos a partir da raiz do projeto, com um banco durável fora do volume vfat:

```sh
install -d -m 700 "$HOME/.local/share/rocha-reis-agent/panel-demo"
export APP_ENV=local STORAGE_BACKEND=sqlite \
  STORAGE_PATH="$HOME/.local/share/rocha-reis-agent/panel-demo/painel.synthetic.sqlite3" \
  WHATSAPP_ENABLED=true WHATSAPP_PHONE_NUMBER_ID=100000000000001 WHATSAPP_BUSINESS_ACCOUNT_ID=200000000000002 \
  PANEL_ENABLED=true PANEL_ORIGIN=http://127.0.0.1:8000 PANEL_COOKIE_SECURE=false PANEL_EMBEDDED_WORKER=true
for name in WHATSAPP_VERIFY_TOKEN WHATSAPP_APP_SECRET WHATSAPP_CONVERSATION_KEY; do
  export "$name=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"; done
PY=/tmp/rocha-reis-agent-venv/bin/python
$PY -m rocha_reis_agent.panel.admin_cli create-admin --login dona --name "Responsável"   # 3. senha digitada
$PY -m rocha_reis_agent.panel.admin_cli seed-demo                                        # conversas fictícias A–E
$PY -m uvicorn rocha_reis_agent.panel.app:create_panel_app --factory --host 127.0.0.1 --port 8000 \
    --no-access-log --timeout-graceful-shutdown 5                                        # 1–2. backend + frontend
# 4. abrir http://127.0.0.1:8000/panel/ e entrar
$PY -m rocha_reis_agent.panel.admin_cli say --from 5511900000107 "Quero falar com uma pessoa"   # 5–7
# 8–13. pelo painel: Assumir → responder → (Transferir) → Finalizar → Retomar automação (admin)
# 14. Ctrl+C no servidor, subir de novo e conferir estado, mensagens e auditoria
```

Execução registrada (script com processos reais e Chromium, banco em `$HOME`):

```text
1-3. create-admin → "Administrador criado. Nenhuma senha foi exibida ou gravada em texto."
     seed-demo    → "Demonstração fictícia criada: A (agendamento), B (preço), C e E (aguardando), D (encerrada)."
4.   login admin e criação de atendente; atendente trocou a senha temporária; conexão: Ao vivo
5-7. say → HTTP 200; a conversa apareceu na fila sem recarregar (3 aguardando)
8.   assumido: Em atendimento humano
9-10. resposta humana pela outbox; status: "Aceita pela plataforma (envio simulado)"
     nova mensagem do cliente chegou ao vivo; automação continua suspensa
11.  transferido para a responsável
12.  encerrado: automação suspensa
13.  retomado: automação ativa
14.  servidor encerrado e reiniciado → estado Automático; mensagens [client, assistant, operator, client];
     auditoria: Entrou, Retomou automação, Finalizou, Transferiu, Enviou mensagem, Assumiu…
     erros de página: nenhum · permissões: diretório 0700, banco 0600
```

Testes:
- `/tmp/rocha-reis-agent-venv/bin/python -m pytest -q`
- navegador: ver `requirements-e2e.txt`, depois `/tmp/rocha-reis-e2e-venv/bin/python -m pytest -q tests_e2e`

## E. Avaliação

**Funciona e foi testado localmente:** tudo o que está na seção B, com dados
sintéticos, transporte fake e um único servidor local.

**Limitações e riscos:**
- HTTP local sem TLS; sem 2FA/SSO; política de senha básica, sem verificação contra senhas vazadas.
- Bloqueio por IP inútil atrás de proxy sem configuração (não há proxy nesta fase).
- SSE por polling no banco (1 s) com SQLite de um escritor: adequado a uma equipe pequena, sem teste de carga.
- Sem notificação externa (som/push/e-mail) quando um cliente pede atendimento; só o indicador da fila.
- Auditoria sem retenção nem exportação definidas.
- Operador não vê conversas automáticas (minimização, decisão de produto a validar com o salão).
- Exclusão a pedido ainda bloqueia o identificador (decisão pendente desde a Fase 5).
- Rótulos de simulação continuam nas respostas automáticas.
- Acessibilidade verificada por teclado, foco, rótulos e anúncios, sem auditoria com leitor de tela.

**O que falta para uso real:**
1. Fase 6B.
2. HTTPS e hospedagem.
3. Autenticação forte.
4. Política de retenção e LGPD.
5. Backup e cifragem.
6. Textos e dados oficiais.
7. Treinamento e validação com a equipe do salão.

**Dependências externas:** conta Meta de teste; decisões do salão sobre papéis,
horários e acesso às conversas automáticas; infraestrutura com TLS.

**Git:** nenhum commit nem push (o pedido proíbe sem autorização).
Mensagem sugerida:

```text
feat: Fase 7 — painel de atendimento humano (auth Argon2id, RBAC, SSE, outbox humana)
```

**Recomendação para a Fase 8:** validar o painel com a equipe do salão (usabilidade
e fluxo real de trabalho) e carregar os dados oficiais aprovados (catálogo, Booksy,
textos sem rótulo de simulação), antes de IA real ou da 6B.

**Prompt sugerido:**

```text
Execute exclusivamente a Fase 8 — Dados oficiais, textos aprovados e validação do painel
com a equipe do salão — somente com o material entregue e aprovado pelo responsável.
Leia AGENTS.md, todos os docs e STATUS_FASE_0..7.md; rode pytest (583 esperados) e
tests_e2e (8 esperados) e relate antes de editar.
Objetivo: (1) importar o catálogo OFICIAL aprovado (serviços, preços, horários,
endereço, profissionais, políticas, link Booksy validado) pelo fluxo de revisões com
aprovação, sem inventar nada; (2) substituir os rótulos de simulação pelos textos
aprovados, mantendo um marcador de ambiente de teste fora de produção; (3) roteiro de
validação do painel com funcionários (tarefas, tempo, erros) e ajustes de UX; (4)
registrar as decisões do salão: acesso dos atendentes a conversas automáticas,
retomada por atendentes, horários sem atendimento humano, retenção e exclusão.
Restrições: sem Meta real (6B), sem IA real, sem Booksy API, sem deploy, sem dados de
clientes reais; nenhum commit/push sem autorização explícita.
```
