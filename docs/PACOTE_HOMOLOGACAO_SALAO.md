# Pacote de homologação — Salão Rocha Reis (RC1 Offline)

**Para:** Heitor e Renan (agência), ao apresentar e validar o sistema com o salão.
**Versão:** Rocha Reis Agent — RC1 Offline `0.14.0rc1.post2` (confira com `ops verificar-rc`).
Este documento é o índice: cada item aponta o material completo.

## 1. O que é o Rocha Reis Agent (apresentação honesta)

Um assistente para o WhatsApp do salão, com um painel para a equipe.
- **Responde sozinho** perguntas simples (preços, endereço, horários, serviços) **somente** com
  informações aprovadas pelo salão; quando não sabe, diz que não sabe e oferece uma pessoa.
- **Agendamento só pelo Booksy:** envia o link oficial; nunca marca, confirma ou promete horário.
- **Passa para a equipe** quando o cliente pede uma pessoa, reclama ou fala de saúde — e para de
  responder sozinho até a administração liberar.
- **Painel da equipe:** fila, assumir, responder, transferir, finalizar, avisos, histórico,
  informações do salão com aprovação, auditoria, verificação em duas etapas, backups cifrados.

### O que AINDA é simulado (dizer sempre)

| Parte | Hoje | Para ser real |
|---|---|---|
| WhatsApp | Simulado; nada é enviado | Homologação Meta (6B) e autorização |
| Inteligência artificial | Regras fixas (o Google Gemini está pronto, **bloqueado** e **não** testado) | Homologação Gemini (9C-B) |
| Dados do salão | Inventados ("[SIMULAÇÃO — DADOS FICTÍCIOS]") | Formulário preenchido e aprovado |
| Servidor | Só neste computador | Hospedagem com disco cifrado, HTTPS e VPN |
| Clientes | Fictícios | Requisitos de privacidade e trava de dados reais |

Não use expressões como "pronto para produção", "IA treinada" ou "integrado ao WhatsApp".

## 2. Demonstração

`ROTEIRO_DEMONSTRACAO_SALAO.md` (30–40 min). Comandos essenciais:
`sh iniciar.sh --nova-demo` (espere a linha **PRONTO** com o endereço e os logins fictícios) ·
`sh iniciar.sh --mensagem "texto"` (cliente fictício) · Ctrl+C para encerrar.

## 3. Treinamento

`TREINAMENTO_RAPIDO.md` (15 min, atendentes e administração) e `MANUAL_PAINEL.md` (consulta).

## 4. Homologação (31 cenários)

`PLANO_VALIDACAO_SALAO.md` (17 para atendentes, 14 para a administração, critérios propostos) e
`CHECKLIST_HOMOLOGACAO_11B.md` (antes/durante/depois). Registro dos resultados:

```sh
python -m rocha_reis_agent.ops verificar-rc                       # mesmo código da candidata?
python -m rocha_reis_agent.ops registro-11b --destino ~/homologacao-11b/sessao-AAAA-MM-DD.csv
python -m rocha_reis_agent.ops conferir-registro ~/homologacao-11b/sessao-AAAA-MM-DD.csv
```

A planilha nasce **em branco**; só o observador preenche, na hora, com o participante. Testes
automáticos aprovados **não** contam como aprovação de cenário humano.

## 5. Dados oficiais

`COLETA_DADOS_OFICIAIS.md` — entregar ao responsável; conferir completude e contradições e
**devolver** o que faltar antes de qualquer cadastro. Nada é importado para a demonstração.

## 6. Decisões

`DECISOES_PENDENTES_SALAO.md` — 14 decisões com opções, riscos, recomendação, quem decide e data.

## 7. Limitações conhecidas

- Assistente por regras ainda não entende pedidos **indiretos** ("tem como alguém me ajudar?") nem
  algumas gírias de preço — avaliação offline 123/129, reconhecimento de pedido humano 90% (meta 95%).
  Depende da IA definitiva.
- Painel só no computador local; celular real só pelo procedimento opcional por cabo (Android de teste).
- Sem QR code no cadastro das duas etapas (a chave é digitada).
- Acessibilidade verificada só de forma automática.

## 8. Próximas etapas

| Etapa | Quem |
|---|---|
| Sessão 11B e registro dos resultados | Agência conduz; equipe do salão participa |
| Corrigir o que a 11B apontar (Críticos/Altos) | Agência |
| Formulário de dados preenchido e conferido | Salão preenche; agência confere |
| 14 decisões | Responsável pelo salão (+ jurídico onde indicado) |
| Cotação e escolha da hospedagem | Agência propõe; salão aprova custo |
| Homologação Gemini e Meta | Salão autoriza; agência executa |
| Produção | Somente após os itens de `CHECKLIST_PRODUCAO.md` |

Situação técnica completa: `RELATORIO_PRONTIDAO_RC1.md`.
