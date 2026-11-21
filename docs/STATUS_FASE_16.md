# Relatório técnico — Fase 16 (redesign do painel, auditoria e refinamento)

Executada localmente em 20/11/2026. Dados fictícios, IA e WhatsApp simulados. **Nenhuma API externa,
nenhum dado real, nenhum deploy, nenhum serviço contratado, nenhum commit, tag ou push.** As quatro
travas continuam `False` e não foram tocadas. **A Fase 11B continua pendente** (nenhum funcionário
participou).

| Situação | Estado |
|---|---|
| IMPLEMENTADO | Sim — sistema de design, todas as telas, correções da auditoria |
| TESTADO LOCALMENTE | **969 pytest + 44 Chromium**; roteiro 13/13; avaliações offline |
| VALIDADO PELO SALÃO / IMPLANTADO | **Não** |

## A. Linha de base

`main` em `f406641` (árvore limpa); `ops verificar-rc` idêntico (`0.14.0rc1.post1`). Baseline medido
antes de qualquer edição: **964 pytest**, **39 Chromium**. Capturas "antes": 11 telas × 3 modos
(desktop claro, desktop escuro, celular 390 px) com a demonstração real (`build_demo`).

## B. Redesign (sistema de design)

`rocha_reis_agent/panel/static/styles.css` reescrito como sistema de design:

- **Tokens semânticos** em `:root` e no tema escuro: superfícies (`--bg`, `--surface`, `--surface-2/3`),
  texto (`--ink`, `--ink-2`, `--muted`), linhas, acento ameixa, estados (`--success`, `--warning`,
  `--danger`, `--info`, `--neutral` com fundos sólidos), escala de espaçamento, raios, sombras e
  tipografia. Os nomes antigos (`--waiting`, `--auto`…) viraram apelidos dos novos.
- **Tipografia** sem serifa em todo o painel, números tabulares, hierarquia de títulos consistente.
- **Ícones** de traço desenhados no próprio CSS (máscaras SVG `data:`, permitidas pela CSP; nenhum
  recurso externo, nenhuma fonte baixada).
- **Navegação** agrupada em *Atendimento*, *Gestão* (só administração) e *Conta*, com ícones,
  contadores e cartão do usuário com iniciais.
- **Visão geral**: indicadores com ícone, tom semântico e número que só "acende" quando há algo a
  fazer; "Precisa de atenção" com faixa de gravidade.
- **Conversas**: filtros numa linha rolável (antes 3 linhas), itens com avatar e marcador lateral de
  espera/urgência, conversa aberta com cabeçalho, bolhas e campo de resposta refinados.
- **Tabelas** com cabeçalho destacado, situações em "pílulas" (Equipe, Auditoria, Conhecimento).
- **Sistema**: estado em lista chave-valor; travas em cartões com cadeado e "bloqueada".
- **Login/2FA/troca de senha**: cartão com marca, campos de 44 px, rodapé de acesso restrito.
- **Diálogos, avisos e toasts**: elevação, animação curta (desligada com "reduzir movimento").
- **Responsivo**: gaveta do menu com fundo escurecido no tablet/celular; páginas com largura máxima
  em telas grandes; Equipe com a lista antes do cadastro.

Contratos preservados: todos os IDs, classes e textos usados pelo JavaScript e pelos testes; nenhum
`innerHTML`; nenhum estilo ou script inline; CSP inalterada.

## C. Auditoria — problemas encontrados e corrigidos

