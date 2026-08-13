# azure-emulators

[![Family chain](https://github.com/calvinchengx/azure-emulators/actions/workflows/chain.yml/badge.svg)](https://github.com/calvinchengx/azure-emulators/actions/workflows/chain.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**📖 [calvinchengx.github.io/azure-emulators](https://calvinchengx.github.io/azure-emulators/)** — quickstart, the family, release coordination, and what the chain test proves.

The Azure emulator family, composed. **This repo runs no emulator of its own** —
it ships no binary, no image, and no Go module. It is the neutral place where
the independently-released emulators are wired together, documented as a family,
and *tested against each other*.

**The point of the family is speed for an AI coding agent.** Proving a data
product, or any Azure-shaped automation, against the real services means a paid
tenant and slow, hard-to-reset round trips. This family lets Claude, Codex, Grok,
whichever agent is doing the work, build and prove the whole thing offline
first, then move to a real tenant with no code changes: months of tenant-bound
iteration becomes a day, or a week. See
[fabric-emulator](https://github.com/calvinchengx/fabric-emulator) for the full
argument, and [contoso-data-platform](https://github.com/calvinchengx/contoso-data-platform)
for what it looks like end to end.

```sh
docker compose up            # entra + keyvault + arm
docker compose --profile fabric up   # …and fabric, the consumer
docker compose --profile apim up     # …or apim, the other one
```

ARM governs the vault, as it does in Azure: role assignments decide who may do
what, and no assignment means no access. The stack seeds what the portal gives
you when you create a vault — the resource, plus a grant for the principal that
created it — so the quickstart works without hand-writing one. To go back to a
vault that allows any authenticated caller:

```sh
KV_ARM_URL= docker compose up
```

## The family

Each emulator lives in its own repo and publishes its own image to GHCR on its
own release cadence. This repo pins and composes them.

| Service | Port | Repo | Role |
|---|---|---|---|
| `entra-emulator` | 8443 | [entra-emulator](https://github.com/calvinchengx/entra-emulator) | **The STS.** Issues every token, publishes the JWKS the others validate against |
| `keyvault-emulator` | 8444 | [azure-keyvault-emulator](https://github.com/calvinchengx/azure-keyvault-emulator) | Key Vault data plane — secrets, keys, certificates |
| `arm-emulator` | 8445 | [arm-emulator](https://github.com/calvinchengx/arm-emulator) | ARM control plane + `Microsoft.Authorization` RBAC |
| `fabric-emulator` | 9443 | [fabric-emulator](https://github.com/calvinchengx/fabric-emulator) | Fabric control plane + OneLake. A **consumer** of the three above, so it sits behind a `fabric` profile |
| `apim-emulator` | 8446 | [azure-apim-emulator](https://github.com/calvinchengx/azure-apim-emulator) | API Management — management plane, gateway, policies. A **consumer** too, behind an `apim` profile, but of entra alone: it serves its own `Microsoft.ApiManagement` ARM surface rather than calling arm |

Not a service in this compose, but part of the family:
[**contoso-data-platform**](https://github.com/calvinchengx/contoso-data-platform)
is the fullest proof the family holds together under real use, four real vendor
sources through a full medallion to a semantic model serving Power BI, running
against a published `fabric-emulator` release and, with one flag, against real
Fabric.

## Why this repo exists

**No single emulator's CI can verify the family.** entra's tests prove entra
issues correct tokens; ARM's tests prove ARM validates *some* issuer. Neither
proves that ARM validates *entra's* tokens, that the advertised issuer matches
the one its peers check, or that the five images boot together in the right
order. That cross-cutting proof has to live somewhere neutral — here.

It also gives the family one canonical answer to "how do I run all of this?"
that doesn't privilege any one repo, and one place to pin a known-good set of
image versions.

## Why separate containers and not one

Every emulator is a static Go binary on `distroless/static-debian12`, so the
images share a base layer and each costs a few tens of MB of RSS. Bundling them
saves essentially nothing — and it would cost the thing that makes the
emulation faithful: **keyvault and arm validating entra's tokens over HTTP
against a separate origin *is* the production trust relationship.** Collapsing
them into one process invites short-circuits (shared memory, direct key access)
that quietly stop resembling Azure.

Separate containers also keep release cadences independent, let you run only
what you need, and isolate failures behind per-service healthchecks.

## The one thing that must line up: issuer alignment

Tokens carry `iss = <entra login origin>/<tenant>/v2.0`. The peers validate
that value, so **entra must *advertise* the origin they check.** On the compose
network that origin is `https://entra-emulator:8443`, which is why the entra
service sets `PUBLIC_ORIGIN` and every `*_ENTRA_ISSUER` repeats it verbatim.

Get this wrong and every call fails with an issuer mismatch — it is the single
most common way these stacks break. Point the `*_ENTRA_ISSUER` variables at a
real tenant instead and nothing else changes.

## Pinning versions

The compose defaults ARE a pinned, certified set — the BOM (see
[Release coordination](#release-coordination-the-bom)). Override any member
per run:

```sh
ENTRA_EMULATOR_VERSION=latest docker compose up -d   # try tomorrow's entra
```

## State

**State persists.** Every service writes its SQLite database to a named volume,
so a directory you seed once is still there tomorrow:

| cycle | state |
|---|---|
| `up` / `down` / `up` | survives |
| `stop` / `start` / `restart` | survives |
| `down -v` | **the reset** |

Wiping is the deliberate act, and `down` alone no longer does it. For a
throwaway stack, set any data directory explicitly empty — that selects
in-memory:

```sh
KV_DATA_DIR= docker compose up
```

`e2e/chain/run.py` takes that path for every service, because a chain that
inherits the previous run's state proves less than it claims. The notes at the
top of [`docker-compose.yml`](docker-compose.yml) carry the detail, including
why entra needs two settings rather than one.

## The chain test

[`e2e/chain/run.py`](e2e/chain/run.py) is the gate this repo exists for. It
brings the **published images** up with `docker compose up --wait`, then proves
the seam:

1. every service reports healthy;
2. entra mints a token per audience (ARM, Key Vault);
3. **arm accepts entra's token** and performs a real write (a resource group);
4. **keyvault authorizes entra's token** on a real data-plane call — 404 and not
   403, so ARM's seeded grant reached the vault rather than merely the token
   being valid;
5. **apim accepts the ARM-audience token** on its own management surface;
6. a **foreign-issuer token is refused** — so steps 3–5 passed because the trust
   chain holds, not because validation is missing.

```sh
./e2e/chain/run.py          # runs the BOM versions, tears down
KEEP_UP=1 ./e2e/chain/run.py    # leave the stack up to poke at
```

It runs under its own compose project on high ports (18443–18446), so it never
collides with a family stack you already have running.

## How much of Azure does the family emulate?

Each emulator grades itself in its own `docs/parity.md` and binds every green
row to a witness in `docs/witnesses.json`. To read all five at once:

```sh
./scripts/family_parity.py              # both tables, markdown
./scripts/family_parity.py --evidence   # just what backs the green rows
```

It reads each repo's published main, and derives its counts using that repo's
own checker rules, so a green count here is the number that repo's gate
reports.

`reached` is green over the ledger's own total: progress against **declared
scope**. The denominator is what that emulator set out to reach parity with,
gaps included, since a 🔴 row is an enumerated absence rather than a silence,
and each ledger says separately under `## Scope boundary` what it leaves out
and why. It moves when discovery adds rows, which is the design. What it cannot
see is surface nobody has enumerated yet; that denominator would have to come
from Microsoft's published specs, and no repo computes it today.

The evidence table counts **claims, not citations**, and classifies each claim
once by its strongest witness: `ci:` a packaged external client in CI, `sdk:`
Microsoft's own client linked in process, otherwise our own client on both ends
of the wire. Citations were the first cut and they flatter, because one claim
can carry several witnesses: keyvault's third-party citations outnumber the
claims they cover by roughly two to one. The share at the end is the honest
headline — how much of what an emulator claims has been proved by something
that is not us. Run the script for the current figures rather than trusting a
number written here, which is exactly the drift this paragraph describes.

## Release coordination: the BOM

The version defaults in [`docker-compose.yml`](docker-compose.yml) —
`${ENTRA_EMULATOR_VERSION:-0.6.0}` and friends — are the family's **bill of
materials**: the newest combination of released images proven to work
together. A bare `docker compose up` runs exactly that set; per-variable
overrides (environment or an uncommitted local `.env`) still win.

CI gives each failure a name:

| job | versions | a red means |
|---|---|---|
| `certified` (push/PR) | the BOM | this repo is broken — blocks merge |
| `drift` (nightly) | `:latest` | an upstream release broke the family — triage; the BOM is the rollback |
| `pins` (push/PR) | — | a consumer repo certifies against a different family than this one |

**To release across the family:**

1. Release the emulator in its own repo — and a release is not done at the
   tag: the GoReleaser run must finish **and** the GHCR image must exist. (The
   Go module proxy can carry a new version for minutes while `:latest` is
   still the previous image.)
2. When the change is breaking, entra goes first — it is upstream of
   everything (`entra → arm → keyvault`, `entra → fabric`) — and its consumers'
   own pins (keyvault's e2e `ENTRA_VERSION`, fabric's fab-driven `.env`,
   each repo's `go.mod`) move next. The `pins` job holds you to this.
3. Open a PR here bumping the compose default. The `certified` job proves the
   new combination before it merges. That merge *is* the family release.

The 2026-08-08 tenant break is the motivating incident: a consumer was swept
to entra's unreleased main, and 12 CI jobs went red hours later on an innocent
commit. Under this scheme the same mistake fails the `pins` job immediately,
named for what it is.

For the *semantics* of ARM→vault authorization (role assignments, access
policies, revocation), see azure-keyvault-emulator's `e2e/arm-chain`, which
covers that in depth from source. This repo deliberately tests the seam, not
the semantics — with one exception it has to make. The stack is ARM-governed,
so `chain` asserts its data-plane probe is *authorized* (404) rather than
merely authenticated (403). Without that, a broken seed would leave the whole
stack denying by default and every test still green.

## Roadmap

- **Renovate/dependabot** — keep the pinned image versions moving.
- **`az` CLI leg** — drive the same chain through the real Azure CLI.

## License

Apache-2.0. This repo composes the family and ships no emulator of its own, so
the compose file here is meant to be copied into your own stack.
