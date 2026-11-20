# Relatório técnico — Fase 10 (consolidação operacional, offline)

**Executada localmente em 20/11/2026, somente com dados fictícios.** Transporte WhatsApp
**simulado** (`FakeWhatsAppTransport`), IA **simulada** (`FakeAIProvider`), travas Meta e
Gemini **fechadas** e inalteradas. Nenhuma chamada externa, nenhuma mensagem real, nenhum
commit ou push.

| Situação | Estado |
|---|---|
| PLANEJADO | Sim (este documento) |
| IMPLEMENTADO | Sim: painel, avisos, backup/restauração, política de contatos excluídos, demonstração, iniciador, medição |
| TESTADO LOCALMENTE | Sim: 837 pytest + 19 Chromium (desktop e celular) — ver §F |
| VALIDADO PELO SALÃO | **Não** |
| HOMOLOGADO EXTERNAMENTE | **Não** (Meta e Gemini continuam bloqueados) |
| IMPLANTADO | **Não** |

## A. Painel e experiência de uso

**Organização.** Menu: Visão geral · Conversas · Aguardando atendimento · Meus
atendimentos · Automáticas · Com outros funcionários (ADMIN) · Encerradas · Equipe ·
Auditoria · Conhecimento · Configurações · **Sistema** (novo, ADMIN).

**Visão geral.** Cartões clicáveis calculados do banco (aguardando sem responsável,
comigo, em atendimento, automáticas, encerradas e, para ADMIN, envios incertos/com
falha/na fila) e a lista **Precisa de atenção** (interrupção de envios, UNKNOWN, FAILED,
espera mais antiga com marca de URGENTE, entradas não processadas, tentativas de contatos
excluídos). Nada é estimado.

**Conversas.** Etiqueta **Urgente · N min** e tempo de espera, alerta de envio com
falha/incerto na lista, responsável sempre visível, estado da automação, mensagens não
lidas, **histórico paginado** ("Carregar mensagens anteriores", páginas antigas preservadas
nas atualizações ao vivo), motivo da falha traduzido junto da situação do envio, faixa de
alerta na conversa para envio **incerto** ("não reenvie às cegas") ou **falho**.

**Composer.** Rascunho **por conversa** (antes o texto de uma conversa aparecia em outra),
guardado em memória e `sessionStorage` da aba; **sobrevive à expiração da sessão** (após
novo login a mesma conversa reabre com o rascunho); descartado ao **Sair**.

**Estados.** Carregando / falha com "Tentar de novo" na lista; conexão ao vivo/reconectando;
toasts nunca bloqueiam cliques (`pointer-events: none`). Faixa **DEMONSTRAÇÃO** quando o
perfil define `PANEL_ENVIRONMENT_LABEL`.

**Responsivo.** Painel de avisos em tela cheia no celular; sem rolagem horizontal
(verificado por teste em 390 px).

**Situações de envio honestas** (inalteradas): na fila / enviando / aceita pela plataforma
/ entregue / lida / falhou / resultado incerto / não enviada.

## A2. Avisos internos (somente no painel)

`GET /api/panel/notifications` calcula os avisos **atuais a partir do estado do banco**
(não de uma fila de eventos). Cada aviso tem **chave estável** por ocorrência; o navegador
só guarda as chaves já mostradas/lidas (`localStorage`, por usuário). Consequências
testadas: recarregar a página ou reconectar o stream **não repete** o toast; um novo pedido
depois de finalizado gera **nova** chave.

| Tipo | Quem recebe | Obrigatório |
|---|---|---|
| `human_requested` | quem vê a fila | não |
| `assigned_to_me` (inclui transferência) | o novo responsável (não quem se atribuiu) | não |
| `new_message` | o responsável, só mensagens processadas **depois** da atribuição e não lidas | não |
| `send_failed` / `send_unknown` | ADMIN e o atendente da conversa/envio | **sim** |
| `dispatch_halted` | todos | **sim** |
| `inbound_failed` | ADMIN | **sim** |
| `retired_contact` | ADMIN | não |

Nenhum aviso contém texto de mensagem ou telefone (teste dedicado). Som opcional gerado no
navegador (WebAudio), desligado por padrão e só após gesto do usuário. Atualização: ao
conectar o stream, a cada mudança, ao voltar à aba e a cada 30 s (interrupção e falha de
entrada não passam pelo feed de mudanças). **Sem e-mail, push, SMS, WhatsApp ou terceiros.**

## A3. Configurações (ADMIN, validadas no backend, auditadas)

Migração **009** (`panel_settings` tipado, preserva valores; índices
`transitions_conversation_current` e `messages_conversation_role`):

| Chave | Tipo/faixa | Padrão |
|---|---|---|
| `operators_can_transfer` / `operators_can_resume` | booleano estrito | sim / não |
| `urgent_after_minutes` | inteiro estrito 1–1440 | 15 (valor sintético) |
| `retired_contact_policy` | `block` \| `allow_new_conversation` | `block` |

