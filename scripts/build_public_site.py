"""Build an allowlisted public GitHub Pages artifact."""
from __future__ import annotations
import argparse
import shutil
from pathlib import Path
PUBLIC_FILES = ("404.html", ".nojekyll", "site-build-info.json", "runtime-config.json", "public-live-banner.js")
SCRIPT_TAG = '<script src="./public-live-banner.js" defer></script>'

def build_public_site(root: Path, output: Path) -> None:
    if output.exists(): shutil.rmtree(output)
    output.mkdir(parents=True)
    content = (root / "index.html").read_text(encoding="utf-8")
    if SCRIPT_TAG not in content:
        content = content.replace("</body>", SCRIPT_TAG + "</body>", 1) if "</body>" in content else content + SCRIPT_TAG
    (output / "index.html").write_text(content, encoding="utf-8", newline="\n")
    for name in PUBLIC_FILES:
        source = root / name
        if not source.exists(): raise FileNotFoundError(name)
        shutil.copy2(source, output / name)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("_site"))
    args = parser.parse_args()
    build_public_site(Path.cwd(), args.output)

if __name__ == "__main__": main()
