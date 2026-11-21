# Relatório técnico — Fase 18 (versionamento, pré-produção e validação da infraestrutura)

Executada em 21/11/2026 na máquina de desenvolvimento, com dados sintéticos, IA e WhatsApp simulados.
**18A concluída no limite do ambiente disponível. 18B (servidor real) NÃO iniciada: não existe servidor
autorizado.** Nenhum servidor contratado, nenhum domínio, nenhuma porta aberta para a rede, nenhum `sudo`,
nada instalado no sistema operacional, nenhuma API real, nenhum dado real, **nenhum commit ou push**.
Quatro travas `False`. **11B continua pendente.**

| Situação | Estado |
|---|---|
| IMPLEMENTADO | 9 correções de produto (§F); 2 alertas novos; ferramentas de validação real em `tests_infra/` |
| TESTADO LOCALMENTE — unitário/integração | pytest **1027**; Chromium **46**; roteiro 13/13 |
| TESTADO COM SERVIÇO REAL (local) | systemd do usuário; Caddy 2.11.9; WireGuard do kernel; nftables do kernel |
| TESTADO EM SERVIDOR EXTERNO | **Nada** (não há servidor) |
| IMPLANTADO | **Não** |

Autorizações usadas (pedidas e dadas nesta sessão): baixar Python 3.13 portátil e o binário oficial do
Caddy para uma pasta descartável no `$HOME` (hashes conferidos), rodar as unidades no systemd **do
usuário** (temporárias, em `/run/user`, removidas no fim) e o laboratório de rede em namespaces sem root.

## A. Estado inicial e final

| | Inicial | Final |
|---|---|---|
| Branch / commit | `main` = `origin/main` = `88ad523` | igual (sem commit) |
| Remoto | sem alterações inesperadas (`ls-remote` = `88ad523`) | idem |
| Versão | `0.14.0rc1.post4`, impressão `7fa848b6eeec4bb2`, 307 arquivos, **idêntica** ao manifesto congelado | `0.14.0rc1.post5`, manifesto recongelado (impressão em `RC1_MANIFESTO.json` / `RELEASE_CANDIDATE_RC1.md`) |
| Alterações sem commit | 54 (revisão final + Fase 17) | 60 caminhos (29 alterados + 31 novos) |
| pytest / Chromium / roteiro | **1011** / **46** / 13/13 (reexecutados no início) | **1027** (+16) / **46** / 13/13 |
| Travas | 4 × `False` | 4 × `False` |

`git diff --check` limpo; nenhum segredo, chave, e-mail ou caminho pessoal nas alterações (varredura por
padrões de chave privada, tokens Google/Meta/GitHub, chaves WireGuard em base64, e-mails e telefones).
Único número fora da convenção (formato `55 11 9xxxx-xxxx`) em `tests/test_review_fixes.py` (remetente fictício de
teste, sem dependência funcional) — padronizado para `5511900000901` na finalização, antes do commit.

**Rastreio das evidências** (`docs/homologacao/fase18/`): as rodadas finais registram a versão como
`0.14.0rc1.post4` porque o número só foi elevado a `post5` durante a rodada 5. Diferença entre o código
testado e o versionado: rodada 5 do systemd — só o número da versão, o telefone fictício de um teste e
documentos; rodada 3 da rede — além disso, os dois alertas novos (sem relação com a rede). Todas as
correções de produto de §F já estavam no código testado.

## B. Matriz — o que foi realmente testado, e onde

Legenda: **U** unitário · **I** integração (processos locais, sem systemd) · **S** infraestrutura
simulada · **R** serviço real local (systemd/Caddy/kernel de verdade, fora do servidor) · **X** servidor
externo. Uma coluna nunca substitui outra.

