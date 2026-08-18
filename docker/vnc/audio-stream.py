#!/usr/bin/env python3
"""按需音频流：有人连进来才起 ffmpeg，没人听时不烧一点 CPU。

原来用的是 `ffmpeg -listen 1 ... http://0.0.0.0:6081`，让 ffmpeg 自己当 HTTP 服务。
问题是 ffmpeg 一启动就把 pulse 输入打开了，即使没有任何听众，
录制线程也在跑，实测常态吃掉 5% 左右的 CPU —— 一台设备无所谓，
开五六台就是白扔半个核。

这里自己 accept：连接进来 → 回 HTTP 头 → 起 ffmpeg 把 mp3 直接写进这个 socket；
客户端一断开 ffmpeg 写失败退出，回到零开销的等待状态。

同一时刻只服务一个听众（控制台场景够用）。要多人同时听，
在前面挂一层 nginx / icecast 转发。
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

PORT = int(os.environ.get("AUDIO_PORT", "6081"))
BITRATE = os.environ.get("AUDIO_BITRATE", "96k")
SINK = f"{os.environ.get('AUDIO_SINK', 'ldm')}.monitor"

HEADERS = (
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: audio/mpeg\r\n"
    "Cache-Control: no-store\r\n"
    "Access-Control-Allow-Origin: *\r\n"
    "Connection: close\r\n"
    "\r\n"
)


def log(msg: str) -> None:
    print(f"[audio {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ffmpeg_cmd() -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        # -re 不要加：这是实时源，加了反而会漂
        "-f", "pulse", "-i", SINK,
        # 48kHz 与安卓侧、null sink 保持一致，避免多一道重采样
        "-ac", "2", "-ar", "48000",
        "-c:a", "libmp3lame", "-b:a", BITRATE,
        "-flush_packets", "1",
        "-f", "mp3", "pipe:1",
    ]


def read_request(conn: socket.socket) -> bytes:
    """读掉 HTTP 请求头（不解析路径，任何路径都给流）。"""
    conn.settimeout(5)
    data = b""
    try:
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk
    except (socket.timeout, OSError):
        pass
    finally:
        conn.settimeout(None)
    return data


def serve(conn: socket.socket, addr: tuple) -> None:
    req = read_request(conn)
    if req.startswith(b"OPTIONS"):
        conn.sendall(
            b"HTTP/1.1 204 No Content\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"Access-Control-Allow-Headers: *\r\n\r\n"
        )
        return

    conn.sendall(HEADERS.encode())
    log(f"听众接入 {addr[0]}，启动编码")
    started = time.time()
    proc = subprocess.Popen(
        ffmpeg_cmd(),
        stdout=conn.fileno(),          # ffmpeg 直接写进 socket，不经过 python
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,          # 单独进程组，便于整组收掉
    )
    try:
        _, err = proc.communicate()
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                pass
    tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
    log(f"听众断开，编码停止（在线 {time.time() - started:.0f}s）")
    for line in tail:
        log(f"ffmpeg: {line}")


def main() -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", PORT))
    except OSError as exc:
        log(f"端口 {PORT} 绑定失败: {exc}")
        return 1
    srv.listen(4)
    log(f"按需音频流就绪 :{PORT}  源={SINK}  码率={BITRATE}（无人收听时不编码）")

    while True:
        try:
            conn, addr = srv.accept()
        except KeyboardInterrupt:
            return 0
        except OSError as exc:
            log(f"accept 失败: {exc}")
            time.sleep(1)
            continue
        try:
            serve(conn, addr)
        except (BrokenPipeError, ConnectionResetError):
            log("听众连接中断")
        except Exception as exc:  # noqa: BLE001
            log(f"服务异常: {exc}")
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
