# Relatório técnico — Fase 18B-0 (correções pré-contratação, backup externo e verificação final)

Executada em 21/11/2026 na máquina de desenvolvimento, com dados sintéticos, IA e WhatsApp simulados.
**Nenhum serviço contratado, nenhuma conta criada, nenhuma credencial real, nenhum envio externo, nenhum
deploy, nenhum `sudo`.** Commit e push só no encerramento autorizado (§J). Quatro travas `False` e uma quinta trava nova
(`EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED`) também `False`. **A Fase 18B real não foi iniciada. 11B continua
pendente.**

| Situação | Estado |
|---|---|
| IMPLEMENTADO | identidade do volume LUKS (UUID + marca) em banco, portão, validação, atualização e restauração; unidade de montagem, portão e alvo systemd; scripts de desbloqueio/bloqueio; adaptador S3 do backup externo (desativado); estado em camadas; alertas de volume; interface de aviso externo (desligada) |
| TESTADO LOCALMENTE — unitário/integração | ver §G (pytest, Chromium, roteiro) |
| TESTADO COM SERVIÇO REAL (local) | disco **LUKS2 real** (arquivo → loop pelo udisks, sem root) + unidades reais no systemd do usuário (`tests_infra/fase18b0_volume.py`); servidor S3 **falso** em 127.0.0.1 |
| TESTADO EM SERVIDOR / PROVEDOR REAL | **Nada** |
| IMPLANTADO | **Não** |

Autorização usada (pedida e dada nesta sessão): laboratório temporário com arquivo de disco transformado
em loop LUKS2 pelo udisks (política padrão da sessão ativa, `--no-user-interaction`) e unidades no
systemd do usuário. Tudo desmontado, bloqueado e desligado no fim (conferido: nenhuma montagem, nenhum
loop do laboratório, nenhuma unidade `rocha-reis*`).

## A. Estado inicial

| Item | Valor |
|---|---|
| Commit | `2b0335e` = `origin/main` |
| Versão / manifesto | `0.14.0rc1.post5`; `verificar-rc` acusava só as 3 alterações documentais da pesquisa (preservadas) |
| Travas | 4 × `False` |

## B. Problemas encontrados

1. **Serviços subiam sem o volume.** As unidades só tinham `After=network-online.target`. Com o volume
   bloqueado, `/var/lib/rocha-reis-agent` é uma pasta comum do disco de sistema.
2. **`RequiresMountsFor=` não resolveria.** Para um caminho sem montagem declarada ele só depende das
   montagens que existem acima (`-.mount`), então o serviço subiria do mesmo jeito.
3. **Cifragem verificada não basta.** Se o disco de sistema também for LUKS, a pasta vazia no ponto de
   montagem passaria na checagem de dm-crypt e o banco seria **criado no disco errado**. A identidade
   precisa ser conferida.
4. **`instalar` e `atualizar` criavam as pastas de dados** (`prepare_layout`) mesmo sem o volume montado.
5. **Sem unidade de montagem, o `systemd-analyze verify` falha.** `BindsTo=` aponta para uma montagem que
   só existe enquanto o disco está montado (achado no laboratório, rodada 1).
6. **Checagem de "somente leitura" no lugar errado.** Sob `ProtectSystem=strict`, o ponto de montagem
   aparece somente leitura no namespace do serviço; o que conta é a pasta de `ReadWritePaths=` (achado no
   laboratório, rodada 2: todos os serviços recusavam um volume correto).
7. **Backup externo:** só existia o destino falso; `sync` quebrava sem registrar falha se o destino
   estivesse fora do ar e apagaria objetos pelo cliente, o que conflita com imutabilidade.
8. **Vultr:** o plano escolhido como Opção A **não está disponível** para novos pedidos em São Paulo.

## C. Correções implementadas