| Componente | U | I | S | R | X | Observação |
|---|---|---|---|---|---|---|
| Pacote → instalar (venv do `requirements.lock`) → preparar → validar | ✔ | ✔ | — | ✔ | ✘ | R: Python 3.13.18, pip com hashes, volume LUKS |
| Unidades systemd (notify, watchdog, restart, limite, OnFailure, ExecStartPre, timers) | ✔ estático | — | — | ✔ | ✘ | R: systemd 255 **do usuário**, sem `User=` |
| Endurecimento (montagens ro, seccomp, capabilities, /tmp privado, $HOME oculto) | ✔ estático | — | — | ✔ | ✘ | observado em `/proc/PID` dos serviços |
| Comunicação webhook → banco → worker → saída | ✔ | ✔ | — | ✔ | ✘ | transporte FALSO (Meta desligada) |
| Atualização / falha / reversão (`ops atualizar` + systemd) | ✔ | ✔ | — | ✔ | ✘ | |
| Backup agendado + teste de restauração | ✔ | ✔ | — | ✔ | ✘ | destino externo continua SIMULADO |
| Alertas (22 códigos) | ✔ todos | ✔ | — | ✔ parcial (§D) | ✘ | disco cheio/2º fator só U |
| Caddy (validate, TLS, SSE, caminhos, XFF, limites, logs) | ✔ estático | — | — | ✔ | ✘ | R: CA interna (`local_certs`); sem ACME |
| WireGuard (handshake, chave desconhecida, isolamento, revogação, recuperação) | ✔ | — | — | ✔ | ✘ | R: kernel; chaves via netlink (`tests_infra/wgnl.py`), não `wg`/`wg-quick` |
| nftables (regras carregadas e efetivas) | ✔ estático | — | — | ✔ | ✘ | em namespace de rede |
| 2º fator pela VPN + Caddy | ✔ | ✔ | — | ✔ | ✘ | |
| Usuário dedicado `rocha-reis`, systemd do sistema, journald do sistema | — | — | — | ✘ | ✘ | **NÃO TESTADO** — exige root |
| `reboot` da máquina | — | — | — | ✘ | ✘ | **NÃO TESTADO** — reiniciaria o computador do responsável |
| Certificado público, DNS, varredura externa de portas | — | — | — | ✘ | ✘ | **NÃO TESTADO** — sem domínio/servidor |
| Aplicativos WireGuard em celulares | — | — | — | ✘ | ✘ | **NÃO TESTADO** |
| Destino externo de backup real | — | — | ✔ falso | ✘ | ✘ | **NÃO TESTADO** — não contratado |
| Meta, Gemini, Booksy, dados reais, 11B | — | — | — | ✘ | ✘ | **proibidos nesta fase** |

## C. systemd real (`tests_infra/fase18_systemd.py`)

Mesmo texto de `deploy/systemd/`, inclusive todo o endurecimento. Desvios inevitáveis, aplicados só pela
ferramenta e verificados por `tests/test_phase18_infra.py`: sem `User=/Group=` (exige root); caminhos
reescritos para a raiz de teste; `ProtectHome=true` → `tmpfs` + bind somente leitura da raiz, do Python e
do socket de notificação do manager do usuário (ProtectHome esconde `/run/user`; no servidor o socket fica
em `/run/systemd/notify` — confirmado com unidade transitória: sem o bind, falha; com bind somente
leitura, funciona); `WORKER_FAKE_SEND_LOG` por drop-in (só transporte falso).

Resultado da rodada final (5):

| Etapa | Resultado medido |
|---|---|
| Instalação pelo fluxo oficial (venv do lock, Python 3.13.18) | `validar-instalacao` ok; travas `False` |
| `systemd-analyze --user verify` | sem erros |
| Subida (ExecStartPre + `Type=notify`) | 3 serviços `running`; worker `STATUS=processando filas`; `LimitCORE=0` |
| 12 mensagens webhook → worker → saída | 12 aceitas; 1 conversa aguardando humano |
| Isolamento observado em `/proc/PID` | perfil e código `ro`, dados `rw`, `$HOME` só com a raiz, `.ssh` invisível, `/tmp` privado, `NoNewPrivs=1`, seccomp, capabilities 0, nenhum segredo no ambiente/linha de comando |
| SIGTERM (`systemctl stop`) | `Result=success`, saída 0, 0,1–0,2 s |
| kill -9 do worker no meio de 40 mensagens | reinício em 7,2 s; 51 envios, **0 duplicados**; 1 UNKNOWN **não reenviado** |
| SIGSTOP (worker travado) | `Watchdog timeout (limit 1min)!` → reiniciado |
| 6 quedas seguidas | `Start request repeated too quickly` → `failed`; `OnFailure` registrou cada falha (8 registros) |
| Perfil 0644 | `ExecStartPre` recusa (`exit-code`, MainPID 0); painel nunca respondeu |
| Backup agendado (unidade) + alertas (unidade) | backup ok; alertas leem o banco (antes da correção: `banco_inacessivel` sempre) |
| Logs (journal + log privado) | 0 segredos, 0 telefones, 0 textos de cliente em 1046 linhas; log privado 0600 |
| `systemd-analyze --user security` | 1,8 (webhook, painel, worker) |
| `ops atualizar` versão válida | `ATUALIZADA` (9 etapas, migração ensaiada em cópia) |
| `ops atualizar` versão que não fica pronta | `REVERTIDA`; serviços prontos |
| `ops reverter` em seguida | volta à versão boa original (antes da correção: à versão quebrada) |
| Integridade final | `integrity_check` ok; 107 envios, 0 duplicados; 108 conversas |

