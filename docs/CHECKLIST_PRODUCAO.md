# Checklist de produção (nada abaixo está concluído)

**A infraestrutura de produção não existe.** Este checklist lista o que precisa ser
decidido, implementado e verificado antes de qualquer atendimento a clientes reais.
Não afirma conformidade com a LGPD: a avaliação cabe a profissionais habilitados.
Operações comerciais e criação de recursos empresariais (contas Meta, hospedagem,
contratos) exigem autorização e responsabilidade de representantes legalmente
habilitados do salão e da agência.

## Prontidão consolidada (Fase 14) — o que está comprovado e o que falta

"Comprovado" = implementado **e** testado localmente (dados fictícios). Nada foi implantado.

| Área | Comprovado localmente | Falta (depende de quem) |
|---|---|---|
| Servidor / sistema operacional | Pacote reprodutível, instalação em árvore Linux, atualização/reversão testadas (Fase 17); **unidades executadas no systemd real do usuário** com todo o endurecimento, `ops atualizar` controlando o systemd (Fase 18) | Contratar e instalar (agência + aprovação de custo pelo salão); 18B: `GUIA_IMPLANTACAO_SERVIDOR.md` §7 (usuário dedicado, systemd do sistema, `reboot`) |
| Disco cifrado | Verificação automática de dm-crypt/LUKS; pré-produção recusa volume sem cifragem | Disco cifrado no servidor (agência) |
| HTTPS / domínio / proxy | HTTPS com certificado de desenvolvimento; HSTS; cookie `__Host-`/Secure; **Caddy 2.11.9 real testado em laboratório** (validação, TLS 1.3, SSE, caminhos, XFF, limites, logs) (Fase 18) | Domínio, certificado público (ACME), Caddy instalado no servidor (agência; salão aprova domínio) |
| VPN do painel | **WireGuard do kernel testado em laboratório** (handshake, chave desconhecida, isolamento, revogação imediata, recuperação, 2º fator pela VPN) + firewall corrigido (IP da VPN só pelo túnel) (Fase 18) | Instalar no servidor, gerar chaves lá e distribuir acessos (agência) |
| Banco | SQLite com migrações transacionais, ambientes marcados, dados reais bloqueados | Decidir hospedagem; manter SQLite enquanto houver um servidor |
| Monitoramento | Indicadores locais + `ops alertas` (0/1/2) com timer de referência; `/ready`; batimento do worker (Fase 17); unidade de alertas **corrigida e exercitada com os serviços reais**; 22 códigos com teste; alertas de backup/restauração com falha (Fase 18) | Canal de aviso a pessoas e verificação externa (agência) |
| Segundo fator | TOTP, recuperação, redefinição auditada, obrigatório em pré-produção | Equipe cadastrar (11B / implantação) |
| Credenciais / segredos | Arquivos 0600 fora do banco/backups; rotação da chave do 2º fator | Cofre/arquivos no servidor; custódia (salão + agência) |
| Backup externo | Rodada automática, verificação, cópia secundária **simulada**, teste de restauração; interface de destino externo testada com adaptador FALSO (Fase 17) | Contratar destino externo real e escrever o adaptador; timer instalado (agência) |
| Rotação de chaves | Comando testado (2º fator); procedimento documentado (backup) | Política e calendário (salão + agência) |
| Acesso administrativo | RBAC no backend, auditoria | Lista real de administradores (salão) |
| Recuperação | Ensaio local isolado; ensaio operacional com queda total e restauração em instância nova (Fase 17) — não é RTO | Ensaio no servidor real; RPO/RTO decididos (salão) |
| Responsáveis / suporte / incidentes | Procedimentos documentados | Nomes, horários e canal de suporte (salão + agência) |
| Retenção / exclusão | Mecanismos configuráveis | Decisão jurídica (salão + jurídico) |
| Conta Google / projeto Gemini | Adaptador bloqueado; avaliação offline (Q2 reprovado com regras) | Conta, faturamento, chave, homologação 9C-B (salão autoriza; agência executa) |
| Meta Business / WhatsApp Cloud API / número / webhook | Adaptador bloqueado; webhook assinado testado offline | Conta verificada, número, app, homologação 6B (salão + agência) |
| Modelos (templates) de mensagem | Não usados (só respostas dentro da janela de 24 h) | Avaliar se haverá mensagens fora da janela (salão) |
| Política de privacidade / aviso ao cliente | — | Redação e aprovação jurídica (salão) |
| Validação pela equipe (11B) | Materiais completos | Sessão com funcionários (salão) |
| Dados oficiais | Formulário e campos estruturados | Preenchimento e aprovação (salão) |

