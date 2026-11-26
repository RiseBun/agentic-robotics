"""Bound startup to three local inference servers, which listen after loading."""
import socket
import time

pending = {8114, 8115, 8116}
deadline = time.monotonic()+180
while pending and time.monotonic() < deadline:
    for port in list(pending):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                pending.remove(port)
        except OSError:
            pass
    if pending:
        time.sleep(0.5)
if pending:
    raise SystemExit(f'Inference services not ready: {sorted(pending)}; inspect systemd user logs')
print('All three local inference services are listening', flush=True)
