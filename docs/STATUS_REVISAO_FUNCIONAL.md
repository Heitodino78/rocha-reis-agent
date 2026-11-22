# Revisão funcional e visual completa (antes da implantação na Hostinger)

Executada em 22/11/2026 na máquina de desenvolvimento (VS Code em Flatpak), com dados **fictícios**, IA e
WhatsApp **simulados**. **Nenhuma conta, contratação, deploy, commit ou push; Meta, Gemini e backup externo
não conectados; as cinco travas continuam `False`** (nenhuma foi alterada; testes que "abrem" uma trava o
fazem só em memória, por `monkeypatch`). Nenhum perfil, banco ou diretório existente foi apagado: tudo rodou
em diretórios descartáveis de teste.

| Situação | Estado |
|---|---|
| IMPLEMENTADO | correções da §B (código, testes e documentos) |
| TESTADO LOCALMENTE | pytest **1100**, Chromium **60**, roteiro **13/13**, auditoria ao vivo (§D) |
| VALIDADO COM USUÁRIO | **Não** (11B pendente) |
| IMPLANTADO | **Não** |

## A. Linha de base

O último resultado completo registrado (18B-0) era pytest 1074, Chromium 46, roteiro 13/13. Os venvs de
`/tmp` tinham sido apagados (reinício); foram recriados **seguindo o README literalmente**. A primeira
regressão completa desta revisão, sobre as quatro correções locais já existentes, deu **10 falhas em 1089**:
7 vinham da reabertura pelo cliente (§B, R1) e 3 eram artefato de editar arquivos durante a execução
(passaram ao repetir).

## B. Problemas encontrados

Gravidade: **Alta** = estado inconsistente, perda de atendimento ou trava invisível; **Média** = fluxo
quebrado com contorno; **Baixa** = documentação/usabilidade; **Cosmética**.

| # | Problema | Gravidade | Causa | Correção | Teste |
|---|---|---|---|---|---|
| C1 | Cliente que escreve num atendimento **encerrado** ficava sem resposta, fora da fila e sem aviso | Alta | `RESOLVED` só gravava a mensagem | Volta para `WAITING_HUMAN` sem responsável (ADR-134) — correção local anterior, conferida | `test_client_writing_after_resolution_…`, ciclo completo no Chromium |
| R1 | **Regressão de C1:** "Retomar automação" logo após o cliente voltar dava 409; 7 testes antigos falhavam; os 3 roteiros de `examples/` do README imprimiam `Erro: Transição inválida` e terminavam sem retomar | Média | Depois da reabertura a conversa está na fila, e a retomada só vale a partir de `RESOLVED` | Roteiros com `/operator resolve` antes de `resume`; 7 testes adaptados **mantendo** as garantias (sem resposta automática, sem chamar o modelo, retomada explícita obrigatória, transição registrada) | `test_documented_cli_demo_scripts_…`, `test_documented_two_process_demo_resumes` |
| C2 | 5ª trava (backup externo) ausente do painel, `validar-instalacao`, `ops ambiente`, manifesto e iniciador; o iniciador conferia só 3 | Alta | Cinco listas copiadas à mão | Correção local anterior + **fonte única** `config.activation_locks()` (ADR-135) | `test_five_locks_…`, `test_single_source_of_locks_…` (×5) |
| N1 | `ops estado-servidor` (diagnóstico do servidor) não mostrava trava nenhuma | Média | Nunca incluídas | Campo `travas` (só informa; não muda a saída) | `test_five_locks_…` |
| N2 | **Corrida:** transferir para alguém (ou assumir) no mesmo instante em que a administração desativa essa pessoa deixava a conversa em atendimento com um responsável **desativado** | Alta | "Ativo" conferido **antes** da transação; a desativação grava `active=0` e só depois devolve à fila o que a pessoa tem | Conferência dentro do mesmo `BEGIN IMMEDIATE` da atribuição (`store.stage_assign`) → 409 "A responsabilidade mudou" | `test_transfer_racing_a_deactivation_…`, `test_assume_in_flight_while_…` (falham sem a correção) |
| N3 | Reiniciar logo após o Ctrl+C dava **"Porta ocupada"** por ~60 s | Média | Teste de porta sem `SO_REUSEADDR`: conexões do painel em `TIME_WAIT` | `SO_REUSEADDR` na sondagem (no Linux, uma porta com alguém escutando continua recusada) | `test_restart_right_after_stop_…`; reinício real na auditoria |
| C3 | Equipe: botões cortados no notebook (1366 px) | Média | Tabela de 7 colunas maior que a área | Cartões rotulados até 1439 px — correção local anterior, conferida | Chromium 6 larguras × 2 temas |
| C4 | Conhecimento: palavras partidas no meio | Média | `overflow-wrap: anywhere` | Quebra só entre palavras — correção local anterior, conferida com detector de palavra partida | idem |
| C5 | Atendente recebia 409 (não 403) em `GET /knowledge` | Baixa | Configuração lida antes da permissão | Permissão primeiro — correção anterior | `test_operator_cannot_reach_any_administrative_route_directly` |
| C6 | Linha PRONTO sem o caminho das senhas (no Flatpak a pasta é outra) | Baixa | — | Caminho completo — correção anterior | `test_ready_line_prints_…` |
| D1 | Guia: ordem impossível e cópia de unidade com nome inválido | Alta (doc) | — | Correção anterior, conferida | `test_deployment_guide_order_…` |
| D2 | Guia §4: dois passos "2b"; `tar` com caminho relativo e `--pacote /root/...`; `python` sem venv (não existe no Ubuntu); `rm -rf` da pasta onde o shell estava | Baixa | — | Passo 2d, caminhos `/root/` coerentes, venv explícito, `cd /` | Simulação literal (§E) |
| D3 | Roteiro de demonstração, checklist 11B e plano de validação mandavam conferir **quatro** travas | Baixa | Desatualizados | "Cinco travas" | — |
| D4 | ADR-107 duplicado | Baixa | Número já usado | ADR-134 | — |
| D5 | README sem o comando dos testes Chromium nem o local do navegador no Flatpak; treinamento sem a reabertura | Baixa | — | Acrescentados | — |

