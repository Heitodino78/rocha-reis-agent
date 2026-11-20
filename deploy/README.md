# Configurações de referência (não instaladas)

Arquivos de **exemplo** para a implantação futura (Fase 13). Nada aqui está em uso, publicado ou
instalado; não há DNS, certificado público nem servidor contratado. Detalhes e decisões em
`docs/ARQUITETURA_PRE_PRODUCAO.md` e `docs/OPERACAO_BACKUP_AUTOMATICO.md`.

| Arquivo | Para quê |
|---|---|
| `caddy/Caddyfile.exemplo` | HTTPS: só `/webhooks/whatsapp` público; painel apenas no endereço da VPN |
| `systemd/rocha-reis-panel.service` | Painel em 127.0.0.1, `X-Forwarded-*` aceito só do proxy local, endurecido (`ProtectSystem`, `UMask=0077`) |
| `systemd/rocha-reis-backup.service` + `.timer` | Backup diário com `ops backup-agendado` (sem segredos na linha de comando) |

O teste `tests/test_phase13_ops.py::test_reference_deploy_configs_are_safe` confere estas regras.
