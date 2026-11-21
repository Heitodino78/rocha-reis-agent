# Configurações de referência (não instaladas)

Arquivos de **exemplo** para a implantação futura (Fases 13, 17 e 18). Nada aqui está em uso, publicado ou
instalado; não há DNS, certificado público, VPN nem servidor contratado. Procedimento completo em
`docs/GUIA_IMPLANTACAO_SERVIDOR.md`.

| Arquivo | Para quê | Verificado |
|---|---|---|
| `systemd/rocha-reis-webhook.service` | Webhook em 127.0.0.1:8080 (único publicado pelo Caddy) | `verify` + `security --offline` (1,3 OK); **executada no systemd do usuário** (Fase 18, 1,8) |
| `systemd/rocha-reis-worker.service` | Worker contínuo, `Type=notify` + `WatchdogSec` | idem |
| `systemd/rocha-reis-panel.service` | Painel em 127.0.0.1:8000 (só pela VPN) | idem |
| `systemd/rocha-reis-backup.service` + `.timer` | Backup diário verificado | idem (0,6 SAFE) |
| `systemd/rocha-reis-alertas.service` + `.timer` | `ops alertas` a cada 5 min | idem (0,6 SAFE) |
| `systemd/rocha-reis-alerta@.service` | Registro de falha de unidade (OnFailure) | `verify` |
| `caddy/Caddyfile.exemplo` | HTTPS: só `/webhooks/whatsapp` público; painel só no IP da VPN; logs sem segredos | **Caddy 2.11.9 real** em laboratório isolado (Fase 18) |
| `caddy/caddy.service.d-10-rocha-reis.conf` | Drop-in: Caddy depois da VPN (`Wants=`) | ordem testada em laboratório (Fase 18) |
| `sysctl/90-rocha-reis.conf` | `ip_nonlocal_bind=1`: Caddy (e o webhook) sobem mesmo sem a VPN | laboratório (Fase 18) |
| `wireguard/*.exemplo` | VPN da equipe (servidor e aparelho), sem chaves | **WireGuard do kernel** em laboratório, chaves de teste (Fase 18) |
| `nftables/rocha-reis.nft.exemplo` | Firewall: só 443/tcp e 51820/udp; 8000/8080/2019 fechados; IP da VPN só pelo `wg0` | **carregado no kernel** em laboratório (Fase 18) |

Testes: `tests/test_phase13_ops.py`, `tests/test_phase17_ops.py`, `tests/test_phase18_infra.py`; com serviços reais: `tests_infra/fase18_systemd.py` e `tests_infra/fase18_rede.py` (ver `docs/STATUS_FASE_18.md`).
