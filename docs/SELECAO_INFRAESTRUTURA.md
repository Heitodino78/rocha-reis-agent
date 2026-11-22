# Seleção da infraestrutura — pesquisa, viabilidade e preparação da Fase 18B

> **Situação:** pesquisa e análise (21/11/2026). **Nada foi contratado, cadastrado, configurado ou
> implantado.** Nenhuma conta foi aberta. Preços consultados nas páginas oficiais em **21/11/2026**,
> salvo indicação; podem mudar sem aviso e devem ser reconfirmados no momento da compra.
> Itens marcados **[não confirmado]** não puderam ser verificados em fonte oficial e não devem ser
> tratados como fato.
>
> Documento para envio ao responsável financeiro e aos provedores: `ESPECIFICACAO_CONTRATACAO_SERVIDOR.md`.

## Sumário da recomendação (REVISADO na Fase 18B-0 — verificação final de 21/11/2026, ver §12)

> **Mudança:** a API oficial de disponibilidade da Vultr (`/v2/regions/sao/availability`) **não lista** o
> `vc2-2c-4gb` (nem `vc2-2c-2gb` ou `vhf-2c-4gb`) para novos pedidos em São Paulo; o único plano de 2 vCPU
> disponível lá é `vhf-2c-2gb` (2 GB). O plano que era a Opção A **não pode ser contratado hoje**. A ordem
> das opções foi invertida, com uma condição eliminatória.

| | Opção A — preferida (condicionada) | Opção B — alternativa |
|---|---|---|
| Servidor | **Magalu Cloud, br-se1 (Sudeste)**, Balanced Value 2 vCPU / 4 GB / 40 GB | **Vultr, São Paulo**, `vhf-2c-2gb` (2 vCPU compartilhada, 2 GB — o mínimo aceitável) |
| Dados (LUKS) | Volume NVMe de 40 GiB anexado, LUKS2 dentro da VM | Block Storage de 40 GB anexado, LUKS2 dentro da VM |
| Backup externo | **Backblaze B2** com Object Lock COMPLIANCE (outro provedor) | **Cloudflare R2** com regra de bloqueio do bucket (outro provedor) |
| Custo fixo verificado | **R$ 126,19/mês** (impostos incluídos) | US$ 27,00/mês ≈ R$ 164,57 com IOF + volume [preço não confirmado] |
| Condição | **Eliminatória:** confirmar por escrito, antes da compra, a imagem Ubuntu 24.04 LTS em br-se1 e um console de emergência/recuperação (não documentados) | Reconfirmar a disponibilidade no momento do pedido; 2 GB exige acompanhar memória |
| Por que | Plano de 4 GB disponível no Brasil; reais com impostos, PIX/boleto; volume e snapshots documentados como persistentes após excluir a VM | Console e ISO de recuperação documentados; operação internacional madura |
| Principal risco | Plataforma mais nova; IPv4 e snapshots grátis só "no momento" | 2 GB sem folga; dólar + IOF; persistência do volume após destruir a VM não documentada |

Hetzner, a opção mais barata no papel, foi **descartada nesta rodada**: os planos de 2 vCPU aparecem como
"currently unavailable", há um aviso oficial de disponibilidade limitada desde 26/06/2026 e não há região
na América do Sul. DigitalOcean é tecnicamente adequada, mas também não tem região na América do Sul e
fica mais cara que as opções com região no Brasil, sem ganho técnico para este caso.

**Decisões que precisam ser aprovadas** (ver §11): (1) desbloqueio **manual** do volume após qualquer
reinicialização (recomendado) ou automático por servidor de chaves; (2) aceitar dados processados no
Brasil e backup cifrado no exterior, sujeito à avaliação jurídica; (3) orçamento; (4) quem guarda a
frase-senha do LUKS e a chave dos backups.

---

## 1. Dimensionamento: o que o sistema realmente usa

Medido na Fase 17 (`STATUS_FASE_17.md`, ensaio de carga): memória **~73 MB** (webhook), **~76 MB**
(painel) e **~59 MB** (worker); banco de **6 MB** para 971 conversas sintéticas. Os limites das unidades
systemd somam até 1,9 GB no pior caso (`MemoryMax=384M` × 3 + 256M alertas + 512M backup). Caddy e
WireGuard são leves.

| Recurso | Avaliação |
|---|---|
| **2 GB de RAM** | Suficiente para o uso medido (~210 MB da aplicação + sistema + Caddy), mas **sem folga** para o pior caso dos limites, a instalação de uma nova versão (criação do ambiente Python) e um backup simultâneo. Com o SDK do Gemini no futuro, a margem diminui. |
| **4 GB de RAM** | **Recomendado.** Cobre o pior caso dos limites com margem e custa pouco a mais (Vultr SP: +US$ 7,50; Magalu: +R$ 23,00). Não há evidência para recomendar mais que isso. |
| 2 vCPU compartilhada | Suficiente: a carga é baixa e intermitente (mensagens de um salão). vCPU **dedicada** custa de 3 a 4 vezes mais (Magalu DP 2/4: R$ 293,00; Hetzner CCX13: € 36,99–54,49) e não se justifica. Reavaliar só se o monitoramento mostrar CPU roubada (`steal`) alta. |
| Disco | 40 GB de sistema é amplo (cada versão instalada ocupa centenas de MB; o instalador mantém poucas). Os dados ficam num **volume separado** de 40 GB, muito acima do necessário, que permite crescer sem migrar. |

## 2. Comparação dos provedores

Fontes consultadas em 21/11/2026. Preços **recorrentes** (nenhum preço promocional foi considerado;
créditos de boas-vindas não entram no orçamento).

### 2.1 Planos e preços

