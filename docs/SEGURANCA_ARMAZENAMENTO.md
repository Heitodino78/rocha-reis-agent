# Segurança do armazenamento e proteção de dados

Situação em 20/11/2026 (Fase 12). Distingue o que está **implementado e testado**, o que é
**recomendado** e o que está **pendente**. Nada aqui autoriza dados reais.

## 1. O que está protegido hoje

| Item | Proteção | Situação |
|---|---|---|
| Banco ativo (SQLite + WAL/SHM) | Arquivo 0600 em diretório privado; `secure_delete=ON`; temporários do SQLite em memória (`temp_store=MEMORY`) | Implementado e testado |
| Banco ativo em repouso | **Depende do volume.** O sistema **constata** dm-crypt/LUKS pelo kernel e pode exigi-lo (`STORAGE_REQUIRE_ENCRYPTED_VOLUME`, sempre em pré-produção) | Verificação implementada e testada; cifragem é do sistema operacional |
| Backups | AES-256-GCM (`cryptography`), manifesto autenticado, chave separada | Implementado e testado (Fase 10) |
| Cópia e verificação do backup | **Em memória** (`serialize`/`deserialize`): nenhuma cópia em claro em `/tmp` | Implementado e testado nesta fase (antes ia para o temporário do sistema) |
| Restauração | Só para arquivo novo, 0600, em diretório privado; substituição do ativo com confirmação e cópia anterior | Implementado (Fase 10) |
| Cópia anterior à promoção (`*.pre-restore-*`) | Mesmo diretório e volume do banco ativo, 0600 | Herda a proteção do volume; não é cifrada à parte |
| Perfis, credenciais de demonstração, chaves | 0600 em diretórios 0700 fora do repositório | Implementado e testado |
| Logs | Sem texto de mensagem, telefone, segredo ou caminho de dados | Testado (inclusive nesta fase) |
| Métricas (`ops metricas`) | Só contagens e tempos | Testado |

**Constatado nesta máquina de desenvolvimento** (pelo kernel, não presumido): `~/.local/share`
está em LVM sobre **LUKS2** (`data-root → cryptdata`); o volume de 1 TB do repositório é vfat
**sem** cifragem (não guarda banco, backup nem chave); `/tmp` é tmpfs (memória). O banco
ativo da demonstração e da pré-produção sintética fica no volume LUKS2.

> Isto **não** significa que um servidor futuro terá cifragem: ela precisa ser configurada lá
> e verificada com `python -m rocha_reis_agent.ops ambiente`.

## 2. Alternativas avaliadas para o banco ativo

| Critério | A — Volume cifrado (LUKS/dm-crypt ou disco cifrado do provedor) | B — SQLCipher | C — Banco gerenciado (PostgreSQL) |
|---|---|---|---|
| Segurança em repouso | Cobre banco, WAL, SHM, journals, cópias, logs e chaves no mesmo volume | Cobre só os arquivos do banco; WAL/temporários dependem da configuração; logs e backups ficam de fora | Cifragem do provedor; dados saem da máquina da aplicação |
| Ameaça coberta | Disco/servidor desligado ou descartado; snapshot do disco | Arquivo copiado isoladamente | Idem A, gerido pelo provedor |
| Não cobre | Servidor ligado e comprometido (o volume está aberto) | Idem, e a chave precisa estar acessível ao processo | Idem; mais uma credencial de rede |
| Compatibilidade | Total: o código não muda | Exige trocar o driver `sqlite3` por binding SQLCipher (compilado), revisar backup online, migrações e testes | Reescrever a persistência (hoje SQLite com `STRICT`, gatilhos e `PRAGMA`) |
| Complexidade/manutenção | Baixa (configuração do sistema/provedor) | Média-alta (dependência nativa, chave no processo) | Alta para o volume atual |
| Desempenho | Impacto pequeno (AES-NI) | Impacto moderado | Latência de rede |
| Backups/recuperação | Inalterados (já cifrados) | Precisam de chave do SQLCipher + chave do backup | Ferramentas do provedor |
| Custo | Normalmente sem custo adicional | Sem licença no núcleo aberto; custo de manutenção | Custo mensal do serviço (não cotado) |
| Portabilidade | Alta | Média | Média |

