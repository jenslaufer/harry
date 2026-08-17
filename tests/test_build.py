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
import unittest.mock
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


class TestReiseAbschnitt(unittest.TestCase):
    """Der Abschnitt ueber die laufende Reise ist der einzige Beleg auf dieser
    Seite, den ein Leser anklicken und selbst nachlesen kann. Alles andere ist
    Innenansicht. Deshalb: keine Zahl ohne Messung, und kein Abschnitt ohne Zahl.
    """

    REISE = {"gemessen": 5, "median_minuten": 66, "juengste_minuten": 17,
             "schnellste_minuten": 17}

    def test_abschnitt_nennt_die_gemessenen_zahlen(self):
        html = build._reise_abschnitt(self.REISE)
        self.assertIn("5", html)
        self.assertIn("66", html)
        self.assertIn("17", html)

    def test_abschnitt_verlinkt_die_reise_seite(self):
        self.assertIn("/malaysia/", build._reise_abschnitt(self.REISE))

    def test_ohne_messung_faellt_der_abschnitt_weg(self):
        # Nach dem 07.09. gibt es keine Reise mehr. Eine Seite, die eine
        # laufende Reise behauptet, die vorbei ist, ist schlechter als eine ohne
        # den Abschnitt — deshalb faellt er weg statt einzufrieren.
        self.assertEqual(build._reise_abschnitt(None), "")
        self.assertEqual(build._reise_abschnitt({}), "")

    def test_reise_ist_kein_pflichtfeld(self):
        # Faellt die Messung aus, muss die uebrige Seite trotzdem bauen.
        self.assertNotIn("reise", build.PFLICHTFELDER)

    def test_platzhalter_wird_ersetzt_wenn_keine_reise_laeuft(self):
        # Der haessliche Fall: {{REISE}} bleibt als Text auf der Seite stehen.
        zahlen = dict(ZAHLEN_BEISPIEL)
        zahlen["reise"] = None
        self.assertNotIn("{{REISE}}", build.rendere(zahlen))

    def test_abschnitt_steht_auf_der_seite_wenn_eine_reise_laeuft(self):
        zahlen = dict(ZAHLEN_BEISPIEL)
        zahlen["reise"] = self.REISE
        seite = build.rendere(zahlen)
        self.assertIn("/malaysia/", seite)

    def test_messung_ohne_gemessene_meldung_gibt_None(self):
        # 0 gemessene Meldungen heisst "noch nichts passiert", nicht "0 Minuten".
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            datei = Path(d) / "m.json"
            datei.write_text(_json.dumps({"gemessen": 0, "median_minuten": None}))
            alt = build.REISE_MESSUNG
            build.REISE_MESSUNG = datei
            try:
                self.assertIsNone(build._lies_reise())
            finally:
                build.REISE_MESSUNG = alt

    def test_fehlende_messdatei_ist_kein_absturz(self):
        alt = build.REISE_MESSUNG
        build.REISE_MESSUNG = Path("/gibt/es/nicht.json")
        try:
            self.assertIsNone(build._lies_reise())
        finally:
            build.REISE_MESSUNG = alt


