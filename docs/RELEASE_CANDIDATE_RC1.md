# Rocha Reis Agent — RC1 Offline

**Versão candidata à homologação interna e aos testes com o salão. NÃO é versão de produção.**
Funciona só nesta máquina (127.0.0.1), com dados fictícios, IA e WhatsApp simulados.

> **Atualização (Fase 18B-0):** a candidata em uso é **`0.14.0rc1.post6`**, impressão **`47b17956c8132cfe`**
> (333 arquivos): Fase 18 + volume LUKS identificado, portão systemd e adaptador S3 desativado
> (`STATUS_FASE_18B_0.md`), congelada no manifesto `docs/homologacao/RC1_MANIFESTO.json`. Confira com
> `python -m rocha_reis_agent.ops verificar-rc`. Diferenças e resultados em
> `RELATORIO_PRONTIDAO_RC1.md`. Histórico: `0.14.0rc1.post1` (Fase 15, impressão `92597a901d64a153`); `0.14.0rc1.post2` (Fase 16, `b67636df59849361`); `0.14.0rc1.post3` (revisão final, `ed50508486b8437c`); `0.14.0rc1.post4` (Fase 17, `7fa848b6eeec4bb2`); `0.14.0rc1.post5` (Fase 18, `7823a5ac7f45ce34`).
> A tabela abaixo é o registro original da Fase 14.

## 1. Identificação (registrada em 20/11/2026)

| Campo | Valor |
|---|---|
| Nome / versão | Rocha Reis Agent — RC1 Offline / `0.14.0rc1` (`rocha_reis_agent/__init__.py`) |
| Commit base | `c59b934` (`main`) |
| Estado do Git | **Não versionada**: 74 caminhos sem commit (34 modificados, 40 novos — Fases 10 a 14) |
| Impressão digital do conteúdo | `74f98ad6a343bc68` (273 arquivos do projeto; calculada por `python -m rocha_reis_agent.ops versao`, que exclui este documento) |
| Python | 3.13.16 |
| Esquema do banco | 11 (migrações 001–011) |

Enquanto não houver commit autorizado, a RC1 é identificada pela **impressão digital**: rodar
`ops versao` e comparar. Qualquer edição em arquivo do projeto muda o valor.

### Dependências relevantes (instaladas a partir do `requirements.lock` com hashes)

fastapi 0.165.0 · uvicorn 0.54.0 · pydantic 2.14.4 · pydantic-settings 2.16.3 · argon2-cffi 25.1.0 ·
cryptography 50.0.2 · google-genai 3.6.1 (**bloqueado**) · httpx2 2.16.2. Testes de navegador:
playwright 1.65.2 (`requirements-e2e.txt`, fora do runtime).

### Migrações (SHA-256, 12 primeiros caracteres)

001 `d941cffab30e` · 002 `2c524de9abbb` · 003 `c01662049d8f` · 004 `6910d37211af` ·
005 `110b7f33655f` · 006 `e91feb136238` · 007 `12beec029eea` · 008 `503727c6e339` ·
009 `5a2452e86c98` · 010 `af76bc8c4ad7` · 011 `0710acda2892`

### Travas (todas `False`, só alteráveis por mudança de código revisada)

`GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED`, `HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED`,
`GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED`, `REAL_DATA_ACTIVATION_AUTHORIZED`.

## 2. Configuração

Perfil de demonstração em `~/.local/share/rocha-reis-agent/demo` (0700; `perfil.json` 0600 com
segredos **sintéticos** gerados na criação; chaves em `chaves/`; backups em `backups/` e
`backups-secundario/` simulado). Painel em `http://127.0.0.1:8000/panel/`, transporte fake, IA
fake, conhecimento no banco (base **sintética**), segundo fator **opcional** (chave própria).
Pré-produção sintética (`ops preprod`) usa HTTPS local e segundo fator obrigatório.

## 3. Como iniciar, testar e encerrar

```sh
sh iniciar.sh --nova-demo                              # demonstração limpa (apaga só a demonstração)
sh iniciar.sh --mensagem "Quero falar com uma pessoa"  # cliente fictício (segundo terminal)
python -m rocha_reis_agent.ops roteiro                 # 13 jornadas automáticas (perfil novo, isolado)
python -m rocha_reis_agent.ops versao                  # conferir a identificação acima
# Ctrl+C encerra com segurança
```

