#!/usr/bin/env python3
"""Do the consumer repos' pins agree with the BOM?

The BOM is docker-compose.yml's version defaults — `${X_VERSION:-N.N.N}` —
which certify one combination of released images. But the
family consumes entra (and arm) through more channels than the image: keyvault's
e2e runners `go install` a pinned release, fabric's fab-driven example pins an
image tag of its own, contoso-fabric-platform pins the whole stack in a
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
    ./scripts/check_family_pins.py --self-test  # prove a wrong repo name fails
"""

import fnmatch
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = "https://raw.githubusercontent.com/calvinchengx/{repo}/main/{path}"
TREE = "https://api.github.com/repos/calvinchengx/{repo}/git/trees/main?recursive=1"

# ---------------------------------------------------------------- the BOM ---
# The compose file's interpolation defaults ARE the BOM: `${X_VERSION:-N.N.N}`.
BOM = dict(re.findall(
    r"\$\{(\w+_EMULATOR_VERSION):-([\d.]+)\}",
    (ROOT / "docker-compose.yml").read_text(),
))
for var in ("ENTRA", "KEYVAULT", "ARM", "FABRIC", "APIM", "DATABRICKS"):
    if f"{var}_EMULATOR_VERSION" not in BOM:
        sys.exit(f"docker-compose.yml has no ${{{var}_EMULATOR_VERSION:-…}} default — "
                 "the BOM must pin every service (never :latest)")

ENTRA = BOM["ENTRA_EMULATOR_VERSION"]
ARM = BOM["ARM_EMULATOR_VERSION"]
KEYVAULT = BOM["KEYVAULT_EMULATOR_VERSION"]
FABRIC = BOM["FABRIC_EMULATOR_VERSION"]
DATABRICKS = BOM["DATABRICKS_EMULATOR_VERSION"]

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
DATABRICKS_ENV = r"^DATABRICKS_EMULATOR_VERSION=([\d.]+)"
# fabric's two COMPUTE SIDECARS. They are published by fabric's own release
# workflow and tagged with fabric's release number, so they belong to the
# FABRIC row of the BOM even though their variables are named for what they
# contain rather than for the emulator.
SAIL_ENV = r"^SAIL_VERSION=([\d.]+)"
SPARK_AGENT_ENV = r"^SPARK_AGENT_VERSION=([\d.]+)"

# Every fabric compose that stands an entra up. Listed rather than globbed
# because this script reads the consumer over HTTP and cannot walk its tree; a
# NEW suite therefore has to be added here, and the "pin pattern not found"
# error is what catches one of these being renamed or dropped.
# examples/fab-driven is deliberately absent: its compose uses
# ${ENTRA_EMULATOR_VERSION:?see .env}, so the pin lives in that .env, which is
# already checked above. Checking both would double-report one pin.
# Every compose file fabric ships, DISCOVERED rather than listed. This used to
# be 24 hand-written paths, and a hand-written list fails in exactly one
# direction: silently, the moment somebody adds a suite. It missed six files —
# az-rest and the two conformance overlays had sat on entra 0.6.0 and 0.7.0
# while the BOM moved to 0.8.1, and eventstream, terraform-fabric and
# examples/fab-driven were correct only by luck, because nothing was checking.
#
# A glob covers a new suite the day it lands. The cost is one git-trees call
# per globbed repo; GITHUB_TOKEN is used when present so CI does not spend the
# unauthenticated rate limit.
# NOT "**/…": fnmatch's * already crosses "/", so a leading "**/" only
# forces at least one directory and would drop the ROOT docker-compose.yml
# — the single most important file in the list it replaces.
FABRIC_COMPOSES = ("*docker-compose*.yml",)