Rodadas 1 a 4 acharam os defeitos de §F (relatórios intermediários não versionados); rodada 5: **19/19**.

## D. Alertas provocados com os serviços reais

Na rodada 5 do systemd, a unidade `rocha-reis-alertas.service` foi executada em cada situação e os
códigos foram lidos da saída **daquela execução** (journal por `InvocationID`):

| Situação provocada | Códigos emitidos (saída 2 = CRÍTICO) |
|---|---|
| Antes do primeiro backup | `backup_atrasado`, `envio_incerto` (o UNKNOWN do kill -9) |
| Worker parado 5 min 15 s, 55 mensagens chegando, 16 logins errados de IPs diferentes | `entrada_parada`, `entrada_acumulada`, `worker_sem_batimento`, `falhas_login`, `envio_incerto` |
| Worker religado e fila esvaziada | os três do worker **somem** |
| Backup corrompido + `ops teste-restauracao` (falha) | `restauracao_falhou` (alerta novo) |
| Backup refeito + teste de restauração ok | `restauracao_falhou` e `backup_atrasado` somem |
| Banco ilegível (permissão 000, serviços parados) | só `banco_inacessivel` |

Sem serviço real (só unitário, `tests/test_phase18_infra.py`, todos os 22 códigos e níveis): disco
baixo/cheio (exigiria encher o disco), banco perto do limite, capacidade esgotada, falhas de 2º fator,
falha de armazenamento, envio falhou, interrupção de envios, atendimento órfão, cópia externa atrasada.
`falhas_login` só dispara com tentativas de **vários** IPs: de um IP só o limitador corta em 5 (10 registros,
abaixo do limite de 30) — é o desenho (alerta de ataque distribuído).

## E. Caddy + WireGuard + nftables reais (`tests_infra/fase18_rede.py`)

Laboratório em namespaces de usuário/rede/montagem (`unshare`, sem root; nada muda na rede do
computador): servidor (`br0` 203.0.113.1, `wg0` 10.8.0.1), aparelhos `ana` e `bia` (autorizados) e
`intruso` (sem VPN). Arquivos de `deploy/` usados com marcadores preenchidos; desvios: CA interna para
todos os sites (`local_certs`, sem ACME), `bind` público implícito, chaves de teste geradas na hora.
Rodada 3: **21/21 etapas OK**.

| Etapa | Resultado medido |
|---|---|
| `nft -c` e carga no kernel | ok |
| `caddy validate` | ok (aviso: `header_up X-Forwarded-For` é redundante — mantido explícito) |
| Caddy sem o IP da VPN | **não sobe** (nem o webhook); com `ip_nonlocal_bind=1` sobe |
| Handshake ana/bia; TLS | ok; TLS 1.3, `TLS_AES_128_GCM_SHA256`, CA interna |
| Login + troca de senha + 2º fator pela VPN | 7 × 200; cookies `Secure` + `HttpOnly` |
| Webhook público (verificação + POST assinado) e SSE no painel | 200/200; evento ao vivo em **1,0 s** pelo proxy |
| 14 caminhos alternativos + PUT/DELETE/PATCH no público | todos 404 |
| Host/SNI do painel no endereço público | 200 **com 0 bytes** (resposta vazia padrão do Caddy; nenhum conteúdo do painel) |
| `X-Forwarded-For`/`X-Real-IP` forjados | ignorados: IP real registrado; 429 na 6ª tentativa |
| Corpo 70 KB painel / 300 KB webhook / cabeçalho 20 KB / cabeçalho lento | 413 / 413 / 431 / conexão fechada em 10 s |
| Portas 8000/8080/2019/22 (público e pela VPN) | sem resposta; só 443 aberta |
| Chave desconhecida | sem túnel |
| IP de outro aparelho dentro do túnel; aparelho que alarga os próprios AllowedIPs | descartados (servidor não encaminha) |
| **Painel sem túnel** (10.8.0.1 roteado pela interface pública) | rodada 1: **200 (falha)**; após correção: sem resposta |
| Revogação (syncconf) | conexão aberta da bia cai; nova não entra; ana continua (200) |
| Recriar a VPN do servidor a partir do arquivo | reconexão automática em **13,1 s**; login + 2º fator ok |
| Log do Caddy | sem token, desafio, assinatura, cookie, CSRF ou IP completo (rodada 1: IP completo — corrigido); 0600 |
| Limite de novas conexões (nftables) | 1ª recusada: **21ª** (rodada 1: **6ª** — rajada implícita 5) |

