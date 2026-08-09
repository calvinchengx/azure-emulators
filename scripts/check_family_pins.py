#!/usr/bin/env python3
"""Do the consumer repos' pins agree with the BOM?

The BOM is docker-compose.yml's version defaults — `${X_VERSION:-N.N.N}` —
which certify one combination of released images. But the
family consumes entra (and arm) through more channels than the image: keyvault's
e2e runners `go install` a pinned release, fabric's fab-driven example pins an
image tag of its own, and three repos link entra as a go.mod library for their
in-process tests. A breaking upstream release has to land in ALL of them, and
2026-08-08 showed what happens when it doesn't: keyvault was swept to a tenant
no released entra seeded, and 12 CI jobs went red on an unrelated commit three
hours later.

This check reads the BOM, fetches each consumer's pins from its main branch on
GitHub, and compares. Two tiers, deliberately different:

  ERROR — release pins: a *released artifact* is installed and run as part of
          the family trust chain (e2e `go install` pins, image-tag pins).
          These MUST match the BOM; a mismatch means some consumer certifies
          against a different family than this repo does.

  WARN  — go.mod library versions: the emulator linked in-process for that
          repo's own tests. A stale one is that repo's latent problem, not a
          family-compose fault, so it is named loudly but does not fail the
          run. (arm@entra-v0.3.1 is the standing example: it breaks the moment
          arm's tests adopt the new tenant, and this warning is the reminder.)

Stdlib-only, like the family's other scripts.

    ./scripts/check_family_pins.py            # exit 1 on any ERROR-tier mismatch
"""

import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = "https://raw.githubusercontent.com/calvinchengx/{repo}/main/{path}"

# ---------------------------------------------------------------- the BOM ---
# The compose file's interpolation defaults ARE the BOM: `${X_VERSION:-N.N.N}`.
BOM = dict(re.findall(
    r"\$\{(\w+_EMULATOR_VERSION):-([\d.]+)\}",
    (ROOT / "docker-compose.yml").read_text(),
))
for var in ("ENTRA", "KEYVAULT", "ARM", "FABRIC"):
    if f"{var}_EMULATOR_VERSION" not in BOM:
        sys.exit(f"docker-compose.yml has no ${{{var}_EMULATOR_VERSION:-…}} default — "
                 "the BOM must pin every service (never :latest)")

ENTRA = BOM["ENTRA_EMULATOR_VERSION"]
ARM = BOM["ARM_EMULATOR_VERSION"]

# ------------------------------------------------------------- the sources --
# (repo, path, regex, BOM version, tier). The regex's group(1) is the pin;
# leading 'v' is normalized away before comparing.
PINS = [
    # keyvault's e2e runners `go install` these releases and run them.
    *[("azure-keyvault-emulator", f"e2e/{suite}/run.py",
       r'"ENTRA_VERSION",\s*"(v[\d.]+)"', ENTRA, "error")
      for suite in ("sdk", "chain", "az-cli", "arm-chain")],
    *[("azure-keyvault-emulator", f"e2e/{suite}/run.py",
       r'"ARM_VERSION",\s*"(v[\d.]+)"', ARM, "error")
      for suite in ("az-cli", "arm-chain")],
    # fabric's fab-driven example pins entra's image the same way this repo does.
    ("fabric-emulator", "examples/fab-driven/.env",
     r'^ENTRA_EMULATOR_VERSION=([\d.]+)', ENTRA, "error"),
    # go.mod libraries: in-process entra for each repo's own tests.
    *[(repo, "go.mod",
       r'github\.com/calvinchengx/entra-emulator (v[\d.]+)', ENTRA, "warn")
      for repo in ("arm-emulator", "azure-keyvault-emulator", "fabric-emulator")],
]


# Waivers: findings acknowledged but deliberately not blocking, each with the
# reason and the condition that retires it. A waiver is visible in every run —
# the point is to keep the gate honest without freezing another repo's
# in-flight work. Remove the entry the moment the condition is met.
# (The founding example — fab-driven pinned 0.3.0 behind fabric's #113 revert —
# retired 2026-08-09 when fabric #120 re-landed the migration.)
WAIVERS = {}


def fetch(url):
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read().decode()
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(3)


def main():
    errors, warnings = [], []
    for repo, path, pattern, want, tier in PINS:
        where = f"{repo}/{path}"
        try:
            text = fetch(RAW.format(repo=repo, path=path))
        except urllib.error.URLError as e:
            (errors if tier == "error" else warnings).append(
                f"{where}: unfetchable ({e}) — fix the manifest in this script if it moved")
            continue
        found = re.findall(pattern, text, re.M)
        if not found:
            (errors if tier == "error" else warnings).append(
                f"{where}: pin pattern not found — fix the manifest in this script if it changed")
            continue
        for pin in found:
            if pin.lstrip("v") != want.lstrip("v"):
                msg = f"{where}: pins {pin}, BOM says {want}"
                if tier == "error" and (repo, path) in WAIVERS:
                    warnings.append(f"{msg} [WAIVED: {WAIVERS[repo, path]}]")
                elif tier == "error":
                    errors.append(msg)
                else:
                    warnings.append(msg)
            else:
                print(f"  ok    {where}: {pin}")

    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  FAIL  {e}")
    if errors:
        print(f"\n{len(errors)} release pin(s) disagree with the BOM.")
        return 1
    print(f"\nAll release pins match the BOM"
          + (f" ({len(warnings)} library-version warning(s) above)." if warnings else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
