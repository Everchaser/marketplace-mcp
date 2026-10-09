import importlib

import pytest

BASE = "https://market.yandex.ru"


@pytest.mark.parametrize(
    "url",
    [
        BASE + "/card/kettle/123?sku=45",
        BASE + "/product/123",
        BASE + "/product--kettle/123",
        BASE + ":443/card/чайник/123",
    ],
)
def test_exact_card_url_is_retained(url):
    assert importlib.import_module("marketplace_mcp.urls").validate_url(url) == url


@pytest.mark.parametrize(
    "tail",
    [
        "http://market.yandex.ru/card/a/1",
        BASE + ".evil/card/a/1",
        "https://u@market.yandex.ru/card/a/1",
        BASE + ":444/card/a/1",
        BASE + "/card/a/١",
        BASE + "/card/a/1#x",
        BASE + "/card/%2e%2e/1",
        BASE + "/card/a%2fb/1",
        BASE + "/card/a/1/",
        BASE + "/search?text=a",
        BASE + "/card/a/1?x=%0a",
        BASE + "/card/../1",
        BASE + "/card/a/1\\x",
    ],
)
def test_reject_unsafe_card_url(tail):
    with pytest.raises(ValueError):
        importlib.import_module("marketplace_mcp.urls").validate_url(tail)


@pytest.mark.parametrize(
    "url",
    [BASE + "/card/a/1?x=%252525250a", BASE + "/card/a/1?x=%zz", BASE + "/card/a/1?x=%", BASE + "/card/a/1#"],
)
def test_reject_ambiguous_escaping_and_empty_fragment(url):
    with pytest.raises(ValueError):
        importlib.import_module("marketplace_mcp.urls").validate_url(url)
