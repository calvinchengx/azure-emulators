#!/usr/bin/env python3
"""The family's parity and evidence tables, from the ledgers themselves.

Two questions get asked often enough to be worth automating: how much of each
emulator is graded green, and what evidence stands behind those greens. Both
answers already exist, spread across the member repos as docs/parity.md and
docs/witnesses.json. This assembles them.

WHY IT READS EACH REPO'S OWN CHECKER. Counting 🟢 with a grep gives the wrong
number, and wrongly by a different amount per repo: fabric's ledger has 112
green rows but 95 CLAIMS, because its checker skips five sections that make no
capability claim — the legend, the conformance table, the scope boundary, the
emulator-only helpers. keyvault and arm skip a different set; entra another.
So the skip list is parsed out of each repo's scripts/check_witnesses.py rather
than guessed, and every green count here matches what that repo's own gate
reports. A number that disagrees with the repo's gate is a number nobody can
act on.

apim grades in words (implemented / sdk-verified / partial / planned) instead
of emoji, and keys its manifest under "claims". Both shapes are handled; the
alternative is leaving the newest family member out of the family table.

Reads main over HTTP, like check_family_pins.py, so it needs no checkouts and
reports what is PUBLISHED rather than what is in someone's working tree.

Stdlib only, like the family's other scripts.

    ./scripts/family_parity.py              both tables, markdown
    ./scripts/family_parity.py --grades     just the parity grades
    ./scripts/family_parity.py --evidence   just the evidence behind the greens
    ./scripts/family_parity.py --local ..   read sibling checkouts instead of main
    ./scripts/family_parity.py entra        one member, in detail

A member name switches to a report the family table cannot carry: the same
counts, plus every green claim that rests on our own client alone, by name.
That list is the actionable part — "37 uncovered" tells a maintainer nothing
about where to start, and the names do.
"""

import ast
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

RAW = "https://raw.githubusercontent.com/calvinchengx/{repo}/main/{path}"

# Ordered by size of ledger, which is also roughly maturity.
REPOS = [
    ("fabric-emulator", "fabric"),
    ("entra-emulator", "entra"),
    ("azure-keyvault-emulator", "keyvault"),
    ("azure-apim-emulator", "apim"),
    ("arm-emulator", "arm"),
    ("databricks-emulator", "databricks"),
]

# apim's word grades, mapped onto the family's three buckets. `sdk-verified`
# and `implemented` are its green; `planned` is its red.
APIM_GREEN = {"implemented", "sdk-verified"}
APIM_AMBER = {"partial"}
APIM_RED = {"planned", "unknown", "blocked-external"}


def fetch(repo, path, local=None):
    if local:
        f = pathlib.Path(local) / repo / path
        return f.read_text(encoding="utf-8") if f.exists() else None
    try:
        with urllib.request.urlopen(RAW.format(repo=repo, path=path), timeout=20) as r:
            return r.read().decode()
    except urllib.error.URLError:
        return None


def checker_rules(src):
    """The skip list and header cells this repo's own checker applies.

    Parsed rather than imported: the checkers are not packages, and importing
    one would run its main(). Falls back to empty sets, which over-counts
    rather than silently under-counting — a visibly wrong number beats a
    plausible one.
    """
    skip, heads = set(), set()
    if not src:
        return skip, heads
    m = re.search(r"SKIP_SECTIONS\s*=\s*(\{.*?\})", src, re.S)
    if m:
        try:
            skip = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            pass
    m = re.search(r"if cells\[0\] in \(([^)]*)\)", src)
    if m:
        try:
            heads = set(ast.literal_eval("(" + m.group(1) + ",)"))
        except (ValueError, SyntaxError):
            pass
    return skip, heads


def emoji_grades(parity, skip, heads):
    """Count 🟢/🟡/🔴 the way the repo's checker reads its own rows."""
    section, counts, green_keys = None, {"green": 0, "amber": 0, "red": 0}, set()
    for line in parity.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("| ") or section is None or section in skip:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= set("-") or cells[0] in heads:
            continue
        last = cells[-1]
        if "🟢" in last:
            counts["green"] += 1
            green_keys.add(key_for(cells[0]))
        elif "🟡" in last:
            counts["amber"] += 1
        elif "🔴" in last:
            counts["red"] += 1
    return counts, green_keys


def word_grades(parity):
    """apim's ledger: the state lives in its own column, spelled out."""
    counts, green_keys = {"green": 0, "amber": 0, "red": 0}, set()
    for line in parity.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        state = cells[1].lower()
        if state in APIM_GREEN:
            counts["green"] += 1
            green_keys.add(cells[0])
        elif state in APIM_AMBER:
            counts["amber"] += 1
        elif state in APIM_RED:
            counts["red"] += 1
    return counts, green_keys


