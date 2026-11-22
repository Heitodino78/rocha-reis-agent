# Guia de implantação em servidor Linux (Fases 17 e 18)

**Situação:** procedimento **preparado e ensaiado localmente** numa raiz descartável em volume cifrado.
**Nenhum servidor foi contratado, nada foi instalado no sistema, nenhuma porta foi aberta, nenhum DNS ou
certificado público foi pedido.** Meta e Gemini continuam desligados (travas `False`); dados só sintéticos.

**Fase 18 (18A):** as unidades systemd REAIS rodaram no systemd do usuário (sem root), e Caddy, WireGuard
(kernel) e nftables REAIS rodaram num laboratório de rede isolado em namespaces. Isso achou e corrigiu
defeitos que a verificação estática não pegava (ver `STATUS_FASE_18.md` §F). **Não é a instalação no
servidor definitivo (18B), que continua pendente** — roteiro em §7.

## 1. Arquitetura escolhida

Um servidor Linux pequeno, três processos e um proxy. Sem Kubernetes, sem microsserviços: um salão não
gera carga que os justifique (medição em `STATUS_FASE_17.md` §F).

```
Internet ──HTTPS──► Caddy (443 público) ──► só /webhooks/whatsapp ──► webhook  127.0.0.1:8080  ─┐
                                                                                                ├─► SQLite (WAL) em volume LUKS
Equipe ──WireGuard──► 10.8.0.1 ──HTTPS──► Caddy (443 só na VPN) ──► painel 127.0.0.1:8000 ─────┤   /var/lib/rocha-reis-agent/dados
                                                                                                │
                                         worker (sem porta; systemd Type=notify + watchdog) ────┘──► Meta/Gemini só após homologação
backup diário (timer) ─► backups cifrados locais ─► cópia externa (destino a contratar; hoje SIMULADO)
alertas (timer 5 min) ─► journal (falha de unidade aciona rocha-reis-alerta@)
```

- O **webhook** é um aplicativo separado, sem nenhuma rota do painel: mesmo um caminho que escapasse do
  Caddy cairia num app sem administração (testado com `../`, `%2e%2e`, `//`, maiúsculas, `/docs`).
- O **painel** só escuta em 127.0.0.1; o Caddy o publica **somente** no IP da VPN. Segundo fator,
  cookie `Secure` e origem HTTPS são obrigatórios (a instalação recusa subir sem eles).
- `X-Forwarded-For`: o Caddy o reescreve com o IP real da conexão e as aplicações só o aceitam de
  127.0.0.1. **Corrigido nesta fase:** a referência anterior confiava em faixas privadas — como os
  clientes da VPN têm IP privado, um funcionário poderia forjar o cabeçalho e escapar do limitador de
  tentativas de login.

## 2. Árvore no servidor

| Caminho | Conteúdo | Permissão / dono |
|---|---|---|
| `/opt/rocha-reis-agent/releases/<versão>-<impressão>/` | Código de cada versão + `.venv` | somente leitura; root |
| `/opt/rocha-reis-agent/current` | Link para a versão ativa (troca atômica) | root |
| `/etc/rocha-reis-agent/perfil.json` | Configuração e segredos da instância | 0600; `rocha-reis` |
| `/etc/rocha-reis-agent/chaves/` | Chave do backup e do 2º fator (fora dos dados e dos backups) | 0700; `rocha-reis` |
| `/var/lib/rocha-reis-agent/dados/` | Banco SQLite + `-wal`/`-shm` | 0700; volume **LUKS** |
| `/var/lib/rocha-reis-agent/backups/` | Backups cifrados locais | 0700 |
| `/var/lib/rocha-reis-agent/estado/` | Batimento do worker, histórico de implantações, versões | 0700 |
| `/var/log/rocha-reis-agent/` | Log técnico privado (rotação 5 MB × 5) | 0700 |

Os serviços leem a configuração do perfil por `ROCHA_REIS_PROFILE=/etc/rocha-reis-agent` — nenhum
segredo na linha de comando, no ambiente da unidade ou no repositório.

## 3. Pré-requisitos do servidor (a contratar — não feito)

| Item | Mínimo justificável | Com margem |
|---|---|---|
| Sistema | Debian 12/13 ou Ubuntu 24.04 LTS, só pacotes oficiais | Igual |
| CPU | 1 vCPU | 2 vCPU |
| RAM | 1 GB (3 processos ≈ 210 MB medidos + SO) | 2 GB |
| Disco | 20 GB SSD, **com LUKS** no volume dos dados | 40 GB SSD |
| Python | 3.13 (`requirements.lock` com hashes). **Não vem no Ubuntu 24.04/Debian 12** (trazem 3.12/3.11): binário portátil verificado ou PPA — decisão I-5; ver §4 passo 2 | Igual |
| Rede | IPv4 público; portas 443/tcp e 51820/udp; SSH só do IP da agência | + IPv6 |
| Backup externo | Armazenamento de objetos em **outro provedor/região** | + versionamento/retenção imutável |

