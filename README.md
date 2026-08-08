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

## Roadmap

- **`e2e/chain/`** — the cross-emulator smoke test this repo is for: acquire a
  token from entra, read a secret from keyvault, create a resource + role
  assignment in arm, all with one unmodified Azure SDK client.
- **CI** — run that chain against the published images on a schedule and on
  every version bump, so a breaking change in any one emulator surfaces here.
- **Renovate/dependabot** — keep the pinned image versions moving.
