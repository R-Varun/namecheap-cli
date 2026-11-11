"""Build and validate the static project site without a JS toolchain."""

import json
import re
import shutil
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
version = pyproject["project"]["version"]

init_text = (root / "src" / "namecheap_cli" / "__init__.py").read_text(encoding="utf-8")
assert f'__version__ = "{version}"' in init_text, "__init__.py version out of sync"

schema_doc = json.loads((root / "docs" / "schema.json").read_text(encoding="utf-8"))
assert schema_doc["data"]["cli_version"] == version, "docs/schema.json cli_version out of sync"

output = (root / "_site").resolve()
assert output == (root / "_site").resolve() and output.name == "_site"
if output.exists():
    shutil.rmtree(output)
shutil.copytree(root / "site", output)
shutil.copytree(root / "assets", output / "assets", dirs_exist_ok=True)
shutil.copy2(root / "docs" / "schema.json", output / "schema.json")
(output / ".nojekyll").touch()

for html_file in output.rglob("*.html"):
    rendered = html_file.read_text(encoding="utf-8").replace("__CLI_VERSION__", version)
    html_file.write_text(rendered, encoding="utf-8")

for text_name in ("llms.txt", "sitemap.xml"):
    target = output / text_name
    if target.exists():
        target.write_text(
            target.read_text(encoding="utf-8").replace("__CLI_VERSION__", version),
            encoding="utf-8",
        )

required = (
    "index.html",
    "404.html",
    "llms.txt",
    "robots.txt",
    "sitemap.xml",
    "favicon.ico",
    "schema.json",
    "assets/logo.svg",
    "assets/logo.png",
    "assets/apple-touch-icon.png",
    "assets/social.png",
    "assets/banner.png",
)
for rel in required:
    assert (output / rel).is_file(), f"Missing built site file: {rel}"

index_html = (output / "index.html").read_text(encoding="utf-8")
assert "__CLI_VERSION__" not in index_html
ld_match = re.search(
    r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', index_html, re.S
)
assert ld_match is not None, "Missing JSON-LD block in index.html"
ld_data = json.loads(ld_match.group(1))
assert ld_data["version"] == version

print(f"Built and verified {output} for v{version}")
