# Relatório técnico — Fase 12 (preparação de pré-produção, offline)

Executada localmente em 20/11/2026. Somente dados sintéticos, `FakeAIProvider`,
`FakeWhatsAppTransport`; as travas Meta (2) e Gemini continuam `False` e uma **quarta trava**
(dados reais) foi criada `False`. Nenhuma chamada externa, nenhum deploy, nenhuma porta pública,
nenhum commit ou push. **Fase 11B continua pendente** (nenhum funcionário participou).

| Situação | Estado |
|---|---|
| PLANEJADO | Implantação futura (`ARQUITETURA_PRE_PRODUCAO.md`) |
| IMPLEMENTADO | Dados estruturados, painel, ambientes, verificação de cifragem, backup sem temporários, retenção, métricas |
| TESTADO LOCALMENTE | **917 pytest + 30 Chromium** aprovados (baseline 856 + 26) |
| VALIDADO PELO SALÃO | **Não** |
| HOMOLOGADO EXTERNAMENTE | **Não** |
| IMPLANTADO | **Não** |

## A. Diagnóstico

1. **Estado inicial:** `main` em `c59b934`; Fases 10 e 11A não commitadas (40 caminhos).
   Sem `CLAUDE.md` nem skills. Travas `False False False`.
2. **Baseline:** 856 pytest e 26 Chromium aprovados, como relatado.
3. **Armazenamento (constatado pelo kernel):** `~/.local/share` em LVM sobre **LUKS2**; volume
   do repositório vfat **sem** cifragem; `/tmp` em tmpfs.
4. **Problemas encontrados:**
   - Sem representação para categoria, duração e profissional por serviço.
   - **Preço "a partir de" era dito como valor fixo** ("Corte: R$ 45,00."), a menos que a
     condição fosse escrita à mão; não havia faixa nem "mediante avaliação".
   - **Backup gravava a cópia em claro do banco no diretório temporário do sistema** durante a
     criação e a verificação (fora do volume protegido).
   - SQLite podia gravar temporários em `/var/tmp`.
   - Nada impedia abrir o banco de um ambiente (ex.: demonstração) como outro.
   - **No celular**, as tabelas Equipe, Sistema e Conhecimento alargavam a página (624–906 px
     numa tela de 390 px): um rótulo oculto com `position: absolute` escapava da área rolável.
     Consequência real: o botão **Confirmar** de aprovações ficava fora da tela. O teste de
     rolagem horizontal da Fase 10 não pegava isso porque comparava com a janela de layout,
     que cresce junto.
   - Ao criar a quarta trava logo abaixo da trava do Gemini, o **patch de ativação revisado da
     9C-B deixou de aplicar** (contexto alterado) — detectado pelo teste existente e corrigido
     movendo o bloco, sem tocar no patch.

## B. Implementação

6. **Campos estruturados** (`knowledge.py`, `knowledge_catalog.py`, `knowledge_projection.py`,
   `responses.py`):
   - Fatos novos `category:<id>` (nome, ativo) e `professional:<id>` (nome, ativo). Profissional
     **não** cria conta no painel.
   - Serviço: `category_id`, `duration` (mínimo 5–720 min; máximo opcional > mínimo) e
     `professional_ids` (até 10).
   - Preço: `pricing` = `fixed` | `starting` | `range` (com `amount_max`) | `on_evaluation`
     (sem valor). Documentos antigos sem `pricing` continuam **fixos**.
   - Respostas: "Duração estimada: cerca de 45 min (pode variar)." / "entre 40 min e 1h30";
     "a partir de R$ 45,00 (o valor final pode variar)"; "de R$ 45,00 a R$ 80,00";
     "o valor é definido após avaliação presencial"; catálogo agrupado por categoria ativa;
     "X é realizado por: …". **Texto idêntico ao anterior quando os campos novos estão
     vazios** (avaliações fixadas por hash preservadas).
   - Inativos/não aprovados nunca chegam às respostas; nomes de profissionais ativos entram na
     lista usada pela validação da IA (o modelo não pode citar pessoas) e pelo oráculo.
7. **Painel (admin):** tipos "Categoria" e "Profissional"; no serviço, seleção de categoria
   (só aprovadas e ativas), duração mínima/máxima, profissionais por caixas de seleção; no
   preço, tipo e valor máximo; resumo com relacionamentos ("serviços: …"); campos opcionais
   explícitos; validação no servidor em português.
8. **Migrações:** **010** registra `data_classification='synthetic'` (idempotente). O
   conhecimento **não precisou** de mudança de tabela (documentos JSON append-only, validados
   pelo modelo com padrões compatíveis).
