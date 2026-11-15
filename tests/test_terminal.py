import asyncio
import unittest

from server_terminal.terminal import TerminalSession, shell_command


async def read_until(session, needle, timeout=5.0):
    collected = ""
    async with asyncio.timeout(timeout):
        while needle not in collected:
            chunk = await session.read()
            if chunk is None:
                break
            collected += chunk
    return collected


class TerminalSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_eingabe_und_ausgabe(self):
        session = TerminalSession(["/bin/sh"], 80, 24, cwd="/")
        self.addCleanup(session.close)

        session.write("echo hallo-$((20+22))\n")

        self.assertIn("hallo-42", await read_until(session, "hallo-42"))

    async def test_fenstergroesse_ist_in_der_shell_sichtbar(self):
        session = TerminalSession(["/bin/sh"], 80, 24, cwd="/")
        self.addCleanup(session.close)
        session.resize(123, 45)

        session.write("stty size\n")

        self.assertIn("45 123", await read_until(session, "45 123"))

    async def test_es_ist_ein_echtes_terminal(self):
        session = TerminalSession(["/bin/sh"], 80, 24, cwd="/")
        self.addCleanup(session.close)

        session.write("test -t 0 && echo tty-ja\n")

        self.assertIn("tty-ja", await read_until(session, "tty-ja"))

    async def test_umlaute_kommen_unversehrt_an(self):
        session = TerminalSession(["/bin/sh"], 80, 24, cwd="/")
        self.addCleanup(session.close)

        session.write("echo äöü€\n")

        self.assertIn("äöü€", await read_until(session, "äöü€"))

    async def test_ende_der_shell_wird_gemeldet(self):
        session = TerminalSession(["/bin/sh", "-c", "echo fertig"], 80, 24, cwd="/")
        self.addCleanup(session.close)

        output = ""
        async with asyncio.timeout(5):
            while (chunk := await session.read()) is not None:
                output += chunk

        self.assertIn("fertig", output)

    async def test_schliessen_beendet_den_prozess(self):
        session = TerminalSession(["/bin/sh", "-c", "sleep 60"], 80, 24, cwd="/")

        session.close()

        self.assertIsNotNone(session.process.returncode)

    async def test_mehrfaches_schliessen_ist_unschaedlich(self):
        session = TerminalSession(["/bin/sh"], 80, 24, cwd="/")

        session.close()
        session.close()
        session.write("ignoriert\n")
        session.resize(10, 10)


class ShellCommandTest(unittest.TestCase):
    def test_ohne_tmux_eine_anmelde_shell(self):
        command = shell_command(False)

        self.assertTrue(command[0].endswith(("bash", "sh")))
        self.assertEqual(command[1:], ["-l"])

    def test_mit_tmux_bleibt_die_sitzung_erhalten(self):
        command = shell_command(True)

        if command[0] == "tmux":
            self.assertIn("-A", command)
            self.assertIn("history-limit", command)
        else:
            self.assertEqual(command[1:], ["-l"])


if __name__ == "__main__":
    unittest.main()
