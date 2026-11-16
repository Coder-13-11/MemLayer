"""Product naming lives here and nowhere else, so the platform can be renamed in one place.

Override at runtime without editing code:
    NEXUS_BRAND_NAME="Cortex" NEXUS_BRAND_TAGLINE="..." nexus web
"""

from __future__ import annotations

import os

BRAND = {
    "name": os.environ.get("NEXUS_BRAND_NAME", "NEXUS"),
    "tagline": os.environ.get("NEXUS_BRAND_TAGLINE", "Shared memory for every AI you work with"),
    # Name of the MCP server as AI clients see it (the `claude mcp add <name>` you registered).
    "mcp_server_name": os.environ.get("NEXUS_MCP_NAME", "nexus"),
}
