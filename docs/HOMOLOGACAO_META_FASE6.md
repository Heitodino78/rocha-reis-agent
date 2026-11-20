# Homologação controlada da integração Meta (Fase 6)

**Estado:** Fase 6A IMPLEMENTADA e TESTADA LOCALMENTE (offline, transporte fake).
Fase 6B **NÃO EXECUTADA**: depende de autorização explícita e específica. Este
documento é o guia técnico e o roteiro (runbook) do ensaio. Ele não autoriza nada.

## 1. Camadas de proteção (todas precisam passar)

| # | Verificação | Onde | Falha → |
|---|---|---|---|
| 1 | `APP_ENV=homologation` com banco próprio `*.homolog.synthetic.sqlite3` | `config.py` | não inicia |
| 2 | Arquivo de autorização válido (0600, do usuário, sem symlink, esquema, frase de confirmação, período ≤ 72h) | `channels/homologation.py` | não inicia |
| 3 | Transporte real: `WHATSAPP_TRANSPORT=graph` **e** trava de código `HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED` (hoje `False`) | `config.py`, `bootstrap.build_transport` | configuração recusada |
| 4 | Configuração válida: três segredos distintos, token exclusivo, versão explícita | `config.py` | configuração recusada |
| 5 | Número comercial da autorização = configuração = linha da outbox | config + guarda | não inicia / SUPPRESSED |
| 6 | Destinatário na allowlist (telefone ou BSUID); remetente fora da allowlist nem é persistido | guarda + ingestor | SUPPRESSED / ignorado |
| 7 | Limites: total, por destinatário, por execução, por minuto (retries contam) | guarda + `channel_send_ledger` | aguarda (outbox intacta) |
| 8 | Janela de atendimento (23h, evidência obrigatória) | `send_policy.py` | SUPPRESSED |
| 9 | Estado/sessão da conversa iguais ao momento da geração (pausa humana) | `send_policy.py` | SUPPRESSED |
| 10 | Sem interrupção emergencial (banco) e sem `CHANNEL_DISPATCH_HALTED=true` | claim / despachante | nada é tentado |

A trava de produção `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED` não foi alterada e
é independente: abrir uma das travas não abre a outra (testado). Uma variável de
ambiente sozinha nunca ativa envio (testado). A CLI local nunca usa o transporte real.

## 2. Ambiente isolado

```text
~/.local/share/rocha-reis-agent/homolog/        (ext4, diretório 0700)
├── ensaio.homolog.synthetic.sqlite3             banco exclusivo do ensaio (0600)
├── autorizacao.json                             preenchido pelo responsável (0600)
└── logs/canal-homolog.log                       log técnico privado (0600)
```

- Nunca no volume vfat do projeto nem em `/tmp`.
- Segredos de homologação distintos dos de qualquer outro ambiente, injetados no
  processo por gerenciador de segredos ou pelo shell. Nunca em `.env` versionado.
- Limpeza ao fim: `local_cli halt`, exportar métricas/rastreamento sem PII, e depois
  o **responsável** apaga o diretório do ensaio. O agente não apaga dados.

Modelo de autorização: [`fixtures/homolog/authorization.example.json`](../fixtures/homolog/authorization.example.json)
(valores fictícios; em `0644` no repositório, portanto recusado se usado como está).

## 3. Interrupção emergencial e desligamento seguro

1. `python -m rocha_reis_agent.channels.local_cli halt "motivo"`: grava a
   interrupção no banco. Todas as instâncias param de reivindicar envios, inclusive
   RETRY. A outbox e a entrada continuam íntegras. É auditado.
2. Alternativa sem banco: reiniciar o worker com `CHANNEL_DISPATCH_HALTED=true`
   (só interrompe; não desfaz um halt gravado).
3. Desligamento completo: parar o worker → `WHATSAPP_ENABLED=false` (remove as rotas
   do webhook) → encerrar o túnel → revogar ou expirar o token no painel Meta.
4. Analisar pendências: `metrics`, `status`, `trace ID`. Linhas UNKNOWN exigem
   reconciliação ou decisão humana (`resend-uncertain`, com confirmação).
5. Retomar só com `resume-dispatch RESUME_DISPATCH`, depois de entender a causa.

## 4. Webhook de homologação (exposição: somente na Fase 6B)

Requisitos verificados na documentação Meta (19/11/2026):
- HTTPS com certificado TLS **válido**; autoassinado não é aceito.
- GET de verificação com `hub.mode`, `hub.verify_token` e `hub.challenge`.
- POST assinado (`X-Hub-Signature-256`).
- A Meta reenvia por até **36h** quando não recebe 200.
- Lotes de até 1000 atualizações.
Timeout e tamanho máximo não estão documentados na página; o limite local é 256 KiB.

Procedimento proposto (executar só com autorização para expor o endpoint):
1. Uvicorn somente em `127.0.0.1`, `--no-access-log` (a query do GET contém o Verify Token).
2. Túnel HTTPS temporário com certificado válido (por exemplo, serviço de túnel da
   equipe, aprovado pelo responsável), apontando **apenas** para `/webhooks/whatsapp`.
   Não há outras rotas além de `/health`.
