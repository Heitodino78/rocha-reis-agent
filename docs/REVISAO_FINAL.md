# Revisão final do projeto (20/11/2026)

Revisão de ponta a ponta antes da homologação 11B: atendimento ao cliente, canal WhatsApp, fila de
envio, painel, persistência, segurança, interface e qualidade de código. Feita localmente, com dados
fictícios; nenhuma integração real, nenhum deploy. **A 11B continua pendente.**

Segurança (sessão, CSRF, permissões, 2FA, webhook, fila sem reenvio às cegas, IA sem trocar fatos,
travas, backup e dependências com hash) **sem falha nova**.

## 1. Corrigido nesta revisão — `0.14.0rc1.post3`

| # | Defeito (comprovado) | Correção | Teste |
|---|---|---|---|
| 5 | "Meus atendimentos" e "Comigo agora" contavam atendimentos já finalizados (finalizar não remove o responsável, que é o que mantém o histórico visível para a atendente) | Contagem e filtro só com atendimentos em andamento; finalizados ficam em "Encerradas" | `test_mine_counts_and_lists_only_ongoing_attendances` |
| 6 | Desativar um funcionário deixava os atendimentos dele presos (o comentário dizia que liberava, o código não fazia) | Os atendimentos em andamento voltam para "Aguardando atendimento" com transição nova e auditada (`owner_deactivated` / `conversation_requeued`); a automação continua suspensa; alerta na Visão geral se algum ficar preso | `test_deactivating_returns_ongoing_attendances_to_the_queue` e outros 2 |
| 7 | Cada mensagem apagava e regravava todo o histórico; passado o limite (100), as posições eram renumeradas: "Carregar mensagens anteriores" podia repetir mensagens e **o aviso de nova mensagem parava de aparecer** naquela conversa | Grava só as mensagens novas, com posição que só cresce; apaga apenas as que passam do limite | `test_history_positions_stay_stable_past_the_limit`, `test_new_message_notice_keeps_arriving_past_the_limit` |
| 10 | Redefinir senha existia na API, mas não no painel | Botão "Redefinir senha" na Equipe (senha repetida, troca obrigatória, sessões encerradas, auditado) | `test_admin_resets_password_*` (API e Chromium) |
| — | Linha do tempo mostrava códigos internos em inglês ("assume", "resolve") | Motivos em português | `test_deactivation_requeues_and_timeline_explains_why` |

Controle negativo: os 5 testes dos defeitos 5, 6 e 7 **falham** no código anterior e passam no novo.
Regressão: **977 pytest** e **46 Chromium** aprovados; roteiro da demonstração 13/13.

O limite de 100 mensagens por conversa foi mantido (minimização de dados); se o salão quiser guardar
mais, é uma decisão de retenção (`SIM_MAX_HISTORY_ENTRIES`, até 1000).

## 2. Para decisão do salão antes de clientes reais (não é só código)

1. **Áudio, foto, figurinha e outros tipos** são descartados sem resposta e sem aparecer no painel
   (`channels/service.py`). Proposta: registrar "o cliente enviou um áudio/foto", mandar para a fila
   humana e responder com texto curto aprovado.
2. **Textos ao cliente são todos de simulação** ("Registrei o pedido nesta simulação…",
   "[SIMULAÇÃO LOCAL]" mesmo com base não fictícia). Os textos de produção precisam ser escritos e
   aprovados pelo salão.
3. **A equipe não sabe quem é o cliente** (painel mostra só "Cliente ··código"). Sem nome/telefone a
   atendente não acha a pessoa no Booksy. Decisão de privacidade × operação do responsável pelos dados.
4. **Sem aviso fora do painel e sem resposta fora do expediente.** Quem pede uma pessoa com o painel
   fechado não recebe expectativa e ninguém fica sabendo; passadas ~23h, a resposta livre é bloqueada e
   não há mensagens-modelo (templates) aprovadas.

## 3. Melhorias registradas (não aplicadas)

- Regras de atendimento: "cabelo humano" (mega hair) e "por pessoa" mandam para atendimento humano
  (`intents.py`); perguntas frequentes só são reconhecidas com o texto idêntico ao cadastrado.
- Lista de serviços enviada ao cliente cortada em 5 (ou 12 com categorias) sem avisar que há mais.
- Qualidade: sem lint, verificação de tipos e integração contínua configurados; 3 imports sem uso
  (`ai/budget.py`, `panel/service.py`, `evaluation/runner.py`); funções longas (`AgentService.handle`,
  `build_panel_router`) e `app.js` num único arquivo.
- Interface: sem escolha manual de tema; celular físico e leitor de tela reais não testados.
