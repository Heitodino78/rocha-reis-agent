# Relatório de prontidão — RC1 Offline para a homologação 11B

**Data:** 20/11/2026. **Conclusão:** a RC1 está **pronta para entrar na homologação 11B com
funcionários reais**. Isso **não** significa aprovação pelo salão (ainda não houve sessão) nem
prontidão para produção.

## 1. Candidata congelada

| Campo | Valor |
|---|---|
| Versão | Rocha Reis Agent — RC1 Offline `0.14.0rc1.post1` |
| Commit base | `c59b934` (`main`) — **árvore não versionada** (80 caminhos sem commit) |
| Impressão digital | `92597a901d64a153` (278 arquivos) |
| Manifesto | `docs/homologacao/RC1_MANIFESTO.json` (hash de cada arquivo; congelado em 2026-11-20T21:58:15Z) |
| Esquema do banco | 11 (migrações 001–011, hashes no manifesto) |
| Travas | Meta Graph, Meta homologação, Gemini real, dados reais: **todas `False`** |
| Demonstração | Perfil isolado `demo` (dados, contas e segredos fictícios gerados a cada criação; 2FA opcional) |

**Antes de cada sessão:** `python -m rocha_reis_agent.ops verificar-rc` precisa mostrar
`"identica": true`. Qualquer correção depois disso exige nova revisão da versão, `ops congelar-rc` e
o registro da mudança aqui.

### Diferença para a 0.14.0rc1 (Fase 14)

Só o iniciador e ferramentas de homologação: aviso "PRONTO" apenas com o servidor no ar (num único
bloco, sem senhas), credenciais certas por perfil, congelamento verificável e planilha de registro.
Painel, atendimento, conhecimento e segurança: **sem mudança de comportamento**.

## 2. Resultados verificados

| Verificação | Resultado |
|---|---|
| pytest (unidade, integração, segurança, autenticação, 2FA, painel, conhecimento, envio, estado humano, recuperação, demonstração, isolamento, travas) | **964 aprovados** |
| Chromium (desktop, notebook, tablet, celular emulado) | **39 aprovados** |
| Roteiro automático da demonstração (13 jornadas) | **13/13** |
| Avaliação offline — regras (configuração ativa) | 123/129; segurança OK; **Q2 reprovado** (recall de pedido humano 0,902 < 0,95) |
| Avaliação offline — modelo adversário simulado | 129/129 |
| Avaliação offline — adaptador Gemini sobre HTTP simulado | 123/129; Q2 reprovado |

Nenhum desses resultados é homologação externa nem validação humana.

## 3. Prontidão por área

| Área | Situação para a 11B | Situação para produção |
|---|---|---|
| Painel e fluxos de atendimento | Pronto | Depende da 11B |
| Login, 2FA, permissões | Pronto | 2FA obrigatório já implementado para pré-produção |
| Conhecimento e aprovação | Pronto (base fictícia) | Depende dos dados oficiais |
| Demonstração e roteiro | Pronto | — |
| Registro dos resultados | Pronto (planilha em branco) | — |
| Celular físico | Só emulação testada; procedimento por cabo documentado | Depende do ambiente privado futuro |
| IA (entendimento de pedidos indiretos) | Limitação explicada na apresentação | **Bloqueio**: homologação Gemini |
| WhatsApp | Simulado | **Bloqueio**: homologação Meta |
| Infraestrutura, backup externo, monitoramento | — | **Bloqueio**: não existe |
| Privacidade, retenção, decisões | Formulário e decisões prontos para o salão | **Bloqueio**: decisões e jurídico |

## 4. Riscos conhecidos

1. Pedidos humanos e reclamações indiretos não reconhecidos pelas regras (mitigação na sessão:
   explicar; solução: Gemini homologado).
2. Celular físico não testado.
3. Acessibilidade só automatizada.
4. Árvore sem commit: perda de trabalho se a máquina falhar antes do versionamento (mitigação:
   commit autorizado; o manifesto ajuda a conferir o conteúdo).

## 5. Versionamento recomendado (não executado)

**Um único commit consolidado das Fases 10–15.** As fases alteraram os mesmos arquivos em camadas;
separar exigiria `git add -p` em dezenas de trechos e criaria commits intermediários que não passam
nos testes. Diff auditado: sem bancos, backups, chaves, certificados, credenciais, logs, temporários,
arquivos grandes ou padrões de segredo.

Antes do commit: (1) `ops verificar-rc` idêntico; (2) `pytest` e `tests_e2e` aprovados;
(3) `git status --short --ignored` sem arquivos sensíveis fora dos ignorados; (4) `git apply --check
docs/homolog/ativacao_gemini_9c.patch`; (5) `git update-index --chmod=+x iniciar.sh` depois do
`git add` (o volume não guarda o bit de execução).

Mensagem sugerida (seguindo o padrão de autoria do histórico do repositório):

```
feat: consolida Fases 10-14 e prepara RC1 offline para homologação

Inclui a Fase 15 (congelamento verificável da RC1 0.14.0rc1.post1, registro da
homologação 11B, iniciador com aviso de prontidão). Integrações reais desativadas;
quatro travas em False; dados exclusivamente fictícios.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

Depois do commit, `ops versao` passa a mostrar `versionada: true`; tag (`v0.14.0-rc1.post1`) e push
**somente com autorização**.
