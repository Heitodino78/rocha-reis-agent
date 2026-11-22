"""Fase 18B-0 — laboratório REAL do volume de dados: disco LUKS2 de verdade + unidades systemd reais.

Sem root e sem sudo: os discos de teste são ARQUIVOS transformados em dispositivos de loop pelo udisks
(política padrão da sessão ativa: loop-setup, formatação, desbloqueio e montagem sem senha de
administrador; `--no-user-interaction` impede qualquer pedido de senha). O disco A recebe a instalação;
o disco B é um SEGUNDO volume LUKS com a mesma etiqueta (é montado no MESMO caminho) e com a marca do A
copiada — o "volume errado no caminho certo". Tudo é desmontado, bloqueado e desligado no fim.

Cenários: volume correto → atendimento; perda do volume com o serviço rodando; "reinício" com o disco
bloqueado; disco inexistente; volume errado no lugar; desbloqueio manual; retomada sem perda nem
duplicidade. Diretório presente sem montagem e montagem sem LUKS: num namespace de montagem isolado
(o ponto de montagem do udisks fica em /media/$USER, que o usuário comum não pode criar).

Diferenças em relação ao servidor (registradas no relatório): as mesmas da 18A (sem User=, systemd do
usuário, caminhos reescritos) e mais — o código e o perfil ficam DENTRO do disco de teste (o udisks só
monta em /media/$USER/<etiqueta>), por isso o portão recebe `--identidade` com a identidade guardada FORA
do disco, e os alertas leem uma cópia do perfil fora do disco; o desbloqueio é feito pelo udisks com
arquivo de frase-senha do laboratório (no servidor: `rocha-reis-desbloquear`, frase digitada).

Uso (no host):  python tests_infra/fase18b0_volume.py --destino DIR_NOVO --python /caminho/python3.13
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests_infra"))

from rocha_reis_agent.ops import deploy  # noqa: E402
from rocha_reis_agent.persistence import storage_security as ss  # noqa: E402
import fase18_systemd as f18  # noqa: E402

LABEL = "RRLAB"
MOUNT = Path("/media") / os.environ.get("USER", "user") / LABEL
MOUNT_UNIT = f"media-{os.environ.get('USER', 'user')}-{LABEL}.mount"
UNITS = ("rocha-reis-volume.service", "rocha-reis-webhook.service", "rocha-reis-panel.service",
         "rocha-reis-worker.service", "rocha-reis-backup.service", "rocha-reis-backup.timer",
         "rocha-reis-alertas.service", "rocha-reis-alerta@.service", "rocha-reis.target")
SERVICES = f18.SERVICES
ORIGIN = f18.ORIGIN


def run(cmd: list[str], *, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True, timeout=timeout)


# ------------------------------------------------------------------ discos de teste (udisks, sem root)
class Disk:
    def __init__(self, image: Path, size_mb: int) -> None:
        self.image, self.size_mb = image, size_mb
        self.loop: str | None = None

    def create(self) -> None:
        with open(self.image, "wb") as handle:
            handle.truncate(self.size_mb * 1024 * 1024)
        os.chmod(self.image, 0o600)
        self.attach()

    def attach(self) -> None:
        out = run(["udisksctl", "loop-setup", "-f", str(self.image), "--no-user-interaction"]).stdout
        self.loop = re.search(r"as (/dev/loop\d+)", out).group(1)  # type: ignore[union-attr]

    def object_path(self, device: str) -> str:
        return "/org/freedesktop/UDisks2/block_devices/" + Path(device).name

    def format_luks(self, passphrase: str) -> None:
        # Frase-senha do LABORATÓRIO (aleatória, descartável) passada ao udisks pelo D-Bus.
        options = (f"{{'encrypt.passphrase': <'{passphrase}'>, 'encrypt.type': <'luks2'>, 'label': <'{LABEL}'>, "
                   "'take-ownership': <true>, 'no-user-interaction': <true>}")
        run(["gdbus", "call", "--system", "--dest", "org.freedesktop.UDisks2", "--object-path",
             self.object_path(str(self.loop)), "--method", "org.freedesktop.UDisks2.Block.Format", "ext4", options],
            timeout=300)

    def cleartext(self) -> str | None:
        holders = Path("/sys/block") / Path(str(self.loop)).name / "holders"
        names = sorted(p.name for p in holders.iterdir()) if holders.is_dir() else []
        return f"/dev/{names[0]}" if names else None

    def unlock(self, key_file: Path) -> None:
        if self.cleartext() is None:
            run(["udisksctl", "unlock", "-b", str(self.loop), "--key-file", str(key_file), "--no-user-interaction"])

    def mount(self) -> str:
        out = run(["udisksctl", "mount", "-b", str(self.cleartext()), "--no-user-interaction"]).stdout
        return out.strip()

    def unmount(self, *, force: bool = False) -> None:
        clear = self.cleartext()
        if clear and ss.mount_entry(MOUNT) is not None:
            run(["udisksctl", "unmount", "-b", clear, "--no-user-interaction", *(["--force"] if force else [])])

    def lock(self) -> None:
        if self.cleartext():
            run(["udisksctl", "lock", "-b", str(self.loop), "--no-user-interaction"])

    def detach(self) -> None:
        if self.loop:
            run(["udisksctl", "loop-delete", "-b", self.loop, "--no-user-interaction"], check=False)
            self.loop = None

    def luks_uuid(self) -> str:
        out = run(["udisksctl", "info", "-b", str(self.loop)]).stdout
        return ss.normalize_luks_uuid(re.search(r"IdUUID:\s+(\S+)", out).group(1))  # type: ignore[union-attr]

    def teardown(self) -> None:
        try:
            self.unmount(force=True)
        except subprocess.SubprocessError:
            pass
        try:
            self.lock()
        except subprocess.SubprocessError:
            pass
        self.detach()


# ------------------------------------------------------------------ unidades do laboratório
def install_units(root: Path, python_home: Path, identity: Path, outside_profile: Path, send_log: Path) -> list[str]:
    target = f18.unit_dir()
    target.mkdir(parents=True, exist_ok=True)
    for search in (Path.home() / ".config/systemd/user", target):
        clash = [p.name for p in search.glob("rocha-reis*")] if search.exists() else []
        if clash:
            raise SystemExit(f"Já existem unidades rocha-reis em {search}: {clash} — nada foi alterado.")
    for name in UNITS:
        text = (REPO / "deploy" / "systemd" / name).read_text(encoding="utf-8")
        (target / name).write_text(f18.render_unit(text, root, python_home, mount_unit=MOUNT_UNIT,
                                                   identity=identity), encoding="utf-8")
    # Portão e alertas precisam rodar com o disco AUSENTE ou ERRADO. No servidor o código deles fica no disco de
    # sistema (/opt); aqui o /opt de teste está DENTRO do disco, então eles rodam do repositório com o Python da
    # ferramenta (fora do disco). Mesmo texto de comando, mesmo endurecimento; só o executável muda de lugar.
    outside_python, venv = sys.executable, Path(sys.prefix)
    data = f"{root}/var/lib/rocha-reis-agent"
    dropins = {
        "rocha-reis-volume.service": (
            "[Service]\nWorkingDirectory=" + str(REPO) + "\nExecStart=\nExecStart=" + outside_python +
            f" -B -m rocha_reis_agent.ops verificar-volume --raiz {root} --identidade {identity}\n"
            f"ReadOnlyPaths=\nReadOnlyPaths=-{root}/etc/rocha-reis-agent -{data}\n"
            f"BindReadOnlyPaths={identity}\nBindReadOnlyPaths={venv}\n"),
        "rocha-reis-alertas.service": (
            "[Service]\nWorkingDirectory=" + str(REPO) + "\nExecStart=\nExecStart=" + outside_python +
            f" -B -m rocha_reis_agent.ops alertas --perfil {outside_profile} --batimento {data}/estado/worker.json\n"
            f"ReadOnlyPaths=\nReadOnlyPaths={outside_profile} -{data}\n"
            f"BindReadOnlyPaths={outside_profile}\nBindReadOnlyPaths={venv}\n"),
        "rocha-reis-worker.service": f"[Service]\nEnvironment=WORKER_FAKE_SEND_LOG={send_log}\n",
    }
    for unit, body in dropins.items():
        folder = target / f"{unit}.d"
        folder.mkdir(exist_ok=True)
        (folder / "fase18b0.conf").write_text("# Fase 18B-0 (laboratório)\n" + body, encoding="utf-8")
    f18.sc("daemon-reload")
    return list(UNITS)


def lab_mount_unit(cleartext_name: str) -> str:
    """A unidade de montagem de REFERÊNCIA com o caminho do laboratório. Quem monta é o udisks (o systemd do
    usuário não monta); a condição vira "o ponto está montado" — no servidor é "o dispositivo LUKS aberto
    existe". O efeito testado é o mesmo: bloqueado → unidade PULADA → BindsTo+After impede os serviços."""
    text = (REPO / "deploy" / "systemd" / "rocha-reis-dados.mount").read_text(encoding="utf-8")
    text = text.replace("ConditionPathExists=/dev/mapper/rocha-reis-dados", f"ConditionPathIsMountPoint={MOUNT}")
    text = text.replace("What=/dev/mapper/rocha-reis-dados", f"What=/dev/mapper/{cleartext_name}")
    return text.replace("Where=/var/lib/rocha-reis-agent", f"Where={MOUNT}")