**Não corrigidos (registrados):**

| # | Problema | Gravidade | Por quê |
|---|---|---|---|
| U1 | **Resolvido na rodada 2 (§H)** — **Decisão de produto:** com ADR-134, qualquer mensagem após o encerramento (até "Obrigada! 😊") volta para a fila com aviso | Média | Decisão do salão, não técnica. Alternativa: reabrir só depois de um intervalo, ou manter `RESOLVED` e só avisar |
| U2 | **Resolvido na rodada 2 (§H)** — Manifesto RC1 congelado lista 4 travas | Baixa | Recongelar faz parte do encerramento autorizado (com commit). Um congelamento de teste fora do repositório já sai com as 5 |
| U3 | **Resolvido na rodada 2 (§H)** — No celular, "· Automação suspensa" pode começar a linha com o separador | Cosmética | Teste existente fixa o texto; mudança desnecessária para corrigir defeito |
| U4 | Equipe/Conhecimento em cartões até 1439 px: ocupam mais altura no notebook | Baixa (usabilidade) | Escolha consciente para nada ficar escondido |

## C. Cinco travas

| Onde | Resultado |
|---|---|
| Código | 5 constantes `Final = False` (teste lê o fonte) |
| Backend / painel | `GET /system` com 5 chaves `false`; tela Sistema mostra as 5 "bloqueada" (captura conferida) |
| Inicialização | `ops iniciar` / `iniciar.sh --verificar`: "travas reais desligadas"; recusa com qualquer uma aberta (×5) |
| Ensaio (`ops roteiro`) | recusa com qualquer uma aberta (×5) |
| Validação da instalação | `validar-instalacao` numa instalação simulada: 5 × `false` |
| Diagnóstico | `ops ambiente` e `ops estado-servidor`: 5 × `false` |
| Manifesto | `release_info` / congelamento de teste: 5; manifesto oficial: 4 (U2) |
| Backup externo | adaptador S3 recusa Backblaze, R2 e AWS com a trava fechada **antes de abrir conexão** (sockets bloqueados no teste) |
| Integrações reais | nenhum teste ou execução saiu de 127.0.0.1 |

## D. Telas e fluxos executados

**Automatizado (Chromium, sistema em execução).** Além das suítes (§F), uma auditoria roteirizada contra o
sistema iniciado pelo **iniciador real** (`ops demo` + `ops iniciar`, perfil descartável):