3. Configurar o callback no app Meta de teste com o Verify Token de homologação.
4. Inscrever somente o campo `messages`.
5. Encerramento: remover a inscrição/URL no painel, encerrar o túnel e parar o servidor.
   O túnel nunca fica aberto sem alguém acompanhando.

## 5. Versão da Graph API

A página oficial de versões (consultada em 19/11/2026) lista **v26.0** (29/07/2026)
como a mais recente. O guia de versionamento menciona v27.0, mas a página de
changelog da v27.0 retorna 404. O exemplo de "get started" usa v23.0.
**Decisão:** manter **v26.0** (validada localmente na Fase 5) e verificar a v27.0 no
início da Fase 6B. Chamadas sem versão usam a versão padrão do app; por isso a
versão é sempre explícita. v20.0 e anteriores estão expiradas.

## 6. Custos (sem estimativas)

Página de preços Meta (19/11/2026, texto literal): mensagens não-template só dentro
da janela de 24h; cobrança por mensagem **entregue**; desde 01/10/2026 mensagens de
serviço voltaram a ser cobradas, com **1.000 mensagens de serviço entregues
gratuitas por mês por número**. Valores por mercado estão nas tabelas da Meta, que
não foram consultadas. Os limites de contagem da autorização são o controle de
custo aplicado. Gasto monetário não é verificável localmente e não é estimado.

## 7. Roteiro 6B (somente após autorizações específicas)

Pré-requisitos por escrito:
- conta/app de desenvolvimento e número **de teste** (nunca o número do salão);
- destinatários (equipe) e responsável pela execução;
- limites, período, recursos que podem ser criados/expostos, operações.

Token de usuário de sistema com `whatsapp_business_messaging` e
`whatsapp_business_management` (e `business_management`, conforme o "get started"),
configurado por mecanismo seguro. **Nunca enviado por chat ou gravado no repositório.**

Marcos com parada humana entre eles:
1. Conta, IDs e versão confirmados.
2. Trava de homologação alterada em branch dedicada, com revisão.
3. Túnel aberto.
4. Webhook verificado.
5. Mensagem recebida (assinatura válida e persistência).
6. Resposta inspecionada antes de enviar.
7. **Autorização do primeiro envio.**
8. Envio e registro da resposta HTTP.
9. Status recebidos e correlacionados.
10. Mensagem contextual; Booksy (link aprovado); pausa humana; reinício.
11. Encerramento: halt, trava de volta a `False`, túnel fechado, token revogado.

Evidências: payloads anonimizados, códigos e status observados, diferenças em
relação à documentação. O que não for testado é registrado como NÃO VALIDADO.

## 8. Matriz dos 50 cenários exigidos

| Cenário | Automatizado (offline) | Verificação manual 6B |
|---|---|---|
| A1–A5 recebimento, duplicata, reinício, rajada, múltiplos | `test_homologation_e2e`, `test_whatsapp_webhook`, `test_whatsapp_outbox` (subprocessos) | 5, 10 |
| A6–A8 inválido, assinatura, tipo não suportado | `test_whatsapp_webhook`, `test_whatsapp_processing` | — |
| A9 sem telefone (BSUID) | `test_homologation_e2e`, `test_whatsapp_processing` | se a conta permitir |
| A10 fora de ordem | `test_homologation_e2e`, `test_delivery_status_and_policy` | 9 |
| B11–B18 atendimento e Booksy | `test_attendance`, `test_booking`, `test_whatsapp_processing`, `test_simulator` | 10 |
| B19–B21 humano e retomada | `test_handoff`, `test_homologation_e2e`, `test_whatsapp_processing` | 10 |
| B22 prompt injection | `test_ai_safety`, `test_whatsapp_processing` | — |
| C23–C27 autorizado, allowlist, número, desabilitado, janela | `test_homologation`, `test_graph_transport`, `test_delivery_status_and_policy` | 7–8 |
| C28–C33 auth, destinatário, limite, timeout, UNKNOWN, reenvio | `test_graph_transport`, `test_whatsapp_outbox`, `test_homologation` | códigos reais |
| C34–C35 falha após commit, reinício | `test_whatsapp_outbox` (os._exit), `test_homologation_e2e` | 10 |
| D36–D43 status | `test_delivery_status_and_policy`, `test_homologation_e2e` | 9 |
| E44–E45 segredo ausente ou inválido | `test_whatsapp_webhook`, `test_homologation` | — |
| E46 acesso administrativo externo | `test_whatsapp_security` (só `/health` e webhook) | 3 |
| E47 contato não autorizado | `test_homologation` (entrada e saída) | — |
| E48–E49 token e PII em erros e logs | `test_graph_transport`, `test_homologation` (log privado), `test_homologation_e2e` | revisar log |
| E50 interrupção emergencial | `test_homologation`, `test_homologation_e2e` | 11 |
