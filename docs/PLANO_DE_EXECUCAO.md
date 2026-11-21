# Plano de execução por fases — Codex

**Método:** escopo pequeno → código → testes → evidência → revisão humana → próxima fase. Não iniciar a próxima fase automaticamente só porque a anterior compilou.

## Fase 0 — Fundação documental e técnica (primeira tarefa)

**Entregas:** revisar este kit, propor árvore de projeto, ambiente Python isolado, configurações seguras e README de execução; estrutura base e endpoint `/health`; contratos de domínio iniciais; testes de smoke.

**Aceite:** ambiente inicial reproduzível, `pytest` executado, relatório com comandos e resultados; nenhuma API externa chamada e nenhuma mensagem real enviada.

**Execução em 19/11/2026:** Fase 0 implementada e testada localmente. Suíte com
14 testes aprovados, `/health` validado em HTTP real e instalação reproduzida em
cópia limpa com segundo venv. Ver `STATUS_FASE_0.md` para comandos, limitações e
evidências. Revisão pelo usuário pendente; esta entrega não autoriza iniciar a
Fase 1 nem integrar serviços externos.

## Fase 1 — Simulador de conversas

**Entregas:** mensagem de entrada/saída normalizada, identificador de conversa, roteador determinístico inicial, respostas simuladas, estado `BOT_ACTIVE`/`PENDING_HUMAN`/`HUMAN_ACTIVE`, mecanismo para teste manual local.

**Aceite:** fluxos de FAQ (fixture), intenção de agendar (com link fake configurado apenas para testes), pedido de humano, falta de informação e mensagens duplicadas testados.

**Execução em 19/11/2026:** autorizada pelo novo anexo de missão da Fase 1,
após a entrega da Fase 0. Simulador CLI e núcleo implementados/testados localmente,
com contexto, múltiplas intenções, base sintética, deduplicação em memória,
limites configuráveis e controle de sessões. O pedido atual acrescentou
`RESOLVED` e os nomes `AUTO`/`WAITING_HUMAN`; os nomes anteriores permanecem aliases.
Fluxo humano é apenas simulado; resolução exige retomada explícita para reativar
o automático. Resultados, critérios e limitações em `STATUS_FASE_1.md`.
Fases 2 a 6 não foram executadas. Validação com o salão e implantação continuam pendentes.

## Fase 2 — Base de conhecimento e IA

**Entregas:** dados estruturados com aprovação, adaptador abstrato de IA, integração real opcional sob autorização e `FakeAIProvider` para testes; prompts versionados; critérios para recusa/escalonamento.

**Aceite:** provedor pode ser substituído sem alterar regra de negócio; testes de alucinação e injeção de prompt; sem segredos em logs.

**Execução em 19/11/2026:** autorizada pelo anexo da Fase 2 com restrição explícita
de executar somente FakeAIProvider offline. A opção de integração real deste
planejamento fica para autorização futura separada. Implementados catálogo
rastreável/aprovação, contratos, fake, prompts versionados, recuperação mínima,
validação de saída e integração ao núcleo/CLI. Nenhum SDK ou modelo real chamado.
Sessões, estados, deduplicação e HTTP preservados. Evidências e limites em
`STATUS_FASE_2.md`. Fases 3 a 6 permanecem não executadas.

## Fase 3 — Persistência e estabilidade

**Entregas:** repositórios de conversas/estados, migrações quando necessário, idempotência, timeouts, retentativas controladas, observabilidade mínima e gestão de custos da IA.

**Aceite:** reinício não perde estado necessário e duplicatas não produzem respostas repetidas; testes de concorrência relevantes.

**Execução da Fase 3:** autorizada pelo novo anexo, exclusivamente local/sintética,
sem IA real ou serviços externos. Implementados SQLite por adaptador, migrações,
commits atômicos, versões otimistas, deduplicação durável, auditoria e manutenção
confirmada. Reinício e interrupção abrupta são testados entre processos reais.
O conhecimento continua em JSON versionado, vinculado ao ensaio persistente por
hash, sem CMS ou migração de dados reais. Limites/evidências em `STATUS_FASE_3.md`.
A gestão de custo de IA real continua futura, pois somente fake está autorizado.
As menções a fases não executadas nos registros anteriores são históricas.
Fases 4 a 6 não foram executadas.