- Administração: Visão geral, Conversas, Equipe, Conhecimento, Configurações, Sistema, Auditoria, Minha
  conta, Ajuda e Avisos em **320, 390, 768, 1024, 1366 e 1920 px, claro e escuro** (120 telas + 12 avisos):
  0 rolagem lateral, 0 controle fora da tela ou recortado, **0 palavra partida entre linhas**, 0 erro JS.
- Atendente: menus administrativos ausentes; **11 chamadas diretas** a rotas administrativas → todas 403;
  menu da Equipe forçado pelo DOM → nenhum dado; filas; assumir, responder (outbox simulado `ACCEPTED`),
  transferir, encerrar; "Retomar automação" oculto para atendente.
- Segundo fator: ativação, código errado recusado, certo aceito.
- **Ctrl+C e reinício imediato na mesma porta**; estados das 12 conversas e equipe iguais após o reinício.

**Ciclo completo** (`test_complete_attendance_cycle_survives_restart`, conferido no banco a cada passo): criar
funcionário → 1º acesso com troca de senha → receber → assumir → responder → transferir → encerrar →
cliente volta (reabre) → desativar com atendimento → redistribuição → reinício → persistência.

**Manual:** inspeção visual das capturas (Equipe e Conhecimento em várias larguras, Sistema, conversa no
celular). **Não houve teste manual por pessoa da equipe do salão.**

## E. Documentação simulada

- Venvs do README e de `requirements-e2e.txt` recriados literalmente: ok.
- Todos os `ops <comando> --opção` de 15 documentos operacionais conferidos contra a CLI real: 0 inexistentes.
- Guia §4, passos 1–2c: pacote gerado, extraído **sem subpasta**, ambiente temporário com
  `--require-hashes --no-deps`, `instalar` numa raiz descartável → `ativada: true`.
- `iniciar.sh --verificar` e os 4 roteiros de `examples/`: ok após R1.
- **Não simulado:** passos com root (useradd, LUKS, systemd do sistema, nftables, Caddy, WireGuard) — já
  ensaiados em laboratório na 18A/18B-0 e não afetados por estas mudanças.

## F. Regressão

| Conjunto | Antes | Depois |
|---|---|---|
| pytest | 1074 (último registrado) | **1100 aprovados, 0 falhas** (10 min 42 s) |
| Chromium | 46 | **60 aprovados, 0 falhas** (13 min 10 s) |
| Roteiro (`ops roteiro`) | 13/13 | **13/13** |
| Laboratórios de infraestrutura (`tests_infra/`) | — | **Não repetidos:** só leem `ok`, `camadas`, `atendimento_operacional` e `travas` dessas saídas, que não mudaram de forma |

Os testes de documentação e da revisão foram repetidos depois das últimas edições de documentos (51 ok).

## G. Riscos restantes

- U1 precisa de decisão do salão antes de operar com clientes.
- Nada foi testado em servidor real, `reboot`, systemd do sistema, usuário `rocha-reis`, LUKS no provedor,
  backup externo real, Meta ou Gemini.
- Navegador só Chromium headless com viewport emulado; sem Safari/Firefox, celular físico ou leitor de tela.
- Validação com a equipe do salão (11B) continua pendente.

## H. Rodada 2 (22/11/2026) — U1, U3, travas, RC `post7`

Mesmas restrições: nada real conectado, nenhuma trava aberta, sem commit/push. Ponto de partida: `e8ea38d`
com as alterações locais da rodada 1 preservadas.

### H.1 U1 — cortesia depois do encerramento (REGRA PROVISÓRIA)

**Causa:** a reabertura (ADR-134) era incondicional: `AgentService` chamava `reopen_by_client` para QUALQUER
mensagem numa conversa `RESOLVED`. E mídia (áudio, imagem…) nem chegava à conversa: o canal a marcava
`unsupported_type_ignored` em qualquer estado, ou seja, um áudio depois do encerramento sumia sem aviso.

**Regra (ADR-136), provisória e sujeita à aprovação do salão na 11B — não é decisão comercial aprovada:**
um único módulo determinístico (`application/courtesy.py`, sem IA), chamado num único ponto, dentro da
transação versionada da conversa e depois da deduplicação. Só é cortesia a mensagem **inteiramente** formada
por expressões de uma lista fechada, emojis de uma lista fechada e pontuação simples; **na dúvida, reabre**.

