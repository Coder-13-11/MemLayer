"""Product naming lives here and nowhere else, so the platform can be renamed in one place.

Override at runtime without editing code:
    MEMLAYER_BRAND_NAME="Cortex" MEMLAYER_BRAND_TAGLINE="..." memlayer web
"""

from __future__ import annotations

import os

BRAND = {
    "name": os.environ.get("MEMLAYER_BRAND_NAME", "MemLayer"),
    "tagline": os.environ.get("MEMLAYER_BRAND_TAGLINE", "Shared memory for every AI you work with"),
    # Name of the MCP server as AI clients see it (the `claude mcp add <name>` you registered).
    "mcp_server_name": os.environ.get("MEMLAYER_MCP_NAME", "memlayer"),
}
