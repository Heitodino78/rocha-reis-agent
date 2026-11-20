# Plano de validação com a equipe do salão (Fase 11B — UAT)

**Situação: preparado (revisado na Fase 14 para a RC1 Offline), ainda não executado.** Nenhum
funcionário participou. Só vale como evidência quando os campos "Resultado real" forem preenchidos
**durante a sessão, com os participantes**. Checklist de condução em `CHECKLIST_HOMOLOGACAO_11B.md`;
roteiro de apresentação em `ROTEIRO_DEMONSTRACAO_SALAO.md`; treinamento em `TREINAMENTO_RAPIDO.md`.

## 1. Objetivo

Verificar, com quem vai usar o painel, se ele é compreensível e suficiente para atender clientes —
**sem** WhatsApp real, **sem** clientes reais e **sem** inteligência artificial real. Tudo é
fictício e marcado com a faixa **DEMONSTRAÇÃO**.

## 2. Participantes

| Papel | Quem | Faz |
|---|---|---|
| Condutor (agência) | | Prepara, lê as tarefas, **não** ajuda a clicar |
| Observador (agência) | | Anota tempo, dúvidas e erros |
| Atendentes do salão | | Cenários AT-* |
| Administração do salão | | Cenários AD-* |
| Responsável pela aprovação final | | Assina a seção 7 |

60–90 minutos por grupo; computador do salão ou notebook da agência; parte no celular.

## 3. Preparação

Seguir `CHECKLIST_HOMOLOGACAO_11B.md` §1. Em resumo: `sh iniciar.sh --nova-demo`, conferir a faixa
DEMONSTRAÇÃO e as quatro travas bloqueadas em Sistema, entregar em papel os logins fictícios
(`admin.demo`, `ana.demo`, `bia.demo`) e, para novos clientes durante a sessão, usar
`sh iniciar.sh --mensagem "texto"` num segundo terminal. Recriar a demonstração entre grupos.
**Proibido:** dados, nomes ou números de clientes reais; WhatsApp ou Gemini reais.

## 4. Cenários

Leia o objetivo e os passos em voz alta, sem mostrar onde clicar. Resultado real: **Sucesso**,
**Com ajuda** ou **Não conseguiu** (+ observação).

### Atendentes

