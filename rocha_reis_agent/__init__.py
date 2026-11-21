"""Rocha Reis Agent: fundação HTTP e simulador determinístico local."""

# Fase 14: identificação da versão candidata. NÃO é versão de produção.
# 0.14.0rc1.post1 (Fase 15): mesma RC1 + correções de demonstração (aviso "PRONTO" só após o
# servidor subir; credenciais certas por perfil) e ferramentas de homologação. Ver RELATORIO_PRONTIDAO_RC1.md.
# 0.14.0rc1.post2 (Fase 16): redesign do painel (sistema de design, temas, responsividade) e correções
# da auditoria (avisos ao entrar, tabela da equipe, cache dos arquivos do painel, gaveta do menu).
# 0.14.0rc1.post3 (revisão final): "Meus atendimentos" só com atendimentos em andamento; desativar devolve
# os atendimentos à fila; histórico com posições estáveis; redefinição de senha no painel.
# 0.14.0rc1.post4 (Fase 17): worker contínuo, prontidão, empacotamento/instalação/atualização/reversão,
# alertas, cópia externa simulada, sessões sem bloqueio de escrita e configurações de servidor endurecidas.
# 0.14.0rc1.post5 (Fase 18): correções achadas com systemd, Caddy, WireGuard e nftables REAIS — RUNTIME.json com o
# caminho final do venv (atualização real era sempre cancelada), chmod sem seguir o link do Python base, reversão
# que nunca volta para a versão que falhou, alertas que abrem o banco em WAL, LimitCORE=0, fronteira da VPN no
# firewall, rajada explícita, log do Caddy sem IP completo e alertas de backup/restauração com falha.
__version__ = "0.14.0rc1.post5"
RELEASE_NAME = "Rocha Reis Agent — RC1 Offline"
