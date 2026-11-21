# Operação contínua (Fase 17)

**Preparado e testado localmente; não está em operação.** Disponibilidade 24/7 **não foi alcançada nem
medida**: depende de servidor, rede e rotina reais.

## 1. Processos e supervisão

| Processo | Unidade | Reinício | Encerramento |
|---|---|---|---|
| Webhook (127.0.0.1:8080) | `rocha-reis-webhook.service` | `Restart=on-failure`, 5 s; até 5 vezes em 5 min | SIGTERM: termina requisições (≤ 10 s) |
| Painel (127.0.0.1:8000) | `rocha-reis-panel.service` | idem | SIGTERM (≤ 5 s); SSE reconecta sozinho |
| Worker | `rocha-reis-worker.service` (`Type=notify`) | idem + **WatchdogSec=60** (travado → reiniciado) | SIGTERM: termina a etapa atual e sai 0 |
| Backup | timer diário 03:15 | rodada seguinte; alerta se > 26 h | — |
| Alertas | timer 5 min | — | — |

Antes de subir, cada serviço roda `ops validar-instalacao` (`ExecStartPre`): permissões, perfil, 2º
fator, HTTPS, cifragem do volume e travas — **recusa** a subida se algo estiver inseguro.

Unidades verificadas com `systemd-analyze verify` (sem erros; controle negativo reprovado) e
`systemd-analyze security --offline`: exposição **1,3 (OK)** para webhook/painel/worker e **0,6 (SAFE)**
para backup/alertas (a unidade da Fase 13 marcava **8,3 — EXPOSED**).

**Fase 18 — execução REAL sob o systemd do usuário** (sem root; `tests_infra/fase18_systemd.py`): as
mesmas unidades, com todo o endurecimento, subiram e foram exercitadas. Resultados medidos:

| Comportamento | Resultado |
|---|---|
| `Type=notify` (READY) e `STATUS=` | ok; `processando filas` |
| SIGTERM (`systemctl stop`) | `Result=success`, saída 0, < 0,3 s nos três |
| kill -9 do worker no meio de 40 mensagens | reinício em ~6,5 s (`RestartSec=5`), 0 envios duplicados, 1 UNKNOWN **não** reenviado |
| Worker congelado (SIGSTOP) | `Watchdog timeout (limit 1min)!` → abortado e reiniciado |
| 6 quedas seguidas | `Start request repeated too quickly` → `failed`; `OnFailure` registra **cada** falha |
| Perfil com permissão frouxa | `ExecStartPre` recusa; o painel **nunca** sobe (fica no ciclo de reinício até o limite) |
| Isolamento observado nos processos | perfil e código montados somente leitura; dados graváveis; `$HOME` e `~/.ssh` invisíveis; `/tmp` privado; seccomp ativo; nenhuma capability; nenhum segredo no ambiente/linha de comando |
| Exposição (`systemd-analyze --user security`) | 1,8 nos três |

Achados corrigidos: alertas não liam o banco (WAL sob montagem somente leitura); watchdog gerava core
dump (`LimitCORE=0` agora). Ainda **não** testado: `User=rocha-reis`, systemd do sistema, `reboot`.

## 2. O que acontece numa queda (testado com kill -9)

| Situação | Garantia | Evidência |
|---|---|---|
| Worker morre no meio | Item volta à fila pelo lease; nada enviado duas vezes | 25 mensagens, kill -9, reinício: 0 duplicados |
| Morre entre enviar e registrar | Envio vira **UNKNOWN** (incerto) e **nunca** é reenviado sozinho | teste de fila + `envio_incerto` CRÍTICO |
| Todos os processos caem juntos | Reinício em ~1,6 s; filas esvaziam; atendimento humano mantém responsável | ensaio operacional |
| Banco | Transações; WAL; `integrity_check` ok após quedas | ensaio operacional |
| Mensagem repetida da Meta | Idempotência por evento | regressão das fases anteriores |

## 3. Logs

- Aplicações → **journald** (`journalctl -u rocha-reis-*`), sem texto de cliente, telefone ou segredo.
  Retenção: configurar `SystemMaxUse=` do journald (sugestão 500 MB).
- Log técnico privado opcional (`/var/log/rocha-reis-agent/canal.log`): **rotação** 5 MB × 5 arquivos,
  todos 0600 (corrigido nesta fase: antes crescia sem limite).
- Caddy: arquivo com rotação 10 MB × 10, sem cookies, assinatura, token de verificação nem IP completo.
  Fase 18 (log real do Caddy 2.11.9): o filtro vale para os logs de **execução/erro**; avisos do
  reverse_proxy traziam o `X-Forwarded-For` com o IP completo do aparelho — agora removido. **Log de
  acesso não está ligado** em nenhum site (decisão de privacidade; ligar só com filtro e retenção
  definidos pelo responsável).
- Core dumps desligados em todas as unidades (`LimitCORE=0`): a memória tem conversas e segredos.

## 4. Rotina

| Quando | O quê |
|---|---|
| Diário (automático) | Backup cifrado verificado + cópia secundária; alertas a cada 5 min |
| Semanal | `ops teste-restauracao --perfil /etc/rocha-reis-agent`; ler alertas da semana |
| Mensal | Atualizações do sistema (janela combinada); `ops ensaio-operacional` num diretório descartável |
| A cada atualização | `ATUALIZACAO_E_ROLLBACK.md` |
| Desligamento de funcionário | Desativar no painel (atendimentos voltam à fila) + revogar VPN |

## 5. Manutenção programada

Avisar a equipe; `ops alertas` sem CRÍTICO; parar na ordem worker → webhook → painel; fazer a
manutenção; subir webhook → painel → worker; conferir `/ready` e `ops alertas`. A Meta reenvia webhooks
não confirmados durante a janela.
