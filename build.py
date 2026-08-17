#!/usr/bin/env python3
"""Baut harry.jenslaufer.com — die Seite ueber den Harness, den Jens gebaut hat.

Die tragende Entscheidung: **die Zahlen auf der Seite werden gemessen, nicht
getippt.** `messe()` liest sie aus der git-Historie der Repos auf dieser
Maschine, schreibt sie nach `content/zahlen.json` und rendert daraus die Seite.
Eine getippte Zahl auf einer Seite, die monatelang steht, ist nach zwei Wochen
falsch und sieht bis dahin genauso aus wie eine richtige.

`content/zahlen.json` liegt im Repo und ist damit der Prueffaden: in der
git-Historie dieser einen Datei sieht man, wann welche Zahl gemessen wurde.
Ohne Messung (`--no-measure`) baut die Seite aus dieser Datei — so laeuft der
Build auch dort, wo die Repos nicht liegen.

Aufruf:
    python3 build.py               # messen, zahlen.json + index.html schreiben
    python3 build.py --no-measure  # nur aus content/zahlen.json bauen
    python3 build.py --check       # messen und pruefen, nichts schreiben

Tests: python3 tests/test_build.py
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

WURZEL = Path(__file__).resolve().parent
CSS = WURZEL / "template" / "site.css"
ZAHLEN = WURZEL / "content" / "zahlen.json"
ZIEL = WURZEL / "index.html"

# Zwei Sprachfassungen, EINE Messung. Der englische Text ist eine zweite
# Ansicht derselben Zahlen, nie eine zweite Rechnung — sonst stehen nach einer
# Woche zwei verschiedene Wahrheiten im Netz, und keiner der beiden Leser
# erfaehrt davon. Die englische Fassung liegt unter `en/`, weil ein
# Unterordner ohne DNS, ohne Zertifikat und ohne zweiten Build auskommt.
SPRACHEN = ("de", "en")
VORLAGEN = {
    "de": WURZEL / "template" / "page.html",
    "en": WURZEL / "template" / "page.en.html",
}
OG_VORLAGEN = {
    "de": WURZEL / "template" / "og.html",
    "en": WURZEL / "template" / "og.en.html",
}
VORLAGE = VORLAGEN["de"]
OG_VORLAGE = OG_VORLAGEN["de"]

REPOS = Path.home() / "repos"
ASSISTANT = REPOS / "assistant"
AGENT_TASKS = REPOS / "agent-tasks"
SKILLS = Path.home() / ".claude" / "skills"

# Erster Commit im Assistenz-Repo. Ab hier laeuft der Aufbau.
START = date(2026, 3, 20)

# Wo die Seite ausgeliefert wird. Heute der Projektpfad, nach dem DNS-Eintrag
# die Subdomain — eine Zeile, damit og:image und canonical nicht auseinander
# laufen. Eine Vorschau, die ins Leere zeigt, ist schlimmer als keine.
BASIS = "https://jenslaufer.com/harry/"

# Die Reise-Seite ist der laufende Beleg fuer diese hier: waehrend Jens drei
# Wochen ohne Rechner unterwegs ist, entsteht sie ausschliesslich aus
# Telegram-Nachrichten. Gemessen wird das drueben in
# tools/reise-werkstatt.py; hier wird die Messung nur gelesen, damit es EINE
# Quelle gibt und nicht zwei Seiten mit zwei Zahlen.
REISE_MESSUNG = ASSISTANT / "state" / "reise-werkstatt.json"
REISE_SEITE = "https://jenslaufer.com/malaysia/"
# Erst eingetragen, als `/malaysia/en/` wirklich ausgeliefert wurde (17.08.,
# HTTP 200 gegen echtes DNS geprueft). Steht hier None, sagt die englische
# Fassung „written in German" dazu — ein Link auf eine geplante Seite ist eine
# Behauptung, ein 404 auf der Arbeitsprobe ist der teuerste Tippfehler.
REISE_SEITE_EN = "https://jenslaufer.com/malaysia/en/"

# Der Tagessatz wird gelesen, nicht getippt — aus derselben Datei, aus der auch
# cv.jenslaufer.com baut. Jens hat drei eigene Flaechen mit drei verschiedenen
# Saetzen (Lebenslauf 2.000, freelancermap 800, Markt 640; von ihm selbst am
# 15.08. gemessen). Eine vierte getippte Zahl waere die vierte Wahrheit. So
# bewegen sich Lebenslauf und diese Seite gemeinsam, wenn er sie aendert.
# Fehlt die Datei, steht hier gar kein Satz statt eines erfundenen.
KONDITIONEN = Path(
    os.environ.get("HARRY_KONDITIONEN", REPOS / "cv" / "data" / "konditionen.csv")
)
LINKEDIN = "https://www.linkedin.com/in/jenslaufer"
LEBENSLAUF = "https://cv.jenslaufer.com/"
# Dieselbe Regel wie bei REISE_SEITE_EN, und sie kam aus dem Fehler: bis zum
# 17.08. gab es die englische Fassung nur als Daten (`cv/data/en/`), gebaut und
# ausgeliefert wurde immer nur die deutsche. Der englische Knopf hier landete
# damit still auf einer deutschen Seite — von Jens gemeldet, 08:25. Steht hier
# None, nennt der Knopf die Sprache, statt den Leser zu ueberraschen.
LEBENSLAUF_EN = "https://cv.jenslaufer.com/en/"


# ---------------------------------------------------------------- Datenschutz

class PrivatException(Exception):
    """Etwas Privates haette die Seite erreicht. Es wird nichts geschrieben."""


class ZahlenException(Exception):
    """Eine Messung fehlt oder ist unglaubwuerdig. Lieber gar keine Seite."""


# Muster, die niemals oeffentlich werden duerfen. Lieber ein Fehlalarm als eine
# Passnummer im Netz — ein Fehlalarm kostet eine Minute, der andere Fall ist
# nicht ruecknehmbar.
MUSTER = [
    (r"\b[CFGHJK][0-9A-Z]{8}\b", "Passnummer (deutsches Format)"),
    (r"\b[A-Z]{2}\d{2}[ ]?(?:[0-9A-Z]{4}[ ]?){3,}[0-9A-Z]{1,4}\b", "IBAN"),
    (r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", "E-Mail-Adresse"),
    (r"\+\d[\d /()-]{7,}\d", "Telefonnummer"),
]

# Namen, Betraege und Adressen faengt kein Muster. Dafuer gibt es eine Liste —
# und die liegt bewusst NICHT in diesem Repo: es ist oeffentlich, und eine
# Sperrliste ist per Definition eine Liste genau der Woerter, die niemand sehen
# soll. Sie liegt im privaten Assistenz-Repo. Fehlt sie, bricht der Build ab;
# ein Schutz, der bei fehlender Datei stillschweigend durchlaesst, ist keiner.
SPERRLISTE = Path(
    os.environ.get("HARRY_SPERRLISTE", ASSISTANT / "state" / "oeffentlich-gesperrt.txt")
)
# Auf DIESER Seite sind auch die Namen aus der Warnliste eine harte Sperre: eine
# Seite ueber den Harness hat keinen Grund, jemanden aus der Familie zu nennen.
# Auf der Reise-Seite ist dieselbe Liste nur eine Meldung — dort geht es an
# Freunde, und wer vorkommt, entscheidet Jens.
WARNLISTE = Path(
    os.environ.get("HARRY_WARNLISTE", ASSISTANT / "state" / "oeffentlich-warnung.txt")
)

GESPERRT: list[str] = []


def lade_sperrliste(pfad: Path = None) -> list[str]:
    """Liest die Sperrliste. Fehlt oder leer -> PrivatException."""
    pfad = Path(pfad) if pfad else SPERRLISTE
    try:
        roh = pfad.read_text(encoding="utf-8")
    except OSError as fehler:
        raise PrivatException(
            f"Sperrliste nicht lesbar ({pfad}): {fehler}. "
            "Ohne sie prueft der Build nur Muster, keine Namen — das ist zu wenig."
        ) from fehler
    woerter = [
        zeile.strip().lower()
        for zeile in roh.splitlines()
        if zeile.strip() and not zeile.lstrip().startswith("#")
    ]
    if not woerter:
        raise PrivatException(f"Sperrliste ist leer ({pfad}).")
    return woerter


def pruefe_privat(text: str) -> None:
    """Wirft PrivatException, wenn etwas Personenbezogenes im Text steht."""
    klein = text.lower()
    for wort in GESPERRT:
        if wort in klein:
            raise PrivatException("gesperrtes Wort im Text (Liste ausserhalb des Repos)")
    for muster, name in MUSTER:
        treffer = re.search(muster, text)
        if treffer:
            raise PrivatException(f"{name} im Text: {treffer.group(0)}")


# ------------------------------------------------------------------- Messung

PFLICHTFELDER = [
    "nachrichten", "sitzungen", "commits_assistant", "auftraege", "prs",
    "pr_repos", "koautor_commits", "koautor_repos", "tage", "werkzeuge",
    "testfunktionen", "codezeilen", "units", "skills",
    "weckzeiten_werktag", "weckzeiten_wochenende",
    "cpu", "kerne", "ram_gb", "start", "stand",
]


def pruefe_zahlen(zahlen: dict) -> None:
    for feld in PFLICHTFELDER:
        if feld not in zahlen:
            raise ZahlenException(f"Messwert fehlt: {feld}")
        wert = zahlen[feld]
        if wert is None:
            raise ZahlenException(f"Messwert nicht ermittelt: {feld}")
        # 0 heisst bei jeder dieser Groessen: die Messung lief nicht. Es gibt
        # keine Lage, in der null Sitzungen oder null Werkzeuge stimmen.
        if isinstance(wert, int) and wert == 0:
            raise ZahlenException(f"Messwert ist 0, das ist hier immer ein Messfehler: {feld}")
        if isinstance(wert, (list, str)) and not wert:
            raise ZahlenException(f"Messwert ist leer: {feld}")


def _git(pfad: Path, argumente: list[str]) -> str | None:
    """git im Repo `pfad`. Gibt None zurueck, wenn das Repo fehlt — nie ''."""
    if not (Path(pfad) / ".git").exists():
        return None
    try:
        lauf = subprocess.run(
            ["git", "-C", str(pfad), *argumente],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if lauf.returncode != 0:
        return None
    return lauf.stdout


def git_zaehle(pfad: Path, argumente: list[str], muster: str = None) -> int | None:
    """Zaehlt Zeilen einer git-Ausgabe, optional gefiltert. Fehlt das Repo: None."""
    ausgabe = _git(pfad, argumente)
    if ausgabe is None:
        return None
    if muster is None:
        text = ausgabe.strip()
        return int(text) if text.isdigit() else len(ausgabe.splitlines())
    return sum(1 for zeile in ausgabe.splitlines() if re.search(muster, zeile))


def _lies_weckzeiten() -> tuple[list[int], list[int]]:
    """Liest `state/schedule.conf` — die Weckzeiten stehen dort in cron-Form."""
    datei = ASSISTANT / "state" / "schedule.conf"
    werktag, wochenende = [], []
    try:
        zeilen = datei.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], []
    for zeile in zeilen:
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        teile = zeile.split()
        if len(teile) < 5:
            continue
        stunde, tage = teile[1], teile[4]
        if not stunde.isdigit():
            continue
        (werktag if tage == "1-5" else wochenende).append(int(stunde))
    return sorted(set(werktag)), sorted(set(wochenende))


def _lies_hardware() -> tuple[str | None, int | None, int | None]:
    cpu = None
    try:
        for zeile in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if zeile.startswith("model name"):
                cpu = zeile.split(":", 1)[1].strip()
                # "AMD Ryzen 5 3500U with Radeon Vega Mobile Gfx" -> kurz genug
                cpu = cpu.split(" with ")[0]
                break
    except OSError:
        pass
    kerne = os.cpu_count()
    ram = None
    try:
        for zeile in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if zeile.startswith("MemTotal"):
                # Abgerundet, nicht gerundet: MemTotal ist 12,6 GiB, aufgerundet
                # staenden hier 13 GB, die niemand verbaut hat. Bei einer Zahl
                # ueber die eigene Maschine ist die kleinere die ehrliche.
                ram = int(int(zeile.split()[1]) / 1024 / 1024)
                break
    except OSError:
        pass
    return cpu, kerne, ram


def _zaehle_koautor() -> tuple[int | None, int | None]:
    """Commits, an denen eine Maschine mitgeschrieben hat, ueber alle Repos.

    Nach Remote entdoppelt: `solytics` und `solytics-website` liegen zweimal
    auf der Platte und zeigen auf dasselbe GitHub-Repo — ohne Entdopplung
    zaehlt jeder ihrer Commits doppelt.
    """
    if not REPOS.is_dir():
        return None, None
    gesehen: dict[str, int] = {}
    for ordner in sorted(REPOS.iterdir()):
        if not (ordner / ".git").exists():
            continue
        url = _git(ordner, ["remote", "get-url", "origin"])
        if not url:
            continue
        treffer = re.search(r"github\.com[:/](jenslaufer/[^/\s]+?)(?:\.git)?\s*$", url)
        if not treffer or treffer.group(1) in gesehen:
            continue
        ausgabe = _git(ordner, ["log", "--format=%b", f"--since={START.isoformat()}"]) or ""
        gesehen[treffer.group(1)] = sum(
            1 for z in ausgabe.splitlines() if "co-authored-by: claude" in z.lower()
        )
    if not gesehen:
        return None, None
    return sum(gesehen.values()), len(gesehen)


def _zaehle_dateien(ordner: Path, muster: str) -> int | None:
    if not ordner.is_dir():
        return None
    return len(list(ordner.glob(muster)))


def _zaehle_zeilen(pfade: list[Path]) -> int | None:
    summe, gefunden = 0, False
    for pfad in pfade:
        if not pfad.is_dir():
            continue
        for datei in pfad.iterdir():
            if datei.suffix in (".py", ".sh") and datei.is_file():
                gefunden = True
                summe += len(datei.read_text(encoding="utf-8", errors="replace").splitlines())
    return summe if gefunden else None


def _zaehle_testfunktionen() -> int | None:
    summe, gefunden = 0, False
    for ordner in (ASSISTANT / "scripts", ASSISTANT / "tools"):
        if not ordner.is_dir():
            continue
        for datei in ordner.glob("test_*.py"):
            gefunden = True
            summe += len(re.findall(r"^\s*def test_", datei.read_text(encoding="utf-8"), re.M))
    return summe if gefunden else None


def _lies_reise() -> dict | None:
    """Die Messung der Reise-Seite. Fehlt sie, gibt es den Abschnitt nicht.

    Kein Pflichtfeld: die Reise endet am 07.09., der Abschnitt verschwindet dann
    von selbst, statt eine tote Zahl weiterzutragen. Und 0 gemessene Meldungen
    ist hier kein Fehler, sondern "noch nichts passiert" — deshalb None statt
    einer Ausnahme.
    """
    try:
        daten = json.loads(REISE_MESSUNG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not daten.get("gemessen"):
        return None
    return {
        "gemessen": daten["gemessen"],
        "median_minuten": daten.get("median_minuten"),
        "juengste_minuten": daten.get("juengste_minuten"),
        "schnellste_minuten": daten.get("schnellste_minuten"),
    }


def messe() -> dict:
    """Alle Zahlen der Seite, aus den Repos dieser Maschine."""
    cpu, kerne, ram = _lies_hardware()
    werktag, wochenende = _lies_weckzeiten()
    koautor, koautor_repos = _zaehle_koautor()
    heute = datetime.now(timezone.utc).date()

    # Jede jemals in die Inbox geschriebene Nachricht — die Datei selbst wird
    # regelmaessig archiviert, die git-Historie nicht.
    inbox_zeilen = _git(ASSISTANT, ["log", "-p", "--format=", "--", "state/inbox.md"])
    nachrichten = None
    if inbox_zeilen is not None:
        eindeutig = {
            z for z in inbox_zeilen.splitlines()
            if re.match(r"^\+- \[20\d\d-\d\d-\d\d ", z)
        }
        nachrichten = len(eindeutig)

    return {
        "nachrichten": nachrichten,
        "sitzungen": git_zaehle(ASSISTANT, ["log", "--format=%s", "--grep=^journal:"]),
        "commits_assistant": git_zaehle(ASSISTANT, ["rev-list", "--count", "HEAD"]),
        "auftraege": git_zaehle(
            AGENT_TASKS, ["log", "--diff-filter=A", "--name-only", "--format="], r"\.yaml$"
        ),
        # PR-Zahlen brauchen Netz; sie werden aus der bestehenden zahlen.json
        # uebernommen und nur mit `--prs` neu geholt.
        "prs": None,
        "pr_repos": None,
        "koautor_commits": koautor,
        "koautor_repos": koautor_repos,
        "tage": (heute - START).days,
        # Nur, was auch wirklich laeuft: `*` wuerde __pycache__, fixtures und
        # eine Wortliste als "Werkzeuge" mitzaehlen.
        "werkzeuge": sum(
            n for n in (_zaehle_dateien(ASSISTANT / "tools", "*.py"),
                        _zaehle_dateien(ASSISTANT / "tools", "*.sh"),
                        _zaehle_dateien(ASSISTANT / "scripts", "*.py"),
                        _zaehle_dateien(ASSISTANT / "scripts", "*.sh")) if n
        ) or None,
        "testfunktionen": _zaehle_testfunktionen(),
        "codezeilen": _zaehle_zeilen([ASSISTANT / "tools", ASSISTANT / "scripts"]),
        "units": _zaehle_dateien(ASSISTANT / "systemd", "*"),
        "skills": _zaehle_dateien(SKILLS, "*"),
        "weckzeiten_werktag": werktag,
        "weckzeiten_wochenende": wochenende,
        "cpu": cpu,
        "kerne": kerne,
        "ram_gb": ram,
        "start": START.isoformat(),
        "stand": heute.isoformat(),
        "basis": BASIS,
        "reise": _lies_reise(),
        "konditionen": _lies_konditionen(),
    }


def hole_pr_zahlen() -> tuple[int | None, int | None]:
    """Gemergte Pull Requests ohne Dependabot, ueber die GitHub-CLI.

    Getrennt von `messe()`, weil es als einziges Netz braucht — und weil eine
    Messung, die am Netz haengt, den ganzen Build kippen wuerde.
    """
    if not REPOS.is_dir():
        return None, None
    gesamt, repos, gesehen = 0, 0, set()
    for ordner in sorted(REPOS.iterdir()):
        if not (ordner / ".git").exists():
            continue
        url = _git(ordner, ["remote", "get-url", "origin"]) or ""
        treffer = re.search(r"github\.com[:/](jenslaufer/[^/\s]+?)(?:\.git)?\s*$", url)
        if not treffer or treffer.group(1) in gesehen:
            continue
        gesehen.add(treffer.group(1))
        try:
            lauf = subprocess.run(
                ["gh", "pr", "list", "--repo", treffer.group(1), "--state", "merged",
                 "--limit", "1000", "--json", "author",
                 "--jq", '[.[] | select(.author.login != "dependabot[bot]")] | length'],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        text = lauf.stdout.strip()
        if lauf.returncode == 0 and text.isdigit() and int(text) > 0:
            gesamt += int(text)
            repos += 1
    return (gesamt or None), (repos or None)


# ------------------------------------------------------------------- Rendern

def zahl(wert: int, sprache: str = "de") -> str:
    """2.167 auf Deutsch, 2,167 auf Englisch — dieselbe Zahl, zwei Schreibweisen.

    Die Trennzeichen sind zwischen den Sprachen vertauscht: eine deutsche
    2.167 liest ein englischer Leser als zwei Komma eins sechs sieben. Eine
    unveraendert uebernommene Zahl waere also nicht bloss fremd, sondern um den
    Faktor Tausend falsch.
    """
    getrennt = f"{wert:,}"
    return getrennt if sprache == "en" else getrennt.replace(",", ".")


MONATE_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]


def _datum(iso: str, sprache: str = "de") -> str:
    jahr, monat, tag = iso.split("-")
    if sprache == "en":
        # 17.08.2026 heisst in den USA der 8. Datum ohne Monatsnamen ist auf
        # einer englischen Seite mehrdeutig, und zwar still.
        return f"{int(tag)} {MONATE_EN[int(monat) - 1]} {jahr}"
    return f"{tag}.{monat}.{jahr}"


def _zeitstreifen(aktive: list[int]) -> str:
    """24 Stunden als Streifen, die Weckzeiten markiert."""
    teile = []
    for stunde in range(24):
        an = " an" if stunde in aktive else ""
        beschriftung = f"{stunde:02d}" if stunde % 6 == 0 else ""
        teile.append(
            f'<span class="stunde{an}" title="{stunde:02d}:00 UTC">'
            f'<i></i><b>{beschriftung}</b></span>'
        )
    return "".join(teile)


def _reise_abschnitt_en(reise: dict) -> str:
    """Dieselbe Messung auf Englisch. Ein Hinweis mehr: die Seite ist deutsch."""
    ziel = REISE_SEITE_EN or REISE_SEITE
    sprachnote = "" if REISE_SEITE_EN else " (written in German)"
    return f"""