## Fase 4 — Integração oficial Meta (somente com autorização)

**Entregas:** webhook de verificação e entrada, validação de autenticidade, envio pelo canal oficial, ambiente de testes controlado, tratamento de erros e limites da plataforma.

**Aceite:** mensagens de teste reais recebidas e respondidas somente em número autorizado; segurança e regras de plataforma atuais validadas. Não habilitar mensagens em massa.

**Execução em 19/11/2026 (somente offline):** autorizada pelo anexo da Fase 4 com
proibição explícita de conexão real, credenciais e envio. Implementados webhook
GET/POST com assinatura, normalização, inbox/outbox SQLite transacionais, worker,
transporte fake, tratamento de envio incerto e CLI local; auditoria das Fases 0–3
registrada. O critério de aceite acima (mensagens reais em número autorizado)
**continua não atendido** e depende de autorização futura. Evidências em
`STATUS_FASE_4.md`. Fase 5 não iniciada.

## Fase 5 (executada em 19/11/2026) — Adaptador HTTP Meta e segurança operacional

**Conflito de numeração registrado:** o pedido do usuário de 19/11/2026 definiu a
Fase 5 como adaptador HTTP oficial (desativado), classificação de erros, status,
janela de atendimento, retenção sem bloqueio e controle administrativo local. Pela
precedência de `AGENTS.md`, o pedido atual prevalece. O "atendimento humano
operacional" planejado abaixo continua **não executado** e passa a depender de nova
numeração/pedido. Nenhuma mensagem real, conta Meta, IA real ou implantação.
Evidências em `STATUS_FASE_5.md`.

## Fase 6 — Homologação Meta (6A executada em 19/11/2026; 6B bloqueada)

**6A (offline):** ambiente `homologation` isolado, autorização em arquivo privado,
trava de código própria (`False`), allowlist de entrada/saída, limites com registro
de tentativas, interrupção emergencial, métricas/rastreamento sem PII, log privado,
ensaio ponta a ponta com reinícios. Evidências em `STATUS_FASE_6.md` e
`HOMOLOGACAO_META_FASE6.md`. **6B:** não executada; exige autorização específica.

## Roadmap atualizado (após Fase 6A)

| Etapa | Conteúdo | Estado |
|---|---|---|
| 6B | Ensaio real com número de teste, allowlist, limites e evidências | Bloqueada até autorização |
| 7 | Painel de atendimento humano: login Argon2id, RBAC, conversas, assumir/transferir/finalizar/retomar, resposta pela outbox, SSE, auditoria | **Implementado e testado localmente** (`STATUS_FASE_7.md`); não validado pelo salão |
| 8 | IA generativa (adaptador Anthropic **bloqueado**), conhecimento gerido/aprovado no painel, prompts v2, validação factual, avaliações reproduzíveis | **Implementado e testado localmente** (`STATUS_FASE_8.md`); IA real não ativada nem validada |
| 9A | Homologação **offline** da IA: auditoria, correções de qualidade, registro verificado de modelos, orçamento com reserva de pior caso, avaliação v2 (120 cenários, critérios pré-registrados), roteiro 9B | **Implementado e testado offline** (`STATUS_FASE_9.md`) |
| 9B | **Migração para Google Gemini** (provedor oficial): SDK `google-genai`, `GeminiProvider` bloqueado, registro de modelos Gemini, orçamento adaptado, prompt v4, avaliação com Gemini simulado | **Implementado e testado offline** (`STATUS_FASE_9.md` §B) |
| 9C-A | Preparação offline da homologação real: auditoria, experimento pré-registrado, etapas, livro-razão persistente, parada emergencial, fluxo de ponta a ponta, 9 cenários prioritários, contexto oferecido pelo backend, orçamento calculado, patch de ativação | **Implementado e testado offline** (`STATUS_FASE_9C.md`) |
| 9C-B | Homologação real com Gemini em duas etapas (Etapa 1: 25 requisições / US$ 0,25; Etapa 2: cumulativo 250 / US$ 1,50), dados sintéticos, conta paga | **Preparada, não autorizada, não executada** (`STATUS_FASE_9C_B.md`); guarda schema 3 e métricas de encaminhamento por causa implementadas e testadas offline |
| 10 (proposta) | Infraestrutura de produção: banco cifrado, backup/restauração, worker supervisionado, alertas, LGPD | Planejado (`CHECKLIST_PRODUCAO.md`); antes numerada como "9 (proposta)" |

