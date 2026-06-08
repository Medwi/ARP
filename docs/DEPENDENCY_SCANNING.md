# Dependency vulnerability scanning

Two complementary gates — use **both**, not either/or.

| Tool | Scope | Policy | When it runs |
|------|--------|--------|----------------|
| **Safety** | Python packages in `requirements.txt` | **`.safety-policy.yml` required** | CI + `make security` / `scripts/safety_check.sh` |
| **Trivy** | Built Docker image (OS + pip layers) | HIGH/CRITICAL fail | CI `build` job |

## Always use the Safety policy file

Streamlit dashboard CVEs are **documented ignores** in `.safety-policy.yml` (internal assessment UI; upgrade tracked separately). Trivy still scans the container image.

Running Safety **without** the policy file will report those Streamlit findings and does not match CI:

```bash
# Wrong — do not use for this repo
safety check -r requirements.txt

# Correct — matches GitHub Actions
make security          # bandit + safety
./scripts/safety_check.sh
```

CI and local scans exclude test-only packages (`pytest`) from the runtime requirements file, same as:

```bash
grep -Ev '^(pytest|#)' requirements.txt > /tmp/runtime-requirements.txt
safety check -r /tmp/runtime-requirements.txt --full-report --policy-file .safety-policy.yml
```

## Adding ignores

1. Document the reason in `.safety-policy.yml` under `security.ignore-vulnerabilities`.
2. Prefer upgrading the dependency when feasible.
3. For image-level issues, fix the Dockerfile/base image — Trivy is the enforcement gate for deployable artifacts.
