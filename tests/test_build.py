#!/usr/bin/env python3
"""Tests fuer den Build von harry.jenslaufer.com.

Aufruf: python3 tests/test_build.py

Die Tests laufen ohne Netz und ohne die Repos von Jens: gemessene Zahlen
kommen im Test aus einer festen Datei, nicht aus git. Genau diese Trennung
ist der Zweck von `--no-measure`.
"""

import contextlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build  # noqa: E402


@contextlib.contextmanager
def sperrliste(*woerter):
    """Setzt fuer die Dauer des Blocks eine eigene Sperrliste."""
    vorher = build.GESPERRT
    build.GESPERRT = [w.lower() for w in woerter]
    try:
        yield
    finally:
        build.GESPERRT = vorher


ZAHLEN_BEISPIEL = {
    "nachrichten": 2167,
    "sitzungen": 2155,
    "commits_assistant": 4993,
    "auftraege": 726,
    "prs": 955,
    "pr_repos": 30,
    "koautor_commits": 1810,
    "koautor_repos": 68,
    "tage": 150,
    "werkzeuge": 93,
    "testfunktionen": 190,
    "codezeilen": 18294,
    "units": 14,
    "skills": 118,
    "weckzeiten_werktag": [22, 23, 1, 2, 4, 5, 7, 11, 15, 19],
    "weckzeiten_wochenende": [9, 13, 17],
    "cpu": "AMD Ryzen 5 3500U",
    "kerne": 8,
    "ram_gb": 12,
    "start": "2026-03-20",
    "stand": "2026-08-17",
}


class TestDatenschutz(unittest.TestCase):
    """Nichts Privates darf die Seite erreichen. Im Zweifel gar nicht bauen."""

    def test_iban_bricht_ab(self):
        with self.assertRaises(build.PrivatException):
            build.pruefe_privat("Kontonummer DE57 3701 0050 0000 3995 09 steht hier")

    def test_mailadresse_bricht_ab(self):
        with self.assertRaises(build.PrivatException):
            build.pruefe_privat("Schreib an jens.laufer@solytics.de wenn du magst")

    def test_telefonnummer_bricht_ab(self):
        with self.assertRaises(build.PrivatException):
            build.pruefe_privat("Ruf an unter +49 172 8443048")

    def test_passnummer_bricht_ab(self):
        with self.assertRaises(build.PrivatException):
            build.pruefe_privat("Reisepass C01X00T47 liegt bereit")

    def test_gesperrtes_wort_bricht_ab(self):
        """Namen und Geldbetraege faengt kein Muster — dafuer gibt es die Liste."""
        with sperrliste("Beispielname", "123456,78 €"):
            with self.assertRaises(build.PrivatException):
                build.pruefe_privat("Beiläufig erwähnt: Beispielname")

    def test_sperrliste_ignoriert_gross_klein(self):
        with sperrliste("Beispielname"):
            with self.assertRaises(build.PrivatException):
                build.pruefe_privat("BEISPIELNAME")


