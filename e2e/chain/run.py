#!/usr/bin/env python3
"""Does the FAMILY compose — as published images?

This is the one check no single emulator's repo can make. Each project already
tests itself, and azure-keyvault-emulator's e2e/arm-chain proves the ARM→vault
authorization semantics in depth — but all of those build from a source tree.
Nothing verifies that the *released container images*, wired by the compose file
in this repo, actually trust each other.

That is what breaks in practice, and it almost always breaks the same way:
issuer misalignment. Tokens carry iss = <entra origin>/<tenant>/v2.0, so entra
must ADVERTISE the origin its peers validate against. Get PUBLIC_ORIGIN or a
*_ENTRA_ISSUER wrong and every cross-service call fails 401 — while each
emulator's own test suite stays green.

So this test asserts the seam, not the semantics:

    1. `docker compose up --wait` — every image reports healthy.
    2. entra mints a token per audience (ARM, Key Vault).
    3. arm ACCEPTS entra's token and performs a real write (resource group).
    4. keyvault ACCEPTS entra's token on a real data-plane call, and ARM's
       seeded grant reaches it — 404, not 403.
    5. apim ACCEPTS the ARM-audience token on its own management surface.
    6. a token from the WRONG issuer is refused — proving steps 3-5 passed
       because the trust chain holds, not because validation is absent.

Stdlib-only, like the family's other e2e scripts.

    ./e2e/chain/run.py                  # pulls :latest, brings the stack up
    KEEP_UP=1 ./e2e/chain/run.py        # leave it running for poking at
"""

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Run under our own project name and on high host ports, so this never collides
# with a family stack (or any other project) already bound to 8443/8444/8445.
PROJECT = "azure-emulators-e2e"
PORTS = {
    "ENTRA_PORT": os.environ.get("ENTRA_PORT", "18443"),
    "KEYVAULT_PORT": os.environ.get("KEYVAULT_PORT", "18444"),
    "ARM_PORT": os.environ.get("ARM_PORT", "18445"),
    "APIM_PORT": os.environ.get("APIM_PORT", "18446"),
}
# --profile apim: the chain certifies the profiled consumer too — its ARM
# surface must accept entra's token the same way arm's does.
COMPOSE = ["docker", "compose", "-p", PROJECT, "--profile", "apim",
           "-f", str(REPO / "docker-compose.yml")]
ENV = {**os.environ, **PORTS}

TENANT = "6f89cf12-978b-4d23-ac18-9ef0c127cf87"
SUB = "6082bfda-63d0-46f4-8272-ae9195139feb"
# entra-emulator's seeded daemon app — a confidential client with a known secret.
SP_CLIENT = "00d88624-f0d7-46f6-a641-6232c2608928"
SP_SECRET = "daemon-app-secret"

ENTRA = f"https://localhost:{PORTS['ENTRA_PORT']}"
KV = f"https://localhost:{PORTS['KEYVAULT_PORT']}"
ARM = f"https://localhost:{PORTS['ARM_PORT']}"
APIM = f"https://localhost:{PORTS['APIM_PORT']}"
ARM_API = "2021-04-01"
APIM_API = "2024-05-01"
KV_API = "7.5"

# Self-signed certs on every service; this is a local emulator stack.
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def http(method, url, headers=None, body=None):
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def token(scope):
    """A client-credentials token from entra for the given resource."""
    status, raw = form(
        f"{ENTRA}/{TENANT}/oauth2/v2.0/token",
        {
            "grant_type": "client_credentials",
            "client_id": SP_CLIENT,
            "client_secret": SP_SECRET,
            "scope": scope,
        },
    )
    if status != 200:
        sys.exit(f"FAIL: entra token for {scope}: {status} {raw[:300]}")
    return json.loads(raw)["access_token"]


def compose(*args, check=True):
    return subprocess.run(COMPOSE + list(args), check=check, env=ENV)


def step(n, msg):
    print(f"  {n}. {msg}", flush=True)


