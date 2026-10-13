import functools
import logging
import re
from typing import Annotated, Any, Optional

import requests
from bs4 import BeautifulSoup
from fastmcp import FastMCP  # type: ignore
from pydantic import BaseModel

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
file_handler = logging.FileHandler("/tmp/workspace.log")
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
log.addHandler(file_handler)
log.addHandler(logging.StreamHandler())

VERIFY_SSL = True
TIMEOUT = 60

INSTRUCTIONS = """
Search for JUNOS Platform and Model Features by JUNOS release.

Typical usage would include:
    - Fetching the supported software releases
    - Fetching the models
    - Fetching the features for a given model and release.
    - Identifying whether a particular feature is supported on a given model/release combination.
"""

mcp = FastMCP(name="Juniper Feature Explorer", instructions=INSTRUCTIONS)

BASE_URL = "https://apps.juniper.net"

URLS = {
    "software_releases": "/feature-explorer/software-release",
    "models_for_release": (
        "/feature-explorer/getPlatformDetails.html?softwareName={junos_os_type}&version={version}"
    ),
    "releases_for_model": "/feature-explorer/getReleasesToCompare.html?prodKey={product_key}",
    "features_for_model": "/feature-explorer/getFeaturesForProductOnAReleaseAndSoftware.html",
    "feature_tree": "/feature-explorer/getFeatureTree.html",
    "feature_details": "/feature-explorer/getFeatureDetail/{feature_key}",
    "product_keys": "/feature-explorer/select-platform.html",
}


def _url_for(key):
    return BASE_URL + URLS[key]


class FeatureExplorerResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    response: Optional[dict[str, Any] | list[dict[str, Any]]] = None


## Helpers for building the model catalog, which I can't find as JSON, so
## we scrape it out of the feature explorer landing page.

# The product list is spread across four categories/tabs, we want them all.
CATEGORIES = {
    "routing": "Routing",
    "switching": "Switching",
    "security": "Security",
    # the "More" tab uses "Junos Space and NFX" in the URL
    "more": "Junos+Space+and+NFX",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}

PID_RE = re.compile(r"pid-(\d+)")


def _snake(to_convert: str) -> str:
    """Convert a label to a safe _snake_case key (lowercase, underscore)."""
    if not to_convert:
        return to_convert
    # keep alphanum and replace other runs with underscore
    key = re.sub(r"[^0-9A-Za-z-]+", "_", to_convert.strip())
    key = re.sub(r"_+", "_", key)  # collapse underscores
    key = key.strip("_").lower()
    return key or to_convert.lower()


def _parse_page_html(html: str):
    """Extract the platform ids from the feature explorer landing page.

    Tries to be robust, prefers the id field but will fall back to onclick
    handlers.

    Returns: list[tuple[family: str, platform: str, platform_key: int]]
    """
    soup = BeautifulSoup(html, "html.parser")
    platforms = []  # list of tuples (family, platform_label, platform_id)
    # find all product buttons
    for prod in soup.find_all("span", class_="prodBtn"):
        # product id from id attribute 'pid-12345678'
        pid_attr = prod.get("id", "")
        m = PID_RE.search(pid_attr)
        pid = int(m.group(1))

        platform = prod.get_text(strip=True) or prod.get("data-platform") or prod.get("value") or ""

        # family: look at parent span with class plorrel -> data-family attribute
        parent = prod.find_parent("span", class_="plorrel")
        family = parent.get("data-family") if parent and parent.get("data-family") else None

        platforms.append((family, platform, pid))
        log.info("_parse_age_html - extracted %s:%s:%s", family, platform, pid)
    return platforms


@functools.lru_cache(maxsize=1)
def _build_platform_catalog():
    """Get the feature explorer landing page for each category and parse it.

    Returns: dict[model:str, dict[str, str | int]]
    """
    catalog = {}
    log.info("_build_platform_catalogue - building...")
    for cat_key, cat_param in CATEGORIES.items():
        try:
            r = requests.get(
                _url_for("product_keys"),
                params={"typ": "1", "category": cat_param},
                headers=HEADERS,
                timeout=15,
                verify=VERIFY_SSL,
            )
            r.raise_for_status()
            html = r.text
        except Exception as e:
            log.exception(f"_build_platform_catalog - ERROR fetching {cat_param}: {e}")
            continue

        products = _parse_page_html(html)

        log.info(
            "_build_platform_catalogue - found %d entries in html [%d bytes] for %s.",
            len(products),
            len(html),
            cat_param,
        )
        for family, label, pid in products:
            catalog[_snake(label)] = {"family": _snake(family), "product_key": pid}
            log.info(
                "_build_platform_catalogue - adding %s:%s:%s", _snake(family), _snake(label), pid
            )

    return catalog


def _get_pid_for_model(model: str) -> int:
    catalog = _build_platform_catalog()
    log.info("_get_pid_for_model - searching for %s in %s", _snake(model), catalog.keys())
    if _snake(model) in catalog:
        return catalog[_snake(model)]["product_key"]

    # handle the case where the model has extra suffixes: QFX5240-64OD-AFO
    model_components = model.split("-")[:-1]
    while model_components:
        log.info("_get_pid_for_model - simplified_model %s", model_components)
        _model = "-".join(model_components)
        if _snake(_model) in catalog:
            return catalog[_snake(_model)]["product_key"]
        model_components = model_components[:-1]

    # bail if we can't match it.
    log.error("_get_pid_for_model - failed to find pid for %s in %s", model, catalog.keys())
    raise ValueError(f"Failed to find matching model for {model}.")


