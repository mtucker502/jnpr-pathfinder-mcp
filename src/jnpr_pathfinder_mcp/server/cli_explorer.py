import logging
import time
from typing import Any, Optional

from fastmcp import FastMCP  # type: ignore
from pydantic import BaseModel

from jnpr_pathfinder_mcp.helpers import post_json

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
file_handler = logging.FileHandler("/tmp/workspace.log")
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
log.addHandler(file_handler)

INSTRUCTIONS = """
Search for JUNOS CLI instructions, hierarchy, and commands.

Typical usage might be to search for a command and then retrieve
the topic page if found.

If unsure of specific topic name, get either topic reference or
hierarchy and browse.
"""

mcp = FastMCP(name="Juniper JUNOS Command Line Interface Explorer", instructions=INSTRUCTIONS)

BASE_URL = "https://apps.juniper.net/softwaresrv/cli"

URLS = {
    "topic_reference": f"{BASE_URL}/reference",
    "topic_hierarchy": f"{BASE_URL}/hierarchy",
    "topic_info": f"{BASE_URL}/topic-info",
}

VERIFY_SSL = True
TIMEOUT = 120
TOPIC_CACHE_TTL = 3600

# The topic reference is ~3MB, so cache it for searches.
_topic_cache: dict[str, Any] = {}


class CliExplorerResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    response: Optional[dict[str, Any] | list[dict[str, Any]]] = None


def _post(url: str, payload: Optional[dict[str, Any]] = None) -> tuple[Any, Optional[str]]:
    return post_json(url, payload, verify=VERIFY_SSL, timeout=TIMEOUT)


def _respond(data: Any, error: Optional[str]) -> CliExplorerResponse:
    if error is not None:
        return CliExplorerResponse(success=False, error=error)
    return CliExplorerResponse(success=True, response=data)


def _get_topics() -> tuple[list[dict[str, Any]], Optional[str]]:
    if _topic_cache and time.monotonic() - _topic_cache["fetched_at"] < TOPIC_CACHE_TTL:
        return _topic_cache["topics"], None
    data, error = _post(URLS["topic_reference"])
    if error is not None:
        return [], error
    topics = data.get("cliTopicList") or [] if isinstance(data, dict) else []
    _topic_cache.update(topics=topics, fetched_at=time.monotonic())
    return topics, None


def _rank(topic: dict[str, Any], query: str) -> tuple[int, int, str]:
    title = (topic.get("topicTitle") or "").lower()
    if title == query:
        tier = 0
    elif title.startswith(query):
        tier = 1
    elif query in title:
        tier = 2
    else:
        tier = 3
    return tier, len(title), title


@mcp.tool
def search(query: str, page_number: int = 1, page_size: int = 100) -> CliExplorerResponse:
    """Search for JUNOS CLI commands and configuration statements by keywords.

    Every keyword must appear in the topic title (case-insensitive), e.g. "show bgp neighbor".
    Results are ranked with exact and prefix matches first. page_number starts at 1.
    """
    topics, error = _get_topics()
    if error is not None:
        return _respond(None, error)
    normalized = " ".join(query.lower().split())
    keywords = normalized.split()
    matches = [
        topic
        for topic in topics
        if all(k in (topic.get("topicTitle") or "").lower() for k in keywords)
    ]
    matches.sort(key=lambda topic: _rank(topic, normalized))
    page_number = max(page_number, 1)
    page_size = max(page_size, 1)
    start = (page_number - 1) * page_size
    return _respond(
        {
            "searchQuery": query,
            "totalRecords": len(matches),
            "pageNumber": page_number,
            "pageSize": page_size,
            "results": matches[start : start + page_size],
        },
        None,
    )


@mcp.tool
def topic_reference() -> CliExplorerResponse:
    """Get the full list of topics (top level cli commands)."""
    return _respond(*_post(URLS["topic_reference"]))


@mcp.tool
def topic_hierarchy() -> CliExplorerResponse:
    """Get the full topic (top level cli commands) hierarchy."""
    return _respond(*_post(URLS["topic_hierarchy"]))


@mcp.tool
def topic_info(topic_title: str) -> CliExplorerResponse:
    """Get details (documentation URL, type, release introduced) for a topic by exact title.

    For example "show bgp neighbor". Use search to find exact topic titles.
    """
    return _respond(*_post(URLS["topic_info"], {"topicTitle": topic_title, "fileName": ""}))

if __name__ == '__main__':  # pragma: nocover
    from jnpr_pathfinder_mcp.helpers import run_cli
    run_cli(prog="Juniper CLI Explorer MCP Server", server=mcp)
