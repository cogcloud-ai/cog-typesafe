# Cog TypeSafe contributor instructions
Read COG.md and README.md. This is a provider for `system-one/decisions`,
not Smith's decision-cog template. It vendors two contracts byte-identically:
the satisfier-binding schema and Smith's `system_one_contract.py`; fix those
upstream and record the new digests in contracts/README.md.
Provider code never admits its own binding. Bind versioned Jev IDs only; never
add alias support, model fallback, or silent retries of a paid turn beyond the
API's 429/529 guidance. Keep the key in TYPESAFE_API_KEY, never files, request
documents or logs, and never echo TypeSafe error bodies (they can carry state).
Run pixi run test and pixi run check. Tests are model-free; `probe` is live and paid.