class TestKonditionen(unittest.TestCase):
    """Der Tagessatz auf dieser Seite wird gelesen, nicht getippt.

    Jens hat drei eigene Flaechen mit drei verschiedenen Saetzen (Lebenslauf
    2.000, freelancermap 800, Markt 640 — am 15.08. von ihm selbst gemessen).
    Eine vierte getippte Zahl waere die vierte Wahrheit. Deshalb liest der Build
    dieselbe Datei, aus der auch der Lebenslauf baut: aendert Jens sie, bewegen
    sich beide Seiten. Fehlt sie, steht hier KEIN Satz — eine erfundene Zahl auf
    einer Angebotsseite ist der teuerste Fehler, den diese Seite machen kann.
    """

    CSV = "field,value\nTagessatz,2.000 €/Tag (netto)\nAnteil Remote,95 %\n" \
          "Verfügbarkeit,ab 15.09.2026\nEinsatzort,weltweit\n"

    @contextlib.contextmanager
    def datei(self, inhalt):
        with tempfile.TemporaryDirectory() as d:
            pfad = Path(d) / "konditionen.csv"
            pfad.write_text(inhalt, encoding="utf-8")
            alt = build.KONDITIONEN
            build.KONDITIONEN = pfad
            try:
                yield pfad
            finally:
                build.KONDITIONEN = alt

    def test_liest_die_felder_aus_der_lebenslauf_datei(self):
        with self.datei(self.CSV):
            k = build._lies_konditionen()
        self.assertEqual(k["tagessatz"], "2.000 €/Tag (netto)")
        self.assertEqual(k["verfuegbar"], "ab 15.09.2026")
        self.assertEqual(k["remote"], "95 %")
        self.assertEqual(k["einsatzort"], "weltweit")

    def test_fehlende_datei_gibt_None_statt_erfundener_werte(self):
        alt = build.KONDITIONEN
        build.KONDITIONEN = Path("/gibt/es/nicht.csv")
        try:
            self.assertIsNone(build._lies_konditionen())
        finally:
            build.KONDITIONEN = alt

    def test_datei_ohne_tagessatz_gibt_None(self):
        # Halb gelesen ist hier schlimmer als gar nicht: der Abschnitt wuerde
        # sonst eine Verfuegbarkeit ohne Preis behaupten.
        with self.datei("field,value\nEinsatzort,weltweit\n"):
            self.assertIsNone(build._lies_konditionen())

    def test_abschnitt_ohne_konditionen_nennt_keinen_preis(self):
        html = build._buchen_abschnitt(None)
        self.assertNotIn("€", html)
        self.assertNotIn("Tagessatz", html)
        # Der Abschnitt selbst bleibt: wer ihn liest, soll trotzdem wissen,
        # was Jens macht und wie man ihn erreicht.
        self.assertIn("linkedin.com/in/jenslaufer", html)

    def test_abschnitt_mit_konditionen_nennt_satz_und_verfuegbarkeit(self):
        with self.datei(self.CSV):
            html = build._buchen_abschnitt(build._lies_konditionen())
        self.assertIn("2.000", html)
        self.assertIn("15.09.2026", html)

    def test_abschnitt_nennt_beide_rollen(self):
        html = build._buchen_abschnitt(None)
        self.assertIn("Forward Deployed Engineer", html)
        self.assertIn("Harness Engineer", html)

    def test_abschnitt_verlinkt_lebenslauf_und_linkedin(self):
        html = build._buchen_abschnitt(None)
        self.assertIn("cv.jenslaufer.com", html)
        self.assertIn("linkedin.com/in/jenslaufer", html)

    def test_konditionen_sind_kein_pflichtfeld(self):
        self.assertNotIn("konditionen", build.PFLICHTFELDER)

    def test_platzhalter_wird_auch_ohne_konditionen_ersetzt(self):
        zahlen = dict(ZAHLEN_BEISPIEL)
        zahlen["konditionen"] = None
        self.assertNotIn("{{BUCHEN}}", build.rendere(zahlen))


class TestPositionierung(unittest.TestCase):
    """Die Seite soll Auftraege bringen. Dann muss sie sagen, wofuer.

    Auftrag Jens 17.08. 07:35: „Sorge dafuer, dass die Leute Schlange stehen um
    mich als Harness Engineer bzw FDE zu buchen." Eine Seite, auf der die Rolle
    nicht steht, kann das nicht — auch wenn alles andere daran stimmt.
    """

    def seite(self):
        zahlen = dict(ZAHLEN_BEISPIEL)
        zahlen["reise"] = {"gemessen": 5, "median_minuten": 66,
                           "juengste_minuten": 17, "schnellste_minuten": 17}
        zahlen["konditionen"] = {"tagessatz": "2.000 €/Tag (netto)",
                                 "verfuegbar": "ab 15.09.2026",
                                 "remote": "95 %", "einsatzort": "weltweit"}
        return build.rendere(zahlen)

    def test_rollen_stehen_auf_der_seite(self):
        seite = self.seite()
        self.assertIn("Forward Deployed Engineer", seite)
        self.assertIn("Harness Engineer", seite)

    def test_name_steht_in_titel_oder_beschreibung(self):
        kopf = self.seite().split("</head>")[0]
        self.assertIn("Jens Laufer", kopf)

    def test_vorschau_und_titel_nennen_die_rolle(self):
        # Was geteilt wird, ist der Vorschautext — nicht der Fliesstext.
        kopf = self.seite().split("</head>")[0]
        self.assertTrue(
            "Harness" in kopf or "Forward Deployed" in kopf,
            "weder Titel noch og:description nennen die Rolle",
        )

    def test_die_vier_interessen_stehen_da(self):
        # Sie sind der Grund, warum der Aufbau so aussieht, nicht Dekoration.
        seite = self.seite()
        for wort in ("Komplex", "Skalier", "Zufall", "Ungewissheit"):
            self.assertIn(wort, seite, f"fehlt auf der Seite: {wort}")

    def test_verfuegbarkeit_steht_nicht_zweimal_verschieden(self):
        # Zwei Daten auf einer Seite sind schlimmer als keins.
        treffer = set(re.findall(r"ab \d\d\.\d\d\.20\d\d", self.seite()))
        self.assertLessEqual(len(treffer), 1, f"widersprechende Angaben: {treffer}")


