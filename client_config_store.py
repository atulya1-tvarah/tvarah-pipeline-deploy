from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CLIENT_CONFIGS_DIR = Path(__file__).resolve().parent / "client_configs"


def save_client_config(client_id: str, config: dict[str, Any]) -> Path:
    """Persist a full client config JSON. Overwrites if already exists."""
    CLIENT_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    path = CLIENT_CONFIGS_DIR / f"{client_id}.json"
    config["client_id"] = client_id  # ensure id is embedded
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_client_config(client_id: str) -> dict[str, Any] | None:
    """Load a client config. Returns None if not found."""
    path = CLIENT_CONFIGS_DIR / f"{client_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_role_config(client_id: str, role_id: str) -> dict[str, Any] | None:
    """Load a specific role config from a client config."""
    config = load_client_config(client_id)
    if not config:
        return None
    role_configs = config.get("role_configs", {})
    return role_configs.get(role_id)


def upsert_role_config(client_id: str, role_id: str, role_config: dict[str, Any]) -> Path:
    """Add or update a single role inside an existing client config."""
    config = load_client_config(client_id)
    if not config:
        config = {"client_id": client_id, "client_name": client_id, "role_configs": {}}
    if "role_configs" not in config:
        config["role_configs"] = {}
    config["role_configs"][role_id] = role_config
    return save_client_config(client_id, config)


def list_client_configs() -> list[dict[str, Any]]:
    """Return a summary list of all stored client configs."""
    if not CLIENT_CONFIGS_DIR.exists():
        return []
    results = []
    for path in CLIENT_CONFIGS_DIR.glob("*.json"):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        results.append({
            "client_id": config.get("client_id"),
            "client_name": config.get("client_name"),
            "role_count": len(config.get("role_configs", {})),
            "assigned_templates": config.get("assigned_templates", []),
        })
    return results