## Dados pessoais e transparência
- [ ] Inventário dos dados tratados: identificador WhatsApp (telefone/BSUID), texto das mensagens, horários, estados de atendimento, ações de funcionários.
- [ ] Finalidade e base legal definidas pelo controlador (com assessoria jurídica).
- [ ] Aviso ao cliente de que o atendimento é automatizado, com opção de falar com uma pessoa.
- [ ] Política de privacidade publicada e revisada.
- [ ] Contrato/acordo de tratamento entre salão e agência; responsabilidades definidas.

## Retenção e exclusão
- [ ] Prazos aprovados: conversas ativas, anonimização (`STORAGE_RETENTION_DAYS`), auditoria, chaves de idempotência (hoje sem prazo).
- [ ] Decisão sobre novo contato após exclusão a pedido (ver `OPERACAO_HUMANA_REQUISITOS.md` §5). *(Fase 10: mecanismo configurável implementado — padrão "bloquear e avisar a administração", alternativa "permitir nova conversa" e reativação individual auditada; **a decisão continua pendente**.)*
- [ ] Execução agendada e monitorada da retenção (hoje manual, com confirmação).
- [ ] Procedimento de atendimento a pedidos de titulares (acesso, correção, exclusão).

## Acesso e segredos
- [ ] Autenticação individual forte de operadores e papéis. *(Fase 7: Argon2id + RBAC; Fase 13: segundo fator TOTP com recuperação e redefinição auditada, obrigatório em pré-produção — testado localmente; falta HTTPS real e uso pela equipe.)*
- [ ] Gerenciador de segredos para App Secret, Verify Token, token de acesso e chave de pseudonimização; rotação documentada.
- [ ] Token Meta de usuário de sistema com permissões mínimas; nunca token temporário.
- [ ] Acesso ao servidor e ao banco restrito e auditado.

## Criptografia
- [ ] Em trânsito: HTTPS com certificado válido (webhook e futura interface); TLS verificado no cliente Graph (já no código).
- [ ] Em repouso: volume cifrado (LUKS) ou serviço gerenciado com cifragem. *(Fase 12: a aplicação **verifica** dm-crypt pelo kernel e pode exigi-lo — obrigatório em pré-produção; nesta máquina o volume do banco é LUKS2 verificado. No servidor futuro ainda não existe.)* Sem criptografia caseira.

## Backup e restauração
- [ ] Backup online (API `sqlite3.Connection.backup` ou snapshot do serviço gerenciado) para destino cifrado, com retenção definida. *(Fase 10: comando local `ops backup` com API online, AES-256-GCM, manifesto autenticado, chave separada; **faltam** agendamento, cópia fora da máquina, retenção aprovada e guarda da chave.)*
- [ ] Teste periódico de restauração com `PRAGMA integrity_check`, verificação de FK e das chaves de idempotência. *(Fase 10: `ops verificar`/`ops restaurar` fazem essas verificações em cópia isolada; falta rotina periódica.)*
- [ ] Registro de quem restaura; backups com o mesmo controle de acesso dos dados. *(Hoje a promoção exige frase de confirmação e guarda cópia anterior, mas não há registro de quem a executou fora do sistema operacional.)*
- [ ] Banco ativo cifrado em repouso **no ambiente de produção**. *(O banco não é cifrado pela aplicação; a proteção vem do volume, verificada por `ops ambiente`.)*
- [ ] Backup agendado (diário) para fora do servidor e teste de restauração periódico registrado. *(Fase 13: `ops backup-agendado`, `ops teste-restauracao`, timer de referência, indicadores no painel; destino externo real e instalação do timer pendentes.)*
- [ ] Ensaio de recuperação no ambiente real. *(Fase 13: ensaio local isolado executado — `PLANO_RECUPERACAO_DESASTRES.md`.)*
- [ ] Custódia das chaves (backup e segundo fator) com duas pessoas, fora do servidor.
- [ ] Proxy HTTPS real (Caddy) com certificado público, painel só na VPN, limite de taxa no webhook. *(Referência em `deploy/`; não instalado.)*
- [ ] RPO/RTO aprovados (propostas em `SEGURANCA_ARMAZENAMENTO.md` §5).
- [ ] Avaliar migração para PostgreSQL (vários escritores, réplicas) antes de volume real.

