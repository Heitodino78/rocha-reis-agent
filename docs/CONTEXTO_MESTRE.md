# Contexto Mestre — Agente Inteligente de Atendimento Rocha Reis

**Versão:** 0.1 (19/11/2026)  
**Classificação:** documento de planejamento; sujeito a validação com o salão  
**Status:** Fases 0 a 5 implementadas e testadas localmente: fundação, simulador, FakeAIProvider, conhecimento versionado/validação, persistência SQLite sintética, integração Meta WhatsApp **offline** (webhook assinado, inbox/outbox, transporte fake) e adaptador HTTP Graph API **bloqueado** por trava de código, com classificação de erros, janela de atendimento e retenção sem bloqueio permanente; Fase 6A: estrutura de homologação controlada (offline). Homologação real (6B) não executada. Nenhuma conexão real com a Meta, IA generativa real, validação com o salão ou implantação. Evidências em `STATUS_FASE_0.md` a `STATUS_FASE_6.md`.

## 1. Identidade do projeto

- **Projeto:** Rocha Reis Agent (nome interno provisório).
- **Cliente/estabelecimento:** Salão de Beleza Rocha Reis.
- **Equipe proponente:** agência digital de Heitor e Renan; projeto separado da Class Engenharia e dos casos fictícios do portfólio.
- **Canal principal:** WhatsApp utilizado no atendimento do salão; número específico ainda não informado.
- **Sistema existente de agendamento:** Booksy.

O salão já utiliza Booksy para agendar serviços. O produto a construir é um **agente de atendimento conversacional**, não um novo sistema de agendamento nem um catálogo fictício.

## 2. Problema de negócio

O WhatsApp concentra dúvidas e intenções de agendamento. O atendimento manual pode gerar espera, repetição de respostas e demanda fora do horário comercial. O projeto pretende melhorar a experiência e a organização do atendimento **sem prometer métricas não medidas**.

## 3. Visão de produto (resultado final desejado)

Um serviço de atendimento automatizado pelo WhatsApp capaz de:

1. Receber mensagens de clientes por uma integração oficial com a plataforma do WhatsApp.
2. Compreender o pedido em linguagem natural e manter o contexto necessário da conversa.
3. Responder com clareza a partir de informações **confirmadas pelo salão**: serviços, preços, funcionamento, endereço, políticas e profissionais, conforme os dados disponíveis.
4. Detectar intenção de agendar e oferecer o **link oficial Booksy** do salão para o próprio cliente selecionar serviço, profissional, data e horário e concluir a reserva.
5. Identificar pedidos de atendimento humano, assuntos não cobertos, reclamações e situações inadequadas para automação.
6. Encaminhar conversas para uma equipe humana por um processo operacional **definido e testado**, mantendo o bot em pausa enquanto necessário.
7. Manter logs técnicos mínimos e indicadores operacionais razoáveis para investigar erros e melhorar respostas.
8. Operar de forma segura, com minimização de dados pessoais e limites de ação definidos.

**O objetivo de longo prazo é um agente natural, consistente e confiável, não respostas rígidas por palavras-chave.**

## 4. Fluxos de exemplo

### 4.1. Dúvida sobre serviço

Cliente: "Vocês fazem escova? Quanto custa?"

Agente: consulta a base de conhecimento aprovada. Se houver preço validado, informa preço e condições. Se faltar o preço, diz que não dispõe de valor confirmado e oferece contato humano. **Não inventa preço nem dá valor aproximado sem autorização.**

### 4.2. Intenção de agendar

Cliente: "Quero marcar um corte para sábado."

Agente: "Você pode verificar os horários disponíveis e fazer sua reserva diretamente no Booksy do salão: [LINK_BOOKSY_CONFIGURADO]. Se preferir, posso encaminhar sua dúvida à equipe."

**Importante:** o agente não diz que há horário no sábado e não afirma ter reservado nada. Se o link ainda não tiver sido fornecido, não inventa o endereço e oferece assistência humana.

### 4.3. Pedido de humano