Já existe no núcleo: máquina de estados humana, pausa e retomada explícita,
cancelamento de respostas obsoletas. Existe só como simulação: ações de operador
via CLI local (`operador-local`). Falta: interface, autenticação, notificação e envio
das respostas humanas. Nenhuma etapa avança sem pedido explícito.

## Fase 7 — Atendimento humano operacional (executada localmente em 19–20/11/2026)

Painel web local (app separada do webhook) integrado ao núcleo, à inbox e à outbox.
583 testes pytest + 8 testes de navegador real. Sem notificação externa, sem HTTPS,
sem 2FA, sem validação com funcionários reais. Detalhes em `STATUS_FASE_7.md`.

## Fase 8 — IA generativa, conhecimento oficial e qualidade conversacional (executada localmente em 20/11/2026)

Adaptador Anthropic implementado e bloqueado por trava de código; contrato v2 (modelo
classifica, backend escreve fatos); conhecimento durável com aprovação pelo painel
(seção Conhecimento, só ADMIN); limites de consumo; módulo de avaliações com cenários
A–T, oráculo independente e modelo hostil simulado. Nenhuma chamada externa, nenhum
dado real, nenhum envio. Detalhes em `STATUS_FASE_8.md` e `IA_GENERATIVA.md`.
Proposta de próxima etapa: coleta e aprovação dos dados oficiais com o salão e
validação do painel com a equipe; avaliação com IA real só com autorização (ver
`IA_GENERATIVA.md` §7). Nada avança sem pedido explícito.

## Fase 9A — Homologação offline da IA generativa (executada em 20/11/2026)

Escopo do pedido do usuário: auditar a IA, corrigir os cinco problemas de qualidade
conhecidos, verificar modelos na documentação oficial, revisar o adaptador, versionar
prompts, evoluir as avaliações, implementar limites de consumo e preparar a 9B — **sem
nenhuma chamada paga**. Conflito de numeração registrado: a "Fase 9 (proposta)" de
infraestrutura passa a ser a etapa 10. Evidências em `STATUS_FASE_9.md`. A Fase 9B só
avança com autorização explícita contendo os itens de `HOMOLOGACAO_IA_FASE9.md` §1.

## Fase 9B — Migração para Google Gemini (executada offline em 20/11/2026)

Decisão do responsável: **Google Gemini é o provedor oficial**; a integração Anthropic das
Fases 8/9A passa a ser histórica. Conflito de numeração registrado: a "9B" preparada na
9A (avaliação real com Anthropic) foi substituída por esta migração; a homologação real
com Gemini passa a ser proposta como 9C e continua bloqueada. Nenhuma chamada externa,
nenhum dado real. Evidências em `STATUS_FASE_9.md` (parte B).

## Fase 10 — Consolidação operacional offline (executada localmente em 20/11/2026)

