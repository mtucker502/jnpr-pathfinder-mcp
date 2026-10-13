import json
from unittest import mock

import pytest
import requests
from fastmcp import Client
from fastmcp.exceptions import ToolError

from jnpr_pathfinder_mcp import helpers
from jnpr_pathfinder_mcp.server.hct import mcp


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


TOOL_CALLS = [
    ("categories", {}),
    ("category_components", {"category_key": 100001}),
    ("component_details", {"component_name": "QSFP-100G-ZR4"}),
    ("component_supported_platforms", {"component_name": "QSFP-100G-ZR4"}),
    ("component_supported_models", {"component_name": "QSFP-100G-ZR4"}),
    ("platforms_by_family", {}),
    ("components_for_platform", {"platform": "ACX710"}),
    ("platform_hardware_details", {"platform": "ACX710"}),
    ("platform_information", {"platform": "ACX710"}),
    ("search", {"query": "QSFP 100G", "page_size": 5}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", TOOL_CALLS)
async def test_tool_live(tool, args):
    """Each tool returns data from the live HCT API."""
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
    """The API reports errors with HTTP 200 and status.success == false."""
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
@pytest.mark.parametrize(
    "tool", ["component_details", "component_supported_platforms", "component_supported_models"]
)
async def test_unknown_component(tool):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, {"component_name": "NOT-A-REAL-PART"})
        assert not result.structured_content.get("success")
        assert "Empty response from API" in result.structured_content.get("error")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["components_for_platform", "platform_information"])
async def test_unknown_platform(tool):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, {"platform": "NOT-A-REAL-PLATFORM"})
        assert not result.structured_content.get("success")
        assert "Empty response from API" in result.structured_content.get("error")


@pytest.mark.asyncio
async def test_platform_tools_split_product_detail():
    detail = {
        "platform": "ACX710",
        "isPlatformEol": "No",
        "productModelInfo": [{"category": "Transceivers"}],
        "overview": None,
        "specificationDetail": {"ACX710-DC": []},
    }
    async with Client(mcp) as client:
        with mock.patch.object(
            helpers.requests, "post", return_value=ResponseMock(True, envelope(detail))
        ):
            info = await client.call_tool("platform_information", {"platform": "ACX710"})
            components = await client.call_tool("components_for_platform", {"platform": "ACX710"})
    assert "productModelInfo" not in info.structured_content["response"]
    assert info.structured_content["response"]["specificationDetail"] == {"ACX710-DC": []}
    assert components.structured_content["response"] == {
        "platform": "ACX710",
        "productModelInfo": [{"category": "Transceivers"}],
    }


@pytest.mark.asyncio
async def test_component_sections():
    details = {
        "component": {"modelNumber": "X"},
        "supportedPlatforms": [{"category": "Routing"}],
        "supportedInterfaceModels": None,
    }
    async with Client(mcp) as client:
        with mock.patch.object(
            helpers.requests, "post", return_value=ResponseMock(True, envelope(details))
        ):
            platforms = await client.call_tool(
                "component_supported_platforms", {"component_name": "X"}
            )
            models = await client.call_tool("component_supported_models", {"component_name": "X"})
    assert platforms.structured_content["response"] == {
        "component": {"modelNumber": "X"},
        "supportedPlatforms": [{"category": "Routing"}],
    }
    assert models.structured_content["response"] == {
        "component": {"modelNumber": "X"},
        "supportedInterfaceModels": [],
    }


def test_post_json_without_envelope():
    with mock.patch.object(helpers.requests, "post", return_value=ResponseMock(True, "[1, 2]")):
        assert helpers.post_json("https://example.com") == ([1, 2], None)


def test_post_json_null_status():
    body = json.dumps({"status": None, "data": {"a": 1}})
    with mock.patch.object(helpers.requests, "post", return_value=ResponseMock(True, body)):
        data, error = helpers.post_json("https://example.com", empty_error="empty")
    assert data is None
    assert error == "empty"


def test_post_json_success_without_data():
    with mock.patch.object(
        helpers.requests, "post", return_value=ResponseMock(True, envelope(None))
    ):
        assert helpers.post_json("https://example.com", empty_error="empty") == (None, "empty")
