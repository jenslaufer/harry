# otto.jenslaufer.com

A page about Otto — Jens Laufer's personal assistant. Claude is the brain; the
harness around it (wake-up schedule, memory, channel, watchdogs) is Jens's work.
Static page, no framework, no dependencies beyond three webfonts.

Commissioned by Jens over Telegram, 2026-08-17 06:16 (the assistant was named
Harry at the time; renamed to Otto on 2026-08-17 15:1x after Jens flagged that a
colleague had given his own coding agent the same name): *"Mach subdomain. Füge
hinzu, dass Du Harry (mein persönlicher Assistent. Claude ist das Gehirn und Jens
hat den Harness erstellt). Du kannst ein paar Statistiken zum Harness geben
(Storytelling mit dem mini-pc im Keller, Telegram als Kommunikation). […] Gib
alles. Reduce ai slop."*

## The load-bearing idea: nothing on this page is typed

Every number is measured at build time from the git history of the repos on the
machine, written to `content/zahlen.json`, and rendered from there. A typed number
on a page that stands for months is wrong after two weeks and looks exactly like a
correct one until someone checks.

`content/zahlen.json` is committed, so the git history of that one file is the
audit trail: it shows when each number was measured and how it moved.

```bash
python3 build.py                 # measure, write zahlen.json + index.html
python3 build.py --og            # also re-render the LinkedIn preview og.png
python3 build.py --no-measure    # build from content/zahlen.json only
python3 build.py --prs           # also refresh the PR counts (needs gh + network)
python3 build.py --check         # measure and verify, write nothing
python3 tests/test_build.py      # 32 tests
```

Measuring takes ~30 s (it walks every repo under `~/repos`). PR counts need
network and are therefore kept out of the main path: a measurement that depends on
the network must never be able to fail the whole build.

## The live proof: the travel page

Everything else on this page is an inside view — numbers about its own repos.
One section is not: while Jens travels for three weeks with only a phone, a
public travel report grows at [jenslaufer.com/malaysia](https://jenslaufer.com/malaysia/),
built from nothing but Telegram messages.

That section renders from `~/repos/assistant/state/reise-werkstatt.json` — the
same measurement the travel page itself reads, so the two can't drift into two
different numbers. `_lies_reise()` returns `None` when the file is missing or no
message has been measured, and the whole section disappears. That is deliberate:
the trip ends 2026-09-07, and a page claiming a running trip that is over is
worse than a page without the section. It expires by itself.

The measured quantity is the one an FDE buyer actually cares about: **from the
request to the published artifact.** Not "an agent exists" — how long, measured,
per entry, in git.

## The one section anchored to a single night

`_messe_fingrab()` renders the FinGrab paywall fix of 2026-08-19 — the one place
on the page where the whole chain (found unprompted → issue → tests → fix →
release → verified against the *shipped* package) hangs on a business case
rather than on the page's own repos.

The anchor is `FINGRAB_ZWEIG`, a **branch name**, not a number. Scope (files,
lines added/removed), the UTC time the fix reached `main` and the version on
`main` are all measured from it at build time. Lose the anchor — history
rewritten, repo gone — and the section disappears, like the travel section: half
a story with dead numbers is worse than no story.

Two details are deliberate. The time comes from `%at` and is converted to UTC in
Python, never `--date=local`: the mini-PC builds in UTC and Jens reads the page
from Asia, so a local time is wrong by up to eight hours depending on the reader.
And the anchor is re-checked in Python after `--grep` has already filtered — a
parser that trusts git's `-1` takes the next-best line when the grep misses, and
a wrong number here is worse than none. That was a real bug, caught by
`TestFingrab.test_uhrzeit_ist_utc_und_nicht_die_zeitzone_des_rechners`.

The section states what Otto may **not** do (push to `main`, merge its own pull
requests, spend money) and names Jens as the one who typed the two merges. That
half is load-bearing, not modesty: an autonomy claim without its limits is
refutable in the first critical reply; with them it is evidence. A test asserts
the limits are present in both languages.

## The blocklist lives outside this repo — on purpose

`pruefe_privat()` aborts the build if a passport number, IBAN, e-mail address or
phone number would reach the page. Patterns cannot catch names, amounts or
addresses, so there is also a word list — and it is **not** in this repo:

    ~/repos/assistant/state/oeffentlich-gesperrt.txt

This repo is public (GitHub Pages is not available for private repos on this
plan), and a blocklist is by definition a list of exactly the words nobody should
see. Keeping it next to the page publishes what it protects. That is not
hypothetical: on 2026-08-17 the sibling repo `jenslaufer/malaysia` shipped an
arrival-card PIN in its own privacy guard.

Missing or empty list → the build aborts. A guard that silently passes when its
config is gone is not a guard.

## Delivery

GitHub Pages from `main`, repo root. `.nojekyll` disables Jekyll processing.

The page is served at `https://jenslaufer.com/otto/` until the DNS record for
the subdomain exists. `BASIS` in `build.py` is the single place that decides which
address goes into `canonical` and `og:image` — switch it in one line, rebuild, and
add a `CNAME` file.

**DNS is deliberately not set yet.** `jenslaufer.com` carries live MX records
(mxa/mxb.mailgun.org); the Namecheap API rewrites the whole record set on change
and has dropped `EmailType` before, which kills mail. During a trip that runs on
booking confirmations, that risk buys nothing — the page is visible immediately
under the project path. DNS goes last, on Jens's word.

Then: CNAME record `otto` → `jenslaufer.github.io` (same as `cv` and `concepts`),
a `CNAME` file containing `otto.jenslaufer.com`, and `BASIS` updated.

## Maintenance

Re-run `python3 build.py --og` when the numbers should be refreshed — monthly is
plenty. The page text itself is in `template/page.html`; there is no CMS and does
not need one.
