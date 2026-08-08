# azure-emulators

The Azure emulator family, composed. **This repo runs no emulator of its own** —
it ships no binary, no image, and no Go module. It is the neutral place where
the independently-released emulators are wired together, documented as a family,
and *tested against each other*.

```sh
docker compose up            # entra + keyvault + arm
docker compose --profile fabric up   # …and fabric, the consumer
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

## Why this repo exists

**No single emulator's CI can verify the family.** entra's tests prove entra
issues correct tokens; ARM's tests prove ARM validates *some* issuer. Neither
proves that ARM validates *entra's* tokens, that the advertised issuer matches
the one its peers check, or that the four images boot together in the right
order. That cross-cutting proof has to live somewhere neutral — here.

It also gives the family one canonical answer to "how do I run all of this?"
that doesn't privilege any one repo, and one place to pin a known-good set of
image versions.

## Why four containers and not one

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

`latest` by default so the quickstart stays current. Pin a known-good set with:

```sh
ENTRA_EMULATOR_VERSION=0.2.2 \
KEYVAULT_EMULATOR_VERSION=0.1.4 \
ARM_EMULATOR_VERSION=0.1.0 \
  docker compose up -d
```

## State

Nothing mounts a volume, so nothing survives `docker compose down` — see the
notes at the top of [`docker-compose.yml`](docker-compose.yml) for how to add
one, and for the keyvault image's current `/data` ownership caveat.

## The chain test

[`e2e/chain/run.py`](e2e/chain/run.py) is the gate this repo exists for. It
brings the **published images** up with `docker compose up --wait`, then proves
the seam:

1. every service reports healthy;
2. entra mints a token per audience (ARM, Key Vault);
3. **arm accepts entra's token** and performs a real write (a resource group);
4. **keyvault authenticates entra's token** on a real data-plane call;
5. a **foreign-issuer token is refused** — so steps 3–4 passed because the trust
   chain holds, not because validation is missing.

```sh
./e2e/chain/run.py          # pulls :latest, runs, tears down
KEEP_UP=1 ./e2e/chain/run.py    # leave the stack up to poke at
```

It runs under its own compose project on high ports (18443/18444/18445), so it
never collides with a family stack you already have running. CI runs it on every
push **and daily** — the failure mode arrives from outside this repo, when any
of the four publishes a new `:latest`.

For the *semantics* of ARM→vault authorization (role assignments, access
policies, revocation), see azure-keyvault-emulator's `e2e/arm-chain`, which
covers that in depth from source. This repo deliberately tests the seam, not
the semantics.

## Roadmap

- **Renovate/dependabot** — keep the pinned image versions moving.
- **`az` CLI leg** — drive the same chain through the real Azure CLI.
