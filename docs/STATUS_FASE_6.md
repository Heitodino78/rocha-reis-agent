# Relatório técnico — Fase 6 (homologação controlada da integração Meta)

**Data:** 19/11/2026 · **Executor:** Claude

## FASE 6A CONCLUÍDA LOCALMENTE — FASE 6B NÃO EXECUTADA

| Classificação | Estado |
|---|---|
| PLANEJADO | 6B (ensaio real), operação humana real, dados oficiais, IA real, infraestrutura de produção |
| IMPLEMENTADO | Ambiente `homologation`, autorização em arquivo, trava de homologação, allowlist (entrada e saída), limites com registro, interrupção emergencial, métricas/rastreamento/log privado, CLI de prontidão |
| TESTADO LOCALMENTE | **512 aprovados, 0 falhos, 0 ignorados** (3 execuções consecutivas); 9 mutações de proteções detectadas |
| HOMOLOGADO COM A META | **Não** |
| VALIDADO PELO SALÃO | Não |
| IMPLANTADO | Não |

## 1. Resumo executivo

O sistema tem agora uma estrutura que permite validar a integração real de forma
controlada. No ambiente `homologation`, isolado, cada envio precisa passar por
dez verificações (ver `HOMOLOGACAO_META_FASE6.md` §1). Uma autorização explícita em
arquivo privado define número, destinatários, período e limites. Uma trava de
código própria, hoje `False`, impede o envio real. Toda a 6A roda offline com
transporte fake. **Nenhuma conexão externa, recurso Meta, túnel ou mensagem real foi feito.**

## 2. Estado inicial

- **Git:** commit `020739f` (Fases 0–4, publicado), com a Fase 5 não commitada
  (24 modificados e 7 novos). Sem segredos nem bancos entre os arquivos.
- Sem `CLAUDE.md` nem skills no projeto.
- Suíte: **464 aprovados**, confirmando o relatório da Fase 5.

## 3. Cobertura da leitura

Todo o código, testes, migrações, configuração e documentos rastreados foram lidos
ou escritos por mim nesta sessão (Fases 4–5). O estado foi conferido com
`git status`/`git diff` antes de editar. Não foram lidos `.git/`, caches e venvs.
Documentação Meta consultada: versões, versionamento, changelog v27.0 (404),
webhooks, "get started" e preços.

## 4. Testes anteriores

464 aprovados antes de qualquer alteração. Continuam todos aprovados, sem
modificação nos testes existentes.

## 5. Alterações

**Criados:**
- `rocha_reis_agent/channels/homologation.py`, `rocha_reis_agent/observability.py`
- `persistence/migrations/005_homologation_controls.sql`
- `tests/test_homologation.py` (47), `tests/test_homologation_e2e.py` (1, multiprocesso), `tests/homolog_support.py`
- `fixtures/homolog/authorization.example.json`
- `docs/HOMOLOGACAO_META_FASE6.md`, `docs/OPERACAO_HUMANA_REQUISITOS.md`, `docs/CHECKLIST_PRODUCAO.md`, `docs/STATUS_FASE_6.md`

**Modificados:**
- `config.py` (ambiente, trava, campos), `bootstrap.py` (autorização, filtro, despachante)
- `main.py` (bloqueio de início, contadores, log), `api/whatsapp.py` (contadores de rejeição)
- `channels/service.py` (filtro de remetentes, guarda, halt por configuração)
- `persistence/sqlite_channel.py` (halt, guarda, registro, métricas, rastreamento), `persistence/sqlite.py` (schema 5)
- `channels/local_cli.py` (comandos novos)
- `.gitignore`, `.env.example`, `README.md`, `docs/{PLANO_DE_EXECUCAO,ARQUITETURA_E_DECISOES,CONTEXTO_MESTRE,DADOS_PENDENTES}.md`

## 6. Arquitetura de homologação

```text
webhook (assinatura) → filtro allowlist (homolog) → inbox → núcleo (FakeAI, validação)
  → outbox → claim: halt? → política (janela/estado) → guarda (período, operação,
    número, allowlist, limites via ledger) → SENDING + ledger → transporte
    (fake; graph só com trava de homologação) → resultado → status correlacionado
métricas (channel_metrics + agregados) · trace por inbox_id · log privado 0600
```

## 7. Versão Meta

**v26.0 mantida.** É a mais recente na página oficial de versões. A v27.0 aparece
no guia de versionamento, mas sem changelog acessível (404), então não há evidência
de compatibilidade. A atualização fica **pendente** para o início da 6B. Versão
sempre explícita, porque chamadas sem versão usam o padrão do app.

## 8. Configurações de ambiente

- `APP_ENV=homologation` exige: banco `*.homolog.synthetic.sqlite3`,
  `HOMOLOG_AUTHORIZATION_FILE`, canal habilitado, segredos próprios.
- Fora da homologação, o banco e a autorização de homologação são recusados.
- Novas variáveis: `CHANNEL_DISPATCH_HALTED`, `CHANNEL_LOG_PATH`.
- Transporte padrão: fake.

## 9. Proteções de ativação

Trava `HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED=False` (nova) e trava de produção
`GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED=False` (intacta). Elas são independentes.
Testado:
- variáveis de ambiente sozinhas não ativam o envio real;
- a CLI recusa o transporte real;
- autorização inválida impede o início da aplicação.

## 10. Allowlist

De 1 a 10 telefones ou BSUIDs, somente no arquivo de autorização (nunca no
código). Na entrada, remetentes fora da lista são descartados sem persistência
(contador `webhook_ignored_not_allowlisted`). Na saída, destinatário fora da lista
→ SUPPRESSED `recipient_not_allowlisted`. O registro de envios guarda só um HMAC
do destinatário.