**Decisão técnica:**
- **Agora e no primeiro servidor:** **A — volume cifrado**, com a verificação automática da
  aplicação ativada (`STORAGE_REQUIRE_ENCRYPTED_VOLUME=true`; obrigatória em pré-produção). É
  a única opção que protege também WAL, journals, cópias de restauração, logs e chaves.
- **SQLCipher: não adotado.** Acrescentaria dependência nativa e chave dentro do processo sem
  proteger o que fica fora do arquivo do banco.
- **Banco gerenciado: só se** houver mais de uma instância da aplicação ou exigência de
  réplica/alta disponibilidade (ver `ARQUITETURA_PRE_PRODUCAO.md`).

Nenhuma criptografia própria foi escrita: a cifragem em repouso é do sistema operacional, e a
dos backups vem da biblioteca `cryptography`.

## 3. Arquivos auxiliares

| Arquivo | Onde fica | Proteção |
|---|---|---|
| `*-wal`, `*-shm`, `*.init.lock` | Diretório do banco | Mesmo volume; 0600 |
| Temporários do SQLite (ordenação, índices) | Memória (`temp_store=MEMORY`) | Não vão a disco |
| Snapshot e verificação do backup | Memória | Não vão a disco |
| `*.rrbackup` | Diretório de backups (0700) | AES-256-GCM |
| `*.rrbackup.partial` | Diretório de backups, durante a escrita | Já cifrado; removido em caso de falha |
| Banco restaurado | Destino novo, 0600, diretório 0700 | **Em claro**: escolha destino no volume cifrado |
| `*.pre-restore-*` | Diretório do banco ativo | Em claro, mesmo volume do ativo |
| Chave de backup | Diretório próprio, 0600, nunca dentro dos backups | Custódia humana pendente |
| Dumps/exportações | **Não existem** comandos de exportação em claro | — |
| Logs | journald/console | Sem conteúdo pessoal |

## 4. Ambientes e isolamento (implementado)

- `ENVIRONMENT_PROFILE` gravado no banco (`metadata.environment_profile`) na primeira abertura:
  um banco `demo` ou `preproduction` **não abre** em outro perfil nem sem perfil; um banco
  antigo sem marcação **não vira** pré-produção.
- Pré-produção: banco `*.preprod.synthetic.sqlite3`, diretório 0700, volume com cifragem
  **verificada**, conhecimento no banco, transporte e IA simulados, sem worker embutido.
- `metadata.data_classification` (migração 010) é `synthetic` em todos os bancos; um banco com
  outro valor é recusado.
- Quarta trava de código `REAL_DATA_ACTIVATION_AUTHORIZED = False`: `DATA_CLASSIFICATION=real`
  é recusado sempre.

## 5. Backup e recuperação — procedimentos

Conceitos: **RPO** = quanto dado se aceita perder (intervalo entre backups); **RTO** = quanto
tempo o atendimento pode ficar parado até voltar. **Propostas iniciais para discussão — não
acordadas com o salão:** RPO de 24 h (backup diário) e RTO de 4 h em horário comercial.
Reduzir o RPO exigiria backups mais frequentes ou replicação contínua.

