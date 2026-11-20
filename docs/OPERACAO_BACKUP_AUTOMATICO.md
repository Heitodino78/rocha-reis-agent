# Operação do backup automático

Situação em 20/11/2026 (Fase 13): **implementado e testado localmente** com dados fictícios.
O agendamento por systemd é **referência** (não instalado). O destino secundário é um **segundo
diretório no mesmo disco** — valida o procedimento, **não** protege contra perda do servidor.

## 1. O que uma rodada faz (`python -m rocha_reis_agent.ops backup-agendado --perfil DIR`)

1. Impede execução simultânea (lock exclusivo).
2. Confere o perfil (0700), o banco, e — onde exigido (pré-produção) — a cifragem **verificada**
   do volume.
3. Confere a chave: existe, é 0600 do próprio usuário e **não** está dentro de um diretório de backups.
4. Cópia consistente (API online do SQLite, em memória), cifrada (AES-256-GCM) e verificada.
5. Cópia para o destino secundário: `.partial` → fsync → conferência do conteúdo → renomear →
   verificação completa da cópia.
6. Retenção nos dois destinos (`manter` mais recentes + `dias`); nunca remove o último backup
   válido nem arquivos não identificados.
7. Grava `estado-backup.json` (0600): última tentativa, último sucesso, falhas consecutivas,
   último erro (mensagem segura), cópias em cada destino, situação do secundário.
8. Saída diferente de zero em qualquer falha (para o systemd/monitoramento perceberem).

Configuração (opcional) no `perfil.json`:

```json
"backup": {"primario": "backups", "secundario": "backups-secundario", "chave": "chaves/backup.key",
           "manter": 14, "dias": 30}
```

## 2. Comandos

| Ação | Comando |
|---|---|
| Uma rodada | `ops backup-agendado --perfil DIR` |
| Teste de restauração (isolado; apaga a cópia de teste ao final) | `ops teste-restauracao --perfil DIR` |
| Indicadores | `ops ambiente --perfil DIR` (seção `seguranca.backup_automatico`) ou `ops metricas` |
| Retenção manual | `ops reter DIR --manter N [--dias D] --confirmacao APAGAR:N` |
| Ensaio de desastre | `ops ensaio-recuperacao --perfil DIR --destino DIR_VAZIO` |

O painel (Sistema, só ADMIN) mostra: em dia/atenção, último sucesso, falhas seguidas, número de
cópias e o resultado do último teste de restauração — sem caminhos nem segredos.

## 3. Agendamento (referência, não instalado)

`deploy/systemd/rocha-reis-backup.service` + `rocha-reis-backup.timer`: diário às 03:15 com
`Persistent=true` (roda depois se a máquina estava desligada). Só o caminho do perfil vai na
linha de comando; a chave é um arquivo 0600 indicado no perfil. Instalação futura:

```sh
sudo cp deploy/systemd/rocha-reis-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rocha-reis-backup.timer
systemctl list-timers rocha-reis-backup.timer
```

Sugestão: teste de restauração semanal (outro timer) e alerta se `idade_horas` > 26 ou
`falhas_consecutivas` > 0.

## 4. Falhas e o que fazer

| Situação | Comportamento verificado | Ação |
|---|---|---|
| Destino secundário indisponível | Primário criado e verificado; estado "falhou" no secundário; saída de erro | Remontar/corrigir o destino; a próxima rodada copia de novo |
| Falha de escrita na cópia | `.partial` removido; nada incompleto fica | Ver espaço em disco |
| Execução interrompida | Sobra `.partial` é limpa na rodada seguinte | Nada |
| Chave ausente, frouxa ou dentro dos backups | Falha antes de copiar | Restaurar a chave da guarda |
| Chave trocada | Teste de restauração falha e é registrado | Voltar a chave certa; se perdida, fazer backup novo com a nova |
| Backup corrompido no diretório | Ignorado pela retenção, listado como não identificado | Investigar e remover manualmente |
| Duas rodadas ao mesmo tempo | A segunda é recusada | Nada |

## 5. Limitações

- Destino secundário no mesmo disco (simulado). Backup externo real exige armazenamento em outro
  provedor/região — a abstração `LocalDirectory` é o ponto de troca.
- Agendamento e alertas não instalados; nenhum aviso automático fora da máquina.
- RPO/RTO não acordados (ver `PLANO_RECUPERACAO_DESASTRES.md`).