Justificativa (medição local, `STATUS_FASE_17.md` §F): ~1 000 conversas, 4 operadores simultâneos,
mensagens chegando e backup rodando ao mesmo tempo → painel p95 ≈ 86 ms, ~75 MB por processo, banco de
~6 MB (~3 KB por mensagem). **Quando crescer:** CPU média > 50% por semanas, p95 do painel > 500 ms,
banco > 60% do `storage_max_bytes` (512 MB na instância) ou conversas ativas perto de 1 000 (alerta
`capacidade_esgotada`). Preços: **a cotar** — nenhum valor presumido.

## 4. Instalação (no servidor, como root; NÃO executado)

```sh
# ORDEM (Fase 18B-0): §8 passos 1–3 (contratar, criar o servidor, preparar e MONTAR o volume) → este §4 →
# §8 passos 4–10. Todos os comandos abaixo rodam a partir de /opt/rocha-reis-agent/current depois do passo 2.
# 0) Volume LUKS DEDICADO já montado em /var/lib/rocha-reis-agent (§8 passo 3; sem fstab/crypttab) e
#    usuário de serviço sem shell:
useradd --system --home /var/lib/rocha-reis-agent --shell /usr/sbin/nologin rocha-reis
# 1) Pacote gerado e conferido na máquina da agência (reprodutível: mesmo conteúdo → mesmo SHA-256):
# (na agência, a partir da raiz do repositório, com o venv do README; "python" sozinho não existe no Ubuntu)
/tmp/rocha-reis-agent-venv/bin/python -m rocha_reis_agent.ops empacotar --destino ./pacotes   # anote o sha256 impresso
# Copiar o pacote para /root/ no servidor (scp pela VPN/IP autorizado) e conferir LÁ:
sha256sum /root/rocha-reis-agent-<versão>-<impressão>.tar.gz        # deve bater com o sha256 anotado
# 2) Instalar e ativar a 1ª versão. Revisão (22/11, simulado literalmente): o servidor limpo NÃO tem o código
#    nem as bibliotecas do instalador; "python3.13 -m rocha_reis_agent.ops instalar" direto falha
#    (ModuleNotFoundError). Sequência que funciona:
# 2a) Python 3.13 em /opt/python3.13 (decisão I-5: binário portátil com soma conferida, como na Fase 18A,
#     ou PPA). Exemplo com o portátil já conferido: tar -xzf cpython-3.13.*.tar.gz -C /opt && mv /opt/python /opt/python3.13
PY313=/opt/python3.13/bin/python3.13
# 2b) Extrair o pacote CONFERIDO e criar um ambiente TEMPORÁRIO só para rodar o instalador:
install -d -m 700 /root/rr-instalacao
tar -xzf /root/rocha-reis-agent-<versão>-<impressão>.tar.gz -C /root/rr-instalacao   # conteúdo na raiz (sem subpasta)
$PY313 -m venv /root/rr-instalacao/.venv-instalador
/root/rr-instalacao/.venv-instalador/bin/pip install --require-hashes --no-deps -r /root/rr-instalacao/requirements.lock
# 2c) Instalar (cria o .venv da versão com pip --require-hashes) e ativar a 1ª versão:
cd /root/rr-instalacao && .venv-instalador/bin/python -m rocha_reis_agent.ops instalar \
    --pacote /root/rocha-reis-agent-<versão>-<impressão>.tar.gz --raiz / --python $PY313
#    (sem internet no servidor: --pip-arg=--no-index --pip-arg=--find-links=/caminho/rodas, também no 2b)
cd / && rm -rf /root/rr-instalacao   # o ambiente temporário não é mais necessário (sair da pasta antes)
# 2d) Marcar o volume (uma única vez; nunca sobrescreve) — exige o código instalado no passo 2c:
/opt/rocha-reis-agent/current/.venv/bin/python -m rocha_reis_agent.ops marcar-volume --ponto-montagem /var/lib/rocha-reis-agent
# 3) Instância de PRÉ-PRODUÇÃO SINTÉTICA (recusa volume sem cifragem verificada e, desde a 18B-0, qualquer
#    coisa que não seja o volume dedicado MARCADO; grava UUID LUKS + id da marca no perfil, em /etc):
/opt/rocha-reis-agent/current/.venv/bin/python -m rocha_reis_agent.ops preparar-servidor --raiz / \
    --origem https://painel.<domínio-interno>
chown -R rocha-reis: /etc/rocha-reis-agent /var/lib/rocha-reis-agent /var/log/rocha-reis-agent
# 4) Conferir (o mesmo comando roda em ExecStartPre de cada serviço):
sudo -u rocha-reis /opt/rocha-reis-agent/current/.venv/bin/python -m rocha_reis_agent.ops validar-instalacao --raiz /
# 5) Unidades, proxy, firewall e VPN (revisar marcadores <...> antes):
cd /opt/rocha-reis-agent/current
# Só .service/.timer/.target: a montagem precisa do nome escapado (com outro nome o systemd a recusa).
cp deploy/systemd/*.service deploy/systemd/*.timer deploy/systemd/*.target /etc/systemd/system/
install -m 644 deploy/systemd/rocha-reis-dados.mount '/etc/systemd/system/var-lib-rocha\x2dreis\x2dagent.mount'
systemctl daemon-reload && systemd-analyze verify /etc/systemd/system/rocha-reis-* '/etc/systemd/system/var-lib-rocha\x2dreis\x2dagent.mount'
install -m 750 deploy/scripts/rocha-reis-desbloquear deploy/scripts/rocha-reis-bloquear /usr/local/sbin/
# Fase 18B-0: o atendimento NÃO sobe no boot (o volume fica bloqueado); só os alertas são habilitados.
systemctl enable rocha-reis-webhook rocha-reis-panel rocha-reis-worker rocha-reis-volume rocha-reis-backup.timer
systemctl enable --now rocha-reis-alertas.timer        # avisa "volume_aguardando_desbloqueio" após reinício
systemctl start rocha-reis.target                        # (ou rocha-reis-desbloquear após cada reinício)
nft -c -f deploy/nftables/rocha-reis.nft.exemplo   # só verifica; aplicar depois de revisar
# Fase 18: o Caddy depende do IP da VPN (bind 10.8.0.1). Ordem + sysctl para a VPN não derrubar o webhook:
install -D -m 644 deploy/caddy/caddy.service.d-10-rocha-reis.conf /etc/systemd/system/caddy.service.d/10-rocha-reis.conf
install -m 644 deploy/sysctl/90-rocha-reis.conf /etc/sysctl.d/90-rocha-reis.conf && sysctl --system
caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile   # validado na Fase 18 (Caddy 2.11.9)
```

