# Relatório de implementação — Fase 0

**Data:** 19/11/2026 (America/Sao_Paulo). **Escopo:** fundação local apenas.

> Registro histórico da entrega da Fase 0. A continuação foi autorizada depois;
> o estado atual do simulador está em [STATUS_FASE_1.md](STATUS_FASE_1.md).

## 1. Produto compreendido

O Rocha Reis Agent será um assistente conversacional em português para o Salão
de Beleza Rocha Reis, desenvolvido pela agência de Heitor e Renan. Deverá usar
dados aprovados, manter contexto limitado, reconhecer pedidos de humano e
suspender respostas enquanto necessário. O canal futuro será a API oficial da
Meta. Agendamento será orientado exclusivamente pelo link oficial Booksy, sem
consulta de disponibilidade, criação ou alteração de reservas.

Nesta entrega não existe atendimento conversacional. A fundação permite iniciar
e testar uma API HTTP local sem dados do salão ou credenciais.

## 2. Diagnóstico e plano executado

Foram lidos integralmente `AGENTS.md`, os cinco documentos de referência,
`README.md` e `PROMPT_INICIAL_CODEX.md`, além da missão anexada. A pasta continha
somente esses oito documentos, sem código, dependências ou `.git`.

- Python disponível: **3.13.16**, `/usr/bin/python3`; pip inicial: **26.2.1**.
- Nenhuma das dependências do projeto estava instalada no Python inspecionado.
- A skill `.agents/skills/rocha-reis-development/SKILL.md` não existe. A busca
  pelos diretórios locais de skills e `/tmp` não encontrou cópia.
- A execução isolada do terminal falhou por restrição de namespaces do sistema;
  os comandos foram executados com a revisão de permissões da ferramenta.
- Criar `.venv` no volume do projeto falhou em `lib64 -> lib` com
  `Operation not permitted`. O diretório incompleto criado nesta execução foi
  removido; o ambiente funcional está em `/tmp/rocha-reis-agent-venv`.
- Os documentos são coerentes quanto ao Booksy por link e às fases. A diferença
  entre a árvore prevista no kit e os arquivos presentes (skill ausente), além
  da falta de Git, foi registrada sem inventar instruções ou dados do salão.

O plano comunicado antes da implementação foi criar pacote Python, API de
saúde, configuração tipada, contratos declarativos mínimos, lock de dependências,
testes herméticos, instruções e evidências. Os critérios foram inicialização,
HTTP 200, falha segura, ausência de integrações e reprodução em ambiente limpo.

## 3. Arquitetura e justificativas

`main.create_app` compõe a configuração e os roteadores. `api/` contém somente
HTTP e tratamento de erros; `config.py` valida ambiente; `domain/` não depende de
FastAPI e declara apenas `InboundMessage` e `ConversationState`.

O pacote fica na raiz para executar sem empacotamento ou instalação editável.
Não há necessidade de framework adicional, contêiner, fila, banco ou SDK de
provedor para validar saúde. A árvore completa está no README.

Nas fases futuras, o atendimento será isolado da API; interfaces para IA,
conhecimento, conversas e gateways serão introduzidas com seus primeiros usos.
As declarações de estados ainda não efetuam handoff. A mensagem declarativa
também não normaliza ou valida entradas externas. Esses comportamentos pertencem
à Fase 1 e posteriores, conforme o plano.

Decisões registradas em `ARQUITETURA_E_DECISOES.md`, ADR-006 a ADR-011.

## 4. Arquivos criados e modificados

Criados:

- `.gitignore`, `.env.example`, `requirements.in`, `requirements.lock` e
  `pyproject.toml` (configuração do pytest).
- `rocha_reis_agent/__init__.py`, `config.py` e `main.py`.
- `rocha_reis_agent/api/__init__.py`, `health.py` e `errors.py`.
- `rocha_reis_agent/domain/__init__.py` e `contracts.py`.
- `tests/conftest.py`, `test_config.py` e `test_health.py`.
- `docs/STATUS_FASE_0.md` (este relatório).

Modificados: `README.md`, o status de `CONTEXTO_MESTRE.md`, o registro de decisões
em `ARQUITETURA_E_DECISOES.md` e a evidência da Fase 0 em `PLANO_DE_EXECUCAO.md`.
`AGENTS.md`, requisitos, dados pendentes e prompt inicial foram preservados.
Nenhuma alteração foi commitada: esta pasta não possui repositório Git.

## 5. Dependências efetivamente utilizadas