| # | Problema (comprovado) | Correção |
|---|---|---|
| 1 | Ao entrar, os avisos já pendentes viravam até 4 toasts por 5 s, cobrindo a fila e o campo de resposta (no celular, quase a tela toda) | Primeira leitura da sessão gera **um** resumo ("N aviso(s) pendente(s) — veja no sino"); depois, no máximo 2 + "Mais N"; no máximo 3 toasts na tela |
| 2 | Toast de erro e contador crítico do sino com contraste insuficiente no tema escuro (texto branco sobre rosa claro, ≈2:1) | Token `--danger-solid` sólido nos dois temas |
| 3 | Tabela da Equipe desalinhada: a célula de ações era a própria caixa flexível (`td.row-actions`) | Botões num contêiner dentro de uma célula de tabela de verdade |
| 4 | Conhecimento: coluna de ações fora da área visível ("Nova ve…"), só acessível rolando a tabela de lado | Colunas de texto e ações quebram linha; teste garante que nenhuma tabela rola de lado no desktop |
| 5 | Arquivos do painel (HTML/JS/CSS) sem `Cache-Control`: após uma atualização, o navegador podia combinar `app.js` antigo com `index.html` novo e o painel quebrava | `Cache-Control: no-cache` em `/panel` (revalidação por ETag); API continua `no-store` |
| 6 | Gaveta do menu (tablet/celular) sem fundo e sem fechar ao tocar fora; foco não ia para o menu | Fundo escurecido que fecha a gaveta e devolve o foco ao botão; foco no item atual ao abrir |
| 7 | Toast no canto inferior direito cobria o botão **Enviar** no desktop | Desktop: canto inferior esquerdo da área de trabalho; tablet/celular: abaixo da barra superior |
| 8 | Rótulo "Aguardando atendimento" quebrava em duas linhas no menu | Menu 268 px e rótulos com reticências |
| 9 | (achado pela regressão, introduzido no próprio redesign) Botão longo da etapa de código ("Perdi o celular…") alargava a tela no celular (417 px > 390 px) | Botões de largura total e botões no celular quebram linha |

Revisados sem defeito novo: busca de conversas (limite de 80 caracteres e `LIKE` com escape no
servidor), cabeçalhos de segurança, CSRF/sessões/RBAC/2FA (cobertos pela regressão), worker e
transporte simulados.

## D. Testes

| | Baseline | Final |
|---|---|---|
| pytest | 964 | **969** (+5 `tests/test_phase16.py`) |
| Chromium | 39 | **44** (+5 `tests_e2e/test_phase16_browser.py`) |
| Roteiro da demonstração | 13/13 | **13/13** |

Novos: cache dos arquivos do painel; todo ID usado pelo script existe uma única vez na página;
CSS sem recurso externo e com os dois temas; script sem injeção de HTML; identificação da versão;
um único resumo de avisos ao entrar; tabelas sem rolagem lateral e linha da Equipe alinhada;
contraste AA + acessibilidade + sem rolagem horizontal nas telas da demonstração (claro e escuro);
gaveta do menu com fundo e foco. Nenhum teste existente foi enfraquecido.

Avaliações offline (critérios inalterados): regras/fake 123/129, segurança OK, **Q2 reprovado** (recall 0,902) — igual ao baseline; hostile 129/129; gemini-simulated (prompt v3) 123/129. Nenhuma chamada externa (executadas sem `GEMINI_API_KEY` no ambiente).

## E. Comparação visual

Capturas antes/depois (fora do repositório, não versionadas) nos três modos. Resumo:
login sóbrio com marca; menu agrupado com ícones; indicadores com hierarquia; filtros numa linha;
lista com avatares e marcadores de urgência; tabelas completas; Sistema em grade com travas
visíveis; tema escuro consistente.

## F. Identificação

`0.14.0rc1.post2` — mesma RC1 Offline com o redesign e as correções acima. Manifesto regenerado
com `ops congelar-rc`; impressão digital e contagem de arquivos registradas em
`RELATORIO_PRONTIDAO_RC1.md` §1 (o manifesto não pode citar a si mesmo).

## G. Pendências

Fase 11B com pessoas reais; dados oficiais; 14 decisões do salão; homologações Gemini e Meta
(autorizadas); infraestrutura. Celular físico e leitor de tela reais não testados (só emulação e
verificação automatizada). Sem modo de tema manual (segue o sistema operacional).