<section class="bahn">
  <p class="kicker">Running right now</p>
  <h2>The travel diary nobody types</h2>
  <p>Since 15 August Jens has been in Singapore and Malaysia for three weeks —
     with a phone, no computer, no terminal. A public page grows during that time
     anyway: <a href="{ziel}">a travel diary</a>{sprachnote} in which every entry
     says whether we lived through it ourselves or only looked it up.</p>
  <p>The procedure is the whole trick. Jens sends a Telegram message whenever
     something out there worked — two sentences, often just a photo. Everything
     after that runs here on its own: hold the message against the existing
     notes, decide whether it becomes a <i>lived through it</i> or an <i>open,
     still to come</i>, write it in, check it for private data, rebuild the page,
     publish. Nobody sits in between — it is three in the morning in Germany when
     a bus leaves over there.</p>
</section>

<section class="band">
  <div class="bahn">
    <p class="kicker hell">From message to published</p>
    <div class="gitter">
      <div class="kachel"><b>{zahl(reise['gemessen'], 'en')}</b><span>messages processed this way so far</span></div>
      <div class="kachel"><b>{zahl(reise['juengste_minuten'], 'en')}<small> min</small></b><span>most recent, message to live page</span></div>
      <div class="kachel"><b>{zahl(reise['median_minuten'], 'en')}<small> min</small></b><span>median across all of them</span></div>
    </div>
    <p class="einschraenkung">These numbers are measured too. Under every
       lived-through entry over there stands its own timing — the timestamp of the
       Telegram message and the timestamp of the commit that published it, both
       taken from the Git history and checkable one by one. The median includes
       the first four messages, which arrived before the page even existed; their
       value contains the building of it. The most recent number is the
       meaningful one.</p>
    <p class="knopfzeile"><a class="knopf" href="{ziel}">Open the travel diary</a></p>
  </div>
