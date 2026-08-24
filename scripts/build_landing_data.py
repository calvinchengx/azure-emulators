#!/usr/bin/env python3
"""Assemble the landing page's data, and refuse a page that types a number.

This repo's landing page sells the FAMILY, so every headline on it is a number
that moves in some other repo: a member's ledger grows, a BOM default is
bumped, a chain assertion is added. A number typed into a page has no idea any
of that happened, and a stale total on a page whose whole argument is "we do
not overstate what is proved" is the worst kind of wrong.

So the page reads its totals at run time from JSON copied beside it, and this
script FAILS when the page carries a literal where a placeholder belongs, when
a placeholder has lost the file that fills it, or when a manifest this page
depends on has stopped being readable. Copying the JSON is the easy half; the
refusals are the point.

Three manifests, three different repos-worth of truth:

  docker-compose.yml   the BOM. Which emulators the family ships and the exact
                       version of each that this repo's chain test certified.
  the member ledgers   docs/parity.md and docs/witnesses.json in each member
                       repo, read through scripts/family_parity.py so the
                       counts here are the counts each member's own gate
                       reports. Never re-derived, because two derivations of
                       one number disagree eventually and the page loses.
  docs/04-chain-test.md  how many assertions the chain actually makes.

    ./scripts/build_landing_data.py --out _site --landing website/src/pages/index.astro
    ./scripts/build_landing_data.py --out _site --landing website/src/pages/index.astro \
        --local ..     # sibling checkouts instead of the published main
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"
CHAIN_DOC = ROOT / "docs" / "04-chain-test.md"

# Each is (the id the page fills, the file that fills it). A page that keeps
# the id but stops fetching the file shows a dash forever, which is worse than
# a wrong number: nothing looks broken.
BINDINGS = {
    "member-count": "family-bom.json",
    "bom-pinned": "family-bom.json",
    "green-count": "family-parity.json",
    "graded-count": "family-parity.json",
    "partial-count": "family-parity.json",
    "red-count": "family-parity.json",
    "evidenced-count": "family-parity.json",
    "verified-count": "family-parity.json",
    "chain-steps": "chain-assertions.json",
}

# `image: ghcr.io/calvinchengx/<repo>:${<VAR>:-<version>}` is the BOM line. The
# default after `:-` is the certified version; the variable is the override.
IMAGE = re.compile(
    r"image:\s*ghcr\.io/calvinchengx/(?P<repo>[a-z0-9-]+):"
    r"\$\{(?P<var>[A-Z0-9_]+):-(?P<version>[^}]+)\}"
)
SERVICE = re.compile(r"^  (?P<name>[a-z0-9-]+):\s*$")
PROFILE = re.compile(r"^\s*profiles:\s*\[(?P<profile>[a-z0-9, -]+)\]")
PORT = re.compile(r'"\$\{[A-Z0-9_]+_PORT:-(?P<port>\d+)\}:')

# A stat tile states a total as a headline. Its <b> may hold placeholders and
# punctuation; a digit in there is a number somebody typed.
STAT_TILE = re.compile(r'<div class="stat">(.*?)</div>', re.S)
BOLD = re.compile(r"<b\b[^>]*>(.*?)</b>", re.S)
TAGS = re.compile(r"<[^>]+>")


def load_family_parity():
    """Import the sibling script by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "family_parity", ROOT / "scripts" / "family_parity.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_bom() -> list[dict]:
    """The family, as the compose file pins it.

    Ordered as the compose file orders them, which is dependency order: entra
    first because everything validates its tokens.
    """
    members, service, profile, port = [], None, None, None
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        found = SERVICE.match(line)
        if found:
            service, profile, port = found.group("name"), None, None
            continue
        found = PROFILE.match(line)
        if found:
            profile = found.group("profile").split(",")[0].strip()
        found = PORT.search(line)
        if found and members and members[-1]["port"] is None:
            members[-1]["port"] = found.group("port")
        found = IMAGE.search(line)
        if found:
            # `profiles:` precedes `image:` in a service block, so the profile
            # in hand is this service's. Nothing means the default profile,
            # which is what a bare `docker compose up` starts.
            members.append({
                "service": service,
                "repo": found.group("repo"),
                "variable": found.group("var"),
                "version": found.group("version").strip(),
                "port": None,
                "profile": profile,
            })
    return members


def chain_assertions() -> int:
    """The numbered assertions under `## What it asserts`, counted where they
    live rather than repeated here. The doc is the only place they are listed,
    so a step added without a page edit still moves the headline."""
    section, count = False, 0
    for line in CHAIN_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip() == "What it asserts"
            continue
        if section and re.match(r"^\d+\.\s", line):
            count += 1
    return count


