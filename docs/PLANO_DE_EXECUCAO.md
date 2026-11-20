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

## Fase 5 — Atendimento humano operacional

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
