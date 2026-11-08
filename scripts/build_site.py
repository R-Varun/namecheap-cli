"""Build the static project page without a JS toolchain."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / "_site"
output.mkdir(exist_ok=True)
for file in (root / "site").iterdir():
    if file.is_file():
        shutil.copy2(file, output / file.name)
shutil.copytree(root / "assets", output / "assets", dirs_exist_ok=True)
(output / ".nojekyll").touch()
print(output)
