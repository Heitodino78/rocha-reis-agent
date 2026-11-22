# Especificação para contratação do servidor — Rocha Reis Agent (pré-produção)

**Para:** responsável financeiro do Salão Rocha Reis e provedores consultados.
**Situação:** proposta para decisão. **Nada foi contratado.** A contratação deve ser feita por pessoa
legalmente habilitada e autorizada a assumir as despesas. Preços consultados em 21/11/2026, a
reconfirmar na compra. Análise completa: `SELECAO_INFRAESTRUTURA.md`.

## 1. O que será contratado

| Item | Especificação |
|---|---|
| Servidor virtual | 2 vCPU (compartilhada), **4 GB de RAM**, 40 GB ou mais de SSD, IPv4 público fixo |
| Sistema | Ubuntu 24.04 LTS, imagem oficial do provedor |
| Região | **Brasil** (São Paulo, de preferência) |
| Volume adicional | 40 GB de armazenamento em bloco, anexado ao servidor (para os dados, cifrado pela agência) |
| Backup externo | Armazenamento de objetos compatível com S3, **em outro provedor**, com bloqueio contra exclusão (Object Lock) |
| Domínio | Um subdomínio para o webhook (usar domínio do salão, se existir) |

**Mínimo aceitável:** 2 vCPU, 2 GB de RAM, 20 GB de SSD + volume de 20 GB. Funciona, mas com pouca folga
para atualizações.

## 2. Requisitos obrigatórios (eliminatórios)

1. Máquina virtual KVM com acesso de administrador completo (root) e kernel Linux padrão.
2. Possibilidade de anexar um **volume em bloco** e cifrá-lo pelo sistema operacional (LUKS).
   Criptografia feita só pelo provedor **não atende**.
3. Acesso por **chave SSH**; autenticação de dois fatores na conta do provedor.
4. **Console de emergência** (acesso à tela do servidor pelo painel do provedor) e modo de recuperação.
5. Liberdade para abrir apenas as portas 443/TCP e 51820/UDP e usar firewall próprio.
6. Snapshots do servidor e do volume.
7. Cobrança previsível, sem fidelidade mínima, com possibilidade de cancelamento.

## 3. Opções recomendadas e custos estimados (revisado em 21/11/2026)

Na verificação final, o plano de 4 GB da Vultr em São Paulo **não aparecia disponível para novos
pedidos** (API oficial de disponibilidade). Por isso a ordem mudou.

Câmbio de referência: PTAX de 19/11/2026 (US$ 1 = R$ 5,8889). Compras em dólar no cartão têm IOF de 3,5%
e o câmbio do cartão costuma ser maior que a PTAX.

| | Opção A (preferida, com condição) | Opção B (alternativa) |
|---|---|---|
| Servidor | Magalu Cloud, Sudeste (br-se1) — Balanced Value 2 vCPU / 4 GB / 40 GB | Vultr, São Paulo — `vhf-2c-2gb` (2 vCPU / 2 GB) |
| Preço do servidor | R$ 102,99/mês (impostos incluídos) | US$ 27,00/mês |
| Volume de 40 GB | R$ 23,20/mês | **a cotar** |
| Backup externo | Backblaze B2: ~US$ 0 (até 10 GB grátis) | Cloudflare R2: ~US$ 0 (até 10 GB grátis) |
| **Estimativa mensal** | **~R$ 126** | **~R$ 165 + volume** (com IOF) |
| Pagamento | Cartão, boleto ou PIX (em reais) | Cartão internacional (em dólar) |
| **Condição para comprar** | **Confirmar por escrito**: imagem Ubuntu 24.04 LTS em br-se1 e console de emergência/recuperação | Plano disponível no dia do pedido; preço do volume |

Não incluídos: domínio (≈ R$ 40/ano, se necessário), serviços da agência (implantação e manutenção, a
orçar), tributos sobre serviço contratado no exterior (consultar o contador), Google Gemini e
WhatsApp/Meta (estimativas separadas).

**Importante para o salão:** depois de qualquer reinício do servidor (manutenção, queda de energia no
provedor), o atendimento automático **fica parado até uma pessoa autorizada desbloquear o disco**. Isso
protege os dados, mas exige combinar quem faz isso e em quanto tempo.

## 4. Cuidados antes da compra

- Abrir a conta **em nome do salão** (ou conforme decidido), com 2FA ativado antes de criar qualquer recurso.
- Não aceitar planos promocionais com renovação mais cara sem registrar o preço de renovação.
- Não ativar serviços extras (backup do provedor, IP adicional, suporte pago) sem necessidade.
- Guardar o contrato, os termos de privacidade e o acordo de tratamento de dados (DPA) de cada provedor
  para a avaliação jurídica.
- Ativar alerta de gastos no painel do provedor.
- **Não** enviar à agência senhas da conta: o acesso técnico será por chave SSH e por usuário
  convidado com 2FA.

## 5. Perguntas a fazer aos provedores

**Vultr (Opção B)**

0. O plano `vc2-2c-4gb` (ou outro com 2 vCPU e 4 GB) voltará a ficar disponível em São Paulo? Hoje só aparece `vhf-2c-2gb`.
1. Preço mensal do Block Storage de 40 GB **em São Paulo** e se o volume continua existindo (com os dados) quando o servidor é destruído.
2. O IPv4 público está incluído no plano `vc2-2c-4gb` em São Paulo?
3. Preço do backup automático em São Paulo (percentual do plano)?
4. O firewall da Vultr tem custo? Existe console web (VNC) e montagem de ISO de recuperação?
5. Formas de pagamento aceitas para clientes no Brasil; emissão de fatura em nome de pessoa jurídica brasileira.

**Magalu Cloud (Opção A — as perguntas 1 e 2 são eliminatórias)**

1. A imagem **Ubuntu 24.04 LTS** está disponível na região br-se1?
2. Existe console de emergência (acesso à tela) e modo de recuperação?
3. Existe firewall/grupo de segurança gratuito?
4. O tráfego de saída é cobrado? (A documentação de rede diz que não; a do Object Storage cita R$ 0,10/GiB.)
5. Até quando IPv4 público e snapshots de instância continuarão sem cobrança, e com qual aviso prévio?
6. O Object Storage tem bloqueio de objetos (Object Lock) com modo de conformidade?

**Armazenamento externo (Backblaze B2 / Cloudflare R2)**

1. Região de armazenamento disponível (EUA, União Europeia) e se pode ser fixada.
2. Object Lock / Bucket Lock em modo que nem o administrador consiga apagar antes do prazo.
3. Chave de acesso restrita a um único bucket, sem permissão de exclusão.
4. (R2) Quem pode remover uma regra de bloqueio do bucket antes do prazo? (A documentação trata a remoção como operação normal de quem administra a conta; na B2, o modo COMPLIANCE não pode ser encurtado por ninguém.)

## 6. Condições a confirmar internamente

- Quem é o titular das contas e quem paga.
- Orçamento mensal máximo aprovado.
- Quem guardará a frase-senha do disco cifrado e a chave dos backups (recomendação: duas pessoas).
- Concordância com a regra: **depois de qualquer reinício, o servidor só volta a atender depois que uma
  pessoa autorizada desbloquear o disco cifrado.**
- Avaliação jurídica sobre backup cifrado guardado fora do Brasil.
