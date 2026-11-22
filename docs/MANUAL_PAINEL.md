# Manual do painel de atendimento — equipe do salão

> **Versão:** RC1 Offline (aparece em **Ajuda**). Treinamento de 15 minutos em `TREINAMENTO_RAPIDO.md`.
>
> **Para a demonstração:** quem cuida do computador abre o painel com `sh iniciar.sh` (na
> pasta do projeto). `sh iniciar.sh --nova-demo` recomeça do zero, apagando só a demonstração.
>
> **Versão de demonstração.** Tudo o que aparece no painel hoje é **fictício**. Nenhuma
> mensagem chega a clientes de verdade. Quando aparecer a faixa amarela
> **DEMONSTRAÇÃO**, você está no ambiente de testes.

## 1. Entrar e sair

1. Abra o endereço que a administração passou (no computador do salão:
   `http://127.0.0.1:8000/panel/`).
2. Digite seu **login** e sua **senha**. No primeiro acesso, o painel pede para você criar
   uma senha nova (pelo menos 12 caracteres).
3. Para sair, use **Sair**, no fim do menu. Em computador compartilhado, saia sempre:
   ao sair, os rascunhos não enviados são apagados.

### Verificação em duas etapas (recomendada; obrigatória em alguns ambientes)

1. Em **Minha conta → Ativar verificação em duas etapas**, confirme sua senha.
2. No celular, abra um aplicativo autenticador, escolha *adicionar conta → inserir chave
   manualmente* e digite a chave mostrada (ela aparece **só uma vez**).
3. Digite o código de 6 números do aplicativo e clique em **Ativar**.
4. Anote os **10 códigos de recuperação** e guarde-os em papel, com você. Cada um vale uma vez.

Depois disso, o login pede senha e, em seguida, o código do aplicativo. Perdeu o celular? Use
**"Perdi o celular: usar código de recuperação"**. Sem celular e sem códigos, peça à
administração para **redefinir** (ela confere sua identidade pessoalmente).

Se a sessão terminar sozinha (muito tempo parado), o painel pede para entrar de novo.
**Seu rascunho e a conversa aberta são mantidos**: depois de entrar, você continua de onde
parou.

## 2. O menu

O menu é dividido em três grupos: **Atendimento** (conversas), **Gestão** (só a administração) e
**Conta** (sua conta e a ajuda). No tablet e no celular ele abre pelo botão ☰; toque fora dele para
fechar.

| Item | Para que serve |
|---|---|
| **Visão geral** | Resumo do momento e a lista **Precisa de atenção** |
| **Conversas** | Todas as conversas que você pode ver |
| **Aguardando atendimento** | Clientes que pediram uma pessoa e ainda estão **sem responsável** |
| **Meus atendimentos** | Atendimentos **em andamento** sob sua responsabilidade (os finalizados ficam em **Encerradas**) |
| **Automáticas** | Conversas que o assistente automático está atendendo |
| **Com outros funcionários** | (administração) atendimentos de colegas |
| **Encerradas** | Atendimentos já finalizados (histórico). Se a cliente voltar a escrever, a conversa sai daqui e volta para **Aguardando atendimento**, sem responsável, com aviso para a equipe. **Exceção provisória (U1):** agradecimento ou confirmação **isolados** ("Obrigada!", "Valeu", "Ok", 👍) ficam só no histórico e a conversa continua aqui, sem aviso. Qualquer outra coisa reabre — inclusive "Obrigada, mas…", perguntas, áudio e imagem |
| **Ajuda** | Resumo deste manual, dentro do painel |
| **Equipe, Auditoria, Conhecimento, Configurações, Sistema** | Só para a administração |

O número ao lado de cada item mostra quantas conversas há ali.

## 3. Avisos (o sino 🔔)

O sino mostra quantos **avisos novos** existem. Eles aparecem **somente dentro do painel**
(não há e-mail, SMS nem WhatsApp para a equipe).

- **Cliente aguardando atendimento humano** — alguém pediu para falar com uma pessoa.
- **Uma conversa foi atribuída a você** — um colega transferiu um atendimento para você.
- **Nova mensagem em um atendimento seu** — o cliente respondeu.
- **Falha no envio** / **Envio com resultado INCERTO** — veja a seção 6.
- **Envios INTERROMPIDOS** — a administração parou todos os envios.

Ao entrar no painel, os avisos que já estavam pendentes aparecem num **único** lembrete
("N aviso(s) pendente(s) — veja no sino"); a lista completa fica no sino.