| Provedor | Plano | vCPU / RAM / disco | Preço mensal | Moeda / impostos | Transferência incluída | IPv4 |
|---|---|---|---|---|---|---|
| **Vultr** (São Paulo) | `vc2-2c-4gb` Regular (**indisponível** para novo pedido em 21/11, §12.1) | 2 compart. / 4 GB / 80 | **30,00** (fora de SP: 20,00) | USD, sem impostos BR | 3072 (unidade não informada na API; provavelmente GB) | [não confirmado] se incluído |
| Vultr (São Paulo) | `vc2-2c-2gb` Regular | 2 / 2 GB / 65 | 22,50 (fora: 15,00) | USD | 3072 | idem |
| Vultr (São Paulo) | `vhf-2c-4gb` High Frequency | 2 / 4 GB / 128 | 36,00 (fora: 24,00) | USD | 3072 | idem |
| **Magalu Cloud** (br-se1) | BV 2 vCPU / 4 GB / 40 GB | 2 compart. / 4 GB / 40 | **102,99** | BRL, **impostos incluídos** | sem cobrança de saída "no momento" (ver §2.3) | **sem cobrança "no momento"**, pode mudar |
| Magalu Cloud (br-se1) | BV 2 vCPU / 2 GB / 40 GB | 2 / 2 GB / 40 | 79,99 | BRL c/ impostos | idem | idem |
| **DigitalOcean** | Basic Regular | 2 / 4 GiB / 80 GiB | 24,00 | USD | 4000 GiB | incluído |
| DigitalOcean | Basic Regular | 2 / 2 GiB / 60 GiB | 18,00 | USD | 3000 GiB | incluído |
| **Hetzner** (Alemanha/Finlândia) | CX23 Cost-Optimized | 2 / 4 GB / 40 | 5,99 | EUR, sem IVA | 20 TB (UE) | incluído ("price incl. IPv4") |
| Hetzner | CPX22 Regular | 2 / 4 GB / 80 | 16,99 (HEL1) · 19,99 (NBG1) | EUR, sem IVA | 20 TB (UE) | incluído |
| **Locaweb Cloud** | Medium | 2 / 4 GB / 80 | 80,00 + **IP público 5,00 + NAT Gateway 30,00 (obrigatórios)** = 115,00 | BRL, impostos [não informado] | "grátis" | cobrado à parte |

Estado: **todos os planos Hetzner de 2 vCPU listados aparecem como "currently unavailable"** na página
oficial. A página de status informa "Limited availability of cloud instances" desde 26/06/2026, e o FAQ
diz que as restrições são temporárias. A página oficial de reajuste de preços retornou 404. Fontes
secundárias relatam reajustes em abril e junho de 2026 [não confirmado em fonte oficial].

### 2.2 Recursos operacionais

| Item | Vultr | Magalu Cloud | DigitalOcean | Hetzner | Locaweb |
|---|---|---|---|---|---|
| Região no Brasil | **Sim** (São Paulo, `sao`) | **Sim** (br-se1, br-ne1) | Não (EUA, Canadá, Europa, Ásia, Austrália) | Não (Alemanha, Finlândia, EUA, Singapura) | Sim [região não informada na página] |
| Volume em bloco | Sim em SP (preço [não confirmado]) | Sim, NVMe: **R$ 0,58/GiB/mês** (1K IOPS) | **US$ 0,10/GiB/mês** | Sim, 10 GB–10 TB (preço por GB [não confirmado]) | SSD adicional R$ 0,30/GB/mês |
| Backup automático | Sim, ~20% do plano (só no resumo da página; [não confirmado]); **não inclui volumes** (confirmado) | [não confirmado] | Semanal 20% / diário 30% do plano | 7 cópias diárias, "a partir de € 1,098/mês" | [não informado] |
| Snapshot | US$ 0,05/GB/mês (tamanho comprimido) | Instâncias: sem cobrança no momento; volumes: cobrado, valor não publicado | US$ 0,06/GB/mês | € 0,0143/GB/mês | R$ 0,30/GB/mês |
| Firewall externo | Sim [custo não confirmado] | [não confirmado] | Sim (sem custo conhecido [não confirmado]) | **Sim, sem custo** | [não informado] |
| Console / recuperação | Console web e ISO [não confirmado nesta consulta] | [não confirmado] | Console de recuperação [não confirmado nesta consulta] | **Rescue system confirmado**; console web [não confirmado] | [não informado] |
| Cobrança | Horária, teto de 672 h/mês (fonte secundária) | **Por segundo**; instância desligada não é cobrada no momento | Por segundo, teto 672 h/mês nos planos Basic | Horária com teto mensal | Horária/mensal |
| Pagamento | Cartão internacional [não confirmado] | **Cartão, boleto, PIX** | Cartão / PayPal [não confirmado] | Cartão / transferência SEPA [não confirmado] | [não informado] |
| Documentação | Extensa (API pública de planos consultada) | Boa, em português; calculadora só em JavaScript | Muito extensa | Extensa | Limitada |

**Compatibilidade técnica (todas as opções):** são máquinas virtuais KVM com kernel Linux completo, o que
permite em princípio **systemd** como PID 1, **dm-crypt/LUKS**, o módulo **WireGuard** (no kernel desde a
5.6) e **nftables**. O **Caddy** é um binário único e não depende do provedor. O **Python 3.13** não vem
no Ubuntu 24.04 (que traz 3.12): o mesmo Python 3.13 portátil e verificado da Fase 18A
(python-build-standalone) funciona em qualquer uma delas. A alternativa é o PPA deadsnakes, que exige
decisão por ser repositório de terceiros. A **imagem Ubuntu 24.04** da Magalu não pôde ser confirmada:
a lista só sai pela CLI ou pelo console.

### 2.3 Inconsistências encontradas nas fontes

