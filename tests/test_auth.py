import stat
import tempfile
import unittest
from pathlib import Path

from server_terminal import totp
from server_terminal.auth import ELEVATED_SECONDS, SESSION_SECONDS, AccountStore, Lockout, new_setup_code

PASSWORD = "ein-sehr-langes-passwort"


class AccountStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "daten" / "account.json"
        self.store = AccountStore(self.path)
        self.secret = totp.new_secret()

    def create(self):
        self.store.create("jason", PASSWORD, self.secret)

    def test_ohne_konto(self):
        self.assertFalse(self.store.exists())
        self.assertFalse(self.store.verify_password("jason", PASSWORD))
        self.assertIsNone(self.store.read_session("a.b", 1))

    def test_konto_anlegen_und_passwort_pruefen(self):
        self.create()

        self.assertTrue(self.store.verify_password("jason", PASSWORD))
        self.assertTrue(self.store.verify_password(" jason ", PASSWORD))
        self.assertFalse(self.store.verify_password("jason", "falsch"))
        self.assertFalse(self.store.verify_password("anderer", PASSWORD))

    def test_datei_ist_nur_fuer_den_besitzer_lesbar(self):
        self.create()

        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_passwort_steht_nicht_im_klartext_in_der_datei(self):
        self.create()

        self.assertNotIn(PASSWORD, self.path.read_text(encoding="utf-8"))

    def test_konto_wird_nicht_ueberschrieben(self):
        self.create()

        with self.assertRaises(FileExistsError):
            self.store.create("anderer", PASSWORD, self.secret)

    def test_eingabepruefung(self):
        self.assertIsNone(AccountStore.check_new("jason", PASSWORD, PASSWORD))
        self.assertIn("Benutzername", AccountStore.check_new("", PASSWORD, PASSWORD))
        self.assertIn("Benutzername", AccountStore.check_new("x" * 65, PASSWORD, PASSWORD))
        self.assertIn("mindestens 12", AccountStore.check_new("jason", "kurz", "kurz"))
        self.assertIn("nicht gleich", AccountStore.check_new("jason", PASSWORD, PASSWORD + "x"))

    def test_zwei_faktor_code_gilt_nur_einmal(self):
        self.create()
        code = totp.code_at(self.secret, totp.current_step(1_800_000_000))

        self.assertTrue(self.store.verify_totp(code, 1_800_000_000))
        self.assertFalse(self.store.verify_totp(code, 1_800_000_000))

    def test_falscher_zwei_faktor_code(self):
        self.create()

        self.assertFalse(self.store.verify_totp("000000", 1_800_000_000))

    def test_sitzung_ist_gueltig_bis_zum_ablauf(self):
        self.create()
        token = self.store.new_session(1000)

        self.assertIsNotNone(self.store.read_session(token, 1000 + SESSION_SECONDS - 1))
        self.assertIsNone(self.store.read_session(token, 1000 + SESSION_SECONDS + 1))

    def test_erhoehte_stufe_laeuft_frueher_ab(self):
        self.create()
        session = self.store.read_session(self.store.new_session(1000, elevated=True), 1000)

        self.assertEqual(session["elev"], 1000 + ELEVATED_SECONDS)
        self.assertEqual(self.store.read_session(self.store.new_session(1000), 1000)["elev"], 0)

    def test_veraenderte_sitzung_ist_ungueltig(self):
        self.create()
        body, _, signature = self.store.new_session(1000).partition(".")
        other = self.store._sign({"exp": 99999999999, "elev": 99999999999})

        for token in (f"{body}x.{signature}", f"{body}.{'0' * 64}", other.split(".")[0] + "." + signature, "kaputt", "", "a.b"):
            self.assertIsNone(self.store.read_session(token, 1000), token)

    def test_sitzung_eines_anderen_kontos_gilt_nicht(self):
        self.create()
        other = AccountStore(self.path.with_name("zweites.json"))
        other.create("jason", PASSWORD, self.secret)

        self.assertIsNone(other.read_session(self.store.new_session(1000), 1000))

    def test_beschaedigte_datei_wird_gemeldet(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"benutzer": "jason"}', encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "account.json"):
            self.store.verify_password("jason", PASSWORD)


class LockoutTest(unittest.TestCase):
    def test_nach_fuenf_fehlern_gesperrt(self):
        lockout = Lockout(max_failures=5, seconds=600)
        for _ in range(4):
            lockout.fail("1.2.3.4", 100)
        self.assertFalse(lockout.blocked("1.2.3.4", 100))

        lockout.fail("1.2.3.4", 100)

        self.assertTrue(lockout.blocked("1.2.3.4", 101))
        self.assertFalse(lockout.blocked("5.6.7.8", 101))

    def test_sperre_laeuft_ab(self):
        lockout = Lockout(max_failures=2, seconds=600)
        lockout.fail("a", 100)
        lockout.fail("a", 100)

        self.assertTrue(lockout.blocked("a", 699))
        self.assertFalse(lockout.blocked("a", 701))

    def test_erfolg_setzt_die_adresse_zurueck(self):
        lockout = Lockout(max_failures=2, seconds=600)
        lockout.fail("a", 100)
        lockout.succeed("a")
        lockout.fail("a", 101)

        self.assertFalse(lockout.blocked("a", 102))

    def test_viele_adressen_loesen_die_gesamtsperre_aus(self):
        lockout = Lockout(max_failures=2, seconds=600)
        for index in range(8):
            lockout.fail(f"10.0.0.{index}", 100)

        self.assertTrue(lockout.blocked("neue-adresse", 101))


class SetupCodeTest(unittest.TestCase):
    def test_code_ist_zufaellig_und_lang_genug(self):
        self.assertNotEqual(new_setup_code(), new_setup_code())
        self.assertGreaterEqual(len(new_setup_code()), 10)


if __name__ == "__main__":
    unittest.main()
