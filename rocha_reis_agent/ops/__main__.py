"""CLI de operação local. Nenhum comando acessa rede, Meta ou Gemini.

  python -m rocha_reis_agent.ops chave      --arquivo ~/.config/rocha-reis-agent/backup.key
  python -m rocha_reis_agent.ops backup     --banco BANCO --destino DIR (--chave ARQ | --frase-senha)
  python -m rocha_reis_agent.ops verificar  ARQUIVO (--chave ARQ | --frase-senha)
  python -m rocha_reis_agent.ops restaurar  ARQUIVO --para NOVO.synthetic.sqlite3 (--chave ARQ | --frase-senha)
  python -m rocha_reis_agent.ops promover   --restaurado R --ativo A --confirmacao SUBSTITUIR:<nome>
  python -m rocha_reis_agent.ops listar     DIR
  python -m rocha_reis_agent.ops reter      DIR --manter 14 [--dias 30] [--confirmacao APAGAR:N]
  python -m rocha_reis_agent.ops ambiente   [--perfil DIR]        diagnóstico do ambiente (somente leitura)
  python -m rocha_reis_agent.ops metricas   [--perfil DIR] [--backups DIR]   indicadores locais sem dados pessoais
  python -m rocha_reis_agent.ops preprod    [--perfil DIR]        cria perfil de PRÉ-PRODUÇÃO com dados SINTÉTICOS
  python -m rocha_reis_agent.ops backup-agendado    --perfil DIR    uma rodada do backup automático (para systemd timer)
  python -m rocha_reis_agent.ops teste-restauracao  --perfil DIR    restaura o último backup em local isolado e confere
  python -m rocha_reis_agent.ops ensaio-recuperacao --perfil DIR --destino DIR_VAZIO   ensaio de desastre isolado
  python -m rocha_reis_agent.ops mfa-rotacionar     --perfil DIR    nova chave do segundo fator (recifra tudo)
  python -m rocha_reis_agent.ops versao                             identificação da versão candidata (RC1)
  python -m rocha_reis_agent.ops roteiro [--destino DIR]            roteiro guiado da demonstração (perfil novo isolado)
  python -m rocha_reis_agent.ops congelar-rc | verificar-rc         congela/confere o código da candidata (manifesto)
  python -m rocha_reis_agent.ops registro-11b --destino ARQ.csv     planilha em branco da homologação (fora do repo)
  python -m rocha_reis_agent.ops conferir-registro ARQ.csv          confere o preenchimento (não aprova nada)
  python -m rocha_reis_agent.ops demo       [--perfil DIR] [--recriar]
  python -m rocha_reis_agent.ops iniciar    [--perfil DIR] [--porta 8000]
  python -m rocha_reis_agent.ops desempenho [--conversas 1000] [--mensagens 400]
  python -m rocha_reis_agent.ops mensagem   "texto" [--de 55119000002NN] [--perfil DIR]   (cliente FICTÍCIO)

Servidor (Fase 17; RAIZ = / no servidor ou um diretório descartável para ensaio):
  python -m rocha_reis_agent.ops empacotar          --destino DIR              pacote reproduzível da versão
  python -m rocha_reis_agent.ops instalar           --pacote ARQ --raiz RAIZ   instala e ativa a 1ª versão
  python -m rocha_reis_agent.ops preparar-servidor  --raiz RAIZ [--origem https://painel.interno]
  python -m rocha_reis_agent.ops validar-instalacao --raiz RAIZ              recusa configuração insegura (ExecStartPre)
  python -m rocha_reis_agent.ops atualizar          --pacote ARQ --raiz RAIZ --confirmacao ATUALIZAR:<versão>
  python -m rocha_reis_agent.ops reverter           --raiz RAIZ [--restaurar-banco BACKUP --confirmacao RESTAURAR:BACKUP]
  python -m rocha_reis_agent.ops verificar-migracao --banco COPIA.synthetic.sqlite3   (usado pela atualização)
  python -m rocha_reis_agent.ops alertas            --perfil DIR [--batimento ARQ] [--externo ESTADO]   saída 0/1/2
  python -m rocha_reis_agent.ops backup-externo     --perfil DIR --destino-falso DIR [--manter 14]   (SIMULADO)
  python -m rocha_reis_agent.ops ensaio-operacional --destino DIR_NOVO   ensaio completo (volume cifrado)
  python -m rocha_reis_agent.ops carga --destino DIR_NOVO [--conversas 1000] [--operadores 4] [--segundos 30]

A frase-senha é lida de ROCHA_REIS_BACKUP_PASSPHRASE ou digitada (sem eco). Nunca é impressa.
"""

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys

