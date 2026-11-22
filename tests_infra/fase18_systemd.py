"""Fase 18 — validação EXECUTÁVEL das unidades systemd de deploy/systemd/ (não é teste unitário).

Roda as unidades REAIS (o mesmo texto de deploy/systemd, inclusive todo o endurecimento) no systemd do
USUÁRIO, sem root, a partir de uma instalação feita pelo fluxo oficial (empacotar → instalar →
preparar-servidor → validar-instalacao) numa raiz descartável em volume cifrado. Só dados sintéticos,
transporte e IA simulados, portas só em 127.0.0.1. Ao final remove as unidades (a raiz é mantida para
inspeção apenas com --manter).

Diferenças INEVITÁVEIS em relação ao servidor (registradas no relatório, não escondidas):
  - sem `User=/Group=` (trocar de usuário exige o systemd do sistema, ou seja, root);
  - caminhos /opt, /etc, /var/lib, /var/log reescritos para dentro da raiz de teste;
  - `ProtectHome=true` vira `ProtectHome=tmpfs` + bind somente leitura da raiz e do Python (a raiz está
    no $HOME cifrado; o efeito — nada do $HOME visível ao serviço — é o mesmo);
  - no manager do usuário o isolamento usa namespace de usuário (PrivateUsers implícito).

Uso (no host, com o Python 3.13 de um venv do requirements.lock):
    python tests_infra/fase18_systemd.py --destino DIR_NOVO --python /caminho/python3.13 [--manter]
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from rocha_reis_agent.ops import deploy  # noqa: E402
from rocha_reis_agent.ops.opsdrill import _envelope, _post_local  # noqa: E402
from rocha_reis_agent.channels.meta import sign  # noqa: E402

SERVICES = ("rocha-reis-webhook.service", "rocha-reis-panel.service", "rocha-reis-worker.service")
UNIT_FILES = sorted(p.name for p in (REPO / "deploy" / "systemd").iterdir() if p.suffix in (".service", ".timer")
                    and p.name != "rocha-reis-volume.service")  # portão do volume: só no roteiro da 18B-0
ORIGIN = "https://painel.interno:18443"
PANEL_HOST = "painel.interno"
SECRET_KEYS = ("whatsapp_verify_token", "whatsapp_app_secret", "whatsapp_conversation_key")


# ------------------------------------------------------------------ utilidades
def run(cmd: list[str], *, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True, timeout=timeout)


def sc(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["systemctl", "--user", *args], check=check)


def show(unit: str, *props: str) -> dict[str, str]:
    out = sc("show", unit, "-p", ",".join(props)).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def wait(condition, timeout: float, interval: float = 0.25) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return True
        except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError, KeyError):
            pass
        time.sleep(interval)
    return False


def journal(unit: str | None = None, *, since: str, identifier: str | None = None) -> str:
    cmd = ["journalctl", "--user", "--no-pager", "-o", "cat", "--since", since]
    if unit:
        cmd += ["-u", unit]
    if identifier:
        cmd += ["-t", identifier]
    return run(cmd, check=False).stdout


class Report:
    def __init__(self) -> None:
        self.steps: list[dict[str, object]] = []

    def step(self, name: str, ok: bool, started: float, **details: object) -> bool:
        entry = {"etapa": name, "ok": bool(ok), "segundos": round(time.monotonic() - started, 2), **details}
        self.steps.append(entry)
        print(("OK   " if ok else "FALHA ") + name, json.dumps(details, ensure_ascii=False, default=str)[:400],
              flush=True)
        return ok


# ------------------------------------------------------------------ unidades
SERVER_MOUNT_UNIT = r"var-lib-rocha\x2dreis\x2dagent.mount"
VOLUME_LINES = ("Requires=rocha-reis-volume.service", "After=rocha-reis-volume.service",
                f"BindsTo={SERVER_MOUNT_UNIT}", f"After={SERVER_MOUNT_UNIT}")


def render_unit(text: str, root: Path, python_home: Path, *, mount_unit: str | None = None,
                identity: Path | None = None) -> str:
    """`mount_unit=None` (roteiro da 18A, raiz no $HOME sem volume dedicado): as dependências do volume
    (Fase 18B-0) são omitidas — cobertas por tests_infra/fase18b0_volume.py. Com `mount_unit`, a unidade de
    montagem do servidor é trocada pela do disco de teste e o portão recebe `--identidade` (laboratório)."""
    if mount_unit is None:
        text = "\n".join(f"# Fase 18 (sem volume dedicado): '{l}' omitido" if l in VOLUME_LINES else l
                         for l in text.splitlines()) + "\n"
    else:
        text = text.replace(SERVER_MOUNT_UNIT, mount_unit)
        if identity is not None:
            text = re.sub(r"verificar-volume --raiz /$", f"verificar-volume --raiz / --identidade {identity}",
                          text, flags=re.MULTILINE)
    text = re.sub(r"--raiz /$", f"--raiz {root}", text, flags=re.MULTILINE)
    text = re.sub(r"--raiz / --identidade", f"--raiz {root} --identidade", text)
    for path in ("/opt/rocha-reis-agent", "/etc/rocha-reis-agent", "/var/lib/rocha-reis-agent",
                 "/var/log/rocha-reis-agent"):
        text = text.replace(path, f"{root}{path}")
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith(("User=", "Group=")):
            out.append(f"# Fase 18 (systemd do usuário): '{line}' omitido — trocar de usuário exige root")
        elif line == "ProtectHome=true":
            # ProtectHome também esconde /run/user/UID, onde fica o NOTIFY_SOCKET do manager do USUÁRIO (no
            # servidor ele fica em /run/systemd/notify, fora do alcance de ProtectHome): bind só do socket.
            out += ["# Fase 18: ProtectHome=true -> tmpfs + bind somente leitura da raiz de teste e do Python",
                    # Com disco de teste (18B-0) a raiz fica DENTRO do disco: "-" deixa as unidades que precisam
                    # rodar sem ele (alertas, alerta@, portão) subirem — no servidor não dependem do volume.
                    "ProtectHome=tmpfs", f"BindReadOnlyPaths={'-' if mount_unit else ''}{root}",
                    f"BindReadOnlyPaths={python_home}",
                    f"BindReadOnlyPaths=-{os.environ['XDG_RUNTIME_DIR']}/systemd/notify"]
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def unit_dir() -> Path:
    return Path(os.environ["XDG_RUNTIME_DIR"]) / "systemd" / "user"


def install_units(root: Path, python_home: Path, send_log: Path) -> list[str]:
    target = unit_dir()
    target.mkdir(parents=True, exist_ok=True)
    for search in (Path.home() / ".config/systemd/user", target):
        clash = [p.name for p in search.glob("rocha-reis-*")] if search.exists() else []
        if clash:
            raise SystemExit(f"Já existem unidades rocha-reis em {search}: {clash} — nada foi alterado.")
    written = []
    for name in UNIT_FILES:
        text = (REPO / "deploy" / "systemd" / name).read_text(encoding="utf-8")
        (target / name).write_text(render_unit(text, root, python_home), encoding="utf-8")
        written.append(name)
    # Instrumentação SÓ do transporte FALSO: registra cada envio aceito (prova de não duplicidade após kill -9).
    dropin = target / "rocha-reis-worker.service.d"
    dropin.mkdir(exist_ok=True)
    (dropin / "fase18-registro.conf").write_text(
        "[Service]\n# Fase 18: registro dos envios do transporte FALSO (só existe para o fake)\n"
        f"Environment=WORKER_FAKE_SEND_LOG={send_log}\n", encoding="utf-8")
    sc("daemon-reload")
    return written


def remove_units() -> None:
    target = unit_dir()
    names = [p.name for p in target.glob("rocha-reis-*")] if target.exists() else []
    loaded = [n for n in names if n.endswith((".service", ".timer")) and "@." not in n]
    if loaded:
        sc("stop", *loaded, check=False)
    sc("stop", "rocha-reis-alerta@*", check=False)
    for name in names:
        path = target / name
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    sc("daemon-reload", check=False)
    sc("reset-failed", check=False)


# ------------------------------------------------------------------ aplicação
def profile(root: Path) -> dict:  # type: ignore[type-arg]
    return json.loads((deploy.layout(root)["config"] / "perfil.json").read_text(encoding="utf-8"))


def database(root: Path) -> Path:
    return Path(profile(root)["database"])


def q(root: Path, sql: str, *params: object) -> list[tuple]:  # type: ignore[type-arg]
    with closing(sqlite3.connect(database(root).as_uri() + "?mode=ro", uri=True, timeout=10)) as c:
        return c.execute(sql, params).fetchall()


def post(root: Path, wamid: str, text: str, sender: str) -> int | None:
    secret = profile(root)["settings"]["whatsapp_app_secret"]
    raw = _envelope(wamid, text, sender)
    return _post_local("http://127.0.0.1:8080/webhooks/whatsapp", raw,
                       {"Content-Type": "application/json", "X-Hub-Signature-256": sign(secret.encode(), raw)})


def ready_both() -> bool:
    return (deploy.http_status("http://127.0.0.1:8080/ready") == 200
            and deploy.http_status("http://127.0.0.1:8000/ready", host=PANEL_HOST) == 200)


def alert_codes() -> tuple[list[str], str]:
    """Roda a UNIDADE de alertas e lê os códigos da saída DESSA execução (journal por InvocationID)."""
    sc("start", "rocha-reis-alertas.service", check=False)
    props = show("rocha-reis-alertas.service", "InvocationID", "ExecMainStatus")
    out = run(["journalctl", "--user", "--no-pager", "-o", "cat",
               f"_SYSTEMD_INVOCATION_ID={props['InvocationID']}"], check=False).stdout
    sc("reset-failed", "rocha-reis-alertas.service", check=False)
    return sorted(set(re.findall(r'"codigo": "([a-z_]+)"', out))), props["ExecMainStatus"]


def panel_login(login: str, forwarded_for: str) -> int | None:
    """Login errado direto no painel (127.0.0.1, como o Caddy faria repassando o IP de cada aparelho)."""
    import http.client
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=5)
    try:
        connection.request("POST", "/api/panel/auth/login", body=json.dumps({"login": login, "password": "errada!"}),
                           headers={"Host": PANEL_HOST, "Origin": ORIGIN, "Content-Type": "application/json",
                                    "X-Forwarded-For": forwarded_for})
        return connection.getresponse().status
    except OSError:
        return None
    finally:
        connection.close()


def main_pid(unit: str) -> int:
    return int(show(unit, "MainPID")["MainPID"])


def active(unit: str) -> bool:
    return show(unit, "ActiveState")["ActiveState"] == "active"


def mounts(pid: int) -> dict[str, str]:
    """ponto de montagem -> opções (de /proc/PID/mountinfo do serviço)."""
    result = {}
    for line in Path(f"/proc/{pid}/mountinfo").read_text().splitlines():
        fields = line.split()
        result[fields[4].replace("\\040", " ")] = fields[5]
    return result


def mount_for(table: dict[str, str], path: str) -> tuple[str, str]:
    best = max((m for m in table if path == m or path.startswith(m.rstrip("/") + "/")), key=len)
    return best, table[best]


# ------------------------------------------------------------------ roteiro
def execute(dest: Path, python: str, keep: bool) -> dict[str, object]:
    from rocha_reis_agent.persistence.storage_security import volume_protection
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    r = Report()
    root = dest / "raiz"
    python_home = Path(python).resolve().parents[1]
    send_log = deploy.layout(root)["estado"] / "envios-falsos.jsonl"
    info: dict[str, object] = {"destino": str(dest), "python": python, "systemd": run(["systemctl", "--version"]).stdout
                               .splitlines()[0], "volume": volume_protection(dest).describe(),
                               "desvios_do_servidor": ["sem User=/Group= (exige root)",
                                                       "caminhos reescritos para a raiz de teste",
                                                       "ProtectHome=tmpfs + BindReadOnlyPaths da raiz/Python",
                                                       "bind somente leitura do NOTIFY_SOCKET do manager do usuário "
                                                       "(escondido por ProtectHome; no servidor fica em /run/systemd)",
                                                       "systemd do usuário (namespace de usuário implícito)",
                                                       "WORKER_FAKE_SEND_LOG por drop-in (só transporte falso)"]}
    try:
        # 1. instalação pelo fluxo oficial (pacote verificado, venv do requirements.lock com hashes)
        t = time.monotonic()
        package = deploy.build_package(dest / "pacotes")
        installed = deploy.install_release(package, root, python=python, create_venv=True)
        deploy.activate(root, str(installed["id"]))
        deploy.prepare_instance(root, panel_origin=ORIGIN, laboratorio=True)
        validation = deploy.validate_installation(root)
        info.update(versao=installed["versao"], release=installed["id"])
        r.step("instalacao_fluxo_oficial", validation["ok"], t, problemas=validation["problemas"],
               travas=validation["travas"])

        # 2. unidades reais no systemd do usuário
        t = time.monotonic()
        written = install_units(root, python_home, send_log)
        verify = run(["systemd-analyze", "--user", "verify", *[str(unit_dir() / n) for n in written
                                                                if "@." not in n]], check=False)
        r.step("unidades_carregadas", verify.returncode == 0, t, unidades=written,
               verify=(verify.stderr or verify.stdout).strip()[-600:])

        # 3. inicialização (ExecStartPre validar-instalacao + Type=notify do worker)
        t = time.monotonic()
        sc("start", *SERVICES)
        up = wait(lambda: all(active(u) for u in SERVICES) and ready_both(), 60)
        worker = show("rocha-reis-worker.service", "Type", "StatusText", "WatchdogUSec", "NotifyAccess", "LimitCORE")
        r.step("inicializacao", up and worker["LimitCORE"] == "0", t, worker=worker,
               estados={u: show(u, "ActiveState", "SubState")["SubState"] for u in SERVICES})

        # 4. comunicação entre processos: webhook -> banco -> worker -> saída (transporte falso)
        t = time.monotonic()
        codes = [post(root, f"wamid.f18.a{i}", "Quero falar com uma pessoa" if i == 0 else
                      f"Quanto custa o corte? ({i})", f"55119000081{i:02d}") for i in range(12)]
        done = wait(lambda: q(root, "SELECT count(*) FROM channel_inbox WHERE kind='message' AND status='DONE'")
                    [0][0] == 12 and q(root, "SELECT count(*) FROM channel_outbox WHERE status IN "
                                             "('PENDING','RETRY','SENDING')")[0][0] == 0, 60)
        accepted = q(root, "SELECT count(*) FROM channel_outbox WHERE status='ACCEPTED'")[0][0]
        waiting = q(root, "SELECT count(*) FROM conversations WHERE state='WAITING_HUMAN'")[0][0]
        r.step("mensagens_via_servicos", done and set(codes) == {200} and accepted >= 12 and waiting == 1, t,
               http=sorted(set(codes)), saidas_aceitas=accepted, aguardando_humano=waiting)

        # 5. isolamento e segredos, observados nos processos REAIS
        t = time.monotonic()
        secrets_values = [profile(root)["settings"][k] for k in SECRET_KEYS]
        findings: dict[str, object] = {}
        ok = True
        for unit in SERVICES:
            pid = main_pid(unit)
            env = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
            status = dict(line.split(":\t", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                          if ":\t" in line)
            table = mounts(pid)
            cfg = mount_for(table, str(deploy.layout(root)["config"]))
            data = mount_for(table, str(deploy.layout(root)["dados"]))
            code = mount_for(table, str(deploy.layout(root)["current"].resolve()))
            home_entries = sorted(os.listdir(f"/proc/{pid}/root{Path.home()}"))
            ssh_visible = Path(f"/proc/{pid}/root{Path.home()}/.ssh").exists()
            tmp_private = os.stat(f"/proc/{pid}/root/tmp").st_ino != os.stat("/tmp").st_ino
            leak = any(s in env or s in cmd for s in secrets_values)
            unit_ok = (not leak and status["NoNewPrivs"].strip() == "1" and status["Seccomp"].strip() == "2"
                       and int(status["CapEff"], 16) == 0 and int(status["CapBnd"], 16) == 0
                       and "ro" in cfg[1].split(",") and "rw" in data[1].split(",")
                       and "ro" in code[1].split(",") and not ssh_visible and tmp_private)
            ok &= unit_ok
            findings[unit] = {"segredo_no_ambiente_ou_linha_de_comando": leak,
                              "NoNewPrivs": status["NoNewPrivs"].strip(), "Seccomp": status["Seccomp"].strip(),
                              "CapEff": status["CapEff"].strip(), "CapBnd": status["CapBnd"].strip(),
                              "perfil_montagem": cfg[1].split(",")[0], "dados_montagem": data[1].split(",")[0],
                              "codigo_montagem": code[1].split(",")[0], "home_visivel": home_entries,
                              "ssh_visivel": ssh_visible, "tmp_privado": tmp_private,
                              "variaveis": sorted(e.split("=", 1)[0] for e in env.split("\0") if "=" in e)}
        r.step("isolamento_e_segredos", ok, t, **findings)

        # 6. encerramento limpo (SIGTERM) de cada serviço
        t = time.monotonic()
        stops = {}
        for unit in ("rocha-reis-worker.service", "rocha-reis-webhook.service", "rocha-reis-panel.service"):
            s = time.monotonic()
            sc("stop", unit)
            props = show(unit, "Result", "ExecMainCode", "ExecMainStatus", "ActiveState")
            stops[unit] = {**props, "segundos": round(time.monotonic() - s, 2)}
        clean = all(v["Result"] == "success" and v["ActiveState"] == "inactive" for v in stops.values())
        sc("start", *SERVICES)
        back = wait(lambda: all(active(u) for u in SERVICES) and ready_both(), 60)
        r.step("sigterm_encerramento_limpo", clean and back, t, **stops)

        # 7. queda abrupta (kill -9) no meio de uma rajada: Restart=on-failure, sem envio duplicado
        t = time.monotonic()
        sc("reset-failed", check=False)
        before_pid = main_pid("rocha-reis-worker.service")
        before_restarts = int(show("rocha-reis-worker.service", "NRestarts")["NRestarts"])
        for i in range(40):
            post(root, f"wamid.f18.b{i}", f"Quanto custa a escova? ({i})", f"55119000082{i:02d}")
        wait(lambda: send_log.exists() and len(send_log.read_text().splitlines()) >= 15, 20, 0.02)
        os.kill(before_pid, signal.SIGKILL)
        killed_at = time.monotonic()
        restarted = wait(lambda: active("rocha-reis-worker.service") and main_pid("rocha-reis-worker.service")
                         not in (0, before_pid), 30)
        restart_s = round(time.monotonic() - killed_at, 2)
        drained = wait(lambda: q(root, "SELECT count(*) FROM channel_inbox WHERE status IN ('PENDING','PROCESSING')")
                       [0][0] == 0 and q(root, "SELECT count(*) FROM channel_outbox WHERE status IN "
                                               "('PENDING','RETRY','SENDING')")[0][0] == 0, 90)
        sends = [json.loads(x)["operation"] for x in send_log.read_text().splitlines()]
        statuses = dict(q(root, "SELECT status, count(*) FROM channel_outbox GROUP BY status"))
        unknown_ops = [row[0] for row in q(root, "SELECT id FROM channel_outbox WHERE status='UNKNOWN'")]
        props = show("rocha-reis-worker.service", "NRestarts", "Result")
        r.step("kill9_reinicio_sem_duplicidade", restarted and drained and len(sends) == len(set(sends))
               and set(statuses) <= {"ACCEPTED", "UNKNOWN"}, t, reinicio_s=restart_s,
               NRestarts=int(props["NRestarts"]) - before_restarts, envios=len(sends),
               duplicados=len(sends) - len(set(sends)), situacoes=statuses, incertos=len(unknown_ops))

        # 8. UNKNOWN nunca é reenviado: mais uma volta do worker e o registro não muda para esses itens
        t = time.monotonic()
        count_before = len(sends)
        post(root, "wamid.f18.c0", "Oi", "5511900008300")
        wait(lambda: q(root, "SELECT count(*) FROM channel_inbox WHERE status IN ('PENDING','PROCESSING')")[0][0] == 0,
             30)
        time.sleep(3)
        sends_after = [json.loads(x)["operation"] for x in send_log.read_text().splitlines()]
        resent_unknown = [op for op in sends_after[count_before:] if op in unknown_ops]
        still_unknown = q(root, "SELECT count(*) FROM channel_outbox WHERE status='UNKNOWN'")[0][0]
        r.step("incerto_nao_reenviado", not resent_unknown and still_unknown == len(unknown_ops), t,
               incertos=len(unknown_ops), reenviados=len(resent_unknown),
               observacao=None if unknown_ops else "nenhum envio ficou incerto nesta queda (o teste só observa)")

        # 9. watchdog: worker congelado (SIGSTOP) é abortado e reiniciado pelo WatchdogSec=60
        t = time.monotonic()
        sc("reset-failed", check=False)
        frozen = main_pid("rocha-reis-worker.service")
        restarts0 = int(show("rocha-reis-worker.service", "NRestarts")["NRestarts"])
        os.kill(frozen, signal.SIGSTOP)
        fired = wait(lambda: int(show("rocha-reis-worker.service", "NRestarts")["NRestarts"]) > restarts0
                     and active("rocha-reis-worker.service"), 150, 1.0)
        log = journal("rocha-reis-worker.service", since=since)
        r.step("watchdog", fired and "Watchdog timeout" in log, t, pid_congelado=frozen,
               pid_novo=main_pid("rocha-reis-worker.service"),
               journal=[x for x in log.splitlines() if "atchdog" in x][-3:])

        # 10. limite de reinícios -> unidade falha -> OnFailure aciona rocha-reis-alerta@
        t = time.monotonic()
        sc("reset-failed", check=False)
        kills = 0
        for _ in range(10):  # StartLimitBurst=5 em 300 s: a 6ª partida é recusada
            if not wait(lambda: show("rocha-reis-worker.service", "SubState")["SubState"] == "running", 20):
                break
            restarts = int(show("rocha-reis-worker.service", "NRestarts")["NRestarts"])
            sc("kill", "--kill-whom=main", "--signal=SIGKILL", "rocha-reis-worker.service", check=False)
            kills += 1
            wait(lambda: int(show("rocha-reis-worker.service", "NRestarts")["NRestarts"]) > restarts
                 or show("rocha-reis-worker.service", "ActiveState")["ActiveState"] == "failed", 20)
            if show("rocha-reis-worker.service", "ActiveState")["ActiveState"] == "failed":
                break
        state = {**show("rocha-reis-worker.service", "ActiveState", "Result", "NRestarts"), "kills": kills}
        wait(lambda: "rocha-reis-worker.service" in journal(since=since, identifier="rocha-reis-alerta"), 20)
        alert_lines = [x for x in journal(since=since, identifier="rocha-reis-alerta").splitlines() if x]
        limited = "Start request repeated too quickly" in journal("rocha-reis-worker.service", since=since)
        r.step("limite_de_reinicios_e_onfailure", state["ActiveState"] == "failed" and limited
               and any("rocha-reis-worker" in x for x in alert_lines), t, estado=state,
               journal_limite=limited, registros_de_alerta=len(alert_lines), alerta_no_journal=alert_lines[-1:])
        sc("reset-failed", "rocha-reis-worker.service", check=False)
        sc("start", "rocha-reis-worker.service")
        wait(lambda: active("rocha-reis-worker.service"), 30)

        # 11. ExecStartPre recusa instalação insegura (perfil com permissão frouxa)
        t = time.monotonic()
        perfil = deploy.layout(root)["config"] / "perfil.json"
        sc("stop", "rocha-reis-panel.service")
        os.chmod(perfil, 0o644)
        sc("start", "rocha-reis-panel.service", check=False)
        refused = show("rocha-reis-panel.service", "ActiveState", "SubState", "Result", "MainPID")
        time.sleep(6)  # Restart=on-failure também repete o ExecStartPre: nenhuma tentativa pode servir o painel
        port = deploy.http_status("http://127.0.0.1:8000/health", timeout=1.0)
        sc("stop", "rocha-reis-panel.service", check=False)
        os.chmod(perfil, 0o600)
        sc("reset-failed", "rocha-reis-panel.service", check=False)
        sc("start", "rocha-reis-panel.service")
        recovered = wait(lambda: active("rocha-reis-panel.service") and ready_both(), 30)
        r.step("execstartpre_recusa_inseguro", refused["Result"] == "exit-code" and refused["MainPID"] == "0"
               and port is None and recovered, t, com_perfil_0644=refused, painel_respondeu=port,
               observacao="a recusa entra no ciclo de reinício (e alerta) até o limite; o painel nunca sobe")

        # 12. alertas como serviço real (montagem SOMENTE LEITURA do banco) e backup agendado
        t = time.monotonic()
        alerts_before = sc("start", "rocha-reis-alertas.service", check=False)
        a1 = show("rocha-reis-alertas.service", "Result", "ExecMainStatus")
        a1_log = journal("rocha-reis-alertas.service", since=since)
        sc("reset-failed", "rocha-reis-alertas.service", check=False)
        sc("start", "rocha-reis-backup.service", check=False)
        b = show("rocha-reis-backup.service", "Result", "ExecMainStatus")
        backups = sorted(p.name for p in deploy.layout(root)["backups"].glob("*") if p.is_file())
        sc("start", "rocha-reis-alertas.service", check=False)
        a2 = show("rocha-reis-alertas.service", "Result", "ExecMainStatus")
        a2_log = journal("rocha-reis-alertas.service", since=since)
        sc("start", "rocha-reis-backup.timer", "rocha-reis-alertas.timer")
        timers = run(["systemctl", "--user", "list-timers", "--all", "--no-pager", "rocha-reis-*"]).stdout
        sc("stop", "rocha-reis-backup.timer", "rocha-reis-alertas.timer", check=False)
        codes_seen = {a1["ExecMainStatus"], a2["ExecMainStatus"]}
        r.step("backup_e_alertas_como_servico", b["Result"] == "success" and bool(backups)
               and codes_seen <= {"0", "1", "2"} and "Traceback" not in a1_log + a2_log
               and "banco_inacessivel" not in a1_log + a2_log, t,
               backup=b, arquivos_backup=backups, alertas_antes_do_backup=a1, alertas_depois=a2,
               saida_alertas=a2_log.strip().splitlines()[-12:], timers=timers.strip().splitlines()[:4],
               execucao_inicial=alerts_before.returncode)

        # 12b. alertas PROVOCADOS com os serviços reais (lidos da saída da própria unidade de alertas)
        t = time.monotonic()
        drill: dict[str, object] = {"antes_do_primeiro_backup": sorted(re.findall(r'"codigo": "([a-z_]+)"', a1_log))}
        sc("stop", "rocha-reis-worker.service")  # worker parado + fila crescendo
        stop_at = time.monotonic()
        for i in range(55):
            post(root, f"wamid.f18.d{i}", f"Quanto custa a escova? ({i})", f"55119000084{i:02d}")
        for i in range(16):  # tentativas de vários IPs (o limitador por IP não as segura; o alerta precisa)
            panel_login(f"inexistente.{i}", f"198.51.100.{i + 1}")
        time.sleep(max(0.0, 5 * 60 + 15 - (time.monotonic() - stop_at)))
        drill["worker_parado_5min"] = alert_codes()
        sc("start", "rocha-reis-worker.service")
        wait(lambda: q(root, "SELECT count(*) FROM channel_inbox WHERE status IN ('PENDING','PROCESSING')")[0][0] == 0,
             90)
        time.sleep(2)
        drill["worker_religado"] = alert_codes()
        backups_dir = deploy.layout(root)["backups"]
        for item in backups_dir.glob("*.rrbackup"):  # backup corrompido → teste de restauração falha
            data = bytearray(item.read_bytes())
            data[len(data) // 2] ^= 0xFF
            item.write_bytes(bytes(data))
        drill["teste_restauracao_corrompido"] = cli(python, root, "teste-restauracao", "--perfil",
                                                    str(deploy.layout(root)["config"])).get("codigo", "ok")
        drill["restauracao_falhou"] = alert_codes()
        for item in backups_dir.glob("*.rrbackup"):
            item.unlink()
        sc("start", "rocha-reis-backup.service", check=False)
        cli(python, root, "teste-restauracao", "--perfil", str(deploy.layout(root)["config"]))
        drill["backup_refeito"] = alert_codes()
        sc("stop", *SERVICES)
        db_file = database(root)
        os.chmod(db_file, 0o000)
        drill["banco_ilegivel"] = alert_codes()
        os.chmod(db_file, 0o600)
        sc("start", *SERVICES)
        wait(lambda: all(active(u) for u in SERVICES) and ready_both(), 60)

        def has(key: str, *codes: str) -> bool:
            return set(codes) <= set(drill[key][0])  # type: ignore[index]

        def lacks(key: str, *codes: str) -> bool:
            return not set(codes) & set(drill[key][0])  # type: ignore[index]

        r.step("alertas_provocados_nos_servicos", "backup_atrasado" in drill["antes_do_primeiro_backup"]  # type: ignore[operator]
               and has("worker_parado_5min", "entrada_parada", "entrada_acumulada", "worker_sem_batimento",
                       "falhas_login", "envio_incerto")
               and lacks("worker_religado", "entrada_parada", "entrada_acumulada", "worker_sem_batimento")
               and has("restauracao_falhou", "restauracao_falhou")
               and lacks("backup_refeito", "restauracao_falhou", "backup_atrasado")
               and drill["banco_ilegivel"][0] == ["banco_inacessivel"], t, **drill)  # type: ignore[index]

        # 13. logs: journal e log privado sem segredos, telefones ou texto de cliente
        t = time.monotonic()
        everything = "".join(journal(u, since=since) for u in [*SERVICES, "rocha-reis-backup.service",
                                                                 "rocha-reis-alertas.service"])
        private_logs = "".join(p.read_text(errors="replace") for p in deploy.layout(root)["logs"].glob("*")
                               if p.is_file())
        password = (deploy.layout(root)["config"] / "credenciais-iniciais.txt").read_text().splitlines()[1].split("\t")[2]
        needles = {"segredos": secrets_values + [password], "telefones": ["5511900008"],
                   "texto_cliente": ["Quanto custa", "falar com uma pessoa"]}
        hits = {k: sum((everything + private_logs).count(n) for n in v) for k, v in needles.items()}
        modes = {p.name: oct(p.stat().st_mode & 0o777) for p in deploy.layout(root)["logs"].glob("*") if p.is_file()}
        r.step("logs_sem_dados_sensiveis", not any(hits.values()) and all(m == "0o600" for m in modes.values()), t,
               ocorrencias=hits, linhas_journal=len(everything.splitlines()), logs_privados=modes)

        # 14. exposição medida pelo próprio systemd (contexto do manager do usuário)
        t = time.monotonic()
        exposure = {}
        for unit in SERVICES:
            out = run(["systemd-analyze", "--user", "security", "--no-pager", unit], check=False).stdout
            match = re.search(r"Overall exposure level for \S+: ([\d.]+)", out)
            exposure[unit] = float(match.group(1)) if match else None
        r.step("exposicao_systemd_analyze", all(v is not None and v <= 2.0 for v in exposure.values()), t,
               **exposure)

        # 15. atualização real: `ops atualizar` controlando o systemd (sucesso, falha + reversão, reversão manual)
        t = time.monotonic()
        from tests.server_support import package_from, source_copy
        base = dest / "versoes"
        good = package_from(source_copy(base / "fonte-18", edits={"rocha_reis_agent/__init__.py": (
            '__version__ = "', '__version__ = "18.')}), base / "pacote-18")
        good_version = json.loads(subprocess.run(["tar", "-xzOf", str(good), "RELEASE.json"], capture_output=True,
                                                 check=True).stdout)["versao"]
        before = deploy.current_release(root)
        upd = cli(python, root, "atualizar", "--pacote", str(good), "--raiz", str(root),
                  "--confirmacao", f"ATUALIZAR:{good_version}",
                  "--parar", "systemctl --user stop rocha-reis-worker rocha-reis-webhook rocha-reis-panel",
                  "--iniciar", "systemctl --user start rocha-reis-webhook rocha-reis-panel rocha-reis-worker")
        running_version = Path(f"/proc/{main_pid('rocha-reis-webhook.service')}/cwd").resolve().name
        r.step("atualizacao_sob_systemd", upd.get("resultado") == "ATUALIZADA" and ready_both()
               and deploy.current_release(root) != before, t, resultado=upd.get("resultado"),
               etapas=upd.get("etapas"), em_execucao=running_version, erro=upd.get("erro"))

        t = time.monotonic()
        working = deploy.current_release(root)
        broken = package_from(source_copy(base / "fonte-17", edits={
            "rocha_reis_agent/__init__.py": ('__version__ = "', '__version__ = "17.'),
            "rocha_reis_agent/api/health.py": ("router = APIRouter()",
                                               'raise RuntimeError("versão quebrada de teste")\nrouter = APIRouter()')}),
            base / "pacote-17")
        broken_version = json.loads(subprocess.run(["tar", "-xzOf", str(broken), "RELEASE.json"], capture_output=True,
                                                   check=True).stdout)["versao"]
        sc("reset-failed", check=False)
        upd2 = cli(python, root, "atualizar", "--pacote", str(broken), "--raiz", str(root),
                   "--confirmacao", f"ATUALIZAR:{broken_version}",
                   "--parar", "systemctl --user stop rocha-reis-worker rocha-reis-webhook rocha-reis-panel",
                   "--iniciar", "systemctl --user start rocha-reis-webhook rocha-reis-panel rocha-reis-worker")
        sc("reset-failed", check=False)
        sc("start", *SERVICES, check=False)
        healthy = wait(lambda: all(active(u) for u in SERVICES) and ready_both(), 60)
        r.step("falha_na_atualizacao_reverte", upd2.get("resultado") == "REVERTIDA" and healthy
               and deploy.current_release(root) == working, t, resultado=upd2.get("resultado"),
               etapas=upd2.get("etapas"))

        t = time.monotonic()
        sc("stop", *SERVICES)
        back_to = cli(python, root, "reverter", "--raiz", str(root))
        sc("start", *SERVICES)
        healthy = wait(lambda: all(active(u) for u in SERVICES) and ready_both(), 60)
        r.step("reversao_manual_de_codigo", "ativa" in back_to and healthy and deploy.current_release(root) == before,
               t, **back_to)

        # 16. integridade final
        t = time.monotonic()
        integrity = q(root, "PRAGMA integrity_check")[0][0]
        sends = [json.loads(x)["operation"] for x in send_log.read_text().splitlines()]
        final = deploy.validate_installation(root)
        r.step("integridade_final", integrity == "ok" and len(sends) == len(set(sends)) and final["ok"], t,
               integridade=integrity, envios=len(sends), duplicados=len(sends) - len(set(sends)),
               conversas=q(root, "SELECT count(*) FROM conversations")[0][0], travas=final["travas"])
    except Exception as error:  # noqa: BLE001 — relatório precisa registrar a etapa interrompida
        r.steps.append({"etapa": "interrompido", "ok": False, "erro": f"{type(error).__name__}: {error}"[:800]})
    finally:
        remove_units()
        if not keep:
            for directory, _, _ in os.walk(dest):
                os.chmod(directory, 0o700)
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(dest / "versoes", ignore_errors=True)
    report = {"tipo": "TESTE COM SERVIÇO REAL — systemd do usuário (não é o servidor definitivo)", **info,
              "resultado": "OK" if all(s["ok"] for s in r.steps) else "FALHOU", "etapas": r.steps}
    out = dest / "relatorio-systemd.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.chmod(out, 0o600)
    return report


def cli(python: str, root: Path, *args: str) -> dict:  # type: ignore[type-arg]
    """Executa `ops` com o Python da VERSÃO ATIVA, como o administrador faria no servidor."""
    current = deploy.layout(root)["current"].resolve()
    done = subprocess.run([str(current / ".venv" / "bin" / "python"), "-B", "-m", "rocha_reis_agent.ops", *args],
                          capture_output=True, text=True, cwd=current, timeout=900)
    try:
        return json.loads(done.stdout)
    except ValueError:
        return {"erro": (done.stderr or done.stdout)[-800:], "codigo": done.returncode}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--destino", required=True)
    parser.add_argument("--python", required=True, help="Python 3.13 usado para criar o .venv da versão")
    parser.add_argument("--manter", action="store_true", help="não apagar a raiz de teste no fim")
    args = parser.parse_args()
    destination = Path(args.destino).expanduser().absolute()
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit("O destino precisa ser um diretório novo ou vazio.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = execute(destination, args.python, args.manter)
    print(json.dumps({"resultado": result["resultado"], "relatorio": str(destination / "relatorio-systemd.json")},
                     ensure_ascii=False))
    raise SystemExit(0 if result["resultado"] == "OK" else 1)
