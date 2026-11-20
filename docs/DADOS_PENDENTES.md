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
- [ ] Versão da Graph API a adotar e confirmação, com conta de teste, do formato real de `from_user_id`/BSUID, `biz_opaque_callback_data` em status e envio para BSUID.
- [ ] Textos aprovados para respostas reais (hoje todas carregam rótulo de simulação).
- [ ] Comportamento desejado para áudio/imagem/outros tipos (hoje: registrados sem resposta).
- [ ] Política operacional para envios UNKNOWN/FAILED (quem verifica, em quanto tempo).
- [ ] Limites de capacidade e retenção para clientes reais (hoje 1000 eventos por conversa e 100 conversas, valores sintéticos).
- [ ] Hospedagem do banco durável com cifragem e backup (o volume de 1 TB é vfat e não serve para dados).

## Booksy

- [x] O salão utiliza Booksy.
- [x] No MVP, compartilhar link para agendamento em vez de usar API.
- [x] O usuário informou que já enviou e-mail sobre a API.
- [ ] Receber e validar o link do perfil Booksy.
- [ ] Verificar se, no futuro, Booksy responde e autoriza integração **somente se houver decisão de reabrir o escopo**.

## Condição para uso de dados reais

Antes de importar conversas, telefones ou cadastro de clientes: obter autorização do responsável e definir finalidade, acesso, retenção e segurança adequados. Antes de comercializar/implantar, verificar as condições jurídicas aplicáveis à prestação do serviço e à responsabilidade pelo tratamento dos dados.
