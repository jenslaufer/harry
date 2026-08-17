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
VORLAGE = WURZEL / "template" / "page.html"
OG_VORLAGE = WURZEL / "template" / "og.html"
CSS = WURZEL / "template" / "site.css"
ZAHLEN = WURZEL / "content" / "zahlen.json"
ZIEL = WURZEL / "index.html"

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

def zahl(wert: int) -> str:
    """Deutsche Schreibweise: 2.167, nicht 2,167 und nicht 2167."""
    return f"{wert:,}".replace(",", ".")


def _datum(iso: str) -> str:
    jahr, monat, tag = iso.split("-")
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


def rendere(zahlen: dict) -> str:
    pruefe_zahlen(zahlen)
    vorlage = VORLAGE.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    werte = {
        "CSS": css,
        "BASIS": zahlen.get("basis") or BASIS,
        "NACHRICHTEN": zahl(zahlen["nachrichten"]),
        "SITZUNGEN": zahl(zahlen["sitzungen"]),
        "COMMITS": zahl(zahlen["commits_assistant"]),
        "AUFTRAEGE": zahl(zahlen["auftraege"]),
        "PRS": zahl(zahlen["prs"]),
        "PR_REPOS": zahl(zahlen["pr_repos"]),
        "KOAUTOR": zahl(zahlen["koautor_commits"]),
        "KOAUTOR_REPOS": zahl(zahlen["koautor_repos"]),
        "TAGE": zahl(zahlen["tage"]),
        "WERKZEUGE": zahl(zahlen["werkzeuge"]),
        "TESTS": zahl(zahlen["testfunktionen"]),
        "CODEZEILEN": zahl(zahlen["codezeilen"]),
        "UNITS": zahl(zahlen["units"]),
        "SKILLS": zahl(zahlen["skills"]),
        "WECKUNGEN": zahl(len(zahlen["weckzeiten_werktag"])),
        "WECKUNGEN_WE": zahl(len(zahlen["weckzeiten_wochenende"])),
        "ZEITSTREIFEN": _zeitstreifen(zahlen["weckzeiten_werktag"]),
        "CPU": zahlen["cpu"],
        "KERNE": zahl(zahlen["kerne"]),
        "RAM": zahl(zahlen["ram_gb"]),
        "START": _datum(zahlen["start"]),
        "STAND": _datum(zahlen["stand"]),
        "NACHRICHTEN_PRO_TAG": f"{zahlen['nachrichten'] / max(zahlen['tage'], 1):.0f}",
    }

    seite = vorlage
    for platzhalter, wert in werte.items():
        seite = seite.replace("{{" + platzhalter + "}}", str(wert))

    # Kommentare in der Vorlage sind Notizen fuer uns, nicht fuer Leser.
    seite = re.sub(r"<!--.*?-->", "", seite, flags=re.S)
    return seite


def baue_og_bild(zahlen: dict, ziel: Path = None) -> Path | None:
    """Rendert die Vorschaukarte fuer LinkedIn (1200x630) mit Chromium.

    Die Karte traegt dieselben gemessenen Zahlen wie die Seite — eine
    handgepflegte Vorschau waere nach dem naechsten Build falsch, und niemand
    sieht Vorschaubilder je wieder an.
    """
    ziel = ziel or (WURZEL / "og.png")
    karte = OG_VORLAGE.read_text(encoding="utf-8")
    for platzhalter, feld in (("NACHRICHTEN", "nachrichten"), ("SITZUNGEN", "sitzungen"),
                              ("AUFTRAEGE", "auftraege"), ("TAGE", "tage")):
        karte = karte.replace("{{" + platzhalter + "}}", zahl(zahlen[feld]))
    karte = karte.replace("{{ZEITSTREIFEN}}", _zeitstreifen(zahlen["weckzeiten_werktag"]))
    pruefe_privat(karte)

    # Snap-Chromium darf weder nach /tmp noch in versteckte Ordner schreiben.
    arbeit = Path.home() / "pdf-slim-work" / "harry"
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
    (ziel or ZIEL).write_text(seite, encoding="utf-8")
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
            pruefe_privat(rendere(zahlen))
            print("geprüft: keine privaten Angaben, alle Messwerte vorhanden.")
            return 0
        seite = schreibe(zahlen)
    except (PrivatException, ZahlenException) as fehler:
        print(f"ABBRUCH: {fehler}", file=sys.stderr)
        print("Es wurde nichts geschrieben.", file=sys.stderr)
        return 1

    print(f"index.html: {len(seite.encode('utf-8'))} B, Stand {_datum(zahlen['stand'])}")

    if argumente.og:
        bild = baue_og_bild(zahlen)
        if bild:
            print(f"og.png: {bild.stat().st_size} B")
        else:
            # Kein Abbruch: die Seite steht. Aber es muss auffallen, sonst zeigt
            # LinkedIn wochenlang eine Vorschau mit alten Zahlen.
            print("og.png NICHT gebaut (kein Chromium?) — Vorschau bleibt alt.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
