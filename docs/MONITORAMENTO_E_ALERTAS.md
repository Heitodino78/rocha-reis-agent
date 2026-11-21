# Monitoramento e alertas (Fases 17 e 18)

**Fase 18:** (1) a unidade `rocha-reis-alertas.service` **não conseguia ler o banco** sob o systemd real
(SQLite em WAL precisa gravar o índice `-shm` até para ler; o diretório estava somente leitura) — todo
ciclo dava `banco_inacessivel` CRÍTICO e escondia os alertas verdadeiros. Corrigido (escrita só no
diretório do banco; consulta continua `mode=ro`). (2) Dois alertas novos sobre o estado do job de backup:
`backup_falhando` e `restauracao_falhou`. (3) Cada código tem teste (antes só 7 de 20) e a tabela abaixo é
conferida contra o código. (4) Alertas provocados com os serviços reais — ver `STATUS_FASE_18.md` §D.

**Implementado e testado localmente:** `ops alertas` avalia as métricas que já existiam (`ops metricas`)
e acrescenta idade das filas, batimento do worker, disco, tamanho do banco, cópia externa e falhas de
2º fator. **Nenhum serviço externo de monitoramento foi conectado**; nada sai da máquina.

```sh
python -m rocha_reis_agent.ops alertas --perfil /etc/rocha-reis-agent \
    --batimento /var/lib/rocha-reis-agent/estado/worker.json [--externo ESTADO]
# saída JSON; código 0 = OK, 1 = ATENÇÃO, 2 = CRÍTICO
```

No servidor, `rocha-reis-alertas.timer` roda a cada 5 min; código ≠ 0 marca a unidade como falha, que
aparece em `journalctl -u rocha-reis-alertas` e aciona `rocha-reis-alerta@` (registro com prioridade
alta: `journalctl -p err -t rocha-reis-alerta`). Canal de aviso para pessoas (e-mail, mensageria):
**decisão futura** do salão/agência.

## Indicadores e limites

| Código | Nível | Condição (padrão) |
|---|---|---|
| `entrada_parada` | CRÍTICO | Mensagem recebida esperando > 5 min (worker parado/travado) |
| `entrada_acumulada` | ATENÇÃO | > 50 mensagens na fila de entrada |
| `entrada_falhou` | ATENÇÃO | Mensagens com falha de processamento |
| `capacidade_esgotada` | CRÍTICO | Limite de conversas ativas atingido nas últimas 24 h |
| `saida_parada` | ATENÇÃO | Resposta esperando envio > 10 min (sem interrupção manual) |
| `envio_incerto` | CRÍTICO | Envio UNKNOWN — decidir antes de reenviar |
| `envio_falhou` | ATENÇÃO | Envio FAILED |
| `envios_interrompidos` | ATENÇÃO | Interrupção de envios ativa |
| `cliente_esperando` | ATENÇÃO | Cliente aguardando pessoa além do tempo de urgência do painel |
| `atendimento_orfao` | CRÍTICO | Atendimento em andamento com funcionário desativado |
| `falhas_login` / `falhas_2fa` | ATENÇÃO | > 30 logins / > 10 códigos errados em 24 h |
| `falha_armazenamento` | ATENÇÃO | Gravações revertidas em 24 h |
| `disco_baixo` / `disco_cheio` | ATENÇÃO / CRÍTICO | < 15% / < 5% livre no volume do banco |
| `banco_perto_do_limite` | ATENÇÃO | ≥ 80% de `storage_max_bytes` |
| `worker_sem_batimento` | CRÍTICO | Sem batimento há > 180 s |
| `backup_atrasado` | CRÍTICO | Último backup válido > 26 h (ou nenhum) |
| `backup_falhando` | ATENÇÃO | Última rodada do backup automático falhou (Fase 18; antes só aparecia depois de 26 h) |
| `restauracao_falhou` | CRÍTICO | Último `ops teste-restauracao` falhou (Fase 18; antes não havia alerta) |
| `copia_externa_atrasada` | ATENÇÃO | Cópia externa > 26 h ou nunca feita |
| `banco_inacessivel` | CRÍTICO | Banco não pôde ser lido |

Sinais complementares: `/health` (vida) e `/ready` (banco, esquema, WAL, disco ≥ 50 MB) em cada
aplicação — usados pela atualização; o `/ready` do painel só pela VPN/127.0.0.1.

Saída sem conteúdo pessoal: só contagens, idades e códigos (teste garante ausência de telefone e texto).