## F. Problemas encontrados e corrigidos (produto)

Todos invisíveis à verificação estática da Fase 17; cada um tem regressão em `tests/test_phase18_infra.py`
que falha no código anterior (controle negativo executado nos de código).

| # | Onde apareceu | Problema | Correção |
|---|---|---|---|
| 1 | systemd real | Alertas como serviço davam `banco_inacessivel` CRÍTICO sempre: SQLite em WAL precisa gravar o `-shm` até para ler, e a unidade montava o diretório do banco somente leitura | `ReadWritePaths=` só do diretório do banco; consulta continua `mode=ro` |
| 2 | systemd real | Watchdog abortou o worker **com core dump** (`LimitCORE=infinity`): memória com conversas e segredos | `LimitCORE=0` em todas as unidades |
| 3 | `ops atualizar` sob systemd | **Toda atualização real era cancelada** (`FileNotFoundError`): `RUNTIME.json` guardava o Python da pasta de preparo, renomeada logo depois | grava o caminho final |
| 4 | instalação real | O `chmod` "somente leitura" seguia `.venv/bin/python` e **alterava o Python do sistema** (observado: base passou a `r-xr-xr-x`) | ignora links |
| 5 | systemd real | Depois de reversão automática, `ops reverter` **reativava a versão quebrada** | registro de versões restaurado ao de antes da tentativa |
| 6 | laboratório | **Painel alcançável sem VPN** por quem está na rede do provedor (10.8.0.1 pela interface pública; a regra do 443 não olha o destino) | nftables: `ip daddr 10.8.0.0/24 iifname != "wg0" drop` antes de tudo |
| 7 | laboratório | Limite do webhook com rajada implícita 5: a 6ª conexão nova seguida era descartada | `burst 20 packets` explícito (rever com a Meta na 6B) |
| 8 | laboratório | Log do Caddy com **IP completo** do aparelho (avisos do reverse_proxy registram o `X-Forwarded-For` enviado ao app) | filtro remove `X-Forwarded-For` e `X-Real-Ip` |
| 9 | auditoria dos alertas | Falha do teste de restauração **sem alerta**; falha do backup só aparecia após 26 h | `restauracao_falhou` (CRÍTICO) e `backup_falhando` (ATENÇÃO) |

Lacuna de configuração (não era defeito de código): o Caddy depende do IP da VPN para subir. Novos
arquivos de referência: `deploy/caddy/caddy.service.d-10-rocha-reis.conf` (`After=`/`Wants=` da VPN) e
`deploy/sysctl/90-rocha-reis.conf` (`ip_nonlocal_bind=1`, para a VPN não derrubar o webhook).

Defeitos das próprias ferramentas de teste, corrigidos durante a fase (não afetam o produto): socket de
notificação escondido pelo `ProtectHome` no manager do usuário; leitura de peers no netlink; "syncconf"
implementado como `setconf` (derrubava a sessão de quem continuava por ~15 s — por isso o guia exige
`wg syncconf`); contagem antes de a fila esvaziar; corrida no teste do limite de reinícios.

## G. Observações (sem correção)

- `OnFailure=` dispara a **cada** falha, inclusive as que reiniciam sozinhas (8 registros em 6 quedas +
  watchdog): bom para alerta, mas o canal futuro de aviso precisa agrupar.
- `ExecStartPre` que recusa entra no ciclo de reinício até o limite (o painel nunca sobe); não vira
  `failed` na hora.
- Log de **acesso** do Caddy não está ligado em nenhum site (só logs de execução, filtrados). Decisão de
  privacidade a confirmar pelo responsável.
- No manager do usuário os serviços herdam variáveis da sessão gráfica (`DISPLAY`, `DBUS_…`); no systemd
  do sistema não existem. Nenhum segredo apareceu no ambiente.