Clique em um aviso para ir direto à conversa. Os avisos **nunca mostram o texto do
cliente**, só o código da conversa (por exemplo, `Cliente ··a1b2c3`).

**Preferências** (dentro do sino): você pode ligar um **som** e escolher quais avisos
comuns quer ver. Falhas de envio, resultados incertos e problemas do sistema **sempre**
aparecem — não dá para desligá-los.

## 4. Atender um cliente

1. Em **Aguardando atendimento**, clique na conversa.
   - A etiqueta vermelha **Urgente** indica que o cliente espera há mais tempo do que o
     limite combinado (padrão: 15 minutos).
2. Clique em **Assumir atendimento**. A partir daí, o assistente automático **não
   responde mais** essa conversa e você é a pessoa **Responsável** (aparece no topo).
3. Escreva no campo de baixo e clique em **Enviar** (ou `Ctrl+Enter`).
   - Clicar duas vezes não manda duas mensagens.
   - Abaixo de cada resposta aparece a situação do envio (seção 6).
4. Para ver mensagens antigas, use **Carregar mensagens anteriores**, no topo da conversa.

### Transferir

**Transferir** passa a conversa para um colega. Você deixa de ver a conversa; o colega
recebe um aviso.

### Finalizar

**Finalizar atendimento** encerra o seu atendimento. O assistente automático **continua
desligado** para esse cliente até alguém usar **Retomar automação**. Quem pode fazer isso: a
administração e, só se a administração liberar nas Configurações, o próprio responsável.
O painel mostra essa informação logo acima do campo de resposta.

### Mensagens que aparecem acima do campo de resposta

| Mensagem | Significado |
|---|---|
| "Assuma o atendimento para responder." | O cliente está na fila; clique em **Assumir atendimento** |
| "Somente o responsável (Nome) pode responder." | Outra pessoa está atendendo |
| "O assistente automático está atendendo esta conversa." | Não há atendimento humano nessa conversa |
| "Atendimento encerrado…" | Finalizado; diz quem pode religar o assistente |
| "A janela de 24h terminou…" | Regra do WhatsApp; veja a seção 5 |

## 5. A janela de 24 horas

Pelas regras do WhatsApp, só é possível responder livremente até 24 horas depois da última
mensagem do cliente. O painel mostra até quando a janela fica aberta. Depois disso, o
campo de resposta fica bloqueado.

## 6. Situação dos envios — leia com atenção

| O que aparece | O que significa | O que fazer |
|---|---|---|
| **Na fila de envio** | Ainda vai sair | Aguarde |
| **Aceita pela plataforma** | O WhatsApp recebeu (não quer dizer que o cliente leu) | Nada |
| **Entregue** / **Lida** | Confirmações do WhatsApp | Nada |
| **Falhou** | A mensagem **não** saiu | Se ainda for necessária, escreva de novo |
| **Resultado incerto** | A mensagem **pode ter chegado** ao cliente | **Não reenvie às cegas.** Pergunte ao cliente ou avise a administração |
| **Não enviada** | Cancelada antes de sair (ex.: o atendimento mudou) | Verifique a conversa |

O sistema **nunca reenvia sozinho** uma mensagem com resultado incerto, para não mandar a
mesma coisa duas vezes.

## 7. Para a administração

### Funcionários

- **Equipe → Novo funcionário**: login, nome, papel (Atendente ou Administrador) e uma senha
  temporária (a pessoa troca no primeiro acesso). Entregue a senha pessoalmente.
- **Desativar** encerra na hora as sessões da pessoa e devolve os atendimentos em andamento dela para
  **Aguardando atendimento**, para outra pessoa assumir. Use quando alguém sair do salão.
- **Redefinir senha**: quando alguém esquecer a senha. Confirme antes a identidade da pessoa, defina uma
  senha temporária (mínimo 12 caracteres) e entregue pessoalmente; ela troca no próximo acesso.
- Cada pessoa tem o seu acesso. Não compartilhe logins: a Auditoria registra quem fez cada ação.

### Informações do salão (Conhecimento)

