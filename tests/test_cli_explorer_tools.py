import json
from unittest import mock

import pytest
import requests
from fastmcp import Client
from fastmcp.exceptions import ToolError

from jnpr_pathfinder_mcp import helpers
from jnpr_pathfinder_mcp.server import cli_explorer
from jnpr_pathfinder_mcp.server.cli_explorer import mcp


class ResponseMock:
    def __init__(self, ok=True, content=""):
        self.content = self.text = content
        self.ok = ok

    def json(self):
        return json.loads(self.content)


def envelope(data, success=True, message="ok"):
    return json.dumps(
        {"status": {"code": 200, "success": success, "errorMessage": message}, "data": data}
    )


TOPICS = [
    {"topicTitle": "show bgp neighbor (EX Series)", "topicType": "command"},
    {"topicTitle": "neighbor", "topicType": "statement"},
    {"topicTitle": "show bgp neighbor", "topicType": "command"},
    {"topicTitle": "show bgp neighbor exact-instance", "topicType": "command"},
    {"topicTitle": "show route", "topicType": "command"},
    {"topicTitle": None},
]


@pytest.fixture(autouse=True)
def clear_topic_cache():
    cli_explorer._topic_cache.clear()
    yield
    cli_explorer._topic_cache.clear()


TOOL_CALLS = [
    ("topic_reference", {}),
    ("topic_hierarchy", {}),
    ("topic_info", {"topic_title": "show bgp neighbor"}),
    ("search", {"query": "show bgp neighbor", "page_size": 10}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", TOOL_CALLS)
async def test_tool_live(tool, args):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args)
        assert result.structured_content.get("success"), result.structured_content
        assert result.structured_content.get("response")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", TOOL_CALLS)
async def test_tool_request_error_raises(tool, args):
    async with Client(mcp) as client:
        with mock.patch.object(
            helpers.requests, "post", side_effect=requests.exceptions.RequestException
        ):
            with pytest.raises(ToolError):
                await client.call_tool(tool, args)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", TOOL_CALLS)
async def test_tool_request_not_ok(tool, args):
    async with Client(mcp) as client:
        with mock.patch.object(
            helpers.requests, "post", return_value=ResponseMock(False, "Failed.")
        ):
            result = await client.call_tool(tool, args)
            assert not result.structured_content.get("success")
            assert result.structured_content.get("error") == "Failed."
            assert not result.structured_content.get("response")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", TOOL_CALLS)
async def test_tool_request_empty_response(tool, args):
    async with Client(mcp) as client:
        with mock.patch.object(helpers.requests, "post", return_value=ResponseMock(True, "")):
            result = await client.call_tool(tool, args)
            assert not result.structured_content.get("success")
            assert "Empty response from API" in result.structured_content.get("error")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", TOOL_CALLS)
async def test_tool_api_reports_failure(tool, args):
    async with Client(mcp) as client:
        with mock.patch.object(
            helpers.requests,
            "post",
            return_value=ResponseMock(True, envelope(None, False, "Not found.")),
        ):
            result = await client.call_tool(tool, args)
            assert not result.structured_content.get("success")
            assert result.structured_content.get("error") == "Not found."


@pytest.mark.asyncio
async def test_topic_info_unknown_topic():
    async with Client(mcp) as client:
        result = await client.call_tool("topic_info", {"topic_title": "not a real topic xyz"})
        assert not result.structured_content.get("success")
        assert result.structured_content.get("error")


@pytest.mark.asyncio
async def test_search_ranks_filters_and_caches():
    response = ResponseMock(True, envelope({"cliTopicList": TOPICS}))
    async with Client(mcp) as client:
        with mock.patch.object(helpers.requests, "post", return_value=response) as post:
            result = await client.call_tool("search", {"query": "  Show BGP  neighbor "})
            again = await client.call_tool("search", {"query": "show route"})
    assert post.call_count == 1
    titles = [t["topicTitle"] for t in result.structured_content["response"]["results"]]
    assert titles == [
        "show bgp neighbor",
        "show bgp neighbor (EX Series)",
        "show bgp neighbor exact-instance",
    ]
    assert result.structured_content["response"]["totalRecords"] == 3
    assert [t["topicTitle"] for t in again.structured_content["response"]["results"]] == [
        "show route"
    ]


@pytest.mark.asyncio
async def test_search_ranks_substring_matches_after_prefix_matches():
    topics = [{"topicTitle": "bgp neighbor x"}, {"topicTitle": "x bgp neighbor"}]
    response = ResponseMock(True, envelope({"cliTopicList": topics}))
    async with Client(mcp) as client:
        with mock.patch.object(helpers.requests, "post", return_value=response):
            result = await client.call_tool("search", {"query": "neighbor bgp"})
            ordered = await client.call_tool("search", {"query": "bgp neighbor"})
    assert result.structured_content["response"]["totalRecords"] == 2
    assert [t["topicTitle"] for t in ordered.structured_content["response"]["results"]] == [
        "bgp neighbor x",
        "x bgp neighbor",
    ]


@pytest.mark.asyncio
async def test_search_pagination():
    response = ResponseMock(True, envelope({"cliTopicList": TOPICS}))
    async with Client(mcp) as client:
        with mock.patch.object(helpers.requests, "post", return_value=response):
            page2 = await client.call_tool(
                "search", {"query": "neighbor", "page_number": 2, "page_size": 2}
            )
            page0 = await client.call_tool(
                "search", {"query": "neighbor", "page_number": 0, "page_size": 0}
            )
    assert [t["topicTitle"] for t in page2.structured_content["response"]["results"]] == [
        "show bgp neighbor (EX Series)",
        "show bgp neighbor exact-instance",
    ]
    assert page0.structured_content["response"]["pageNumber"] == 1
    assert page0.structured_content["response"]["pageSize"] == 1
    assert [t["topicTitle"] for t in page0.structured_content["response"]["results"]] == [
        "neighbor"
    ]


@pytest.mark.asyncio
async def test_search_unexpected_reference_shape():
    response = ResponseMock(True, envelope([{"topicTitle": "show bgp"}]))
    async with Client(mcp) as client:
        with mock.patch.object(helpers.requests, "post", return_value=response):
            result = await client.call_tool("search", {"query": "bgp"})
    assert result.structured_content["success"]
    assert result.structured_content["response"]["totalRecords"] == 0