| Arquivo | O que mudou |
|---|---|
| `persistence/storage_security.py` | `check_data_volume`: ponto de montagem exato (`/proc/self/mountinfo`), LUKS (não aceita dm-crypt *plain*), UUID LUKS = perfil, marca `.rocha-reis-volume.json` = perfil, pastas no mesmo sistema de arquivos e graváveis (`statvfs`); `mark_volume` (nunca sobrescreve) |
| `persistence/sqlite.py`, `bootstrap.py`, `config.py`, `ops/demo.py` | o perfil carrega a identidade; **todo processo** confere o volume antes de criar pasta ou banco |
| `ops/deploy.py` | `preparar-servidor` exige o volume dedicado marcado (modo `--laboratorio` só fora de `/`); `validar-instalacao`, `atualizar` e `reverter --restaurar-banco` conferem o volume ANTES de tocar em qualquer coisa; `instalar` não cria pastas de dados |
| `deploy/systemd/rocha-reis-dados.mount` (novo) | montagem sem `[Install]`, `ConditionPathExists=/dev/mapper/rocha-reis-dados`: bloqueado → unidade pulada na hora |
| `deploy/systemd/rocha-reis-volume.service` (novo) | portão `verificar-volume` (oneshot), `BindsTo=` na montagem, `OnFailure=` |
| `deploy/systemd/rocha-reis.target` (novo) | atendimento agrupado; não sobe no boot |
| webhook, painel, worker, backup | `Requires=`/`After=` do portão + `BindsTo=`/`After=` da montagem; `WantedBy=rocha-reis.target` |
| alertas | **não** depende do volume (é quem avisa); caminhos de dados com `-` |
| `deploy/scripts/rocha-reis-desbloquear`, `-bloquear` (novos) | frase só digitada num terminal; confere identidade como `rocha-reis`; volume errado → fecha e sai |
| `ops/s3_store.py` (novo) | adaptador S3 (SigV4 com a biblioteca padrão), trava própria, sem sobrescrita, integridade, tentativas limitadas |
| `ops/offsite.py` | execução única (lock), falha de destino registrada, retenção pelo provedor quando o cliente não pode apagar, estatísticas |
| `ops/readiness.py` (novo), `ops estado-servidor` | camadas ligado → disco → banco → worker → webhook → painel → atendimento; `operacao_24_7` falso sem prazo acordado |
| `ops/alerts.py`, `ops/notify.py` (novo) | `volume_aguardando_desbloqueio`, `volume_incorreto`; interface de aviso externo **desligada** |
| `config.py` | trava `EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED = False` |

## D. Dependências de montagem testadas

### D.1 Unitários (`tests/test_phase18b0_volume.py`, /sys e mountinfo falsos + raiz instalada no $HOME cifrado)

Volume correto; ponto inexistente; pasta presente sem montagem (inclusive com marca copiada);
montagem sem dm-crypt; dm-crypt *plain*; LUKS errado com marca copiada; LUKS sem marca; marca de outra
instância; marca gravável por outros; link simbólico; pastas fora do volume; somente leitura (ponto e
pasta); montagens empilhadas; escape octal; `marcar-volume` único. De ponta a ponta: instância →
"bloqueio" → banco, `validar-instalacao`, `verificar-volume` (3), `alertas` (2 +
`volume_aguardando_desbloqueio`), `estado-servidor` (3), `reverter --restaurar-banco` e `atualizar`
recusam **sem criar arquivo nem pasta**; volume errado → `volume_incorreto`; volume de volta → banco só
então. Estrutura das unidades e dos scripts conferida.

### D.2 Laboratório real (`tests_infra/fase18b0_volume.py`)

Disco **LUKS2 real**: arquivo → loop pelo udisks (sem root, `--no-user-interaction`) → ext4, montado em
`/media/$USER/RRLAB`. Unidades **reais** (portão, webhook, painel, worker, backup, alvo e montagem de
referência renderizada) no systemd do usuário. Instalação pelo fluxo oficial. Rodada final 5: **11/11 OK**
(`docs/homologacao/fase18/volume-18b0-rodada5.json`, sanitizado).