</section>
"""


def _reise_abschnitt(reise: dict | None, sprache: str = "de") -> str:
    """Der laufende Beweis: eine Seite, die es ohne diesen Aufbau nicht gaebe.

    Alles andere hier ist Innenansicht — Zahlen ueber das eigene Repo. Dieser
    Abschnitt zeigt ein Ergebnis, das jeder anklicken und lesen kann, und die
    einzige Zahl, die fuer einen Auftraggeber wirklich zaehlt: wie lange von der
    Anforderung bis zum veroeffentlichten Artefakt.

    Ohne Messung faellt der Abschnitt weg. Eine Seite, die eine laufende Reise
    behauptet, die vorbei ist, ist schlechter als eine ohne den Abschnitt.
    """
    if not reise:
        return ""
    if sprache == "en":
        return _reise_abschnitt_en(reise)
    return f"""
<section class="bahn">
  <p class="kicker">Läuft gerade</p>
  <h2>Der Reisebericht, den niemand tippt</h2>
  <p>Seit dem 15. August ist Jens drei Wochen in Singapur und Malaysia — mit dem
     Telefon, ohne Rechner, ohne Terminal. Trotzdem wächst in dieser Zeit eine
     öffentliche Seite: <a href="{REISE_SEITE}">ein Reisebericht</a>, in dem jeder
     Punkt sagt, ob wir es selbst erlebt oder nur nachgeschlagen haben.</p>
  <p>Der Ablauf ist der ganze Trick. Jens schickt eine Telegram-Nachricht, wenn
     unterwegs etwas funktioniert hat — zwei Sätze, oft nur ein Foto. Hier läuft
     dann alles Weitere allein: den Satz gegen die bisherigen Notizen halten,
     entscheiden, ob daraus ein <i>selbst erlebt</i> wird oder ein <i>offen, kommt
     noch</i>, eintragen, auf private Daten prüfen, die Seite neu bauen,
     veröffentlichen. Niemand sitzt dazwischen — in Deutschland ist es drei Uhr
     nachts, wenn dort ein Bus fährt.</p>