Painel reorganizado (visão geral com "precisa de atenção", automáticas, com outros
funcionários, sistema), avisos internos sem conteúdo e sem duplicatas, urgência
configurável, histórico paginado, recuperação de sessão com rascunho, configurações
tipadas e auditadas, política configurável (padrão: bloquear com aviso) para contato
excluído que volta a escrever, backup criptografado/verificado e restauração isolada,
demonstração isolada e iniciador local. Tudo com dados fictícios, transporte e IA
simulados, travas reais fechadas. Evidências em `STATUS_FASE_10.md`; manual da equipe em
`MANUAL_PAINEL.md`. **Não validado com a equipe do salão; nada implantado.**
Próximas etapas (não iniciadas, dependem de autorização): validação do painel com a
equipe; coleta e aprovação dos dados oficiais; decisões do controlador (exclusão e
retenção); infraestrutura de produção (HTTPS, segredos, volume cifrado, backup agendado).

## Fase 11 — Finalização funcional e preparação da validação (11A executada localmente em 20/11/2026)

**11A concluída localmente; 11B aguardando validação pelo salão.** Auditoria de ponta a ponta
com 10 cenários, correções (conflito de conhecimento, erros compreensíveis, contraste, textos
contextuais, ajuda no painel), comandos de demonstração para a sessão de validação,
formulário de coleta de dados oficiais, plano UAT e análise de pré-produção. Evidências em
`STATUS_FASE_11.md`. Próximo passo depende do salão: sessão UAT (`PLANO_VALIDACAO_SALAO.md`)
e entrega do formulário (`COLETA_DADOS_OFICIAIS.md`). Gemini e Meta reais continuam para
etapas finais, com autorização expressa.

## Fase 12 — Preparação de pré-produção (executada localmente em 20/11/2026)

Dados estruturados do salão (categorias, duração estimada, profissionais, tipos de preço) no
fluxo de aprovação existente; perfis de ambiente gravados no banco; pré-produção **sintética**
com volume cifrado verificado; quarta trava (dados reais) fechada; backup sem temporários em
claro, retenção configurável; métricas locais; análise de hospedagem e plano de implantação.
Evidências em `STATUS_FASE_12.md`, `ARQUITETURA_PRE_PRODUCAO.md` e
`SEGURANCA_ARMAZENAMENTO.md`. **Nada implantado; Fase 11B continua pendente.**

## Fase 13 — Segurança avançada e preparação final de implantação (executada localmente em 20/11/2026)

Segundo fator TOTP (cadastro, login em duas etapas, recuperação, redefinição auditada,
obrigatório em pré-produção), rotação de sessão, HSTS e confiança explícita em proxies, HTTPS
local com certificado de desenvolvimento, configurações de referência (Caddy/systemd), backup
automático com cópia secundária simulada, teste de restauração, ensaio de desastre e rotação da
chave do segundo fator. Evidências em `STATUS_FASE_13.md`. **Nada implantado; 11B pendente.**

## Fase 14 — Consolidação e RC1 Offline (executada localmente em 20/11/2026)

Fases 10–13 revisadas em conjunto; versão candidata **Rocha Reis Agent — RC1 Offline (0.14.0rc1)**
identificada (`ops versao`), **não versionada no Git**; demonstração corrigida e roteiro guiado
automático (`ops roteiro`, 13 jornadas); materiais da 11B (apresentação, treinamento, cenários
estruturados, registro de problemas, checklist da sessão) e decisões pendentes organizadas.
Evidências em `STATUS_FASE_14.md` e `RELEASE_CANDIDATE_RC1.md`. **Próximo passo depende do salão:
sessão 11B e formulário de dados.**

## Fase 15 — Preparação da homologação com o salão (executada localmente em 20/11/2026)

RC1 congelada de forma verificável (`ops congelar-rc` / `ops verificar-rc`, revisão `0.14.0rc1.post1`);
iniciador anuncia "PRONTO" só com o servidor no ar; planilha de registro da 11B gerada a partir do
plano e conferida sem aprovar nada (`ops registro-11b` / `ops conferir-registro`); cenários AT-09 e
AT-16 cobrindo recuperação de erro e sessão encerrada; critérios de aceitação propostos para o
salão confirmar; pacote de homologação para a agência. Evidências em `STATUS_FASE_15.md` e
`RELATORIO_PRONTIDAO_RC1.md`. **A 11B continua pendente até a participação real da equipe.**