| ID | Objetivo | Pré-condições | Passos | Resultado esperado | Resultado real | Observação | Critério de aceitação |
|---|---|---|---|---|---|---|---|
| AT-01 | Entrar no painel | Demonstração nova; login `ana.demo` | Digitar login e senha; entrar | Tela inicial com o nome da pessoa | | | Sem ajuda |
| AT-02 | Achar quem espera atendimento | AT-01 | Dizer quantos clientes aguardam | Usa "Aguardando atendimento" ou a Visão geral | | | Sem ajuda |
| AT-03 | Ler o histórico | Reclamação fictícia na fila | Abrir a conversa da reclamação e ler | Lê mensagens do cliente e do assistente | | | Sem ajuda |
| AT-04 | Assumir | AT-03 | Assumir o atendimento | "Responsável: (nome)"; assistente para de responder | | | Sem ajuda |
| AT-05 | Responder | AT-04 | Enviar "Olá! Sinto muito pelo ocorrido, vou te ajudar." | Mensagem aparece com situação "Aceita pela plataforma (envio simulado)" | | | Sem ajuda |
| AT-06 | Perceber novo cliente | Condutor envia `--mensagem "Quero falar com uma pessoa"` | Notar o aviso e abrir a conversa | Usa o sino ou a lista; abre a conversa nova | | | Sem ajuda |
| AT-07 | Transferir | Atendimento assumido | Transferir para `bia.demo` | Conversa sai da lista; Bia recebe aviso | | | Sem ajuda |
| AT-08 | Finalizar | Atendimento próprio | Finalizar; dizer quem pode religar o assistente | Estado "Encerrado"; responde "a administração" (texto acima do campo) | | | Sem ajuda |
| AT-09 | Entender falha de envio e se recuperar | Login `bia.demo` (tem uma falha) | Achar a mensagem que falhou, dizer o que fazer e escrever a resposta de novo | Explica "não saiu"; a nova mensagem aparece com "Aceita pela plataforma (envio simulado)" | | | Explicação correta e reenvio feito |
| AT-10 | Entender envio incerto | Condutor mostra a conversa incerta (admin) | Dizer o que fazer | "Pode ter chegado; não reenviar às cegas" | | | Explicação correta |
| AT-11 | Achar o histórico | — | Abrir uma conversa encerrada | Usa "Encerradas" | | | Sem ajuda |
| AT-12 | Achar a ajuda | — | Abrir a ajuda do painel | Abre "Ajuda" | | | Sem ajuda |
| AT-13 | Ativar 2 etapas | Celular com app autenticador | Minha conta → ativar; guardar códigos | "Ativada"; 10 códigos mostrados uma vez | | | Com no máximo uma ajuda |
| AT-14 | Entrar com 2 etapas | AT-13; sair antes | Entrar com senha e depois o código | Painel abre só após o código | | | Sem ajuda |
| AT-15 | Usar no celular | Navegador do computador em modo celular (o painel local não é acessível pela rede) | Repetir AT-02, AT-04 e AT-05 | Consegue sem rolar a tela para os lados | | | Sem ajuda |
| AT-16 | Sair e reconhecer sessão encerrada | Painel aberto em **duas abas**; na segunda, começar a escrever uma resposta (sem enviar) | Sair na primeira aba; olhar a segunda | 1ª aba: tela de login. 2ª aba, em poucos segundos e sozinha: "Sua sessão terminou. Entre novamente. Seu rascunho foi mantido."; após entrar, o texto volta | | | Sem ajuda |
| AT-17 | Dificuldade de navegação | Ao final | Perguntar: "o que foi mais difícil de achar?" | Resposta registrada | | | Registrar (não reprova) |

### Administração

| ID | Objetivo | Pré-condições | Passos | Resultado esperado | Resultado real | Observação | Critério de aceitação |
|---|---|---|---|---|---|---|---|
| AD-01 | Criar acesso | Login `admin.demo` | Criar atendente "Teste Validação" | Na Equipe com "senha temporária" | | | Sem ajuda |
| AD-02 | Desativar acesso | AD-01 | Desativar | "Desativado"; a pessoa não entra | | | Sem ajuda |
| AD-03 | Entender rascunho | Rascunho do preço da escova | Ver a prévia de "Quanto custa a escova?" | Entende que sem aprovação o preço não é informado | | | Explicação correta |
| AD-04 | Aprovar | AD-03 | Aprovar o rascunho | "Aprovado"; prévia mostra R$ 60,00 (fictício) | | | Sem ajuda |
| AD-05 | Revogar | — | Revogar uma informação aprovada | Deixa de ser usada; histórico continua | | | Sem ajuda |
| AD-06 | Preço "a partir de" | — | Prévia de "Quanto custa a coloração?" | "a partir de R$ 120,00 (o valor final pode variar)" | | | Entende que não é valor final |
| AD-07 | Dados estruturados | — | Cadastrar categoria "Teste" e um profissional fictício, aprovar e usá-los numa nova versão de serviço com duração 40–60 min | A prévia mostra categoria, "Duração estimada" e o profissional | | | Com no máximo uma ajuda |
| AD-08 | Configurar | — | Mudar o tempo de urgência para 10 min | Salvo; aparece na Auditoria | | | Sem ajuda |
| AD-09 | Auditoria | AD-04 | Achar quem aprovou a informação | Acha "Aprovou informação" | | | Sem ajuda |
| AD-10 | Travas | — | Em Sistema, confirmar o bloqueio | Quatro travas "bloqueada" | | | Sem ajuda |
| AD-11 | Contato excluído | — | Em Sistema, achar o contato que voltou a escrever | Entende que reativar é decisão do responsável pelos dados | | | Explicação correta |
| AD-12 | Redefinir 2 etapas | AT-13 feito por uma atendente; admin com 2 etapas ativas | Redefinir com justificativa | Atendente precisa cadastrar de novo; registro na Auditoria | | | Sem ajuda |
| AD-13 | Segurança | Login `ana.demo` | Tentar abrir Equipe/Sistema/Conhecimento | Itens não aparecem; nada administrativo acessível | | | Confirmado |
| AD-14 | Backup (acompanhar) | Suporte técnico executa `ops backup-agendado` e `ops teste-restauracao` | Acompanhar e ver "em dia" em Sistema | Entende: cópia cifrada, chave separada, restaurar exige confirmação | | | Explicação correta |

