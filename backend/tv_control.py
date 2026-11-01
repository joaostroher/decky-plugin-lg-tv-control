"""Small SSAP adapter with bounded sessions; no pairing prompts in automation."""
import asyncio
import json
import re
import socket
import ssl
from contextlib import asynccontextmanager
from .network import normalize_mac, resolve_ipv4


class TVError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def response_error(message):
    # Preserve only a numeric code; never expose arbitrary response/credential data.
    match = re.match(r"^\s*(\d{3})(?:\s|$)", str(message.get("error", "")))
    code = int(match.group(1)) if match else None
    if code in (401, 403):
        return TVError(f"The TV did not authorize this command ({code}). Pair again to renew permissions.", code)
    return TVError("The TV rejected the request. Check pairing.", code)


class Session:
    def __init__(self, socket):
        self.socket = socket
        self.counter = 0

    async def receive(self, request_id):
        while True:
            message = json.loads(await self.socket.recv())
            if message.get("id") == request_id:
                if message.get("type") == "error":
                    raise response_error(message)
                return message

    async def request(self, uri, payload=None):
        self.counter += 1
        identifier = str(self.counter)
        await self.socket.send(json.dumps(dict(id=identifier, type="request",
            uri="ssap://" + uri, payload=payload or {})))
        message = await self.receive(identifier)
        data = message.get("payload", {})
        if data.get("returnValue") is False:
            raise TVError("The TV did not execute the command.")
        return data

    async def active_input(self):
        data = await self.request("com.webos.applicationManager/getForegroundAppInfo")
        return data.get("appId")

    async def select_input(self, hdmi):
        try:
            await self.request("tv/switchInput", {"inputId": hdmi})
        except TVError as error:
            if error.code in (401, 403):
                raise TVError("The TV did not authorize HDMI switching. Use Pair / authorize TV to renew permissions.", error.code) from None
            raise

    async def power_off(self):
        await self.request("system/turnOff")

    async def mac_address(self, host):
        """Read the TV's own MACs; disambiguate Wi-Fi/Ethernet by its active IP."""
        info = await self.request("com.webos.service.connectionmanager/getinfo")
        macs = {name: normalize_mac(info.get(name + "Info", {}).get("macAddress"))
                for name in ("wifi", "wired") if isinstance(info.get(name + "Info"), dict)}
        unique = {mac for mac in macs.values() if mac}
        if len(unique) == 1:
            return unique.pop()
        if len(unique) != 2:
            return None
        address = await resolve_ipv4(host)
        if not address:
            return None
        try:
            status = await self.request("com.webos.service.connectionmanager/getStatus")
        except TVError:
            status = await self.request("com.webos.service.connectionmanager/getstatus")
        matched = {mac for name, mac in macs.items() if mac
                   and isinstance(status.get(name), dict)
                   and status[name].get("state") == "connected"
                   and status[name].get("ipAddress") == address}
        return matched.pop() if len(matched) == 1 else None


class TV:
    @asynccontextmanager
    async def session(self, config, key, pairing=False):
        from websockets.legacy.client import connect
        if not config["host"]:
            raise TVError("Set the TV address first.")
        if not key and not pairing:
            raise TVError("Pair the TV first.")
        secure = config["secure"]
        # LG TVs use self-signed certificates. Key authorization is still required.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT) if secure else None
        if context:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        url = f'{"wss" if secure else "ws"}://{config["host"]}:{3001 if secure else 3000}'
        async with connect(url, ssl=context, open_timeout=3, close_timeout=0.2,
                           max_size=1024*1024, ping_interval=None) as ws:
            session = Session(ws)
            payload = {"pairingType": "PROMPT", "forcePairing": False,
                "manifest": {"manifestVersion": 1, "appVersion": "0.1.0",
                    "permissions": ["LAUNCH", "CONTROL_POWER", "CONTROL_INPUT_TV", "READ_RUNNING_APPS",
                                    "READ_POWER_STATE", "READ_INPUT_DEVICE_LIST", "READ_NETWORK_STATE"]}}
            if key:
                payload["client-key"] = key
            await ws.send(json.dumps(dict(id="register", type="register", payload=payload)))
            while True:
                message = await session.receive("register")
                if message.get("type") == "registered":
                    session.key = message.get("payload", {}).get("client-key", key)
                    if not session.key:
                        raise TVError("The TV did not provide a pairing key.")
                    break
                if not pairing:
                    raise TVError("Pairing has expired. Authorize the plugin again on the TV.")
            yield session

    async def wake(self, config):
        mac = config["mac"].replace(":", "")
        if not mac:
            raise TVError("Set the TV MAC address to use Wake-on-LAN.")
        packet = b"\xff" * 6 + bytes.fromhex(mac) * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_sendto(sock, packet, (config["broadcast"], 9))


async def discover():
    """SSDP candidates only; never authorizes or commands discovered devices."""
    target = "urn:lge-com:service:webos-second-screen:1"
    request = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
               'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ' + target + "\r\n\r\n").encode()
    loop = asyncio.get_running_loop()
    found = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        await loop.sock_sendto(sock, request, ("239.255.255.250", 1900))
        deadline = loop.time() + 3
        while loop.time() < deadline and len(found) < 30:
            try:
                data, address = await asyncio.wait_for(loop.sock_recvfrom(sock, 8192), deadline-loop.time())
            except asyncio.TimeoutError:
                break
            if target in data.decode(errors="ignore"):
                found.add(address[0])
    return sorted(found)
