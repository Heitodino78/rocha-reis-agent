# Atualização e reversão (Fases 17 e 18)

**Implementado e testado localmente** (raiz descartável, serviços reais da versão instalada, dados
sintéticos). Comandos: `ops empacotar`, `ops atualizar`, `ops reverter`. Nenhum commit, tag ou release é
criado por eles.

**Fase 18 — `ops atualizar` controlando o systemd REAL** (systemd do usuário; `--parar`/`--iniciar` com
`systemctl --user`, venv criado do `requirements.lock` como no servidor):

| Cenário | Resultado medido |
|---|---|
| Versão nova válida | `ATUALIZADA` — todas as 9 etapas, inclusive backup verificado e migração ensaiada em cópia |
| Versão que não fica pronta | `REVERTIDA` — serviços prontos de novo na versão anterior |
| `ops reverter` depois disso | volta à versão boa **anterior** (não à que falhou) |

Três defeitos só apareceram assim (os testes da Fase 17 usavam `create_venv=False`) e foram corrigidos:
1. **Toda atualização real era cancelada** (`CANCELADA_ANTES_DA_TROCA`, `FileNotFoundError`): o
   `RUNTIME.json` guardava o Python da pasta de preparo, renomeada logo depois; o ensaio de migração não
   achava o interpretador. A falha era segura (serviços religados), mas impedia qualquer atualização.
2. **O instalador alterava o Python do sistema:** o `chmod` "somente leitura" seguia o link
   `.venv/bin/python` até o interpretador base.
3. **`ops reverter` reativava a versão quebrada:** depois de uma reversão automática, o registro de
   versões apontava a versão que falhou como "anterior". Agora o registro volta ao de antes da tentativa.

Regressões em `tests/test_phase18_infra.py` (cada uma falha no código antigo).

## 1. Fluxo de `ops atualizar --pacote ARQ --raiz / --confirmacao ATUALIZAR:<versão>`

| # | Etapa | Se falhar |
|---|---|---|
| 1 | Pacote conferido arquivo a arquivo (hash, sem extras, sem links, sem `..`) | Recusa; nada muda |
| 2 | Compatibilidade: Python mínimo; esquema do pacote **≥** esquema do banco | Recusa; nada muda |
| 3 | Versão nova instalada ao lado (`releases/<id>`), código somente leitura | Recusa; nada muda |
| 4 | **Serviços parados** (o backup passa a ser o ponto exato de restauração) | — |
| 5 | Backup cifrado do banco + verificação | Religa a versão atual (`CANCELADA_ANTES_DA_TROCA`) |
| 6 | Migração **ensaiada numa cópia** (restaurada do backup, no volume cifrado) com o código novo + `integrity_check` + `foreign_key_check` | Religa a versão atual; banco em uso intocado |
| 7 | Troca atômica do link `current` | — |
| 8 | Serviços iniciados; `/ready` do webhook e do painel em até 20–30 s | Ver §2 |
| 9 | Registro em `estado/implantacoes.jsonl` (0600); mantém as 3 versões mais recentes | — |

Durante a troca o webhook fica fora do ar por segundos; a Meta reenvia eventos não confirmados.

## 2. Falha depois da troca

- **Esquema do banco não mudou** → volta o link para a versão anterior, religa e confere `/ready`
  (`REVERTIDA`). Testado: versão nova que não sobe → reversão automática, serviço pronto de novo.
- **O banco já foi migrado** pela versão nova → a versão anterior **recusa** abrir esquema mais novo (não
  corrompe nada, mas não sobe). Resultado `REVERTIDA_AGUARDANDO_RESTAURACAO_DO_BANCO`, com o nome do
  backup. **Nunca é restaurado sozinho.** Com os serviços parados:

```sh
python -m rocha_reis_agent.ops reverter --raiz / --restaurar-banco <backup> --confirmacao RESTAURAR:<backup>
systemctl start rocha-reis-webhook rocha-reis-panel rocha-reis-worker   # conferir /ready
```

O banco substituído é guardado como cópia (`*.pre-restore-*.synthetic.sqlite3`, 0600). Mensagens que
chegaram entre o backup e a falha não estão no backup — por isso os serviços ficam parados desde o
passo 4 (a Meta reenvia o que não foi confirmado).

## 3. O que é reversível

| Mudança | Reverter o código basta? |
|---|---|
| Código, interface, regras, textos | Sim (`ops reverter --raiz /`, depois reiniciar) |
| Migração de banco (esquema novo) | **Não.** Exige restaurar o backup feito na atualização |
| Dados gravados depois da atualização | Perdem-se ao restaurar o backup (aceitar conscientemente) |
| Configuração em `perfil.json` | Manual: guardar cópia antes de editar |
| Chaves (backup, 2º fator) | Nunca rotacionar junto com atualização de código |

`ops reverter` (só código) **recusa** quando o banco está num esquema mais novo que a versão anterior e
indica a restauração.

## 4. Testes automatizados (`tests/test_phase17_server.py`)

Atualização bem-sucedida com dados preservados; confirmação errada; pacote adulterado; versão que não
sobe (reversão automática); migração quebrada pega na cópia (banco intocado); migração válida + falha
de subida (código anterior não abre o banco, restauração confirmada volta ao estado anterior); pacote
com esquema mais antigo recusado; reversão só do código.