def verify_units(dest: Path, root: Path, python_home: Path, identity: Path, mount_text: str) -> subprocess.CompletedProcess[str]:
    folder = dest / "verificar-unidades"
    folder.mkdir(exist_ok=True)
    names = []
    for name in UNITS:
        if "@." in name:
            continue
        text = (REPO / "deploy" / "systemd" / name).read_text(encoding="utf-8")
        (folder / name).write_text(f18.render_unit(text, root, python_home, mount_unit=MOUNT_UNIT, identity=identity))
        names.append(str(folder / name))
    (folder / MOUNT_UNIT).write_text(mount_text)
    shutil.copy2(REPO / "deploy" / "systemd" / "rocha-reis-alerta@.service", folder / "rocha-reis-alerta@.service")
    return run(["systemd-analyze", "--user", "verify", *names, str(folder / MOUNT_UNIT)], check=False)


def remove_units() -> None:
    target = f18.unit_dir()
    (target / MOUNT_UNIT).unlink(missing_ok=True)
    f18.sc("stop", "rocha-reis.target", *UNITS[:-2], check=False)
    f18.sc("stop", "rocha-reis-alerta@*", check=False)
    for path in target.glob("rocha-reis*"):
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    f18.sc("daemon-reload", check=False)
    f18.sc("reset-failed", check=False)