| # | Cenário | Resultado observado |
|---|---|---|
| 1 | Disco LUKS2 real | dm-crypt LUKS2; UUID lido em /sys = UUID do udisks |
| 2 | Instalação exigindo volume dedicado | `marcar-volume` → `preparar-servidor` → `validar-instalacao` PRONTO; travas `False` |
| 3 | Unidades | `systemd-analyze verify` limpo; montagem carregada pelo arquivo de referência |
| 4 | Volume correto | portão `exited/success`; 6 mensagens processadas; todas as camadas `ok`; `operacao_24_7: false` |
| 5 | **Perda do volume** com tudo rodando (desmontagem forçada) | webhook, painel, worker e portão **parados** (`inactive`, `success`), 0 reinícios em 8 s; nada no ponto de montagem; alerta real `volume_aguardando_desbloqueio` (saída 2) |
| 6 | **"Reinício" com disco bloqueado** | montagem pulada (`ConditionResult=no`); serviços `inactive`, 0 reinícios, nenhuma unidade em falha, nada criado; ~20 s (antes da correção: 90 s) |
| 7 | **Disco inexistente** (loop desligado) | idem |
| 8 | **Volume errado no caminho certo** (2º LUKS2, mesma etiqueta, marca do 1º COPIADA) | portão `failed`: "volume INCORRETO: UUID LUKS diferente do esperado no perfil"; nenhum serviço ativo; `rocha-reis-alerta@` registrou a falha |
| 9 | Pasta presente sem montagem / tmpfs / outro LUKS (namespace isolado) | 3 = AGUARDANDO; 1 = RECUSADO (sem dm-crypt); 1 = RECUSADO (UUID e marca) |
| 10 | **Desbloqueio manual** → conferência → alvo | PRONTO; 10 mensagens (inclusive as 4 enviadas no momento da perda), 10 envios, **0 duplicados**, 0 UNKNOWN, `integrity_check` ok |
| 11 | Pós-recuperação | sem alerta de volume; atendimento operacional; 24/7 não declarado |

Limpeza conferida: nenhuma montagem, nenhum loop do laboratório, nenhuma unidade `rocha-reis*`; imagens
de disco apagadas.

**O laboratório achou 4 defeitos, corrigidos e retestados:**
- rodada 1: `verify` sem unidade de montagem;
- rodada 2: "somente leitura" conferido no ponto de montagem em vez da pasta de dados;
- rodada 3: o worker saía com código 1 ao perder a pasta do batimento; além disso, a ferramenta de
  teste prendia a raiz de teste nas unidades que precisam rodar sem o disco;
- rodada 4: espera de 90 s pelo dispositivo, resolvida com `JobTimeoutSec=20s`.

**Diferenças em relação ao servidor** (registradas no relatório):
- não há `User=`;
- o código e o perfil ficam dentro do disco de teste;
- portão e alertas rodam do repositório, com a identidade guardada fora do disco;
- o desbloqueio é feito pelo udisks, com arquivo de frase do laboratório;
- a condição da montagem é "o ponto está montado" (no servidor: "o dispositivo LUKS aberto existe").

**`rocha-reis-desbloquear` e `rocha-reis-bloquear` não foram executados**: exigem root.

**Regressão do roteiro da 18A** com as unidades novas: **19/19 OK**
(`docs/homologacao/fase18/systemd-rodada6-18b0.json`). Unidades de produção com a montagem instalada sob o
nome escapado: `systemd-analyze verify` sem erros de dependência (o único aviso é o Python de `/opt`
ausente nesta máquina); portão com exposição 0,6 SAFE.

## E. Adaptador S3 e testes do backup (`tests/test_phase18b0_s3.py`, 22 testes)