def key_for(feature):
    """The manifest key a ledger row maps to. Same derivation every checker
    uses, so the keys computed here are the keys they look up."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", feature)
    text = re.sub(r"[*`_]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


def coverage(manifest, green_keys):
    """How many green CLAIMS have independent evidence, not how many citations.

    Citations were the first cut and they overstate breadth, because one claim
    can carry several witnesses: keyvault shows 71 third-party citations across
    48 claims, which reads like more than full coverage and is really 28 of
    them. The question worth answering is what share of what an emulator claims
    has been proved by something other than our own client, so each claim is
    classified once, by its STRONGEST witness:

      ci    a packaged external client in CI, over a network
      sdk   Microsoft's own client, linked into a test in process
      own   our client on both ends, which is our reading of the contract

    Counted the other way round, entra's 14 citations looked like progress on a
    51-claim ledger; 14 of 51 claims covered is the number that shows the gap.
    """
    tiers = {"ci": 0, "sdk": 0, "own": 0}
    entries = manifest.get("claims", manifest) if isinstance(manifest, dict) else {}
    for key, val in entries.items():
        if key.startswith("_") or key.startswith("$") or key not in green_keys:
            continue
        witnesses = val.get("witnesses", []) if isinstance(val, dict) else val
        kinds = {w.partition(":")[0] for w in witnesses if isinstance(witnesses, list)}
        if "ci" in kinds:
            tiers["ci"] += 1
        elif "sdk" in kinds:
            tiers["sdk"] += 1
        else:
            tiers["own"] += 1
    return tiers


def evidence(manifest, green_keys):
    """Witness citations by kind, counted ONLY over the green claims.

    Counting every manifest entry instead is the tempting shortcut and it is
    wrong: fabric's manifest carries 99 entries against 95 green claims,
    because a row that stops being green does not delete its entry. That
    over-counted its own tests by 23 and produced a table that disagreed with
    fabric's own gate, which is the one thing this script must never do.

    `_gated` is fabric's declaration block and `$comment` is apim's
    documentation; neither is a claim.
    """
    kinds = {}
    entries = manifest.get("claims", manifest) if isinstance(manifest, dict) else {}
    for key, val in entries.items():
        if key.startswith("_") or key.startswith("$") or key not in green_keys:
            continue
        witnesses = val.get("witnesses", []) if isinstance(val, dict) else val
        for w in witnesses if isinstance(witnesses, list) else []:
            kind = w.partition(":")[0]
            kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


def uncovered(manifest, green_keys):
    """The green claims resting on our own client alone, with what they DO have.

    Names alone would say where the gaps are; carrying the existing witnesses
    says what a new one would be added to, which is the difference between a
    list and a work queue.
    """
    out = []
    entries = manifest.get("claims", manifest) if isinstance(manifest, dict) else {}
    for key, val in entries.items():
        if key.startswith("_") or key.startswith("$") or key not in green_keys:
            continue
        witnesses = val.get("witnesses", []) if isinstance(val, dict) else val
        witnesses = witnesses if isinstance(witnesses, list) else []
        if not {w.partition(":")[0] for w in witnesses} & {"ci", "sdk"}:
            out.append((key, witnesses))
    return sorted(out)


def member_report(short, grades, cov, gaps):
    """One emulator, in the same table shape as the family view."""
    total = cov["ci"] + cov["sdk"] + cov["own"]
    indep = cov["ci"] + cov["sdk"]
    pct = f" ({round(100 * indep / total)}%)" if total else ""
    lines = [
        f"## {short}\n",
        "| green | partial | not implemented | total |",
        "|---:|---:|---:|---:|",
        f"| {grades['green']} | {grades['amber']} | {grades['red']} | "
        f"{grades['green'] + grades['amber'] + grades['red']} |",
        "",
        "| green claims | ci: external | sdk: only | own tests only | "
        "independently evidenced |",
        "|---:|---:|---:|---:|---:|",
        f"| {total} | {cov['ci']} | {cov['sdk']} | {cov['own']} | "
        f"**{indep}/{total}{pct}** |",
    ]
    if gaps:
        lines += ["", f"### The {len(gaps)} claims with no third-party witness", "",
                  "| claim | witnessed today by |", "|---|---|"]
        for key, witnesses in gaps:
            have = ", ".join(f"`{w}`" for w in witnesses) or "*nothing*"
            lines.append(f"| `{key}` | {have} |")
    else:
        lines += ["", "Every green claim has a third-party witness."]
    return "\n".join(lines)


def collect(local=None, only=None):
    rows = []
    for repo, short in REPOS:
        if only and only not in (short, repo):
            continue
        parity = fetch(repo, "docs/parity.md", local)
        raw_manifest = fetch(repo, "docs/witnesses.json", local)
        checker = fetch(repo, "scripts/check_witnesses.py", local)
        if parity is None or raw_manifest is None:
            print(f"  ! {short}: could not read its ledger, skipped", file=sys.stderr)
            continue
        skip, heads = checker_rules(checker)
        grades, green_keys = (word_grades(parity) if short == "apim"
                              else emoji_grades(parity, skip, heads))
        manifest = json.loads(raw_manifest)
        rows.append((short, grades, evidence(manifest, green_keys),
                     coverage(manifest, green_keys),
                     uncovered(manifest, green_keys)))
    return rows


def grades_table(rows):
    """Green over the ledger's own total: progress against DECLARED scope.

    The denominator is what the repo set out to reach parity with, gaps
    included — a 🔴 row is an enumerated absence, not a silence — and each
    ledger states separately, under `## Scope boundary`, what it deliberately
    leaves out and why. So this share is honest about intent, and it moves when
    discovery adds rows, which is the design rather than a defect.

    What it cannot see is surface nobody has enumerated yet. For that the
    denominator has to come from Microsoft's published specs, which is a
    different and harsher number this script does not compute.
    """
    out = ["| emulator | green | partial | not implemented | total | reached |",
           "|---|---:|---:|---:|---:|---:|"]
    tot = {"green": 0, "amber": 0, "red": 0}
    for short, g, _, _cov, _gaps in rows:
        for k in tot:
            tot[k] += g[k]
        n = g["green"] + g["amber"] + g["red"]
        share = f"{round(100 * g['green'] / n)}%" if n else "-"
        out.append(f"| {short} | {g['green']} | {g['amber']} | {g['red']} | {n} | "
                   f"**{share}** |")
    grand = sum(tot.values())
    out.append(f"| **family** | **{tot['green']}** | **{tot['amber']}** | "
               f"**{tot['red']}** | **{grand}** | "
               f"**{round(100 * tot['green'] / grand)}%** |")
    return "\n".join(out)


def evidence_table(rows):
    """Claims, classified once each by their strongest witness."""
    out = ["| emulator | green claims | ci: external | sdk: only | own tests only | "
           "independently evidenced |",
           "|---|---:|---:|---:|---:|---:|"]
    for short, _, _k, cov, _gaps in rows:
        total = cov["ci"] + cov["sdk"] + cov["own"]
        indep = cov["ci"] + cov["sdk"]
        share = f"{indep}/{total}" + (f" ({round(100 * indep / total)}%)" if total else "")
        out.append(f"| {short} | {total} | {cov['ci']} | {cov['sdk']} | "
                   f"{cov['own']} | **{share}** |")
    return "\n".join(out)


def main(argv):
    local = None
    if "--local" in argv:
        i = argv.index("--local")
        local = argv[i + 1] if i + 1 < len(argv) else ".."
    # A bare word is a member name: `family_parity.py entra`. Accepts the short
    # name or the repo name, so both `arm` and `arm-emulator` work.
    known = {s for _, s in REPOS} | {r for r, _ in REPOS}
    only = next((a for a in argv if not a.startswith("-") and a in known), None)
    stray = [a for a in argv if not a.startswith("-") and a not in known
             and a != local]
    if stray:
        print(f"unknown member {stray[0]!r}. Known: "
              + ", ".join(sorted(s for _, s in REPOS)))
        return 2
    rows = collect(local, only)
    if not rows:
        print("FAIL: no ledger could be read — network, or a repo moved its docs.")
        return 1

    if only:
        short, grades, _kinds, cov, gaps = rows[0]
        print(member_report(short, grades, cov, gaps))
        return 0
    want_grades = "--evidence" not in argv
    want_evidence = "--grades" not in argv
    if want_grades:
        print("## Parity grades\n")
        print(grades_table(rows))
    if want_grades and want_evidence:
        print()
    if want_evidence:
        print("## Evidence behind the green rows\n")
        print(evidence_table(rows))
        print("\nEach claim is counted once, by its strongest witness. ci: a packaged "
              "external\nclient in CI. sdk: Microsoft's own client, in process. own: our "
              "client on both\nends, so our own reading of the contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