**Atenção (Fase 18):**
- Sem o IP da VPN o Caddy **inteiro** não sobe — inclusive o webhook público. Por isso o drop-in põe o
  Caddy depois de `wg-quick@wg0` (com `Wants=`, não `Requires=`) e `net.ipv4.ip_nonlocal_bind=1` deixa o
  Caddy subir mesmo se a VPN falhar. Ambos testados no laboratório (`caddy_antes_da_vpn`).
- O firewall recusa o IP da VPN vindo de qualquer interface que não seja `wg0`. Sem essa regra, um
  vizinho na rede do provedor roteava 10.8.0.1 pela interface pública e abria o painel **sem VPN**
  (reproduzido no laboratório e corrigido).
- O instalador **não** altera mais as permissões do Python do sistema (`/usr/bin/python3.13`): o `chmod`
  que deixa o código somente leitura seguia o link `.venv/bin/python` — corrigido e testado.
- Revogar acesso: `wg syncconf` (como abaixo), **nunca** `wg setconf`: este recria todos os peers e derruba
  as sessões de quem continua por ~15 s (medido com a ferramenta de teste).

`credenciais-iniciais.txt` (0600) traz a senha temporária de `admin.preprod`; troca e 2º fator são
obrigatórios no primeiro acesso. Entregar pessoalmente e apagar o arquivo.

## 5. VPN (WireGuard) — referência em `deploy/wireguard/`

**Por que WireGuard:** no kernel Linux, configuração curta, aplicativos oficiais para Android, iOS,
Windows e macOS, sem custo de licença, revogação imediata (apagar a chave pública do aparelho). A VPN
só dá acesso a `10.8.0.1/32` (o painel); não vira saída de internet do aparelho.