- Efeito colateral no computador: o primeiro teste de WireGuard carregou o módulo `wireguard` do kernel
  (nenhuma interface no host; some no próximo reinício). Restam para remoção manual, só com dados
  sintéticos: `~/.local/share/rocha-reis-fase18/run-systemd-4` e `run-rede-3` (a remoção automática foi
  bloqueada pela verificação de segurança) e a própria pasta `~/.local/share/rocha-reis-fase18`
  (Python/Caddy baixados, venv da ferramenta e relatórios) quando não forem mais necessários.

## H. Segurança

Quatro travas `False` (conferidas no início, em cada `validar-instalacao` e no fim). Segredos só no perfil
0600 (nunca no ambiente/linha de comando dos processos — verificado em `/proc/PID/environ` e `cmdline`);
logs do journal e do canal sem segredo, telefone ou texto de cliente (0 ocorrências em ~550 linhas);
serviços sem capabilities, com seccomp e sem ver `$HOME`; VPN como fronteira de rede efetiva após a
correção 6; 2º fator obrigatório também pela VPN; `X-Forwarded-For` não forjável.

## I. Pendências para o servidor definitivo (18B)

Roteiro com critérios de aceite: `GUIA_IMPLANTACAO_SERVIDOR.md` §7. Depende de: contratação e
autorização do servidor; volume LUKS; usuário `rocha-reis` e systemd do sistema; domínio + certificado
público; WireGuard com chaves geradas no servidor e aparelhos reais; destino externo de backup (+
adaptador); canal de alerta; `reboot` com conferência; varredura externa de portas. Meta (6B) e Gemini
(9C-B) continuam com homologação própria. 11B continua pendente.

## J. Proposta de versionamento (NÃO executada — aguarda autorização)

Um único commit consolidado (revisão final + Fase 17 + Fase 18): as três mexem nos mesmos arquivos
(`deploy.py`, unidades, guias) e separá-las exigiria montar commits parciais à mão, sem ganho de rastreio.
Base: `88ad523`; remoto conferido igual. Identidade de commit já configurada no repositório.

Arquivos: todos os listados por `git status` (29 alterados + 31 novos caminhos): código
(`rocha_reis_agent/worker.py`, `ops/{deploy,alerts,offsite,localserver,opsdrill,loadtest}.py`, `api/health.py`,
`config.py`, `observability.py`, `panel/*`, `persistence/sqlite.py`, `__init__.py`), referências
(`deploy/systemd/*`, `deploy/caddy/*`, `deploy/nftables/*`, `deploy/wireguard/*`, `deploy/sysctl/*`),
testes (`tests/test_phase17_*.py`, `tests/test_phase18_infra.py`, `tests/test_review_fixes.py`,
`tests/server_support.py`, `tests_e2e/test_review_fixes_browser.py`), ferramentas `tests_infra/`, documentos
(`docs/*`, evidências `docs/homologacao/fase18/*.json` sem caminhos pessoais, `RC1_MANIFESTO.json`).
Fora do commit (ignorados): bancos, perfis, chaves, `.env`, venvs, raízes de teste.

Mensagem recomendada:

```
feat: Fases 17 e 18 — operação contínua e infraestrutura validada (RC1 0.14.0rc1.post5)

Revisão final (post3) e Fase 17 (post4): worker 24/7 com sd_notify/watchdog, /ready, pacote
reprodutível, instalação/atualização/reversão, alertas locais, cópia externa simulada, unidades
systemd, Caddy, WireGuard e nftables de referência.

Fase 18 (18A, sem servidor): unidades executadas no systemd real do usuário; Caddy 2.11.9,
WireGuard do kernel e nftables reais em laboratório isolado. Corrige: atualização real sempre
cancelada (RUNTIME.json), instalador alterando o Python do sistema, reverter reativando versão
quebrada, alertas sem acesso ao banco WAL, core dump do worker, painel alcançável sem VPN,
rajada do limite de conexões, IP completo no log do Caddy, falha de restauração sem alerta.

Testes: 1027 pytest, 46 Chromium, roteiro 13/13; travas fechadas. 18B e 11B pendentes.
```

Antes de executar: nova autorização específica; `git fetch` e conferir que `origin/main` continua em
`88ad523`; `ops verificar-rc` idêntica; `git diff --check`.
