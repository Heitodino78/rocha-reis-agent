# Arquitetura de pré-produção e plano de implantação futura

**Documento de planejamento.** Nada foi contratado, publicado, exposto ou configurado fora desta
máquina. Custos estão como itens **a cotar**: nenhum valor foi presumido. Proteção de dados em
`SEGURANCA_ARMAZENAMENTO.md`.

## 1. Ambientes

| Ambiente | Situação | Dados | Banco | Integrações |
|---|---|---|---|---|
| Desenvolvimento | Implementado | Sintéticos | Qualquer `*.synthetic.sqlite3` (sem marcação) | Simuladas |
| Testes automatizados | Implementado | Sintéticos | Temporários privados por teste | Simuladas |
| Demonstração | Implementado (`sh iniciar.sh`) | Fictícios | `demo.synthetic.sqlite3`, marcado `demo` | Simuladas |
| Pré-produção | **Perfil implementado, só sintético** (`ops preprod`) | Sintéticos | `*.preprod.synthetic.sqlite3`, marcado, volume cifrado verificado | Simuladas |
| Produção | Planejado (este documento) | Reais, só após `SEGURANCA_ARMAZENAMENTO.md` §6 | — | Meta e Gemini, após homologação |

## 2. Componentes

```
                Internet (Meta)                         Equipe do salão
                      │ HTTPS                                  │ VPN / acesso autenticado
              ┌───────▼────────┐                       ┌───────▼────────┐
              │ Proxy reverso  │  só /webhooks/whatsapp │  Painel (8000) │ 127.0.0.1 ou rede da VPN
              │ (TLS, limites) │──────────────┐         └───────┬────────┘
              └────────────────┘              │                 │
                                      ┌───────▼────────┐        │
                                      │ App do webhook │        │
                                      │ (127.0.0.1)    │        │
                                      └───────┬────────┘        │
                                              │  mesmo arquivo SQLite (WAL)
                                      ┌───────▼─────────────────▼──┐
                                      │ Volume CIFRADO (LUKS)      │◄── Worker: inbox → núcleo → outbox
                                      │ banco · WAL · cópias       │      (Gemini e Meta só após homologação)
                                      └───────┬────────────────────┘
                                              │ ops backup (diário, cifrado)
                                      ┌───────▼────────┐
                                      │ Armazenamento  │ fora do servidor; chave guardada à parte
                                      │ externo        │
                                      └────────────────┘
```

Os três processos (webhook, worker, painel) são separados e compartilham o banco por SQLite em WAL com
leases e idempotência. **Correção da Fase 17:** até então não havia worker CONTÍNUO (só o embutido da
demonstração e o comando que esvazia as filas uma vez); agora existe `python -m rocha_reis_agent.worker`
(systemd `Type=notify` + watchdog). Implantação concreta em `GUIA_IMPLANTACAO_SERVIDOR.md`.

## 3. Opções de hospedagem

| Critério | 1 — Servidor virtual simples (VPS Linux) | 2 — Serviços gerenciados (PaaS + banco gerenciado) | 3 — Híbrida (webhook público isolado + painel em rede privada) |
|---|---|---|---|
| Complexidade | Baixa a média; a agência administra o Linux | Média; exige trocar SQLite por PostgreSQL e adaptar processos | Média; dois pontos de implantação |
| Custo | Servidor + armazenamento externo de backups (a cotar) | Plano da aplicação + banco gerenciado + armazenamento (a cotar; normalmente maior) | Soma de duas hospedagens (a cotar) |
| Segurança | Boa se disco cifrado, firewall, só o webhook exposto, painel atrás de VPN | Boa; superfície do provedor; dados fora da máquina | Melhor isolamento do painel; mais configuração |
| Disponibilidade | Um servidor; reinício automático; restauração em novo servidor | Maior, com custo e complexidade | Igual à 1 para o atendimento |
| Manutenção | Atualizações do sistema pela agência | Menos sistema operacional, mais integração | Maior |
| Backups | `ops backup` + agendador + destino externo | Do provedor + `ops backup` | Igual à 1 |
| Observabilidade | journald + `ops metricas` + verificação externa de `/health` | Ferramentas do provedor | Igual à 1 |
| Atualização | Versão etiquetada + `requirements.lock` + reinício | Pipeline do provedor | Duas implantações |
| Escala | Suficiente para um salão (hipótese abaixo) | Escala horizontal (desnecessária hoje) | Igual à 1 |

**Recomendação:** **Opção 1** (um servidor virtual Linux pequeno com disco cifrado), expondo à
internet **apenas** o webhook por proxy reverso com HTTPS, e o painel acessível somente pela
VPN da equipe (ex.: WireGuard) ou proxy com autenticação adicional. Opção 3 só se a equipe
precisar do painel fora da VPN; Opção 2 só se houver necessidade de várias instâncias. Sem
Kubernetes ou arquitetura distribuída: não há demanda que os justifique.

**Hipóteses de volume (a confirmar com o salão):** um número de WhatsApp; dezenas a poucas
centenas de conversas por dia; 1–5 pessoas no painel ao mesmo tempo. Medição local da Fase 12
(1000 conversas, §F do `STATUS_FASE_12.md`) ficou em milissegundos por consulta — referência,
não garantia.

## 4. Exposição de serviços (nada exposto nesta fase)

