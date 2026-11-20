# Plano de recuperação de desastres

Situação em 20/11/2026 (Fase 13). Procedimentos **testados localmente** com dados fictícios, em
diretório isolado, salvo onde marcado como dependente de infraestrutura externa.

## 1. O que precisa sobreviver separado do servidor

| Item | Por quê | Hoje (local) | Produção (pendente) |
|---|---|---|---|
| Backups cifrados | Dados | Diretório secundário **no mesmo disco** (simulado) | Armazenamento externo, outra região |
| Chave dos backups | Abrir os backups | Arquivo em `chaves/` | Guardada por duas pessoas, fora do servidor |
| Chave do segundo fator | Funcionários entrarem | Arquivo em `chaves/` | Idem |
| Configuração do perfil (`perfil.json`, segredos do webhook) | Reconstruir o ambiente | No perfil | Cofre/arquivo de configuração guardado à parte |
| Código (versão etiquetada + `requirements.lock`) | Reinstalar | Repositório | Repositório remoto + etiqueta da versão em produção |

## 2. Ensaio executado (`ops ensaio-recuperacao`)

Simula: o "servidor" (cópia do perfil) **perde o banco**; um ambiente **novo** é montado com
configuração e chaves vindas da guarda separada e com o backup mais recente da cópia
**secundária**. O perfil de origem não é alterado (conferido por teste).

Resultado medido nesta máquina (perfil de demonstração, após um backup automático):

| Etapa | Tempo |
|---|---|
| 1. Perda do banco simulada | 5 ms |
| 2. Configuração e chaves restauradas | 1 ms |
| 3. Banco restaurado da cópia secundária | 15 ms |
| 4. Integridade e conferências | 295 ms |
| 5. Verificações de inicialização | 119 ms |
| **Total** | **≈ 0,44 s** |

Conferido: `integrity_check` ok; contagens iguais ao manifesto do backup; fila de saída com os
mesmos estados (incluindo **UNKNOWN**, que continua sem reenvio automático, e FAILED); 3
funcionários ativos; auditoria preservada; segredos do segundo fator legíveis com a chave
recuperada; verificações do iniciador aprovadas. Perdido: tudo o que entrou **depois** do backup
usado.

Esses tempos **não** são RTO: excluem provisionar servidor, instalar sistema, DNS, VPN, proxy e
a decisão humana de acionar a recuperação.

## 3. Procedimentos por cenário

| Cenário | Passos | Testado |
|---|---|---|
| Perda do servidor/disco | Novo servidor com disco cifrado → instalar versão etiquetada → restaurar configuração e chaves da guarda → `ops restaurar` do backup externo mais recente para `dados/` → `ops ambiente` → iniciar serviços → conferir Sistema e filas | Parte local sim (ensaio); servidor novo, backup externo e DNS **não** |
| Banco corrompido | Parar serviços → `ops verificar` → `ops restaurar` em caminho novo → `ops promover --confirmacao SUBSTITUIR:<arquivo>` (guarda cópia anterior) → iniciar | Sim (Fase 10) |
| Atualização malsucedida | Migração transacional não altera o banco se falhar; voltar à versão anterior do código. Se a migração já foi aplicada: restaurar o backup tirado antes da atualização | Sim (Fase 12) |
| Erro humano | Restaurar backup anterior em caminho isolado, comparar e decidir com o responsável | Sim (restauração isolada) |
| Perda da chave de backup | Backups existentes **irrecuperáveis**. Gerar chave nova e backup imediato | Sim (recusa com chave errada) |
| Perda da chave do segundo fator | Nenhum login completa → redefinir o segundo fator de todos (via banco, procedimento técnico) e recadastrar | Parcial (falha segura 503 testada) |
| Comprometimento de chave | Rotacionar (`ops mfa-rotacionar`), novo backup com chave nova, revogar sessões | Rotação sim |

## 4. RPO e RTO

- **RPO** (perda máxima): com backup diário, até ~24 h de dados. **Proposta**, não acordada.
- **RTO** (tempo até voltar): depende de infraestrutura que não existe. **Não estimado.**
- Medições acima são do ensaio local e não substituem um ensaio no ambiente real.

## 5. Pendências

Backup externo real; guarda das chaves com duas pessoas; ensaio completo em servidor novo;
alertas automáticos; decisão de RPO/RTO pelo salão.
