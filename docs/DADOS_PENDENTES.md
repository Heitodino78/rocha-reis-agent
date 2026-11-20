# Levantamento de informações — dados pendentes

> **Regra:** campos em branco são desconhecidos, não devem ser preenchidos por suposição. Não usar dados sintéticos como se fossem reais.

## Dados do salão (solicitar com aprovação)

- [ ] Link **oficial e exato** do perfil Booksy do Salão Rocha Reis.
- [ ] Nome público e tratamento preferido da marca.
- [ ] Lista atualizada de serviços e preços, incluindo variações, durações e observações.
- [ ] Profissionais e serviços atendidos por cada um (se relevante para o atendimento).
- [ ] Endereço, referência de localização e horário de funcionamento.
- [ ] Formas de pagamento, cancelamento, tolerância de atraso e demais políticas.
- [ ] FAQs mais comuns e respostas validadas pelo salão.
- [ ] Tom de voz e apresentação do agente como assistente virtual.

## Operação de atendimento

- [ ] Número de WhatsApp que será conectado, autorização do responsável e elegibilidade da conta.
- [ ] Como os atendentes humanos visualizam e respondem conversas (inbox, ferramenta, processo).
- [ ] Quem assume, quem retoma e como lidar com atendimento humano fora do expediente.
- [ ] Eventos que exigem transferência humana; horários e expectativas de resposta.
- [ ] Quem pode editar dados, acompanhar métricas e desligar o agente.

## Tecnologia, privacidade e custos

- [ ] Conta Meta Business e acesso técnico necessário à configuração oficial.
- [ ] Provedor/modelo de IA e orçamento mensal/limites aprovados.
- [ ] Hospedagem, domínio, banco, backups e responsáveis pelo acesso.
- [ ] Retenção de dados, justificativa para o tratamento e texto de privacidade revisados.
- [ ] Permissões de teste com número real, proteção de dados e contatos autorizados.
- [ ] Plano de incidentes, interrupção e suporte/manutenção após entrega.

## Integração Meta (levantados na Fase 4)

- [ ] Autorização para ensaio real: app Meta, WABA, número de teste e lista de contatos permitidos.
- [ ] Onde guardar App Secret, Verify Token, token de acesso e chave de pseudonimização (gerenciador de segredos).
- [ ] Versão da Graph API a adotar (v26.0 era a mais recente confirmada em 19/11/2026) e confirmação, com conta de teste, do formato real de `from_user_id`/BSUID, `biz_opaque_callback_data` em status e envio para BSUID.
- [ ] Textos aprovados para respostas reais (hoje todas carregam rótulo de simulação).
- [ ] Comportamento desejado para áudio/imagem/outros tipos (hoje: registrados sem resposta).
- [ ] Política operacional para envios UNKNOWN/FAILED (quem verifica, em quanto tempo).
- [ ] Limites e retenção para clientes reais: nº de conversas ativas, horas até arquivar, dias até anonimizar (Fase 5 removeu o bloqueio permanente; valores atuais são sintéticos).
- [ ] Decisão do controlador: cliente cujos dados foram excluídos a pedido (`retire`) pode voltar a conversar? Hoje o identificador permanece bloqueado.
- [ ] Prazo de guarda das chaves de idempotência (hoje mantidas indefinidamente, sem conteúdo) e plano de backup/restauração/cifragem.
- [ ] Modelo de autenticação/autorização dos operadores para a futura interface (hoje só `operador-local` simulado).
- [ ] Validação real dos códigos de erro da Graph API e da troca da trava `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED`.
- [ ] Hospedagem do banco durável com cifragem e backup (o volume de 1 TB é vfat e não serve para dados).

## Homologação (Fase 6B)

- [ ] Autorização escrita do responsável: conta/app de teste, número de teste (não o do salão), destinatários da equipe, executor, limites, período, recursos a criar/expor.
- [ ] Mecanismo seguro para o token de teste (nunca chat ou repositório).
- [ ] Túnel/infraestrutura HTTPS aprovados para expor o webhook temporariamente.
- [ ] Confirmar v27.0 vs v26.0 e a tabela de preços vigente.

## Booksy

- [x] O salão utiliza Booksy.
- [x] No MVP, compartilhar link para agendamento em vez de usar API.
- [x] O usuário informou que já enviou e-mail sobre a API.
- [ ] Receber e validar o link do perfil Booksy.
- [ ] Verificar se, no futuro, Booksy responde e autoriza integração **somente se houver decisão de reabrir o escopo**.

## Condição para uso de dados reais

Antes de importar conversas, telefones ou cadastro de clientes: obter autorização do responsável e definir finalidade, acesso, retenção e segurança adequados. Antes de comercializar/implantar, verificar as condições jurídicas aplicáveis à prestação do serviço e à responsabilidade pelo tratamento dos dados.
