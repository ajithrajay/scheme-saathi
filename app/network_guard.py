"""Reject private/metadata network destinations in hosted official browser sessions.

DNS validation is defense in depth; production hosts should also enforce egress
rules. A proxy/firewall is needed to close DNS rebinding races completely.
"""
import asyncio
import ipaddress
import socket

async def public_destination(host):
    if not host or host.lower() in {'localhost','metadata.google.internal'}:
        return False
    try:
        results = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo,host,443,type=socket.SOCK_STREAM),5)
        return bool(results) and all(ipaddress.ip_address(item[4][0]).is_global for item in results)
    except (OSError, ValueError, asyncio.TimeoutError):
        return False