PINS = [
    # keyvault's e2e runners `go install` these releases and run them.
    *[("azure-keyvault-emulator", f"e2e/{suite}/run.py",
       r'"ENTRA_VERSION",\s*"(v[\d.]+)"', ENTRA, "error")
      for suite in ("sdk", "chain", "az-cli", "arm-chain")],
    # arm's suites do the same: on a CI runner there is no sibling checkout, so
    # they `go install` this entra release and the real Azure CLI authenticates
    # against it. A stale pin here would quietly witness arm's parity claims
    # against a retired entra.
    #
    # The pin used to sit in e2e/az-cli/run.py; arm has since hoisted the
    # bring-up into e2e/emulators.py, which every arm suite imports. So this is
    # not merely the same pin at a new path — it is now the ONE pin governing
    # all of them, which is why the entry stayed singular after the move.
    ("arm-emulator", "e2e/emulators.py",
     r'"ENTRA_VERSION",\s*"(v[\d.]+)"', ENTRA, "error"),
    *[("azure-keyvault-emulator", f"e2e/{suite}/run.py",
       r'"ARM_VERSION",\s*"(v[\d.]+)"', ARM, "error")
      for suite in ("az-cli", "arm-chain")],
    # fabric's ARM-capacities harness go-installs this arm release on CI
    # runners (no sibling checkout). A stale pin would witness FABRIC_ARM_URL
    # against an arm that does not serve Microsoft.Fabric/capacities.
    ("fabric-emulator", "e2e/arm-capacities/run.py",
     r'"ARM_VERSION",\s*"(v[\d.]+)"', ARM, "error"),
    # fabric's fab-driven example pins entra's image the same way this repo does.
    ("fabric-emulator", "examples/fab-driven/.env", ENTRA_ENV, ENTRA, "error"),
    # fabric-platform-notebook-pipelines (named contoso-fabric-platform until the
    # platform repos were split by orchestrator) is a CONSUMER, not a family
    # member: it is what a reader writes from the published docs, and it runs
    # the images rather than building them. The old name still resolves through
    # GitHub's rename redirect, which is precisely why it had to be changed
    # here deliberately — a redirect is not a guarantee, and it lapses the day
    # anything else claims that name. That makes it the one place family drift shows up as a
    # user would meet it — and until 2026-08-09 it was unwatched, which is how
    # its keyvault pin sat three releases behind without anything saying so.
    ("fabric-platform-notebook-pipelines", "versions.env", KEYVAULT_ENV, KEYVAULT, "error"),
    ("fabric-platform-notebook-pipelines", "versions.env", ENTRA_ENV, ENTRA, "error"),
    ("fabric-platform-notebook-pipelines", "versions.env", FABRIC_ENV, FABRIC, "error"),
    # The same file's two COMPUTE SIDECAR pins, added 2026-08-16 after a bump
    # that this gate passed while shipping a skew. fabric-emulator-sail and
    # fabric-emulator-spark-agent are built by fabric's release workflow and
    # tagged with fabric's release number, and versions.env says outright that
    # they "move in lockstep" — but only FABRIC_EMULATOR_VERSION was watched,
    # so bumping fabric alone left the stack running new API against old
    # compute with every pin reporting green.
    #
    # That is the failure this gate exists to prevent, in the file it was
    # written for: not a stale pin it reported, but a stale pin it could not
    # see. Found by grepping versions.env for stragglers, not by the gate.
    # Ordinary drift is survivable; this one is not, because the fabric
    # 0.25.0 -> 0.27.0 range moved Sail to 0.7.0 with its paired Connect
    # client and changed the shared agent's SQL handling, so the two halves
    # disagree about the engine they are talking to.
    ("fabric-platform-notebook-pipelines", "versions.env", SAIL_ENV, FABRIC, "error"),
    ("fabric-platform-notebook-pipelines", "versions.env", SPARK_AGENT_ENV, FABRIC, "error"),
    # databricks-platform-jobs, the Databricks-side twin of the platform above.
    # Added with the 0.2.5 bump because the BOM's databricks row had NO consumer
    # rule at all: every other row was cross-checked against somebody, so a
    # stale databricks pin was the one kind of drift this gate structurally
    # could not report. It sat at 0.2.4 against a released 0.2.5 and the run
    # was green.
    #
    # It pins fabric's two compute sidecars as well, and against the FABRIC row
    # rather than a databricks one — the engine emulator is Databricks, but the
    # Spark underneath it is the one fabric's release workflow builds, so a
    # fabric release moves this platform too.
    ("databricks-platform-jobs", "versions.env", DATABRICKS_ENV, DATABRICKS, "error"),
    ("databricks-platform-jobs", "versions.env", ENTRA_ENV, ENTRA, "error"),
    ("databricks-platform-jobs", "versions.env", KEYVAULT_ENV, KEYVAULT, "error"),
    ("databricks-platform-jobs", "versions.env", SAIL_ENV, FABRIC, "error"),
    ("databricks-platform-jobs", "versions.env", SPARK_AGENT_ENV, FABRIC, "error"),
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
    # apim names its file compose.yaml, so every path above missed it and its
    # entra pin sat on 0.4.1 while the BOM moved to 0.8.1.
    ("azure-apim-emulator", "*compose*.y*ml", ENTRA_IMAGE, ENTRA, "error"),
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
#
# fabric-emulator#250 (entra 0.8.1 compose pins) retired the last standing
# waiver; keep this empty until the next acknowledged mismatch.
WAIVERS = {}


def tree_paths(repo, _cache={}):
    """Every file path on the repo's main, from the git trees API."""
    if repo in _cache:
        return _cache[repo]
    req = urllib.request.Request(TREE.format(repo=repo),
                                 headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    if data.get("truncated"):
        raise SystemExit(f"{repo}: git tree came back truncated; the glob would "
                         "silently under-report, which is the failure this replaced")
    _cache[repo] = [e["path"] for e in data.get("tree", []) if e["type"] == "blob"]
    return _cache[repo]


def expand(pins):
    """Turn glob paths into one entry per real file.

    A globbed entry whose pattern is absent is NOT an error: a compose file
    that pins no entra image simply is not about entra. An ENUMERATED entry
    keeps the opposite rule, because there a missing pattern means the file
    moved and the check has stopped checking.
    """
    out = []
    for repo, path, pattern, want, tier in pins:
        if "*" not in path:
            out.append((repo, path, pattern, want, tier, False))
            continue
        matches = sorted(p for p in tree_paths(repo) if fnmatch.fnmatch(p, path))
        if not matches:
            raise SystemExit(f"{repo}: glob {path!r} matched no file — a glob that "
                             "matches nothing checks nothing")
        out.extend((repo, m, pattern, want, tier, True) for m in matches)
    return out


def fetch(url):
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read().decode()
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(3)


def pin_label(pattern):
    """The variable a pattern matches, for the report.

    One file can hold several watched pins — contoso's versions.env holds five —
    and `repo/path` alone cannot tell them apart. Before this, three stale pins
    in one file printed three identical FAIL lines, which says something is
    wrong without saying what to edit. The variable name is already in the
    pattern; this lifts it out rather than making every entry carry a label by
    hand, because a hand-written label is a second copy of a name that can
    disagree with the regex beside it.
    """
    m = re.search(r"[A-Z][A-Z0-9_]{2,}", pattern)
    return m.group(0) if m else None


def evaluate(pins):
    """Return (errors, warnings) for a manifest. Split out of main() so the
    self-test can run the same code over a deliberately broken entry."""
    errors, warnings = [], []
    for repo, path, pattern, want, tier, globbed in expand(pins):
        label = pin_label(pattern)
        where = f"{repo}/{path}" + (f" ({label})" if label else "")
        try:
            text = fetch(RAW.format(repo=repo, path=path))
        except urllib.error.URLError as e:
            (errors if tier == "error" else warnings).append(
                f"{where}: unfetchable ({e}) — fix the manifest in this script if it moved")
            continue
        found = re.findall(pattern, text, re.M)
        if not found:
            if globbed:
                continue
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

    return errors, warnings


def self_test():
    """Prove the gate FAILS when a manifest entry points nowhere.

    This rename is why the assertion is worth having. GitHub redirects renamed
    repositories, so the stale `contoso-data-platform` entry kept answering 200
    and the gate kept passing — the manifest named a repo that no longer
    existed and nothing said so. The failure would have arrived whenever the
    redirect lapsed, long after the change that caused it.

    So the test that matters is not "the new name resolves" (the old one does
    too, which is the problem). It is: does the gate still FAIL when a name is
    genuinely wrong? Runs the real code path against a repo that cannot exist,
    exercising the `unfetchable` branch rather than trusting that it works.
    """
    bogus = [("calvinchengx-no-such-repo-0000", "versions.env",
              ENTRA_ENV, ENTRA, "error")]
    errors, _ = evaluate(bogus)
    if not errors:
        print("SELF-TEST FAIL: a manifest entry pointing at a nonexistent repo "
              "produced no error. The gate would silently pass a moved repo.")
        return 1
    if "unfetchable" not in errors[0]:
        print(f"SELF-TEST FAIL: expected an 'unfetchable' error, got: {errors[0]}")
        return 1
    print(f"self-test ok: a missing repo is an error\n  {errors[0][:96]}")

    # fabric's compute sidecars stay watched. Offline on purpose: this asserts
    # the MANIFEST, not the network, so it cannot be skipped by an outage.
    #
    # Worth a permanent assertion rather than a one-off check, because the
    # failure it guards against is invisible: delete these two rows and every
    # run still says "All release pins match the BOM" while a consumer runs new
    # fabric against old compute. A gate that cannot fail is indistinguishable
    # from a gate that passes.
    watched = {pin_label(p) for _, path, p, _, _ in PINS if path == "versions.env"}
    for var in ("SAIL_VERSION", "SPARK_AGENT_VERSION"):
        if var not in watched:
            print(f"SELF-TEST FAIL: {var} is no longer watched in versions.env. "
                  "It carries fabric's release number and moves in lockstep with "
                  "FABRIC_EMULATOR_VERSION; unwatched, a bump ships a version skew "
                  "with every pin reporting green.")
            return 1
    print("self-test ok: fabric's sail and spark-agent sidecars are watched")

    # And the labels the report leans on actually resolve, or every FAIL in a
    # multi-pin file becomes an unactionable duplicate line again.
    if pin_label(SAIL_ENV) != "SAIL_VERSION" or pin_label(FABRIC_IMAGE) != "FABRIC_EMULATOR_VERSION":
        print("SELF-TEST FAIL: pin_label no longer extracts the variable name.")
        return 1
    print("self-test ok: report labels resolve")
    return 0


def main():
    if "--self-test" in sys.argv:
        return self_test()
    errors, warnings = evaluate(PINS)
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
