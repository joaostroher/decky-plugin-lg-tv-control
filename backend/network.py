"""Best-effort IPv4 neighbour lookup without root or raw sockets."""
import asyncio
import ipaddress
import json
import os
import platform
import re
import shutil
import socket


def normalize_mac(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{1,2}:){5}[0-9a-fA-F]{1,2}", value):
        return None
    octets = bytes(int(part, 16) for part in value.split(":"))
    if not any(octets) or octets[0] & 1:
        return None
    return ":".join(f"{octet:02X}" for octet in octets)


async def command_output(executable, *arguments):
    process = await asyncio.create_subprocess_exec(executable, *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 1)
        return output.decode(errors="replace") if process.returncode == 0 else ""
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def ip_query(*arguments):
    executable = shutil.which("ip") or next((path for path in
        ("/usr/bin/ip", "/usr/sbin/ip", "/bin/ip", "/sbin/ip") if os.access(path, os.X_OK)), None)
    if executable is None:
        return []
    output = await command_output(executable, "-j", *arguments)
    data = json.loads(output) if output else []
    return data if isinstance(data, list) else []


def macos_route_interface(output):
    fields = dict(line.strip().split(":", 1) for line in output.splitlines() if ":" in line)
    flags = fields.get("flags", "").strip()
    if not flags.startswith("<") or not flags.endswith(">"):
        return None
    flags = set(flags[1:-1].split(","))
    interface = fields.get("interface", "").strip()
    if ("UP" not in flags or flags & {"GATEWAY", "REJECT", "BLACKHOLE", "LOCAL"}
            or not re.fullmatch(r"[a-zA-Z0-9_.-]+", interface) or interface.startswith("lo")):
        return None
    return interface


def macos_arp_mac(output, address, interface):
    candidates = set()
    for match in re.finditer(r"\(([^)]+)\)\s+at\s+(\S+)\s+on\s+(\S+)([^\n]*)", output):
        ip, raw_mac, device, flags = match.groups()
        if ip != address or device != interface or any(flag in flags.lower() for flag in ("published", "proxy")):
            continue
        mac = normalize_mac(raw_mac)
        if mac:
            candidates.add(mac)
    return candidates.pop() if len(candidates) == 1 else None


async def detect_macos_mac(address):
    route = await command_output("/sbin/route", "-n", "get", address)
    interface = macos_route_interface(route)
    if not interface:
        return None
    await probe(address)
    for attempt in range(2):
        output = await command_output("/usr/sbin/arp", "-n", "-i", interface, address)
        mac = macos_arp_mac(output, address, interface)
        if mac:
            return mac
        if attempt == 0:
            await asyncio.sleep(0.2)
    return None


async def resolve_ipv4(host):
    addresses = await asyncio.get_running_loop().getaddrinfo(host, None,
        family=socket.AF_INET, type=socket.SOCK_STREAM)
    unique = {item[4][0] for item in addresses}
    if len(unique) != 1:
        return None
    address = ipaddress.IPv4Address(unique.pop())
    if address.is_loopback or address.is_multicast or address.is_unspecified or address.is_reserved:
        return None
    return str(address)


async def probe(address):
    # A TCP handshake triggers normal kernel ARP resolution, even if this port is closed.
    # No webOS command, magic packet or pairing request is sent.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.setblocking(False)
        try:
            await asyncio.wait_for(asyncio.get_running_loop().sock_connect(connection, (address, 3001)), 0.7)
        except (OSError, TimeoutError):
            pass


def neighbour_mac(entries, address, interface):
    candidates = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("dst") != address or entry.get("dev") != interface:
            continue
        states = entry.get("state", [])
        if isinstance(states, str):
            states = [states]
        if not isinstance(states, list) or not states:
            continue
        if any(state not in {"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT"} for state in states):
            continue
        mac = normalize_mac(entry.get("lladdr"))
        if mac and not entry.get("router"):
            candidates.add(mac)
    return candidates.pop() if len(candidates) == 1 else None


async def detect_mac(host):
    try:
        async with asyncio.timeout(4):
            address = await resolve_ipv4(host)
            if not address:
                return None
            if platform.system() == "Darwin":
                return await detect_macos_mac(address)
            routes = await ip_query("route", "get", address)
            # A routed destination exposes the gateway's MAC, not the TV's MAC.
            if len(routes) != 1 or not isinstance(routes[0], dict):
                return None
            route = routes[0]
            interface = route.get("dev")
            if (not isinstance(interface, str) or interface == "lo" or route.get("gateway")
                    or route.get("type", "unicast") != "unicast"):
                return None
            await probe(address)
            for attempt in range(2):
                entries = await ip_query("neigh", "show", "to", address, "dev", interface)
                mac = neighbour_mac(entries, address, interface)
                if mac:
                    return mac
                if attempt == 0:
                    await asyncio.sleep(0.2)
    except (OSError, ValueError, TimeoutError):
        pass
    return None