class TestSperrliste(unittest.TestCase):
    """Die Liste der gesperrten Woerter darf nicht in diesem Repo stehen: es ist
    oeffentlich, und eine Sperrliste ist eine Liste genau der Woerter, die
    niemand sehen soll. Sie liegt im privaten Assistenz-Repo."""

    def test_liste_liegt_ausserhalb_dieses_repos(self):
        self.assertNotIn(str(build.WURZEL), str(build.SPERRLISTE))

    def test_fehlende_liste_bricht_ab_statt_stillschweigend_durchzulassen(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(build.PrivatException):
                build.lade_sperrliste(Path(tmp) / "gibt-es-nicht.txt")

    def test_leere_liste_bricht_ab(self):
        """Eine leere Datei ist derselbe Fall wie eine fehlende: der Schutz laeuft nicht."""
        with tempfile.TemporaryDirectory() as tmp:
            leer = Path(tmp) / "leer.txt"
            leer.write_text("# nur ein Kommentar\n\n", encoding="utf-8")
            with self.assertRaises(build.PrivatException):
                build.lade_sperrliste(leer)

    def test_kommentare_und_leerzeilen_zaehlen_nicht_als_eintrag(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / "liste.txt"
            datei.write_text("# Kommentar\n\nEinWort\n", encoding="utf-8")
            self.assertEqual(build.lade_sperrliste(datei), ["einwort"])

    def test_echte_liste_ist_vorhanden_und_gefuellt(self):
        """Auf dieser Maschine muss der Schutz wirklich scharf sein."""
        if not build.SPERRLISTE.exists():
            self.skipTest("privates Assistenz-Repo nicht vorhanden")
        self.assertGreaterEqual(len(build.lade_sperrliste(build.SPERRLISTE)), 5)

    def test_gegenprobe_uhrzeit_und_zahlen(self):
        """Fehlalarme kosten Vertrauen: normale Seitenzahlen muessen durchgehen."""
        build.pruefe_privat(
            "Werktags 10 Weckzeiten, 22:00 UTC, 2.167 Nachrichten, "
            "12 GB Speicher, 8 Kerne, Version 2.0.4, Stand 17.08.2026."
        )

    def test_gegenprobe_oeffentliche_adressen(self):
        """Die eigenen Domains sind oeffentlich und muessen erlaubt bleiben."""
        build.pruefe_privat("Mehr unter jenslaufer.com und cv.jenslaufer.com")


class TestZahlen(unittest.TestCase):
    def test_alle_pflichtfelder_vorhanden(self):
        fehlend = [k for k in build.PFLICHTFELDER if k not in ZAHLEN_BEISPIEL]
        self.assertEqual(fehlend, [], f"Beispieldaten unvollstaendig: {fehlend}")

    def test_fehlendes_feld_faellt_auf(self):
        unvollstaendig = dict(ZAHLEN_BEISPIEL)
        del unvollstaendig["nachrichten"]
        with self.assertRaises(build.ZahlenException):
            build.pruefe_zahlen(unvollstaendig)

    def test_null_ist_kein_messwert(self):
        """0 heisst hier fast immer: die Messung lief nicht, nicht 'es gab nichts'."""
        kaputt = dict(ZAHLEN_BEISPIEL, nachrichten=0)
        with self.assertRaises(build.ZahlenException):
            build.pruefe_zahlen(kaputt)

    def test_deutsche_tausenderpunkte(self):
        self.assertEqual(build.zahl(2167), "2.167")
        self.assertEqual(build.zahl(955), "955")
        self.assertEqual(build.zahl(18294), "18.294")


class TestRendern(unittest.TestCase):
    def setUp(self):
        self.html = build.rendere(ZAHLEN_BEISPIEL)

    def test_keine_platzhalter_uebrig(self):
        """Ein nicht ersetzter Platzhalter steht sonst sichtbar auf der Seite."""
        rest = re.findall(r"\{\{[A-Z_]+\}\}", self.html)
        self.assertEqual(rest, [], f"nicht ersetzt: {rest}")

    def test_zahlen_stehen_drin(self):
        for wert in ("2.167", "2.155", "726", "1.810", "18.294"):
            self.assertIn(wert, self.html, f"{wert} fehlt in der Seite")

    def test_html_kommentare_erreichen_die_seite_nicht(self):
        """Kommentare in der Vorlage sind Notizen fuer uns, nicht fuer Leser."""
        self.assertNotIn("<!--", self.html)

    def test_seite_ist_deutsch_ausgezeichnet(self):
        self.assertIn('<html lang="de">', self.html)

    def test_titel_und_beschreibung_gesetzt(self):
        self.assertRegex(self.html, r"<title>[^<]{10,}</title>")
        self.assertRegex(self.html, r'<meta name="description" content="[^"]{40,}"')

    def test_css_ist_eingebettet(self):
        """Eine Datei weniger heisst: die Seite kann nicht halb ausgeliefert werden."""
        self.assertIn("<style>", self.html)
        self.assertNotIn('rel="stylesheet" href="site.css"', self.html)

    def test_weckzeiten_sind_markiert(self):
        """Der Zeitstreifen muss genau so viele aktive Stunden haben wie der Plan."""
        aktive = self.html.count('class="stunde an"')
        self.assertEqual(aktive, len(ZAHLEN_BEISPIEL["weckzeiten_werktag"]))

    def test_datenschutzpruefung_laeuft_ueber_die_fertige_seite(self):
        build.pruefe_privat(self.html)

    def test_vorschaubild_ist_verlinkt_und_vorhanden(self):
        """Ein og:image-Tag ohne Datei ergibt auf LinkedIn eine leere Karte —
        und genau dort soll die Seite geteilt werden."""
        self.assertIn('property="og:image"', self.html)
        self.assertTrue((build.WURZEL / "og.png").exists(), "og.png fehlt: python3 build.py --og")

    def test_adressen_sind_absolut_und_gleich(self):
        kanonisch = re.search(r'rel="canonical" href="([^"]+)"', self.html).group(1)
        og = re.search(r'property="og:url" content="([^"]+)"', self.html).group(1)
        self.assertTrue(kanonisch.startswith("https://"))
        self.assertEqual(kanonisch, og)
        self.assertTrue(kanonisch.endswith("/"), "Basis muss auf / enden, sonst bricht og:image")

    def test_stand_steht_auf_der_seite(self):
        self.assertIn("17.08.2026", self.html)


class TestSchreiben(unittest.TestCase):
    def test_bei_privatem_inhalt_wird_nichts_geschrieben(self):
        """Der teure Fall: die Datei liegt schon, der Build kippt, und die alte
        Fassung bleibt stehen — statt einer halben neuen mit Privatem drin."""
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "index.html"
            ziel.write_text("alte fassung", encoding="utf-8")
            kaputt = dict(ZAHLEN_BEISPIEL, cpu="CPU von Beispielname")
            with sperrliste("Beispielname"), self.assertRaises(build.PrivatException):
                build.schreibe(kaputt, ziel)
            self.assertEqual(ziel.read_text(encoding="utf-8"), "alte fassung")

    def test_zahlen_werden_als_json_mitgeschrieben(self):
        """Die JSON ist der Prueffaden: in der git-Historie sieht man, wann
        welche Zahl gemessen wurde. Ohne sie ist jede Zahl auf der Seite
        eine Behauptung."""
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "index.html"
            zahlen = Path(tmp) / "zahlen.json"
            build.schreibe(ZAHLEN_BEISPIEL, ziel, zahlen_ziel=zahlen)
            self.assertTrue(ziel.exists())
            gelesen = json.loads(zahlen.read_text(encoding="utf-8"))
            self.assertEqual(gelesen["nachrichten"], 2167)


class TestMessen(unittest.TestCase):
    """Die Messfunktionen selbst — sie laufen gegen echte Repos und sind
    deshalb tolerant: fehlt ein Repo, ist das Ergebnis None, nie 0."""

    def test_fehlendes_repo_gibt_none_statt_null(self):
        self.assertIsNone(build.git_zaehle(Path("/gibt/es/nicht"), ["rev-list", "--count", "HEAD"]))

    def test_none_faellt_in_der_pruefung_auf(self):
        kaputt = dict(ZAHLEN_BEISPIEL, sitzungen=None)
        with self.assertRaises(build.ZahlenException):
            build.pruefe_zahlen(kaputt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
