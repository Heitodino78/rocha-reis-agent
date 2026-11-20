# Segurança de autenticação, sessões, HTTPS e segredos

Situação em 20/11/2026 (Fase 13). **Implementado e testado localmente**, salvo onde indicado
como referência ou pendência. Nenhum serviço externo, DNS ou certificado público foi usado.

## 1. Verificação em duas etapas (TOTP)

| Item | Decisão |
|---|---|
| Padrão | TOTP (RFC 6238): SHA-1, 6 dígitos, passo de 30 s — o que os aplicativos autenticadores usam |
| Biblioteca | `cryptography` (já travada no `requirements.lock`): geração/verificação e URI `otpauth://`. Nenhum código próprio de geração |
| Segredo | 160 bits aleatórios; mostrado **uma única vez** no cadastro (chave em grupos de 4 + URI); nunca devolvido depois, nunca em log |
| Guarda do segredo | AES-256-GCM com chave em arquivo 0600 **fora do diretório do banco e dos backups**; id do usuário como dado associado (segredo copiado para outra conta não decifra); id da chave guardado junto para detectar chave errada |
| Tolerância de relógio | ±1 passo (±30 s). Fora disso: código recusado. No cadastro, a mensagem orienta a conferir a hora do celular; no login, a mensagem é genérica ("Código inválido ou já utilizado") |
| Reutilização | O último passo aceito é gravado; o mesmo código (ou um anterior) é recusado — atômico no banco |
| Códigos de recuperação | 10 por emissão, 60 bits cada (alfabeto sem caracteres ambíguos), mostrados uma vez, guardados como HMAC-SHA-256 por usuário, uso único, regeneração exige código TOTP válido (revoga os antigos) |
| QR code | **Não gerado** (exigiria nova dependência). Cadastro pela chave manual ou colando a URI |

### Fluxo de login
1. Login e senha (Argon2id, limitador por conta e por IP — inalterados).
2. Com segundo fator ativo, **nenhuma sessão é criada**: o servidor grava uma *etapa* (hash do
   token, 5 minutos, até 5 tentativas) e envia um cookie próprio (`rr_panel_mfa`, HttpOnly,
   SameSite=Strict, Secure/`__Host-` com HTTPS). Esse cookie **não é aceito por nenhuma rota**
   protegida (conversas, administração, SSE).
3. Código TOTP ou de recuperação → etapa consumida (uso único) → sessão completa criada (a
   sessão anterior do navegador, se houver, é revogada).

Falhas do segundo fator alimentam o mesmo limitador por IP: depois de 5 erros, até a 1ª etapa
fica bloqueada para aquele IP — recomeçar o login não dá novas tentativas.

### Política por ambiente
- `PANEL_MFA_REQUIRED=true`: sem segundo fator ativo, a sessão só alcança cadastro, troca de
  senha, o próprio estado e sair (403 em todo o resto, inclusive SSE).
- **Pré-produção: obrigatório**, sem exceção configurável (validação da configuração).
- Demonstração: opcional, com credenciais fictícias e chave própria do perfil.
- Desativar o próprio segundo fator: só onde não é obrigatório, com senha **e** código.

### Redefinição administrativa (perda do celular e dos códigos)
`POST /users/{id}/mfa-reset` (tela Equipe → "Redefinir duas etapas"): só ADMIN; nunca a si mesmo;
com política obrigatória o administrador precisa ter o próprio segundo fator ativo; justificativa
de 10–300 caracteres (tabela `panel_mfa_resets`); confirmação `REDEFINIR:<login>`; auditoria de
sucesso e de negação; todas as sessões da pessoa são encerradas e ela cadastra de novo no próximo
acesso. **Procedimento:** conferir a identidade pessoalmente antes — o sistema não verifica isso.

Risco conhecido: com política obrigatória, quem tem só a senha de uma conta **ainda sem**
segundo fator pode cadastrar o seu próprio aparelho. Mitigação: contas novas usam senha
temporária (troca obrigatória) e devem cadastrar o segundo fator no primeiro acesso, acompanhadas.

## 2. Sessões

