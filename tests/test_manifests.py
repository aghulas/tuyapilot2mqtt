"""The add-on manifests agree with each other and with the code."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import yaml

import tuyapilot2mqtt
from tuyapilot2mqtt import config

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "tuyapilot2mqtt"


def test_repository_manifest() -> None:
    repo = yaml.safe_load((ROOT / "repository.yaml").read_text(encoding="utf-8"))
    assert repo["name"] and "tuyapilot2mqtt" in repo["url"]


def test_addon_manifest_matches_the_code() -> None:
    cfg = yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["slug"] == "tuyapilot2mqtt"
    assert cfg["version"] == tuyapilot2mqtt.__version__
    assert "mqtt:want" in cfg["services"]
    assert set(cfg["options"]) == set(cfg["schema"]) == {f.name for f in fields(config.Settings)}
    defaults = config.Settings()
    for name, value in cfg["options"].items():
        if value != "":
            expected = getattr(defaults, name)
            assert value == (list(expected) if isinstance(expected, tuple) else expected), name
    build = yaml.safe_load((ADDON / "build.yaml").read_text(encoding="utf-8"))
    assert set(build["build_from"]) == set(cfg["arch"])


def test_contract_version_is_the_one_documented() -> None:
    contract = (ROOT / "docs" / "mqtt-contract.md").read_text(encoding="utf-8")
    assert f"Contract version: **{tuyapilot2mqtt.CONTRACT_VERSION}**" in contract


def test_changelog_has_the_version() -> None:
    assert f"## {tuyapilot2mqtt.__version__}" in (ADDON / "CHANGELOG.md").read_text(encoding="utf-8")


def test_store_images() -> None:
    """The add-on store shows an icon (square) and a logo (wide)."""
    for name, (width, height) in {"icon.png": (128, 128), "logo.png": (250, 100)}.items():
        data = (ADDON / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert (int.from_bytes(data[16:20]), int.from_bytes(data[20:24])) == (width, height), name
