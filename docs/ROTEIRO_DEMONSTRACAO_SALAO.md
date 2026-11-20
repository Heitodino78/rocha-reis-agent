# Roteiro de apresentação — Rocha Reis Agent (RC1 Offline)

**Para quem:** a pessoa da agência que apresenta o sistema à equipe do Salão Rocha Reis.
**Duração sugerida:** 30–40 minutos, antes da validação (`PLANO_VALIDACAO_SALAO.md`).
**Regra de ouro:** tudo o que aparece é **fictício** — nada foi enviado pelo WhatsApp de verdade e
nenhuma inteligência artificial real foi usada. Diga isso logo no começo e repita quando perguntarem.

## Antes de começar (5 min, sem a equipe)

1. `python -m rocha_reis_agent.ops verificar-rc` → `"identica": true`. Depois `sh iniciar.sh --nova-demo`
   e esperar a linha **PRONTO** (mostra o endereço e os logins fictícios). Abrir o endereço e
   conferir a faixa amarela **DEMONSTRAÇÃO**.
2. Opcional, para mostrar que está tudo certo: `python -m rocha_reis_agent.ops roteiro` (num perfil
   separado) deve terminar com **13/13 OK**.
3. Deixar um segundo terminal aberto para `sh iniciar.sh --mensagem "..."`.

## 1. Introdução (3 min)

"Este é o assistente de atendimento que estamos preparando para o WhatsApp do salão. Hoje vocês
vão ver uma **versão de teste**, com clientes e preços inventados. Nada do que acontecer aqui
chega a clientes de verdade."

## 2. Para que serve (3 min)

- Responder sozinho as dúvidas simples, 24 horas: preços, endereço, horários, serviços.
- **Nunca inventar**: se a informação não foi aprovada pelo salão, ele diz que não sabe e oferece
  uma pessoa.
- Agendamento continua **só pelo Booksy**: o assistente manda o link; não marca nem confirma horário.
- Quando o cliente pede uma pessoa, reclama ou fala de saúde, ele **para de responder** e chama a equipe.

## 3. Atendimento automático (6 min)

Mostre as conversas automáticas já criadas e, ao vivo, use o segundo terminal:

| Mostrar | Comando | O que dizer |
|---|---|---|
| Preço aprovado | `sh iniciar.sh --mensagem "Quanto custa um corte?"` | "Ele só responde o que foi aprovado." |
| Preço "a partir de" | `... --mensagem "Quanto custa a coloração?"` | "Quando o preço varia, ele avisa que é valor inicial." |
| Informação ausente | `... --mensagem "Quanto custa a escova?"` | "Sem preço aprovado, ele não chuta." |
| Agendamento | `... --mensagem "Quero marcar sábado"` | "Só o link do Booksy; nunca confirma horário." |

(A resposta aparece em "Automáticas" em poucos segundos. Os textos começam com
"[SIMULAÇÃO — DADOS FICTÍCIOS]".)

## 4. Painel da equipe (10 min)

Entre como **ana.demo**:
1. Visão geral → "Precisa de atenção".
2. `... --mensagem "Quero falar com uma pessoa"` → o **sino** acende.
3. Abrir, **Assumir**, responder. Mostrar "Aceita pela plataforma (envio simulado)".
4. **Transferir** para a Bia; **Finalizar**; ler o texto acima do campo: só a administração religa
   o assistente.
5. Mostrar uma falha (login bia.demo) e o envio **incerto** (admin): "Incerto quer dizer que pode ter
   chegado — não reenviem às cegas."

## 5. Informações do salão (5 min)

Entre como **admin.demo** → Conhecimento: rascunho → **Ver prévia** (simulada) → **Aprovar**.
Explicar: "Nada vale até alguém da administração aprovar. As informações reais virão do formulário
que vamos entregar (`COLETA_DADOS_OFICIAIS.md`)."

## 6. Segurança (3 min)

- Cada pessoa tem o seu acesso; atendente não vê administração (mostrar com ana.demo).
- Verificação em duas etapas (código do celular), obrigatória quando for para valer.
- Tudo fica registrado na Auditoria; backups criptografados.
- Sistema → quatro travas **bloqueadas**: WhatsApp real, Gemini real e dados reais estão desligados.

## 7. Rotina (2 min)

Quem abre o painel de manhã; como ver a fila; o que fazer com falha/incerto; como pedir suporte
(administração → agência). Detalhes no `TREINAMENTO_RAPIDO.md`.

## 8. Limitações — dizer com clareza (2 min)

- Ainda **não** está ligado ao WhatsApp nem à inteligência artificial de verdade.
- As respostas automáticas atuais são por regras; algumas frases indiretas ("tem como alguém me
  ajudar?") ainda **não** são entendidas — isso depende da IA definitiva, que será testada depois.
- Preços, endereço e serviços são inventados até o salão entregar e aprovar os oficiais.
- Não está publicado na internet; só funciona neste computador.

## 9. Perguntas da equipe (5 min)

Anote tudo no registro de problemas do `PLANO_VALIDACAO_SALAO.md` §5. Não prometa funcionalidades
na hora: pedidos novos são avaliados depois.

## Encerrar

Ctrl+C no terminal do painel. Para o próximo grupo: `sh iniciar.sh --nova-demo`.