def parity(module, local, members) -> dict:
    """Each member's ledger, through the family's own reader.

    Every member must resolve. A partial read would silently shrink the family
    total, and a smaller number on this page looks exactly like honesty, which
    is how a broken fetch survives review.
    """
    rows = module.collect(local, None, module.FAMILY)
    got = {short for short, *_ in rows}
    want = {short for _, short in module.FAMILY}
    if got != want:
        missing = ", ".join(sorted(want - got)) or "none"
        raise SystemExit(
            f"FAIL: could not read the ledgers of: {missing}. The page states a "
            f"family total, and a total assembled from part of the family is a "
            f"smaller number that looks like modesty."
        )
    repos = {repo for repo, _ in module.FAMILY}
    composed = {m["repo"] for m in members}
    if repos != composed:
        raise SystemExit(
            f"FAIL: the BOM composes {sorted(composed)} but family_parity.py grades "
            f"{sorted(repos)}. The page shows one card per member and one grade per "
            f"card; the two lists must be the same list."
        )

    # Keyed by REPO, not by the short name family_parity.py reports. The page
    # names each member by its repository, which is also how the BOM names it
    # and how a reader finds it on GitHub; two spellings of one member is how a
    # card ends up silently unfilled.
    repo_of = {short: repo for repo, short in module.FAMILY}
    out, totals = {}, {"green": 0, "amber": 0, "red": 0, "ci": 0, "sdk": 0, "own": 0}
    kinds: dict[str, int] = {}
    for short, grades, witness_kinds, cov, _gaps in rows:
        out[repo_of[short]] = {
            "short": short,
            "green": grades["green"],
            "amber": grades["amber"],
            "red": grades["red"],
            "total": grades["green"] + grades["amber"] + grades["red"],
            "ci": cov["ci"],
            "sdk": cov["sdk"],
            "own": cov["own"],
        }
        for key in ("green", "amber", "red"):
            totals[key] += grades[key]
        for key in ("ci", "sdk", "own"):
            totals[key] += cov[key]
        for kind, n in witness_kinds.items():
            kinds[kind] = kinds.get(kind, 0) + n
    totals["total"] = totals["green"] + totals["amber"] + totals["red"]
    totals["evidenced"] = totals["ci"] + totals["sdk"]
    return {
        "members": out,
        "totals": totals,
        "witness_kinds": kinds,
        # No member has a witness of this kind today, and 0 is the honest
        # answer rather than a key the page would render as a dash. The day a
        # differential run against a tenant lands anywhere in the family, this
        # page changes without anyone remembering to edit it.
        "verified": kinds.get("verified", 0),
    }


def check_page(text: str, page: pathlib.Path, members: list[dict]) -> str | None:
    """Every refusal, in one place. Returns the reason, or None to proceed."""
    for tile in STAT_TILE.findall(text):
        for bold in BOLD.findall(tile):
            inner = TAGS.sub("", bold)
            if any(ch.isdigit() for ch in inner):
                label = TAGS.sub(" ", tile).strip()[:80]
                return (
                    f"{page} types {inner.strip()!r} into a stat tile ({label!r}). "
                    f"The tiles are filled at run time; a typed number goes stale "
                    f"the day the count moves and nothing looks broken."
                )

    for element, source in BINDINGS.items():
        if f'id="{element}"' not in text:
            return (f"{page} no longer has #{element}, so a headline number would "
                    f"never fill.")
        if source not in text:
            return (f"{page} no longer reads {source}, so #{element} would show a "
                    f"dash forever.")

    carded = set(re.findall(r'data-member="([a-z0-9-]+)"', text))
    composed = {m["repo"] for m in members}
    if carded != composed:
        return (f"{page} shows cards for {sorted(carded)} but the BOM composes "
                f"{sorted(composed)}. A member with no card is a member this page "
                f"hides.")
    for slot in ("data-version", "data-grade", "data-port"):
        found = set(re.findall(slot + r'="([a-z0-9-]+)"', text))
        if found != composed:
            return (f"{page} has {slot} slots for {sorted(found)}, not the composed "
                    f"{sorted(composed)}.")

    for member in members:
        if member["version"] in text:
            return (f"{page} types the version {member['version']} ({member['repo']}). "
                    f"Versions come from the BOM at run time; the BOM is bumped by "
                    f"pull requests that will never think to edit this page.")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--landing", required=True)
    parser.add_argument("--local", default=None,
                        help="read sibling checkouts instead of each member's main")
    args = parser.parse_args()

    page = pathlib.Path(args.landing)
    if not page.exists():
        print(f"FAIL: {page} does not exist.")
        return 1
    text = page.read_text(encoding="utf-8")

    members = read_bom()
    if not members:
        print(f"FAIL: no pinned images parsed from {COMPOSE.relative_to(ROOT)}. The "
              f"BOM is what the page counts; an empty read would publish a zero.")
        return 1
    unpinned = [m["service"] for m in members if not re.match(r"^\d", m["version"])]
    if unpinned:
        print(f"FAIL: {', '.join(unpinned)} is not pinned to a version in the BOM "
              f"({COMPOSE.relative_to(ROOT)}); the page claims every member is.")
        return 1

    steps = chain_assertions()
    if steps < 2:
        print(f"FAIL: parsed {steps} assertions from "
              f"{CHAIN_DOC.relative_to(ROOT)}; the section moved or was renamed.")
        return 1

    reason = check_page(text, page, members)
    if reason:
        print(f"FAIL: {reason}")
        return 1

    module = load_family_parity()
    grades = parity(module, args.local, members)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "family-bom.json").write_text(json.dumps(
        {"members": members, "count": len(members),
         "pinned": sum(1 for m in members if m["version"])},
        indent=2) + "\n", encoding="utf-8")
    (out / "family-parity.json").write_text(
        json.dumps(grades, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "chain-assertions.json").write_text(
        json.dumps({"assertions": steps}, indent=2) + "\n", encoding="utf-8")

    totals = grades["totals"]
    print(
        f"landing data: {len(members)} members pinned, "
        f"{totals['green']}/{totals['total']} graded green, "
        f"{totals['evidenced']} of those with a third-party witness, "
        f"{steps} chain assertions, {grades['verified']} verified against Azure"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
