# Avaliação documental de modelos — Google Gemini (Fase 9B)

> **Google Gemini é o provedor oficial do Rocha Reis Agent.** Esta avaliação é
> **documental**: nenhuma chamada à API Gemini foi feita. Qualidade, latência e custo
> reais só serão conhecidos na homologação real, que exige autorização
> ([`HOMOLOGACAO_IA_FASE9.md`](HOMOLOGACAO_IA_FASE9.md)). A avaliação Anthropic da Fase 9A
> está no apêndice como histórico.

## 1. Fontes consultadas (20/11/2026)

`ai.google.dev/gemini-api/docs/*` redireciona (301) para `aistudio.google.com/docs/*`.

| Fonte | Conteúdo usado | Forma |
|---|---|---|
| aistudio.google.com/docs/models | Índice: estáveis, previews, descontinuados, desligados | Verbatim |
| …/docs/models/gemini-3.1-flash-lite, gemini-3.5-flash-lite, gemini-3.8-flash, gemini-3-flash-preview, gemini-3.1-pro-preview | Limites de tokens, recursos (saída estruturada, raciocínio, cache, batch), data de atualização | Verbatim |
| …/docs/pricing | Tabela Standard paga e gratuita; "Used to improve our products" | Verbatim para 3.1/3.5 Flash-Lite e 3.8 Flash; **resumo** para 2.5 Flash-Lite, 3.1 Pro e 3 Flash preview |
| …/docs/thinking | Níveis de raciocínio por modelo, raciocínio cobrado como saída | Verbatim |
| …/docs/rate-limits | Dimensões (RPM, TPM, RPD), limites de gasto por nível, 429 `RESOURCE_EXHAUSTED` | Verbatim. Valores por modelo só no AI Studio autenticado (**não lidos**) |
| …/docs/structured-output | Recursos de JSON Schema aceitos | Página atual fala da Interactions API |
| …/docs/terms | Dados no nível gratuito × pago, idade, regiões, uso médico vedado | Verbatim, "Effective March 23, 2026" |
| README do `googleapis/python-genai` + **código do SDK instalado (3.6.1)** | `generate_content`, `response_json_schema`, `HttpOptions` (timeout em ms, `retry_options`, `httpx_client`), `errors.APIError/ClientError/ServerError`, `usage_metadata`, `model_version`, leitura automática de `GEMINI_API_KEY`/`GOOGLE_API_KEY` | Verbatim + inspeção do pacote |

## 2. Modelos registrados (`rocha_reis_agent/ai/models.json`)

Critério para registrar um modelo: estável, com saída estruturada, e tarifa lida em texto
**verbatim**.

| | Gemini 3.1 Flash-Lite | Gemini 3.5 Flash-Lite | Gemini 3.8 Flash |
|---|---|---|---|
| ID | `gemini-3.1-flash-lite` | `gemini-3.5-flash-lite` | `gemini-3.8-flash` |
| Situação | Estável (atualização maio/2026) | Estável (julho/2026) | Estável (setembro/2026) |
| Entrada / saída máx. | 1.048.576 / 65.536 tokens | idem | idem |
| Saída estruturada | Sim | Sim | Sim |
| Raciocínio | minimal (padrão), low, medium, high | minimal (padrão), low, medium, high | low, medium (padrão), high — **minimal recusado** |
| Entrada / saída US$/MTok (pago Standard) | 0,25 / 1,50 | 0,30 / 2,50 | 0,75 / 3,75 até 31/12/2026; **1,50 / 7,50 a partir de 01/01/2027** |
| Cache (leitura) | 0,025 | 0,03 | 0,075 → 0,15 |
| Faixa por tamanho de prompt | Não | Não | Não |
| Nível gratuito | Sim (conteúdo **usado** para melhorar produtos) | Sim (idem) | Sim (idem) |

**Observação de custo:** o 3.5 Flash-Lite, mais novo, é **mais caro** que o 3.1 Flash-Lite
(saída 2,50 contra 1,50). A família não indica preço: o registro guarda a tarifa de cada
ID.

## 3. Não registrados (pendências de verificação)

| Modelo | Motivo |
|---|---|
| `gemini-2.5-flash-lite` | Ativo "até novo aviso" e possivelmente o mais barato (US$ 0,10 / 0,40), mas a tarifa veio de resumo e a página do modelo não foi lida. Verificar antes de usar |
| `gemini-3.1-pro-preview` (Pro) | Preview (pode ser descontinuado com 2 semanas de aviso), sem nível gratuito, tarifa por faixa (≤ 200K / > 200K) obtida de resumo. Só se a avaliação mostrar que Flash-Lite/Flash não bastam |
| `gemini-3-flash-preview` | Preview de dez/2025, substituído pelos Flash estáveis |
| `gemini-3.6-flash` | Estável no índice, página do modelo não lida |
| `gemini-2.5-flash`, `gemini-2.5-pro` | Descontinuados (redirecionados a outros modelos a partir de 01/12/2026) |
| `gemini-4-*` | Família nova, fora das categorias pedidas; status não informado no índice |

## 4. Matriz de comparação (documental, para a TAREFA deste projeto)

O modelo classifica mensagens curtas em JSON fechado, e o backend monta todos os fatos.
Escala: ●●● favorável, ●● intermediário, ● desfavorável.

