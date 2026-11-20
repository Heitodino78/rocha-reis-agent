# Checklist da homologação com a equipe (11B)

**Situação:** pronta para uso; **sessão ainda não realizada**. Marque durante a preparação e a sessão.

## 1. Antes (agência, no dia anterior ou 1 h antes)

- [ ] Computador com a RC1: `python -m rocha_reis_agent.ops verificar-rc` → `"identica": true` (o código é
      exatamente o da candidata congelada em `docs/homologacao/RC1_MANIFESTO.json`; se não for, a saída
      lista os arquivos alterados — **não** fazer a sessão até resolver). Anotar versão e impressão digital.
- [ ] `python -m rocha_reis_agent.ops roteiro` → **13/13 OK** (perfil separado, não afeta a demonstração).
- [ ] `sh iniciar.sh --nova-demo` → painel abre; faixa **DEMONSTRAÇÃO** visível.
- [ ] Sistema (admin.demo): quatro travas **bloqueadas**; "Perfil do ambiente: demo".
- [ ] Credenciais fictícias impressas, uma folha por papel; o arquivo **não** é projetado.
- [ ] Segundo terminal pronto para `sh iniciar.sh --mensagem "..."`.
- [ ] (Opcional, AT-13/14) um celular **de teste** com aplicativo autenticador.
- [ ] Planilha da sessão: `python -m rocha_reis_agent.ops registro-11b --destino ~/homologacao-11b/sessao-AAAA-MM-DD.csv`
      (fora do repositório; 31 cenários em branco; abre no LibreOffice/Excel). Ou impressos do plano.
- [ ] Critérios de aceitação (§7 do plano) **confirmados pelo responsável** antes de começar (decisão 14).
- [ ] Impressos: `TREINAMENTO_RAPIDO.md` e, se não usar a planilha, as fichas do plano.
- [ ] Combinado com o salão: participantes, papéis, horário, sala, quem assina.
- [ ] Ninguém vai usar dados ou números de clientes reais (avisar no início).

## 1b. Celular de verdade (opcional)

O painel só escuta em `127.0.0.1`: nenhum celular alcança o computador pela rede, e isso **não**
deve ser mudado para a sessão (sem expor porta, sem desligar segurança, sem túnel público).

| Opção | Situação | Como |
|---|---|---|
| Modo celular do navegador do computador | **Recomendado; testado** (Chromium 390 px) | F12 → modo dispositivo |
| Celular **Android de teste** por cabo USB | **Documentado, não testado** (requer autorização para instalar a ferramenta `adb` e ativar a depuração USB no aparelho de teste) | `adb reverse tcp:8000 tcp:8000` → no celular abrir `http://127.0.0.1:8000/panel/` (o tráfego vai pelo cabo; nenhuma porta é aberta na rede). Ao terminar: `adb reverse --remove-all` e desligar a depuração USB |
| iPhone | Sem equivalente simples e seguro | Usar o modo celular do navegador |
| Celular pela rede Wi-Fi | **Não usar nesta fase** | Exigiria abrir porta na rede, certificado confiável instalado no celular e firewall — fica para o ambiente privado de homologação futuro (VPN + HTTPS + 2FA) |

## 2. Durante

- [ ] Apresentação (`ROTEIRO_DEMONSTRACAO_SALAO.md`) — 30–40 min.
- [ ] Treinamento rápido — 15 min.
- [ ] Cenários AT-* e AD-* — condutor lê, observador anota, **ninguém ajuda a clicar**.
- [ ] Cada resultado anotado **na hora**, pelo observador, com o que o participante fez e disse.
- [ ] Cada problema anotado no registro (§5 do plano) com tipo e gravidade.
- [ ] Pedidos novos anotados como "Pedido de nova funcionalidade" — sem promessa.
- [ ] Entre grupos: Ctrl+C → `sh iniciar.sh --nova-demo`.

## 3. Depois

- [ ] `python -m rocha_reis_agent.ops conferir-registro <planilha>` sem campos faltando (só confere; não aprova).
- [ ] Critérios da §7 do plano avaliados com o responsável; resultado assinado.
- [ ] Fichas guardadas fora do repositório (têm nomes de pessoas).
- [ ] Resumo sem dados pessoais em `STATUS_FASE_11.md` (seção "Fase 11B").
- [ ] Problemas Críticos/Altos viram correções combinadas; pedidos novos vão para avaliação de escopo.
- [ ] Formulário `COLETA_DADOS_OFICIAIS.md` e `DECISOES_PENDENTES_SALAO.md` entregues ao responsável.

## 4. O que esta sessão NÃO valida

WhatsApp real, Gemini real, servidor de produção, dados oficiais e conformidade jurídica — cada um
tem etapa própria (ver `CHECKLIST_PRODUCAO.md`).
