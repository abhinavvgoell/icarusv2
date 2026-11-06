# Runtime dependency and image policy

- The app image uses a digest-pinned official Python 3.12 slim base. Update the digest deliberately from the official image manifest, then rebuild and rescan; do not switch back to a mutable tag for a release.
- `requirements-runtime.in` lists direct runtime dependencies. `requirements-runtime.lock` is generated for Linux/Python 3.12 with hashes and installed using pip's `--require-hashes` mode.
- Regenerate the lock with `scripts/compile_runtime_lock.ps1`; audit it with `scripts/audit_runtime_lock.ps1`. Both use the pinned Python image so platform markers match the deployment container. A clean audit means no known Python package advisories were returned by pip-audit at the audit time; it does not prove the image OS or application is vulnerability-free.
- `requirements.txt` is for local development and tests. The runtime image installs only the lock, excludes tests/docs/migration tooling and local data from its build context, runs as UID 10001, and removes pip after installation.
- The runtime image must still receive an OS-package/image CVE scan in the organization's release pipeline. Docker Scout was not usable in this workspace because it requires Docker account authentication; do not treat the Python package audit as a substitute.