</section>

<section class="band">
  <div class="bahn">
    <p class="kicker hell">Von der Nachricht bis online</p>
    <div class="gitter">
      <div class="kachel"><b>{zahl(reise['gemessen'])}</b><span>Meldungen bisher so verarbeitet</span></div>
      <div class="kachel"><b>{zahl(reise['juengste_minuten'])}<small> min</small></b><span>zuletzt von der Nachricht bis online</span></div>
      <div class="kachel"><b>{zahl(reise['median_minuten'])}<small> min</small></b><span>Median über alle</span></div>
    </div>
    <p class="einschraenkung">Auch diese Zahlen sind gemessen. Unter jedem selbst
       erlebten Eintrag drüben steht seine eigene Zeit — der Zeitstempel der
       Telegram-Nachricht und der des Commits, der ihn veröffentlicht hat, beide
       aus der Git-Historie und einzeln nachrechenbar. Der Median enthält die
       ersten vier Meldungen, die eintrafen, bevor es die Seite überhaupt gab;
       ihr Wert enthält deren Bau mit. Die jüngste Zahl ist die aussagekräftige.</p>
    <p class="knopfzeile"><a class="knopf" href="{REISE_SEITE}">Die Reise-Seite ansehen</a></p>
  </div>
</section>
"""


def _lies_konditionen() -> dict | None:
    """Tagessatz und Verfuegbarkeit aus der Lebenslauf-Datei, oder None.

    Halb gelesen waere schlimmer als gar nicht: ein Abschnitt, der eine
    Verfuegbarkeit ohne Preis nennt, sieht aus wie eine Entscheidung. Ohne
    Tagessatz gibt es deshalb nichts.
    """
    try:
        roh = KONDITIONEN.read_text(encoding="utf-8")
    except OSError:
        return None
    felder = {}
    for zeile in roh.splitlines()[1:]:
        if "," not in zeile:
            continue
        name, _, wert = zeile.partition(",")
        felder[name.strip()] = wert.strip()
    if not felder.get("Tagessatz"):
        return None
    return {
        "tagessatz": felder["Tagessatz"],
        "verfuegbar": felder.get("Verfügbarkeit", ""),
        "remote": felder.get("Anteil Remote", ""),
        "einsatzort": felder.get("Einsatzort", ""),
    }


def _konditionen_en(konditionen: dict) -> list[tuple[str, str]]:
    """Die Konditionen fuer englische Leser, ohne eine einzige neue Zahl.

    Der Wert kommt aus `cv/data/konditionen.csv` und ist deutsch geschrieben
    ("2.000 €/Tag (netto)", "ab 15.09.2026"). Uebersetzt wird nur die
    Schreibweise, nie der Inhalt: 2.000 muss auf Englisch 2,000 heissen, sonst
    liest es sich als zwei Euro. Passt ein Wert auf kein bekanntes Muster,
    steht er unveraendert da — ein unuebersetzter Originalwert ist ein
    Schoenheitsfehler, ein erfundener Tagessatz ist ein Schaden.
    """
    zeilen = []

    verfuegbar = konditionen.get("verfuegbar", "")
    if verfuegbar:
        treffer = re.fullmatch(r"ab (\d{2})\.(\d{2})\.(\d{4})", verfuegbar.strip())
        if treffer:
            tag, monat, jahr = treffer.groups()
            verfuegbar = f"from {int(tag)} {MONATE_EN[int(monat) - 1]} {jahr}"
        zeilen.append(("Available", verfuegbar))

    satz = konditionen["tagessatz"]
    treffer = re.fullmatch(r"([\d.]+)\s*€/Tag\s*\(netto\)", satz.strip())
    if treffer:
        satz = f"€{treffer.group(1).replace('.', ',')}/day (net)"
    zeilen.append(("Day rate", satz))

    if konditionen.get("remote"):
        zeilen.append(("Remote", konditionen["remote"]))
    if konditionen.get("einsatzort"):
        ort = konditionen["einsatzort"]
        zeilen.append(("Based", {"weltweit": "worldwide"}.get(ort.strip().lower(), ort)))
    return zeilen


def _buchen_abschnitt_en(konditionen: dict | None) -> str:
    lebenslauf = LEBENSLAUF_EN or LEBENSLAUF
    sprachnote = "" if LEBENSLAUF_EN else " (in German)"
    tabelle = "".join(
        f'<div class="kondition"><span>{name}</span><b>{wert}</b></div>'
        for name, wert in (_konditionen_en(konditionen) if konditionen else [])
    )
    konditionen_html = f'<div class="konditionen">{tabelle}</div>' if tabelle else ""

    return f"""
