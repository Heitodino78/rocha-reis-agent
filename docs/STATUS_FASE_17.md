# Relatório técnico — Fase 17 (infraestrutura, implantação e operação contínua)

Executada localmente em 20/11/2026, com dados sintéticos, IA e WhatsApp simulados. **Nenhum servidor
contratado, nada instalado no sistema, nenhuma porta aberta, nenhum DNS/certificado público, nenhuma API
real, nenhum dado real, nenhum commit/tag/push.** Quatro travas `False`. **11B adiada (pendente).**

| Situação | Estado |
|---|---|
| IMPLEMENTADO | Worker contínuo, `/ready`, pacote/instalação/atualização/reversão, alertas, cópia externa (adaptador falso), ensaio operacional, medição de carga, referências de servidor |
| TESTADO LOCALMENTE | 1011 pytest + 46 Chromium; ensaio operacional OK; carga OK; roteiro 13/13 |
| PREPARADO PARA INSTALAÇÃO | Unidades systemd, Caddy, WireGuard, nftables, guia (`GUIA_IMPLANTACAO_SERVIDOR.md`) |
| DEPENDE DE CONTRATAÇÃO | Servidor, volume LUKS, domínio/certificado, destino externo de backup |
| DEPENDE DE HOMOLOGAÇÃO | Meta (6B), Gemini (9C-B), equipe (11B) |
| DEPENDE DO SALÃO | Custos, retenção, RPO/RTO, canal de alerta, lista de acessos à VPN |
| IMPLANTADO | **Não** |

## A. Diagnóstico

- Git: `main` = `origin/main` = `88ad523` (Fase 16). **Divergência com o enunciado:** a árvore local já
  estava na `0.14.0rc1.post3` (correções da revisão final, sem commit) e o baseline real era **977 pytest +
  46 Chromium** (não 969/44); roteiro 13/13.
- Lacunas reais encontradas:
  1. **Sem worker contínuo**: em pré-produção (`panel_embedded_worker=false`) as mensagens ficariam paradas
     na fila; a documentação afirmava que os três processos já existiam.
  2. Só `/health` (vida); sem verificação de **prontidão**.
  3. Sem empacotamento, instalação em árvore Linux, atualização ou reversão executáveis.
  4. Unidades de **webhook e worker inexistentes**; a do painel com exposição **8,3 (EXPOSED)**.
  5. **Caddy confiava em `X-Forwarded-For` de faixas privadas** — os clientes da VPN têm IP privado: um
     funcionário poderia forjar o cabeçalho e escapar do limitador de tentativas de login.
  6. Sem VPN/firewall de referência; sem avaliação de alertas; sem destino externo testável.
  7. Log técnico privado **sem rotação**.
- Achados durante a fase (corrigidos, com teste):
  8. **Erros 500 sob carga**: toda requisição autenticada abria transação de ESCRITA só para conferir a
     sessão; com o worker gravando, estourava a espera do SQLite (3 erros em 1131 requisições, pico 4 s).
  9. **Webhook recusado em rajada** (2 de 799) com 1 s de espera por bloqueio; a instância usa 5 s.
  10. **Capacidade**: padrão de 100 conversas ativas (arquivamento só após 24 h paradas) esgotaria num dia
      movimentado; a instância usa 1000 (teto) e o alerta `capacidade_esgotada` vigia.
  11. **Backup automático falharia no servidor**: gravava o estado dentro do perfil (`/etc`, somente
      leitura para os serviços); agora o perfil aponta `/var/lib/.../estado`.
  12. Pacote identificava versão/esquema pelo código importado, não pela árvore empacotada.
  13. Cópia externa reenviava a cada rodada o backup que a retenção apagaria (ciclo).

## B. Infraestrutura (arquivos)

| Arquivo | O que é |
|---|---|
| `rocha_reis_agent/worker.py` | Worker 24/7: laço com `drain`, sd_notify (READY/WATCHDOG/STOPPING), batimento 0600, SIGTERM limpo, sai com erro após 20 falhas seguidas; exige `KNOWLEDGE_SOURCE=database` |
| `rocha_reis_agent/api/health.py` | `/ready`: banco, esquema = código, WAL, disco ≥ 50 MB; 503 sem detalhes |
| `rocha_reis_agent/config.py`, `ops/demo.py` | `ROCHA_REIS_PROFILE` (serviços leem o perfil 0600); `panel_origin` no perfil |
| `rocha_reis_agent/ops/deploy.py` | Pacote reprodutível; verificação (hash, extras, links, `..`); árvore e permissões; instância; `validar-instalacao`; atualização; reversão; restauração confirmada |
| `rocha_reis_agent/ops/alerts.py` | 19 alertas sobre as métricas existentes, saída 0/1/2 |
| `rocha_reis_agent/ops/offsite.py` | Contrato do destino externo + adaptador FALSO com falhas injetáveis |
| `rocha_reis_agent/ops/localserver.py` | Supervisor local (papel do systemd) para ensaios e testes |
| `rocha_reis_agent/ops/opsdrill.py`, `ops/loadtest.py` | Ensaio operacional e medição de carga |
| `rocha_reis_agent/observability.py` | Rotação 5 MB × 5, todo arquivo 0600 |
| `rocha_reis_agent/panel/store.py` | Sessão conferida em LEITURA; renovação da ociosidade melhor-esforço |
| `rocha_reis_agent/ops/backup_job.py` | Local do estado configurável |
| `deploy/systemd/*` | webhook, worker, painel, backup, alertas (+ timers) e alerta@ — exposição 1,3 / 0,6 |
| `deploy/caddy/Caddyfile.exemplo` | Sem `trusted_proxies`; XFF reescrito; só GET/POST do webhook; logs filtrados; painel só nas rotas do app |
| `deploy/wireguard/*`, `deploy/nftables/*` | VPN (sem chaves) e firewall (`nft -c` aprovado) |

