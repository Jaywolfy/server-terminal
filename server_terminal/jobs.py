"""Ein Hintergrundauftrag nach dem anderen (z. B. Updates), mit mitlaufender Ausgabe für die Oberfläche."""

import asyncio
import os
import time
from collections import deque

MAX_LINES = 2000


class JobBusyError(RuntimeError):
    """Es läuft schon ein Auftrag."""


class JobRunner:
    def __init__(self):
        self.lines: deque[str] = deque(maxlen=MAX_LINES)
        self.name: str | None = None
        self.started: float | None = None
        self.finished: float | None = None
        self.exit_codes: list[int] = []
        self.running = False
        self._task: asyncio.Task | None = None

    def start(self, name: str, steps: list[list[str]], env: dict[str, str] | None = None) -> None:
        """Startet die Schritte nacheinander. Bricht beim ersten Fehler ab."""
        if self.running:
            raise JobBusyError(f"Es läuft bereits ein Auftrag: {self.name}")
        self.lines.clear()
        self.name, self.started, self.finished, self.exit_codes, self.running = name, time.time(), None, [], True
        self._task = asyncio.get_running_loop().create_task(self._run(steps, {**os.environ, **(env or {})}))

    async def _run(self, steps: list[list[str]], env: dict[str, str]) -> None:
        try:
            for step in steps:
                self.lines.append(f"$ {' '.join(step)}")
                try:
                    process = await asyncio.create_subprocess_exec(
                        *step, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
                    )
                except FileNotFoundError:
                    self.lines.append(f"Befehl nicht gefunden: {step[0]}")
                    self.exit_codes.append(127)
                    break
                assert process.stdout is not None
                async for raw in process.stdout:
                    self.lines.append(raw.decode("utf-8", "replace").rstrip("\n"))
                code = await process.wait()
                self.exit_codes.append(code)
                if code != 0:
                    self.lines.append(f"Abgebrochen: Exit-Code {code}")
                    break
        finally:
            self.finished, self.running = time.time(), False

    def status(self, since: int = 0) -> dict:
        """Zustand und die Ausgabe ab Zeile `since`, damit die Oberfläche nur Neues holt."""
        lines = list(self.lines)
        return {
            "name": self.name,
            "laeuft": self.running,
            "gestartet": self.started,
            "beendet": self.finished,
            "exit_codes": self.exit_codes,
            "erfolg": (not self.running and bool(self.exit_codes) and all(c == 0 for c in self.exit_codes))
            if self.name
            else None,
            "zeilen": lines[since:],
            "naechste": len(lines),
        }

    async def wait(self) -> None:
        if self._task:
            await self._task