<section class="bahn buchen" id="buchen">
  <p class="kicker">Available for hire</p>
  <h2>Jens Laufer — Forward Deployed Engineer</h2>
  <p class="lead-klein">A Forward Deployed Engineer does not sit in product
     development, he sits inside the customer's problem: he takes a model that
     works in the demo and gets it to where the real data, the real workflows and
     the real failures are. The part of that Jens likes most now has a name of its
     own — <b>Harness Engineer</b>: not building the model, but building the
     scaffolding around it in which it works unattended.</p>
  <p>Solytics GmbH, Karlstein am Main, Germany. Around sixteen years of fullstack
     engineering, freelance without interruption since 2009 — Java and Spring Boot
     behind him, Vue in front, test-driven throughout; alongside that, data science
     and machine learning as a second track. Since late 2025 almost nothing but
     this: wake-up times, memory, channels, watchdogs, and a system's ability to
     notice its own failure.</p>
  <p>This page is the work sample. It is not described, it is built, every number
     on it is measured, and the job for it came in by phone from the other side of
     the planet.</p>
  {konditionen_html}
  <p class="knopfzeile">
    <a class="knopf" href="{LINKEDIN}">Message on LinkedIn</a>
    <a class="knopf knopf--leise" href="{lebenslauf}">See the CV{sprachnote}</a>
  </p>