@mcp.tool
def software_releases(
    junos_os_type: Annotated[str, "One of ['Junos OS', 'Junos OS Evolved']"] = "Junos OS",
) -> FeatureExplorerResponse:
    """Fetch the list of all supported software releases.

    Arguments:
      junos_os_type: str - one of "Junos OS" or "Junos OS Evolved"
    """
    payload = {"software": junos_os_type}
    response = requests.post(
        _url_for("software_releases"), json=payload, verify=VERIFY_SSL, timeout=TIMEOUT
    )
    if response.ok and len(response.content):
        return FeatureExplorerResponse(success=True, response=response.json())
    return FeatureExplorerResponse(success=False, error=response.text or "Empty response from API.")


@mcp.tool
def models_compatible_with_release(
    junos_version: Annotated[str, "A JUNOS software version like 25.1R2"],
    junos_os_type: Annotated[str, "One of ['Junos OS', 'Junos OS Evolved']"] = "Junos OS",
) -> FeatureExplorerResponse:
    """Fetch the models supported for the given JUNOS OS type and version.

    Arguments:
      junos_os_type: str - one of "Junos OS" or "Junos OS Evolved"
      junos_version: str - software version, for example "25.2R1"
    """
    if junos_os_type not in ["Junos OS", "Junos OS Evolved"]:
        raise ValueError("junos_os_type must be one of ['Junos OS', 'Junos OS Evolved']")
    url = _url_for("models_for_release").format(junos_os_type=junos_os_type, version=junos_version)
    response = requests.get(url, verify=VERIFY_SSL, timeout=TIMEOUT)
    if response.ok and len(response.content):
        return FeatureExplorerResponse(success=True, response=response.json())
    return FeatureExplorerResponse(success=False, error=response.text or "Empty response from API.")


@mcp.tool
def releases_compatible_with_model(
    model: Annotated[str, "A Juniper device model, like the ACX710."],
) -> FeatureExplorerResponse:
    """Fetch the releases compatible with the given model."""
    product_key = _get_pid_for_model(model)
    url = _url_for("releases_for_model").format(product_key=product_key)
    response = requests.get(url, verify=VERIFY_SSL, timeout=TIMEOUT)
    if response.ok and len(response.content):
        return FeatureExplorerResponse(success=True, response=response.json())
    return FeatureExplorerResponse(success=False, error=response.text or "Empty response from API.")


@mcp.tool
def features_for_model_on_junos_version(
    model: Annotated[str, "A Juniper device model, like the ACX710."],
    junos_version: Annotated[str, "A JUNOS software version like 25.1R2"],
    junos_os_type: Annotated[str, "One of ['Junos OS', 'Junos OS Evolved']"] = "Junos OS",
) -> FeatureExplorerResponse:
    """Fetch the features for a given model on a specific release."""
    payload = {"software": junos_os_type, "release": junos_version, "platform": model}
    response = requests.post(
        _url_for("features_for_model"), json=payload, verify=VERIFY_SSL, timeout=TIMEOUT
    )
    if response.ok and len(response.content):
        return FeatureExplorerResponse(success=True, response=response.json())
    return FeatureExplorerResponse(success=False, error=response.text or "Empty response from API.")


@mcp.tool
def feature_tree() -> FeatureExplorerResponse:
    """Fetch the feature tree, including all features and their keys."""
    response = requests.get(_url_for("feature_tree"), verify=VERIFY_SSL, timeout=TIMEOUT)
    if response.ok and len(response.content):
        return FeatureExplorerResponse(success=True, response=response.json())
    return FeatureExplorerResponse(success=False, error=response.text or "Empty response from API.")


@mcp.tool
def feature_details(
    feature_key: Annotated[
        str, "The unique alphanumeric key for the feature, can be found in feature tree."
    ],
) -> FeatureExplorerResponse:
    """Fetch the details of a specific feature."""
    url = _url_for("feature_details").format(feature_key=feature_key)
    response = requests.get(url, verify=VERIFY_SSL, timeout=TIMEOUT)
    if response.ok and len(response.content):
        return FeatureExplorerResponse(success=True, response=response.json())
    return FeatureExplorerResponse(success=False, error=response.text or "Empty response from API.")


@mcp.tool
def product_keys() -> FeatureExplorerResponse:
    """Fetch the product IDs for all categories."""
    catalog = None
    error = None
    catalog = _build_platform_catalog()
    if catalog and len(catalog.keys()):
        return FeatureExplorerResponse(success=True, response=catalog)
    return FeatureExplorerResponse(success=False, error=error or "Empty response from API.")

if __name__ == '__main__':  # pragma: nocover
    from jnpr_pathfinder_mcp.helpers import run_cli
    run_cli(prog="Juniper Feature Explorer MCP Server", server=mcp)
