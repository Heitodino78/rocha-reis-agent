# Relatório técnico — Fase 13 (segurança avançada e preparação final de implantação)

Executada localmente em 20/11/2026. Dados fictícios, `FakeAIProvider`, `FakeWhatsAppTransport`.
**Nenhuma chamada externa, nenhum serviço público, nenhum DNS ou certificado público, nenhum
commit ou push.** Fase 11B continua pendente.

| Situação | Estado |
|---|---|
| PLANEJADO | Instalação real de proxy, timer, VPN e backup externo (referências em `deploy/`) |
| IMPLEMENTADO | Segundo fator, rotação de sessão, HSTS/proxies, HTTPS local, backup automático, teste de restauração, ensaio de desastre, rotação de chave, endurecimento da pré-produção |
| TESTADO LOCALMENTE | **950 pytest + 34 Chromium** aprovados (baseline 917 + 30) |
| VALIDADO PELO SALÃO | **Não** |
| HOMOLOGADO EXTERNAMENTE | **Não** |
| IMPLANTADO | **Não** |

## 1–3. Estado inicial, diagnóstico e testes iniciais

- `main` em `c59b934`; Fases 10, 11A e 12 não commitadas (54 caminhos). Política de autoria do
  histórico: todos os commits terminam com `Co-Authored-By: Claude Opus 5.5 …`.
- Travas confirmadas no código (`config.py`): `GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED`,
  `HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED`, `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED`,
  `REAL_DATA_ACTIVATION_AUTHORIZED` — todas `False`; validação da configuração recusa transporte
  real, Gemini real e `DATA_CLASSIFICATION=real`.
- Baseline: **917 pytest e 30 Chromium** aprovados.
- Diagnóstico de autenticação: login já rotacionava a sessão (fixação), logout/desativação já
  revogavam, CSRF por HMAC + Origin, cookies HttpOnly/SameSite=Strict. Lacunas: **sem segundo
  fator**; troca de senha **mantinha o mesmo token**; **sem HSTS**; o iniciador herdava do uvicorn
  a confiança em `X-Forwarded-For` vindo de 127.0.0.1 mesmo sem proxy; backup só manual;
  nenhum ensaio de recuperação.

## 4. Arquivos

**Criados:** `panel/mfa.py`, `persistence/migrations/011_panel_mfa.sql`, `ops/backup_job.py`,
`ops/disaster.py`, `ops/tls.py`, `deploy/README.md`, `deploy/caddy/Caddyfile.exemplo`,
`deploy/systemd/rocha-reis-panel.service`, `deploy/systemd/rocha-reis-backup.{service,timer}`,
`tests/test_phase13_mfa.py`, `tests/test_phase13_ops.py`, `tests_e2e/test_phase13_browser.py`,
`docs/STATUS_FASE_13.md`, `docs/SEGURANCA_AUTENTICACAO.md`, `docs/OPERACAO_BACKUP_AUTOMATICO.md`,
`docs/PLANO_RECUPERACAO_DESASTRES.md`.

**Modificados:** `config.py`, `persistence/sqlite.py` (esquema 11), `panel/{api.py, app.py,
store.py}`, `panel/static/{index.html, app.js, styles.css}`, `ops/{__main__.py, demo.py,
environments.py, launcher.py}`, `.env.example`, `.gitignore`, `tests/panel_support.py` (1ª etapa
do login sem CSRF), documentação listada no fim.

## 5. Segundo fator

TOTP (RFC 6238) pela `cryptography`; cadastro com reconfirmação da senha, chave mostrada uma vez,
ativação só com código válido, 10 códigos de recuperação de uso único; login em duas etapas em que a
1ª **não cria sessão**; ±30 s de tolerância; reutilização bloqueada; etapa de 5 min/5 tentativas;
limitador por conta/IP compartilhado com o login; desativação própria (senha + código) onde não é
obrigatório; redefinição administrativa com justificativa, confirmação `REDEFINIR:<login>`,
administrador com segundo fator, auditoria e encerramento das sessões; política obrigatória por
ambiente (pré-produção sem exceção). Detalhes: `SEGURANCA_AUTENTICACAO.md` §1.

## 6. Proteção dos segredos

Segredo TOTP cifrado (AES-256-GCM, AAD = usuário) com chave 0600 fora do banco e dos backups
(configuração recusa chave junto do banco; app não inicia com chave ausente ou frouxa; chave
errada → 503 seguro). Testado: segredo e códigos ausentes do arquivo do banco/WAL e dos logs;
rotação (`ops mfa-rotacionar`) recifra tudo numa transação e guarda a chave antiga à parte.
Tabela completa de segredos e rotação: `SEGURANCA_AUTENTICACAO.md` §4.

## 7. Sessões

Rotação na troca de senha e na ativação do segundo fator; revogação em redefinição/desativação;
SSE encerrado após logout/revogação (testado) e reaberto após rotação.

## 8. HTTPS

Caddy escolhido (referência `deploy/caddy/Caddyfile.exemplo`: só o webhook público; painel no IP
da VPN; SSE sem buffer). HSTS com origem HTTPS. `PANEL_TRUSTED_PROXIES` explícito. **TLS real
testado localmente** com certificado de desenvolvimento: cliente sem confiança é recusado; com
ele, cookie `__Host-`/Secure/HttpOnly/SameSite=Strict, HSTS e SSE funcionam. Caddy não executado
(não instalado nesta máquina).

