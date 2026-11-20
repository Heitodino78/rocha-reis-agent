# Relatório técnico — Fase 14 (consolidação e RC1 Offline)

Executada localmente em 20/11/2026. Dados fictícios, IA e transporte simulados. **Nenhuma API
externa, nenhum dado real, nenhum deploy, nenhum commit, tag ou push.** Fase 11B **pendente**.

| Situação | Estado |
|---|---|
| IMPLEMENTADO | RC1 Offline identificada, demonstração corrigida, roteiro guiado automático, materiais da 11B |
| TESTADO LOCALMENTE | **958 pytest + 37 Chromium** (baseline 950 + 34); roteiro 13/13; avaliações offline |
| VALIDADO PELO SALÃO | **Não** |
| HOMOLOGADO EXTERNAMENTE | **Não** |
| IMPLANTADO | **Não** |

## A. Estado inicial (verificado)

- Git: `main` em `c59b934`; 65 alterações sem commit (33 modificados, 32 novos) das Fases 10–13;
  `.gitignore` sem nada sensível pendente; política de autoria do histórico com `Co-Authored-By`.
- Testes: **950 pytest e 34 Chromium** aprovados. Travas: as quatro `False`.
- Pendências herdadas: 11B, dados oficiais, decisões do salão, infraestrutura, homologações.

## B. Consolidação

Revisão conjunta das Fases 10–13 (modelos, migrações 001–011, autenticação/2FA/sessões,
configuração, interface, conhecimento, backups, demonstração, pré-produção, métricas). Funções
"sem uso" encontradas eram validadores Pydantic e auxiliares internos — **nenhum código morto**
removido. Inconsistências reais corrigidas:

1. **Demonstração (Fase 11) × roteiro:** o rascunho de treino ficava no preço do **corte**, o que
   suspende o valor aprovado — ao vivo, "Quanto custa um corte?" respondia "não tenho preço". O
   rascunho passou para o preço da **escova** (sem valor aprovado).
2. **Demonstração × Fase 12:** não havia serviço com preço "a partir de", categoria, profissional
   nem duração na demonstração; agora há (coloração fictícia, a partir de R$ 120,00, 90–150 min).
3. **Respostas de login divergentes:** login e 2ª etapa devolviam campos diferentes da sessão
   (sem a versão), e a Ajuda ficava sem versão até recarregar. Agora as três respostas têm os
   mesmos campos (teste dedicado).
4. **Sem identificação de versão:** criada (`__version__ = "0.14.0rc1"`, `ops versao`), exibida
   só para usuários autenticados (Ajuda e Sistema), nunca no `/health` público.

Impacto nas fases anteriores: contagem de conversas da demonstração 11 → 12 (teste ajustado);
cenários UAT AD-03/AD-04 passam a usar a escova. Nenhum teste enfraquecido.

**Arquivos (Fase 14):** criados `rocha_reis_agent/ops/{rehearsal.py, release.py}`,
`tests/test_phase14.py`, `tests_e2e/test_phase14_browser.py`, `docs/{STATUS_FASE_14,
RELEASE_CANDIDATE_RC1, ROTEIRO_DEMONSTRACAO_SALAO, CHECKLIST_HOMOLOGACAO_11B, TREINAMENTO_RAPIDO,
DECISOES_PENDENTES_SALAO}.md`; modificados `rocha_reis_agent/__init__.py`, `ops/{demo.py,
__main__.py}`, `panel/api.py`, `panel/static/{index.html, app.js}`, `tests/test_phase10_backup.py`,
`docs/{PLANO_VALIDACAO_SALAO, COLETA_DADOS_OFICIAIS, MANUAL_PAINEL, DADOS_PENDENTES,
CHECKLIST_PRODUCAO, PLANO_DE_EXECUCAO}.md`, `README.md`.

## C. RC1 Offline

**Rocha Reis Agent — RC1 Offline (0.14.0rc1)**, candidata **local não versionada** (árvore com
alterações sem commit). Identificação, dependências, migrações e limitações em
`RELEASE_CANDIDATE_RC1.md`.

## D. Demonstração

| Ação | Comando |
|---|---|
| Iniciar (cria na 1ª vez) | `sh iniciar.sh` |
| Recomeçar do zero | `sh iniciar.sh --nova-demo` |
| Simular cliente | `sh iniciar.sh --mensagem "texto"` (segundo terminal) |
| Testar atendente / administração | logins `ana.demo`, `bia.demo` / `admin.demo` (senhas geradas em `credenciais-demo.txt`, 0600) |
| Encerrar | Ctrl+C |
| Conferir tudo sozinho | `python -m rocha_reis_agent.ops roteiro` (perfil novo, 13 jornadas) |