| Não reabre (fica no histórico, continua encerrada, sem aviso, sem resposta) | Reabre (fila humana, sem responsável, aviso, sem resposta automática) |
|---|---|
| "Obrigado", "Obrigada!", "Valeu!!!", "Ok", "Beleza", "Show", "Tudo certo.", "👍", "🙏", "Obrigado 😊", "OBRIGADAAA 🙏🏽", "ok, obrigada!", "Muito obrigada pelo atendimento ❤️", "Tá bom", "Combinado" | Pedido: "Quero agendar um horário.", "Preciso mudar meu agendamento.", "Quanto custa o corte?", "Quero falar com uma pessoa." · Reclamação: "O atendimento ainda não resolveu meu problema." · Mista: "Obrigado, mas preciso de ajuda.", "Valeu, queria agendar amanhã.", "Ok, mas não consegui resolver.", "Obrigada! Qual o preço da escova?" · Ambígua: "Então...", "Ainda não", "Pode ser", "?", "Ok?", "Ok..." · Negação: "Não está tudo certo" · Emoji fora da lista: "😡" · Saudação: "Bom dia" · **Qualquer mídia** |

**Mídia:** com atendimento humano ou encerrada, entra no histórico como aviso ("[O cliente enviou um áudio.
Conteúdo não exibido nem interpretado pelo sistema.]"), e a encerrada reabre. Em conversa **automática** e de
contato sem conversa, o tratamento anterior foi mantido (decisão pendente em `DADOS_PENDENTES.md`). Nada
é baixado nem respondido. A janela de 24 h da Meta continua contada pela última mensagem do cliente.

**Preservado:** retomada da automação só por ação explícita da administração; depois de uma cortesia ela
vale direto (a conversa continuou `RESOLVED`); reabertura remove o responsável anterior; funcionário
desativado nunca recebe a conversa; nenhuma resposta fictícia é gravada como enviada.

### H.2 Testes de U1 (`tests/test_u1_cortesia.py`, 98 testes) — conferem banco, histórico, transições, avisos e saída

| # pedido | Cobertura |
|---|---|
| 1–4 | 14 cortesias (simples, emoji, pontuação, confirmação): `RESOLVED`, mensagem no histórico, transições iguais, responsável histórico intacto, outbox igual, **nenhum** aviso para admin/Ana/Bia, fila vazia |
| 5–9 | 18 mensagens (pedido, pedido de humano, reclamação, 4 mistas, 4 ambíguas, negação, emoji, saudação): `WAITING_HUMAN`, `client_returned`, sem responsável, aviso para todos, sem resposta |
| 10–11 | áudio, imagem, documento, localização, interativa, tipo desconhecido após encerramento: reabre com aviso sem conteúdo; áudio durante atendimento chega ao responsável sem mudar estado; em AUTO, tratamento anterior |
| 12 | reentrega do mesmo `wamid` (cortesia, pedido, mídia): processada uma vez |
| 13 | duas mensagens SIMULTÂNEAS por dois núcleos no mesmo arquivo (cortesia+pedido, dois pedidos, duas cortesias): nenhuma perdida, **no máximo uma** reabertura; repetido 10×, estável |
| 14 | reinício (aplicação nova sobre o mesmo banco): estado e histórico iguais |
| 15–17, 19 | dentro dos casos acima |
| 18 | responsável anterior desativado: cortesia não reabre; pedido reabre sem responsável; requisição dela "em voo" é recusada na transação; outra pessoa assume |
| 20 | travas fechadas, transporte fake, nenhuma entrega; rede bloqueada pelo `conftest` |
| navegador | `tests_e2e/test_u1_u3_browser.py`: admin, atendente no notebook e no celular, ao vivo |

### H.3 U3 — separador

`· Automação suspensa` podia começar a linha. Agora o texto não tem separador (a linha já é flex com
espaçamento); nas listas em texto corrido o "·" é precedido de espaço inseparável, e "Cliente ··xxxxxx" não
quebra entre a palavra e o código (o detector achou esse segundo caso em 768 px). Teste: 320, 390, 768 e
1366 px, claro e escuro, sem nenhum "·" no início de linha e sem rolagem lateral.