| Tema | Requisito |
|---|---|
| HTTPS | Certificado válido com renovação automática no proxy; TLS 1.2+; HSTS |
| Webhook | Só `POST/GET /webhooks/whatsapp` público; assinatura `X-Hub-Signature-256` já validada no código; limite de tamanho já aplicado; limite de taxa no proxy |
| Painel | Nunca público sem controle: VPN ou proxy autenticado; HTTPS; cookie `Secure` (`PANEL_COOKIE_SECURE=true`); `PANEL_ORIGIN` com https; **segundo fator obrigatório** (implementado na Fase 13; `SEGURANCA_AUTENTICACAO.md`) |
| Referências | `deploy/caddy/`, `deploy/systemd/`, `deploy/wireguard/`, `deploy/nftables/` (Fases 13 e 17, não instalados); `X-Forwarded-*` só do proxy local e reescrito pelo Caddy (sem `trusted_proxies`) |
| Separação de rotas | App do webhook não tem rotas administrativas (já separado); painel em porta/host distintos |
| Limites | Proxy: limite de requisições por IP no webhook e no login; o painel já limita tentativas de login |
| Certificados | Gerenciados pelo proxy; nenhum certificado no repositório |

## 5. Disponibilidade e operação

| Necessidade | Planejamento |
|---|---|
| Reinício automático | Unidades `systemd` (webhook, worker, painel) com `Restart=on-failure` (referência do painel em `deploy/systemd/`) |
| Backup automático | `ops backup-agendado` + timer diário (Fase 13; `OPERACAO_BACKUP_AUTOMATICO.md`); recuperação em `PLANO_RECUPERACAO_DESASTRES.md` |
| Saúde | `/health` monitorado externamente; alerta se falhar |
| Filas | `ops metricas`: entrada pendente/falha, saída UNKNOWN/FAILED, aguardando sem responsável, tempo de processamento |
| Alertas | UNKNOWN, FAILED, `capacity_exhausted`, entrada FAILED, falhas de armazenamento, backup com mais de 26 h |
| Recuperação | Procedimentos de `SEGURANCA_ARMAZENAMENTO.md` §5 |
| Atualização controlada | Parar worker → backup → atualizar versão → abrir (migração transacional) → testes de fumaça → reativar worker |
| Rollback | Voltar à versão anterior do código; se a migração já rodou, restaurar o backup feito antes da atualização em caminho novo e promover com confirmação |

## 6. Procedimentos de operação (comandos existentes)

| Ação | Comando |
|---|---|
| Diagnóstico do ambiente (somente leitura) | `python -m rocha_reis_agent.ops ambiente --perfil DIR` |
| Indicadores | `python -m rocha_reis_agent.ops metricas --perfil DIR --backups DIR` |
| Iniciar / encerrar (local) | `python -m rocha_reis_agent.ops iniciar --perfil DIR` / Ctrl+C |
| Backup / verificar / restaurar / promover | `ops backup`, `ops verificar`, `ops restaurar`, `ops promover` |
| Retenção | `ops reter DIR --manter N [--dias D] [--confirmacao APAGAR:N]` |
| Criar pré-produção sintética | `ops preprod [--perfil DIR]` (recusa volume sem cifragem verificada) |

## 7. Plano de implantação futura (não iniciado)

| Etapa | Depende de | Critério de aprovação |
|---|---|---|
| 1. Validação do painel com a equipe (11B) | Salão | Fichas preenchidas; critérios de `PLANO_VALIDACAO_SALAO.md` §7 |
| 2. Coleta e aprovação dos dados oficiais | Salão | Formulário entregue; registros aprovados por quem o salão indicar |
| 3. Decisões do responsável pelos dados | Salão + jurídico | Itens de `DADOS_PENDENTES.md` §3 registrados |
| 4. Cotação e escolha da hospedagem | Agência + salão | Custos aprovados por escrito |
| 5. Servidor com disco cifrado, firewall, VPN, proxy | Etapa 4 | `ops ambiente` mostra cifragem verificada; só webhook exposto (varredura de portas) |
| 6. Pré-produção sintética no servidor | Etapa 5 | Testes de fumaça; backup/restauração testados lá |
| 7. Homologação Gemini (9C-B) | Autorização e chave | Critérios pré-registrados de `evals/criteria.json` |
| 8. Homologação Meta (6B) | Autorização, conta e número de teste | Checklist de `HOMOLOGACAO_META_FASE6.md` |
| 9. Abertura da trava de dados reais | Etapas 1–8 + `SEGURANCA_ARMAZENAMENTO.md` §6 | Revisão de código e autorização escrita |
| 10. Entrada em produção | Etapa 9 | Responsável aprova o início e sabe como pausar (`halt`) |

Riscos: dependência de uma pessoa da agência para operar; perda da chave de backup; servidor
único (indisponibilidade até restaurar); custos de mensagens e de IA acima do previsto.

## 8. Checklists

### 8.1 Segurança — antes de usar dados reais
Ver `SEGURANCA_ARMAZENAMENTO.md` §6 (oito itens, todos obrigatórios) e `CHECKLIST_PRODUCAO.md`.

### 8.2 Integração — antes de conectar Google Gemini
- [ ] Autorização escrita (schema 3) e projeto Google Cloud com faturamento e limites definidos.
- [ ] Chave exclusiva, com restrição de API, fora do repositório; nunca no chat.
- [ ] Patch `docs/homolog/ativacao_gemini_9c.patch` revisado (só troca a trava) e revertido ao fim.
- [ ] Execução em duas etapas da 9C-B, com livro-razão e arquivo de parada.
- [ ] Critérios de `evals/criteria.json` aprovados; Q2 (recall de encaminhamento) conferido.
- [ ] Travas da Meta fechadas durante a avaliação.

### 8.3 Integração — antes de conectar a Meta
- [ ] Conta Business verificada, app e número de **teste** autorizados por escrito.
- [ ] Webhook público só pelo proxy HTTPS; App Secret e Verify Token fora do repositório.
- [ ] Token de usuário de sistema com permissões mínimas.
- [ ] Versão da Graph API confirmada; códigos de erro validados na homologação 6B.
- [ ] Limites de envio, janela de 24 h e templates conferidos; `halt` testado.
