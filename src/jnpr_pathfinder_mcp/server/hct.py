import logging
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
Determine hardware compatibility and interoperability using the Juniper Networks
Hardware Compatiblity Tool. You can retrieve detailed hardware information for
Juniper products from the HCT.

These tools allow you to access the Hardware Compatibility Tool (HCT) API. A typical workflow is:

- Get the list of all categories
- Get the list of components in the categories your interested in.
- Confirm that the component name exists.
- Get the component details.
- Get the supported models and platforms.

Juniper groups things in the following way:

- Family (Routing, Switching, Security, etc.)
  - Series (MX Series, ACX Series, EX Series, etc.)
    - Platform (EX4000)
      - Models [components compatible with the parent platform] (i.e EX-SFP-... transcievers)

- Categories are horizontal groupings of models.  For example, some transcievers
(common optics) can be used across different product families.
"""

mcp = FastMCP(name="Juniper Hardware Compatibility Tool", instructions=INSTRUCTIONS)

BASE_URL = "https://apps.juniper.net/hardwaresrv/hct"

URLS = {
    "categories": f"{BASE_URL}/categories",
    "category_models": f"{BASE_URL}/category-models",
    "component_details": f"{BASE_URL}/component-details",
    "platforms_grouped_by_family": f"{BASE_URL}/platforms-grouped-by-family",
    "product_detail": f"{BASE_URL}/product-detail",
    "specification_detail": f"{BASE_URL}/specification-detail",
    "search": f"{BASE_URL}/search",
}

VERIFY_SSL = True
TIMEOUT = 60

EMPTY_RESPONSE_ERROR = (
    "Empty response from API. Check that component names and category ids are correct."
)


class HctResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    response: Optional[dict[str, Any] | list[dict[str, Any]]] = None


def _post(url: str, payload: Optional[dict[str, Any]] = None) -> tuple[Any, Optional[str]]:
    return post_json(
        url, payload, empty_error=EMPTY_RESPONSE_ERROR, verify=VERIFY_SSL, timeout=TIMEOUT
    )


def _respond(data: Any, error: Optional[str]) -> HctResponse:
    if error is not None:
        return HctResponse(success=False, error=error)
    return HctResponse(success=True, response=data)


def _component(component_name: str) -> tuple[Any, Optional[str]]:
    data, error = _post(URLS["component_details"], {"modelNumber": component_name})
    # Unknown components return success with an empty "component" object.
    if error is None and not data.get("component"):
        return None, EMPTY_RESPONSE_ERROR
    return data, error


def _component_section(component_name: str, section: str) -> HctResponse:
    data, error = _component(component_name)
    if error is None:
        data = {"component": data.get("component"), section: data.get(section) or []}
    return _respond(data, error)


def _product_detail(platform: str) -> tuple[Any, Optional[str]]:
    data, error = _post(URLS["product_detail"], {"platform": platform})
    # Unknown platforms return success with all fields null.
    if error is None and not data.get("platform"):
        return None, EMPTY_RESPONSE_ERROR
    return data, error


@mcp.tool
def categories() -> HctResponse:
    """Get the list of all component categories."""
    return _respond(*_post(URLS["categories"]))


@mcp.tool
def category_components(category_key: int) -> HctResponse:
    """Get the list of all components in a category."""
    return _respond(*_post(URLS["category_models"], {"catKey": category_key}))


@mcp.tool
def component_details(component_name: str) -> HctResponse:
    """Get the details of a specific component."""
    return _respond(*_component(component_name))


@mcp.tool
def component_supported_platforms(component_name: str) -> HctResponse:
    """Get list of platforms on which a component is supported."""
    return _component_section(component_name, "supportedPlatforms")


@mcp.tool
def component_supported_models(component_name: str) -> HctResponse:
    """Get the list of interface models (line cards, MICs, etc.) that support the component."""
    return _component_section(component_name, "supportedInterfaceModels")


@mcp.tool
def platforms_by_family() -> HctResponse:
    """Get the list of all platforms grouped by family."""
    return _respond(*_post(URLS["platforms_grouped_by_family"]))


@mcp.tool
def components_for_platform(platform: str) -> HctResponse:
    """Get the components (grouped by category) supported on a platform."""
    data, error = _product_detail(platform)
    if error is None:
        data = {"platform": data.get("platform"), "productModelInfo": data.get("productModelInfo")}
    return _respond(data, error)


@mcp.tool
def platform_hardware_details(platform: str) -> HctResponse:
    """Get the hardware specifications for each variant of a platform."""
    return _respond(*_post(URLS["specification_detail"], {"productName": platform}))


@mcp.tool
def platform_information(platform: str) -> HctResponse:
    """Get general information (EOL status, overview, specifications) for a platform."""
    data, error = _product_detail(platform)
    if error is None:
        data = {k: v for k, v in data.items() if k != "productModelInfo"}
    return _respond(data, error)


@mcp.tool
def search(query: str, page_number: int = 1, page_size: int = 10) -> HctResponse:
    """Search the HCT for components and platforms by keyword (e.g. "QSFP 100G ZR")."""
    payload = {
        "searchQuery": query,
        "pageNumber": page_number,
        "pageSize": page_size,
        "categories": [],
        "eol": [],
        "filterDataRequired": False,
    }
    return _respond(*_post(URLS["search"], payload))

if __name__ == '__main__':  # pragma: nocover
    from jnpr_pathfinder_mcp.helpers import run_cli
    run_cli(prog="Juniper Hardware Compatibility Tool MCP Server", server=mcp)