from rocha_reis_agent.ops.backup import (
    BackupError, KeyMaterial, create_backup, generate_key, list_backups, promote, restore_backup, verify_backup,
)


def _material(args: argparse.Namespace) -> KeyMaterial:
    if args.chave:
        return KeyMaterial("keyfile", key_file=Path(args.chave).expanduser())
    passphrase = os.environ.get("ROCHA_REIS_BACKUP_PASSPHRASE") or getpass("Frase-senha do backup: ")
    return KeyMaterial("passphrase", passphrase=passphrase)


def _key_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chave", help="arquivo de chave (fora do diretório de backups)")
    group.add_argument("--frase-senha", action="store_true", help="usar frase-senha (env ou digitada)")


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def rotate_profile_mfa_key(profile: Path) -> dict[str, object]:
    """Gera chave nova, recifra todos os segredos numa transação e aponta o perfil para ela.
    A chave antiga é mantida (renomeada, 0600) até a remoção manual, após conferência."""
    from datetime import datetime, timezone
    from rocha_reis_agent.bootstrap import configured_repository
    from rocha_reis_agent.ops.demo import _write_private, profile_settings
    from rocha_reis_agent.panel.mfa import MfaCipher, rotate_key
    from rocha_reis_agent.panel.store import SQLitePanelStore
    profile = profile.absolute()
    data = json.loads((profile / "perfil.json").read_text(encoding="utf-8"))
    if not data.get("mfa_key"):
        raise BackupError("Perfil sem chave de segundo fator configurada.")
    settings = profile_settings(profile)
    old_path = profile / data["mfa_key"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    new_relative = f"chaves/mfa-{stamp}.key"
    new_path = generate_key(profile / new_relative)
    store = SQLitePanelStore(configured_repository(settings))
    count = rotate_key(store, MfaCipher.from_file(old_path), MfaCipher.from_file(new_path))
    data["mfa_key"] = new_relative
    _write_private(profile / "perfil.json", json.dumps(data, indent=2) + "\n")
    retired = old_path.with_name(old_path.name + f".antiga-{stamp}")
    os.rename(old_path, retired)
    return {"segredos_recifrados": count, "chave_nova": new_relative, "chave_antiga_guardada": retired.name,
            "aviso": "Reinicie o painel. Apague a chave antiga só depois de conferir os logins."}


def default_profile_path() -> Path:
    from rocha_reis_agent.ops.demo import default_profile
    return default_profile()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rocha_reis_agent.ops", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("chave", help="gerar arquivo de chave (0600)")
    p.add_argument("--arquivo", required=True)
    p = sub.add_parser("backup", help="criar backup criptografado e verificado")
    p.add_argument("--banco", required=True)
    p.add_argument("--destino", required=True)
    _key_args(p)
    p = sub.add_parser("verificar", help="verificar backup sem restaurar")
    p.add_argument("arquivo")
    _key_args(p)
    p = sub.add_parser("restaurar", help="restaurar para arquivo NOVO e isolado")
    p.add_argument("arquivo")
    p.add_argument("--para", required=True)
    _key_args(p)
    p = sub.add_parser("promover", help="substituir o banco ativo pelo restaurado (com cópia de segurança)")
    p.add_argument("--restaurado", required=True)
    p.add_argument("--ativo", required=True)
    p.add_argument("--confirmacao", required=True)
    p = sub.add_parser("listar", help="listar backups de um diretório (sem chave)")
    p.add_argument("diretorio")
    p = sub.add_parser("reter", help="aplicar retenção de backups (sem --confirmacao só mostra o plano)")
    p.add_argument("diretorio")
    p.add_argument("--manter", type=int, required=True)
    p.add_argument("--dias", type=int)
    p.add_argument("--confirmacao")
    for name, text in (("ambiente", "diagnóstico do ambiente"), ("metricas", "indicadores operacionais locais")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--perfil")
        if name == "metricas":
            p.add_argument("--backups")
    p = sub.add_parser("preprod", help="criar perfil de pré-produção (dados sintéticos)")
    p.add_argument("--perfil")
    for name, text in (("backup-agendado", "uma rodada do backup automático"),
                       ("teste-restauracao", "teste de restauração isolado"),
                       ("mfa-rotacionar", "rotação da chave do segundo fator")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--perfil", required=True)
    p = sub.add_parser("ensaio-recuperacao", help="ensaio de recuperação de desastre em diretório isolado")
    p.add_argument("--perfil", required=True)
    p.add_argument("--destino", required=True)
    sub.add_parser("versao", help="identificação da versão candidata")
    sub.add_parser("congelar-rc", help="grava o manifesto (hash de cada arquivo) da candidata")
    sub.add_parser("verificar-rc", help="confere se o código ainda é o da candidata congelada")
    p = sub.add_parser("registro-11b", help="gera a planilha em branco da homologação 11B")
    p.add_argument("--destino", required=True)
    p = sub.add_parser("conferir-registro", help="confere o preenchimento da planilha da 11B")
    p.add_argument("arquivo")
    p = sub.add_parser("roteiro", help="executa as jornadas da demonstração num perfil novo e isolado")
    p.add_argument("--destino")
    p = sub.add_parser("demo", help="criar/atualizar perfil de demonstração isolado")
    p.add_argument("--perfil")
    p.add_argument("--recriar", action="store_true", help="apaga SOMENTE o perfil de demonstração indicado")
    p = sub.add_parser("iniciar", help="iniciar o painel local (127.0.0.1)")
    p.add_argument("--perfil")
    p.add_argument("--porta", type=int, default=8000)
    p.add_argument("--verificar", action="store_true", help="só executa as verificações e sai")
    p = sub.add_parser("mensagem", help="simular mensagem de cliente FICTÍCIO na demonstração (painel pode estar aberto)")
    p.add_argument("texto")
    p.add_argument("--de", help="número fictício (padrão: um novo a cada uso)")
    p.add_argument("--perfil")
    p = sub.add_parser("desempenho", help="medir consultas do painel com volume sintético (diretório temporário)")
    p.add_argument("--conversas", type=int, default=1000)
    p.add_argument("--mensagens", type=int, default=400)
    p.add_argument("--repeticoes", type=int, default=30)
    # ---- Fase 17: servidor
    p = sub.add_parser("empacotar", help="pacote reproduzível da versão (código de execução + manifesto)")
    p.add_argument("--destino", required=True)
    p = sub.add_parser("instalar", help="instala um pacote verificado na árvore do servidor e ativa se for o 1º")
    p.add_argument("--pacote", required=True)
    p.add_argument("--raiz", required=True)
    p.add_argument("--python", help="interpretador (padrão: cria .venv da versão a partir do requirements.lock)")
    p.add_argument("--sem-venv", action="store_true", help="não cria ambiente virtual (ensaio local)")
    p.add_argument("--pip-arg", action="append", default=[], help="ex.: --no-index --find-links DIR (sem rede)")
    p = sub.add_parser("preparar-servidor", help="cria a instância de PRÉ-PRODUÇÃO SINTÉTICA no layout do servidor")
    p.add_argument("--raiz", required=True)
    p.add_argument("--origem", default="https://painel.interno")
    p.add_argument("--ponto-montagem", help="laboratório: ponto de montagem do volume (ancestral de var/lib/...)")
    p.add_argument("--laboratorio", action="store_true",
                   help="ensaio local SEM volume dedicado (proibido na raiz /; registrado no perfil)")
    # ---- Fase 18B-0: volume de dados dedicado e estado em camadas
    p = sub.add_parser("marcar-volume", help="grava a marca de identificação num volume LUKS recém-preparado")
    p.add_argument("--ponto-montagem", required=True)
    p = sub.add_parser("verificar-volume", help="confere o volume de dados (0 pronto, 3 aguardando desbloqueio, 1 recusado)")
    p.add_argument("--raiz", required=True)
    p.add_argument("--identidade", help="laboratório: JSON com a identidade esperada fora da raiz de teste")
    p = sub.add_parser("estado-servidor", help="estado em camadas: ligado, disco, banco, worker, webhook, atendimento")
    p.add_argument("--raiz", required=True)
    p = sub.add_parser("validar-instalacao", help="checa permissões, perfil, cifragem e travas (sai 1 se inseguro)")
    p.add_argument("--raiz", required=True)
    p = sub.add_parser("atualizar", help="atualização com backup verificado, ensaio de migração e reversão automática")
    p.add_argument("--pacote", required=True)
    p.add_argument("--raiz", required=True)
    p.add_argument("--confirmacao", required=True)
    p.add_argument("--parar", default="systemctl stop rocha-reis-worker rocha-reis-webhook rocha-reis-panel")
    p.add_argument("--iniciar", default="systemctl start rocha-reis-webhook rocha-reis-panel rocha-reis-worker")
    p.add_argument("--prontidao", action="append", default=None,
                   help="URL local de /ready (padrão: webhook 8080 e painel 8000)")
    p.add_argument("--python")
    p.add_argument("--sem-venv", action="store_true")
    p.add_argument("--pip-arg", action="append", default=[])
    p = sub.add_parser("reverter", help="volta à versão anterior; banco só com restauração confirmada")
    p.add_argument("--raiz", required=True)
    p.add_argument("--restaurar-banco")
    p.add_argument("--confirmacao")
    p = sub.add_parser("verificar-migracao", help="abre uma CÓPIA do banco com este código e confere integridade")
    p.add_argument("--banco", required=True)
    p = sub.add_parser("alertas", help="alertas operacionais locais (0 OK, 1 atenção, 2 crítico)")
    p.add_argument("--perfil", required=True)
    p.add_argument("--batimento", help="arquivo de batimento do worker")
    p.add_argument("--externo", help="estado da cópia externa")
    p = sub.add_parser("carga", help="mede operação com N conversas e operadores simultâneos (servidor simulado)")
    p.add_argument("--destino", required=True)
    p.add_argument("--conversas", type=int, default=1000)
    p.add_argument("--operadores", type=int, default=4)
    p.add_argument("--segundos", type=float, default=30.0)
    p = sub.add_parser("ensaio-operacional", help="ensaio completo do servidor simulado (instala, derruba, restaura)")
    p.add_argument("--destino", required=True)
    p = sub.add_parser("backup-externo", help="envia backups cifrados ao destino externo (real BLOQUEADO pela trava)")
    p.add_argument("--perfil", required=True)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--destino-falso", help="diretório que SIMULA o armazenamento externo")
    target.add_argument("--s3", action="store_true", help="destino S3 do perfil (backup.externo); só 127.0.0.1 com a trava fechada")
    p.add_argument("--manter", type=int, default=14)
    p = sub.add_parser("baixar-externo", help="baixa um backup do destino externo para restaurar (verificado)")
    p.add_argument("--perfil", required=True)
    p.add_argument("--nome", required=True)
    p.add_argument("--para", required=True)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--destino-falso")
    target.add_argument("--s3", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in ("empacotar", "instalar", "preparar-servidor", "validar-instalacao", "atualizar", "reverter",
                            "verificar-migracao", "alertas", "backup-externo", "ensaio-operacional", "carga",
                            "marcar-volume", "verificar-volume", "estado-servidor", "baixar-externo"):
            return _server(args)
        if args.command == "chave":
            _print({"chave_criada": str(generate_key(Path(args.arquivo).expanduser())),
                    "aviso": "Guarde a chave FORA do diretório de backups. Sem ela o backup é irrecuperável."})
        elif args.command == "backup":
            path = create_backup(Path(args.banco).expanduser(), Path(args.destino).expanduser(), _material(args))
            _print({"backup": str(path), "verificado": True})
        elif args.command == "verificar":
            manifest = verify_backup(Path(args.arquivo).expanduser(), _material(args))
            _print({"valido": True, "criado_em": manifest["created_at"], "esquema": manifest["schema_version"],
                    "contagens": manifest["table_counts"]})
        elif args.command == "restaurar":
            manifest = restore_backup(Path(args.arquivo).expanduser(), Path(args.para).expanduser(), _material(args))
            _print({"restaurado_em": str(Path(args.para).expanduser()), "esquema": manifest["schema_version"],
                    "aviso": "Banco ISOLADO. O banco ativo não foi alterado."})
        elif args.command == "promover":
            safety = promote(Path(args.restaurado).expanduser(), Path(args.ativo).expanduser(),
                             confirmation=args.confirmacao)
            _print({"substituido": str(Path(args.ativo).expanduser()), "copia_anterior": str(safety)})
        elif args.command == "listar":
            _print(list_backups(Path(args.diretorio).expanduser()))
        elif args.command == "reter":
            from rocha_reis_agent.ops.backup import apply_retention, retention_plan
            directory = Path(args.diretorio).expanduser()
            plan = retention_plan(directory, keep_last=args.manter, keep_days=args.dias)
            if args.confirmacao is None:
                _print({**plan, "aviso": f"Nada foi apagado. Para aplicar: --confirmacao APAGAR:{len(plan['remover'])}"})
            else:
                _print({"apagados": apply_retention(directory, plan, confirmation=args.confirmacao)})
        elif args.command in ("ambiente", "metricas", "preprod"):
            from rocha_reis_agent.ops import environments
            profile = Path(args.perfil).expanduser() if args.perfil else None
            if args.command == "ambiente":
                _print(environments.diagnose(profile or default_profile_path()))
            elif args.command == "metricas":
                _print(environments.metrics(profile or default_profile_path(),
                                            backups=Path(args.backups).expanduser() if args.backups else None))
            else:
                _print(environments.build_preproduction(profile or environments.default_preprod()))
        elif args.command in ("backup-agendado", "teste-restauracao"):
            from rocha_reis_agent.ops import backup_job
            profile = Path(args.perfil).expanduser()
            result = (backup_job.run_backup_job(profile) if args.command == "backup-agendado"
                      else backup_job.restore_test(profile))
            _print(result)
        elif args.command == "ensaio-recuperacao":
            from rocha_reis_agent.ops.disaster import drill
            _print(drill(Path(args.perfil).expanduser(), Path(args.destino).expanduser()))
        elif args.command == "mfa-rotacionar":
            _print(rotate_profile_mfa_key(Path(args.perfil).expanduser()))
        elif args.command == "versao":
            from rocha_reis_agent.ops.release import release_info
            _print(release_info())
        elif args.command in ("congelar-rc", "verificar-rc"):
            from rocha_reis_agent.ops import release
            result = release.freeze() if args.command == "congelar-rc" else release.verify()
            _print(result)
            if args.command == "verificar-rc" and not result.get("identica"):
                return 3
        elif args.command == "registro-11b":
            from rocha_reis_agent.ops.uat import generate
            _print(generate(Path(args.destino).expanduser()))
        elif args.command == "conferir-registro":
            from rocha_reis_agent.ops.uat import check
            _print(check(Path(args.arquivo).expanduser()))
        elif args.command == "roteiro":
            from rocha_reis_agent.ops.rehearsal import run_rehearsal
            result = run_rehearsal(Path(args.destino).expanduser() if args.destino else None)
            _print(result)
            return 0 if result["resultado"] == "OK" else 3
        elif args.command == "demo":
            from rocha_reis_agent.ops.demo import build_demo, default_profile
            _print(build_demo(Path(args.perfil).expanduser() if args.perfil else default_profile(), reset=args.recriar))
        elif args.command == "mensagem":
            from rocha_reis_agent.ops.demo import default_profile, send_fictitious
            _print(send_fictitious(Path(args.perfil).expanduser() if args.perfil else default_profile(),
                                   args.texto, sender=args.de))
        elif args.command == "desempenho":
            from rocha_reis_agent.ops.perf import measure
            _print(measure(args.conversas, args.mensagens, args.repeticoes))
        elif args.command == "iniciar":
            from rocha_reis_agent.ops.launcher import default_profile as launcher_profile, launch
            return launch(Path(args.perfil).expanduser() if args.perfil else launcher_profile(), port=args.porta,
                          check_only=args.verificar)
    except BackupError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2
    return 0


def _server(args) -> int:  # type: ignore[no-untyped-def]
    """Comandos de servidor (Fase 17). Nenhum acessa a rede; `atualizar` só chama os comandos informados."""
    from rocha_reis_agent.ops import deploy
    try:
        if args.command == "empacotar":
            package = deploy.build_package(Path(args.destino).expanduser())
            _print({"pacote": str(package), "sha256": deploy._sha(package.read_bytes())})
        elif args.command == "instalar":
            result = deploy.install_release(Path(args.pacote), Path(args.raiz), python=args.python,
                                            create_venv=not args.sem_venv, pip_args=tuple(args.pip_arg))
            if deploy.current_release(Path(args.raiz)) is None:
                deploy.activate(Path(args.raiz), str(result["id"]))
                result["ativada"] = True
            result.pop("manifesto", None)
            _print(result)
        elif args.command == "preparar-servidor":
            _print(deploy.prepare_instance(Path(args.raiz), panel_origin=args.origem, laboratorio=args.laboratorio,
                                           volume_mount=Path(args.ponto_montagem) if args.ponto_montagem else None))
        elif args.command == "marcar-volume":
            from rocha_reis_agent.persistence.storage_security import mark_volume
            try:
                _print(mark_volume(Path(args.ponto_montagem)))
            except (ValueError, FileExistsError) as error:
                raise deploy.DeployError("Marca já existe; nunca é sobrescrita." if isinstance(error, FileExistsError)
                                         else str(error)) from None
        elif args.command == "verificar-volume":
            import json as _json
            spec = None
            if args.identidade:
                if deploy.is_server_root(Path(args.raiz)):
                    raise deploy.DeployError("--identidade é só para laboratório; no servidor vale o perfil.")
                spec = _json.loads(Path(args.identidade).read_text(encoding="utf-8"))
            status = deploy.volume_status(Path(args.raiz), spec)
            _print(status.as_dict())
            return 0 if status.ready else (3 if status.state == "AGUARDANDO_DESBLOQUEIO" else 1)
        elif args.command == "estado-servidor":
            from rocha_reis_agent.ops import readiness
            state = readiness.server_state(Path(args.raiz))
            _print(state)
            return readiness.exit_code(state)
        elif args.command == "validar-instalacao":
            result = deploy.validate_installation(Path(args.raiz))
            _print(result)
            return 0 if result["ok"] else 1
        elif args.command == "verificar-migracao":
            _print(_rehearse(Path(args.banco)))
        elif args.command == "atualizar":
            root = Path(args.raiz)
            from rocha_reis_agent.ops.demo import profile_settings
            settings = profile_settings(deploy.layout(root)["config"])
            urls = args.prontidao or ["http://127.0.0.1:8080/ready", "http://127.0.0.1:8000/ready"]
            from urllib.parse import urlsplit
            host = urlsplit(settings.panel_origin).hostname or "127.0.0.1"
            hosts = {u: host for u in urls if ":8000/" in u}
            report = deploy.update(
                Path(args.pacote), root, database=Path(settings.storage_path),  # type: ignore[arg-type]
                key_file=deploy.layout(root)["chaves"] / "backup.key", confirmation=args.confirmacao,
                stop=lambda: deploy.run_commands([deploy.split_command(args.parar)]),
                start=lambda: deploy.run_commands([deploy.split_command(args.iniciar)]),
                ready=lambda: deploy.probe_http(urls, hosts=hosts), python=args.python,
                create_venv=not args.sem_venv, pip_args=tuple(args.pip_arg))
            _print(report)
            return 0 if report.get("resultado") == "ATUALIZADA" else 1
        elif args.command == "reverter":
            root = Path(args.raiz)
            if args.restaurar_banco:
                from rocha_reis_agent.ops.demo import profile_settings
                settings = profile_settings(deploy.layout(root)["config"])
                _print(deploy.restore_database(root, database=Path(settings.storage_path),  # type: ignore[arg-type]
                                               key_file=deploy.layout(root)["chaves"] / "backup.key",
                                               backup_name=args.restaurar_banco, confirmation=args.confirmacao or ""))
            else:
                _print(deploy.rollback_code(root))
        elif args.command == "alertas":
            from rocha_reis_agent.ops import alerts
            from rocha_reis_agent.ops.backup_job import job_config, load_status
            from rocha_reis_agent.ops.demo import profile_settings
            from rocha_reis_agent.ops.notify import dispatch
            profile = Path(args.perfil).expanduser().absolute()
            settings = profile_settings(profile)
            if settings.storage_volume_mount and settings.storage_volume_id:
                # Fase 18B-0: primeiro o volume — bloqueado/incorreto, o banco não está lá para avaliar.
                from rocha_reis_agent.persistence.storage_security import check_data_volume
                volume = check_data_volume(settings.storage_volume_mount,
                                           {"id": settings.storage_volume_id,
                                            "luks_uuid": settings.storage_volume_luks_uuid},
                                           paths=(Path(settings.storage_path).parent,))  # type: ignore[arg-type]
                blocked = alerts.evaluate_volume(volume.as_dict())
                if blocked is not None:
                    blocked["notificacao_externa"] = dispatch(blocked)
                    print(alerts.dumps(blocked))
                    return alerts.exit_code(blocked)
            backups = profile / str(job_config(profile)["primario"])
            result = alerts.evaluate(Path(settings.storage_path), backups=backups,  # type: ignore[arg-type]
                                     heartbeat=Path(args.batimento) if args.batimento else None,
                                     offsite_state=Path(args.externo) if args.externo else None,
                                     database_limit_bytes=settings.storage_max_bytes,
                                     backup_status=load_status(profile))
            result["notificacao_externa"] = dispatch(result)
            print(alerts.dumps(result))
            return alerts.exit_code(result)
        elif args.command == "ensaio-operacional":
            from rocha_reis_agent.ops.opsdrill import run_drill
            report = run_drill(Path(args.destino).expanduser())
            _print(report)
            return 0 if report.get("resultado") == "OK" else 1
        elif args.command == "carga":
            from rocha_reis_agent.ops.loadtest import run_load
            _print(run_load(Path(args.destino).expanduser(), conversations=args.conversas, operators=args.operadores,
                            seconds=args.segundos))
        elif args.command == "backup-externo":
            from rocha_reis_agent.ops import offsite
            from rocha_reis_agent.ops.backup_job import job_config
            profile = Path(args.perfil).expanduser().absolute()
            backups = profile / str(job_config(profile)["primario"])
            state = backups.parent / "estado-copia-externa.json"
            _print(offsite.sync(backups, _external_store(profile, args), state, keep=args.manter))
        elif args.command == "baixar-externo":
            from rocha_reis_agent.ops import offsite
            from rocha_reis_agent.ops.backup import read_manifest
            profile = Path(args.perfil).expanduser().absolute()
            target = offsite.fetch(_external_store(profile, args), args.nome, Path(args.para).expanduser())
            read_manifest(target)  # é um backup do projeto; a restauração (ops restaurar) confere a autenticidade
            _print({"baixado": str(target), "proximo_passo": "ops verificar / ops restaurar (arquivo NOVO e isolado)"})
    except (deploy.DeployError, BackupError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 — só a falha do destino externo, com mensagem segura
        from rocha_reis_agent.ops.offsite import ExternalError
        if not isinstance(error, ExternalError):
            raise
        print(f"ERRO: {error}", file=sys.stderr)
        return 2
    return 0


def _external_store(profile: Path, args):  # type: ignore[no-untyped-def]
    """Destino externo: diretório FALSO ou o adaptador S3 do perfil (endpoint real bloqueado pela trava)."""
    from rocha_reis_agent.ops import offsite
    if getattr(args, "destino_falso", None):
        return offsite.FakeExternalStore(Path(args.destino_falso))
    import json as _json
    from rocha_reis_agent.ops import s3_store
    external = (_json.loads((profile / "perfil.json").read_text(encoding="utf-8")).get("backup") or {}).get("externo")
    if not isinstance(external, dict):
        raise offsite.ExternalError("Perfil sem a seção backup.externo.")
    return s3_store.from_profile(profile, external)


def _rehearse(database: Path) -> dict[str, object]:
    """Abre a cópia com ESTE código (aplica migrações) e roda integrity_check; nunca o banco em uso."""
    from contextlib import closing
    import sqlite3
    from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION, SQLiteConversationRepository
    database = Path(database).absolute()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        before = connection.execute("PRAGMA user_version").fetchone()[0]
        try:
            marked = dict(connection.execute("SELECT key,value FROM metadata").fetchall()).get("environment_profile")
        except sqlite3.Error:
            marked = None
    # Abre como o ambiente gravado no próprio banco (a marcação impede abrir com outro perfil); a cópia de
    # pré-produção continua exigindo volume cifrado verificado.
    SQLiteConversationRepository(database, environment=marked,
                                 require_encrypted_volume=marked == "preproduction")  # migrações transacionais
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        after = connection.execute("PRAGMA user_version").fetchone()[0]
    if integrity != "ok" or foreign or after != SCHEMA_VERSION:
        raise BackupError("Cópia do banco reprovada após a migração (integridade ou versão).")
    return {"esquema_antes": before, "esquema_depois": after, "integridade": integrity}


if __name__ == "__main__":
    raise SystemExit(main())