### H.4 Cinco travas

`config.activation_locks()` é a fonte de todas as listas: inicialização, ensaio, painel/API (`/system`,
com mapa fixo de nomes), `ops ambiente`, `estado-servidor`, `validar-instalacao`, manifesto. Novo
`tests/test_travas_divergencia.py`: abre cada trava **só em memória** e exige que TODAS as representações
mudem igual; acusa qualquer módulo que monte lista própria (os portões `bootstrap.py` e
`evaluation/real_run.py` leem cada um a trava da própria integração, e estão explicitamente listados);
confere o manifesto oficial. As cinco continuam `False` no código.

### H.5 Defeitos encontrados nesta rodada

| # | Defeito | Gravidade | Correção | Teste |
|---|---|---|---|---|
| U1 | Cortesia reabria e avisava | Média | Regra provisória (H.1) | `test_u1_cortesia.py` |
| M1 | Áudio/imagem depois do encerramento sumia (nem histórico, nem fila) | **Alta** | Histórico + reabertura (H.1) | idem, casos 10–11 |
| S1 | **401 atrasado** de pedido da sessão anterior derrubava a sessão nova logo após o login (e apagava a senha); aparecia como falha intermitente (1 em 8) de `test_session_expiry_…` | Média | 401 só encerra a sessão que fez o pedido (`sessionGeneration`) | `test_late_401_from_the_previous_session_…` (falha sem a correção; 12/12 no teste antes intermitente) |
| U3 | Separador no início da linha (+ rótulo "Cliente ··") | Cosmética | H.3 | `test_u3_separator_never_starts_a_line` (×8) |
| U2 | Manifesto com 4 travas | Baixa | Recongelado (`post7`) | `test_official_rc1_manifest_lists_the_five_locks_closed` |
| T1 | Painel montava a própria lista de travas | Baixa | Derivada da fonte única | `test_travas_divergencia.py` |
| P1 | Pacote com um byte adulterado podia derrubar `instalar`/`atualizar` com `zlib.error` cru em vez da recusa controlada; aparecia como falha **dependente do conteúdo** de `test_update_refuses_wrong_confirmation_and_tampered_package` (troca só o byte do meio) | Média | `zlib.error` tratado como pacote corrompido | `test_any_single_corrupted_byte_is_refused_cleanly` (~100 posições; aceita só se o conteúdo instalado for idêntico ao original) |

Ao revisar, uma edição cosmética minha no arquivo de cenários (número do ADR) quebrou o **pré-registro por
hash** do experimento 9C (5 testes); foi desfeita, não re-registrada. O texto "ADR-107" permanece ali como
rótulo histórico.

### H.6 Regressão

| Conjunto | Antes (rodada 1) | Depois |
|---|---|---|
| pytest | 1100 | **1207 aprovados, 0 falhas** (12 min 10 s), no código final (inclui P1 e S1) |
| Chromium | 60 | **70 aprovados, 0 falhas** (15 min 16 s), depois da correção S1 (P1 é só do servidor de instalação: não afeta o painel) |
| Roteiro | 13/13 | **13/13** |
| Concorrência (U1 + corridas + duplicatas) | — | 9 testes × 10 execuções, todas aprovadas |
| Avaliação offline da IA (fake v1/v4, hostil v2) | — | U1 muda **0 de 129** cenários (só latência local varia). Diferenças contra `evals/results/phase9b-*` já existiam no commit `e8ea38d` (suíte de cenários diferente; a ferramenta avisa "comparação NÃO equivalente"); a revisão só altera `handoff_recall` 0,9024 → 0,9048 (cenário H6) |
| Laboratórios `tests_infra/` | — | **não repetidos**: nada em systemd, volume, rede, instalação ou códigos de saída mudou; só leem campos inalterados |

### H.7 Situação

- **Implementado e testado localmente:** H.1–H.5.
- **Depende de validação humana:** a lista de cortesia (11B, item no checklist); comportamento de mídia em
  conversa automática (pendente).
- **Não feito:** commit, push, tag, release, implantação, homologação 11B.
- RC: `0.14.0rc1.post7`, manifesto `docs/homologacao/RC1_MANIFESTO.json` (impressão no próprio manifesto e em
  `verificar-rc`). O manifesto prova **integridade de arquivos**, não implantação nem homologação.