- **Magalu, tráfego de saída:** a página de rede diz que nenhum tráfego está sendo cobrado
  (preços vigentes em 02/10/2026); a página do Object Storage lista saída a R$ 0,10/GiB. **Confirmar por
  escrito.** Neste projeto o impacto é pequeno (webhook e painel trafegam pouco).
- **Backblaze B2:** a página principal diz US$ 6,95/TB/mês e a de transações diz US$ 0,005/GB/mês após
  10 GB. Para o volume deste projeto (< 15 GB), as duas resultam em centavos.
- **Vultr:** o site comercial bloqueou a leitura automática (HTTP 403); os preços vêm da **API pública
  oficial** (`api.vultr.com/v2/plans`), sem data e sem unidade explícita de disco e banda.

## 3. Requisito crítico — LUKS

### 3.1 O que o sistema exige e como verifica

`rocha_reis_agent/persistence/storage_security.py` não aceita declaração: lê `/sys/dev/block/<maj>:<min>`
do dispositivo onde o banco está e só reconhece proteção se encontrar, na cadeia device-mapper, um
dispositivo com `dm/uuid` começando por `CRYPT-` (LUKS1, LUKS2 ou dm-crypt plain, direto ou sob LVM).
Sem essa evidência o resultado é "não verificado". No perfil `preproduction`, o banco
(`persistence/sqlite.py`), o backup automático, o instalador e o `validar-instalacao` **recusam**
operar.

**Consequência direta:** a "criptografia em repouso gerenciada pelo provedor" (disco cifrado no
hipervisor) **não é vista** de dentro da VM: o disco aparece como `vda` comum e a verificação falha.
O fato de o provedor cifrar o armazenamento dele **não substitui** o LUKS exigido. As duas proteções
podem coexistir, mas só o LUKS dentro do convidado é verificável pelo sistema.

### 3.2 Viabilidade por provedor

| Pergunta | Vultr | Magalu | DigitalOcean | Hetzner |
|---|---|---|---|---|
| Criar LUKS dentro da VM? | Sim, num volume anexado (ou num arquivo de loop no disco de sistema) | Sim, idem | Sim, idem | Sim, idem |
| `validar-instalacao` reconhece? | Sim: o dispositivo `dm-N` terá uuid `CRYPT-LUKS2-…`, o mesmo mecanismo testado na Fase 18A | Sim | Sim | Sim |
| Só criptografia gerenciada? | Não aplicável: o LUKS é do convidado | idem | idem | idem |

Não depende de recurso especial do provedor. A forma recomendada é um **volume em bloco separado**, não
o disco de sistema:

1. o volume sobrevive à destruição da VM e pode ser anexado a uma VM nova (recuperação);
2. o sistema operacional pode ser reinstalado sem tocar nos dados;
3. o disco de sistema não precisa de cifragem completa, que exigiria desbloqueio no boot pelo console.

O arquivo de loop cifrado no disco de sistema funciona, mas perde as vantagens 1 e 2. Fica como plano
de contingência caso o volume não esteja disponível.

### 3.3 O conflito: reinício automático × chave fora do servidor

Se a chave do LUKS ficar no próprio servidor (arquivo em `/etc/crypttab`), o volume abre sozinho, mas
quem obtiver uma cópia do disco de sistema e do volume (snapshot, backup do provedor, disco descartado,
acesso indevido à conta) também abre. **Isso anula a proteção e está descartado.** Sem a chave no
servidor, alguém precisa desbloquear após cada reinício.

| Estratégia | Como funciona | Disponibilidade | Segurança | Recomendação |
|---|---|---|---|---|
| **A. Desbloqueio manual** | Após o reinício o servidor sobe só com SSH, VPN e Caddy; o operador entra por SSH (pela VPN ou IP autorizado) e executa `cryptsetup open` + `mount` + `systemctl start` dos serviços | Fica fora do ar **até alguém agir** (minutos a horas). Mensagens recebidas nesse intervalo dependem das novas tentativas da Meta [prazo a confirmar na Fase 6B] | **Alta:** a frase-senha nunca fica no servidor | **Recomendada para a pré-produção** |
| B. Servidor de chaves na rede (Clevis + Tang) | O volume abre sozinho se um servidor Tang, em **outro provedor**, estiver alcançável; o Tang só aceita conexões do IP do servidor | Volta sozinho, desde que o Tang esteja no ar | **Média:** uma cópia do disco sozinha não abre, mas quem controlar a VM no mesmo IP consegue. Exige um 2º servidor e mais manutenção | Avaliar para produção, se o tempo fora do ar da opção A for inaceitável |
| C. Chave no disco de sistema (`crypttab`) | Abre sozinho | Total | **Nula** contra quem copia os discos | **Descartada** |
| D. TPM virtual | Chave presa a um chip virtual da VM | Total | Depende do provedor | Nenhum dos quatro provedores tem vTPM confirmado; descartada |

**Recomendação:** estratégia A, com estes cuidados (a implementar na 18B, mediante aprovação):

- as unidades `rocha-reis-*.service` passam a ter `RequiresMountsFor=/var/lib/rocha-reis-agent`.
  Hoje só declaram `After=network-online.target`, e sem o volume os serviços falham em ciclo até o
  `StartLimit`. O sistema já recusa abrir o banco fora do volume cifrado (falha segura), mas o ciclo
  gera ruído. É uma mudança de 1 linha por unidade, com teste;
- um script de desbloqueio na instalação (`cryptsetup open` → `mount` → `validar-instalacao` →
  `systemctl start`) que **pede** a frase-senha e nunca a grava;
- alerta de "servidor reiniciado e aguardando desbloqueio" enviado para fora do servidor. Hoje os
  alertas são locais; o canal externo continua pendente (Decisão 11 em `DECISOES_PENDENTES_SALAO.md`);
