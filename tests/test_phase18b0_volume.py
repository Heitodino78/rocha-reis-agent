"""Fase 18B-0 — identidade do volume LUKS de dados, dependências systemd e estado em camadas.

Unitários com /sys e /proc/self/mountinfo FALSOS (cada cenário do volume) e de ponta a ponta sobre uma
raiz instalada no $HOME cifrado, simulando só o "é ponto de montagem" (o UUID LUKS lido é o REAL do
$HOME). O laboratório com disco LUKS2 real e systemd está em tests_infra/fase18b0_volume.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

import pytest

from rocha_reis_agent.ops import deploy
from rocha_reis_agent.persistence import storage_security as ss
from server_support import PYTHON, encrypted_tmp

ROOT = Path(__file__).resolve().parents[1]
UUID_A = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
UUID_B = "aaaabbbbccccddddeeeeffff00001111"


def fake_sys(tmp_path: Path, dm_uuid: str | None, major: int = 253, minor: int = 3) -> Path:
    root = tmp_path / "sys"
    device = root / "devices" / "virtual" / "block" / f"dm-{minor}"
    (device / "slaves").mkdir(parents=True)
    if dm_uuid is not None:
        (device / "dm").mkdir()
        (device / "dm" / "uuid").write_text(dm_uuid)
        (device / "dm" / "name").write_text("rocha-reis-dados")
    (root / "dev" / "block").mkdir(parents=True)
    (root / "dev" / "block" / f"{major}:{minor}").symlink_to(device)
    return root


def mountinfo(tmp_path: Path, mount: Path | None, *, dev: str = "253:3", opts: str = "rw,relatime",
              fstype: str = "ext4") -> Path:
    path = tmp_path / "mountinfo"
    lines = ["22 1 259:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw"]
    if mount is not None:
        escaped = str(mount).replace(" ", "\\040")
        lines.append(f"40 22 {dev} / {escaped} {opts} shared:9 - {fstype} /dev/mapper/rocha-reis-dados rw")
    path.write_text("\n".join(lines) + "\n")
    return path


def luks(uuid: str) -> str:
    return f"CRYPT-LUKS2-{uuid}-rocha-reis-dados"


def marked(mount: Path, uuid: str = UUID_A, ident: str = "1" * 32, mode: int = 0o644) -> dict:
    mount.mkdir(parents=True, exist_ok=True)
    data = {"formato": "rocha-reis-volume", "versao": 1, "id": ident, "luks_uuid": uuid}
    (mount / ss.VOLUME_MARKER).write_text(json.dumps(data))
    os.chmod(mount / ss.VOLUME_MARKER, mode)
    return {"id": ident, "luks_uuid": uuid}


def check(tmp_path, mount, expected, *, dm=luks(UUID_A), mounted=True, **kw):  # type: ignore[no-untyped-def]
    return ss.check_data_volume(mount, expected, sys_root=fake_sys(tmp_path, dm),
                                mountinfo=mountinfo(tmp_path, mount if mounted else None, **kw))


# ------------------------------------------------------------------ cenários do volume (unitários)
def test_correct_luks_volume_with_matching_marker_is_ready(tmp_path):
    mount = tmp_path / "vol"
    expected = marked(mount)
    (mount / "dados").mkdir()
    status = ss.check_data_volume(mount, expected, paths=(mount / "dados",), sys_root=fake_sys(tmp_path, luks(UUID_A)),
                                  mountinfo=mountinfo(tmp_path, mount))
    assert status.ready and status.luks_uuid == UUID_A and status.volume_id == "1" * 32, status


def test_missing_mount_point_means_waiting_for_unlock(tmp_path):
    status = check(tmp_path, tmp_path / "nao-existe", {"id": "1" * 32, "luks_uuid": UUID_A}, mounted=False)
    assert status.state == ss.WAITING and "inexistente" in status.summary()


def test_directory_present_without_mount_is_never_accepted(tmp_path):
    """Volume bloqueado deixa o ponto de montagem como pasta comum do disco de sistema — mesmo cifrado
    (disco de sistema com LUKS), mesmo com uma marca COPIADA dentro: não está montado, não serve."""
    mount = tmp_path / "vol"
    expected = marked(mount)
    status = check(tmp_path, mount, expected, mounted=False)
    assert status.state == ss.WAITING and "nenhum volume montado" in status.summary()


def test_mounted_without_dm_crypt_is_refused(tmp_path):
    mount = tmp_path / "vol"
    expected = marked(mount)
    status = check(tmp_path, mount, expected, dm=None)
    assert status.state == ss.REFUSED and "SEM dm-crypt" in status.summary()


def test_plain_dm_crypt_has_no_identity_and_is_refused(tmp_path):
    mount = tmp_path / "vol"
    expected = marked(mount)
    status = check(tmp_path, mount, expected, dm="CRYPT-PLAIN-rocha-reis-dados")
    assert status.state == ss.REFUSED and "exige LUKS" in status.summary()


def test_wrong_luks_volume_at_expected_path_is_refused_even_with_copied_marker(tmp_path):
    mount = tmp_path / "vol"
    expected = marked(mount, uuid=UUID_A)  # marca copiada do volume certo para o volume B
    status = check(tmp_path, mount, expected, dm=luks(UUID_B))
    assert status.state == ss.REFUSED
    assert "UUID LUKS diferente" in status.summary() and "não pertence a este volume" in status.summary()


def test_fresh_luks_volume_without_marker_is_refused(tmp_path):
    mount = tmp_path / "vol"
    mount.mkdir()
    status = check(tmp_path, mount, {"id": "1" * 32, "luks_uuid": UUID_A})
    assert status.state == ss.REFUSED and "sem marca" in status.summary()


def test_marker_from_another_instance_is_refused(tmp_path):
    mount = tmp_path / "vol"
    marked(mount, ident="2" * 32)
    status = check(tmp_path, mount, {"id": "1" * 32, "luks_uuid": UUID_A})
    assert status.state == ss.REFUSED and "identificação diferente" in status.summary()


def test_writable_marker_or_symlinked_mount_point_is_refused(tmp_path):
    mount = tmp_path / "vol"
    expected = marked(mount, mode=0o666)
    assert "inseguro" in check(tmp_path, mount, expected).summary()
    link = tmp_path / "atalho"
    link.symlink_to(mount)
    assert ss.check_data_volume(link, expected).state == ss.REFUSED


def test_data_folders_must_live_on_the_volume(tmp_path):
    mount = tmp_path / "vol"
    expected = marked(mount)
    (tmp_path / "fora").mkdir()
    (mount / "dados").symlink_to(tmp_path / "fora")
    status = ss.check_data_volume(mount, expected, paths=(mount / "dados", mount / "estado"),
                                  sys_root=fake_sys(tmp_path, luks(UUID_A)), mountinfo=mountinfo(tmp_path, mount))
    assert status.state == ss.REFUSED and "ausente no volume: dados" in status.summary()
    assert "ausente no volume: estado" in status.summary()


def test_read_only_mount_is_refused_only_where_writing_is_needed(tmp_path, monkeypatch):
    mount = tmp_path / "vol"
    expected = marked(mount)
    (mount / "dados").mkdir()
    args = dict(sys_root=fake_sys(tmp_path, luks(UUID_A)), mountinfo=mountinfo(tmp_path, mount, opts="ro,relatime"))
    assert ss.check_data_volume(mount, expected, **args).ready  # portão/alertas: leitura basta
    status = ss.check_data_volume(mount, expected, require_writable=True, **args)
    assert status.state == ss.REFUSED and "volume montado somente leitura" in status.summary()
    # Com pastas de dados, vale a gravabilidade EFETIVA de cada pasta (ProtectSystem=strict + ReadWritePaths=):
    # ponto de montagem "ro" no namespace do serviço, mas a pasta de dados gravável → aceito.
    assert ss.check_data_volume(mount, expected, paths=(mount / "dados",), require_writable=True, **args).ready
    real = os.statvfs

    class ReadOnly:
        def __init__(self, result):  # type: ignore[no-untyped-def]
            self.f_flag = result.f_flag | os.ST_RDONLY

    monkeypatch.setattr(ss.os, "statvfs", lambda path: ReadOnly(real(path)))
    status = ss.check_data_volume(mount, expected, paths=(mount / "dados",), require_writable=True, **args)
    assert status.state == ss.REFUSED and "pasta de dados somente leitura: dados" in status.summary()


def test_mountinfo_octal_escapes_and_stacked_mounts(tmp_path):
    mount = tmp_path / "com espaço"
    expected = marked(mount)
    info = mountinfo(tmp_path, mount)
    # Montagem MAIS RECENTE por cima (ex.: tmpfs sobre o volume) é a que vale: recusada.
    info.write_text(info.read_text() + f"41 40 0:55 / {str(mount).replace(' ', chr(92) + '040')} rw - tmpfs tmpfs rw\n")
    status = ss.check_data_volume(mount, expected, sys_root=fake_sys(tmp_path, luks(UUID_A)), mountinfo=info)
    assert status.state == ss.REFUSED and "SEM dm-crypt" in status.summary()


def test_mark_volume_writes_once_and_only_on_luks(tmp_path):
    mount = tmp_path / "vol"
    mount.mkdir()
    sys_root, info = fake_sys(tmp_path, luks(UUID_A)), mountinfo(tmp_path, mount)
    marker = ss.mark_volume(mount, sys_root=sys_root, mountinfo=info)
    assert marker["luks_uuid"] == UUID_A and re.fullmatch(r"[0-9a-f]{32}", marker["id"])
    assert (mount / ss.VOLUME_MARKER).stat().st_mode & 0o777 == 0o644
    with pytest.raises(FileExistsError):
        ss.mark_volume(mount, sys_root=sys_root, mountinfo=info)
    plain_dir, plain_tmp = tmp_path / "plain", tmp_path / "p"
    plain_dir.mkdir()
    plain_tmp.mkdir()
    with pytest.raises(ValueError, match="não é LUKS"):
        ss.mark_volume(plain_dir, sys_root=fake_sys(plain_tmp, "CRYPT-PLAIN-x"), mountinfo=mountinfo(plain_tmp, plain_dir))
    with pytest.raises(ValueError, match="não é um ponto de montagem"):
        ss.mark_volume(plain_dir, sys_root=sys_root, mountinfo=mountinfo(plain_tmp, None))


def test_server_root_rules_cannot_be_bypassed():
    with pytest.raises(deploy.DeployError, match="laboratório não é permitido"):
        deploy.prepare_instance(Path("/"), laboratorio=True)
    with pytest.raises(deploy.DeployError, match="/var/lib/rocha-reis-agent"):
        deploy.volume_mount_for(Path("/"), Path("/mnt/outro"))
    assert deploy.volume_mount_for(Path("/"), None) == Path("/var/lib/rocha-reis-agent")


# ------------------------------------------------------------------ ponta a ponta (raiz instalada)
@pytest.fixture()
def dedicated(monkeypatch):  # type: ignore[no-untyped-def]
    """Raiz instalada com volume DEDICADO: o ponto de montagem é uma pasta no $HOME cifrado e só o
    'é ponto de montagem' é simulado (o dispositivo e o UUID LUKS são os REAIS do $HOME)."""
    real_entry = ss.mount_entry
    state = {"mounted": True, "uuid_override": None}
    with encrypted_tmp() as base:
        root = base / "raiz"
        mount = deploy.layout(root)["dados"].parent
        mount.mkdir(parents=True)
        os.chmod(mount, 0o700)
        status = os.stat(mount)

        def fake_entry(path, *, mountinfo=Path("/proc/self/mountinfo")):  # type: ignore[no-untyped-def]
            if os.path.realpath(path) == os.path.realpath(mount):
                if not state["mounted"]:
                    return None
                return {"major": os.major(status.st_dev), "minor": os.minor(status.st_dev), "opcoes": ["rw"],
                        "tipo": "ext4", "origem": "/dev/mapper/teste"}
            return real_entry(path, mountinfo=mountinfo)

        real_uuid = ss.luks_uuid_of

        def fake_uuid(major, minor, *, sys_root=Path("/sys")):  # type: ignore[no-untyped-def]
            kind, uuid = real_uuid(major, minor, sys_root=sys_root)
            return (kind, state["uuid_override"] or uuid)

        monkeypatch.setattr(ss, "mount_entry", fake_entry)
        monkeypatch.setattr(ss, "luks_uuid_of", fake_uuid)
        kind, uuid = real_uuid(os.major(status.st_dev), os.minor(status.st_dev))
        if not uuid:
            pytest.skip("Requer $HOME em LUKS para o teste de ponta a ponta.")
        package = deploy.build_package(base / "pacotes")
        result = deploy.install_release(package, root, python=PYTHON, create_venv=False)
        deploy.activate(root, str(result["id"]))
        yield root, mount, state


def _cli(*args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    import contextlib
    import io
    from rocha_reis_agent.ops.__main__ import main
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(args))
    text = out.getvalue()
    return code, (json.loads(text) if text.strip().startswith("{") else {})


def test_instance_on_dedicated_volume_end_to_end(dedicated):
    root, mount, state = dedicated
    # Sem marca: recusado e NADA criado nas pastas de dados.
    with pytest.raises(deploy.DeployError, match="sem marca"):
        deploy.prepare_instance(root)
    assert not (mount / "dados").exists()
    code, marker = _cli("marcar-volume", "--ponto-montagem", str(mount))
    assert code == 0 and marker["luks_uuid"]
    assert _cli("marcar-volume", "--ponto-montagem", str(mount))[0] == 2  # nunca sobrescreve
    deploy.prepare_instance(root)
    profile = json.loads((deploy.layout(root)["config"] / "perfil.json").read_text())
    assert profile["volume"] == {"ponto_montagem": str(mount), "id": marker["id"], "luks_uuid": marker["luks_uuid"]}
    validation = deploy.validate_installation(root)
    assert validation["ok"] and validation["volume"]["estado"] == "PRONTO", validation
    assert set(validation["travas"].values()) == {False}
    assert _cli("verificar-volume", "--raiz", str(root))[0] == 0

    # 1. volume "bloqueado" (desmontado): tudo recusa; nenhum banco ou pasta é criado no disco de sistema.
    state["mounted"] = False
    database = Path(profile["database"])
    database.unlink()
    for name in ("-wal", "-shm", ".init.lock"):
        Path(str(database) + name).unlink(missing_ok=True)
    validation = deploy.validate_installation(root)
    assert not validation["ok"] and validation["volume"]["estado"] == "AGUARDANDO_DESBLOQUEIO"
    assert _cli("verificar-volume", "--raiz", str(root))[0] == 3
    from rocha_reis_agent.bootstrap import configured_repository
    from rocha_reis_agent.ops.demo import profile_settings
    from rocha_reis_agent.persistence.sqlite import StorageError
    with pytest.raises(StorageError, match="AGUARDANDO_DESBLOQUEIO"):
        configured_repository(profile_settings(deploy.layout(root)["config"]))
    assert not database.exists(), "banco criado com o volume bloqueado"
    code, result = _cli("alertas", "--perfil", str(deploy.layout(root)["config"]))
    assert code == 2 and [a["codigo"] for a in result["alertas"]] == ["volume_aguardando_desbloqueio"]
    assert result["notificacao_externa"] == {"habilitada": False, "seria_enviada": True, "enviada": False,
                                             "canal": None}
    code, layers = _cli("estado-servidor", "--raiz", str(root))
    assert code == 3 and layers["primeira_camada_parada"] == "disco_montado"
    assert layers["camadas"]["banco_disponivel"] == {"ok": False, "nao_avaliado": True}
    (deploy.layout(root)["backups"] / "x.rrbackup").write_bytes(b"x")
    with pytest.raises(deploy.DeployError, match="AGUARDANDO_DESBLOQUEIO"):
        deploy.restore_database(root, database=database, key_file=deploy.layout(root)["chaves"] / "backup.key",
                                backup_name="x.rrbackup", confirmation="RESTAURAR:x.rrbackup")
    assert not database.exists()

    # 2. volume ERRADO montado no caminho esperado (outro LUKS, mesma marca copiada): recusado.
    state["mounted"], state["uuid_override"] = True, UUID_B
    assert _cli("verificar-volume", "--raiz", str(root))[0] == 1
    code, result = _cli("alertas", "--perfil", str(deploy.layout(root)["config"]))
    assert code == 2 and [a["codigo"] for a in result["alertas"]] == ["volume_incorreto"]
    with pytest.raises(StorageError, match="RECUSADO"):
        configured_repository(profile_settings(deploy.layout(root)["config"]))
    assert not database.exists()

    # 3. volume correto de volta: o banco é recriado SÓ agora (a instância sintética foi apagada acima).
    state["uuid_override"] = None
    configured_repository(profile_settings(deploy.layout(root)["config"]))
    assert database.exists()
    code, layers = _cli("estado-servidor", "--raiz", str(root))
    assert code == 1 and layers["camadas"]["disco_montado"]["ok"] and layers["camadas"]["banco_disponivel"]["ok"]
    assert layers["primeira_camada_parada"] == "worker_funcionando" and layers["operacao_24_7"] is False


def test_layers_report_operational_only_when_everything_is_up(dedicated, monkeypatch):
    root, mount, state = dedicated
    _cli("marcar-volume", "--ponto-montagem", str(mount))
    deploy.prepare_instance(root)
    from rocha_reis_agent.ops import readiness
    import time
    beat = deploy.layout(root)["estado"] / "worker.json"
    beat.write_text(json.dumps({"at": time.time()}))
    calls = []
    up = readiness.server_state(root, probe=lambda url, host=None: calls.append((url, host)) or 200)
    assert up["atendimento_operacional"] and readiness.exit_code(up) == 0 and up["operacao_24_7"] is False
    assert calls == [("http://127.0.0.1:8080/ready", None), ("http://127.0.0.1:8000/ready", "painel.interno")]
    down = readiness.server_state(root, probe=lambda url, host=None: 503 if "8080" in url else 200)
    assert down["primeira_camada_parada"] == "webhook_pronto"
    assert down["camadas"]["painel_pronto"]["nao_avaliado"]
    # 24/7 só com prazo de desbloqueio ACORDADO registrado no perfil (decisão do salão).
    path = deploy.layout(root)["config"] / "perfil.json"
    data = json.loads(path.read_text())
    data["continuidade"] = {"prazo_desbloqueio_minutos": 30}
    path.write_text(json.dumps(data))
    assert readiness.server_state(root, probe=lambda url, host=None: 200)["operacao_24_7"] is True


def test_update_refuses_and_creates_nothing_while_volume_is_locked(dedicated):
    root, mount, state = dedicated
    _cli("marcar-volume", "--ponto-montagem", str(mount))
    deploy.prepare_instance(root)
    state["mounted"] = False
    import shutil
    for name in deploy.VOLUME_DIRS:
        shutil.rmtree(deploy.layout(root)[name])
    package = next((root.parent / "pacotes").glob("*.tar.gz"))
    with pytest.raises(deploy.DeployError, match="AGUARDANDO_DESBLOQUEIO"):
        deploy.update(package, root, database=Path("x"), key_file=Path("x"), confirmation="x",
                      stop=lambda: None, start=lambda: None, ready=lambda: True)
    assert not any(deploy.layout(root)[name].exists() for name in deploy.VOLUME_DIRS)


# ------------------------------------------------------------------ unidades systemd (estático)
def _unit(name: str) -> str:
    return (ROOT / "deploy" / "systemd" / name).read_text(encoding="utf-8")


MOUNT = r"var-lib-rocha\x2dreis\x2dagent.mount"


@pytest.mark.parametrize("name", ["rocha-reis-webhook.service", "rocha-reis-panel.service",
                                  "rocha-reis-worker.service", "rocha-reis-backup.service"])
def test_data_services_depend_on_the_verified_volume(name):
    lines = set(_unit(name).splitlines())
    assert {"Requires=rocha-reis-volume.service", "After=rocha-reis-volume.service", f"BindsTo={MOUNT}",
            f"After={MOUNT}"} <= lines, name
    assert "WantedBy=multi-user.target" not in lines  # nada sobe sozinho no boot com o volume bloqueado


def test_volume_gate_alerts_and_target_shape():
    gate = _unit("rocha-reis-volume.service")
    assert "Type=oneshot" in gate and "RemainAfterExit=yes" in gate and f"BindsTo={MOUNT}" in gate
    assert "ops verificar-volume --raiz /" in gate and "ReadWritePaths" not in gate and "PrivateNetwork=true" in gate
    assert "User=rocha-reis" in gate and "LimitCORE=0" in gate
    target = _unit("rocha-reis.target")
    assert "[Install]" not in target.splitlines() and "Requires=rocha-reis-volume.service" in target
    alerts = _unit("rocha-reis-alertas.service")
    assert "rocha-reis-volume" not in alerts and MOUNT not in alerts  # avisa justamente quando o volume falta
    assert "WantedBy=timers.target" in _unit("rocha-reis-alertas.timer")
    assert "WantedBy=rocha-reis.target" in _unit("rocha-reis-backup.timer")


def test_unlock_script_never_takes_the_passphrase_from_arguments_or_files():
    script = (ROOT / "deploy" / "scripts" / "rocha-reis-desbloquear").read_text(encoding="utf-8")
    assert "cryptsetup open --type luks2" in script and "--key-file" not in script and "-d " not in script
    assert "[ -t 0 ]" in script and "verificar-volume --raiz /" in script and "systemctl start rocha-reis.target" in script
    assert "cryptsetup close" in script  # volume errado: fecha e sai sem iniciar nada
    assert "verificar-migracao" not in script  # nunca migra nem copia o banco durante o desbloqueio
    for path in (ROOT / "deploy").rglob("*"):
        if path.is_file():
            assert "crypttab" not in path.name and "fstab" not in path.name


def test_reference_mount_unit_is_skipped_when_locked_and_never_mounted_at_boot():
    text = _unit("rocha-reis-dados.mount")
    lines = set(text.splitlines())
    assert {"ConditionPathExists=/dev/mapper/rocha-reis-dados", "What=/dev/mapper/rocha-reis-dados",
            "Where=/var/lib/rocha-reis-agent", "Options=nodev,nosuid,noexec", "JobTimeoutSec=20s"} <= lines
    assert "[Install]" not in lines and r"var-lib-rocha\x2dreis\x2dagent.mount" in text  # nome de instalação
    guide = (ROOT / "docs" / "GUIA_IMPLANTACAO_SERVIDOR.md").read_text(encoding="utf-8")
    assert r"'/etc/systemd/system/var-lib-rocha\x2dreis\x2dagent.mount'" in guide


def test_worker_heartbeat_failure_never_crashes_the_worker(tmp_path, caplog):
    """Achado no laboratório: com o volume perdido a pasta do batimento some; a gravação não pode derrubar
    o worker (saída 1 no meio da parada controlada)."""
    from rocha_reis_agent.worker import write_heartbeat
    assert write_heartbeat(tmp_path / "some" / "worker.json", processed=1, failures=0) is False
    assert "worker_heartbeat_failed" in caplog.text and str(tmp_path) not in caplog.text
    assert write_heartbeat(tmp_path / "worker.json", processed=1, failures=0) is True