Requisito: ambiente Python do projeto (`.venv` ou `ROCHA_REIS_PYTHON`), instalado com
`pip install --require-hashes -r requirements.lock`. O volume do repositório não guarda permissão
de execução: usar `sh iniciar.sh`.

## 4. Recursos disponíveis na RC1

Atendimento automático por regras com conhecimento aprovado (preço fixo, "a partir de", faixa,
"mediante avaliação", duração estimada, categorias, profissionais); Booksy só por link; fila
humana, assumir, responder, transferir, finalizar, retomar (administração); avisos; histórico;
falha/incerto sem reenvio; gestão de conhecimento com aprovação, conflitos e prévia simulada;
equipe, auditoria, configurações, sistema; segundo fator com recuperação; backup cifrado,
automático, teste de restauração e ensaio de desastre; ambientes isolados.

## 5. Resultados de teste desta RC

- pytest **958** aprovados; Chromium **37** aprovados (desktop, notebook, tablet, celular).
- Roteiro guiado **13/13** OK.
- Avaliações offline: regras/fake 123/129 (segurança OK; **Q2 reprovado**, recall 0,902);
  hostile 129/129; gemini-simulated 123/129. Não medem o Gemini real.

### Limitações conhecidas

- Respostas automáticas por **regras**: pedidos humanos e reclamações **indiretos** e gírias de
  preço ainda falham (dependem do Gemini real, não homologado).
- Sem WhatsApp real; sem servidor; painel só no computador local (celular real não acessa).
- Base de conhecimento **fictícia**; dados oficiais não recebidos.
- Sem QR code no cadastro do segundo fator; acessibilidade só por verificação automatizada.
- Backup secundário no mesmo disco (simulado).
- Árvore não versionada no Git.

## 6. Proposta de versionamento (não executada)

**Recomendação: um único commit consolidado das Fases 10 a 14.** Motivo: as fases alteraram os
mesmos arquivos (`panel/api.py`, `panel/static/app.js`, `config.py`, `persistence/sqlite.py`,
`ops/*`, testes e docs) em camadas; separar por fase exigiria `git add -p` em dezenas de trechos,
com risco de commits intermediários que não passam nos testes.

**Incluir:** todos os 74 caminhos listados por `git status --short` (código, migrações 009–011,
testes, `tests_e2e`, `deploy/`, `docs/`, `iniciar.sh`, `.env.example`, `.gitignore`,
`requirements.in`, `requirements.lock`).
**Excluir (já ignorados; conferir que não aparecem):** `*.sqlite3*`, `*.rrbackup*`, `*.key`,
`*.crt`, `credenciais-*.txt`, `perfil.json`, `estado-backup.json`, `.teste-restauracao-*`,
`.pytest_cache/`.

**Verificações antes do commit:**
1. `git status --short --ignored` sem banco, chave, backup ou credencial fora dos ignorados.
2. Busca por segredos (`AIza…`, `EAA…`, e-mails pessoais) sem resultados.
3. `pytest` e `tests_e2e` completos aprovados; `ops roteiro` 13/13; `ops versao` com travas `False`.
4. `git apply --check docs/homolog/ativacao_gemini_9c.patch` aplica (patch intacto).
5. `git update-index --chmod=+x iniciar.sh` (o volume vfat não guarda o bit de execução).

**Mensagem sugerida** (mesmo padrão de autoria dos commits anteriores do repositório):

```
feat: Fases 10 a 14 — consolidação operacional, pré-produção sintética, segurança avançada e RC1 Offline

- Fase 10: avisos internos, urgência, histórico paginado, backup cifrado, demo isolada, iniciador
- Fase 11A: cenários de ponta a ponta, conflitos de conhecimento, erros claros, materiais da 11B
- Fase 12: categorias, profissionais, duração e tipos de preço; ambientes; cifragem verificada
- Fase 13: segundo fator (TOTP), rotação de sessão, HTTPS local, backup automático, recuperação
- Fase 14: RC1 Offline (0.14.0rc1), roteiro guiado, materiais finais da validação com o salão
Nenhuma integração real ativada; quatro travas em False; dados exclusivamente fictícios.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

**Publicação posterior (só com autorização):** commit → `ops versao` mostra `versionada: true` →
tag opcional `v0.14.0-rc1` → `git push origin main` (e a tag, se criada). Nenhum desses passos foi
executado.