## Fase 16 — Redesign do painel, auditoria e refinamento (executada localmente em 20/11/2026)

Sistema de design (tokens, temas claro/escuro, ícones locais, navegação agrupada, responsivo) aplicado
a todas as telas, sem mudar fluxos, textos operacionais ou contratos de segurança. Auditoria corrigiu:
excesso de avisos flutuantes ao entrar, contraste do erro no tema escuro, linha desalinhada na Equipe,
ações do Conhecimento escondidas, cache dos arquivos do painel após atualização, gaveta do menu sem
fundo, avisos sobre o botão Enviar e botões longos alargando a tela no celular. Revisão
`0.14.0rc1.post2`. Evidências em `STATUS_FASE_16.md`. **A 11B continua pendente.**

## Fase 17 — Infraestrutura, implantação e operação contínua (executada localmente em 20/11/2026)

Worker contínuo com watchdog; `/ready`; pacote reprodutível, instalação em árvore Linux, atualização com
backup e ensaio de migração em cópia, reversão automática e restauração confirmada; unidades systemd
endurecidas e verificadas; proxy corrigido (XFF), VPN e firewall de referência; alertas locais; cópia
externa (adaptador falso); ensaio operacional e medição de carga em servidor simulado. Revisão
`0.14.0rc1.post4`. Evidências em `STATUS_FASE_17.md`. **Nada instalado em servidor; 11B adiada.**

## Fase 18 — Versionamento, instalação de pré-produção e validação da infraestrutura (21/11/2026)

**18A concluída no limite do ambiente disponível:** unidades systemd REAIS executadas no systemd do
usuário (sem root) a partir do fluxo oficial `empacotar → instalar → preparar-servidor →
validar-instalacao`; Caddy 2.11.9, WireGuard do kernel e nftables REAIS num laboratório de rede isolado
(namespaces, sem alterar a rede do computador). Encontrados e corrigidos 9 defeitos que a verificação
estática não pegava (atualização real sempre cancelada, instalador alterando o Python do sistema,
`reverter` reativando versão quebrada, alertas sem acesso ao banco, core dump do worker, painel alcançável
sem VPN, rajada do limite de conexões, IP completo no log do Caddy, falha de restauração sem alerta).
Revisão `0.14.0rc1.post5`. Evidências e matriz do que foi testado em cada ambiente: `STATUS_FASE_18.md`.
**18B (servidor real) NÃO iniciada:** não há servidor autorizado — roteiro em `GUIA_IMPLANTACAO_SERVIDOR.md`
§7. **Commit/push não executados** (aguardam autorização específica). **11B continua pendente.**

## Atendimento humano operacional — planejamento original (histórico)

**Entregas:** integração com inbox/processo humano efetivamente disponível; transições de estado autorizadas e auditáveis; notificação aos responsáveis; recuperação após falha.

**Aceite:** um humano recebe a conversa por um meio real e o bot não responde durante a intervenção. Handoff não pode ser apenas status em banco.

## Fase 6 — Homologação, privacidade e implantação

**Entregas:** validação de catálogo/link, teste de ponta a ponta, plano de rollback, critérios de custo, operação, monitoramento e manutenção; check de LGPD.

**Aceite:** responsável aprova explicitamente o go-live e sabe como pausar o agente; testes críticos e condições legais/técnicas verificados.

## Ritual obrigatório de fechamento de fase

Codex deve entregar:

1. Resumo de arquivos adicionados/alterados.
2. O que foi **implementado**, separado do que ficou **planejado**.
3. Comandos executados e resultados observados de testes.
4. Riscos, bloqueios, decisões e dados ainda pendentes.
5. Sugestão da próxima tarefa, **sem executá-la automaticamente**.

## Não antecipar

Não construir dashboard complexo, integração Booksy, envio automático real, transcrição de áudio ou marketing em massa antes de demanda autorizada. Não confundir protótipo e produção.
