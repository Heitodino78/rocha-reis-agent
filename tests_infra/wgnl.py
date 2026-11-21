"""Configurador WireGuard MÍNIMO para os testes da Fase 18 (substitui `wg`/`wg syncconf` onde eles não existem).

Fala com o módulo WireGuard do KERNEL por netlink genérico (WG_CMD_SET_DEVICE / WG_CMD_GET_DEVICE) e lê o
mesmo formato de arquivo do wg-quick usado em deploy/wireguard/. Chaves X25519 pela biblioteca
`cryptography` (já dependência do projeto). É ferramenta de TESTE: no servidor use o `wg` oficial
(wireguard-tools). Não altera nada fora do namespace de rede em que é executado.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import ipaddress
import os
import socket
import struct

NETLINK_GENERIC = 16
NLM_F_REQUEST, NLM_F_ACK, NLM_F_DUMP = 0x1, 0x4, 0x300
NLMSG_ERROR, NLMSG_DONE = 2, 3
GENL_ID_CTRL, CTRL_CMD_GETFAMILY, CTRL_ATTR_FAMILY_ID, CTRL_ATTR_FAMILY_NAME = 0x10, 3, 1, 2
NLA_F_NESTED = 1 << 15
WG_CMD_GET_DEVICE, WG_CMD_SET_DEVICE = 0, 1
(WGDEVICE_A_IFNAME, WGDEVICE_A_PRIVATE_KEY, WGDEVICE_A_PUBLIC_KEY, WGDEVICE_A_FLAGS, WGDEVICE_A_LISTEN_PORT,
 WGDEVICE_A_PEERS) = 2, 3, 4, 5, 6, 8
WGDEVICE_F_REPLACE_PEERS = 1
(WGPEER_A_PUBLIC_KEY, WGPEER_A_PRESHARED_KEY, WGPEER_A_FLAGS, WGPEER_A_ENDPOINT, WGPEER_A_KEEPALIVE,
 WGPEER_A_LAST_HANDSHAKE, WGPEER_A_RX, WGPEER_A_TX, WGPEER_A_ALLOWEDIPS) = 1, 2, 3, 4, 5, 6, 7, 8, 9
WGPEER_F_REMOVE_ME, WGPEER_F_REPLACE_ALLOWEDIPS = 1, 2
WGALLOWEDIP_A_FAMILY, WGALLOWEDIP_A_IPADDR, WGALLOWEDIP_A_CIDR_MASK = 1, 2, 3


# ------------------------------------------------------------------ chaves
def genkey() -> str:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
    raw = X25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(raw).decode()


def pubkey(private: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private))
    return base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def genpsk() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# ------------------------------------------------------------------ arquivo wg-quick
@dataclass
class Peer:
    public_key: str
    allowed_ips: list[str] = field(default_factory=list)
    preshared_key: str | None = None
    endpoint: str | None = None
    keepalive: int = 0


@dataclass
class Config:
    private_key: str
    addresses: list[str] = field(default_factory=list)
    listen_port: int = 0
    peers: list[Peer] = field(default_factory=list)


def parse(text: str) -> Config:
    section, interface, peers = None, {}, []  # type: ignore[var-annotated]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            section = line.strip("[]").lower()
            if section == "peer":
                peers.append({})
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        (peers[-1] if section == "peer" else interface)[key.lower()] = value
    split = lambda v: [x.strip() for x in v.split(",") if x.strip()]  # noqa: E731
    return Config(private_key=interface["privatekey"], addresses=split(interface.get("address", "")),
                  listen_port=int(interface.get("listenport", 0)),
                  peers=[Peer(public_key=p["publickey"], allowed_ips=split(p.get("allowedips", "")),
                              preshared_key=p.get("presharedkey"), endpoint=p.get("endpoint"),
                              keepalive=int(p.get("persistentkeepalive", 0))) for p in peers])


# ------------------------------------------------------------------ netlink
def _attr(kind: int, payload: bytes) -> bytes:
    length = 4 + len(payload)
    return struct.pack("=HH", length, kind) + payload + b"\0" * ((4 - length % 4) % 4)


def _nested(kind: int, items: list[bytes]) -> bytes:
    return _attr(kind | NLA_F_NESTED, b"".join(items))


def _parse_attrs(data: bytes) -> dict[int, list[bytes]]:
    attrs: dict[int, list[bytes]] = {}
    offset = 0
    while offset + 4 <= len(data):
        length, kind = struct.unpack_from("=HH", data, offset)
        if length < 4:
            break
        attrs.setdefault(kind & ~NLA_F_NESTED, []).append(data[offset + 4:offset + length])
        offset += (length + 3) & ~3
    return attrs


class Netlink:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.sock.bind((0, 0))
        self.seq = 0
        self.family = self._family("wireguard")

    def close(self) -> None:
        self.sock.close()

    def _request(self, kind: int, cmd: int, version: int, payload: bytes, flags: int) -> list[bytes]:
        self.seq += 1
        body = struct.pack("=BBH", cmd, version, 0) + payload
        self.sock.send(struct.pack("=IHHII", 16 + len(body), kind, flags, self.seq, 0) + body)
        replies: list[bytes] = []
        while True:
            data = self.sock.recv(1 << 16)
            offset, finished = 0, False
            while offset + 16 <= len(data):
                length, mtype, mflags, seq, _ = struct.unpack_from("=IHHII", data, offset)
                message = data[offset + 16:offset + length]
                offset += (length + 3) & ~3
                if mtype == NLMSG_ERROR:
                    error = struct.unpack_from("=i", message)[0]
                    if error:
                        raise OSError(-error, os.strerror(-error))
                    finished = True
                elif mtype == NLMSG_DONE:
                    finished = True
                else:
                    replies.append(message[4:])
                    if not flags & NLM_F_DUMP:
                        finished = finished or not flags & NLM_F_ACK
            if finished:
                return replies

    def _family(self, name: str) -> int:
        replies = self._request(GENL_ID_CTRL, CTRL_CMD_GETFAMILY, 1,
                                _attr(CTRL_ATTR_FAMILY_NAME, name.encode() + b"\0"), NLM_F_REQUEST | NLM_F_ACK)
        for reply in replies:
            attrs = _parse_attrs(reply)
            if CTRL_ATTR_FAMILY_ID in attrs:
                return struct.unpack("=H", attrs[CTRL_ATTR_FAMILY_ID][0][:2])[0]
        raise OSError("família wireguard indisponível (módulo do kernel?)")

    def set_device(self, ifname: str, config: Config, *, replace_peers: bool = True,
                   remove: tuple[str, ...] = ()) -> None:
        """replace_peers=True equivale a `wg setconf` (todos os peers recriados: sessões e endpoints aprendidos
        se perdem). Para revogar sem derrubar os outros use sync_device (= `wg syncconf`)."""
        peers = [_nested(1000 + i, [_attr(WGPEER_A_PUBLIC_KEY, base64.b64decode(key)),
                                    _attr(WGPEER_A_FLAGS, struct.pack("=I", WGPEER_F_REMOVE_ME))])
                 for i, key in enumerate(remove)]
        for index, peer in enumerate(config.peers):
            items = [_attr(WGPEER_A_PUBLIC_KEY, base64.b64decode(peer.public_key)),
                     _attr(WGPEER_A_FLAGS, struct.pack("=I", WGPEER_F_REPLACE_ALLOWEDIPS)),
                     _attr(WGPEER_A_KEEPALIVE, struct.pack("=H", peer.keepalive))]
            if peer.preshared_key:
                items.append(_attr(WGPEER_A_PRESHARED_KEY, base64.b64decode(peer.preshared_key)))
            if peer.endpoint:
                host, port = peer.endpoint.rsplit(":", 1)
                items.append(_attr(WGPEER_A_ENDPOINT, struct.pack("=H", socket.AF_INET) + struct.pack("!H", int(port))
                                   + socket.inet_aton(host) + b"\0" * 8))
            allowed = []
            for position, network in enumerate(peer.allowed_ips):
                net = ipaddress.ip_network(network, strict=False)
                family = socket.AF_INET if net.version == 4 else socket.AF_INET6
                allowed.append(_nested(position, [_attr(WGALLOWEDIP_A_FAMILY, struct.pack("=H", family)),
                                                  _attr(WGALLOWEDIP_A_IPADDR, net.network_address.packed),
                                                  _attr(WGALLOWEDIP_A_CIDR_MASK, struct.pack("=B", net.prefixlen))]))
            items.append(_nested(WGPEER_A_ALLOWEDIPS, allowed))
            peers.append(_nested(index, items))
        payload = (_attr(WGDEVICE_A_IFNAME, ifname.encode() + b"\0")
                   + _attr(WGDEVICE_A_PRIVATE_KEY, base64.b64decode(config.private_key))
                   + _attr(WGDEVICE_A_FLAGS, struct.pack("=I", WGDEVICE_F_REPLACE_PEERS if replace_peers else 0))
                   + _attr(WGDEVICE_A_LISTEN_PORT, struct.pack("=H", config.listen_port))
                   + _nested(WGDEVICE_A_PEERS, peers))
        self._request(self.family, WG_CMD_SET_DEVICE, 1, payload, NLM_F_REQUEST | NLM_F_ACK)

    def sync_device(self, ifname: str, config: Config) -> list[str]:
        """`wg syncconf`: remove só os peers que saíram do arquivo e atualiza os demais SEM recriá-los (quem
        continua mantém sessão e endpoint aprendido). Retorna as chaves removidas."""
        wanted = {peer.public_key for peer in config.peers}
        current = set(self.get_device(ifname)["peers"])  # type: ignore[arg-type]
        removed = tuple(sorted(current - wanted))
        self.set_device(ifname, config, replace_peers=False, remove=removed)
        return list(removed)

    def get_device(self, ifname: str) -> dict[str, object]:
        replies = self._request(self.family, WG_CMD_GET_DEVICE, 1, _attr(WGDEVICE_A_IFNAME, ifname.encode() + b"\0"),
                                NLM_F_REQUEST | NLM_F_DUMP)
        device: dict[str, object] = {"peers": {}}
        for reply in replies:
            attrs = _parse_attrs(reply)
            if WGDEVICE_A_PUBLIC_KEY in attrs:
                device["public_key"] = base64.b64encode(attrs[WGDEVICE_A_PUBLIC_KEY][0]).decode()
            if WGDEVICE_A_LISTEN_PORT in attrs:
                device["listen_port"] = struct.unpack("=H", attrs[WGDEVICE_A_LISTEN_PORT][0][:2])[0]
            for peers_blob in attrs.get(WGDEVICE_A_PEERS, []):
                for blob in [b for group in _parse_attrs(peers_blob).values() for b in group]:
                    p = _parse_attrs(blob)
                    key = base64.b64encode(p[WGPEER_A_PUBLIC_KEY][0]).decode()
                    seconds = struct.unpack("=qq", p[WGPEER_A_LAST_HANDSHAKE][0])[0] if WGPEER_A_LAST_HANDSHAKE in p else 0
                    device["peers"][key] = {  # type: ignore[index]
                        "ultimo_handshake": seconds,
                        "rx": struct.unpack("=Q", p[WGPEER_A_RX][0])[0] if WGPEER_A_RX in p else 0,
                        "tx": struct.unpack("=Q", p[WGPEER_A_TX][0])[0] if WGPEER_A_TX in p else 0}
        return device
