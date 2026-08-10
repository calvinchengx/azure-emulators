#!/usr/bin/env python3
"""Do the consumer repos' pins agree with the BOM?

The BOM is docker-compose.yml's version defaults — `${X_VERSION:-N.N.N}` —
which certify one combination of released images. But the
family consumes entra (and arm) through more channels than the image: keyvault's
e2e runners `go install` a pinned release, fabric's fab-driven example pins an
image tag of its own, contoso-data-platform pins the whole stack in a
versions.env, and three repos link entra as a go.mod library for their
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
for var in ("ENTRA", "KEYVAULT", "ARM", "FABRIC", "APIM"):
    if f"{var}_EMULATOR_VERSION" not in BOM:
        sys.exit(f"docker-compose.yml has no ${{{var}_EMULATOR_VERSION:-…}} default — "
                 "the BOM must pin every service (never :latest)")

ENTRA = BOM["ENTRA_EMULATOR_VERSION"]
ARM = BOM["ARM_EMULATOR_VERSION"]
KEYVAULT = BOM["KEYVAULT_EMULATOR_VERSION"]
FABRIC = BOM["FABRIC_EMULATOR_VERSION"]

# ------------------------------------------------------------- the sources --
# (repo, path, regex, BOM version, tier). The regex's group(1) is the pin;
# leading 'v' is normalized away before comparing.
# A pinned family image: `ghcr.io/calvinchengx/<name>:${<VAR>:-N.N.N}`.
ENTRA_IMAGE = r"entra-emulator:\$\{ENTRA_EMULATOR_VERSION:-([\d.]+)\}"
ARM_IMAGE = r"arm-emulator:\$\{ARM_EMULATOR_VERSION:-([\d.]+)\}"
KEYVAULT_IMAGE = r"azure-keyvault-emulator:\$\{KEYVAULT_EMULATOR_VERSION:-([\d.]+)\}"
FABRIC_IMAGE = r"fabric-emulator:\$\{FABRIC_EMULATOR_VERSION:-([\d.]+)\}"
# A pin in an env file read by `docker compose --env-file`: `<VAR>=N.N.N`.
ENTRA_ENV = r"^ENTRA_EMULATOR_VERSION=([\d.]+)"
KEYVAULT_ENV = r"^KEYVAULT_EMULATOR_VERSION=([\d.]+)"
FABRIC_ENV = r"^FABRIC_EMULATOR_VERSION=([\d.]+)"

# Every fabric compose that stands an entra up. Listed rather than globbed
# because this script reads the consumer over HTTP and cannot walk its tree; a
# NEW suite therefore has to be added here, and the "pin pattern not found"
# error is what catches one of these being renamed or dropped.
# examples/fab-driven is deliberately absent: its compose uses
# ${ENTRA_EMULATOR_VERSION:?see .env}, so the pin lives in that .env, which is
# already checked above. Checking both would double-report one pin.
FABRIC_COMPOSES = (
    "docker-compose.yml",
    "e2e/notebook-run/docker-compose.jvm.yml",
    *[f"e2e/{suite}/docker-compose.yml" for suite in (
        "airflow", "azurite-shortcut", "data-science-loop", "dbt-fabric",
        "dbt-fabricspark", "deployment-pipelines", "environment",
        "external-shortcuts", "fabric-cli", "livy", "medallion",
        "notebook-driven", "notebook-run", "rest-helix", "rest-servicenow",
        "rti", "s3", "sail", "salesforce", "spark", "spark-jvm",
        "vscode-extension",
    )],
)

PINS = [
    # keyvault's e2e runners `go install` these releases and run them.
    *[("azure-keyvault-emulator", f"e2e/{suite}/run.py",
       r'"ENTRA_VERSION",\s*"(v[\d.]+)"', ENTRA, "error")
      for suite in ("sdk", "chain", "az-cli", "arm-chain")],
    # arm's az-cli witness does the same: on a CI runner there is no sibling
    # checkout, so it `go install`s this entra release and the real Azure CLI
    # authenticates against it. A stale pin here would quietly witness arm's
    # parity claims against a retired entra.
    # The pin used to sit in e2e/az-cli/run.py; arm has since lifted its emulator
    # plumbing into e2e/emulators.py, which every suite imports, so the literal
    # moved with it. Watch it where it lives — pointing at the old file made this
    # gate report "pin pattern not found", which is the loud failure the manifest
    # comment above promises, and the wrong repair would have been to drop the
    # entry and stop checking arm at all.
    ("arm-emulator", "e2e/emulators.py",
     r'ENTRA_VERSION = os\.environ\.get\("ENTRA_VERSION", "(v[\d.]+)"\)',
     ENTRA, "error"),
    *[("azure-keyvault-emulator", f"e2e/{suite}/run.py",
       r'"ARM_VERSION",\s*"(v[\d.]+)"', ARM, "error")
      for suite in ("az-cli", "arm-chain")],
    # fabric's fab-driven example pins entra's image the same way this repo does.
    ("fabric-emulator", "examples/fab-driven/.env", ENTRA_ENV, ENTRA, "error"),
    # contoso-data-platform is a CONSUMER, not a family member: it is what a
    # reader writes from the published docs, and it runs the images rather than
    # building them. That makes it the one place family drift shows up as a
    # user would meet it — and until 2026-08-09 it was unwatched, which is how
    # its keyvault pin sat three releases behind without anything saying so.
    ("contoso-data-platform", "versions.env", KEYVAULT_ENV, KEYVAULT, "error"),
    ("contoso-data-platform", "versions.env", ENTRA_ENV, ENTRA, "error"),
    ("contoso-data-platform", "versions.env", FABRIC_ENV, FABRIC, "error"),
    # go.mod libraries: in-process entra for each repo's own tests. apim joins
    # here and NOT above: it publishes an image this BOM pins, but it consumes
    # no family image itself — it serves its own Microsoft.ApiManagement ARM
    # surface rather than calling arm, and its Key Vault support is modelled
    # references, not calls. entra is its one real dependency, and go.mod is
    # where that dependency is pinned.
    *[(repo, "go.mod",
       r'github\.com/calvinchengx/entra-emulator (v[\d.]+)', ENTRA, "warn")
      for repo in ("arm-emulator", "azure-keyvault-emulator", "fabric-emulator",
                   "azure-apim-emulator")],
    # Compose image tags. Until 2026-08-09 these were :latest, so an upstream
    # release reached a consumer's CI the moment it published rather than when
    # that repo chose to adopt it — the drift this repo's nightly job is meant
    # to absorb centrally. They now carry the same ${X:-N.N.N} default this
    # BOM does, which makes them checkable, so they are checked.
    ("arm-emulator", "docker-compose.yml", ENTRA_IMAGE, ENTRA, "error"),
    ("azure-keyvault-emulator", "docker-compose.yml", ENTRA_IMAGE, ENTRA, "error"),
    ("azure-keyvault-emulator", "docker-compose.yml", FABRIC_IMAGE, FABRIC, "error"),
    # keyvault v0.6.0 stands arm up in its base compose, so that stack now
    # certifies an arm version too. It went in as ARM_IMAGE_TAG and was
    # therefore invisible here; renamed in keyvault #19 so it is checkable.
    ("azure-keyvault-emulator", "docker-compose.yml", ARM_IMAGE, ARM, "error"),
    *[("fabric-emulator", path, ENTRA_IMAGE, ENTRA, "error")
      for path in FABRIC_COMPOSES],
    *[("fabric-emulator", path, KEYVAULT_IMAGE, KEYVAULT, "error")
      for path in ("docker-compose.yml", "e2e/medallion/docker-compose.yml")],
]


# Waivers: findings acknowledged but deliberately not blocking, each with the
# reason and the condition that retires it. A waiver is visible in every run —
# the point is to keep the gate honest without freezing another repo's
# in-flight work. Remove the entry the moment the condition is met.
# (The founding example — fab-driven pinned 0.3.0 behind fabric's #113 revert —
# retired 2026-08-09 when fabric #120 re-landed the migration. The second pair
# — contoso's entra and fabric pins — retired the same day, and taught the
# lesson below: BOTH were one blocker, not two. `fabric_target`, the wheel that
# resolves the target, hardcodes the seeded tenant and client id, and it ships
# with FABRIC_EMULATOR_VERSION. So entra could not move until fabric did, and
# the entra waiver's stated reason — a compose migration — was the visible half
# of a cause that lived in another repo's release artifact. A waiver naming the
# wrong blocker is worse than a loud failure: it tells the next reader the work
# is understood.)
#
# Keyed by (repo, path, pattern) rather than (repo, path), which matters for any
# consumer pinning several images in ONE file: a file-level key waives every pin
# in that file, including the ones still enforceable. contoso was the case that
# forced it — three pins, two blocked, one current.
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
                if tier == "error" and (repo, path, pattern) in WAIVERS:
                    warnings.append(f"{msg} [WAIVED: {WAIVERS[repo, path, pattern]}]")
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