| Situação | Procedimento (comandos de `python -m rocha_reis_agent.ops`) | Testado localmente |
|---|---|---|
| Falha do disco | Novo servidor com volume cifrado → `restaurar` o backup mais recente para caminho novo → `ambiente` → iniciar | Restauração e abertura: sim. Troca de servidor: não |
| Corrupção do banco | Parar painel/worker → `verificar` backups → `restaurar` em caminho novo → `promover --confirmacao SUBSTITUIR:<arquivo>` (guarda cópia anterior) | Sim (promoção, recusa com app aberto, cópia anterior) |
| Atualização mal-sucedida | Migrações são transacionais: falha deixa o banco na versão anterior, intacto; corrigir e reabrir | Sim (migração interrompida e repetida) |
| Erro humano (dado apagado/alterado) | Restaurar backup anterior em caminho **isolado**, conferir e decidir; nunca sobrescrever sem confirmação | Sim (restauração isolada) |
| Interrupção durante migração | Igual a atualização mal-sucedida | Sim |
| Perda da chave de backup | **Backups irrecuperáveis.** Gerar nova chave e novo backup imediatamente; por isso a custódia deve ter duas pessoas | Sim (chave errada é recusada); não há recuperação possível |
| Restaurar backup antigo | Restaura para caminho novo; ao abrir, migra a cópia para a versão atual sem tocar no original | Sim (backup na versão 9 → atual) |
| Fila incerta após restauração | Envios UNKNOWN continuam UNKNOWN; nada é reenviado sozinho | Sim |

Retenção configurável: `ops reter DIR --manter N [--dias D]` mostra o plano; só apaga com
`--confirmacao APAGAR:<quantidade>`; nunca apaga o backup válido mais recente nem arquivos não
identificados.

## 6. Requisitos antes de qualquer dado pessoal real

Todos obrigatórios; `ops ambiente` mostra a situação dos verificáveis:

1. Trava `REAL_DATA_ACTIVATION_AUTHORIZED` aberta por mudança de código revisada, com autorização
   escrita do responsável pelos dados.
2. Banco em volume com cifragem **verificada** pela aplicação.
3. Painel só por HTTPS, cookie `Secure`, atrás de VPN ou proxy autenticado; segundo fator
   obrigatório (*Fase 13: implementado e testado localmente; HTTPS só com certificado de
   desenvolvimento; VPN/proxy real pendentes*).
4. Backup diário automático para fora do servidor, teste de restauração mensal registrado
   (*Fase 13: rodada automática, teste de restauração e ensaio de desastre implementados; destino
   externo e agendamento real pendentes*).
5. Custódia da chave de backup com duas pessoas; procedimento de troca de chave.
6. Base legal, aviso de privacidade, retenção e atendimento a titulares aprovados com apoio
   jurídico.
7. Homologações Meta e Gemini concluídas; validação do painel pela equipe (11B).
8. Segredos fora do repositório (arquivo 0600 ou credenciais do `systemd`), com rotação documentada.

Enquanto qualquer item estiver pendente, o sistema continua restrito a dados sintéticos.

## 7. Servidor (Fase 17)

- **Árvore:** código somente leitura em `/opt/rocha-reis-agent/releases/`; perfil e segredos em
  `/etc/rocha-reis-agent` (0700/0600, **somente leitura** para os serviços); banco, backups e estado em
  `/var/lib/rocha-reis-agent` (0700, volume LUKS verificado); `validar-instalacao` recusa subir se algo
  estiver frouxo, se o banco ou a chave do 2º fator estiverem fora do lugar, sem cifragem ou com trava aberta.
- **SQLite** continua adequado (um servidor, três processos, ~3 KB por mensagem; medição em
  `STATUS_FASE_17.md` §F). Ajustes da instância: espera por bloqueio 5 s (1 s recusava webhooks em rajada),
  até 1000 conversas ativas, limite de 512 MB. `-wal`/`-shm` herdam 0600 do banco. Migração para PostgreSQL
  **não** recomendada sem evidência de necessidade (vários servidores ou carga muito maior).
- **Ensaio de migração** numa cópia decifrada **no volume cifrado** (nunca em /tmp), apagada no fim.
- **Cópia externa:** só backups já cifrados saem; chaves nunca; envio conferido por SHA-256; destino real
  ainda não contratado (adaptador falso nos testes).
