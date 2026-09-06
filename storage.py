"""
Simple shared JSON storage. Any cog can import `load_data` / `save_data`
to persist its own data using its own top-level key, so cogs never
clobber each other's data in the same file.
"""

import json
import os

DATA_FILE = "bot_data.json"


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# One shared in-memory copy, loaded once at import time.
DATA = load_data()


def get_bucket(namespace: str, guild_id: int, default: dict):
    """
    Get (or create) a cog's private data bucket for a given guild.
    `namespace` should be unique per cog, e.g. "sobs".
    """
    key = f"{namespace}:{guild_id}"
    if key not in DATA:
        DATA[key] = default
    return DATA[key]


def persist():
    save_data(DATA)
