# Relatório técnico — Fase 11 (finalização funcional e preparação da validação)

## **FASE 11A CONCLUÍDA LOCALMENTE — FASE 11B AGUARDANDO VALIDAÇÃO PELO SALÃO.**

Executada em 20/11/2026, somente com dados fictícios, `FakeAIProvider`,
`FakeWhatsAppTransport` e as três travas reais (`GRAPH_TRANSPORT_…`, `HOMOLOGATION_TRANSPORT_…`,
`GEMINI_REAL_PROVIDER_…`) em `False`, sem alteração. Nenhuma chamada externa, nenhum envio
real, nenhum commit ou push. Nenhum funcionário do salão participou; nenhum resultado de
validação foi registrado.

| Situação | Estado |
|---|---|
| IMPLEMENTADO | Sim (§B) |
| TESTADO LOCALMENTE | Sim (§C) — 856 pytest + 26 Chromium aprovados (baseline 837 + 19) |
| VALIDADO PELO SALÃO | **Não** (11B preparada em `PLANO_VALIDACAO_SALAO.md`) |
| DADOS OFICIAIS RECEBIDOS | **Não** (formulário em `COLETA_DADOS_OFICIAIS.md`) |
| HOMOLOGADO EXTERNAMENTE (Meta / Gemini) | **Não** |
| IMPLANTADO | **Não** |

## A. Diagnóstico

- **Estado inicial:** `main` em `c59b934`; a Fase 10 **não está commitada** (31 caminhos
  alterados/novos), conforme relatado. Sem `CLAUDE.md` nem `.agents/skills` no repositório.
- **Baseline:** **837 passed** (pytest) e **19 passed** (Chromium) — igual ao relatado.
- **Auditoria:** o caminho completo (webhook assinado → inbox → núcleo com conhecimento do
  banco → outbox → transporte fake → painel → encerramento → reinício) foi exercitado por um
  teste de ponta a ponta com os 10 cenários pedidos (§C). Todos passaram **sem alteração de
  código** — o núcleo já se comportava como especificado.
- **Problemas concretos encontrados e corrigidos:**
  1. Aprovar dois serviços com o mesmo nome/apelido, ou duas perguntas frequentes iguais, era
     aceito — o atendimento passaria a responder de forma ambígua.
  2. Erros do próprio FastAPI chegavam à tela crus: validação de parâmetro devolvia uma lista
     (exibida como "[object Object]") e rotas inexistentes vinham em inglês ("Not Found").
  3. Erro 500 mostrava "Erro interno do servidor." sem orientação.
  4. O texto acima do campo de resposta dizia "Assuma o atendimento" também em conversas
     automáticas e encerradas (onde não há como assumir) e não dizia **quem pode retomar** a
     automação.
  5. Contraste do selo "Admin" no tema claro: **3,68:1** (mínimo AA 4,5:1).
  6. Mensagens técnicas para a equipe ("admin_cli knowledge-init", "KNOWLEDGE_SOURCE=database").
  7. Preparar a sessão de validação exigia terminal com variáveis de ambiente (recriar a
     demonstração; simular um cliente escrevendo).
  8. Não havia ajuda dentro do painel (tarefa "localizar a ajuda" do UAT).
  9. A prévia do conhecimento não se apresentava explicitamente como **simulada**.
  10. **Corrida real no envio da equipe** (achada porque um teste antigo falhou de forma
      intermitente na regressão; investigada, não repetida até passar): dois cliques
      simultâneos com a mesma chave — o segundo via "sem mensagem", lia a conversa já gravada
      pelo primeiro e esbarrava no `UNIQUE(idempotency_key)` da outbox. O resultado era
      "Operação não concluída" na tela, embora a mensagem tivesse sido gravada uma vez. Reproduzida
      em script (falha na 1ª de 25 rodadas), causa confirmada pelo `IntegrityError`.

## B. Implementação

**Corrigido / adicionado**

- Conhecimento: aprovação recusada (409, auditada como `denied`) quando nome/apelido de serviço
  ou pergunta frequente colide com **outro** registro aprovado; nova versão do mesmo registro
  não conta como conflito; conflitos já existentes na base aparecem em **Pendências**. Prévia
  marcada como `simulated` e exibida como "Prévia SIMULADA (sem IA real e sem envio)".