## 5. Registro de problemas (um por linha)

**Planilha (recomendado):** `python -m rocha_reis_agent.ops registro-11b --destino ~/homologacao-11b/sessao-AAAA-MM-DD.csv`
gera, **fora do repositório**, a planilha dos 31 cenários (abre no LibreOffice/Excel) e uma planilha
de problemas, carimbadas com a versão testada e com os resultados **em branco**. Ao final,
`python -m rocha_reis_agent.ops conferir-registro <arquivo>` aponta campos faltando — não aprova nada.
As tabelas abaixo servem para quem prefere papel.

| Nº | Quem achou (papel) | Cenário | O que aconteceu | Tipo | Gravidade | Decisão (preenchida depois) |
|---|---|---|---|---|---|---|
| | | | | | | |

**Tipo:** Defeito técnico · Dificuldade de usabilidade · Dúvida operacional · Pedido de nova
funcionalidade · Mudança de regra de negócio.
**Gravidade:** **Crítico** (impede atender ou expõe dados) · **Alto** (atende com erro ou contorno
difícil) · **Médio** (atrapalha, há contorno simples) · **Baixo** (detalhe visual/texto).
Pedidos de funcionalidade e mudanças de regra **não** são implementados automaticamente: a agência
avalia escopo, custo e impacto e o responsável decide.

## 6. Ficha individual

**Participante:** ______________ **Papel:** ( ) Atendente ( ) Administração **Data:** ___/___/____
**Aparelho:** ( ) Computador ( ) Celular ( ) Tablet
- Conseguiria atender clientes reais com este painel depois de um treinamento? ( ) Sim ( ) Com ajustes ( ) Não
- O que mais ajudou: __________________ · O que mais atrapalhou: __________________

## 7. Critérios de aceitação da sessão

> **Proposta da agência — o responsável pelo salão confirma, ajusta ou recusa cada critério antes
> da sessão** (assinatura abaixo). Aprovar o painel **não** aprova o Gemini, a Meta nem a produção.

Critérios gerais propostos (marque os aceitos):
- [ ] Nenhum defeito **Crítico** aberto.
- [ ] Nenhuma falha conhecida de autenticação ou de permissão.
- [ ] Nenhuma mensagem perdida nos fluxos testados.
- [ ] Nenhuma informação fictícia apresentada como oficial.
- [ ] Fluxos essenciais executados pelos próprios participantes (itens 1–3 abaixo).
- [ ] Pendências de usabilidade registradas (seção 5) com decisão posterior.
- [ ] Procedimento de suporte entendido pela equipe.
- [ ] Aprovação expressa do responsável pelo salão.

Critérios dos cenários:

1. Todos os atendentes concluem AT-01, AT-04, AT-05, AT-08 e AT-16 **sem ajuda**.
2. Todos explicam corretamente AT-09 e **AT-10**.
3. A administração conclui AD-03, AD-04, AD-06, AD-10 e AD-13.
4. Nenhum problema **Crítico** em aberto; problemas **Altos** com correção combinada.
5. O responsável assina.

**Resultado:** ( ) Aprovado ( ) Aprovado com ajustes ( ) Reprovado
**Responsável pelo salão:** ______________ **Data:** ___/___/____

## 8. Depois da sessão

Resumo em `STATUS_FASE_11.md` (seção "Fase 11B"); fichas com nomes guardadas fora do repositório;
nenhum item marcado como validado sem a ficha. Validar o painel **não** autoriza uso com clientes
reais (ver `CHECKLIST_PRODUCAO.md`).