def props(unit: str) -> dict[str, str]:
    return f18.show(unit, "ActiveState", "SubState", "Result", "NRestarts")


def isolated_check(python: str, root: Path, identity: Path, setup: str) -> dict:  # type: ignore[type-arg]
    """verificar-volume num namespace de USUÁRIO+MONTAGEM isolado (nada muda fora dele)."""
    code = identity.parent / "repositorio-ns"  # o tmpfs sobre /media/$USER esconderia o repositório
    code.mkdir(exist_ok=True)
    script = (f"set -e; mount --bind {REPO} {code}; mount -t tmpfs -o size=1m,mode=0755 tmpfs {MOUNT.parent}; "
              f"mkdir -p {MOUNT}; {setup}; cd {code}; "
              f"exec {python} -B -m rocha_reis_agent.ops verificar-volume --raiz {root} --identidade {identity}")
    done = subprocess.run(["unshare", "--user", "--map-root-user", "--mount", "sh", "-c", script], check=False,
                          text=True, capture_output=True, timeout=60, cwd=REPO)
    try:
        return {"codigo": done.returncode, **json.loads(done.stdout)}
    except ValueError:
        return {"codigo": done.returncode, "erro": (done.stderr or done.stdout)[-400:]}


def nothing_under_mount_point() -> bool:
    return not MOUNT.exists() or (ss.mount_entry(MOUNT) is None and not any(MOUNT.iterdir()))