Isolamento verificado contra os serviços reais: webhook responde 404/405 a `/panel/`, `/api/panel/...`,
`../`, `%2e%2e`, `//`, maiúsculas, `/docs`; painel recusa host forjado (400) e não tem a rota do webhook.

## C. Operação

Inicialização ~1,6 s até `/ready`; SIGTERM: worker sai 0, uvicorn sai pelo sinal após concluir
(convenção que o systemd trata como limpa); kill -9 do worker no meio de 25 mensagens → reinício sem
duplicata; queda de todos os processos → pronto de novo em ~1,6 s. Logs no journald + log privado com
rotação. Alertas: `MONITORAMENTO_E_ALERTAS.md`. Backups: diário + cópia externa (simulada).

## D. Implantação

`empacotar` (mesmo conteúdo → mesmo SHA-256) → `instalar` (venv por `requirements.lock` com hashes; modo
sem rede com rodas locais) → `preparar-servidor` → `validar-instalacao` → unidades. Atualização e
reversão: `ATUALIZACAO_E_ROLLBACK.md`. Servidor recomendado: 1 vCPU/1 GB/20 GB LUKS (mínimo), 2 vCPU/2 GB/
40 GB (margem) — `GUIA_IMPLANTACAO_SERVIDOR.md` §3.

## E. Segurança

Usuário dedicado sem shell; código somente leitura; perfil e chaves 0600/0700 e somente leitura para os
serviços; nada sensível em linha de comando/ambiente; `validar-instalacao` recusa: permissões frouxas,
perfil inválido (2º fator desligado, Gemini/Meta reais, dados reais, chave junto do banco), volume sem
cifragem, ausência de versão ativa, execução como root. 2º fator, HTTPS e cookie Secure obrigatórios.
Ensaio de migração decifra só no volume cifrado. Sem cliente HTTP geral no produto (prontidão por socket
restrito a 127.0.0.1). Quatro travas `False`.

## F. Qualidade

| | Baseline | Final |
|---|---|---|
| pytest | 977 | **1011** |
| Chromium | 46 | **46** |
| Roteiro da demonstração | 13/13 | **13/13** |

Novos: `tests/test_phase17_ops.py` (pacote, adulteração, layout, validação insegura, perfil, prontidão,
worker/sd_notify, alertas, cópia externa com falhas, rotação de log, unidades/proxy/VPN/firewall, sessão
sob bloqueio, backup com perfil somente leitura) e `tests/test_phase17_server.py` (serviços reais da
versão instalada, isolamento, kill -9, atualização/reversão/migração/restauração, ensaio operacional).
Testes que sobem serviços exigem volume cifrado verificado no `$HOME` (pulados, com motivo, se não houver).
Controles negativos: sessão sob bloqueio e revisão anterior falham no código antigo; `systemd-analyze
verify` e `nft -c` reprovam arquivos inválidos.

**Ensaio operacional** (`ops ensaio-operacional`, volume LUKS): instalação 0,7 s; subida 1,6 s; 20
mensagens 0,8 s; atendimento humano com troca de senha e 2º fator; backup + cópia externa; queda de todos
os processos → pronto em 1,6 s, 36 envios, **0 duplicados**, integridade ok; restauração numa instância
nova só com backup externo + configuração e chaves → 20/20 conversas, 2º fator ok, auditoria preservada.

**Carga** (`ops carga`, 8 CPUs x86_64, Python 3.13, volume LUKS; não vale para o servidor futuro): 799
conversas pelo webhook em 26 s (~30 msg/s, p95 293 ms), todas aceitas; 4 operadores por 30 s contra o
painel real com mensagens chegando e backup rodando: 2246 requisições, **0 erros**, p50 51 ms, p95 86 ms;
backup durante 0,3 s; memória ~73/76/59 MB (webhook/painel/worker); banco 6 MB para 971 conversas
(~3 KB/mensagem); reinício até pronto ~7 s com volume. Antes da correção da sessão: 3 erros 500, pico 4 s.

## G. Pendências e próximos passos

1. **Servidor:** contratar, LUKS, usuário, firewall, Caddy, WireGuard, destino externo real (+ adaptador),
   canal de alerta; repetir `GUIA_IMPLANTACAO_SERVIDOR.md` §6.
2. **Gemini:** autorização, conta/chave, homologação 9C-B (critérios pré-registrados).
3. **Meta:** conta verificada, número, app, homologação 6B; webhook público pelo Caddy.
4. **Dados reais:** `SEGURANCA_ARMAZENAMENTO.md` §6, decisões jurídicas, abertura revisada da trava.
5. **Produção:** 11B, decisões do salão (`REVISAO_FINAL.md` §2), `CHECKLIST_PRODUCAO.md`.