Texto `"15"`, `true` como número, chaves desconhecidas ou de integração (`ai_provider`,
`whatsapp_transport`, travas) → **422**. A tela **Sistema** mostra as travas reais como
**somente leitura**.

## B. Backup e restauração

`python -m rocha_reis_agent.ops …` (`rocha_reis_agent/ops/backup.py`):

1. **backup** — API online do SQLite lendo a origem em modo somente leitura (consistente
   com WAL e com o aplicativo aberto; transação não confirmada não entra) →
   `integrity_check` + `foreign_key_check` + aplicação/propósito do banco → contagens por
   tabela → **AES-256-GCM** (`cryptography` 50.0.2, já no lock; agora dependência direta)
   com o manifesto em claro como **dado associado** → escrita `.partial` + fsync + rename
   (0600 em diretório 0700).
2. **Chave**: arquivo de 32 bytes (`ops chave`, 0600, nunca sobrescrito) **fora do
   diretório de backups** (recusado se estiver dentro) ou frase-senha (scrypt n=2^15, r=8,
   p=1; mínimo 12 caracteres; lida de variável de ambiente ou digitada; nunca impressa).
3. **verificar** — decifra num diretório temporário privado, confere SHA-256, integridade
   e contagens do manifesto. Versão de formato ou de esquema incompatível é recusada
   **antes** de decifrar.
4. **restaurar** — somente para um caminho **novo** (`.synthetic.sqlite3`); destino
   existente é recusado; falha no meio não deixa arquivo final nem `.partial`.
5. **promover** — substitui o conteúdo do banco ativo pelo restaurado apenas com a frase
   `SUBSTITUIR:<nome do arquivo>`, com lock exclusivo (falha se o painel/worker estiver
   aberto) e **cópia anterior** 0600 no mesmo diretório (essa cópia **não** é cifrada, como
   o próprio banco ativo).
6. **listar** — lê manifestos sem a chave (sem conteúdo nem segredo).

**Os backups são cifrados; o banco ativo não é** (continua dependendo de volume cifrado —
pendência do `CHECKLIST_PRODUCAO.md`). Não há agendamento, cópia fora da máquina nem
retenção aprovada.

## C. Recuperação de falhas, retenção e exclusão

- UNKNOWN continua **sem reenvio automático** (testado também na demonstração e no backup:
  o estado incerto é preservado na restauração).
- **Contato com dados excluídos que volta a escrever** — antes: inbox `FAILED
  rejected_by_core`, conteúdo novo **guardado** e nenhuma visibilidade (bloqueio silencioso).
  Agora, sem escolher a política pelo controlador:
  - `block` (padrão): inbox `DONE` com payload **redigido**, desfecho
    `retired_contact_blocked`, nenhuma resposta, aviso ao ADMIN e contagem em Sistema;
  - `allow_new_conversation`: a conversa recomeça **vazia** (nova sessão, nada do
    excluído volta; chaves de idempotência mantidas), auditada com origem `client`;
  - **reativação individual** pelo ADMIN com `REATIVAR:<código>` (negada/sucesso
    auditados).
- Retenção (`/purge`) inalterada: anonimiza sem bloquear.
- Nenhum prazo legal foi definido ou presumido.

## D. Demonstração isolada e iniciador local

`sh iniciar.sh` (o volume do repositório não preserva o bit de execução) ou
`python -m rocha_reis_agent.ops demo|iniciar`:

- Perfil em `~/.local/share/rocha-reis-agent/demo` (0700), **fora do repositório** e sem
  tocar em nenhum banco existente; `--recriar` só apaga diretório com o marcador de demo.
- Segredos do canal **sintéticos** gerados no perfil (`perfil.json`, 0600); senhas dos
  usuários fictícios em `credenciais-demo.txt` (0600), **nunca impressas**.
- Roteiro executado pelo caminho real (webhook assinado → núcleo → outbox fake): 3 usuários,
  conhecimento sintético aprovado, 8 clientes (automático, fila, atendimento com nova
  mensagem, transferência com **falha** de envio, envio **incerto**, finalizado, automação
  retomada, contato **excluído** que volta a escrever), backup cifrado e **restauração
  isolada** idêntica ao original.
- Iniciador: verifica Python, dependências, permissões do perfil, travas fechadas,
  transporte/IA simulados, integridade/migração do banco e porta livre; escuta **somente
  em 127.0.0.1**; Ctrl+C encerra com desligamento gracioso (worker embutido parado).

## E. Auditoria de segurança (escopo desta fase)