# ------------------------------------------------------------------ roteiro
def execute(dest: Path, python: str, keep: bool) -> dict[str, object]:
    r = f18.Report()
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    root = MOUNT / "raiz"
    python_home = Path(python).resolve().parents[1]
    key_file = dest / "frase-senha-do-laboratorio"
    key_file.write_text(secrets.token_urlsafe(24))
    os.chmod(key_file, 0o600)
    identity = dest / "identidade-esperada.json"
    outside_profile = dest / "perfil-fora-do-disco"
    send_log = deploy.layout(root)["estado"] / "envios-falsos.jsonl"
    disk_a, disk_b = Disk(dest / "disco-a.img", 1536), Disk(dest / "disco-b.img", 64)
    info: dict[str, object] = {
        "destino": "~/" + str(dest.relative_to(Path.home())) if dest.is_relative_to(Path.home()) else str(dest),
        "systemd": run(["systemctl", "--version"]).stdout.splitlines()[0],
        "udisks": run(["udisksctl", "status"], check=False).returncode == 0,
        "desvios_do_servidor": ["sem User=/Group= (exige root)", "systemd do usuário",
                                "código e perfil dentro do disco de teste (udisks monta em /media/$USER/<etiqueta>)",
                                "portão com --identidade fora do disco; alertas com cópia do perfil fora do disco",
                                "portão e alertas executados do repositório (Python da ferramenta), fora do disco",
                                "desbloqueio pelo udisks com arquivo de frase-senha do laboratório",
                                "unidade de montagem de referência com condição 'ponto montado' (udisks monta; "
                                "no servidor: 'dispositivo LUKS aberto existe')",
                                "diretório sem montagem e montagem sem LUKS: namespace de montagem isolado"]}
    if ss.mount_entry(MOUNT) is not None or MOUNT.exists():
        raise SystemExit(f"{MOUNT} já existe; nada foi alterado.")
    try:
        # 1. disco A: arquivo → loop → LUKS2 real → ext4 → montado pelo udisks
        t = time.monotonic()
        disk_a.create()
        disk_a.format_luks(key_file.read_text())
        disk_a.unlock(key_file)
        mounted = disk_a.mount()
        entry = ss.mount_entry(MOUNT)
        kind, uuid = ss.luks_uuid_of(int(entry["major"]), int(entry["minor"])) if entry else (None, None)  # type: ignore[call-overload]
        r.step("disco_luks2_real", entry is not None and kind == "LUKS2" and uuid == disk_a.luks_uuid(), t,
               montagem=mounted.replace(str(Path.home()), "~"), tipo=kind, cadeia_dm=entry and entry["origem"],
               uuid_confere_com_udisks=uuid == disk_a.luks_uuid())

        # 2. marca + instalação pelo fluxo oficial + instância exigindo o volume dedicado
        t = time.monotonic()
        marker = ss.mark_volume(MOUNT)
        package = deploy.build_package(dest / "pacotes")
        installed = deploy.install_release(package, root, python=python, create_venv=True)
        deploy.activate(root, str(installed["id"]))
        deploy.prepare_instance(root, panel_origin=ORIGIN, volume_mount=MOUNT)
        profile = f18.profile(root)
        identity.write_text(json.dumps(profile["volume"]))
        outside_profile.mkdir(mode=0o700)
        shutil.copy2(deploy.layout(root)["config"] / "perfil.json", outside_profile / "perfil.json")
        validation = deploy.validate_installation(root)
        info.update(versao=installed["versao"], release=installed["id"])
        r.step("instalacao_com_volume_dedicado", validation["ok"] and validation["volume"]["estado"] == "PRONTO"
               and profile["volume"]["luks_uuid"] == uuid == marker["luks_uuid"], t,
               volume=validation["volume"] | {"ponto_montagem": str(MOUNT)}, travas=validation["travas"],
               problemas=validation["problemas"])

        # 3. unidades reais (portão, serviços, alvo) carregadas
        t = time.monotonic()
        mount_text = lab_mount_unit(Path(str(entry["origem"])).name)  # type: ignore[index]
        verify = verify_units(dest, root, python_home, identity, mount_text)
        install_units(root, python_home, identity, outside_profile, send_log)
        (f18.unit_dir() / MOUNT_UNIT).write_text(mount_text, encoding="utf-8")
        f18.sc("daemon-reload")
        mount_props = f18.show(MOUNT_UNIT, "LoadState", "ActiveState", "FragmentPath")
        r.step("unidades_carregadas", verify.returncode == 0 and mount_props["ActiveState"] == "active", t,
               verify=(verify.stderr or verify.stdout).strip()[-600:], montagem=mount_props,
               dependencias_webhook=f18.show("rocha-reis-webhook.service", "Requires", "BindsTo", "After"))

        # 4. volume correto desbloqueado → alvo → atendimento
        t = time.monotonic()
        f18.sc("start", "rocha-reis.target")
        up = f18.wait(lambda: all(f18.active(u) for u in SERVICES) and f18.ready_both(), 90)
        codes = [f18.post(root, f"wamid.f18b0.a{i}", f"Quanto custa o corte? ({i})", f"55119000091{i:02d}")
                 for i in range(6)]
        done = f18.wait(lambda: f18.q(root, "SELECT count(*) FROM channel_inbox WHERE status='DONE'")[0][0] == 6, 60)
        gate = props("rocha-reis-volume.service")
        state = f18.cli(python, root, "estado-servidor", "--raiz", str(root))
        r.step("volume_correto_atendimento", up and done and set(codes) == {200} and gate["SubState"] == "exited"
               and state.get("atendimento_operacional") is True, t, portao=gate,
               camadas={k: v.get("ok") for k, v in state.get("camadas", {}).items()},
               operacao_24_7=state.get("operacao_24_7"))

        # 5. perda do volume com tudo rodando (desmontagem forçada): os serviços PARAM, sem ciclo
        t = time.monotonic()
        burst = [f18.post(root, f"wamid.f18b0.b{i}", f"Oi, tudo bem? ({i})", f"55119000092{i:02d}") for i in range(4)]
        disk_a.unmount(force=True)
        stopped = f18.wait(lambda: not any(f18.active(u) for u in (*SERVICES, "rocha-reis-volume.service")), 40)
        time.sleep(8)  # janela maior que RestartSec=5: nenhum reinício pode acontecer
        after = {u: props(u) for u in (*SERVICES, "rocha-reis-volume.service")}
        codes_alert, status_alert = alert_run()
        r.step("perda_do_volume_para_servicos", stopped and all(p["NRestarts"] == "0" and p["ActiveState"] != "active"
                                                                for p in after.values())
               and nothing_under_mount_point() and "volume_aguardando_desbloqueio" in codes_alert, t,
               http_rajada=sorted(set(map(str, burst))), servicos=after, alerta=codes_alert, saida_alertas=status_alert)

        # 6. "reinício" com o disco BLOQUEADO: tentar subir o alvo falha por dependência, sem ciclo nem banco novo
        t = time.monotonic()
        disk_a.lock()
        attempt = f18.sc("start", "rocha-reis.target", check=False)
        time.sleep(8)
        blocked = {u: props(u) for u in (*SERVICES, "rocha-reis-volume.service")}
        failed = run(["systemctl", "--user", "--failed", "--no-legend", "--plain"], check=False).stdout
        r.step("reinicio_com_disco_bloqueado", time.monotonic() - t < 40 and all(
            p["ActiveState"] == "inactive" and p["NRestarts"] == "0" for p in blocked.values())
               and "rocha-reis-webhook" not in failed and nothing_under_mount_point(), t,
               systemctl={"codigo": attempt.returncode, "saida": attempt.stderr.strip()[-300:]},
               montagem=f18.show(MOUNT_UNIT, "ActiveState", "ConditionResult"),
               servicos=blocked, unidades_em_falha=failed.strip() or None)

        # 7. disco INEXISTENTE (loop desligado): idem
        t = time.monotonic()
        disk_a.detach()
        attempt = f18.sc("start", "rocha-reis.target", check=False)
        time.sleep(3)
        r.step("disco_inexistente", not any(f18.active(u) for u in SERVICES)
               and all(props(u)["NRestarts"] == "0" for u in SERVICES) and nothing_under_mount_point(), t,
               systemctl={"codigo": attempt.returncode, "saida": attempt.stderr.strip()[-300:]})

        # 8. volume ERRADO no caminho esperado: disco B (outro LUKS, mesma etiqueta) com a marca do A COPIADA
        t = time.monotonic()
        disk_b.create()
        disk_b.format_luks(key_file.read_text())
        disk_b.unlock(key_file)
        disk_b.mount()
        forged = MOUNT / ss.VOLUME_MARKER
        forged.write_text(json.dumps({**marker}))
        os.chmod(forged, 0o644)
        f18.sc("reset-failed", check=False)
        attempt = f18.sc("start", "rocha-reis.target", check=False)
        time.sleep(4)
        gate = props("rocha-reis-volume.service")
        gate_log = run(["journalctl", "--user", "--no-pager", "-o", "cat", "-u", "rocha-reis-volume.service",
                        "--since", since], check=False).stdout
        alert_log = f18.journal(since=since, identifier="rocha-reis-alerta")
        r.step("volume_errado_recusado", gate["Result"] == "exit-code"
               and not any(f18.active(u) for u in SERVICES) and "INCORRETO" in gate_log
               and "rocha-reis-volume.service" in alert_log, t, portao=gate,
               motivo=[line for line in gate_log.splitlines() if "INCORRETO" in line][:2],
               alerta_journal="falha na unidade rocha-reis-volume.service" in alert_log)
        disk_b.teardown()

        # 9. diretório presente sem montagem / montagem sem LUKS / outro LUKS (namespace isolado)
        t = time.monotonic()
        checks = {
            "diretorio_sem_montagem": isolated_check(sys.executable, root, identity,
                                                     f"touch {MOUNT}/arquivo-qualquer"),
            "montagem_sem_luks": isolated_check(sys.executable, root, identity,
                                                f"mount -t tmpfs tmpfs {MOUNT}"),
            "outro_volume_luks": isolated_check(sys.executable, root, identity,
                                                f"mount --bind {dest} {MOUNT}"),
        }
        r.step("cenarios_isolados", checks["diretorio_sem_montagem"].get("estado") == "AGUARDANDO_DESBLOQUEIO"
               and checks["diretorio_sem_montagem"]["codigo"] == 3
               and checks["montagem_sem_luks"].get("estado") == "RECUSADO"
               and checks["outro_volume_luks"].get("estado") == "RECUSADO", t,
               **{k: {"codigo": v["codigo"], "estado": v.get("estado"), "problemas": v.get("problemas", v.get("erro"))}
                  for k, v in checks.items()})

        # 10. desbloqueio manual → conferência → alvo → retomada sem perda nem duplicidade
        t = time.monotonic()
        disk_a.attach()
        disk_a.unlock(key_file)
        disk_a.mount()
        check = f18.cli(python, root, "verificar-volume", "--raiz", str(root), "--identidade", str(identity))
        f18.sc("reset-failed", check=False)
        f18.sc("start", "rocha-reis.target")
        up = f18.wait(lambda: all(f18.active(u) for u in SERVICES) and f18.ready_both(), 90)
        settled = f18.wait(lambda: f18.q(root, "SELECT count(*) FROM channel_inbox WHERE status IN "
                                               "('PENDING','PROCESSING')")[0][0] == 0
                           and f18.q(root, "SELECT count(*) FROM channel_outbox WHERE status IN "
                                           "('PENDING','RETRY','SENDING')")[0][0] == 0, 90)
        received = f18.q(root, "SELECT count(*) FROM channel_inbox WHERE kind='message'")[0][0]
        unknown = f18.q(root, "SELECT count(*) FROM channel_outbox WHERE status='UNKNOWN'")[0][0]
        sends = [json.loads(x)["operation"] for x in send_log.read_text().splitlines()] if send_log.exists() else []
        accepted = f18.q(root, "SELECT count(*) FROM channel_outbox WHERE status='ACCEPTED'")[0][0]
        integrity = f18.q(root, "PRAGMA integrity_check")[0][0]
        r.step("desbloqueio_manual_e_retomada", check.get("estado") == "PRONTO" and up and settled
               and len(sends) == len(set(sends)) and integrity == "ok", t, verificar_volume=check.get("estado"),
               mensagens_recebidas=received, envios=len(sends), duplicados=len(sends) - len(set(sends)),
               aceitos=accepted, incertos_UNKNOWN=unknown, integridade=integrity,
               nota="UNKNOWN nunca é reenviado sozinho; aparece no painel e no alerta envio_incerto")

        # 11. alertas voltam ao normal (volume ok) e o estado em camadas confirma o atendimento
        t = time.monotonic()
        codes_alert, status_alert = alert_run()
        state = f18.cli(python, root, "estado-servidor", "--raiz", str(root))
        r.step("pos_recuperacao", "volume_aguardando_desbloqueio" not in codes_alert
               and state.get("atendimento_operacional") is True, t, alertas=codes_alert, saida=status_alert,
               operacao_24_7=state.get("operacao_24_7"), observacao=state.get("continuidade", {}).get("observacao"))
    except Exception as error:  # noqa: BLE001 — o relatório registra a etapa interrompida
        r.steps.append({"etapa": "interrompido", "ok": False, "erro": f"{type(error).__name__}: {error}"[:800]})
    finally:
        remove_units()
        for disk in (disk_a, disk_b):
            if disk.loop is None and disk.image.exists():
                continue
            disk.teardown()
        if not keep:
            for image in (disk_a.image, disk_b.image):
                image.unlink(missing_ok=True)
        key_file.unlink(missing_ok=True)
    clean = {"montagem": ss.mount_entry(MOUNT) is None, "loops_do_laboratorio": [
        line for line in run(["losetup", "-a"], check=False).stdout.splitlines() if str(dest) in line]}
    report = {"tipo": "TESTE COM SERVIÇO REAL — disco LUKS2 de loop (udisks) + systemd do usuário; "
                      "não é o servidor definitivo", **info, "limpeza": clean,
              "resultado": "OK" if all(s["ok"] for s in r.steps) else "FALHOU", "etapas": r.steps}
    out = dest / "relatorio-volume.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.chmod(out, 0o600)
    return report


def alert_run() -> tuple[list[str], str]:
    return f18.alert_codes()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--destino", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--manter", action="store_true", help="manter as imagens de disco (sempre desligadas)")
    args = parser.parse_args()
    destination = Path(args.destino).expanduser().absolute()
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit("O destino precisa ser um diretório novo ou vazio.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = execute(destination, args.python, args.manter)
    print(json.dumps({"resultado": result["resultado"], "relatorio": str(destination / "relatorio-volume.json")},
                     ensure_ascii=False))