| Critério | 3.1 Flash-Lite | 3.5 Flash-Lite | 3.8 Flash | Observação |
|---|---|---|---|---|
| 1. Qualidade de classificação | ●● | ●● | ●●● | Flash tem mais capacidade; **só a medição dirá** se Flash-Lite basta |
| 2. Português brasileiro | ? | ? | ? | Documentação não traz dado por idioma; medir com os 120 cenários |
| 3. Saída estruturada | ●●● | ●●● | ●●● | Todos listam structured outputs |
| 4. Velocidade | ●●● | ●●● | ●● | Flash-Lite com raciocínio `minimal` por padrão; o 3.8 Flash não aceita minimal |
| 5. Custo | ●●● | ●● | ● | Ver §5; o 3.8 Flash dobra em 01/01/2027 |
| 6. Limites de tokens | ●●● | ●●● | ●●● | 1M de entrada; pedido real ≈ 2,6 mil caracteres |
| 7. Limites de requisição | ? | ? | ? | Por modelo só no AI Studio autenticado; limite de gasto de US$ 10 por 10 min no Tier 1 |
| 8. Estabilidade | ●●● | ●●● | ●●● | Estáveis; o mais antigo (3.1) pode ser o primeiro a ser substituído |
| 9. Tratamento de erros | ●●● | ●●● | ●●● | Mesmo SDK e mesmos códigos |
| 10. Política de dados | ●● | ●● | ●● | Igual entre modelos; depende de conta **paga** (§6) |
| 11. Compatibilidade com o SDK | ●●● | ●●● | ●●● | Mesmo `generate_content` |
| 12. Região/tipo de conta | ●● | ●● | ●● | Brasil consta como região disponível; uso profissional, 18+ |

## 5. Custos estimados (premissas explícitas, NÃO medições)

Pedido real do adaptador (medido na Fase 9A, tamanho semelhante com v4): ≈ 2,6 mil
caracteres. Premissa de ≈ 3,5 caracteres/token, ou seja, ≈ 760 tokens de entrada. Saída
de ≈ 150 tokens de JSON mais raciocínio, **desconhecido**; por isso há dois cenários: 300
e 1.500 tokens de saída.

| Modelo | Por mensagem (300 saída) | Por mensagem (1.500 saída) | 1.000 mensagens |
|---|---|---|---|
| 3.1 Flash-Lite | ≈ US$ 0,00064 | ≈ US$ 0,0024 | US$ 0,64 – 2,44 |
| 3.5 Flash-Lite | ≈ US$ 0,00098 | ≈ US$ 0,0040 | US$ 0,98 – 3,98 |
| 3.8 Flash (2026) | ≈ US$ 0,0017 | ≈ US$ 0,0062 | US$ 1,70 – 6,20 |
| 3.8 Flash (2027) | ≈ US$ 0,0034 | ≈ US$ 0,0124 | US$ 3,39 – 12,39 |

Avaliação sintética simulada (tokens **sintéticos**, `evals/results/phase9b-*`): US$ 0,08
(3.1 FL), 0,11 (3.5 FL) e 0,22 (3.8 Flash) por execução completa. Isso mede o adaptador,
não o consumo real.

## 6. Privacidade e termos (resumo; detalhes em `IA_GENERATIVA.md` §5)

- **Nível gratuito: inadequado.** O conteúdo é usado para melhorar produtos do Google,
  revisores humanos podem lê-lo, e há a orientação de não enviar dados pessoais.
- **Nível pago** (projeto Cloud com faturamento): o conteúdo não é usado para treino; vale
  o *Data Processing Addendum*. Há retenção limitada para abuso, mas **o número de dias não
  consta dos termos lidos**. Os dados podem ser processados em qualquer país.
- Uso por **maiores de 18 anos**, profissional; vedado conselho médico; apps para menores
  vedados. A conta, a aceitação dos termos e a contratação são do responsável legal.

## 7. Recomendação provisória

1. **Piloto da homologação real: `gemini-3.1-flash-lite`** (raciocínio `minimal`). É o
   mais econômico **verificado**, estável, com saída estruturada. Comparar com
   `gemini-3.5-flash-lite` e usar `gemini-3.8-flash` (`low`) como referência de qualidade.
2. **Hipótese a testar, não escolha:** Flash-Lite pode bastar porque a tarefa é
   classificação fechada. O critério decisivo é Q2 (recall de encaminhamento humano
   ≥ 0,95), que o detector sozinho não atinge (0,947).
3. Um modelo mais caro só com ganho demonstrável nos critérios pré-registrados. **Se
   nenhum passar, não ativar** a IA real.
4. **Não fixar modelo padrão** antes da homologação (`AI_MODEL` não tem default).

## Apêndice — histórico substituído (Fase 9A, Anthropic)

Em 20/11/2026 a Fase 9A avaliou documentalmente Claude Opus 5.5, Sonnet 5.5 e Haiku 5.5 e
recomendou o Haiku 5.5 como candidato. Essa avaliação foi substituída pela decisão de usar
Google Gemini. Os arquivos `evals/results/phase9-anthropic-simulated-*.json` permanecem só
como evidência histórica do backend; não valem para o Gemini.
