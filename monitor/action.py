import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


class DemoService:
    def __init__(self):
        self.process = None
        self.last_attempt = -float("inf")

    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("demo.py"))],
            stdout=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(30):
            if self.process.poll() is not None:
                break
            try:
                with urlopen("http://127.0.0.1:18081/", timeout=0.2) as response:
                    if int(response.read()) == self.process.pid:
                        return self.process.pid
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        raise RuntimeError("Demo service did not pass its health check")

    def restart(self):
        old_pid = self.process.pid if self.process else None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        return old_pid, self.start()