Servidor S3 **falso** (`tests/fake_s3.py`) em 127.0.0.1, com verificação **independente** da assinatura
SigV4. A rede fica limitada a 127.0.0.1 por fixture, e qualquer outro destino reprova o teste.

| Requisito | Teste |
|---|---|
| Assinatura correta | vetor oficial da AWS ("GET Object", `f0e8bd…bdb41`) |
| Nenhuma API real | 4 endpoints externos (B2, R2, IP privado, HTTP) recusados pela trava **antes** de conectar; também pelo perfil e pela CLI |
| Upload, download, integridade, metadados | SHA-256 nos metadados, `x-amz-content-sha256`, `Content-MD5` |
| Só backups cifrados saem | banco em claro e nome inválido recusados |
| Arquivo corrompido | download divergente detectado |
| Upload interrompido | conexão cortada no meio → nova tentativa; nenhum objeto parcial |
| Timeout / destino indisponível | 2 tentativas limitadas; conexão recusada → falha registrada no estado |
| Falha de autenticação | segredo errado e chave desconhecida → sem novas tentativas |
| Permissão insuficiente | chave só de leitura → recusa imediata |
| Duplicado / sobrescrita | repetição idempotente (1 PUT); conteúdo diferente recusado; corrida resolvida por `If-None-Match: *` (412) |
| Retenção / exclusão proibida | COMPLIANCE (estilo B2): exclusão recusada mesmo com permissão; regra do bucket (estilo R2): exclusão e sobrescrita 409; exclusão pelo cliente desligada por padrão (nenhuma requisição) |
| Listagem | paginação; objetos estranhos ignorados |
| Restauração | `sync` → `fetch` → `restore_backup` numa pasta nova |
| Credencial ausente/insegura | ausente, 0644 e `repr` mascarado |
| Reinicialização durante a transferência | processo morto com o PUT em andamento → nada parcial no destino; estado íntegro; a rodada seguinte envia uma única vez |

**B2 × R2:** os mecanismos não são equivalentes. A comparação está em `SELECAO_INFRAESTRUTURA.md` §12.3:
na B2, COMPLIANCE não pode ser encurtado por ninguém; na R2, quem administra a conta pode remover a regra
do bucket. A retenção definitiva depende de aprovação (Decisão 3).

## F. Consequências do desbloqueio manual (decisão provisória preservada)

- Após qualquer reinício, o **atendimento fica parado** até uma pessoa autorizada rodar
  `rocha-reis-desbloquear`. O servidor fica ligado, com SSH, VPN e alertas no ar.
- O aviso é `volume_aguardando_desbloqueio` (CRÍTICO, a cada 5 min, no journal). O canal para pessoas
  continua pendente (Decisão 11); a interface está pronta e desligada.
- O volume certo é conferido por UUID LUKS + marca contra o perfil em `/etc`, fora do volume. Dados
  vazios ou outro volume nunca são aceitos.
- As filas: itens interrompidos voltam pelo lease, sem duplicar envios; `UNKNOWN` só com conferência
  humana.
- **Não é 24/7.** `ops estado-servidor` informa `operacao_24_7: false` enquanto não houver
  `continuidade.prazo_desbloqueio_minutos` acordado no perfil.

## G. Testes antes e depois

| Conjunto | Antes (commit `2b0335e`) | Depois |
|---|---|---|
| pytest | 1027 | **1074** aprovados, 0 falhas (+24 volume, +22 S3, +1 batimento; 3 testes antigos ajustados ao comportamento novo, sem relaxar o que verificavam) |
| Chromium | 46 | **46** (o painel não mudou; depois dessa rodada só mudaram um teste da Fase 17 e o ensaio operacional) |
| Demonstração (`ops roteiro`) | 13/13 | **13/13** |
| systemd real (18A) | 19/19 | **19/19** (rodada 6) |
| Caddy/WireGuard/nftables (18A) | 21/21 | **21/21** (rodada 4) |
| Laboratório do volume (novo) | — | **11/11** (rodada 5; as rodadas 1–4 acharam os 4 defeitos do §D.2) |
| Avaliação offline da IA | — | não reexecutada: nada no núcleo do agente, na IA ou nos prompts mudou |
| Nenhuma API real | — | rede limitada a 127.0.0.1 nos testes S3; invariante de cliente HTTP ampliado e conferido; trava externa `False` |