**Roteiro executado (13/13 OK):** F1 preço aprovado; F2 "a partir de"; F3 ausente/promoção não
inventada; F4 Booksy sem confirmar horário; F5 fila e assumir; F6 resposta com agente suspenso;
F7 transferência; F8 encerramento (retomar só administração); F9 aprovação e prévia simulada;
F10 envio incerto sem reenvio; F11 atendente barrado no backend; F12 reinício íntegro; F13 2FA
(cadastro, etapa intermediária sem acesso, recuperação). O roteiro também **detecta** defeitos
(teste com defeito proposital).

## E. Homologação 11B

- Cenários: **31** (17 atendentes AT-01…AT-17, 14 administração AD-01…AD-14), cada um com
  objetivo, pré-condições, passos, esperado, resultado real (em branco), observação e critério.
- Critérios de aceitação da sessão (§7 do plano); registro de problemas com 4 gravidades e 5 tipos.
- Materiais: `ROTEIRO_DEMONSTRACAO_SALAO.md`, `TREINAMENTO_RAPIDO.md`, `CHECKLIST_HOMOLOGACAO_11B.md`,
  `MANUAL_PAINEL.md`.
- Dados a coletar: `COLETA_DADOS_OFICIAIS.md` (revisado: categorias/profissionais, remarcação).
- Decisões do salão: 14 itens em `DECISOES_PENDENTES_SALAO.md`.

## F. Qualidade

| | Antes | Depois |
|---|---|---|
| pytest | 950 | **958** |
| Chromium | 34 | **37** (RC1 real: notebook, celular, tablet) |

**Avaliações offline (129 cenários, critérios `rocha-reis-acceptance-v1` inalterados):**

| Provedor (simulado) | Aprovados | Segurança | Recall de encaminhamento | Precisão | Critério geral |
|---|---|---|---|---|---|
| Regras + `fake` (configuração ativa), prompt v1 | 123/129 | OK, 0 violações | 0,902 | 1,0 | **Reprovado em Q2** (< 0,95) |
| `hostile` (modelo adversário simulado), v4 | 129/129 | OK | 0,951 | 0,907 | OK |
| `gemini-simulated` (adaptador real sobre HTTP simulado), v4 | 123/129 | OK | 0,902 | 1,0 | Reprovado em Q2 |

Reprovações: H9/H12 (pedido humano indireto), L3/L4 (reclamação indireta), J6/J7 (gíria de
preço) — **casos difíceis preservados**, que dependem da homologação do Gemini real. Nenhum desses
resultados mede a qualidade do Gemini real.

**Segurança revisada:** contas da demonstração só existem no banco marcado `demo` (não abre em
outro ambiente; senhas geradas a cada demonstração); 2FA sem contorno (API, SSE e navegador);
etapa intermediária sem dados; frontend não altera travas (só leitura em Sistema); atendente
barrado no backend; demonstração só com transporte fake (iniciador e roteiro recusam travas
abertas); dados fictícios rotulados "[SIMULAÇÃO — DADOS FICTÍCIOS]" e base sintética não vira
oficial; logs sem segredos (testes das Fases 12–13). CORS: nenhum middleware CORS (mesma origem);
CSRF + Origin nas rotas que alteram estado.

**Limitações:** acessibilidade só automatizada (sem leitor de tela nem pessoas); demonstração
só em 127.0.0.1 (celular real não acessa o painel local — AT-15 usa o modo celular do navegador);
handoff indireto depende do Gemini.

## G. Próximos passos

| Pronto (local) | Depende do salão | Depende da agência | Depende de infraestrutura | Depende de homologação externa |
|---|---|---|---|---|
| RC1, demonstração, roteiro, materiais 11B | Sessão 11B; formulário de dados; 14 decisões | Conduzir 11B; cotar hospedagem; corrigir o que a 11B apontar | Servidor, disco cifrado, HTTPS, VPN, backup externo, monitoramento | Gemini (9C-B), Meta (6B) |

**Impede produção:** todos os itens "Falta" de `CHECKLIST_PRODUCAO.md` (tabela de prontidão) e a
trava de dados reais. **Ordem recomendada:** (1) sessão 11B e correções; (2) dados oficiais e
decisões; (3) hospedagem e pré-produção real; (4) homologação Gemini; (5) homologação Meta;
(6) requisitos jurídicos e abertura revisada da trava de dados reais; (7) produção.

## H. Git

- Estado final: `main` em `c59b934`, **74 caminhos** alterados/novos (34 modificados, 40 novos),
  nenhum commit, tag ou push executado.
- Excluídos/ausentes: bancos, backups, chaves, certificados, credenciais, `perfil.json`,
  `estado-backup.json` (cobertos pelo `.gitignore`; nenhum na árvore).
- Proposta de versionamento em `RELEASE_CANDIDATE_RC1.md` §6.
