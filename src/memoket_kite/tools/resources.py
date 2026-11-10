"""Locate agent resources next to the installed implementation."""

from pathlib import Path

from .contracts import ToolError


def resource_root(override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    package = Path(__file__).resolve().parents[1]
    bundled = package / "_agent_assets"
    checkout = package.parent.parent
    if (bundled / "AGENTS.md").is_file():
        return bundled
    if package.parent.name == "src" and (checkout / "AGENTS.md").is_file():
        return checkout
    raise ToolError("missing_resources", "reinstall KITE or select a trusted checkout with --repo")