## 11. Limites

Total, por destinatário, por execução e por minuto são obrigatórios, com tetos
rígidos no código (100/30/20/10). Retries contam. Ao atingir um limite, o envio
aguarda e a outbox fica intacta. Período máximo de 72h. Custo monetário não é
verificável localmente e não é estimado; os limites de contagem são o controle.

## 12. Interrupção emergencial

`local_cli halt "motivo"` grava no banco e é auditado. Com ele, nenhuma tentativa nem
retry acontece, em nenhuma instância e em nenhum ambiente. A outbox é preservada e
a entrada continua processando. `CHANNEL_DISPATCH_HALTED=true` só interrompe.
A retomada exige `resume-dispatch RESUME_DISPATCH`. O procedimento de desligamento
seguro está em `HOMOLOGACAO_META_FASE6.md` §3.

## 13. Webhook de testes

O código existente (verificação, assinatura, limites, persistência antes do 200,
deduplicação) já atende aos requisitos. O plano de exposição por túnel HTTPS com
certificado válido e o procedimento de encerramento estão documentados. A abertura
do túnel é da 6B e **não foi feita**.

## 14. Fluxo ponta a ponta

`test_homologation_e2e.py` executa 22 processos independentes da CLI real, cada
etapa num processo novo:
1. verificação de prontidão;
2. webhook válido, duplicado e de remetente não autorizado;
3. resposta e envio fake;
4. pergunta contextual;
5. status fora de ordem, duplicado e sem correlação;
6. pedido de atendimento humano e pausa;
7. interrupção, resposta retida, retomada e envio;
8. métricas e rastreamento sem PII;
9. log privado.

## 15–16. Testes e resultados reais

```text
pytest -q (início)          → 464 passed
pytest -q (final, ×3)       → 512 passed, 0 failed, 0 skipped (~37 s)
mutações (9, restauradas)   → todas detectadas: allowlist, halt, ledger, filtro de entrada,
                              trava homolog, permissão do arquivo, isolamento, período, halt por config
```

Mapeamento dos 50 cenários: `HOMOLOGACAO_META_FASE6.md` §8.

## 17–18. Problemas identificados e correções

1. **Defeito no código:** `HomologationError` herda de `ValueError` e era capturado
   pelo tratamento de JSON. A recusa por permissão `0644` aparecia como "JSON
   inválido". O arquivo continuava bloqueado, mas o diagnóstico ficava errado. Corrigido.
2. Defeito no helper de teste, que sobrescrevia a autorização. Corrigido.
3. Duas expectativas de teste estavam erradas e foram corrigidas, não o código:
   o limite de 3/min de fato bloqueava o 4º envio, e um status repetido era
   deduplicado antes de virar anomalia.

## 19. Pendências

- Decisão sobre novo contato após exclusão.
- Prazo das chaves de idempotência.
- Operação humana real.
- Worker contínuo.
- Alertas automáticos.
- v27.0.
- Tabela de preços vigente.
- Dados oficiais e textos aprovados.

## 20–22. Segurança, privacidade e backup

- Revisão de logs: só nomes estáticos e campos técnicos, sem PII nem segredos (testado).
- Métricas e rastreamento sem PII (testado).
- Arquivos privados `0600` (banco, log, autorização).
- Nenhum banco no volume vfat.
- Sem cifragem em repouso e sem backup: plano e itens em `CHECKLIST_PRODUCAO.md`.
- Não se afirma conformidade com a LGPD.

## 23. Controle de versão

Antes do commit:
- revisados `git status`/`diff`;
- busca de segredos sem ocorrências (só falsos positivos de hash no lock);
- `.gitignore` reforçado (autorizações reais, bancos de homologação);
- FAT32 com `core.filemode=false` e `core.symlinks=false`.

O item 12 do pedido proíbe commit e push sem aprovação; o item 15 da ordem de
execução pede "fazer o commit para o GitHub". Tratei o item 15 como aprovação
explícita para **commit e push no repositório já existente**. Nenhum repositório
foi criado ou republicado, e o histórico não foi reescrito.

## 24. Atendimento humano operacional

Ver `OPERACAO_HUMANA_REQUISITOS.md`. No núcleo existe a máquina de estados com
pausa e retomada explícita. Só como simulação, existem as ações de operador na
CLI. Faltam inbox, autenticação, notificação e envio das respostas humanas.
**O produto não está pronto para clientes.**

## 25–26. Estado das etapas

**6A: concluída localmente.** **6B: não executada.** Não houve autorização, conta,
número de teste, token, túnel ou envio.

## 27. Evidências reais

Nenhuma. Não há integração real a evidenciar.

## 28. Limitações

Mocks e fakes não demonstram compatibilidade com a Meta. O FakeAIProvider não
demonstra a qualidade de um modelo generativo. SQLite tem um único escritor.
O worker roda sob demanda.

## 29. Checklist de produção

Ver `CHECKLIST_PRODUCAO.md`.

## 30. Próxima fase

**Pronto para iniciar a 6B tecnicamente**, desde que os bloqueios abaixo sejam
resolvidos pelo responsável:
1. autorização escrita com todos os campos do §7 de `HOMOLOGACAO_META_FASE6.md`;
2. app e número de teste;
3. mecanismo seguro para o token;
4. túnel HTTPS aprovado.

A 6B então troca a trava de homologação em branch dedicada e segue o roteiro com
paradas humanas. Em paralelo, e sem dependência da Meta: decisões do controlador
(retenção e exclusão) e especificação da operação humana com o salão.