| Item | Verificação |
|---|---|
| Autenticação/sessões | Rotas novas exigem sessão; `/notifications` não renova inatividade (`touch=False`); expiração leva ao login sem perder rascunho |
| CSRF/Origin | Todas as rotas que alteram estado (inclui `PUT /settings` e `POST …/reinstate`) recusam sem token ou com Origin estranha — teste dedicado |
| RBAC/IDOR | `/system`, `/retired-contacts`, reativação: só ADMIN; `/messages?before=` aplica a visibilidade; filtro `others` vazio para atendente; avisos de envio restritos ao escopo |
| XSS | Interface nova só com `textContent`; teste de XSS anterior continua passando |
| Logs | Nenhum log novo com texto ou telefone; caminho de excluídos só registra o desfecho |
| Arquivos | Backups/perfil/credenciais/chave 0600 em diretórios 0700; `.gitignore` cobre `*.rrbackup`, `*.partial`, credenciais de demo, chaves e bancos |
| Integrações | Interface **não** liga Meta/Gemini; iniciador recusa trava aberta; nenhuma trava foi alterada |
| Criptografia | Biblioteca consolidada; nenhuma primitiva própria |

**Limites conhecidos:** sem HTTPS/2FA (uso só em 127.0.0.1); banco ativo sem cifragem;
`localStorage` guarda apenas chaves de avisos e preferências (sem conteúdo); a promoção
não registra quem a executou fora do sistema operacional.

## F. Testes e medições (resultados reais)

Comandos:

```sh
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q -p no:cacheprovider
/tmp/rocha-reis-e2e-venv/bin/python -m pytest -q -p no:cacheprovider tests_e2e
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.ops desempenho --conversas 1000 --mensagens 400
```

- Linha de base antes da fase: **798 passed** (pytest) e **10 passed** (Chromium).
- Depois: **837 passed** (pytest) e **19 passed** (Chromium); dois testes antigos ajustados de propósito: `test_explicit_deletion_remains_operator_decision` (novo desfecho visível, ADR-110) e o fluxo do operador no navegador (aceita o 404 esperado quando a conversa sai do escopo da atendente — intermitente sob carga). Corrigido também um roubo de foco assíncrono encontrado por teste intermitente.
- Novos: `tests/test_phase10.py` (painel: configurações, avisos, urgência, filtros,
  paginação, excluídos, sistema, CSRF, migração 009), `tests/test_phase10_backup.py`
  (backup válido, cifrado, frase-senha, chave errada, corrompido/adulterado ×4, versões
  incompatíveis ×2, arquivos ausentes e regras da chave, destino existente, falha no meio
  da restauração e do backup, consistência com escrita concorrente, promoção protegida,
  listagem, CLI, demonstração, iniciador) e `tests_e2e/test_phase10_browser.py` (avisos ao
  vivo sem conteúdo e sem duplicata após recarga, falha obrigatória, urgência e
  configurações, histórico sob demanda, expiração de sessão com rascunho, Sistema e
  reativação, celular, acessibilidade básica em todas as telas).
- **Acessibilidade básica automatizada** (sem biblioteca externa): nome acessível em
  botões, rótulo em campos visíveis, ids únicos, idioma `pt-BR`, título e ausência de
  rolagem horizontal — desktop (ADMIN e atendente, todas as telas) e celular. Não substitui
  avaliação com leitor de tela nem com pessoas.

**Medição de desempenho** (esta máquina: Linux, 8 CPUs, Python 3.13.16; volume gerado pelo
caminho real: 1000 conversas, 2398 mensagens, uma conversa com 400 mensagens, 1199 envios,
banco de 5,35 MB; 30 repetições):

| Consulta | Mediana | p95 |
|---|---|---|
| Lista "todas" (ADMIN, 25 itens) | 11,4 ms | 11,8 ms |
| Lista "aguardando" (atendente) | 4,1 ms | 4,6 ms |
| Busca por texto (ADMIN) | 11,7 ms | 12,7 ms |
| Visão geral (ADMIN) | 3,7 ms | 4,3 ms |
| Avisos (ADMIN / atendente) | 2,6 / 2,1 ms | 3,3 / 2,6 ms |
| Página recente do histórico (50) | 1,5 ms | 1,8 ms |
| Página anterior do histórico | 1,4 ms | 2,0 ms |
| Histórico completo (método antigo, 400 msgs) | 9,8 ms | 9,9 ms |
| Feed de mudanças | 1,6 ms | 2,1 ms |
| Backup cifrado (criar / verificar) | 92 ms / 42 ms | — |

São medições locais com dados sintéticos, **não** garantias para produção (outra máquina,
outro volume ou concorrência real mudam os números). O teto de 1000 conversas ativas do
protótipo continua valendo (`capacity_exhausted` acima disso).

## Pendências e próximos passos (não iniciados)

1. Sessão de validação do painel com a equipe do salão (manual `MANUAL_PAINEL.md`).
2. Decisões do controlador: contato excluído, retenção, tempo de urgência, quem restaura,
   guarda da chave e frequência dos backups.
3. Infraestrutura de produção: HTTPS, 2FA, gerenciador de segredos, volume cifrado,
   backup agendado fora da máquina, supervisão do worker.
4. Avisos fora do painel, se a equipe precisar (exige decisão e provedor autorizado).
5. Homologações Meta (6B) e Gemini (9C-B) continuam bloqueadas até autorização expressa.
