# Checklist de produção (nada abaixo está concluído)

**A infraestrutura de produção não existe.** Este checklist lista o que precisa ser
decidido, implementado e verificado antes de qualquer atendimento a clientes reais.
Não afirma conformidade com a LGPD: a avaliação cabe a profissionais habilitados.
Operações comerciais e criação de recursos empresariais (contas Meta, hospedagem,
contratos) exigem autorização e responsabilidade de representantes legalmente
habilitados do salão e da agência.

## Dados pessoais e transparência
- [ ] Inventário dos dados tratados: identificador WhatsApp (telefone/BSUID), texto das mensagens, horários, estados de atendimento, ações de funcionários.
- [ ] Finalidade e base legal definidas pelo controlador (com assessoria jurídica).
- [ ] Aviso ao cliente de que o atendimento é automatizado, com opção de falar com uma pessoa.
- [ ] Política de privacidade publicada e revisada.
- [ ] Contrato/acordo de tratamento entre salão e agência; responsabilidades definidas.

## Retenção e exclusão
- [ ] Prazos aprovados: conversas ativas, anonimização (`STORAGE_RETENTION_DAYS`), auditoria, chaves de idempotência (hoje sem prazo).
- [ ] Decisão sobre novo contato após exclusão a pedido (ver `OPERACAO_HUMANA_REQUISITOS.md` §5).
- [ ] Execução agendada e monitorada da retenção (hoje manual, com confirmação).
- [ ] Procedimento de atendimento a pedidos de titulares (acesso, correção, exclusão).

## Acesso e segredos
- [ ] Autenticação individual forte de operadores e papéis. *(Fase 7: login individual Argon2id + RBAC implementados localmente; faltam 2FA/SSO e HTTPS.)*
- [ ] Gerenciador de segredos para App Secret, Verify Token, token de acesso e chave de pseudonimização; rotação documentada.
- [ ] Token Meta de usuário de sistema com permissões mínimas; nunca token temporário.
- [ ] Acesso ao servidor e ao banco restrito e auditado.

## Criptografia
- [ ] Em trânsito: HTTPS com certificado válido (webhook e futura interface); TLS verificado no cliente Graph (já no código).
- [ ] Em repouso: volume cifrado (LUKS) ou serviço gerenciado com cifragem. **Não implementado.** Sem criptografia caseira.

## Backup e restauração
- [ ] Backup online (API `sqlite3.Connection.backup` ou snapshot do serviço gerenciado) para destino cifrado, com retenção definida.
- [ ] Teste periódico de restauração com `PRAGMA integrity_check`, verificação de FK e das chaves de idempotência.
- [ ] Registro de quem restaura; backups com o mesmo controle de acesso dos dados.
- [ ] Avaliar migração para PostgreSQL (vários escritores, réplicas) antes de volume real.

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
- [ ] Dados oficiais do salão aprovados (serviços, preços, horários, endereço, profissionais, políticas, link Booksy).
- [ ] Textos finais aprovados (sem rótulos de simulação).
- [ ] Operação humana real. *(Fase 7: painel local implementado e testado; falta notificação externa, validação com a equipe e implantação segura.)*
- [ ] Homologação Meta (Fase 6B) concluída com evidências.
- [ ] Avaliação de modelo generativo real (se adotado) com testes adversariais. *(Fase 8: módulo `evaluation` pronto; executado só com provedores simulados.)*
- [ ] Condições de `IA_GENERATIVA.md` §7 cumpridas (autorização, LGPD, segredo, trava de código revisada).
- [ ] Retenção de dados no provedor de IA e transferência internacional avaliadas juridicamente.
- [ ] Aprovação explícita de go-live pelo responsável.
