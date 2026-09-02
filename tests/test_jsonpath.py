import pytest

from noisefloor.jsonpath import JsonPath, JsonPathError

DOC = {
    "confidence": "high",
    "citations": [
        {"source": "a.md", "score": 0.7},
        {"source": "b.md", "score": 0.4},
    ],
}


def test_top_level_key() -> None:
    assert JsonPath.parse("confidence").extract(DOC) == ["high"]


def test_indexed_element() -> None:
    assert JsonPath.parse("citations[0].source").extract(DOC) == ["a.md"]


def test_wildcard_yields_every_element() -> None:
    assert JsonPath.parse("citations[].source").extract(DOC) == ["a.md", "b.md"]


def test_missing_key_is_empty_not_an_error() -> None:
    assert JsonPath.parse("nope").extract(DOC) == []
    assert JsonPath.parse("citations[].nope").extract(DOC) == []


def test_index_out_of_range_is_empty() -> None:
    assert JsonPath.parse("citations[9].source").extract(DOC) == []


def test_indexing_a_non_list_is_empty() -> None:
    assert JsonPath.parse("confidence[0]").extract(DOC) == []


def test_is_multi_flags_wildcards() -> None:
    assert JsonPath.parse("citations[].source").is_multi
    assert not JsonPath.parse("citations[0].source").is_multi


@pytest.mark.parametrize("raw", ["", "a..b", "a[", "a[x]", "a[-1]", "[0]", "a.", ".a"])
def test_malformed_paths_are_rejected_at_parse_time(raw: str) -> None:
    """Suite authors get the error at load time, not mid-run."""
    with pytest.raises(JsonPathError):
        JsonPath.parse(raw)
