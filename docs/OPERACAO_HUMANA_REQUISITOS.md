# Requisitos da operação humana (para validação com o salão)

**Estado (atualizado na Fase 7):** o painel local implementa §2 (exceto notificação externa) e §3 em versão básica — ver `STATUS_FASE_7.md`. Itens **[DECISÃO]** continuam pendentes do salão. Antes da Fase 7 este documento era só planejamento.
Este é um documento de requisitos para o salão revisar. Não define política
empresarial nem legal: os itens marcados **[DECISÃO]** dependem do responsável.

## 1. O que existe hoje

| Item | Situação |
|---|---|
| Estados AUTO → WAITING_HUMAN → HUMAN_ACTIVE → RESOLVED → AUTO (só por retomada explícita) | Implementado no núcleo; testado; persistido |
| Pausa: nenhuma resposta automática fora de AUTO; respostas geradas antes da troca são canceladas no envio | Implementado e testado |
| Cliente não executa comandos por mensagem | Implementado e testado |
| Ações de operador (assumir, responder, resolver, retomar, encerrar) | **Somente simulação** na CLI local, com identidade `operador-local` (`OperatorAssurance.SIMULATED_LOCAL`): não é autenticação |
| Resposta do operador ao cliente pelo WhatsApp | **Não existe**: hoje é só registrada no histórico simulado |
| Notificação a funcionários quando o cliente pede atendimento | **Não existe**: a mensagem automática diz explicitamente que ninguém foi notificado |
| Interface (inbox) para funcionários | **Não existe** |

## 2. Fluxo pretendido

1. **Pedido de atendimento:** cliente pede ou o backend decide (falha, reclamação).
   Estado WAITING_HUMAN, e uma notificação é enviada ao canal definido pelo salão. **[DECISÃO: canal]**
2. **Assumir:** um funcionário autenticado assume (HUMAN_ACTIVE). Só ele, ou um supervisor, responde.
3. **Contexto:** o funcionário vê o histórico limitado daquela conversa (sem outras
   conversas), o motivo do pedido e se há envio pendente ou incerto (UNKNOWN).
4. **Responder:** a resposta sai pela mesma outbox (janela de 24h, sem reenvio cego),
   registrada com a identidade verificada do funcionário.
5. **Encerrar:** RESOLVED. A automação continua suspensa.
6. **Retomar:** ação explícita e auditada volta para AUTO.
7. **Ninguém disponível:** mensagens continuam registradas sem resposta automática.
   **[DECISÃO]** texto de expectativa (horário de atendimento humano, SLA), sem
   prometer resposta imediata. Pendências com mais de X horas geram alerta. **[DECISÃO: X]**

## 3. Autenticação, permissões e auditoria (requisitos para a futura interface)

- Login individual. Nunca conta compartilhada. Autenticação forte (2FA ou SSO). Sessão com expiração.
- Papéis mínimos: **atendente** (assumir, responder, resolver as próprias conversas);
  **supervisor** (retomar automação, reatribuir, ver métricas, interromper envios);
  **administrador técnico** (configuração, sem acesso rotineiro a conversas).
  **[DECISÃO: quem ocupa cada papel]**
- Isolamento: acesso só a conversas atribuídas ou em fila. Busca por cliente
  restrita e auditada. Nenhuma exportação em massa.
- Auditoria: quem, quando, qual ação, qual conversa (pseudônimo), resultado.
  Registro somente de inclusão, retido conforme a política. **[DECISÃO: prazo]**
- Interface web com HTTPS, proteção CSRF, cabeçalhos de segurança, limite de
  tentativas de login e sem rotas administrativas no mesmo endpoint público do webhook.
- Nível de identidade `VERIFIED` substituindo `SIMULATED_LOCAL`. A CLI continua
  apenas local e nunca é exposta em rede.

## 4. Dependências técnicas

Ferramenta de inbox (construir ou adotar) **[DECISÃO]**; provedor de identidade;
canal de notificação; worker contínuo supervisionado; transporte real aprovado;
textos aprovados (sem rótulos de simulação); política de retenção.

## 5. Exclusão de dados e novo contato **[DECISÃO do controlador]**

Hoje existem dois mecanismos:
- **Retenção (`/purge`):** anonimiza conversas inativas e **não** bloqueia o cliente.
  Chaves técnicas de idempotência (hashes, sem conteúdo) são mantidas.
- **Exclusão explícita (`retire`, a pedido):** apaga o conteúdo e **bloqueia** novas
  mensagens daquele identificador (FAILED `rejected_by_core`, sem resposta).

Opções para decisão, com impactos:
1. **Manter o bloqueio:** a pessoa que pediu exclusão e volta a escrever fica sem
   atendimento automático e sem aviso. Atende a uma leitura restritiva, mas
   prejudica o atendimento.
2. **Permitir novo contato como conversa nova:** após a exclusão, uma nova mensagem
   cria uma conversa limpa, sem o histórico anterior. As chaves técnicas continuam
   impedindo reprocessar mensagens antigas.
3. **Bloquear e encaminhar a humano:** a mensagem é registrada para um funcionário decidir.

Nenhuma opção foi aplicada. A escolha cabe ao controlador dos dados, com assessoria
jurídica, e deve ser registrada antes de qualquer uso com clientes reais.