| Procedimento | Como |
|---|---|
| Criar acesso | No servidor: `wg genkey`/`wg pubkey`/`wg genpsk` para o aparelho; bloco `[Peer]` com IP `/32` próprio (planilha de IPs ↔ pessoa/aparelho); entregar `aparelho.conf` por QR **na tela, pessoalmente**; apagar o arquivo. |
| Revogar | Apagar o `[Peer]` e `wg syncconf wg0 <(wg-quick strip wg0)`; desativar também o login no painel (Equipe → Desativar). |
| Aparelho perdido | Revogar a chave do aparelho + redefinir o 2º fator da pessoa no painel. |
| Recuperação do servidor | `wg0.conf` (com a chave privada do servidor) guardado cifrado junto das chaves de backup; sem ele, gerar nova chave e reenviar configurações a todos. |
| Riscos | Chave do aparelho copiada (mitigado por 2º fator + senha no painel); aparelhos sem bloqueio de tela; esquecer de revogar quem saiu (checklist de desligamento). |

O 2º fator do painel continua obrigatório **mesmo com a VPN**.

## 6. Testes que precisam ser repetidos NO servidor definitivo

Ensaiados na Fase 18 fora do servidor (ver matriz em `STATUS_FASE_18.md` §B); **repetir lá**:

1. `validar-instalacao` e `systemd-analyze security` (exposição ≤ 2) **com o usuário `rocha-reis`** e o
   systemd do SISTEMA (na 18A: systemd do usuário, sem `User=`; exposição medida 1,8).
2. Subida real das unidades com o endurecimento completo — feito na 18A no systemd do usuário
   (notify, watchdog, SIGTERM, kill -9, limite de reinícios, OnFailure, ExecStartPre, timers, montagens
   somente leitura, seccomp, sem capabilities). Falta: usuário dedicado, `ProtectHome=true` literal,
   journald do sistema, `systemd-coredump` (conferir que `LimitCORE=0` impede o core).
3. `caddy validate`, certificado público (ACME) para o webhook, varredura de portas **externa**: só
   443/tcp e 51820/udp; `/panel`, `/api`, `/health`, `/ready` → 404 pelo endereço público.
4. Webhook real da Meta (homologação 6B, autorizada à parte) — inclusive a rajada de entregas contra o
   limite do nftables (60/min por IP, rajada 20).
5. `ops ensaio-operacional` e `ops carga` no próprio servidor; teste de restauração a partir do destino
   externo **real**.
6. Reinício da máquina inteira (`reboot`) com conferência de `/ready`, filas, VPN e Caddy (ordem de
   subida). **Não testado em nenhuma fase.**
7. Caddy subindo **antes** da VPN (com `ip_nonlocal_bind=1`) e o painel passando a responder quando o
   `wg0` aparece — a primeira parte foi testada no laboratório; a segunda não.

## 7. Roteiro da Fase 18B (servidor real — NÃO executado; exige autorização específica)

Pré-condições (nenhuma existe hoje): servidor contratado e autorizado por escrito; acesso SSH por chave
da agência; volume de dados LUKS montado em `/var/lib/rocha-reis-agent`; domínio para o webhook; destino
externo de backup; lista de aparelhos da VPN. **Antes de mexer no servidor, apresentar o plano de
alterações ao responsável** (o que será instalado, portas abertas, como desfazer).

| # | Ação no servidor | Aceite |
|---|---|---|
| 1 | §4 passos 0–4 (usuário, pacote conferido por SHA-256, `instalar`, `preparar-servidor`, `validar-instalacao` como `rocha-reis`) | `ok: true`, travas `False` |
| 2 | Unidades + drop-in do Caddy + sysctl + nftables (`nft -c` antes de `nft -f`) | `systemd-analyze verify` sem erro; exposição ≤ 2 |
| 3 | WireGuard com chaves geradas NO servidor; um aparelho de teste | Handshake; painel só pela VPN; 2º fator |
| 4 | Repetir no servidor o roteiro de `tests_infra/fase18_systemd.py` (subida, SIGTERM, kill -9, watchdog, limite de reinícios, ExecStartPre, backup/alertas, atualização/reversão) | Mesmos resultados da 18A, agora com `User=rocha-reis` |
| 5 | Varredura externa de portas e caminhos (de fora da rede do provedor) | Só 443/tcp e 51820/udp; tudo fora do webhook = 404 |
| 6 | `ops ensaio-operacional` e `ops carga` em diretório descartável | Sem duplicidade; integridade ok |
| 7 | Backup → destino externo real → restauração numa instância nova | Contagens iguais; 2º fator funciona |
| 8 | `reboot` | Tudo pronto sem intervenção; `/ready` 200; filas vazias |

18B só é concluída com todos os aceites no próprio servidor. VMs, contêineres e namespaces locais **não**
contam (ver `STATUS_FASE_18.md`).

## 8. Fase 18B-0 — procedimento completo do volume, do backup externo e da recuperação (NÃO executado)