- Envio da equipe: a violação de unicidade da chave passa a ser tratada como o mesmo envio
  (resposta idempotente), como já acontecia com o conflito de versão; a transação perdedora é
  revertida por inteiro. Teste determinístico que falha sem a correção (409) e passa com ela;
  60 rodadas do estresse concorrente sem falha.
- Erros: handlers para `RequestValidationError` (422 "Dados da requisição inválidos.", sem
  ecoar a entrada) e para 404/405 da API em português; a interface só exibe `detail` textual e
  troca qualquer 5xx por orientação clara.
- Painel: texto contextual acima do campo de resposta para cada estado, incluindo quem pode
  retomar a automação; página **Ajuda** para todos os papéis; rótulos da auditoria para ações
  de conhecimento; token `--gold-text` com contraste AA.
- Operação da demonstração: `sh iniciar.sh --nova-demo` (recria **só** o perfil marcado) e
  `sh iniciar.sh --mensagem "texto"` (cliente fictício pelo caminho do webhook assinado; só
  números `55119000…`; o painel aberto processa). A demonstração ganhou reclamação, preço
  ausente, pedido de agendamento e um **rascunho de preço pendente** para treino de aprovação
  (11 conversas fictícias).

**Arquivos criados:** `tests/test_phase11_scenarios.py`, `tests/test_phase11.py`,
`tests_e2e/test_phase11_browser.py`, `docs/STATUS_FASE_11.md`,
`docs/PLANO_VALIDACAO_SALAO.md`, `docs/COLETA_DADOS_OFICIAIS.md`.

**Arquivos modificados:** `rocha_reis_agent/panel/{knowledge.py, api.py, app.py, service.py}`,
`rocha_reis_agent/panel/static/{app.js, index.html, styles.css}`,
`rocha_reis_agent/ops/{demo.py, __main__.py}`, `iniciar.sh`, `tests/test_phase10_backup.py`
(demonstração agora com 11 conversas), `docs/{MANUAL_PAINEL, DADOS_PENDENTES,
CHECKLIST_PRODUCAO, PLANO_DE_EXECUCAO, ARQUITETURA_E_DECISOES}.md`, `README.md`.

**Decisões (ADR-115 a ADR-119 em `ARQUITETURA_E_DECISOES.md`):** conflito de termos bloqueia
aprovação; erros do framework normalizados sem eco; ajuda estática no painel (sem novo
módulo); comandos de demonstração restritos a perfis marcados e números fictícios.

**Não feito de propósito:** campos novos de categoria/duração/profissional por serviço
(exigiriam mudar o modelo de conhecimento e as respostas; o formulário orienta a coletar e a
usar Pergunta frequente aprovada até haver decisão); backup pelo painel (operação sensível
fica por comando, com o suporte técnico).

## C. Validação local

**Cenários de ponta a ponta** (`tests/test_phase11_scenarios.py`, mesmo banco, API HTTP real do
painel, conhecimento do banco):

| Nº | Cenário | Resultado |
|---|---|---|
| 1 | Pergunta de serviço com preço aprovado | Responde "Corte de teste: R$ 45,00" |
| 2 | Preço não cadastrado | "Não tenho preço confirmado…", sem "R$" |
| 3 | Agendamento | Só o link Booksy aprovado; nenhuma confirmação de horário |
| 4 | Pedido de pessoa | Fila → atendente assume e responde; o robô fica calado; aviso de nova mensagem |
| 5 | Reclamação | Vai para a fila humana e gera aviso |
| 6 | Transferência | Responsável muda; a anterior perde acesso (404) e não responde mais |
| 7 | Falha de envio | Exibida como "Falhou", aviso obrigatório, sem nova tentativa |
| 8 | Reinício (canal e painel) | Estados, responsável e histórico idênticos; o pipeline continua |
| 9 | Nova versão aprovada | Em revisão nada é informado; aprovada vale a nova; revogada deixa de valer |
| 10 | Ação administrativa por atendente | 8 tentativas recusadas (403), nada muda |
| + | Base sintética não vira oficial | Recusado; respostas mantêm "[SIMULAÇÃO — DADOS FICTÍCIOS]" |

