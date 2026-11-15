"""Browser-Terminal: eine echte Shell auf einem Pseudo-Terminal (PTY), verbunden über WebSocket.

Mit tmux bleibt die Sitzung beim Schließen des Tabs bestehen und lässt sich später wieder aufnehmen.
Nachrichten vom Browser sind JSON: {"t": "i", "d": "<Eingabe>"} und {"t": "r", "c": Spalten, "r": Zeilen}.
"""

import asyncio
import codecs
import fcntl
import os
import shutil
import signal
import struct
import subprocess
import termios

MAX_SESSIONS = 3
TMUX_SESSION = "web"


def shell_command(persistent: bool) -> list[str]:
    if persistent and shutil.which("tmux"):
        return [
            "tmux", "new-session", "-A", "-s", TMUX_SESSION, ";",
            "set-option", "history-limit", "50000", ";",
            "set-option", "mouse", "on", ";",
            "set-option", "status", "off",
        ]  # fmt: skip
    return [shutil.which("bash") or "/bin/sh", "-l"]


def set_window_size(fd: int, columns: int, rows: int) -> None:
    columns, rows = max(1, min(columns, 500)), max(1, min(rows, 200))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


class TerminalSession:
    def __init__(self, command: list[str], columns: int = 100, rows: int = 30, cwd: str | None = None):
        self.command = command
        self.master, slave = os.openpty()
        set_window_size(self.master, columns, rows)
        env = {**os.environ, "TERM": "xterm-256color", "LANG": os.environ.get("LANG", "C.UTF-8"), "COLORTERM": "truecolor"}

        def controlling_terminal():
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.process = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=cwd or env.get("HOME", "/"),
            start_new_session=True,
            preexec_fn=controlling_terminal,  # noqa: PLW1509 - nötig, damit die Shell ein steuerndes Terminal hat
            close_fds=True,
        )
        os.close(slave)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(self.master, self._on_readable)
        self._closed = False

    def _on_readable(self) -> None:
        try:
            data = os.read(self.master, 65536)
        except OSError:
            data = b""
        if not data:
            self._loop.remove_reader(self.master)
            self._queue.put_nowait(None)
            return
        text = self._decoder.decode(data)
        if text:
            self._queue.put_nowait(text)

    async def read(self) -> str | None:
        """Nächste Ausgabe oder None, wenn die Shell beendet ist."""
        return await self._queue.get()

    def write(self, text: str) -> None:
        if not self._closed:
            os.write(self.master, text.encode("utf-8"))

    def resize(self, columns: int, rows: int) -> None:
        if not self._closed:
            set_window_size(self.master, columns, rows)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.remove_reader(self.master)
        except (ValueError, OSError):
            pass
        try:
            os.killpg(self.process.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.close(self.master)
        except OSError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
