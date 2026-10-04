"""GET /api/checkout/regions：地区与国家呼叫码列表、马来西亚州属列表与默认电话地区。

每条测试的文档字符串写明它守住的规则：SHOP-TASK-023 验收标准的原句，或 docs/DESIGN.md 1.11
「权限与资料保护」中结账第 1 步国家码默认 +60 的约定。

接口不访问数据库：会话依赖换成一调用就抛错的替身，任何请求若用到会话都会失败。
"""

from __future__ import annotations

import re

import phonenumbers
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.services.shipping import MY_STATE_CODES

URL = "/api/checkout/regions"

MY_STATE_NAMES = [
    ("MY-01", "Johor"),
    ("MY-02", "Kedah"),
    ("MY-03", "Kelantan"),
    ("MY-04", "Melaka"),
    ("MY-05", "Negeri Sembilan"),
    ("MY-06", "Pahang"),
    ("MY-07", "Pulau Pinang"),
    ("MY-08", "Perak"),
    ("MY-09", "Perlis"),
    ("MY-10", "Selangor"),
    ("MY-11", "Terengganu"),
    ("MY-12", "Sabah"),
    ("MY-13", "Sarawak"),
    ("MY-14", "Wilayah Persekutuan Kuala Lumpur"),
    ("MY-15", "Wilayah Persekutuan Labuan"),
    ("MY-16", "Wilayah Persekutuan Putrajaya"),
]


def _no_database():
    raise AssertionError("the regions endpoint must not open a database session")


@pytest.fixture
def client() -> TestClient:
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_session] = _no_database
    return TestClient(app)


@pytest.fixture
def body(client: TestClient) -> dict:
    response = client.get(URL)
    assert response.status_code == 200
    return response.json()


# ---- 响应结构 ----


def test_response_has_only_the_three_fields(body: dict) -> None:
    """SHOP-TASK-023 验收：响应由 regions、my_states 与 default_phone_region 组成；
    「国家名称不由接口提供」：地区项只有代码与呼叫码，没有名称字段。"""
    assert set(body) == {"regions", "my_states", "default_phone_region"}
    assert all(set(item) == {"code", "calling_code"} for item in body["regions"])
    assert all(set(item) == {"code", "name"} for item in body["my_states"])


# ---- 地区列表 ----


def test_malaysia_and_singapore_have_calling_codes_60_and_65(body: dict) -> None:
    """SHOP-TASK-023 验收「MY 与 SG 在列表中且呼叫码分别为 60 与 65」：呼叫码是整数。"""
    calling = {item["code"]: item["calling_code"] for item in body["regions"]}

    assert calling["MY"] == 60
    assert calling["SG"] == 65
    assert type(calling["MY"]) is int


def test_region_codes_are_two_uppercase_letters_sorted_and_unique(body: dict) -> None:
    """SHOP-TASK-023 验收「每项为地区代码与国家呼叫码（整数），按地区代码排序」与
    「不含非地理编码」：每项代码都是两位大写字母（没有 001 这类非地理编码）、已排序、不重复。"""
    codes = [item["code"] for item in body["regions"]]

    assert codes
    assert all(re.fullmatch(r"[A-Z]{2}", code) for code in codes)
    assert codes == sorted(codes)
    assert len(set(codes)) == len(codes)
    assert all(type(item["calling_code"]) is int for item in body["regions"])


def test_regions_are_exactly_what_phonenumbers_supports(body: dict) -> None:
    """SHOP-TASK-023 验收「regions 列出 phonenumbers 支持的全部两位地区代码」：代码集合等于
    phonenumbers.SUPPORTED_REGIONS，每项呼叫码与 phonenumbers 给出的相同。"""
    calling = {item["code"]: item["calling_code"] for item in body["regions"]}

    assert set(calling) == phonenumbers.SUPPORTED_REGIONS
    for code, calling_code in calling.items():
        assert calling_code == phonenumbers.country_code_for_region(code)


# ---- 马来西亚州属 ----


def test_my_states_are_the_16_official_malay_names_in_order(body: dict) -> None:
    """SHOP-TASK-023 验收「my_states 按 MY-01 到 MY-16 的顺序列出马来西亚州属代码
    与马来文官方名称」（Kelvin 2026-10-01 决定的名称）：恰好 16 项，顺序与名称逐项相同。"""
    states = [(item["code"], item["name"]) for item in body["my_states"]]

    assert len(states) == 16
    assert states == MY_STATE_NAMES


def test_my_state_codes_equal_shipping_state_codes(body: dict) -> None:
    """SHOP-TASK-023 验收「代码集合与 app/services/shipping.py 的 MY_STATE_CODES 一致」：
    州属下拉给出的每个代码运费查询都接受，运费接受的每个代码下拉里都有。"""
    assert {item["code"] for item in body["my_states"]} == MY_STATE_CODES


@pytest.mark.parametrize("lang", ["en", "zh", "ms"])
def test_state_names_do_not_change_with_language(client: TestClient, lang: str) -> None:
    """SHOP-TASK-023 验收「三种界面语言都用这一名称」：带 lang 参数时州属名称不变。"""
    response = client.get(URL, params={"lang": lang})

    assert response.status_code == 200
    assert [(s["code"], s["name"]) for s in response.json()["my_states"]] == MY_STATE_NAMES


# ---- 默认电话地区 ----


def test_default_phone_region_is_malaysia(body: dict) -> None:
    """DESIGN 1.11「权限与资料保护」结账第 1 步国家码默认 +60；SHOP-TASK-023 验收
    「default_phone_region 为 MY」：默认地区为 MY，且它在地区列表里、呼叫码为 60。"""
    calling = {item["code"]: item["calling_code"] for item in body["regions"]}

    assert body["default_phone_region"] == "MY"
    assert calling[body["default_phone_region"]] == 60


# ---- 接口行为 ----


def test_endpoint_never_touches_the_database(client: TestClient) -> None:
    """SHOP-TASK-023 验收「不访问数据库」：会话依赖换成会抛错的替身时接口照常 200；
    未配置数据库（其他要用会话的接口回答 503）时也照常 200。"""
    assert client.get(URL).status_code == 200

    no_db = TestClient(create_app(Settings(_env_file=None, database_url="")))
    assert no_db.get(URL).status_code == 200


def test_endpoint_needs_no_login_and_sets_no_cookie(client: TestClient) -> None:
    """SHOP-TASK-023 验收「不需要登录，不读写 cookie」：带不带 cookie 结果相同，
    响应不设 cookie；只读接口，写方法 405。"""
    anonymous = client.get(URL)
    client.cookies.set("__Host-shop_order_access", "anything")
    with_cookie = client.get(URL)

    for response in (anonymous, with_cookie):
        assert response.status_code == 200
        assert "set-cookie" not in response.headers
    assert anonymous.json() == with_cookie.json()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, URL, json={}).status_code == 405


def test_response_is_cacheable_as_static_data(client: TestClient) -> None:
    """SHOP-TASK-023 验收「响应可按静态数据缓存」：带可公开缓存的 Cache-Control，
    不是 no-store；两次请求的响应相同。"""
    first = client.get(URL)
    second = client.get(URL)

    cache_control = first.headers["cache-control"]
    assert "public" in cache_control
    assert "max-age=" in cache_control
    assert "no-store" not in cache_control
    assert first.json() == second.json()