## 9–11. Backup automático, restauração e ensaio de recuperação

`ops backup-agendado` (lock, ambiente, chave, cópia consistente, cifragem, verificação, destino
secundário **simulado no mesmo disco**, retenção, estado sem segredos, saída de erro);
`ops teste-restauracao` (isolado, sem sobra em claro); indicadores no painel (Sistema) e em
`ops ambiente`. Ensaio `ops ensaio-recuperacao`: banco "perdido" recuperado da cópia secundária
em ambiente novo, com contagens, filas (UNKNOWN/FAILED), funcionários, auditoria e segredos do
segundo fator conferidos — ≈ 0,44 s local, **não é RTO**. `OPERACAO_BACKUP_AUTOMATICO.md` e
`PLANO_RECUPERACAO_DESASTRES.md`.

## 12. Segurança (auditoria)

Rotas novas: todas autenticadas no backend, CSRF/Origin nas que alteram estado, RBAC (redefinição
só ADMIN, nunca a si mesmo), sem IDOR (redefinição por id confere existência e papel). Contorno do
segundo fator testado por HTTP e pelo navegador (rotas, SSE, cookie da etapa). XSS: telas novas só
com `textContent`. Logs sem segredo, código ou chave (testado). Arquivos 0600/0700. Pré-produção
recusa 2FA desligado, HTTP, cookie sem Secure e inatividade > 30 min.

## 13–14. Testes

- pytest: **950** aprovados (novos: `test_phase13_mfa.py` 13, `test_phase13_ops.py` 20).
- Chromium: **34** aprovados (novos: `test_phase13_browser.py` — cadastro e login em duas
  etapas no desktop e no celular, código errado, código de recuperação de uso único, política
  obrigatória, etapa intermediária sem acesso, redefinição pela tela Equipe, acessibilidade e
  contraste nas telas novas).

## 15–16. Problemas encontrados e corrigidos

1. Troca de senha mantinha o token da sessão → agora gera token novo.
2. Sem HSTS com HTTPS → adicionado.
3. Iniciador confiava em `X-Forwarded-For` de 127.0.0.1 sem haver proxy → só com proxy declarado.
4. **Ativar o segundo fator deslogava a pessoa** (o stream ao vivo usava a sessão antiga, que a
   rotação revogou) — achado pelo teste de navegador; o painel agora reabre o stream e confere a
   sessão antes de deslogar.
5. Botão "Ativar" duplicado durante o cadastro (confuso) → ações escondidas durante o passo a passo.
6. **Corrida na nova verificação do stream** (achada pela regressão completa, investigada e
   reproduzida): a checagem atrasada de sessão podia deslogar uma sessão nova feita nesse meio
   tempo → cada sessão aplicada tem um número; checagens antigas são ignoradas (3 repetições
   seguidas estáveis + suíte completa).
7. Minha própria unidade systemd de referência usava `--no-proxy-headers`: atrás do Caddy local
   todos pareceriam o mesmo IP e o limitador bloquearia a equipe → confiança só no proxy local.

## 17–18. Limitações e pendências

Sem QR code e sem WebAuthn; redefinição depende de conferência humana de identidade; risco de
cadastro do segundo fator por quem tem só a senha de conta ainda não cadastrada (mitigado por
senha temporária); destino secundário no mesmo disco; timer, proxy, VPN e certificados reais não
instalados; sem leitor de tela; RPO/RTO não acordados; custódia das chaves por duas pessoas não
definida.

## 19–20. Travas e Git

Quatro travas `False`, sem alteração. Patch de ativação do Gemini continua aplicável
(`git apply --check`). Git: `main` em `c59b934`, Fases 10–13 sem commit.

**Proposta de commits** (não executados; mesmo padrão de autoria do histórico):
1. `feat: Fase 10 — consolidação operacional offline`
2. `feat: Fase 11A — finalização funcional e preparação da validação`
3. `feat: Fase 12 — pré-produção sintética, dados estruturados e proteção do armazenamento`
4. `feat: Fase 13 — segundo fator, sessões, HTTPS de referência, backup automático e recuperação`

Os arquivos da Fase 13 são os listados no item 4 (as demais alterações pertencem às fases
anteriores). Como as fases foram desenvolvidas na mesma árvore, separar 10–12 exige `git add -p`
cuidadoso; alternativa honesta: um único commit "Fases 10 a 13".

## 21–23. Requisitos e próxima fase

- **Pré-produção real:** servidor com disco cifrado verificado, Caddy com certificado público só
  para o webhook, painel na VPN, timer de backup instalado com destino externo, chaves com duas
  pessoas, ensaio de recuperação lá.
- **Produção:** os oito requisitos de `SEGURANCA_ARMAZENAMENTO.md` §6 (2FA agora implementado),
  homologações Meta (6B) e Gemini (9C-B), 11B, dados oficiais, decisões jurídicas e abertura
  revisada da trava de dados reais.
- **Próxima fase recomendada:** executar a 11B com a equipe (agora incluindo o cadastro da
  verificação em duas etapas) e receber o formulário de dados; em paralelo, cotação da hospedagem.
  Nenhuma API real antes disso.
