import unittest

from server_terminal import totp

# Testwerte aus RFC 6238 (SHA-1, Geheimnis "12345678901234567890"), auf 6 Stellen gekürzt
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class TotpTest(unittest.TestCase):
    def test_rfc_6238_testwerte(self):
        for timestamp, expected in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")]:
            self.assertEqual(totp.code_at(SECRET, totp.current_step(timestamp)), expected, timestamp)

    def test_neues_geheimnis_ist_gueltig_und_zufaellig(self):
        first, second = totp.new_secret(), totp.new_secret()

        self.assertNotEqual(first, second)
        self.assertEqual(len(totp.code_at(first, 1)), 6)

    def test_richtiger_code_wird_akzeptiert(self):
        self.assertEqual(totp.verify(SECRET, "287082", 59), 1)

    def test_leerzeichen_im_code_sind_egal(self):
        self.assertEqual(totp.verify(SECRET, "287 082", 59), 1)

    def test_toleranz_von_einem_schritt(self):
        self.assertEqual(totp.verify(SECRET, "287082", 59 + 30), 1)
        self.assertEqual(totp.verify(SECRET, "287082", 59 - 30), 1)

    def test_zu_alter_code_wird_abgelehnt(self):
        self.assertIsNone(totp.verify(SECRET, "287082", 59 + 120))

    def test_falsche_und_kaputte_codes(self):
        for code in ("000000", "28708", "2870820", "abcdef", ""):
            self.assertIsNone(totp.verify(SECRET, code, 59), code)

    def test_code_gilt_nur_einmal(self):
        step = totp.verify(SECRET, "287082", 59)

        self.assertIsNone(totp.verify(SECRET, "287082", 59, last_used_step=step))

    def test_ein_spaeterer_code_gilt_nach_einem_frueheren(self):
        self.assertEqual(totp.verify(SECRET, totp.code_at(SECRET, 3), 3 * 30 + 5, last_used_step=2), 3)

    def test_uri_fuer_authenticator_apps(self):
        uri = totp.provisioning_uri("ABC", "jason", "Server")

        self.assertTrue(uri.startswith("otpauth://totp/Server:jason?secret=ABC"))
        self.assertIn("digits=6", uri)
        self.assertIn("period=30", uri)


if __name__ == "__main__":
    unittest.main()