class TestEchteUmlaute(unittest.TestCase):
    """Der ausgelieferte Text traegt echte Umlaute, nie die ASCII-Umschrift.

    Der Fehler entsteht im Python-Quelltext, wo die Umschrift Gewohnheit ist,
    und wandert von dort auf eine deutsche Seite, die als Arbeitsprobe dient.
    Am 17.08. genau so im Werkstatt-Band der Schwester-Seite passiert und erst
    beim Rendern aufgefallen. Geprueft wird der SICHTBARE Text — ein Kommentar
    im Stylesheet liest niemand, und ein Test, der am falschen Ort misst, wird
    abgeschaltet statt befolgt.
    """

    UMSCHRIFT = [
        "prueft", "traegt", "laeuft", "veroeffentlich", "geschaetzt",
        "waehrend", "fuer ", "ueber ", "koennen", "muessen", "naechste",
        "gepruef", "haelt", "faehrt", "gehoert",
    ]

    @staticmethod
    def sichtbar(seite: str) -> str:
        ohne = re.sub(r"<(style|script)\b.*?</\1>", " ", seite, flags=re.S | re.I)
        ohne = re.sub(r"<!--.*?-->", " ", ohne, flags=re.S)
        return re.sub(r"<[^>]+>", " ", ohne).lower()

    def test_reise_abschnitt_hat_keine_ascii_umschrift(self):
        klein = self.sichtbar(build._reise_abschnitt(
            {"gemessen": 5, "median_minuten": 66, "juengste_minuten": 17,
             "schnellste_minuten": 17}
        ))
        for wort in self.UMSCHRIFT:
            self.assertNotIn(wort, klein, f"ASCII-Umschrift im Abschnitt: {wort!r}")

    def test_ausgelieferte_seite_hat_keine_ascii_umschrift(self):
        if not build.ZIEL.exists():
            self.skipTest("index.html noch nicht gebaut")
        klein = self.sichtbar(build.ZIEL.read_text(encoding="utf-8"))
        for wort in self.UMSCHRIFT:
            self.assertNotIn(wort, klein, f"ASCII-Umschrift auf der Seite: {wort!r}")