**Testes antigos ajustados**:
- `test_phase17_ops` (layout): agora exige que `instalar` **não** crie as pastas de dados e que a
  preparação as crie com 0700.
- `test_phase17_ops` (cifragem): o modo laboratório é explícito, e há um caso novo para o volume dedicado.
- `test_phase18_infra`: a unidade de alertas aceita o prefixo `-`, e a fidelidade do roteiro da 18A
  passou a listar as 4 linhas de volume omitidas.
- `test_whatsapp_security`: o adaptador S3 entrou no invariante de rede como segundo cliente confinado,
  com verificação de que não é carregado sem pedido explícito.

## J. Git

Encerramento autorizado: **um único commit** sobre `2b0335e` com a pesquisa de infraestrutura e a Fase
18B-0, e push sem `--force`. Antes do commit eram 47 caminhos (31 alterados + 16 novos) e `git diff --check`
estava limpo; no encerramento entraram também a versão, o manifesto e os documentos da RC. Nenhum banco, chave, imagem de disco, backup, log, certificado ou
credencial na árvore. O único identificador com cara de chave é o **exemplo público da documentação da
AWS** (`AKIAIOSFODNN7EXAMPLE`) no teste de assinatura. As evidências foram sanitizadas (`$HOME` → `~`,
usuário → `$USER`). `verificar-rc`: idêntica ao manifesto recongelado (`post6`; impressão no manifesto).

## H. Segurança (auditoria)

- **Credenciais:** a credencial S3 fica em arquivo 0600 do próprio usuário, fora do perfil e do
  repositório, com `repr` mascarado. Nenhum segredo vai para o estado ou para as mensagens de erro (teste).
  A trava é verificada antes de ler a credencial.
- **Rede:** o adaptador S3 é o único arquivo além do adaptador da Meta que usa cliente HTTP (invariante
  ampliado e conferido). Nunca é carregado pela aplicação, pelo worker ou pela CLI (verificado em processo
  separado). Fora de 127.0.0.1, só HTTPS com verificação de certificado.
- **LUKS:** a frase nunca vai em argumento, variável ou arquivo no servidor; o volume fica fora do
  fstab/crypttab; montagem `nodev,nosuid,noexec`. O desbloqueio checa a integridade só em leitura, sem
  migração e sem cópia em claro.
- **systemd:** o endurecimento foi mantido em todas as unidades; o portão não tem rede nem escrita.
- **Backups:** só `.rrbackup` cifrado sai do servidor; nunca há sobrescrita; a exclusão pelo cliente vem
  desligada.
- **Isolamento:** o modo laboratório é proibido na raiz `/` e `--identidade` só é aceito fora de `/`
  (testado).
- **Bloqueios de dados reais:** inalterados.

## I. Pendências para contratar

Ver `SELECAO_INFRAESTRUTURA.md` §12 e `ESPECIFICACAO_CONTRATACAO_SERVIDOR.md` §3 e §5:

- **Magalu:** confirmar por escrito a imagem Ubuntu 24.04 e o console de recuperação (eliminatórios).
- **Vultr:** disponibilidade no dia e preço do volume.
- **Salão:** decisões I-1 a I-7, prazo de desbloqueio, quem guarda as chaves, canal de alerta.
- **Jurídico:** onde fica o backup.
- **Versionamento:** feito no encerramento autorizado: `0.14.0rc1.post6`, manifesto RC recongelado
  (impressão em `docs/homologacao/RC1_MANIFESTO.json` e `RELEASE_CANDIDATE_RC1.md`; `verificar-rc` idêntica).