</section>
"""


def _buchen_abschnitt(konditionen: dict | None, sprache: str = "de") -> str:
    """Wofuer man Jens bucht, ab wann, und was es kostet.

    Der Abschnitt bleibt auch ohne Konditionen stehen — wer bis hierher gelesen
    hat, soll erfahren, was Jens macht und wie er erreichbar ist. Es faellt nur
    die Zeile mit dem Satz weg, denn die einzige Zahl auf dieser Seite, die
    nicht gemessen werden kann, darf auch nicht geraten werden.
    """
    if sprache == "en":
        return _buchen_abschnitt_en(konditionen)
    zeilen = []
    if konditionen:
        if konditionen.get("verfuegbar"):
            zeilen.append(("Verfügbar", konditionen["verfuegbar"]))
        zeilen.append(("Tagessatz", konditionen["tagessatz"]))
        if konditionen.get("remote"):
            zeilen.append(("Remote", konditionen["remote"]))
        if konditionen.get("einsatzort"):
            zeilen.append(("Einsatzort", konditionen["einsatzort"]))
    tabelle = "".join(
        f'<div class="kondition"><span>{name}</span><b>{wert}</b></div>'
        for name, wert in zeilen
    )
    konditionen_html = f'<div class="konditionen">{tabelle}</div>' if tabelle else ""

    return f"""
