import json
from pathlib import Path
from scripts.build_public_site import build_public_site


def test_public_build_is_disconnected_and_allowlisted(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "site"
    build_public_site(root, output)
    assert json.loads((output / "runtime-config.json").read_text()) == {
        "mode": "PUBLIC", "localApiAvailable": False, "apiBase": None}
    assert "public-live-banner.js" in (output / "index.html").read_text()
    assert not any(output.rglob("*.sqlite*"))
    assert not (output / "config.local.json").exists()
