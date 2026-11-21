"""Medição de operação (Fase 17) num servidor SIMULADO local — dados sintéticos, transporte simulado.

    python -m rocha_reis_agent.ops carga --destino DIR_NOVO [--conversas 1000] [--operadores 4] [--segundos 30]

Mede, com os processos reais (webhook, painel, worker) da versão empacotada: tempo de inicialização,
vazão do webhook + worker, latência das consultas do painel com N operadores simultâneos enquanto o worker
processa mensagens e um backup roda, memória (RSS) e CPU dos processos, tamanho do banco e tempo de
reinício. Os números valem para ESTA máquina; não são garantia para um servidor futuro.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import sys
from threading import Event, Thread
import time

from rocha_reis_agent.ops import deploy
from rocha_reis_agent.ops.backup import BackupError
from rocha_reis_agent.ops.localserver import LocalServices
from rocha_reis_agent.ops.opsdrill import ORIGIN, _panel, _q, _send, _wait


def _get(port: int, path: str, cookie: str) -> tuple[int | None, float]:
    request = (f"GET {path} HTTP/1.1\r\nHost: painel.interno\r\nCookie: {cookie}\r\nConnection: close\r\n\r\n").encode()
    started = time.perf_counter()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(("127.0.0.1", port))
        connection.sendall(request)
        chunks = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    elapsed = (time.perf_counter() - started) * 1000
    line = b"".join(chunks).split(b"\r\n", 1)[0].split()
    return (int(line[1]) if len(line) >= 2 and line[1].isdigit() else None), elapsed


def _proc(pid: int) -> dict[str, float]:
    status = Path(f"/proc/{pid}/status").read_text()
    rss = next(int(l.split()[1]) for l in status.splitlines() if l.startswith("VmRSS:")) / 1024
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    return {"rss_mb": round(rss, 1), "cpu_s": round(cpu, 2)}


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * p))], 1) if ordered else 0.0


def run_load(destination: Path, *, conversations: int = 1000, operators: int = 4, seconds: float = 30.0,
             python: str | None = None) -> dict[str, object]:
    from rocha_reis_agent.ops import backup_job
    from rocha_reis_agent.panel import mfa as mfa_module
    from rocha_reis_agent.persistence.storage_security import volume_protection
    if not 250 <= conversations <= 1000 or not 1 <= operators <= 16 or not 5 <= seconds <= 600:
        raise BackupError("Use 250–1000 conversas (teto de conversas ativas), 1–16 operadores e 5–600 segundos.")
    destination = Path(destination).absolute()
    if destination.exists() and any(destination.iterdir()):
        raise BackupError("O destino precisa ser um diretório novo ou vazio.")
    protection = volume_protection(destination.parent if not destination.exists() else destination)
    if not (protection.encrypted and protection.persistent):
        raise BackupError(f"Medição exige volume com cifragem verificada: {protection.describe()}.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = destination / "servidor"
    package = deploy.build_package(destination / "pacotes")
    installed = deploy.install_release(package, root, python=python or sys.executable, create_venv=False)
    deploy.activate(root, str(installed["id"]))
    deploy.prepare_instance(root, panel_origin=ORIGIN)
    profile = deploy.layout(root)["config"]
    initial = conversations - 201  # + 1 conversa longa + 200 que chegam durante a medição = total pedido
    services = LocalServices(root, python=python)
    result: dict[str, object] = {
        "ambiente": {"maquina": platform.machine(), "cpus": os.cpu_count(), "python": platform.python_version(),
                     "sistema": platform.system(), "volume": protection.describe()},
        "aviso": "Números desta máquina, com dados sintéticos e envio simulado; não garantem o servidor futuro."}
    try:
        started = time.monotonic()
        services.start()
        if not services.ready(60):
            raise BackupError("Serviços não ficaram prontos.")
        result["inicializacao_s"] = round(time.monotonic() - started, 2)
        # carga inicial: N conversas pelo webhook, processadas pelo worker
        started = time.monotonic()
        latencies: list[float] = []
        statuses: list[int | None] = []
        def send(i: int) -> None:
            t = time.perf_counter()
            statuses.append(_send(root, services, f"wamid.carga.{i}", "Quero falar com uma pessoa" if i % 10 == 0 else
                                  f"Quanto custa o corte? ({i})", f"5511{i:09d}"))
            latencies.append((time.perf_counter() - t) * 1000)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(send, range(initial)))
        accepted = statuses.count(200)
        done = _wait(lambda: _q(root, "SELECT count(*) FROM channel_inbox WHERE kind='message' AND status='DONE'")[0][0]
                     >= accepted, 600)
        elapsed = time.monotonic() - started
        result["carga_inicial"] = {"conversas": initial, "aceitas_pelo_webhook": accepted,
                                   "recusadas_pelo_webhook": len(statuses) - accepted, "processadas": done,
                                   "segundos": round(elapsed, 1),
                                   "mensagens_por_segundo": round(initial / elapsed, 1),
                                   "webhook_ms_p50": _pct(latencies, 0.5), "webhook_ms_p95": _pct(latencies, 0.95)}
        # histórico extenso numa conversa
        for i in range(150):
            _send(root, services, f"wamid.carga.longa.{i}", f"Quanto custa a escova? ({i})", "5511888888888")
        _wait(lambda: _q(root, "SELECT count(*) FROM channel_inbox WHERE status IN ('PENDING','PROCESSING')")[0][0] == 0, 300)
        # operador autenticado (senha + 2º fator) para obter a sessão usada pelos N operadores
        password = (profile / "credenciais-iniciais.txt").read_text().splitlines()[1].split("\t")[2]
        panel = _panel(root)
        panel.login("admin.preprod", password)
        new_password = "Carga-Senha-Definitiva-2026!"
        panel.post("/auth/password", {"current_password": password, "new_password": new_password})
        setup = panel.post("/auth/mfa/setup", {"password": new_password}).json()
        raw = setup["secret"].replace(" ", "")
        secret = __import__("base64").b32decode(raw + "=" * (-len(raw) % 8))
        panel.post("/auth/mfa/activate", {"code": mfa_module.code_for(secret)})
        cookie_name = "__Host-rr_panel"
        cookie = f"{cookie_name}={panel.client.cookies.get(cookie_name)}"
        long_id = _q(root, "SELECT c.id FROM conversations c JOIN messages m ON m.conversation_id=c.id GROUP BY c.id "
                           "ORDER BY count(*) DESC LIMIT 1")[0][0]
        paths = ["/api/panel/conversations?filter=all", "/api/panel/conversations?filter=waiting", "/api/panel/overview",
                 f"/api/panel/conversations/{long_id}", "/api/panel/notifications", "/api/panel/conversations?filter=all&q=escova"]
        stop = Event()
        timings: dict[str, list[float]] = {p.split("?")[0].rsplit("/", 1)[-1] if long_id not in p else "detalhe_longo": []
                                          for p in paths}
        errors: list[int | None] = []
        def operator(index: int) -> None:
            i = index
            while not stop.is_set():
                path = paths[i % len(paths)]
                code, ms = _get(services.ports["panel"], path, cookie)
                key = "detalhe_longo" if long_id in path else path.split("?")[0].rsplit("/", 1)[-1]
                (timings[key] if code == 200 else errors).append(ms if code == 200 else code)  # type: ignore[arg-type]
                i += 1
        # durante a medição: mais 200 mensagens chegando e um backup rodando
        background_sent: list[int] = []
        def traffic() -> None:
            for i in range(200):
                if stop.is_set():
                    break
                _send(root, services, f"wamid.carga.durante.{i}", f"Quanto custa a coloração? ({i})", f"5512{i:09d}")
                background_sent.append(i)
                time.sleep(seconds / 220)
        backup_result: dict[str, object] = {}
        def backup() -> None:
            time.sleep(seconds / 3)
            t = time.monotonic()
            status = backup_job.run_backup_job(profile)
            backup_result.update(segundos=round(time.monotonic() - t, 2), bytes=status.get("tamanho_bytes"))
        threads = [Thread(target=operator, args=(n,)) for n in range(operators)] + [Thread(target=traffic),
                                                                                     Thread(target=backup)]
        before = {name: _proc(p.pid) for name, p in services.processes.items()}
        for thread in threads:
            thread.start()
        time.sleep(seconds)
        stop.set()
        for thread in threads:
            thread.join(timeout=120)
        after = {name: _proc(p.pid) for name, p in services.processes.items()}
        all_ms = [ms for values in timings.values() for ms in values]
        result["operadores_simultaneos"] = {
            "operadores": operators, "segundos": seconds, "requisicoes": len(all_ms), "erros": len(errors),
            "codigos_erro": {str(c): errors.count(c) for c in sorted(set(errors), key=str)},
            "requisicoes_por_segundo": round(len(all_ms) / seconds, 1),
            "ms_p50": _pct(all_ms, 0.5), "ms_p95": _pct(all_ms, 0.95), "ms_max": round(max(all_ms), 1) if all_ms else None,
            "por_consulta_ms_p95": {k: _pct(v, 0.95) for k, v in timings.items()},
            "mensagens_durante": len(background_sent), "backup_durante": backup_result}
        result["processos"] = {name: {"rss_mb": after[name]["rss_mb"],
                                      "cpu_s_durante_medicao": round(after[name]["cpu_s"] - before[name]["cpu_s"], 2)}
                               for name in after}
        database = Path(json.loads((profile / "perfil.json").read_text())["database"])
        size = sum(p.stat().st_size for p in database.parent.glob(database.name + "*"))
        total = _q(root, "SELECT count(*) FROM conversations")[0][0]
        messages = _q(root, "SELECT count(*) FROM messages")[0][0]
        result["banco"] = {"bytes": size, "conversas": total, "mensagens": messages,
                           "bytes_por_mensagem": round(size / max(messages, 1))}
        # reinício sob volume
        started = time.monotonic()
        services.stop()
        services.start()
        result["reinicio_ate_pronto_s"] = round(time.monotonic() - started, 2) if services.ready(60) else None
        result["resultado"] = ("OK" if not errors and done and accepted == initial
                               and result["reinicio_ate_pronto_s"] is not None else "COM_FALHAS")
    finally:
        services.stop(timeout=15)
    descriptor = os.open(destination / "relatorio-carga.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return result