class TestZweisprachig(unittest.TestCase):
    """Deutsch UND Englisch aus EINER Messung.

    Die Regel dahinter: zwei Sprachfassungen sind zwei Leser derselben Zahlen,
    nie zwei Rechnungen. Sobald die englische Seite eine eigene Messung haette,
    stuenden nach einer Woche zwei verschiedene Wahrheiten im Netz.
    """

    # Woerter, die es auf der englischen Seite nicht geben darf. Der Test misst
    # die ABWESENHEIT des alten Zustands, nicht die Anwesenheit des neuen: ein
    # vergessener Abschnitt faellt sonst nicht auf, weil die Seite trotzdem
    # rendert und englisch aussieht.
    DEUTSCH = [
        r"\bund\b", r"\bnicht\b", r"\bwird\b", r"\bsind\b", r"\bjede\b",
        r"\bkeine\b", r"\bich\b", r"sitzungen", r"nachrichten", r"tagessatz",
        r"weckzeiten", r"auftr", r"werkzeuge",
    ]

    @staticmethod
    def sichtbar(seite: str) -> str:
        ohne = re.sub(r"<(style|script)\b.*?</\1>", " ", seite, flags=re.S | re.I)
        ohne = re.sub(r"<!--.*?-->", " ", ohne, flags=re.S)
        return re.sub(r"<[^>]+>", " ", ohne).lower()

    def test_zahlformat_folgt_der_sprache(self):
        self.assertEqual(build.zahl(2167), "2.167")
        self.assertEqual(build.zahl(2167, "en"), "2,167")

    def test_datum_folgt_der_sprache(self):
        self.assertEqual(build._datum("2026-08-17"), "17.08.2026")
        self.assertEqual(build._datum("2026-08-17", "en"), "17 August 2026")

    def test_englische_seite_rendert_vollstaendig(self):
        seite = build.rendere(ZAHLEN_BEISPIEL, "en")
        self.assertIn('lang="en"', seite)
        self.assertNotIn("{{", seite)

    def test_deutsche_seite_bleibt_deutsch(self):
        seite = build.rendere(ZAHLEN_BEISPIEL)
        self.assertIn('lang="de"', seite)
        self.assertNotIn("{{", seite)

    def test_englische_seite_hat_keine_deutschen_reste(self):
        klein = self.sichtbar(build.rendere(ZAHLEN_BEISPIEL, "en"))
        for muster in self.DEUTSCH:
            self.assertIsNone(
                re.search(muster, klein),
                f"deutscher Rest auf der englischen Seite: {muster}",
            )

    def test_beide_seiten_tragen_dieselben_zahlen(self):
        de = build.rendere(ZAHLEN_BEISPIEL)
        en = build.rendere(ZAHLEN_BEISPIEL, "en")
        # Dieselbe Messung, nur anders geschrieben: 2.167 hier, 2,167 dort.
        self.assertIn(build.zahl(ZAHLEN_BEISPIEL["sitzungen"]), de)
        self.assertIn(build.zahl(ZAHLEN_BEISPIEL["sitzungen"], "en"), en)
        self.assertNotIn(build.zahl(ZAHLEN_BEISPIEL["sitzungen"]), en)

    def test_jede_seite_zeigt_auf_die_andere(self):
        basis = ZAHLEN_BEISPIEL.get("basis") or build.BASIS
        de = build.rendere(ZAHLEN_BEISPIEL)
        en = build.rendere(ZAHLEN_BEISPIEL, "en")
        self.assertIn(f'hreflang="en" href="{basis}en/"', de)
        self.assertIn(f'hreflang="de" href="{basis}"', en)
        self.assertIn(f'rel="canonical" href="{basis}"', de)
        self.assertIn(f'rel="canonical" href="{basis}en/"', en)

    def test_jede_fassung_verlinkt_den_lebenslauf_ihrer_sprache(self):
        """Der Knopf auf der englischen Seite darf nicht deutsch landen.

        Gemeldet von Jens am 17.08. 08:25: „Der Link des englischen harry geht
        auf deutschen Cv." Eine Arbeitsprobe, die ihren eigenen Leser in die
        falsche Sprache schickt, widerlegt genau das, wofuer sie da ist.
        """
        de = build.rendere(ZAHLEN_BEISPIEL)
        en = build.rendere(ZAHLEN_BEISPIEL, "en")
        self.assertIn(f'href="{build.LEBENSLAUF_EN}"', en)
        self.assertNotIn(f'href="{build.LEBENSLAUF}"', en)
        self.assertIn(f'href="{build.LEBENSLAUF}"', de)
        self.assertNotIn(f'href="{build.LEBENSLAUF_EN}"', de)

    def test_ohne_englischen_lebenslauf_nennt_der_knopf_die_sprache(self):
        """Faellt der Knopf auf die deutsche Fassung zurueck, sagt er es.

        Dieselbe Bauart wie REISE_SEITE_EN: lieber ein ehrlicher Hinweis als
        ein stiller Sprachwechsel — und lieber gar kein Link als ein 404.
        """
        with unittest.mock.patch.object(build, "LEBENSLAUF_EN", None):
            en = build.rendere(ZAHLEN_BEISPIEL, "en")
        self.assertIn(f'href="{build.LEBENSLAUF}"', en)
        self.assertIn("in German", en)

    def test_englische_vorschau_zeigt_auf_das_englische_bild(self):
        en = build.rendere(ZAHLEN_BEISPIEL, "en")
        basis = ZAHLEN_BEISPIEL.get("basis") or build.BASIS
        self.assertIn(f'og:image" content="{basis}en/og.png"', en)

    def test_schreibe_legt_beide_fassungen_an(self):
        with tempfile.TemporaryDirectory() as ordner:
            ziel = Path(ordner) / "index.html"
            with sperrliste("nichts-davon"):
                build.schreibe(ZAHLEN_BEISPIEL, ziel=ziel,
                               zahlen_ziel=Path(ordner) / "zahlen.json")
            self.assertTrue(ziel.exists())
            englisch = Path(ordner) / "en" / "index.html"
            self.assertTrue(englisch.exists(), "en/index.html fehlt")
            self.assertIn('lang="en"', englisch.read_text(encoding="utf-8"))

    def test_privatpruefung_gilt_auch_englisch(self):
        # Die Sperrliste greift auf BEIDEN Seiten. Eine Fassung, die nicht
        # geprueft wird, ist das Leck.
        with tempfile.TemporaryDirectory() as ordner:
            with sperrliste("karlstein"):
                with self.assertRaises(build.PrivatException):
                    build.schreibe(ZAHLEN_BEISPIEL,
                                   ziel=Path(ordner) / "index.html",
                                   zahlen_ziel=Path(ordner) / "zahlen.json")

    def test_englische_konditionen_erfinden_nichts(self):
        roh = {"tagessatz": "2.000 €/Tag (netto)", "verfuegbar": "ab 15.09.2026",
               "remote": "95 %", "einsatzort": "weltweit"}
        block = build._buchen_abschnitt(roh, "en")
        self.assertIn("2,000", block)
        self.assertIn("15 September 2026", block)
        self.assertIn("worldwide", block)
        # Unbekannte Schreibweise: lieber der Originalwert als eine Erfindung.
        fremd = build._buchen_abschnitt(
            {"tagessatz": "nach Absprache", "verfuegbar": "sofort",
             "remote": "", "einsatzort": ""}, "en")
        self.assertIn("nach Absprache", fremd)
        self.assertIn("sofort", fremd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