- reinicializações planejadas (atualização de kernel) em horário combinado; `unattended-upgrades` sem
  reinício automático.

### 3.4 Situações

| Situação | Comportamento esperado (estratégia A) |
|---|---|
| Reinício planejado | Operador agenda, reinicia, desbloqueia; `/ready` volta a 200 |
| Queda de energia / reinício do hipervisor | Igual ao anterior, mas sem aviso. O SQLite em WAL é resistente a corte: na abertura o journal é reaplicado. Conferir com `ops verificar` / `integrity_check` antes de reabrir as filas |
| Frase-senha esquecida | Os dados do volume são **irrecuperáveis**; restaurar do backup externo (que usa outra chave) |
| Cabeçalho LUKS corrompido | Restaurar o cabeçalho do backup (`cryptsetup luksHeaderBackup`, guardado fora do servidor) ou restaurar do backup externo |
| VM destruída, volume intacto | Criar VM nova, anexar o volume, desbloquear, reinstalar o pacote, validar |
| Volume destruído / conta perdida | Novo servidor + restauração do backup externo cifrado (fluxo `PLANO_RECUPERACAO_DESASTRES.md`) |

### 3.5 Chaves

- **Frase-senha LUKS:** longa e aleatória, num gerenciador de senhas, com **duas pessoas** com acesso
  (salão + agência, Decisão 4). Um segundo *keyslot* LUKS com outra frase de recuperação guardada em
  envelope lacrado.
- **Cópia do cabeçalho LUKS:** fora do servidor, junto às chaves.
- **Chave dos backups (`.rrbackup`):** **diferente** da frase LUKS; o sistema já impede guardá-la no
  diretório dos backups.

### 3.6 Impacto em backups e snapshots do provedor

- O backup automático da Vultr **não inclui volumes anexados** (confirmado no FAQ oficial). Nos demais
  provedores isso não foi confirmado. Os backups e snapshots do provedor protegem o **sistema e a
  configuração**, não os dados.
- Um snapshot do volume LUKS contém só dados cifrados: útil para voltar o volume inteiro, inútil para
  quem não tem a frase-senha.
- **O backup que vale para os dados é o da própria aplicação** (`.rrbackup`, AES-256-GCM, testado),
  enviado a outro provedor (§4).

## 4. Backup externo

O módulo `rocha_reis_agent/ops/offsite.py` define o contrato (`put`/`get`/`list`/`delete`, conferência
SHA-256 após envio, retenção de N cópias), hoje com um adaptador **falso**. Só arquivos já cifrados
saem, e a chave nunca é enviada. A 18B precisa de um adaptador compatível com S3, a desenvolver e testar.
**Nenhum envio foi implementado nesta etapa.**

Volume estimado: com 6 MB para 971 conversas, um banco real deve ficar na casa de dezenas a centenas
de MB. Com 14 cópias diárias, **menos de 15 GB** [estimativa, a medir com dados reais].

| Item | **Backblaze B2** (A) | **Cloudflare R2** (B) | Hetzner Object Storage | DigitalOcean Spaces | Vultr Object Storage | Magalu Object Storage |
|---|---|---|---|---|---|---|
| API S3 | Sim | Sim | Sim | Sim | Sim | Sim |
| Armazenamento | US$ 6,95/TB/mês; 10 GB grátis | US$ 0,015/GB/mês; 10 GB/mês grátis | € 6,49/mês com 1 TB | US$ 5/mês com 250 GiB | Archive: US$ 6/mês (100 GB + 1 TB arquivo); outros níveis [não confirmados] | R$ 0,10/GiB/mês |
| Operações | Classes A, B e C grátis | A: US$ 4,50/milhão; B: US$ 0,36/milhão (1 e 10 milhões grátis) | Grátis | Incluídas | [não confirmado] | [não confirmado] |
| Download | Grátis até 3× o armazenado; depois US$ 0,01/GB | **Grátis** | 1 TB incluído | 1024 GiB incluídos; depois US$ 0,01/GiB | 1 TB incluído | R$ 0,10/GiB (ver §2.3) |
| Versionamento | [não confirmado nesta consulta] | [não confirmado] | **Sim** | [não confirmado] | **Sim** | **Sim** |
| Imutabilidade | **Object Lock** (APIs `PutObjectRetention`/`PutObjectLockConfiguration` documentadas) | **Bucket Locks** por prazo, data ou indefinido | **Object Lock GOVERNANCE/COMPLIANCE** (ativar na criação do bucket) | [não confirmado] | **Não documentado** | Citado, sem detalhes |
| Mínimo de retenção cobrado | Nenhum | Nenhum (Standard) | — | — | — | — |
| Região | EUA / UE [não confirmado na página] | Global, com opção de jurisdição [não confirmado] | Alemanha, Finlândia | EUA, Europa, Ásia | Várias | Brasil |

**Recomendação:**

- **A — Backblaze B2 com Object Lock**, provedor diferente do servidor (Opção A ou B). Custo praticamente
  nulo para < 15 GB, operações grátis e imutabilidade. Uma restauração completa cabe na franquia de
  download.
- **B — Cloudflare R2 com Bucket Lock**, com download sempre grátis e franquia que cobre o volume
  previsto.
- **Hetzner Object Storage** é uma boa terceira opção (Object Lock COMPLIANCE confirmado), mas tem
  mensalidade fixa de € 6,49.
- **Não usar o armazenamento do mesmo provedor do servidor** como cópia externa: uma conta suspensa ou
  comprometida levaria as duas cópias.