9. **Regras de aprovação:** serviço só é aprovado com categoria e profissionais aprovados e
   ativos (409 "Pendência…"); nomes duplicados de categoria/profissional bloqueiam (409
   "Conflito…"); referências inexistentes são recusadas já no rascunho (422); pendências
   listam serviços ligados a itens inativos.
10. **APIs:** `/knowledge` passa a informar os tipos de preço; `/system` (ADMIN) mostra perfil,
    classificação e proteção em repouso constatada, e a quarta trava. Sem rotas novas
    públicas.
11. **Arquivos criados:** `persistence/storage_security.py`, `persistence/migrations/010_data_classification.sql`,
    `ops/environments.py`, `tests/test_phase12_knowledge.py`, `tests/test_phase12_environment.py`,
    `tests_e2e/test_phase12_browser.py`, `docs/STATUS_FASE_12.md`, `docs/ARQUITETURA_PRE_PRODUCAO.md`,
    `docs/SEGURANCA_ARMAZENAMENTO.md`.
    **Modificados:** `knowledge.py`, `knowledge_catalog.py`, `knowledge_projection.py`,
    `responses.py`, `evaluation/oracle.py`, `config.py`, `bootstrap.py`, `persistence/sqlite.py`,
    `panel/{knowledge.py, api.py}`, `panel/static/{index.html, app.js, styles.css}`,
    `ops/{backup.py, demo.py, perf.py, __main__.py}`, `.env.example`, `.gitignore`, testes
    afetados (versão de esquema e quantidade de travas), documentação listada abaixo.

## C. Segurança

12. **Ambientes:** `ENVIRONMENT_PROFILE` (development, demo, test, preproduction) gravado no banco;
    banco `demo`/`preproduction` não abre em outro perfil nem sem perfil; banco antigo não vira
    pré-produção; banco `*.preprod.*` só com o perfil certo; configurações contraditórias recusadas.
13. **Persistência:** pré-produção exige diretório 0700 do próprio usuário e volume com cifragem
    **verificada** (falha antes de criar qualquer arquivo).
14. **Criptografia:** a aplicação **não cifra o banco**; ela **constata** dm-crypt/LUKS pelo
    `/sys` (inclusive LVM sobre LUKS) e pode exigi-lo. SQLCipher avaliado e não adotado;
    recomendação: volume cifrado agora e no primeiro servidor (`SEGURANCA_ARMAZENAMENTO.md` §2).
15. **Backups:** cópia e verificação **em memória** (nenhum arquivo em claro no temporário);
    retenção `ops reter` com plano e confirmação `APAGAR:N`.
16. **Proteção de dados:** quarta trava `REAL_DATA_ACTIVATION_AUTHORIZED=False`;
    `DATA_CLASSIFICATION=real` sempre recusado; banco marcado com outra classificação é recusado.
    `ops ambiente` lista os requisitos para dados reais (todos pendentes, exceto a cifragem do
    volume nesta máquina).
17. **Segredos:** nada novo no repositório; perfis com segredos sintéticos gerados localmente
    (0600); credenciais de pré-produção com troca obrigatória no primeiro acesso.
18. **Auditoria:** aprovações, negações (conflitos/pendências) e revogações dos novos tipos
    ficam no `panel_audit`.

Revisão sistemática: autenticação/sessões/cookies/CSRF/XSS/RBAC/IDOR preservados (testes das
Fases 7–11 continuam passando); APIs novas do conhecimento conferem ADMIN no backend (teste de
atendente recebendo 403); logs e métricas sem conteúdo pessoal (teste com texto e telefone
fictícios). Riscos remanescentes: sem 2FA; painel sem HTTPS fora de 127.0.0.1; servidor ligado
e comprometido expõe o volume aberto (limite de qualquer cifragem em repouso).

## D. Infraestrutura (planejada, não implantada)

19–24. Recomendação: **um servidor virtual Linux pequeno com disco cifrado**, `systemd` para
webhook/worker/painel, proxy reverso com HTTPS expondo **só** o webhook, painel atrás de VPN
ou proxy autenticado (HTTPS, cookie Secure, 2FA antes de dados reais), backup diário cifrado
para armazenamento externo, monitoramento de `/health` e dos indicadores de `ops metricas`.
Comparação das três opções, exposição, disponibilidade, procedimentos e plano em 10 etapas em
`ARQUITETURA_PRE_PRODUCAO.md`.
25. **Custos:** nenhum valor presumido. A cotar: servidor, armazenamento externo de backups,
domínio, mensagens do WhatsApp acima da franquia, uso do Gemini, horas de manutenção.

