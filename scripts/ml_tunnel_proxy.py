"""SSH ProxyCommand that carries the ML host's reverse tunnel over the hosted /api/ml-tunnel WebSocket.

Usage (on the ML host): ssh -o ProxyCommand="python ml_tunnel_proxy.py" ... with ML_TUNNEL_URL and
ML_TOKEN_FILE set. Requires simple-websocket.
"""

import os
import sys
import threading
from pathlib import Path

from simple_websocket import Client, ConnectionClosed


def main() -> None:
    token = Path(os.environ["ML_TOKEN_FILE"]).read_text().strip()
    ws = Client.connect(os.environ["ML_TUNNEL_URL"], headers={"Authorization": f"Bearer {token}"})

    def upload() -> None:
        try:
            while data := os.read(0, 65536):
                ws.send(data)
        except (OSError, ConnectionClosed):
            pass
        ws.close()

    threading.Thread(target=upload, daemon=True).start()
    try:
        while (data := ws.receive()) is not None:
            sys.stdout.buffer.write(data if isinstance(data, bytes) else data.encode())
            sys.stdout.buffer.flush()
    except ConnectionClosed:
        pass


if __name__ == "__main__":
    main()
