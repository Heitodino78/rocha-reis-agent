"""Inicialização local do painel (Fase 10): verificações claras, só 127.0.0.1, parada segura.

Sem Docker, sem porta pública. Ctrl+C encerra com desligamento gracioso (o worker embutido
para antes do processo sair).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import socket
import sys

from rocha_reis_agent.ops.demo import default_profile, profile_settings

HOST = "127.0.0.1"  # fixo: o painel nunca escuta em interfaces externas nesta fase
REQUIRED = ("fastapi", "uvicorn", "pydantic_settings", "argon2", "cryptography")

__all__ = ["default_profile", "launch", "preflight"]


class LaunchError(Exception):
    pass


def preflight(profile: Path, port: int) -> list[str]:
    """Retorna as verificações aprovadas; levanta LaunchError com instrução clara na primeira falha."""
    done: list[str] = []
    if sys.version_info < (3, 11):
        raise LaunchError("Python 3.11 ou mais novo é necessário.")
    done.append(f"Python {sys.version_info.major}.{sys.version_info.minor}")
    missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
    if missing:
        raise LaunchError(f"Dependências ausentes: {', '.join(missing)}. Instale com: pip install -r requirements.lock")
    done.append("dependências instaladas")
    if not (profile / "perfil.json").is_file():
        raise LaunchError(f"Perfil não encontrado em {profile}. Crie a demonstração: python -m rocha_reis_agent.ops demo")
    for path, expected in ((profile, 0o700), (profile / "perfil.json", 0o600)):
        if path.stat().st_mode & 0o777 != expected or path.stat().st_uid != os.geteuid():
            raise LaunchError(f"Permissões inseguras em {path.name} (esperado {expected:o}). Corrija com chmod {expected:o}.")
    done.append("perfil privado (0700/0600)")
    if not 1024 <= port <= 65535:
        raise LaunchError("Use uma porta entre 1024 e 65535.")
    settings = profile_settings(profile, port=port)
    from rocha_reis_agent import config as config_module
    # Revisão (22/11): conferia só 3 travas; dados reais (Fase 12) e backup externo (18B-0) ficavam de fora.
    if any(config_module.activation_locks().values()):
        raise LaunchError("Trava de integração real ativa no código: o iniciador local recusa executar.")
    if settings.whatsapp_transport != "fake" or settings.ai_provider != "fake":
        raise LaunchError("O iniciador local só executa com transporte e IA SIMULADOS.")
    done.append("travas reais desligadas; transporte e IA simulados")
    database = settings.storage_path
    if database is None or not database.is_file():
        raise LaunchError("Banco do perfil não encontrado. Recrie: python -m rocha_reis_agent.ops demo --recriar")
    from rocha_reis_agent.bootstrap import configured_repository
    from rocha_reis_agent.domain.contracts import StorageError
    try:
        configured_repository(settings)
    except StorageError as error:
        raise LaunchError(f"Banco inválido: {error}") from None
    done.append("banco íntegro e migrado")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Revisão (22/11): sem SO_REUSEADDR, conexões em TIME_WAIT do servidor anterior (Ctrl+C com o painel
        # aberto) davam "Porta ocupada" por ~60 s, embora o uvicorn (que usa SO_REUSEADDR) subisse. No Linux a
        # opção NÃO libera uma porta com alguém escutando: o conflito real continua recusado.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, port))
        except OSError:
            raise LaunchError(f"Porta {port} ocupada em {HOST}. Use --porta com outro número.") from None
    done.append(f"porta {port} livre em {HOST}")
    return done


def server_config(app, settings, port: int, profile: Path | None = None):  # type: ignore[no-untyped-def]
    """Configuração do uvicorn (Fase 13): só 127.0.0.1; cabeçalhos X-Forwarded-* SOMENTE de proxies
    explicitamente confiáveis (PANEL_TRUSTED_PROXIES); TLS quando o perfil tem certificado."""
    import uvicorn
    trusted = settings.panel_trusted_proxies.replace(" ", "")
    tls = {}
    if profile is not None and (profile / "tls" / "dev.crt").is_file():
        tls = {"ssl_certfile": str(profile / "tls" / "dev.crt"), "ssl_keyfile": str(profile / "tls" / "dev.key")}
    return uvicorn.Config(app, host=HOST, port=port, access_log=False, log_level="warning",
                          timeout_graceful_shutdown=5, proxy_headers=bool(trusted),
                          forwarded_allow_ips=trusted or None, **tls)


def launch(profile: Path, *, port: int = 8000, check_only: bool = False) -> int:
    try:
        checks = preflight(Path(profile).absolute(), port)
    except LaunchError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2
    for item in checks:
        print(f"  ok  {item}")
    if check_only:
        return 0
    import uvicorn
    from rocha_reis_agent.panel.app import create_panel_app
    profile = Path(profile).absolute()
    settings = profile_settings(profile, port=port)
    app = create_panel_app(settings)
    server = uvicorn.Server(server_config(app, settings, port, profile))
    credentials = sorted(profile.glob("credenciais-*.txt"))
    logins = [line.split("\t")[0] for path in credentials
              for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]

    def announce_when_ready() -> None:
        # Fase 15: "pronto" só quando o servidor de fato aceita conexões (antes era impresso antes de subir).
        import threading
        import time
        def wait() -> None:
            deadline = time.monotonic() + 20
            while not server.started and not server.should_exit and time.monotonic() < deadline:
                time.sleep(0.1)
            if server.started:
                lines = [f"\n  PRONTO — abra no navegador: {settings.panel_origin}/panel/   (somente neste computador)"]
                if settings.panel_origin.startswith("https://"):
                    lines.append("  Certificado de DESENVOLVIMENTO (autoassinado): o navegador vai alertar; "
                                 "não é certificado público.")
                if logins:
                    # Caminho COMPLETO: no VS Code em Flatpak a pasta de dados não é ~/.local/share (achado real).
                    lines.append(f"  Logins fictícios: {', '.join(logins)}")
                    lines.append(f"  Senhas (só neste computador): {credentials[0]}")
                lines.append("  Para encerrar com segurança: Ctrl+C\n")
                print("\n".join(lines), flush=True)  # de uma vez: o aviso nunca sai pela metade
        threading.Thread(target=wait, daemon=True).start()

    print("\nIniciando o painel…", flush=True)
    announce_when_ready()
    try:
        # O uvicorn conclui o desligamento gracioso (worker embutido parado) e então repassa o Ctrl+C.
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass  # o uvicorn encerra assim quando não consegue abrir a porta
    if not server.started:
        print(f"ERRO: o painel não conseguiu iniciar na porta {port}. Feche outro programa que a use "
              f"ou rode com --porta e outro número.", file=sys.stderr)
        return 2
    print("Painel encerrado.")
    return 0
