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

## Painel de atendimento (Fase 7)

- [ ] Quem terá acesso (nomes e papéis ADMIN/ATENDENTE) e quem é o administrador responsável.
- [ ] Atendentes devem ver conversas automáticas? Podem retomar a automação? (hoje: não e não).
- [ ] Como avisar a equipe de um novo pedido de atendimento (som, push, e-mail) e em quais horários.
- [ ] Retenção da auditoria do painel e de quem pode consultá-la.
- [ ] Validação de usabilidade com funcionários reais (sem dados reais de clientes).

## Booksy

- [x] O salão utiliza Booksy.
- [x] No MVP, compartilhar link para agendamento em vez de usar API.
- [x] O usuário informou que já enviou e-mail sobre a API.
- [ ] Receber e validar o link do perfil Booksy.
- [ ] Verificar se, no futuro, Booksy responde e autoriza integração **somente se houver decisão de reabrir o escopo**.

## Condição para uso de dados reais

Antes de importar conversas, telefones ou cadastro de clientes: obter autorização do responsável e definir finalidade, acesso, retenção e segurança adequados. Antes de comercializar/implantar, verificar as condições jurídicas aplicáveis à prestação do serviço e à responsabilidade pelo tratamento dos dados.

## IA generativa (Fase 8)

- [x] Fornecedor escolhido: **Google Gemini** (decisão do responsável, 20/11/2026, Fase 9B). Anthropic substituído.
- [ ] Autorização para usar a Gemini API com conta Google e projeto Cloud **pagos** identificados (nível gratuito usa o conteúdo para melhorar produtos: vedado para conversas de clientes).
- [ ] Orçamento mensal e limites diários (`AI_MAX_REQUESTS_PER_DAY`, `AI_MAX_COST_USD_PER_DAY`).
- [ ] Avaliação jurídica: retenção e uso para treinamento pelo provedor, transferência internacional, contrato de tratamento, aviso ao cliente sobre IA.
- [ ] Decisão sobre minimização: nomes e dados livres digitados pelos clientes hoje não são removidos antes do envio.
- [ ] Quem cria e guarda a chave de API (gerenciador de segredos) e quem autoriza a troca da trava de código.
- [ ] Modelo e nível de raciocínio escolhidos após medição real (piloto `gemini-3.1-flash-lite`; comparar `gemini-3.5-flash-lite` e `gemini-3.8-flash`).
- [ ] Ativação do prompt `v2` ou `v3` (ambos registrados e inativos) após avaliação com o modelo escolhido.

## Avaliação com IA real (Fase 9B)

- [ ] Autorização escrita específica para a avaliação (responsável, executor, modelos, orçamento, nº de requisições e cenários, período, dados só sintéticos, armazenamento, interrupção) — formato em `fixtures/homolog/ai_evaluation_authorization.example.json`.
- [ ] Orçamento da avaliação aprovado (sugestão técnica: US$ 15 / 1.500 requisições — `HOMOLOGACAO_IA_FASE9.md` §6).
- [ ] Retenção de prompts/respostas pelo Google no nível pago (os termos dizem "limited period", sem dias) confirmada no Data Processing Addendum; transferência internacional ("any country") avaliada pelo jurídico.
- [ ] Responsável legal (18+) aceita os termos da Gemini API e contrata o projeto; restrição de API configurada na chave.
- [ ] Verificar `gemini-2.5-flash-lite` (possivelmente mais barato; tarifa não lida em texto verbatim) e os rate limits por modelo no AI Studio.
- [ ] Latência aceitável para o salão (o critério O1 pré-registrado usa p95 ≤ 8 s; `AI_TIMEOUT_SECONDS` padrão de 2 s é curto para modelos com raciocínio).
- [ ] Quantas mensagens tem um atendimento típico (para converter custo por mensagem em custo por atendimento).
- [ ] Como tratar temas sensíveis (saúde, gravidez, alergias): hoje o detector não os reconhece (cenário X2).

## Checklist de coleta junto ao Salão Rocha Reis (para a base de conhecimento)

> Nada abaixo existe ainda como dado oficial. Fixtures (`fixtures/`, `evals/`) são
> fictícias e **nunca** contam como confirmação. Cada item entra no painel como
> rascunho, com a **origem** registrada, e só vale depois de aprovado por um
> administrador do salão (base com escopo OFFICIAL).

| Categoria | O que pedir | Formato para o cadastro | Quem confirma |
|---|---|---|---|
| Identidade | Nome público exato e como o assistente deve se apresentar | Texto (até 240 caracteres) | Proprietária |
| Endereço | Endereço completo e ponto de referência | Texto | Proprietária |
| Horário | Dias e horários de funcionamento, feriados e exceções | Texto | Gerência |
| Serviços | Lista de serviços com nome, outros nomes usados pelos clientes (ex.: "mechas" = luzes) e descrição curta | Um registro por serviço | Gerência |
| Preços | Valor por serviço; se variar (comprimento, avaliação, profissional), a condição exata; data de vigência | Um registro por serviço, valor + condição | Proprietária |
| Profissionais | Quem atende e quais serviços (se for para divulgar) | Lista | Proprietária + consentimento de cada profissional |
| Políticas | Pagamento, atraso/tolerância, crianças, acompanhantes etc. | Lista de frases aprovadas | Gerência |
| Cancelamento | Prazo, multa (se houver), como cancelar | Lista | Proprietária |
| FAQ | As 10–20 perguntas mais comuns e a resposta aprovada de cada | Pergunta(s) + resposta | Recepção + gerência |
| Booksy | Link oficial do perfil, testado em celular | URL `https://…booksy.com/…` | Proprietária |
| Complementares | Promoções vigentes (com validade), estacionamento, acessibilidade | Somente se forem divulgadas oficialmente | Gerência |

Pendências que a interface mostra automaticamente: categorias sem registro aprovado
e serviços sem preço aprovado (seção **Conhecimento** → Pendências).