Preparado e **testado em laboratório** (`tests_infra/fase18b0_volume.py`, disco LUKS2 de loop + systemd do
usuário; `tests/test_phase18b0_*.py`). **Nada disto foi executado num servidor.** Cada etapa exige a
autorização da 18B.

1. **Contratar** (pessoa habilitada, após aprovação de `ESPECIFICACAO_CONTRATACAO_SERVIDOR.md`): servidor
   e volume em bloco na mesma região; armazenamento externo em OUTRO provedor; 2FA nas contas.
2. **Criar o servidor**: Ubuntu 24.04 LTS oficial, chave SSH, firewall do provedor só 443/tcp e 51820/udp
   (+22/tcp do IP da agência), volume de 40 GB anexado **sem** formatar pelo painel.
3. **Preparar o volume** (uma vez, como root):
   ```sh
   cryptsetup luksFormat --type luks2 /dev/disk/by-id/<volume>        # frase DIGITADA pelo responsável
   cryptsetup luksAddKey /dev/disk/by-id/<volume>                     # 2ª frase (recuperação, envelope lacrado)
   cryptsetup luksHeaderBackup /dev/disk/by-id/<volume> --header-backup-file cabecalho-luks.img  # levar p/ fora
   cryptsetup open --type luks2 /dev/disk/by-id/<volume> rocha-reis-dados
   mkfs.ext4 -L rocha-reis-dados /dev/mapper/rocha-reis-dados
   install -d -m 755 /var/lib/rocha-reis-agent
   # Só nesta primeira vez a montagem é manual (as unidades ainda não existem); depois, sempre pela unidade.
   mount -o nodev,nosuid,noexec /dev/mapper/rocha-reis-dados /var/lib/rocha-reis-agent
   blkid -s UUID -o value /dev/disk/by-id/<volume>      # UUID LUKS: anotar no registro da instalação
   ```
   Em seguida: §4 completo (instalar → **marcar o volume** no passo 2d → instância → unidades) e depois os
   passos 4–10 abaixo.
   **Nunca** colocar o volume no `/etc/crypttab` nem no `/etc/fstab` (com chave no disco a cifragem não
   protege nada; sem chave o boot ficaria esperando senha no console).
4. **Desbloquear após cada reinício**: `rocha-reis-desbloquear /dev/disk/by-uuid/<UUID-LUKS>` (SSH pela
   VPN ou pelo IP autorizado). O script recusa pasta com conteúdo sem volume montado, confere a identidade
   como `rocha-reis`, fecha o LUKS se o volume for outro e só então inicia `rocha-reis.target`.
5. **Iniciar os serviços**: `systemctl start rocha-reis.target`. Sem o volume, os serviços falham por
   **dependência** (sem ciclo de reinício, sem banco novo no disco de sistema); se o volume sair com tudo
   rodando, `BindsTo=` para os serviços.
6. **Armazenamento externo**: bucket com **Object Lock em COMPLIANCE** (Backblaze B2) ou com **regra de
   bloqueio do bucket** (Cloudflare R2), chave **restrita ao bucket** sem permissão de apagar; credencial em
   `/etc/rocha-reis-agent/chaves/armazenamento-externo.json` (0600); seção `backup.externo` no perfil.
   **O envio real exige abrir a trava `EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED`** (revisão de código +
   autorização escrita) — hoje o adaptador só fala com 127.0.0.1.
7. **Testar o backup**: `ops backup-agendado`, `ops backup-externo --perfil ... --s3`, conferir
   `estado-copia-externa.json` (enviados, bytes, sem falhas) e tentar apagar um objeto com a credencial do
   servidor (deve ser recusado).
8. **Restaurar numa instância nova**: servidor novo → passos 2–3 com volume novo → `ops baixar-externo
   --perfil ... --s3 --nome <arquivo> --para <pasta>` → `ops verificar` → `ops restaurar` (arquivo novo) →
   conferir contagens → `promover` só com confirmação.
9. **Reiniciar o servidor**: `reboot` → o atendimento fica parado, o alerta `volume_aguardando_desbloqueio`
   aparece no journal a cada 5 min → passo 4.
10. **Verificar a prontidão**: `ops estado-servidor --raiz /` com todas as camadas `ok` (saída 0); `UNKNOWN`
    listados para conferência humana — nunca reenviados automaticamente. Desde a revisão de 22/11 a saída
    traz também `travas` com as **cinco** (Meta Graph, homologação Meta, Gemini, dados reais, backup externo),
    todas `false` até autorização escrita; elas não mudam o código de saída.
