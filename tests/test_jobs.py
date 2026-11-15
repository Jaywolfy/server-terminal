import unittest

from server_terminal.jobs import JobBusyError, JobRunner


class JobRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_schritte_laufen_nacheinander_mit_ausgabe(self):
        job = JobRunner()
        job.start("test", [["printf", "eins\\n"], ["printf", "zwei\\n"]])

        await job.wait()
        status = job.status()

        self.assertFalse(status["laeuft"])
        self.assertTrue(status["erfolg"])
        self.assertEqual(status["exit_codes"], [0, 0])
        self.assertEqual(status["zeilen"], ["$ printf eins\\n", "eins", "$ printf zwei\\n", "zwei"])

    async def test_erster_fehler_bricht_ab(self):
        job = JobRunner()
        job.start("test", [["sh", "-c", "echo schief; exit 2"], ["printf", "nie"]])

        await job.wait()
        status = job.status()

        self.assertFalse(status["erfolg"])
        self.assertEqual(status["exit_codes"], [2])
        self.assertNotIn("nie", status["zeilen"])
        self.assertIn("Abgebrochen: Exit-Code 2", status["zeilen"])

    async def test_unbekannter_befehl(self):
        job = JobRunner()
        job.start("test", [["gibt-es-nicht-xyz"]])

        await job.wait()

        self.assertEqual(job.status()["exit_codes"], [127])
        self.assertFalse(job.status()["erfolg"])

    async def test_zweiter_auftrag_waehrend_einer_laeuft_wird_abgewiesen(self):
        job = JobRunner()
        job.start("lang", [["sleep", "1"]])

        with self.assertRaises(JobBusyError):
            job.start("zweiter", [["true"]])
        await job.wait()

    async def test_neuer_auftrag_nach_dem_ende_moeglich(self):
        job = JobRunner()
        job.start("a", [["true"]])
        await job.wait()

        job.start("b", [["printf", "x"]])
        await job.wait()

        self.assertEqual(job.status()["name"], "b")

    async def test_ausgabe_ab_zeile(self):
        job = JobRunner()
        job.start("test", [["printf", "a\\nb\\nc\\n"]])
        await job.wait()

        first = job.status()
        later = job.status(since=first["naechste"] - 1)

        self.assertEqual(later["zeilen"], ["c"])

    async def test_umgebung_wird_gesetzt(self):
        job = JobRunner()
        job.start("test", [["sh", "-c", "printf $MEINE_VARIABLE"]], {"MEINE_VARIABLE": "wert"})
        await job.wait()

        self.assertIn("wert", job.status()["zeilen"])

    def test_ohne_auftrag(self):
        status = JobRunner().status()

        self.assertFalse(status["laeuft"])
        self.assertIsNone(status["erfolg"])


if __name__ == "__main__":
    unittest.main()