**Credenciais restritas:** uma chave de aplicação **exclusiva** para um único bucket, com permissão de
gravar e ler e **sem permissão de apagar fora da retenção** (o Object Lock impede apagar antes do prazo,
mesmo com credencial vazada). A configuração de permissões granulares de cada provedor deve ser
[confirmada na 18B]. A credencial fica num arquivo 0600 fora do repositório, como os demais segredos.

**Restauração:** o teste mensal de restauração já existe (`ops`), e o alerta `restauracao_falhou` foi
criado na 18A. A 18B deve incluir **uma restauração real a partir do destino externo** numa instância nova.

## 5. Segurança e operação

| Item | Viável? | Como |
|---|---|---|
| SSH por chave | Sim (todos) | Chave injetada na criação; `PasswordAuthentication no`, `PermitRootLogin no` |
| MFA na conta do provedor | Vultr, DigitalOcean e Hetzner oferecem 2FA [não confirmado nesta consulta]; Magalu [não confirmado] | **Obrigatório** antes de criar o servidor; conta em nome do salão ou da agência (decisão) |
| Firewall externo | Hetzner (grátis, confirmado), Vultr e DigitalOcean (sim); Magalu (grupos de segurança [não confirmado]) | Espelhar o nftables: 443/tcp, 51820/udp; 22/tcp só do IP da agência ou só pela VPN |
| nftables, WireGuard, HTTPS | Sim | Arquivos de exemplo testados na 18A (`deploy/`) |
| Certificados | Sim | Caddy com ACME (Let's Encrypt) para o domínio do webhook; exige porta 443 (ou 80) pública e DNS apontado |
| Logs e alertas | Sim | journald + alertas locais (22 códigos); canal externo para pessoas ainda pendente |
| Snapshots | Sim | Antes de cada atualização (`ATUALIZACAO_E_ROLLBACK.md`) |
| Console de emergência | Rescue da Hetzner confirmado; demais [não confirmado] | Necessário para recuperar um SSH ou firewall mal configurado; **conferir antes da compra** |
| Monitoramento externo | Sim | Uma verificação HTTPS de `/health` a partir de fora (serviço a escolher; pode ter custo zero) |
| Atualizações de segurança | Sim | `unattended-upgrades` só para segurança, **sem reinício automático** (por causa do LUKS) |

**Latência e localização:** para o salão e a agência no Brasil, São Paulo dá a menor latência no painel
(pela VPN). O webhook da Meta funciona de qualquer região. Servidores nos EUA ou na Europa adicionam
latência perceptível, mas tolerável no painel. A localização pesa mais na questão jurídica do que no
desempenho.

**Para avaliação jurídica posterior (não é parecer):**

- Opções A e B mantêm o banco ativo no **Brasil**.
- O backup externo recomendado (B2/R2) fica **fora do Brasil**: cifrado antes do envio, com a chave fora
  do provedor. Ainda assim, pode ser enquadrado como transferência internacional de dados pessoais
  (LGPD, Cap. V, e a regulamentação da ANPD sobre transferência internacional). Se o jurídico vetar, a
  alternativa é o backup externo num **segundo provedor brasileiro** (ex.: Magalu Object Storage com
  servidor na Vultr SP, ou vice-versa), aceitando imutabilidade não confirmada.
- O Google Gemini e a Meta (fases próprias) também envolvem tratamento por terceiros no exterior; devem
  ser avaliados juntos.
- Contrato e termos de tratamento de dados (DPA) de cada provedor: solicitar e anexar.

## 6. Custo mensal estimado

Câmbio: **PTAX de fechamento do Banco Central, 19/11/2026** (último dia útil publicado na consulta):
venda **USD 5,8889** e **EUR 6,4672**. Cartão internacional: **IOF de 3,5%** (Decreto 12.499/2025,
fontes secundárias). O câmbio efetivo do cartão costuma ser **maior** que a PTAX (spread do emissor),
então os valores em reais das opções em dólar são um **piso**.

### Cenário original Vultr SP `vc2-2c-4gb` + Backblaze B2 (superado: plano indisponível — ver §12.4)

| Item | Original | R$ (PTAX) | Situação |
|---|---|---|---|
| VPS `vc2-2c-4gb` (SP) | US$ 30,00 | 176,67 | verificado (API oficial) |
| IPv4 | — | — | [não confirmado] se incluído |
| Volume 40 GB (dados LUKS) | a cotar | — | [preço não confirmado] |
| Backup automático do provedor (opcional, ~20%) | ~US$ 6,00 | ~35,33 | [não confirmado] |
| Snapshots antes de atualizações | US$ 0,05/GB/mês, eventual | variável | verificado |
| Backup externo B2 (< 15 GB) | ~US$ 0,00–0,10 | ~0,00–0,59 | verificado (10 GB grátis) |
| Tráfego | dentro da franquia | 0 | verificado |
| IOF 3,5% sobre itens em USD | — | ~7,42 (sobre US$ 36) | fonte secundária |
| **Subtotal verificável** (VPS + backup opcional + IOF) | **~US$ 36,00** | **~R$ 219,42** | + volume |

### Cenário Magalu Cloud br-se1 (hoje Opção A, com B2 — ver §12.4)

| Item | Original | Situação |
|---|---|---|
| VPS BV 2 vCPU / 4 GB / 40 GB | R$ 102,99 | verificado, impostos incluídos |
| Volume NVMe 40 GiB × R$ 0,58 | R$ 23,20 | verificado |
| IPv4 | R$ 0,00 "no momento" | **pode passar a ser cobrado** |
| Snapshot de instância | R$ 0,00 "no momento" | idem |
| Tráfego de saída | R$ 0,00 a R$ 0,10/GiB | inconsistente (§2.3) |
| Backup externo R2 (< 10 GB) | US$ 0,00 (franquia) | verificado |
| **Total verificável** | **R$ 126,19** | |

### Itens comuns e fora do preço fixo

| Item | Valor | Situação |
|---|---|---|
| Domínio `.com.br` (se o salão não tiver) | R$ 40,00/ano ≈ R$ 3,33/mês | fontes secundárias; confirmar no Registro.br |
| Monitoramento externo | a definir (há opções sem custo) | não cotado |
| Tributos sobre serviço contratado no exterior | **avaliar com o contador** (ex.: tributação de importação de serviços para pessoa jurídica) | não estimado |
| Implantação e manutenção pela agência | **não definido** | a orçar à parte |
| Google Gemini e Meta WhatsApp | **fora deste orçamento** | estimativas próprias nas fases 9C-B e 6B |

Comparação (só a VPS de 2 vCPU / 4 GB, sem volume): Hetzner CX23 € 5,99 ≈ R$ 38,74 (**indisponível**);
DigitalOcean US$ 24,00 ≈ R$ 141,33 (+ volume US$ 4,00 ≈ R$ 23,56); Locaweb R$ 115,00 (com IP e NAT
obrigatórios).

## 7. Matriz comparativa

Notas de 1 (fraco) a 5 (forte), justificadas pelas evidências acima. Não são pesos de preço.

| Critério | Vultr SP | Magalu br-se1 | DigitalOcean | Hetzner | Locaweb |
|---|---|---|---|---|---|
| Compatibilidade técnica (KVM, volume, LUKS, WG) | 5 | 4 (Ubuntu 24.04 não confirmado) | 5 | 5 | 3 (NAT obrigatório, pouca documentação) |
| Segurança (firewall, 2FA, console) | 4 | 3 (não confirmados) | 4 | 4 | 2 (não informado) |
| Custo | 3 (USD + IOF) | 5 | 3 | 5 no papel | 4 |
| Suporte e documentação | 4 | 3 | 5 | 4 | 2 |
| Facilidade de implantação | 4 | 3 | 5 | 4 | 2 |
| Recuperação (volume independente, snapshots) | 4 | 4 | 5 | 4 | 3 |
| Localização (latência + dados no Brasil) | 5 | 5 | 2 | 2 | 5 |
| Disponibilidade para contratar agora | 4 | 4 | 5 | **1** | 4 |
| Riscos | Câmbio; preço do volume a confirmar | Plataforma nova; gratuidades "no momento" | Dados fora do Brasil | Indisponível; reajustes | Pouca transparência |

**Avaliação original (antes da verificação final):** a Vultr vinha à frente por documentação e console. **Superada na Fase 18B-0:** o plano de 4 GB não está disponível em São Paulo; a ordem atual (Magalu A condicionada; Vultr `vhf-2c-2gb` B) está no sumário e no §12.4.

## 8. Riscos restantes

1. Tempo fora do ar após reinício não planejado (estratégia A do LUKS) — mitigação: alerta externo e
   rotina de plantão; alternativa B (Tang) se inaceitável.
2. Perda da frase-senha LUKS ou da chave dos backups — mitigação: duas pessoas, cópia lacrada; sem as
   duas chaves, nada se recupera.
3. Preços em USD/EUR variam com o câmbio; gratuidades "no momento" da Magalu podem acabar.
4. Adaptador S3 do backup externo ainda não existe (só o falso) — desenvolvimento e teste na 18B.
5. `RequiresMountsFor` ausente nas unidades systemd — correção proposta, não aplicada.
6. Canal de alerta para pessoas (fora do servidor) inexistente.
7. Questões jurídicas: transferência internacional do backup; DPAs dos provedores; titularidade da conta.
8. Itens [não confirmados] deste documento — ver §9.

## 9. Perguntas a confirmar antes da compra

Ver `ESPECIFICACAO_CONTRATACAO_SERVIDOR.md` §5, que é o documento a enviar.

## 10. Plano da Fase 18B

Nada abaixo será executado sem servidor contratado e autorização escrita para cada etapa.

| # | Etapa | O que fazer | Evidência / aceite |
|---|---|---|---|
| 1 | Contratação autorizada | Pessoa habilitada contrata conforme a especificação; 2FA na conta; acesso entregue à agência por chave SSH | Autorização escrita; recibo; conta com 2FA |
| 2 | Configuração inicial | Ubuntu 24.04 LTS oficial; atualizar; fuso; hostname; `unattended-upgrades` só de segurança sem reinício; usuário admin com sudo; `rocha-reis` sem shell | `lsb_release`, `systemctl --version` (255) |
| 3 | Criptografia | Volume de 40 GB → `cryptsetup luksFormat --type luks2` com frase digitada pelo responsável; 2º keyslot de recuperação; `luksHeaderBackup` para fora; ext4; montar em `/var/lib/rocha-reis-agent`; **sem** chave em `crypttab` | `lsblk`, `dmsetup info` (uuid `CRYPT-LUKS2-`), `validar-instalacao` reconhece |
| 4 | Acesso SSH | Só chave; sem root; sem senha; 22/tcp restrito ao IP da agência ou à VPN | Tentativa por senha recusada; root recusado |
| 5 | Firewall | Firewall do provedor + `rocha-reis.nft.exemplo` adaptado; política padrão `drop` | `nft list ruleset`; varredura externa |
| 6 | Instalação | Python 3.13 portátil verificado; `empacotar → instalar → preparar-servidor → validar-instalacao` | `ok: true` como `rocha-reis` |
| 7 | systemd | Unidades de `deploy/systemd` como serviços **do sistema** com `User=rocha-reis` (+ `RequiresMountsFor`, se aprovado) | Roteiro de `tests_infra/fase18_systemd.py` reproduzido: watchdog, reinício, StartLimit, OnFailure |
| 8 | Caddy | Binário oficial com soma conferida; drop-in `After=wg-quick@wg0`; sysctl `ip_nonlocal_bind` | Painel só no IP da VPN; fora do webhook → 404 |
| 9 | WireGuard | Chaves geradas **no servidor**; um aparelho de teste; `10.8.0.1` | Painel acessível só com VPN; acesso direto bloqueado |
| 10 | HTTPS | Domínio do webhook apontado; certificado público ACME | `curl -v https://<domínio>/health` com cadeia válida |
| 11 | Backup externo | Adaptador S3 desenvolvido e testado offline; bucket com Object Lock; credencial restrita | Envio + conferência SHA-256; tentativa de apagar antes do prazo recusada |
| 12 | Monitoramento | Timer de alertas; verificação externa de `/health`; canal para pessoas, se decidido | Disparo controlado de um alerta |
| 13 | Testes de segurança | Varredura de portas de fora (só 443 e 51820); TLS; cabeçalhos; logs sem IP completo e sem conteúdo de mensagens | Relatório anexado |
| 14 | Reinício | `reboot` → desbloqueio manual → serviços sobem; queda simulada (reset pelo painel do provedor) | `/ready` 200; filas íntegras; `integrity_check` ok |
| 15 | Recuperação | (a) VM nova + volume reanexado; (b) instância nova + restauração do backup externo | Dados sintéticos idênticos; tempo medido × RTO/RPO da Decisão 5 |
| 16 | Ensaio operacional | `ops ensaio-operacional` e `ops carga` no próprio servidor | Resultados registrados |
| 17 | Critérios de aceite | Todos os itens acima com evidência **no servidor real**; quatro travas continuam fechadas; nenhum dado real; nenhuma integração externa ativada | `STATUS_FASE_18B.md` com matriz; aprovação do responsável |

## 11. Decisões a aprovar

| # | Decisão | Recomendação técnica | Quem decide |
|---|---|---|---|
| I-1 | Provedor do servidor | (revisado na 18B-0) Magalu br-se1 se Ubuntu 24.04 e console forem confirmados por escrito; senão Vultr SP `vhf-2c-2gb` | Responsável financeiro + agência |
| I-2 | Desbloqueio do LUKS após reinício | Manual (A), aceitando ficar fora do ar até intervenção | Responsável pelo salão |
| I-3 | Destino do backup externo | B2 (A) ou R2 (B); provedor brasileiro se o jurídico vetar o exterior | Responsável + jurídico |
| I-4 | Titularidade das contas (servidor, backup, domínio) | Em nome do salão, com a agência como usuária com 2FA | Responsável pelo salão |
| I-5 | Python 3.13: portátil verificado ou PPA deadsnakes | Portátil verificado (testado na 18A) | Agência |
| I-6 | Adicionar `RequiresMountsFor` às unidades | Sim | Agência (mudança de código, com teste) |
| I-7 | Orçamento mensal e de implantação | — | Responsável financeiro |

## 12. Verificação final dos provedores e do armazenamento (Fase 18B-0, 21/11/2026)

Fontes oficiais consultadas hoje. O que depende do painel de uma conta continua **pendente** — nenhuma
conta foi aberta.

### 12.1 Vultr (São Paulo)

| Item | Situação |
|---|---|
| `vc2-2c-4gb` disponível para novo pedido em SP | **Não** — ausente de `api.vultr.com/v2/regions/sao/availability` (disponíveis: `vc2-1c-1gb`, `vhf-1c-1gb`, `vhf-1c-2gb`, `vhf-2c-2gb`) |
| Preço regional de US$ 30 | Ainda listado em `/v2/plans` (`location_cost.sao`), mas **não contratável** hoje; `vhf-2c-2gb` em SP: **US$ 27,00** (`/v2/plans`, consulta anterior) |
| Volume anexável em SP | `/v2/regions` lista `block_storage_storage_opt` para `sao` (HDD); NVMe/alto desempenho **não** listado |
| Custo exato do volume | **Não confirmado** (páginas de preço bloqueadas; documentação sem valor) |
| Volume persiste após excluir a VM | **Não documentado**: a documentação cobre só "Detach" e "Delete" (que apaga os dados) |
| Console de emergência | "Vultr Console" e **ISO personalizada/ambiente de recuperação** documentados; VNC não nomeado |
| Recuperação do volume cifrado | Possível pelo console + ISO (o LUKS é do convidado); a cifragem AES-256 do provedor no Block Storage **não conta** para o sistema |
| 2FA / custo do firewall | Não confirmados (página de conta 404; firewall sem preço) |

### 12.2 Magalu Cloud (br-se1)

| Item | Situação |
|---|---|
| Ubuntu 24.04 LTS | **Não confirmado** — a documentação só diz "Ubuntu"; a lista sai pela CLI/console (`mgc virtual-machine images list`) — **eliminatório** |
| Disponibilidade no Sudeste | Regiões br-se1 e br-ne1 documentadas; disponibilidade "pode variar por região e zona" |
| Preço | BV 2 vCPU/4 GB/40 GB R$ 102,99; volume NVMe 1K IOPS R$ 0,58/GiB (40 GiB = R$ 23,20); impostos incluídos (vigência 02/10/2026) |
| IPv4 público | Sem cobrança "no momento"; "a cobrança pode ser iniciada a qualquer momento" |
| Console de recuperação | **Não documentado** (só SSH/RDP) — **eliminatório** até confirmação |
| Firewall | Grupos de segurança com negação implícita documentados; custo não informado |
| Snapshot | De instância: sem cobrança atual e persiste após excluir a instância; de volume: cobrado, valor não publicado |
| Tráfego de saída | VM/IP/NAT: sem cobrança no momento. Object Storage: R$ 0,10/GiB (são caminhos diferentes — não é contradição) |
| Volume persiste | Documentado de forma geral ("delete sua máquina sem perder os dados armazenados") |
| Cobrança | Pagamento conforme uso, por segundo; formas de pagamento (cartão, boleto, PIX) na consulta anterior |

### 12.3 Armazenamento externo — o que impede a exclusão (B2 e R2 NÃO são equivalentes)

| Controle | Backblaze B2 | Cloudflare R2 |
|---|---|---|
| Mecanismo | **Object Lock S3** (por objeto ou retenção padrão do bucket), GOVERNANCE ou COMPLIANCE | **Regras de bloqueio do bucket** (idade, data ou indefinido); a API S3 **não** implementa `x-amz-object-lock-*` |
| Quem pode apagar antes do prazo | COMPLIANCE: **ninguém**, nem o dono da conta (só encerrando a conta); GOVERNANCE: chave com `bypassGovernance` | Ninguém enquanto a regra existir — mas **quem administra a conta pode remover a regra** (a documentação trata a remoção como operação normal) |
| Sobrescrita | Bucket sempre versionado: nova versão; a anterior fica retida | Bloqueada pela regra (409 `ObjectLockedByBucketPolicy`); `If-None-Match: *` suportado |
| Versionamento | Sempre ativo | Não implementado na API S3 |
| Credencial restrita | Chave limitada a 1 bucket + prefixo; capacidades `listFiles`, `readFiles`, `writeFiles` (+ `writeFileRetentions` se o servidor marcar a retenção por objeto); **sem** `deleteFiles`, `bypassGovernance`, `writeKeys` | Token "Object Read & Write" limitado aos buckets escolhidos; prefixo não documentado; se inclui exclusão não é dito (a regra de bloqueio protege mesmo assim) |
| Revogação | Apagar a chave de aplicação | Revogar o token |
| Conclusão | **Imutabilidade mais forte** → recomendado | Protege contra a credencial do servidor, **não** contra um administrador da conta comprometido |

O adaptador (`rocha_reis_agent/ops/s3_store.py`) segue essas diferenças: com `provedor: r2` recusa
`modo_bloqueio` (a R2 não aceita os cabeçalhos); a exclusão pelo cliente vem **desligada** (retenção pelo
provedor); nunca sobrescreve (HEAD + `If-None-Match: *`). **A retenção definitiva (dias) depende de
aprovação** (Decisão 3).

### 12.4 Custo completo verificável e pendências de contratação

| | Opção A — Magalu br-se1 + B2 | Opção B — Vultr SP `vhf-2c-2gb` + R2 |
|---|---|---|
| Servidor | R$ 102,99 | US$ 27,00 → R$ 159,00 (PTAX 5,8889) + IOF 3,5% = **R$ 164,57** |
| Volume 40 GB | R$ 23,20 | **a cotar** |
| Backup do provedor (opcional) | a confirmar | ~20% do plano (não confirmado) |
| Backup externo (< 10 GB) | US$ 0 (franquia de 10 GB da B2) | US$ 0 (franquia de 10 GB da R2) |
| **Total verificável** | **R$ 126,19/mês** | **R$ 164,57/mês + volume** |
| Pendências antes da compra | Ubuntu 24.04 em br-se1; console de recuperação; custo do firewall; aviso prévio de cobrança de IPv4 | Disponibilidade no dia; preço do volume; volume persiste após destruir a VM; 2FA |

Fora do preço fixo: domínio (≈ R$ 40/ano), tributos sobre serviço do exterior (contador), horas da
agência, Gemini e Meta (estimativas próprias).

## Fontes (consultadas em 21/11/2026)

- 18B-0: api.vultr.com/v2/regions/sao/availability (e `?type=vc2`), /v2/plans?type=vc2; docs.vultr.com (block storage: provisioning, attach-instances, delete-block-storage; cloud-compute FAQ e custom-iso; firewall FAQ); docs.magalu.cloud (images general-overview e list-images, network overview e prices, block-storage overview e pricing, object-storage overview e price, virtual-machine overview); backblaze.com/docs (object-lock, application-keys, application-key-capabilities, s3-compatible-api, bucket-versions); developers.cloudflare.com/r2 (bucket-locks, api/s3/api, api/s3/conditional-operations, api/tokens).

- Hetzner: hetzner.com/cloud, /cloud/regular-performance, /cloud/cost-optimized, /cloud/general-purpose,
  /cloud/block-storage, /storage/object-storage; docs.hetzner.com (backups-snapshots, volumes, FAQ,
  rescue-system, object-storage: versioning, object-lock retention, supported-actions);
  status.hetzner.com. A página de reajuste de preços retornou 404.
- DigitalOcean: digitalocean.com/pricing/droplets; docs.digitalocean.com: droplets, backups, spaces,
  volumes pricing (atualizadas entre 09/03/2026 e 17/11/2026).
- Vultr: api.vultr.com/v2/plans (vc2, vhf) e /v2/regions; docs.vultr.com: FAQs de backups, snapshots,
  block storage e object storage, limits, S3 compatibility matrix (atualizadas em 23/10/2026). Páginas
  comerciais retornaram 403.
- Magalu Cloud: docs.magalu.cloud: VM pricing, instance types, block storage, object storage price e
  overview, network prices, FinOps (preços vigentes em 02/10/2026).
- Locaweb: locaweb.com.br/locaweb-cloud/precos ("Tabela de Preços 2026").
- Backblaze: backblaze.com/cloud-storage/pricing e /transaction-pricing.
- Cloudflare: developers.cloudflare.com/r2/pricing e /r2/buckets/bucket-locks.
- Câmbio: BCB PTAX (olinda.bcb.gov.br), fechamento de 19/11/2026.
- IOF e domínio: fontes secundárias (ARQ Finance, InfoMoney; Teletime citando o NIC.br).
