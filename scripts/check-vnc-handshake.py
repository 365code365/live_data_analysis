#!/usr/bin/env python3
"""验证 noVNC 那条链路是否真的通：WebSocket → websockify → x11vnc → RFB 握手。

浏览器里黑屏时，问题可能出在 websockify、x11vnc、或者页面 JS 三段中的任意一段。
这个脚本只用标准库做一次真实的 WebSocket 握手并读取 RFB 版本号，
能把「服务端链路」和「前端页面」彻底分开定位。

用法:
    python3 scripts/check-vnc-handshake.py <host> <port> [password]
    python3 scripts/check-vnc-handshake.py localhost 21001
"""
from __future__ import annotations

import base64
import os
import socket
import sys


def ws_handshake(host: str, port: int, path: str = "/websockify", timeout: float = 10.0) -> socket.socket:
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Protocol: binary\r\n"
        "\r\n"
    )
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("握手期间连接被关闭")
        buf += chunk
    head = buf.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
    if "101" not in head.splitlines()[0]:
        raise RuntimeError(f"WebSocket 升级失败:\n{head}")
    print("  [1/3] WebSocket 升级成功")
    if "binary" not in head.lower():
        print("        注意: 服务端没有回 binary 子协议（老版本 websockify 可能如此）")
    return sock


def read_ws_frame(sock: socket.socket) -> bytes:
    """读一个（未分片、服务端不掩码的）WebSocket 数据帧。"""
    hdr = sock.recv(2)
    if len(hdr) < 2:
        raise RuntimeError("没有读到 WebSocket 帧头")
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(sock.recv(2), "big")
    elif length == 127:
        length = int.from_bytes(sock.recv(8), "big")
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            break
        payload += chunk
    return payload


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2] if len(sys.argv) > 2 else 6080)
    print(f"检查 {host}:{port}")
    try:
        sock = ws_handshake(host, port)
    except Exception as exc:
        print(f"  失败: {exc}")
        return 1

    try:
        data = read_ws_frame(sock)
    except Exception as exc:
        print(f"  [2/3] 读取 RFB 版本失败: {exc}")
        return 1
    finally:
        sock.close()

    if data.startswith(b"RFB "):
        print(f"  [2/3] x11vnc 回了 RFB 版本: {data[:12].decode(errors='replace').strip()}")
        print("  [3/3] 服务端链路正常（websockify → x11vnc 通）")
        print("\n结论: 如果浏览器里仍然黑屏，问题在前端页面或密码参数，不在服务端。")
        return 0

    print(f"  [2/3] 收到非 RFB 数据: {data[:32]!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
