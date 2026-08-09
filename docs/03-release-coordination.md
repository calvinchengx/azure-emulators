# 03 — Release coordination

Five independently released products that must work together. This is how the
family stays consistent without freezing anyone's cadence.

## The BOM

The version defaults in [`docker-compose.yml`](../docker-compose.yml) —
`${ENTRA_EMULATOR_VERSION:-0.4.1}` and friends — **are** the bill of
materials: the newest combination proven to work together. A bare
`docker compose up` runs exactly that set. Never `:latest`; the pins gate
fails if a default reverts to it.

Overrides still work per variable, via the environment or an uncommitted local
`.env`.

## Two jobs, two meanings

A red should always say *whose* problem it is:

| Job | Versions | A red means |
|---|---|---|
| `certified` (push/PR) | the BOM | **this repo is broken** — the certified set no longer composes; blocks merge |
| `drift` (nightly 06:17 UTC) | `:latest` | an **upstream release** broke the family — triage; the BOM is the rollback |
| `pins` (push/PR) | — | a **consumer repo** certifies against a different family than this one |

## Releasing across the family

1. **Release the emulator in its own repo.** A release is not done at the tag:
   the GoReleaser run must finish **and** the GHCR image must exist. The Go
   module proxy can carry a new version for many minutes while `:latest` is
   still the previous image — that skew has produced a red where the Go-based
   suites went green and compose stayed red *on the same commit*.
2. **Breaking changes go in dependency order** — entra first, since it is
   upstream of everything, then its consumers' own pins.
3. **Open a PR here bumping the compose default.** The `certified` job proves
   the new combination before it merges. **That merge is the family release.**

## Why a pins gate exists

The BOM can only police the *image* channel, but the family consumes its
members through three:

| Channel | Who | Fails as |
|---|---|---|
| `go install …@$VERSION` in e2e runners | keyvault's suites | `AADSTS90002: Unknown tenant` |
| `go.mod` library (in-process emulator) | arm, keyvault, fabric | compile or auth failure in Go tests |
| `ghcr.io/…:<tag>` in compose | everyone | `HTTP 404` on discovery |

A breaking change has to land in all three.
[`scripts/check_family_pins.py`](../scripts/check_family_pins.py) reads each
consumer's pins straight from its `main` branch and compares them to the BOM —
release pins as errors, `go.mod` versions as warnings, since a stale library
is that repo's own latent problem rather than a family-compose fault.

### The incident this encodes

On 2026-08-08 a sweep repointed keyvault at a tenant that existed only on
entra's *unreleased* main. Twelve CI jobs went red three hours later, on an
unrelated Dependabot merge — the failing commit was innocent, and the split
between the green Go-library job and the red release-pin jobs is what
identified the real cause. Under this gate the same mistake fails the `pins`
job immediately, named for what it is.

## Waivers

A finding can be acknowledged without blocking, as a dated entry in the pins
script naming the condition that retires it. Waivers are printed on every run,
so they cannot rot silently. There are none outstanding.
