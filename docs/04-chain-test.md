# 04 — The chain test

The one check no single emulator's repo can make.

Each project already tests itself, and keyvault's `e2e/arm-chain` proves the
ARM→vault authorization semantics in depth — but all of those build from a
source tree. Nothing else verifies that the **released container images**,
wired by this repo's compose file, actually trust each other.

```sh
./e2e/chain/run.py              # runs the BOM versions, tears down
KEEP_UP=1 ./e2e/chain/run.py    # leave the stack up to poke at
```

It runs under its own compose project on high ports (18443–18447), so it never
collides with a family stack you already have running. Stdlib-only Python,
like the family's other e2e scripts.

## What it asserts

1. every service reports healthy (`docker compose up --wait`);
2. entra mints a token per audience — ARM and Key Vault;
3. **arm accepts entra's token** and performs a real write (a resource group);
4. **keyvault authorizes entra's token** on a real data-plane call;
5. **apim accepts the ARM-audience token** on its own management surface;
6. **fabric accepts a Fabric-audience token** on `/v1/workspaces`;
7. **an ARM-created Fabric capacity appears** on fabric `GET /v1/capacities`;
8. **databricks accepts its seeded PAT** on `/Me` and refuses `token=dev`;
9. **entra mints a Databricks-audience token** (`2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`)
   and databricks accepts it on `/Me`;
10. **fabric submits a `DatabricksSparkPython` activity** against this host
    (`dbfs:/jobs/chain.py`). The job exists on databricks. Failed naming the
    missing Spark engine is an honest pass — family compose has no Spark sidecar;
11. a **foreign-issuer token is refused** — so steps 3–10 passed because the
    trust chain holds, not because validation is absent.

Step 11 is what makes the rest mean anything. Without it, an emulator that
skipped validation entirely would sail through steps 3–10.

## Reading a failure

Step 4 distinguishes three outcomes, and the distinction is the whole value of
the step:

| Response | Meaning |
|---|---|
| `401` | the trust chain is broken — the original point of this test |
| `403` | authenticated, then denied: `arm-seed`'s role assignment never landed |
| `404` | authorized, and the probe secret does not exist — **success** |

Until the BOM wired `KV_ARM_URL`, a `403` was an accepted pass, which would
have let a broken seed ship. The vault *polls* ARM, so the grant is not
visible the instant `arm-seed` exits — the step retries rather than races.

Step 7 is the capacities equivalent. Until the BOM wired `FABRIC_ARM_URL`,
workspaces could 200 while `GET /v1/capacities` stayed on the seeded row —
a silent miss. Fabric *polls* ARM too, so the step retries rather than races.

Step 9 is the Databricks-audience equivalent of step 6. Until entra carved
out `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`, `DATABRICKS_OIDC_ISSUERS` was
wired and unused: every federated JWT was `invalid_resource` at mint time.

Step 10 recreates fabric with the scraped PAT. The PAT is printed once on
first boot, so compose cannot bake `FABRIC_DATABRICKS_TOKEN`. A missing
databricks job means the activity never reached `jobs/create` — URL, token,
or TLS. Failed naming the missing Spark engine is not a miss: this compose
has no Spark sidecar.

## Seam, not semantics

This test deliberately proves the seam. What each service *does* with a valid
token is its own repo's business, tested there in depth. What no other repo
can prove is that the published images, composed together, still agree about
who issues tokens and who trusts them.

That failure mode is real and arrives from outside: any of the six can
publish a release that breaks the family without a commit landing here. That
is why the drift job runs [nightly](03-release-coordination.md).