1. **Novo registro (rascunho)**: escolha o tipo (endereço, horário, serviço, preço, política,
   pergunta frequente, link Booksy), preencha e informe a **origem** (ex.: "tabela de preços
   de dezembro").
2. Use **Ver prévia** para ver como o assistente responderia. A prévia é **simulada**: não usa
   IA real e não envia nada.
3. **Aprovar** publica a informação no atendimento. Enquanto houver um rascunho novo sobre um
   dado já aprovado, o assistente **deixa de informar** esse dado (ex.: preço em revisão), para
   nunca passar valor antigo.
4. **Rejeitar** descarta o rascunho (fica no histórico). **Revogar** tira de uso uma informação
   aprovada.
5. **Categorias e profissionais** (novo): cadastre e aprove primeiro; depois, na nova versão
   do serviço, escolha a categoria, marque os profissionais e, se aprovada para divulgar, a
   **duração** (um valor aproximado ou uma faixa). O assistente sempre diz "duração estimada".
   Profissional cadastrado aqui **não** ganha acesso ao painel. Para tirar alguém ou uma
   categoria de uso, crie nova versão com **Ativo** desmarcado — o histórico fica.
6. **Preço**: escolha o tipo — fixo, "a partir de", faixa (mínimo e máximo) ou "mediante
   avaliação presencial". O assistente nunca apresenta "a partir de" ou faixa como valor final.
7. O painel **recusa** aprovar um serviço cujo nome ou apelido já pertence a outro serviço
   aprovado, ou uma pergunta frequente repetida — o aviso diz qual registro conflita.
8. **Pendências** mostra o que ainda falta (ex.: serviço sem preço aprovado, profissional
   inativo ainda ligado a um serviço).

As informações chegam pelo formulário `docs/COLETA_DADOS_OFICIAIS.md`; ninguém precisa editar
arquivos técnicos.

- **Configurações**: permissões dos atendentes, **minutos até "Urgente"** (1 a 1440) e a
  política para **contatos com dados excluídos** (abaixo). Mudanças ficam na Auditoria.
- **Interromper envios**: botão de emergência; nada sai até a retomada. A fila é
  preservada.
- **Sistema**: contagens, estado dos envios e as **travas de integração real** (Meta e
  Google Gemini). Elas aparecem como **bloqueadas** e **não podem ser ligadas pelo painel**.
- **Contatos com dados excluídos**: quando um cliente pede exclusão dos dados e depois
  volta a escrever, o padrão é **bloquear e avisar a administração** (nada é respondido
  automaticamente e o texto novo é descartado). Quem decide se essa pessoa pode voltar a
  conversar é o **responsável pelos dados do salão**. É possível:
  - reativar um contato específico (botão **Reativar**, digitando o código de confirmação);
    a conversa recomeça **do zero**, sem nada do que foi apagado; ou
  - mudar a política para **Permitir nova conversa**.

### Verificação em duas etapas da equipe

Em **Equipe**, a coluna "Duas etapas" mostra quem ativou (nunca a chave de ninguém). Para quem
perdeu o celular e os códigos: **Redefinir duas etapas** — escreva a justificativa e confirme
digitando `REDEFINIR:<login>`. Confira a identidade da pessoa **antes**, pessoalmente. As
sessões dela são encerradas e ela cadastra de novo. Fica registrado na Auditoria.

### Auditoria

Lista quem entrou, assumiu, transferiu, finalizou, aprovou informações, mudou configurações
etc. Não mostra senhas nem o texto das mensagens.

### Cópias de segurança

Serão feitas automaticamente pelo computador (agendamento diário, ainda **não instalado**;
nunca pelo painel), sempre **criptografadas**. Em **Sistema** aparece se o backup está "em dia", quando foi o último e o
resultado do último teste de restauração. A chave
fica guardada **separada** das cópias. Sem a chave, a cópia não pode ser aberta — guarde-a
em local seguro. Restaurar uma cópia **nunca** apaga o banco atual sem uma confirmação
explícita e sem antes guardar uma cópia de segurança do que existia. Detalhes técnicos em
`docs/STATUS_FASE_10.md` §B.

## 8. Pedir suporte

Anote **o que você estava fazendo**, **a mensagem que apareceu** e o **horário**, e fale com a
administração do salão, que aciona o suporte técnico da agência. Nunca envie por mensagem
senhas ou fotos de conversas de clientes.

## 9. Problemas comuns

| Situação | O que fazer |
|---|---|
| "Conectando…" ou "Reconectando…" no topo | Aguarde alguns segundos; o painel volta sozinho. Se demorar, recarregue a página |
| "Sem conexão com o servidor local" | O programa do painel pode estar fechado. Avise quem cuida do computador |
| A conversa sumiu da minha lista | Ela foi transferida, ou a automação foi retomada por outra pessoa |
| "Ação incompatível com o estado atual da conversa" | Alguém mudou a conversa ao mesmo tempo; ela é recarregada sozinha — confira e tente de novo |
| "Ação reservada a administradores" | Seu papel não permite essa ação |
| Não consigo responder | Veja a mensagem acima do campo: talvez você não seja o responsável ou a janela de 24 h terminou |