Cliente: "Quero falar com uma pessoa."  
Agente: sinaliza transferência conforme fluxo configurado; passa a conversa ao estado de atendimento humano e não concorre com o atendente. Se não houver caixa de entrada/turno disponível, oferece uma expectativa cautelosa sem prometer resposta imediata.

### 4.4. Informação desconhecida

Cliente: "Vocês têm promoção amanhã?"  
Agente: se a promoção não estiver cadastrada e validada, informa que não consegue confirmar e oferece atendimento humano. Não usa suposições.

## 5. Fatos conhecidos e decisões confirmadas

- O salão usa o Booksy para agendamentos.
- O usuário já enviou e-mail sobre a possibilidade de acesso à API Booksy.
- **Decisão atual:** ignorar essa integração direta por enquanto; o agente enviará o link Booksy para o cliente usar.
- O projeto foi descrito como agente inteligente de atendimento via WhatsApp com interpretação de mensagens, informações do salão e encaminhamento humano.
- A construção deve começar por arquitetura/documentação e testes locais com Codex.

## 6. Hipóteses e pontos não confirmados

Não presumir como fatos:

- Endereço do estabelecimento, telefone, link Booksy, serviços, preços, duração dos procedimentos, políticas e profissionais.
- Existência de autorização para integração com o número WhatsApp atual, Business Portfolio Meta, token, app, webhooks ou acesso à infraestrutura.
- Ferramenta usada pelo salão para acompanhar e responder conversas quando a IA encaminhar para humanos.
- Horários de atendimento humano e SLA de resposta.
- Preferências de linguagem, tom comercial, tratamento por nome, consentimento e política de privacidade.
- Fornecedor de IA, orçamento mensal de APIs, hospedagem, limites de uso e retenção de histórico.
- Confirmação contratual, autorização de uso de dados reais e ambiente de produção.

Essas lacunas constam em `DADOS_PENDENTES.md`.

## 7. Escopo incluído no MVP

- Atendimento em **texto** em português do Brasil.
- Respostas baseadas em dados de referência aprovados.
- Memória curta/contexto da conversa com limites configuráveis.
- Detecção de intenção: dúvidas, agendamento, atendimento humano, assuntos fora de escopo.
- Envio do link Booksy, sem operações de agenda.
- Estados de atendimento e transferência humana testados.
- Simulador local e suíte de testes antes de mensageria real.
- Integração futura com WhatsApp Cloud API e observabilidade básica.

## 8. Fora do escopo inicial

- Integração com API Booksy, scraping, automação de navegador ou painel Booksy.
- Consulta de disponibilidade em tempo real; criação, remarcação e cancelamento automático.
- Pagamentos, cobrança, campanhas proativas, promoções disparadas em massa ou CRM completo.
- Atendimento por áudio, fotos ou vídeos como funcionalidades garantidas.
- Aplicativo de gestão administrativo completo e painel de BI sofisticado.
- Prometer reserva concluída após enviar link.
- Implantação sem validação com cliente e autorização apropriada.

## 9. Princípios de experiência

- Ser útil, educado, curto e natural; não fingir ser pessoa real.
- Identificar-se como assistente virtual de forma adequada à comunicação do salão.
- Responder apenas com informações sustentadas por fontes aprovadas.
- Ao solicitar agendamento, priorizar orientação objetiva e o link Booksy.
- Se houver incerteza, dizer que não consegue confirmar e oferecer pessoa responsável.
- Evitar insistência, loops, múltiplas mensagens redundantes e respostas fora do contexto.
- Não solicitar dados que não sejam necessários para a dúvida ou a transferência.

## 10. Critério geral de conclusão

O MVP só estará pronto para operação real quando: os dados do salão forem validados; o fluxo de agendamento por link funcionar; a integração Meta estiver autenticada e testada; o atendimento humano tiver canal e responsável definidos; houver testes de erros e incidentes; a segurança e a privacidade estiverem revisadas; e houver autorização explícita para implantação.

## 11. Documento vivo

Atualize este arquivo quando houver decisão **confirmada pelo usuário/cliente**. Para fatos desconhecidos, registre pendência, não invente. Não reintroduza API Booksy sem solicitação explícita.