## E. Qualidade

26–27. **917 pytest e 30 Chromium** aprovados ao final (inclui avaliações offline, Gemini
simulado, travas, Booksy por link, inbox/outbox, deduplicação, atendimento humano, notificações).
Novos: `test_phase12_knowledge.py` (33), `test_phase12_environment.py` (28),
`test_phase12_browser.py` (4: desktop e celular, tipos de preço, permissões).
28. **Migração:** banco novo; banco antigo (versão 9 com conversas, filas e conhecimento)
atualizado sem alterar dados; documentos antigos válidos; integridade e chaves estrangeiras;
migração interrompida (tabela e metadado parciais revertidos, versão inalterada, nova
tentativa aplica); execução repetida sem efeito; 010 idempotente.
29. **Recuperação:** backup de versão antiga restaurado e migrado (original intacto); backup
após migração; nenhum temporário em claro; WAL e `temp_store` conferidos; retenção; os casos da
Fase 10 (chave errada, corrompido, sobrescrita, falha no meio, UNKNOWN/outbox/auditoria
preservados) continuam passando.
30. **Segurança:** ambientes, diretório inseguro, dados reais, travas, RBAC dos novos dados,
logs/métricas sem dados pessoais, verificação de cifragem (cadeias dm simuladas e reais).
31. **Desempenho** (Linux, 8 CPUs, Python 3.13.16; 1000 conversas, 2398 mensagens, conversa de 400;
30 repetições; `ops desempenho`):

| Medida | Mediana | p95 |
|---|---|---|
| Lista "todas" (ADMIN) | 11,6 ms | 11,8 ms |
| Visão geral (ADMIN) | 3,9 ms | 4,4 ms |
| Avisos (ADMIN) | 2,6 ms | 3,1 ms |
| Página recente do histórico | 1,5 ms | 1,8 ms |
| Prévia do conhecimento | 3,1 ms | 3,6 ms |
| Rascunho + aprovação de categoria | 17,4 ms | 17,9 ms |
| 4 atendentes simultâneos (30 ciclos lista+avisos+visão cada) | 0,70 s no total, **0 erros** | — |
| Backup cifrado com mensagens chegando (5 gravadas durante a cópia) | 115 ms (verificar: 70 ms) | — |
| Reabrir o banco (reinício) | 7,2 ms | — |
| Memória de pico do processo de medição | 116 MB | — |

Medição local com dados sintéticos; **não** é garantia de produção. Nenhuma otimização foi
necessária.
32. **Corrigidos:** preço "a partir de" dito como fixo; cópia em claro do backup no temporário;
temporários do SQLite fora do volume; ambientes misturáveis; tabelas alargando a página no
celular (botão Confirmar inalcançável); verificação de rolagem horizontal fraca; patch do
Gemini quebrado pela nova trava.
33. **Limitações:** acessibilidade só automatizada; sem servidor real para testar cifragem,
HTTPS, VPN e restauração entre máquinas; limite de 20 serviços e 30 profissionais/categorias
por base (suficiente para a hipótese de volume, a rever com os dados oficiais); o banco
restaurado e a cópia `pre-restore` ficam em claro no volume de destino (dependem da cifragem
do volume).

## F. Prontidão

| Pergunta | Resposta |
|---|---|
| 34. Implementado | Itens da seção B e C |
| 35. Testado localmente | Seção E (917 + 30) |
| 36. Depende do salão | 11B, dados oficiais (agora com campos de categoria, duração, profissionais e tipo de preço), decisões de `DADOS_PENDENTES.md` §3, RPO/RTO |
| 37. Falta para dados reais | Os oito requisitos de `SEGURANCA_ARMAZENAMENTO.md` §6 (inclui 2FA, HTTPS, backup externo, custódia da chave, jurídico, homologações) e abertura revisada da quarta trava |
| 38. Falta para ativar Gemini | Checklist `ARQUITETURA_PRE_PRODUCAO.md` §8.2 (autorização, chave, execução 9C-B, critérios) |
| 39. Falta para ativar Meta | Checklist §8.3 (conta e número de teste, webhook HTTPS, token, 6B) |
| 40. Falta para produção | Etapas 4–10 de `ARQUITETURA_PRE_PRODUCAO.md` §7 |
| 41. Próxima fase recomendada | Executar a **11B** com a equipe e receber o formulário de dados; em paralelo, cotação de hospedagem. Nenhuma API real antes disso |
