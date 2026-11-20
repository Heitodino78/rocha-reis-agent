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
    args = parser.parse_args(argv)
    try:
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


if __name__ == "__main__":
    raise SystemExit(main())
