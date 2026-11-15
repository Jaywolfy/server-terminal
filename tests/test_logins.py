import unittest

from server_terminal import logins

JOURNAL = """2026-10-23T10:00:01+00:00 ubuntu sshd-session[1]: Invalid user admin from 203.0.113.5 port 4000
2026-10-23T10:00:02+00:00 ubuntu sshd-session[1]: Failed password for invalid user admin from 203.0.113.5 port 4000 ssh2
2026-10-23T10:00:09+00:00 ubuntu sshd-session[2]: Failed password for root from 203.0.113.5 port 4001 ssh2
2026-10-23T10:05:00+00:00 ubuntu sshd-session[3]: Failed password for root from 198.51.100.7 port 5000 ssh2
2026-10-23T10:06:00+00:00 ubuntu sshd-session[4]: Invalid user oracle from 198.51.100.7 port 5001
2026-10-23T11:00:00+00:00 ubuntu sshd-session[5]: Accepted publickey for root from 192.0.2.44 port 54764 ssh2
2026-10-23T11:00:01+00:00 ubuntu sshd-session[5]: pam_unix(sshd:session): session opened for user root(uid=0) by root(uid=0)
2026-10-23T11:01:00+00:00 ubuntu sshd-session[6]: Connection closed by authenticating user root 203.0.113.5 port 4002 [preauth]
"""
GEO = {
    "203.0.113.5": {"land": "Deutschland", "flagge": "🇩🇪", "stadt": "Berlin", "anbieter": "Beispiel AG"},
    "198.51.100.7": {"land": "Frankreich", "flagge": "🇫🇷", "stadt": "Paris", "anbieter": "Exemple"},
}


class ParseTest(unittest.TestCase):
    def test_erkennt_fehlgeschlagene_und_erfolgreiche(self):
        events = logins.parse_events(JOURNAL)

        self.assertEqual([e["art"] for e in events], ["fehlgeschlagen"] * 5 + ["erfolgreich"])

    def test_felder(self):
        events = logins.parse_events(JOURNAL)

        self.assertEqual((events[1]["user"], events[1]["ip"], events[1]["method"]), ("admin", "203.0.113.5", "password"))
        self.assertEqual(events[0]["method"], "unbekannter Benutzer")
        accepted = events[-1]
        self.assertEqual((accepted["user"], accepted["ip"], accepted["method"]), ("root", "192.0.2.44", "publickey"))

    def test_zeitstempel_sind_iso(self):
        self.assertEqual(logins.parse_events(JOURNAL)[0]["ts"], "2026-10-23T10:00:01+00:00")

    def test_sitzungszeilen_und_abbrueche_werden_ignoriert(self):
        self.assertEqual(len(logins.parse_events(JOURNAL)), 6)

    def test_leere_ausgabe(self):
        self.assertEqual(logins.parse_events(""), [])


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.summary = logins.summarize(logins.parse_events(JOURNAL), GEO)

    def test_zaehler(self):
        self.assertEqual((self.summary["fehlgeschlagen"], self.summary["erfolgreich"], self.summary["ips"]), (5, 1, 2))

    def test_haeufigste_adressen_mit_herkunft(self):
        top = self.summary["haeufigste"]

        self.assertEqual(top[0]["ip"], "203.0.113.5")
        self.assertEqual(top[0]["anzahl"], 3)
        self.assertEqual((top[0]["land"], top[0]["stadt"], top[0]["flagge"]), ("Deutschland", "Berlin", "🇩🇪"))
        self.assertEqual(set(top[0]["benutzer"]), {"admin", "root"})
        self.assertEqual(top[0]["zuletzt"], "2026-10-23T10:00:09+00:00")

    def test_laender(self):
        self.assertEqual(self.summary["laender"][0], {"land": "Deutschland", "anzahl": 3})

    def test_letzte_versuche_neueste_zuerst(self):
        self.assertEqual(self.summary["zuletzt"][0]["ip"], "198.51.100.7")
        self.assertEqual(self.summary["zuletzt"][0]["benutzer"], "oracle")

    def test_erfolgreiche_mit_herkunft_unbekannt(self):
        ok = self.summary["erfolgreiche"][0]

        self.assertEqual((ok["ip"], ok["land"]), ("192.0.2.44", ""))

    def test_unbekannte_herkunft_zaehlt_als_unbekannt(self):
        summary = logins.summarize(logins.parse_events(JOURNAL), {})

        self.assertEqual(summary["laender"], [{"land": "Unbekannt", "anzahl": 5}])

    def test_limits(self):
        summary = logins.summarize(logins.parse_events(JOURNAL), GEO, recent_limit=2, top_limit=1)

        self.assertEqual((len(summary["zuletzt"]), len(summary["haeufigste"])), (2, 1))


if __name__ == "__main__":
    unittest.main()