**Suítes:** baseline 837 + 19 → final 856 pytest + 26 Chromium aprovados (baseline 837 + 19). Novos testes Chromium
(`test_phase11_browser.py`): contraste WCAG AA em todas as telas nos temas **claro e escuro**,
notebook 1280×720 e tablet 820×1180 (botão Enviar visível e com ≥ 40 px), ajuda alcançável só
por teclado, diálogo de confirmação com foco interno e cancelamento seguro por Esc, conflito de
conhecimento e prévia simulada vistos pelo administrador.

**Segurança revisada:** sessões, CSRF/Origin (inclusive rotas novas da Fase 10), RBAC no
backend (cenário 10 e testes da Fase 7), isolamento entre clientes/atendentes (cenário 6;
IDOR da Fase 7), XSS (só `textContent`), logs sem conteúdo, permissões 0600/0700 da
demonstração e dos backups, travas reais somente leitura. Os comandos novos recusam perfis
não marcados e números não fictícios.

**Backup/restauração (Fase 10, reexecutados, não reimplementados):** banco em uso com
transação aberta, restauração íntegra com igualdade de mensagens, auditoria, operadores,
estados, inbox e outbox; chave errada, arquivo corrompido/adulterado, versões incompatíveis,
destino existente e falha no meio — todos cobertos em `tests/test_phase10_backup.py`.

**Limitações:** verificação de acessibilidade automatizada simples (sem leitor de tela, sem
axe-core, sem pessoas); contraste conferido só para texto; sem teste em Safari/Firefox nem em
aparelhos físicos; desempenho medido só nesta máquina (Fase 10 §F).

## D. Conhecimento

- **Categorias suportadas:** identidade, endereço, horário, serviço (nome, apelidos,
  descrição), preço (valor + condição), profissionais, políticas, cancelamento, perguntas
  frequentes, link Booksy (só `https://…booksy.com`).
- **Fluxo:** rascunho com origem → prévia simulada → aprovação (opcionalmente por outro
  administrador: `KNOWLEDGE_REQUIRE_DISTINCT_APPROVER`) → uso; rejeição; revogação;
  histórico imutável; versão esperada evita sobrescrita concorrente (409); rascunho sobre
  dado aprovado suspende o uso; conflito de termos bloqueia; somente ADMIN, conferido no
  backend; escopo sintético/oficial fixo por base.
- **Coleta:** `COLETA_DADOS_OFICIAIS.md` (formulário para o salão + tabela de onde cada
  resposta entra no painel). **Pendências:** `DADOS_PENDENTES.md` (três prioridades).
- **Bloqueio de produção relevante:** o armazenamento local só aceita bancos marcados como
  sintéticos (`*.synthetic.sqlite3`, propósito "synthetic-only"). Registrar os dados oficiais
  do salão exige decidir o ambiente de pré-produção (§F) — não foi feito.

## E. Operação e políticas pendentes

Manual atualizado (`MANUAL_PAINEL.md`), ajuda no painel, demonstração reproduzível e plano
UAT com tarefas, ficha de registro e critérios de aceitação (`PLANO_VALIDACAO_SALAO.md`).

Decisões que **não** foram tomadas (cabem ao salão / ao responsável pelos dados):

| Tema | Opções | Vantagens / riscos | Recomendação técnica (não é decisão) |
|---|---|---|---|
| Contato que pediu exclusão e volta a escrever | (a) bloquear e avisar — atual; (b) nova conversa limpa; (c) reativação individual | (a) máxima cautela, mas a pessoa fica sem resposta; (b) atende melhor, reabre tratamento sem nova decisão humana; (c) equilíbrio, exige rotina diária da administração | (c) com prazo de verificação definido; as três já estão implementadas |
| Retenção | prazos para conversas ativas, anonimização, auditoria e chaves técnicas | prazos longos aumentam exposição; curtos dificultam suporte | definir com assessoria jurídica; hoje valores sintéticos |
| Backups | frequência, retenção, local, responsável, custódia da chave, teste de restauração | sem chave não há recuperação; chave junto do backup anula a cifragem | diário automático para fora da máquina, chave com duas pessoas, teste mensal de restauração |
| Atendimento humano | quem assume/transfere/retoma, horários, urgência, avisos | permissões amplas agilizam, mas diluem responsabilidade | atendentes transferem; só administração retoma; urgência após medir a demanda real |
| Saúde (alergia, gravidez, medicamentos, irritação) | sempre encaminhar (atual) ou responder com texto aprovado + encaminhar | qualquer orientação automática pode ser lida como avaliação de segurança | manter encaminhamento sem orientação; texto padrão aprovado pelo salão |