## Arquitetura de implantação
- [ ] Decisão sobre a arquitetura recomendada em `STATUS_FASE_11.md` §F (servidor pequeno com disco cifrado, só o webhook público, painel atrás de VPN), após cotação de custos.

## Operação e monitoramento
- [ ] Worker contínuo supervisionado (reinício automático, um processo por função).
- [ ] Alertas: UNKNOWN, FAILED, `capacity_exhausted`, `dispatch_blocked_*`, anomalias de status, webhook rejeitado em excesso.
- [ ] Painel de métricas a partir de `local_cli metrics` (sem PII) ou equivalente.
- [ ] Plano de resposta a incidentes: quem decide, `halt`, comunicação, registro.
- [ ] Procedimento de desligamento do agente conhecido pelo salão.

## Custos
- [ ] Limites de mensagens/mês e alerta. Mensagens de serviço são cobradas desde 01/10/2026 após 1.000/mês por número (conferir tabela vigente).
- [ ] Orçamento aprovado para IA real (quando autorizada), com limite por conversa. *(Fase 8: limites diários de requisições e de custo estimado implementados; valores pendentes.)*
- [ ] Tarifas e capacidades do modelo **Gemini** reconfirmadas na fonte oficial e `ai/models.json` (registro verificado) atualizado com data; reajustes programados considerados.
- [ ] Projeto Google Cloud com faturamento (nível pago), chave com restrição de API, DPA aceito pelo responsável legal.
- [ ] Homologação real com Gemini aprovada nos critérios pré-registrados de `evals/criteria.json`.

## Produto
- [ ] Dados oficiais do salão aprovados (serviços, preços, horários, endereço, profissionais, políticas, link Booksy). *(Fase 11: formulário `COLETA_DADOS_OFICIAIS.md` pronto; nada recebido.)*
- [ ] Ambiente de pré-produção para a base OFICIAL. *(Fase 12: perfil de pré-produção implementado e isolado, **só sintético**; dados reais bloqueados pela quarta trava `REAL_DATA_ACTIVATION_AUTHORIZED`.)*
- [ ] Validação do painel com a equipe (Fase 11B, `PLANO_VALIDACAO_SALAO.md`) com fichas preenchidas pelos participantes.
- [ ] Teste de acessibilidade com leitor de tela e com pessoas (hoje: verificações automatizadas simples, incluindo contraste AA).
- [ ] Textos finais aprovados (sem rótulos de simulação).
- [ ] Operação humana real. *(Fase 7: painel local implementado e testado; Fase 10: avisos internos ao painel, urgência e manual da equipe; faltam notificação fora do painel, validação com a equipe e implantação segura.)*
- [ ] Homologação Meta (Fase 6B) concluída com evidências.
- [ ] Avaliação de modelo generativo real (se adotado) com testes adversariais. *(Fase 8: módulo `evaluation` pronto; executado só com provedores simulados.)*
- [ ] Condições de `IA_GENERATIVA.md` §7 cumpridas (autorização, LGPD, segredo, trava de código revisada).
- [ ] Retenção de dados no provedor de IA e transferência internacional avaliadas juridicamente.
- [ ] Aprovação explícita de go-live pelo responsável.
