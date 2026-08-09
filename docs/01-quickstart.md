# 01 — Quickstart

The whole family, one command. Nothing to install but Docker.

```sh
docker compose up
```

That is **entra + arm + keyvault**, wired to each other and governed the way
Azure governs: ARM decides who may read a secret, and the vault enforces it.

| Service | Host port | What it is |
|---|---|---|
| `entra-emulator` | 8443 | the STS — issues every token, publishes the JWKS |
| `arm-emulator` | 8445 | ARM control plane + `Microsoft.Authorization` RBAC |
| `keyvault-emulator` | 8444 | Key Vault data plane — secrets, keys, certificates |

The consumers are profiled, because most callers do not need them and both
images are large:

```sh
docker compose --profile fabric up   # …adds fabric-emulator  :9443
docker compose --profile apim up     # …adds apim-emulator    :8446
```

## Get a token and use it

The seeded daemon app is a confidential client with a known secret — the same
identity the chain test authenticates as.

```sh
TENANT=6f89cf12-978b-4d23-ac18-9ef0c127cf87
TOKEN=$(curl -sk -X POST "https://localhost:8443/$TENANT/oauth2/v2.0/token" \
  -d grant_type=client_credentials \
  -d client_id=00d88624-f0d7-46f6-a641-6232c2608928 \
  -d client_secret=daemon-app-secret \
  -d scope=https://management.azure.com/.default | jq -r .access_token)

curl -sk -H "Authorization: Bearer $TOKEN" \
  "https://localhost:8445/subscriptions/6082bfda-63d0-46f4-8272-ae9195139feb/resourcegroups?api-version=2021-04-01"
```

Every service uses a self-signed certificate, hence `-k`. Point a real Azure
SDK at these origins and it works unmodified — that is the point of the
family.

## ARM governs the vault

`docker compose up` gives you the Azure-shaped posture: **no role assignment
means no access**. A one-shot `arm-seed` service grants the seeded daemon app
on the vault at startup, mirroring how Azure grants the principal that creates
a resource — without it the stack would stand up a vault that refuses its own
quickstart.

To opt out and get the permissive standalone vault instead:

```sh
KV_ARM_URL= docker compose up
```

The empty value is deliberate — the compose file uses `${KV_ARM_URL-…}`
(single dash), so *explicitly empty* opts out while *unset* gets the default.

## Ports are overridable

Every port is a variable, so the family can coexist with anything already
bound:

```sh
ENTRA_PORT=18443 KEYVAULT_PORT=18444 ARM_PORT=18445 docker compose up
```

## Nothing persists

No service mounts a volume, so nothing outlives `docker compose down`. Each
image bakes a `<PREFIX>_DATA_DIR=/data` for callers who *do* mount one; this
file pins it back to empty so the stack runs fully in memory. See the notes at
the top of [`docker-compose.yml`](../docker-compose.yml) to persist instead.