## F. Pré-produção — análise (nada contratado, publicado ou configurado)

**Hipóteses a confirmar com o salão:** um número de WhatsApp; dezenas a poucas centenas de
conversas por dia; 1–5 funcionários simultâneos no painel. Para referência, a medição local
da Fase 10 com 1000 conversas ficou em milissegundos por consulta — não é teste de carga de
produção.

**Arquitetura recomendada (proporcional a um salão):**

| Item | Recomendação | Motivo |
|---|---|---|
| Hospedagem | 1 servidor virtual Linux pequeno, em região com boa latência para o Brasil, disco **cifrado** pelo provedor ou LUKS | Um processo por função; manutenção simples pela agência |
| Processos | webhook, worker e painel como serviços `systemd` separados, reinício automático | Já são processos separados no código |
| Banco | SQLite (WAL) no disco cifrado enquanto houver um único servidor; migrar para PostgreSQL gerenciado só se houver mais de uma instância ou réplica | Volume pequeno; menos peças |
| HTTPS / webhook | proxy reverso com certificado automático expondo **apenas** `/webhooks/whatsapp` | Superfície pública mínima |
| Painel | **não** público: acesso por VPN da equipe (ex.: WireGuard) ou proxy com autenticação adicional; HTTPS e 2FA antes de dados reais | Menor exposição |
| Segredos | arquivo de ambiente 0600 ou credenciais do `systemd`, fora do repositório; rotação documentada; gerenciador de segredos se o provedor oferecer sem custo relevante | Simples e auditável |
| Backups | `ops backup` diário por agendador, cópia para armazenamento externo (outro provedor/região), chave fora do servidor, teste de restauração mensal com `ops verificar`/`restaurar` | Já implementado; falta agendamento e destino |
| Monitoramento | verificação externa de `/health`; alertas de UNKNOWN/FAILED/`capacity_exhausted`/inbox FAILED; logs do journald sem conteúdo, com retenção | Sinais já existem no código |
| Atualizações | atualizações de segurança automáticas do sistema; deploy por versão etiquetada com `requirements.lock` (hashes) e janela combinada | Reprodutível |
| Disponibilidade | um servidor + backup testado; a Meta reenvia webhooks não confirmados por um período | Proporcional ao risco |

**Custos ainda não confirmados (cotar antes de decidir):** servidor virtual, armazenamento
externo de backups, domínio, mensagens de serviço do WhatsApp acima da franquia, uso do
Gemini (limites já configuráveis), horas de manutenção da agência. Nenhum valor foi presumido
neste relatório.

## G. Prontidão

| | |
|---|---|
| **Implementado** | Atendimento automático simulado, painel completo, conhecimento com aprovação e conflitos, avisos, backup cifrado, demonstração e iniciador |
| **Testado localmente** | 856 pytest + 26 Chromium aprovados (baseline 837 + 19); 10 cenários de ponta a ponta; Chromium desktop/notebook/tablet/celular, temas claro e escuro |
| **Depende do salão** | Dados oficiais, responsável pelas aprovações, UAT (11B), decisões da §E |
| **Exige homologação externa** | Meta (6B) e Gemini (9C-B), ambos bloqueados por trava de código |
| **Falta para produção** | Itens de `CHECKLIST_PRODUCAO.md`: hospedagem com disco cifrado, HTTPS, 2FA, segredos, backup agendado fora da máquina, monitoramento, base oficial em ambiente não sintético, avaliação jurídica |
| **Riscos conhecidos** | Banco ativo sem cifragem; acessibilidade validada só por automação simples; qualidade do Gemini real não medida; decisões de privacidade pendentes; dependência de uma pessoa da agência para operar comandos |

## Fase 11B — validação com a equipe

**Aguardando.** Nenhuma sessão realizada, nenhuma ficha preenchida. Quando houver, registrar
aqui: data, participantes (por papel, sem dados pessoais desnecessários), tarefas executadas,
resultado por critério de `PLANO_VALIDACAO_SALAO.md` §7, defeitos e correções.