<section class="bahn buchen" id="buchen">
  <p class="kicker">Zu buchen</p>
  <h2>Jens Laufer — Forward Deployed Engineer</h2>
  <p class="lead-klein">Ein Forward Deployed Engineer sitzt nicht in der Produktentwicklung,
     sondern beim Kunden im Problem: er nimmt ein Modell, das in der Demo funktioniert,
     und bringt es dorthin, wo echte Daten, echte Abläufe und echte Ausfälle sind.
     Der Teil davon, den Jens am liebsten macht, hat inzwischen einen eigenen Namen —
     <b>Harness Engineer</b>: nicht das Modell bauen, sondern den Aufbau darum, in dem
     es unbeaufsichtigt arbeitet.</p>
  <!-- Angaben aus cv/data: highlights.csv ("~16 Jahre Fullstack seit 2009,
       durchgehend als Freelancer"), projects.csv (Harness Engineering seit
       12/2025). Nichts hier geschaetzt — eine erfundene Jahreszahl im
       Lebenslauf-Absatz faellt beim ersten Gespraech auf. -->
  <p>Solytics GmbH, Karlstein am Main. Rund sechzehn Jahre Fullstack-Entwicklung,
     seit 2009 durchgehend freiberuflich — Java und Spring Boot im Rücken, Vue davor,
     konsequent testgetrieben; daneben Data Science und Machine Learning als zweites
     Standbein. Seit Ende 2025 fast nur noch das hier: Weckzeiten, Gedächtnis, Kanäle,
     Wächter, und die Fähigkeit eines Systems, den eigenen Ausfall zu bemerken.</p>
  <p>Diese Seite ist die Arbeitsprobe. Sie ist nicht beschrieben, sondern gebaut,
     jede Zahl darauf ist gemessen, und der Auftrag dazu kam per Telefon von der
     anderen Seite der Erde.</p>
  {konditionen_html}
  <p class="knopfzeile">
    <a class="knopf" href="{LINKEDIN}">Auf LinkedIn schreiben</a>
    <a class="knopf knopf--leise" href="{LEBENSLAUF}">Lebenslauf ansehen</a>
  </p>