| Direta | Versão travada | Finalidade |
|---|---|---|
| FastAPI | 0.165.0 | Aplicação e roteamento HTTP |
| Pydantic | 2.14.4 | Validação de configuração e resposta tipada |
| Pydantic Settings | 2.16.3 | Variáveis de ambiente e `.env` |
| Uvicorn | 0.54.0 | Servidor ASGI local |
| pytest | 9.2.0 | Testes |
| httpx2 | 2.16.2 | Transporte em memória do TestClient |

O lock contém **24 pacotes**, incluindo transitivos; Starlette foi instalado na
versão **1.10.0**. A API OpenTelemetry é dependência transitiva do FastAPI; não
foi configurado SDK, exportador ou envio de telemetria. Não há dependência de
Meta, Booksy, provedor de IA ou banco.

O lock foi gerado com **pip-tools 7.7.0**, em venv separado, com resolução
backtracking e hashes. A ferramenta de manutenção não faz parte da aplicação.
Runtime e testes compartilham o lock porque esta entrega é exclusivamente local.

Referências primárias consultadas para decisões de dependências:
[Pydantic Settings](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/),
[TestClient do Starlette](https://starlette.dev/testclient/) e
[hashes no pip-tools](https://github.com/pypa/pip-tools#using-hashes).

## 6. Comandos e resultados observados

Comandos de inspeção: `pwd`, `rg --files --hidden`, `cat` dos documentos,
`python3 --version`, `command -v python3`, `python3 -m pip --version`,
inspeção de pacotes por `importlib.metadata`, `df -h . /tmp`, `git status --short`.
Este último retornou `fatal: not a git repository`; não é falha da aplicação.

Tentativa de ambiente no volume: `python3 -m venv .venv` falhou por links
simbólicos, conforme diagnóstico. A instalação funcional foi:

```sh
python3 -m venv /tmp/rocha-reis-agent-venv
python3 -m venv /tmp/rocha-reis-lock-tools
/tmp/rocha-reis-lock-tools/bin/python -m pip install --disable-pip-version-check --index-url https://pypi.org/simple pip-tools
/tmp/rocha-reis-lock-tools/bin/python -m piptools compile --quiet --resolver=backtracking --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --index-url https://pypi.org/simple --output-file requirements.lock requirements.in
/tmp/rocha-reis-agent-venv/bin/python -m pip install --disable-pip-version-check --index-url https://pypi.org/simple --require-hashes -r requirements.lock
/tmp/rocha-reis-agent-venv/bin/python -m pip check
/tmp/rocha-reis-agent-venv/bin/python -m pytest -q
```

Resultados finais no ambiente de trabalho: **`No broken requirements found.`**
e **`14 passed in 0.77s`**. A primeira coleta havia falhado porque `httpx`
estava depreciado no Starlette atual e avisos são tratados como erros. A solução
foi usar o transporte suportado `httpx2`, regenerar o lock e repetir os testes.
Nenhum aviso foi silenciado para obter aprovação.
Ao final, os pacotes `httpx`, `httpcore` e `certifi` instalados na tentativa
anterior foram removidos do venv de trabalho com `python -m pip uninstall -y
httpx httpcore certifi`. Um novo `pip check` não encontrou conflitos. O segundo
ambiente limpo já havia sido instalado somente a partir do lock final.

Validação em servidor real:

```sh
/tmp/rocha-reis-agent-venv/bin/python -m uvicorn rocha_reis_agent.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
curl --noproxy '*' --fail-with-body -i --max-time 5 http://127.0.0.1:8000/health
```

Resultado: **`HTTP/1.1 200 OK`**, `content-type: application/json`, corpo
**`{"status":"ok"}`**. O Uvicorn confirmou inicialização e, após `Ctrl+C`,
encerramento completo com código 0. Nenhum servidor desta entrega ficou ativo.
Uma inspeção por `create_app().openapi()['paths']` confirmou apenas `/health`.
A inspeção anterior de atributos internos de routers foi substituída por essa
API pública após mudança de estrutura na versão instalada.

Validação de configuração inválida pelo executável real:

```sh
APP_ENV=SYNTHETIC_INVALID_ENV /tmp/rocha-reis-agent-venv/bin/python -m uvicorn rocha_reis_agent.main:create_app --factory --host 127.0.0.1 --port 8001 --no-access-log
```

Resultado esperado e observado: **código 1**, falha antes de iniciar o servidor,
`ConfigurationError` sem o valor sintético. O CLI exibe traceback técnico de
inicialização; a exceção original de validação e os valores de entrada são
suprimidos. Erros em requisições são tratados separadamente pelo middleware.

## 7. Reprodução em ambiente limpo

Foi criada a pasta **`/tmp/rocha-reis-phase0-repro-y3ei0spd`** com uma cópia
apenas das fontes, testes, configurações de exemplo e documentos. Não foram
copiados `.env`, caches ou pacotes instalados. Nela foi criado um segundo venv,
sem herdar pacotes do sistema.

O procedimento foi executado por script Python via entrada padrão, usando `tempfile`,
`shutil`, `venv.EnvBuilder(with_pip=True)` e `subprocess.run`. Os comandos
executados nesse segundo ambiente foram:

```sh
# Executar a partir da cópia limpa em /tmp/rocha-reis-phase0-repro-y3ei0spd/source
/tmp/rocha-reis-phase0-repro-y3ei0spd/venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check --index-url https://pypi.org/simple --require-hashes -r requirements.lock
/tmp/rocha-reis-phase0-repro-y3ei0spd/venv/bin/python -m pip check
/tmp/rocha-reis-phase0-repro-y3ei0spd/venv/bin/python -m pytest -q
```

Evidência: **`INSTALL_EXIT=0`**, **`No broken requirements found.`**,
**`14 passed in 0.13s`**, **`REPRODUCIBILITY=PASS`**.
O log temporário da instalação ficou em `install.log` na pasta de reprodução.
Essa evidência cobre Python 3.13.16/Linux deste host; não é uma matriz de outras
versões, sistemas operacionais ou instalações sem conectividade ao PyPI.

## 8. Testes e critérios de aceite

| Verificação | Evidência | Resultado |
|---|---|---|
| Inicialização local sem credenciais | Uvicorn em loopback e testes com configuração padrão | Aprovado |
| `GET /health` HTTP 200 | TestClient e curl no servidor real | Aprovado |
| Suíte pytest | 14 testes, incluindo casos parametrizados | Aprovado |
| Configuração | Padrões, `.env`, precedência, exemplo, valores inválidos, chave desconhecida e UTF-8 inválido | Aprovado |
| Erros HTTP | 404/405 preservados; 500 genérico sem marcador privado no corpo ou log | Aprovado |
| Ausência de APIs externas da aplicação | Sem clientes/credenciais; conexões e resolução DNS bloqueadas nas fixtures de testes | Aprovado no escopo local |
| Reprodução limpa | Segundo venv e cópia limpa, instalação com hashes e sem cache | Aprovado |
| Arquivos sem credenciais | 25 arquivos de código/configuração/documentação inspecionados; nenhum `.env` local ou padrão conhecido de chave encontrado | Aprovado na inspeção local |
| Exclusões do `.gitignore` | 10 caminhos conferidos por `git check-ignore` em repositório temporário; `.env.example` permanece incluível | Aprovado |
| Sintaxe e documentação | AST dos arquivos Python e destinos dos links Markdown relativos verificados | Aprovado |

A inspeção complementar usou um script Python via entrada padrão com `ast`,
`pathlib`, expressões regulares para padrões conhecidos de credenciais e um
repositório Git temporário para conferir exclusões. Resultados:
`SECRETS_SCAN=PASS`, `PYTHON_SYNTAX=PASS`, `DOCUMENT_LINKS=PASS` e
`GITIGNORE=PASS`. Não foi criado `.git` no projeto. A busca por padrões de
credenciais é uma verificação pontual, não uma auditoria completa de segurança.

Os acessos externos desta tarefa foram consulta de documentação técnica e
download de dependências no PyPI. A aplicação não chamou APIs de Meta, IA,
Booksy ou outros serviços do produto; não houve envio de mensagens.

## 9. Limitações, pendências e riscos

- A skill local mencionada no kit precisa ser fornecida/restaurada pelo
  responsável, se desejada. Não foi substituída por uma skill inventada.
- A pasta precisa ser versionada; não há histórico Git, commit ou revisão de PR.
- O ambiente em `/tmp` é descartável. Código e lock permanecem no projeto e
  permitem reconstruí-lo; validar novamente ao mudar Python ou plataforma.
- A aplicação não implementa simulador, FAQ, classificação, memória, validação de
  mensagens recebidas, deduplicação, transições de estado, IA, persistência,
  webhook, envio WhatsApp ou transferência humana operacional.
- Testes de duplicatas, falhas de provedores, isolamento de conversas e handoff
  serão exigidos quando esses fluxos existirem; não foram substituídos por testes
  fictícios nesta fase.
- O middleware cobre falhas antes do início da resposta HTTP. Streaming,
  tarefas de fundo, métricas, correlação e operação contínua não estão validados.
- Não há prontidão comercial, teste de carga, implantação, auditoria completa
  de segurança/dependências ou aprovação do salão. O `/health` é apenas liveness.
- Link Booksy, catálogo, tom, profissionais, políticas, inbox, orçamento,
  retenção e autorizações continuam em `DADOS_PENDENTES.md`, sem suposições.

## 10. Estado objetivo

| Categoria | Estado |
|---|---|
| Planejado | Produto conversacional e fases 1 a 6 |
| Implementado | Fundação HTTP/configuração, contratos declarativos e testes da Fase 0 |
| Testado localmente | 14 testes aprovados em dois ambientes; HTTP real em loopback; instalação limpa |
| Validado com usuário | Pendente de revisão desta entrega; dados/respostas ainda não validados pelo salão |
| Implantado | Não; servidor local de verificação encerrado |

Os critérios técnicos locais da **Fase 0 foram atendidos**. Isso não significa
que o agente de atendimento esteja pronto. A próxima ação depende de pedido do
usuário para executar exclusivamente a Fase 1.

## 11. Como executar

As instruções exatas de criação do ambiente, instalação com hashes, configuração,
execução e teste estão no [`README.md`](../README.md). Nenhum segredo é exigido;
criar `.env` é opcional. Execute sempre a partir da raiz do projeto.

## 12. Prompt completo recomendado para a Fase 1

```text
Execute exclusivamente a Fase 1 — Simulador de conversas do Rocha Reis Agent.

Antes de editar, leia integralmente AGENTS.md, docs/CONTEXTO_MESTRE.md,
docs/REQUISITOS_E_ACEITE.md, docs/ARQUITETURA_E_DECISOES.md,
docs/PLANO_DE_EXECUCAO.md, docs/DADOS_PENDENTES.md, docs/STATUS_FASE_0.md
e README.md. Localize e use .agents/skills/rocha-reis-development/SKILL.md
se disponível; se não existir, registre a ausência sem inventar instruções.
Inspecione a fundação e execute os testes existentes antes de alterar código.
Apresente um plano curto com arquivos afetados e critérios de aceite.

Implemente somente um simulador local e determinístico: validação/normalização
de mensagem de entrada e resultado de saída, identificador de conversa,
roteador inicial, FAQ com fixtures explicitamente sintéticas, intenção de
agendamento por link configurado, ausência de informação e pedido de humano.
Use e evolua os contratos existentes com justificativa. Isole atendimento,
conhecimento, canal e estado por interfaces mínimas que tenham uso real.
Ofereça um comando simples para teste manual local e documente sua execução.

Use repositórios em memória. Implemente estados BOT_ACTIVE, PENDING_HUMAN
e HUMAN_ACTIVE, impedindo respostas normais nos dois estados humanos.
Permita assumir/liberar a conversa somente por ação administrativa local
explícita do simulador; mensagens de clientes não podem retomar o bot.
Deixe claro que a simulação de handoff não entrega conversa a um atendente real.
Implemente deduplicação em memória por evento/conversa, sem banco ou garantia
de persistência após reinício. Não acrescente IA real nem antecipação da Fase 2.

Booksy: somente link configurado; URLs sintéticas de teste devem usar domínio
reservado, como example.invalid, e aparecer como simulação. Nunca consultar
disponibilidade, criar/remarcar/cancelar reservas, fazer scraping ou automatizar
painéis. Com link ausente/inválido, explicar a indisponibilidade e oferecer
ajuda humana sem fabricar URL ou afirmar que a transferência aconteceu.
Nunca apresentar fixtures como dados reais, inventar preços/horários ou
afirmar que houve confirmação de reserva.

Escreva e execute testes para FAQ sintética, dado ausente, link de agendamento,
link ausente/inválido, ausência de confirmação de reserva, pedido de humano,
pausa/retomada autorizada, evento duplicado, isolamento entre conversas,
entrada inválida e tentativa do cliente de alterar regras internas.
Preserve GET /health, as configurações seguras e os testes da Fase 0.
Use apenas dados sintéticos e execução local sem APIs externas. Não integrar
WhatsApp, IA paga, Booksy, banco de produção ou inbox; não implantar nem enviar
mensagens externas. Não solicitar credenciais reais.

Documente decisões e evidências reais. Ao concluir, informe arquivos alterados,
comandos e resultados dos testes, limites, riscos e pendências, diferenciando
planejado, implementado, testado localmente, validado com usuário e implantado.
Apresente a próxima etapa, sem executá-la automaticamente.
```