| Controle | Situação |
|---|---|
| Cookie | HttpOnly, SameSite=Strict, `Path=/`, Secure + prefixo `__Host-` com HTTPS (testado em TLS real) |
| Expiração | Inatividade (padrão 30 min; máx. 30 em pré-produção) e absoluta (12 h) |
| Fixação | Login e 2ª etapa sempre criam token novo e revogam o anterior do navegador |
| Rotação | **Nova (Fase 13):** troca de senha e ativação do segundo fator geram token novo; o antigo deixa de valer |
| Revogação | Logout, desativação do usuário, redefinição/desativação do segundo fator, troca de senha (demais sessões) |
| CSRF | Token derivado (HMAC) da sessão + checagem de Origin em toda rota que altera estado (inclui as novas) |
| SSE | Revalida a sessão a cada ciclo: sessão revogada encerra o stream (`auth`). Após rotação, o painel confere a sessão e reabre o stream com o cookie novo |
| Privilégios | Papel lido do banco a cada requisição: mudança vale na hora |

## 3. HTTPS e proxy reverso

- **Escolha:** Caddy (certificados automáticos, configuração curta) — referência em
  `deploy/caddy/Caddyfile.exemplo`: endereço público responde **só** `/webhooks/whatsapp`; o
  painel escuta apenas no IP da VPN, com SSE sem buffer. Não instalado, não validado com Caddy
  real (binário ausente nesta máquina).
- **Aplicação:** HSTS quando a origem é HTTPS; CSP, `X-Frame-Options`, `nosniff`, `no-referrer`
  (já existentes); `TrustedHostMiddleware`.
- **Cabeçalhos encaminhados:** o iniciador **não confia** em `X-Forwarded-*` a menos que
  `PANEL_TRUSTED_PROXIES` diga quem é o proxy (testado: IPs forjados não escapam do limitador).
  Atrás do Caddy local: `--forwarded-allow-ips 127.0.0.1` (unidade de referência).
- **Teste local real:** pré-produção sobe com certificado de **desenvolvimento** autoassinado
  (`ops/tls.py`, 30 dias, só 127.0.0.1/localhost) — cliente sem confiar nele é recusado; com ele,
  cookie `__Host-`/Secure, HSTS e SSE funcionam. **Não é certificado público.**

## 4. Segredos

| Segredo | Onde fica | Rotação | Perda/comprometimento |
|---|---|---|---|
| Chave dos backups | Arquivo 0600 em `chaves/` (fora dos backups) | Nova chave → novos backups; manter a antiga até os backups antigos expirarem | Perda: backups antigos irrecuperáveis — fazer backup novo já. Vazamento: nova chave + novo backup + apagar antigos conforme retenção |
| Chave do segundo fator | Arquivo 0600 em `chaves/` (fora do banco e dos backups) | `ops mfa-rotacionar --perfil` (recifra tudo numa transação; antiga guardada à parte) | Perda: ninguém passa da 2ª etapa → redefinir todos e recadastrar. Vazamento + cópia do banco = segredos expostos → rotacionar chave **e** recadastrar todos |
| Sessões | Só hash do token no banco; não há segredo de servidor | Não se aplica (tokens aleatórios) | Revogar sessões (desativar/reativar usuário ou redefinir) |
| Segredos do webhook (verify token, app secret, chave de pseudônimos) | Perfil/arquivo de ambiente 0600 | Troca coordenada com a Meta (app secret, verify token). Chave de pseudônimos: trocar muda os ids das conversas (planejar) | Trocar e revisar logs de webhook |
| Credenciais Gemini/Meta (futuras) | **Não existem nesta fase.** Planejado: credenciais do `systemd` (`LoadCredential`) ou arquivo 0600 | Conforme provedor | Revogar no console do provedor |
| Senhas da equipe | Argon2id no banco | Troca pelo painel; redefinição pelo administrador | Redefinir e revogar sessões |

Proporcional ao porte: arquivos privados + `EnvironmentFile`/`LoadCredential` do systemd. Cofre
gerenciado só se a hospedagem escolhida já oferecer sem custo relevante.

## 5. Limitações

- Sem QR code; sem chaves de segurança físicas (WebAuthn); sem verificação de identidade na
  redefinição (procedimento humano).
- Teste com leitor de tela não realizado; acessibilidade só por verificação automatizada.
- TLS testado com certificado de desenvolvimento e cliente Python; Caddy real não executado.
