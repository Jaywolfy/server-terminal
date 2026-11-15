import json
import unittest
from unittest import mock

from server_terminal import docker_ctl

PS = "\n".join(
    json.dumps(item)
    for item in [
        {"Names": "web", "Image": "nginx:alpine", "State": "running", "Status": "Up 2 hours", "Ports": "0.0.0.0:80->80/tcp"},
        {"Names": "alpha", "Image": "redis", "State": "exited", "Status": "Exited (0) 1 day ago", "Ports": ""},
    ]
)
STATS = json.dumps({"Name": "web", "CPUPerc": "1.50%", "MemUsage": "20MiB / 1GiB"})


class ParseTest(unittest.TestCase):
    def test_container_nach_namen_sortiert(self):
        containers = docker_ctl.parse_ps(PS)

        self.assertEqual([c["name"] for c in containers], ["alpha", "web"])
        self.assertEqual(containers[1]["zustand"], "running")

    def test_zeilen_ohne_json_werden_ignoriert(self):
        self.assertEqual(docker_ctl.parse_ps("Warnung: irgendwas\n" + PS)[0]["name"], "alpha")

    def test_statistik(self):
        self.assertEqual(docker_ctl.parse_stats(STATS), {"web": {"cpu": "1.50%", "speicher": "20MiB"}})


class ActionTest(unittest.IsolatedAsyncioTestCase):
    async def test_ungueltige_namen_und_aktionen(self):
        for name in ("", "-x", "a b", "a;rm -rf /", "../etc", "a\nb", "x" * 200):
            with self.assertRaises(ValueError, msg=name):
                await docker_ctl.act(name, "restart")
            with self.assertRaises(ValueError, msg=name):
                await docker_ctl.logs(name)
        with self.assertRaises(ValueError):
            await docker_ctl.act("web", "rm")
        with self.assertRaises(ValueError):
            await docker_ctl.act("web", "exec")

    async def test_aktion_ruft_docker_ohne_shell_auf(self):
        run = mock.AsyncMock(return_value=(0, "web\n"))
        with mock.patch.object(docker_ctl, "run", run):
            output = await docker_ctl.act("web", "restart")

        run.assert_awaited_once_with("docker", "restart", "web", timeout=90)
        self.assertEqual(output, "web")

    async def test_fehler_von_docker_wird_gemeldet(self):
        with mock.patch.object(docker_ctl, "run", mock.AsyncMock(return_value=(1, "No such container: web"))):
            with self.assertRaisesRegex(RuntimeError, "No such container"):
                await docker_ctl.act("web", "stop")

    async def test_container_mit_statistik(self):
        run = mock.AsyncMock(side_effect=[(0, PS), (0, STATS)])
        with mock.patch.object(docker_ctl, "run", run):
            containers = await docker_ctl.containers()

        web = next(c for c in containers if c["name"] == "web")
        self.assertEqual((web["cpu"], web["speicher"]), ("1.50%", "20MiB"))
        self.assertEqual(next(c for c in containers if c["name"] == "alpha")["cpu"], "")

    async def test_docker_nicht_erreichbar(self):
        with mock.patch.object(docker_ctl, "run", mock.AsyncMock(return_value=(1, "Cannot connect to the Docker daemon"))):
            with self.assertRaisesRegex(RuntimeError, "Docker daemon"):
                await docker_ctl.containers()


if __name__ == "__main__":
    unittest.main()
