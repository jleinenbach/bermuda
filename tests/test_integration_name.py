"""The integration name is the same everywhere and shows the FMDN connection."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.bermuda.const import NAME

ROOT = Path(__file__).parent.parent
COMPONENT = ROOT / "custom_components" / "bermuda"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_name_is_identical_in_hacs_manifest_const_and_config_flow() -> None:
    """hacs.json, manifest.json, const.NAME and the English config flow title agree."""
    hacs = _load(ROOT / "hacs.json")
    manifest = _load(COMPONENT / "manifest.json")
    english = _load(COMPONENT / "translations" / "en.json")

    assert hacs["name"] == manifest["name"] == NAME == english["config"]["step"]["user"]["title"]


def test_name_shows_fmdn_connection() -> None:
    """The fork's name says what it connects to."""
    assert "FMDN" in NAME


def test_domain_unchanged_and_first() -> None:
    """The domain stays bermuda (entities, devices and storage depend on it), hassfest key order kept."""
    manifest = _load(COMPONENT / "manifest.json")

    assert manifest["domain"] == "bermuda"
    assert list(manifest)[:2] == ["domain", "name"]