def main():
    keep = os.environ.get("KEEP_UP") == "1"
    print("azure-emulators :: family chain (published images)\n")

    # 1. Bring the published images up and wait for every healthcheck.
    print("Bringing the stack up (docker compose up --wait)…", flush=True)
    compose("pull", "-q", check=False)
    try:
        compose("up", "-d", "--wait")
    except subprocess.CalledProcessError:
        subprocess.run(COMPOSE + ["ps"], check=False, env=ENV)
        subprocess.run(COMPOSE + ["logs", "--tail", "40"], check=False, env=ENV)
        sys.exit("FAIL: the stack never became healthy")
    step(1, "every service reports healthy")

    try:
        # 2. entra mints a token per audience.
        arm_tok = token("https://management.azure.com/.default")
        kv_tok = token("https://vault.azure.net/.default")
        step(2, "entra issued ARM- and Key Vault-audience tokens")

        # 3. ARM accepts entra's token for a real write.
        rg = "chain-rg"
        status, raw = http(
            "PUT",
            f"{ARM}/subscriptions/{SUB}/resourcegroups/{rg}?api-version={ARM_API}",
            bearer(arm_tok),
            {"location": "eastus"},
        )
        if status not in (200, 201):
            sys.exit(f"FAIL: arm rejected entra's token: {status} {raw[:300]}")
        step(3, f"arm accepted the token and created resourceGroups/{rg}")

        # 4. Key Vault accepts entra's token on a real data-plane call AND
        #    ARM's grant reaches it. Three outcomes, all meaningful now that
        #    the stack is governed:
        #      401 — the trust chain is broken, the original point of this test.
        #      403 — authenticated, then denied: arm-seed's role assignment
        #            never landed. Until the BOM wired KV_ARM_URL this was an
        #            accepted pass, which would have let a broken seed ship.
        #      404 — authorized, and chain-probe does not exist. Success.
        #    The vault POLLS ARM, so the grant is not visible the instant
        #    arm-seed exits. Retry rather than race.
        deadline = time.time() + 60
        while True:
            status, raw = http(
                "GET", f"{KV}/secrets/chain-probe?api-version={KV_API}", bearer(kv_tok)
            )
            if status == 401:
                sys.exit(f"FAIL: keyvault refused entra's token (401): {raw[:300]}")
            if status != 403 or time.time() > deadline:
                break
            time.sleep(2)
        if status == 403:
            sys.exit("FAIL: keyvault authenticated the token but denied it (403) — "
                     "arm-seed's grant never reached the vault")
        step(4, f"keyvault authorized the token (HTTP {status})")

        # 5. apim accepts the same ARM-audience token on its management
        #    surface. The seam, not the semantics: any non-401 means the
        #    trust chain held; apim's own suites cover what the routes do.
        status, raw = http(
            "GET",
            f"{APIM}/subscriptions/{SUB}/providers/Microsoft.ApiManagement/service"
            f"?api-version={APIM_API}",
            bearer(arm_tok),
        )
        if status == 401:
            sys.exit(f"FAIL: apim refused entra's token (401): {raw[:300]}")
        step(5, f"apim accepted the ARM-audience token (HTTP {status})")

        # 6. A token from the wrong issuer must be refused — otherwise steps
        #    3-5 prove nothing about the trust chain.
        bogus = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJpc3MiOiJodHRwczovL2V2aWwuZXhhbXBsZS8iLCJhdWQiOiJodHRwczovL21hbmFnZW1lbnQuYXp1cmUuY29tIn0."
            "not-a-real-signature"
        )
        status, _ = http(
            "GET",
            f"{ARM}/subscriptions/{SUB}/resourcegroups?api-version={ARM_API}",
            bearer(bogus),
        )
        if status != 401:
            sys.exit(f"FAIL: arm accepted a foreign-issuer token (HTTP {status})")
        status, _ = http(
            "GET",
            f"{APIM}/subscriptions/{SUB}/providers/Microsoft.ApiManagement/service"
            f"?api-version={APIM_API}",
            bearer(bogus),
        )
        if status != 401:
            sys.exit(f"FAIL: apim accepted a foreign-issuer token (HTTP {status})")
        step(6, "arm and apim rejected a foreign-issuer token (401) — the gate is real")

    finally:
        if keep:
            print("\nKEEP_UP=1 — leaving the stack running. Tear down with:")
            print(f"  docker compose -p {PROJECT} -f {REPO / 'docker-compose.yml'} down -v")
        else:
            compose("down", "-v", check=False)

    print("\nPASS: the published images compose and trust each other.")


if __name__ == "__main__":
    main()