</section>
"""


def rendere(zahlen: dict, sprache: str = "de") -> str:
    pruefe_zahlen(zahlen)
    vorlage = VORLAGEN[sprache].read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    werte = {
        "CSS": css,
        "BASIS": zahlen.get("basis") or BASIS,
        "NACHRICHTEN": zahl(zahlen["nachrichten"], sprache),
        "SITZUNGEN": zahl(zahlen["sitzungen"], sprache),
        "COMMITS": zahl(zahlen["commits_assistant"], sprache),
        "AUFTRAEGE": zahl(zahlen["auftraege"], sprache),
        "PRS": zahl(zahlen["prs"], sprache),
        "PR_REPOS": zahl(zahlen["pr_repos"], sprache),
        "KOAUTOR": zahl(zahlen["koautor_commits"], sprache),
        "KOAUTOR_REPOS": zahl(zahlen["koautor_repos"], sprache),
        "TAGE": zahl(zahlen["tage"], sprache),
        "WERKZEUGE": zahl(zahlen["werkzeuge"], sprache),
        "TESTS": zahl(zahlen["testfunktionen"], sprache),
        "CODEZEILEN": zahl(zahlen["codezeilen"], sprache),
        "UNITS": zahl(zahlen["units"], sprache),
        "SKILLS": zahl(zahlen["skills"], sprache),
        "WECKUNGEN": zahl(len(zahlen["weckzeiten_werktag"]), sprache),
        "WECKUNGEN_WE": zahl(len(zahlen["weckzeiten_wochenende"]), sprache),
        "ZEITSTREIFEN": _zeitstreifen(zahlen["weckzeiten_werktag"]),
        "CPU": zahlen["cpu"],
        "KERNE": zahl(zahlen["kerne"], sprache),
        "RAM": zahl(zahlen["ram_gb"], sprache),
        "START": _datum(zahlen["start"], sprache),
        "STAND": _datum(zahlen["stand"], sprache),
        "NACHRICHTEN_PRO_TAG": f"{zahlen['nachrichten'] / max(zahlen['tage'], 1):.0f}",
        "REISE": _reise_abschnitt(zahlen.get("reise"), sprache),
        "BUCHEN": _buchen_abschnitt(zahlen.get("konditionen"), sprache),
    }

    seite = vorlage
    for platzhalter, wert in werte.items():
        seite = seite.replace("{{" + platzhalter + "}}", str(wert))

    # Kommentare in der Vorlage sind Notizen fuer uns, nicht fuer Leser.
    seite = re.sub(r"<!--.*?-->", "", seite, flags=re.S)
    return seite


def baue_og_bild(zahlen: dict, ziel: Path = None, sprache: str = "de") -> Path | None:
    """Rendert die Vorschaukarte fuer LinkedIn (1200x630) mit Chromium.

    Die Karte traegt dieselben gemessenen Zahlen wie die Seite — eine
    handgepflegte Vorschau waere nach dem naechsten Build falsch, und niemand
    sieht Vorschaubilder je wieder an.
    """
    ziel = ziel or (WURZEL / ("og.png" if sprache == "de" else "en/og.png"))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    karte = OG_VORLAGEN[sprache].read_text(encoding="utf-8")
    for platzhalter, feld in (("NACHRICHTEN", "nachrichten"), ("SITZUNGEN", "sitzungen"),
                              ("AUFTRAEGE", "auftraege"), ("TAGE", "tage")):
        karte = karte.replace("{{" + platzhalter + "}}", zahl(zahlen[feld], sprache))
    karte = karte.replace("{{ZEITSTREIFEN}}", _zeitstreifen(zahlen["weckzeiten_werktag"]))
    pruefe_privat(karte)

    # Snap-Chromium darf weder nach /tmp noch in versteckte Ordner schreiben.
    arbeit = Path.home() / "pdf-slim-work" / "harry" / sprache
    arbeit.mkdir(parents=True, exist_ok=True)
    quelle = arbeit / "og-karte.html"
    quelle.write_text(karte, encoding="utf-8")

    for programm in ("chromium", "chromium-browser", "google-chrome"):
        try:
            lauf = subprocess.run(
                [programm, "--headless", "--disable-gpu", "--hide-scrollbars",
                 "--window-size=1200,630", "--virtual-time-budget=8000",
                 f"--screenshot={arbeit / 'og.png'}", f"file://{quelle}"],
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if lauf.returncode == 0 and (arbeit / "og.png").exists():
            ziel.write_bytes((arbeit / "og.png").read_bytes())
            return ziel
    return None


def schreibe(zahlen: dict, ziel: Path = None, zahlen_ziel: Path = None) -> str:
    """Rendert und schreibt — aber erst, nachdem die Seite geprueft ist.

    Die Reihenfolge ist der Punkt: erst der ganze Text, dann die Pruefung, dann
    das Schreiben. Wer zwischendurch schreibt, hinterlaesst bei einem Treffer
    eine halbe Seite mit dem Privaten darin.
    """
    seite = rendere(zahlen)
    pruefe_privat(seite)
    englisch = rendere(zahlen, "en")
    # Beide Fassungen werden geprueft, bevor EINE geschrieben wird. Die
    # englische ist derselbe Inhalt in anderen Worten — also dieselbe Gefahr,
    # und eine ungeprueft geschriebene Fassung waere das Leck.
    pruefe_privat(englisch)

    ziel_de = ziel or ZIEL
    ziel_de.write_text(seite, encoding="utf-8")
    ziel_en = ziel_de.parent / "en" / "index.html"
    ziel_en.parent.mkdir(parents=True, exist_ok=True)
    ziel_en.write_text(englisch, encoding="utf-8")

    ziel_json = zahlen_ziel or ZAHLEN
    ziel_json.parent.mkdir(parents=True, exist_ok=True)
    ziel_json.write_text(
        json.dumps(zahlen, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return seite


def main() -> int:
    global GESPERRT
    zerleger = argparse.ArgumentParser(description=__doc__)
    zerleger.add_argument("--no-measure", action="store_true",
                          help="nicht messen, aus content/zahlen.json bauen")
    zerleger.add_argument("--prs", action="store_true",
                          help="auch die PR-Zahlen neu holen (braucht gh und Netz)")
    zerleger.add_argument("--og", action="store_true",
                          help="auch die LinkedIn-Vorschaukarte og.png neu rendern")
    zerleger.add_argument("--check", action="store_true",
                          help="messen und pruefen, nichts schreiben")
    argumente = zerleger.parse_args()

    GESPERRT = lade_sperrliste()
    if WARNLISTE.exists():
        GESPERRT = GESPERRT + lade_sperrliste(WARNLISTE)

    alt = {}
    if ZAHLEN.exists():
        alt = json.loads(ZAHLEN.read_text(encoding="utf-8"))

    if argumente.no_measure:
        zahlen = alt
        if not zahlen:
            print("content/zahlen.json fehlt — ohne Messung ist nichts zu bauen.", file=sys.stderr)
            return 2
    else:
        zahlen = messe()
        # Was nicht gemessen werden konnte, behaelt den letzten bekannten Wert.
        # Eine Zahl, die beim naechsten Lauf verschwindet, waere schlimmer als
        # eine, die einen Lauf alt ist — und die JSON sagt, wann sie herkam.
        for feld, wert in zahlen.items():
            if wert in (None, [], "") and feld in alt:
                zahlen[feld] = alt[feld]
        if argumente.prs or not zahlen.get("prs"):
            prs, repos = hole_pr_zahlen()
            if prs:
                zahlen["prs"], zahlen["pr_repos"] = prs, repos

    try:
        if argumente.check:
            for sprache in SPRACHEN:
                pruefe_privat(rendere(zahlen, sprache))
            print("geprüft (de + en): keine privaten Angaben, alle Messwerte vorhanden.")
            return 0
        seite = schreibe(zahlen)
    except (PrivatException, ZahlenException) as fehler:
        print(f"ABBRUCH: {fehler}", file=sys.stderr)
        print("Es wurde nichts geschrieben.", file=sys.stderr)
        return 1

    englisch = (ZIEL.parent / "en" / "index.html").read_text(encoding="utf-8")
    print(f"index.html: {len(seite.encode('utf-8'))} B, "
          f"en/index.html: {len(englisch.encode('utf-8'))} B, "
          f"Stand {_datum(zahlen['stand'])}")

    # Sitemap. Am 17.08. bei Google selbst gemessen: die Search Console meldet
    # fuer diese Seite `URL is unknown to Google`. Sie liegt in einem eigenen
    # Repo und stand deshalb in keiner sitemap der Domain — ohne einen Weg
    # dorthin holt Google sie nicht ab, wie gut die Zahlen darauf auch sind.
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for adresse in (BASIS, BASIS + "en/"):
        sm.append(f"  <url><loc>{adresse}</loc>"
                  f"<lastmod>{str(zahlen['stand'])[:10]}</lastmod></url>")
    sm.append("</urlset>")
    (WURZEL / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")
    print(f"sitemap.xml: {len(sm) - 3} Adressen")

    if argumente.og:
        for sprache in SPRACHEN:
            bild = baue_og_bild(zahlen, sprache=sprache)
            if bild:
                print(f"{bild.relative_to(WURZEL)}: {bild.stat().st_size} B")
            else:
                # Kein Abbruch: die Seite steht. Aber es muss auffallen, sonst
                # zeigt LinkedIn wochenlang eine Vorschau mit alten Zahlen.
                print(f"og.png ({sprache}) NICHT gebaut (kein Chromium?) — "
                      "Vorschau bleibt alt.", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
