# The documentation site, and nothing else. Every other repository in this
# family drives its everyday workflow through a Makefile; this one has no
# emulator to run, so this file exists for the one thing it does have. The
# answer to "how do I preview the docs site?" is then the same in all eight.
#
# `pnpm docs:dev` is the fast inner loop for PROSE, and it is not this. It is
# based at the docs subpath and knows nothing about the tree around it, so
# under it the landing page does not exist and the redirect stubs do not exist.
# Use it to write a page; use `make docs-serve` before believing the site works.
#
# CI runs `make docs-build` and publishes exactly what it leaves in ./_site, so
# the thing previewed here is the thing that deploys.
#
# Not called `docs`: there is a docs/ DIRECTORY here, and a target sharing its
# name is satisfied by the directory existing, so `make docs` would print
# "nothing to be done" and exit 0. .PHONY below would also fix it; a name that
# cannot collide fixes it whether or not anyone remembers .PHONY.

DOCS_PKG  ?= azure-emulators-docs
DOCS_PORT ?= 8099
# The interpreter CI uses, pinned. These scripts are stdlib-only, hence
# --no-project: no environment to resolve, and a local 3.9 cannot pass
# something 3.12 would reject.
UVPY ?= uv run --no-project --python 3.12 python

.PHONY: help docs-build docs-serve

help: ## Show the available targets
	@grep -hE '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

docs-build: ## Build the published site into ./_site (what CI deploys)
	@command -v uv >/dev/null 2>&1 || { echo "uv is not on PATH: https://docs.astral.sh/uv/" >&2; exit 1; }
	pnpm install --frozen-lockfile
	pnpm --filter $(DOCS_PKG) build
	$(UVPY) scripts/assemble_site.py --self-test
	$(UVPY) scripts/assemble_site.py --out _site
	$(UVPY) scripts/build_landing_data.py --out _site --landing site/index.html

docs-serve: docs-build ## …and serve it locally at its published URLs (DOCS_PORT=8099)
	$(UVPY) scripts/assemble_site.py --serve --site _site --port $(DOCS_PORT)
