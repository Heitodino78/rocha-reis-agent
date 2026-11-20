# Relatório técnico — Fase 15 (preparação da homologação com o salão)

Executada localmente em 20/11/2026. Dados fictícios, IA e WhatsApp simulados. **Nenhuma API externa,
nenhum dado real, nenhum deploy, nenhum commit, tag ou push.** **A Fase 11B continua pendente**:
nenhum funcionário participou; nenhum resultado humano foi registrado.

| Situação | Estado |
|---|---|
| PREPARADO PARA A 11B | Sim — materiais, planilha de registro, critérios propostos, checklist |
| TESTADO LOCALMENTE | **964 pytest + 39 Chromium**; roteiro 13/13; avaliações offline |
| VALIDADO PELO SALÃO | **Não** |
| HOMOLOGADO EXTERNAMENTE / IMPLANTADO | **Não** |

## A. Diagnóstico

- Git: `main` em `c59b934`, 74 alterações das Fases 10–14 (sem commit).
- RC1 conferida: `0.14.0rc1`, impressão digital `74f98ad6a343bc68` — **igual** à registrada na Fase 14.
- Travas: as quatro `False`. Baseline: **958 pytest**; roteiro **13/13**. Chromium: 37 na Fase 14;
  a contagem do baseline desta fase saiu 39 porque os 2 testes novos de navegador já existiam quando
  a parte de navegador começou.
- Problemas encontrados:
  1. **Iniciador anunciava o endereço antes de o servidor subir**; se a porta fosse tomada nesse meio
     tempo, a pessoa via o endereço e depois um erro técnico do uvicorn.
  2. Num perfil de pré-produção, o iniciador apontava `credenciais-demo.txt` (inexistente lá).
  3. Na primeira versão da correção 1, o aviso "PRONTO" era impresso em várias linhas e podia sair
     pela metade se o painel fosse encerrado logo depois (achado pelo teste novo, 3 repetições estáveis após a correção).
  4. Cenários da 11B sem **sessão encerrada** nem **recuperação de erro**; a redação do AT-16, ao ser
     testada, descrevia um comportamento pior que o real (a segunda aba percebe o encerramento sozinha).
  5. Formulário de dados com a linha "Remarcação" duplicada e referência das decisões apontando
     para um relatório antigo.
  6. Sem forma de garantir que a sessão de homologação use **o mesmo código** da candidata.
  7. Sem mecanismo de registro dos resultados humanos.

## B. Correções e acréscimos

- `ops/launcher.py`: "Iniciando…" → **"PRONTO"** só depois que o servidor aceita conexões, num único
  bloco (endereço, logins fictícios **sem** senhas, como encerrar); mensagem clara se não subir;
  arquivo de credenciais certo por perfil.
- `ops/release.py`: **congelamento verificável** — `ops congelar-rc` grava
  `docs/homologacao/RC1_MANIFESTO.json` (hash de cada arquivo); `ops verificar-rc` diz se o código é o
  mesmo e, se não, quais arquivos mudaram/entraram/saíram.
- `ops/uat.py`: `ops registro-11b` (planilha CSV dos 31 cenários gerada do próprio plano, carimbada
  com versão e impressão digital, campos de resultado **em branco**, 0600, **recusada dentro do
  repositório**) e `ops conferir-registro` (aponta faltas/valores inválidos/versão diferente; **não
  altera o arquivo e não aprova nada**).
- Versão da candidata: **`0.14.0rc1.post1`** — a mesma RC1 com as correções acima (comportamento do
  iniciador mudou, então a identificação mudou).
- Documentos: `PLANO_VALIDACAO_SALAO.md` (AT-09 e AT-16 estendidos, mesma numeração e quantidade;
  critérios gerais propostos para o salão confirmar), `CHECKLIST_HOMOLOGACAO_11B.md` (verificar-rc,
  planilha, confirmação dos critérios, opções de celular real), `COLETA_DADOS_OFICIAIS.md` (fluxo de
  conferência e devolução antes do cadastro), `DECISOES_PENDENTES_SALAO.md` (colunas problema/quem
  decide/data; "quem restaura" unido a RTO/RPO e incluída a confirmação dos critérios da 11B — 14
  itens), `ROTEIRO_DEMONSTRACAO_SALAO.md`, `PLANO_DE_EXECUCAO.md`, `README.md`; novos
  `PACOTE_HOMOLOGACAO_SALAO.md`, `RELATORIO_PRONTIDAO_RC1.md`, `STATUS_FASE_15.md`.
- Testes novos: `tests/test_phase15.py` (6) e `tests_e2e/test_phase15_browser.py` (2: AT-16 com duas
  abas e AT-09 com falha e reenvio, na demonstração real).

Impacto nas fases anteriores: nenhum comportamento do painel ou do atendimento mudou.

## C. Homologação

31 cenários (17 AT, 14 AD) com instruções, esperado, campos de resultado em branco e critério;
apresentação, treinamento, checklist, planilha de registro e critérios propostos. **Condição para a
11B acontecer:** pessoas reais do salão + responsável que confirme os critérios e assine.

**Celular físico:** não testado. Opções documentadas (`CHECKLIST_HOMOLOGACAO_11B.md` §1b): modo
celular do navegador (testado); Android de teste por cabo USB com `adb reverse` (sem abrir porta na
rede; **requer autorização** para instalar `adb`; não testado); Wi-Fi **não** usado nesta fase.

## D. Segurança

Autenticação, 2FA, sessões, RBAC, CSRF, XSS, isolamento, logs, backup e restauração cobertos pela
regressão completa. Novo: a demonstração **não** contorna a pré-produção (perfil de demonstração
apontado para o banco de pré-produção é recusado; contas da demonstração não existem lá; pré-produção
mantém 2FA e HTTPS). Iniciador não imprime senhas. Registro da 11B fora do repositório, 0600.
Quatro travas `False`.

## E. Qualidade

| | Baseline | Final |
|---|---|---|
| pytest | 958 | **964** |
| Chromium | 37 (Fase 14) | **39** |
| Roteiro da demonstração | 13/13 | **13/13** |

Avaliações offline (critérios inalterados): regras/fake 123/129, segurança OK, **Q2 reprovado**
(recall 0,902); hostile 129/129; gemini-simulated 123/129. Riscos pendentes para a homologação do
Gemini: pedido humano indireto (H9, H12), reclamação indireta (L3, L4), gírias de preço (J6, J7).
Não foram "corrigidos" encaminhando mais conversas a pessoas nem alterando cenários.

## F. Entrega

Ponto de entrada: `PACOTE_HOMOLOGACAO_SALAO.md`. Iniciar: `ops verificar-rc` → `sh iniciar.sh
--nova-demo` → linha "PRONTO". Testes com funcionários: `CHECKLIST_HOMOLOGACAO_11B.md`. Registro:
`ops registro-11b` → preencher na sessão → `ops conferir-registro` → responsável assina §7 do plano.

## G. Pendências

Salão: participar da 11B, confirmar critérios, preencher o formulário, decidir os 14 itens.
Infraestrutura: servidor, disco cifrado, HTTPS, VPN, backup externo, monitoramento. Gemini e Meta:
homologações autorizadas. Produção: `CHECKLIST_PRODUCAO.md`.

## H. Git

Estado final: `main` em `c59b934`; 80 caminhos alterados/novos (34 modificados, 46 novos); nenhum
commit, tag ou push. Diff auditado: nenhum banco, backup, chave, credencial, log, temporário ou arquivo
> 200 KB; nenhum padrão de segredo. Estratégia e mensagem em `RELATORIO_PRONTIDAO_RC1.md` §5.
