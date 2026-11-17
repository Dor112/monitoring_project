import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import sleep

MIB = 1024 ** 2
marker = Path("/trial/started")
allocations = []


def grow_once():
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        return
    sleep(120)
    for _ in range(20):
        block = bytearray(8 * MIB)
        for offset in range(0, len(block), 4096):
            block[offset] = 1
        allocations.append(block)
        sleep(55)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, *_args):
        pass


server = HTTPServer(("0.0.0.0", 8000), Health)
threading.Thread(target=grow_once, daemon=True).start()
server.serve_forever()
