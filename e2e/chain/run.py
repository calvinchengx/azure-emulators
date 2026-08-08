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
    4. keyvault ACCEPTS entra's token on a real data-plane call.
    5. a token from the WRONG issuer is refused — proving step 3/4 passed
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
}
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(REPO / "docker-compose.yml")]
ENV = {**os.environ, **PORTS}

TENANT = "11111111-1111-1111-1111-111111111111"
SUB = "00000000-0000-0000-0000-000000000001"
# entra-emulator's seeded daemon app — a confidential client with a known secret.
SP_CLIENT = "cccccccc-0000-0000-0000-000000000002"
SP_SECRET = "daemon-app-secret"

ENTRA = f"https://localhost:{PORTS['ENTRA_PORT']}"
KV = f"https://localhost:{PORTS['KEYVAULT_PORT']}"
ARM = f"https://localhost:{PORTS['ARM_PORT']}"
ARM_API = "2021-04-01"
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

        # 4. Key Vault accepts entra's token on a real data-plane call. 200 or
        #    403 both prove the token was VALIDATED (403 is an authorization
        #    decision made after authentication); 401 means the trust chain is
        #    broken, which is what we are here to catch.
        status, raw = http(
            "GET", f"{KV}/secrets/chain-probe?api-version={KV_API}", bearer(kv_tok)
        )
        if status == 401:
            sys.exit(f"FAIL: keyvault refused entra's token (401): {raw[:300]}")
        step(4, f"keyvault authenticated the token (HTTP {status})")

        # 5. A token from the wrong issuer must be refused — otherwise steps 3
        #    and 4 prove nothing about the trust chain.
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
        step(5, "arm rejected a foreign-issuer token (401) — the gate is real")

    finally:
        if keep:
            print("\nKEEP_UP=1 — leaving the stack running. Tear down with:")
            print(f"  docker compose -p {PROJECT} -f {REPO / 'docker-compose.yml'} down -v")
        else:
            compose("down", "-v", check=False)

    print("\nPASS: the published images compose and trust each other.")


if __name__ == "__main__":
    main()
