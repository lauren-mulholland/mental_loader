"""Run the app and native Hermes gateway in the foreground; Ctrl+C stops both."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port_busy(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main():
    def stop_requested(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_requested)
    if any(port_busy(port) for port in (8000, 8642)):
        raise SystemExit(
            "Port 8000 or 8642 is already in use. Stop existing project services first."
        )
    commands = [
        [sys.executable, "-m", "mental_loader.cli", "serve"],
        ["bash", str(ROOT / "scripts/hermes.sh"), "gateway", "run"],
    ]
    children = []
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT, start_new_session=True))
        print("Local app: http://localhost:8000 · Hermes API: http://127.0.0.1:8642", flush=True)
        print("Ctrl+C stops these two processes. Docker PostgreSQL stays running.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise SystemExit(
            "A local service exited. Check its output; stopping the companion service."
        )
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    main()
