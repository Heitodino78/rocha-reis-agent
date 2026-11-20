# Decisões pendentes do salão

**Para quem:** responsável pelo Salão Rocha Reis e, quando indicado, assessoria jurídica.
A agência **não** decide estes pontos: apresenta opções, riscos e uma recomendação técnica.
Assuntos jurídicos (privacidade/LGPD, retenção) exigem avaliação profissional.
Preencha as três últimas colunas. Ordem = prioridade sugerida.

| # | Problema | Opções | Vantagens e riscos | Recomendação técnica | Quem decide | Decisão / aprovação | Data |
|---|---|---|---|---|---|---|---|
| 1 | Cliente que pediu exclusão dos dados e volta a escrever | (a) bloquear e avisar a administração (atual); (b) nova conversa limpa automática; (c) reativação individual pela administração | (a) máxima cautela, mas o cliente fica sem resposta; (b) sem atrito, mas reabre tratamento sem decisão humana; (c) equilíbrio, exige rotina diária | (c), com prazo para olhar os avisos. As três já existem no sistema | Responsável pelos dados + jurídico | | |
| 2 | Por quanto tempo guardar conversas | 30 dias; 90 dias; 1 ano; outro | Curto: menos exposição, perde contexto de clientes recorrentes. Longo: mais histórico, mais dados guardados | Definir com o jurídico; o sistema anonimiza o que passar do prazo | Responsável pelos dados + jurídico | | |
| 3 | Frequência e retenção dos backups | Diário com 14 cópias + 30 dias; a cada 6 h; outro | Diário: simples e barato. Mais frequente: perde menos dados, usa mais armazenamento externo | Diário no início; revisar com o volume real | Responsável pelo salão (custo) + agência | | |
| 4 | Quem guarda as chaves (backup e duas etapas) | Duas pessoas (salão + agência); só a agência; só o salão | Duas pessoas: ninguém sozinho perde ou abusa. Uma pessoa ausente = backups inacessíveis | Duas pessoas | Responsável pelo salão | | |
| 5 | Recuperação: quem restaura e em quanto tempo (RTO/RPO) | Até 4 h parado / até 24 h de dados; mais exigente | Mais exigente exige mais infraestrutura e custo. Restaurar sozinho é mais rápido; em dupla, mais seguro | 4 h / 24 h para começar; restaurar o banco ativo com agência + responsável juntos | Responsável pelo salão | | |
| 6 | O que sempre vai para uma pessoa | Reclamação, saúde, pedido de pessoa (atuais) + outros? | Mais itens dão mais segurança e mais fila | Manter os atuais; acrescentar só o necessário | Responsável pelo salão | | |
| 7 | Tempo até "Urgente" | 5, 10, 15 (atual, fictício) ou 30 min | Curto: atenção rápida, mas tudo vira urgente | Medir a demanda real; começar com 15 | Responsável pelo salão | | |
| 8 | Horário com atendente disponível | Dias e horários | O cliente sabe quando terá resposta humana; pedidos fora do horário ficam na fila | O assistente informa o horário aprovado | Responsável pelo salão | | |
| 9 | Assuntos de saúde (alergia, gravidez, medicamentos, irritação) | (a) sempre encaminhar a uma pessoa sem orientar (atual); (b) texto padrão aprovado + encaminhar | (a) mais seguro. (b) texto mal escrito pode parecer orientação de saúde | (a); se (b), texto revisado pelo salão | Responsável pelo salão (+ jurídico se houver dúvida) | | |
| 10 | Quem aprova as informações do salão | Nome + substituto; exigir segunda pessoa para preços? | Responsabilidade clara; uma pessoa só pode aprovar erro sozinha | Uma pessoa + substituto; segunda aprovação para preços | Responsável pelo salão | | |
| 11 | Avisos à equipe | Só no painel (atual); também som; fora do painel (celular/e-mail) | Fora do painel exige serviço externo e cuidado com dados | Painel + som no início | Responsável pelo salão | | |
| 12 | Permissões dos atendentes | Transferir (sim/não); retomar a automação (sim/não) | Mais autonomia, menos controle da administração | Transferir: sim. Retomar: só administração | Responsável pelo salão | | |
| 13 | Segundo fator para todos | Obrigatório (recomendado) ou só administração | Protege conversas de clientes; exige celular com aplicativo | Obrigatório para todos quando houver dados reais | Responsável pelo salão | | |
| 14 | Critérios de aceitação da validação (11B) | Aceitar, ajustar ou recusar a proposta de `PLANO_VALIDACAO_SALAO.md` §7 | Critérios claros evitam discussão depois da sessão | Confirmar antes da sessão | Responsável pelo salão | | |

Depois de decidido: o que for configurável é ajustado no painel (Configurações); o que depende de
implantação vai para `DADOS_PENDENTES.md`. Nenhuma decisão é aplicada sem esta tabela preenchida.
