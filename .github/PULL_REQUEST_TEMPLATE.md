## Summary

- 

## Verification

- [ ] `uv run pytest --cov=namecheap_cli --cov-report=term-missing`
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `docs/schema.json` regenerated if CLI flags, schemas, or error codes changed (`uv run namecheap schema > docs/schema.json`)
- [ ] Uses only reserved example domains (`example.com`) and documentation IP ranges (`203.0.113.0/24`)
