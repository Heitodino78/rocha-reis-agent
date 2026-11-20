# Fixtures sintéticas de webhook Meta

Envelopes no formato do webhook `messages` do WhatsApp Cloud API, com números,
IDs e nomes **fictícios**. `"timestamp": "NOW"` é substituído pela CLI local
(`python -m rocha_reis_agent.channels.local_cli post ...`) pelo horário atual antes
de assinar, para que a janela de atendimento de 24h seja realista.

Não contêm dados reais e não devem ser enviados a nenhum endpoint externo.
