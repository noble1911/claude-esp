from esp_gateway.butler import parse_sse_payload
from esp_gateway.session import pop_sentence


def test_parse_delta():
    ev = parse_sse_payload('{"delta":"hi"}')
    assert ev.kind == "delta" and ev.text == "hi"


def test_parse_done():
    assert parse_sse_payload("[DONE]").kind == "done"


def test_parse_device_card():
    ev = parse_sse_payload('{"type":"device_card","card":{"op":"card","title":"X"}}')
    assert ev.kind == "card" and ev.card["title"] == "X"


def test_parse_visual_content():
    ev = parse_sse_payload('{"type":"visual_content","content":"# md"}')
    assert ev.kind == "visual" and ev.text == "# md"


def test_parse_garbage():
    assert parse_sse_payload("not json") is None
    assert parse_sse_payload("") is None


def test_pop_sentence_basic():
    assert pop_sentence("Hello there. Rest") == ("Hello there.", " Rest")


def test_pop_sentence_incomplete():
    assert pop_sentence("no end yet") == ("", "no end yet")


def test_pop_sentence_decimal_not_split():
    # "3.14" must not split on the decimal point
    assert pop_sentence("pi is 3.14 ok") == ("", "pi is 3.14 ok")


def test_pop_sentence_question():
    assert pop_sentence("How are you? More") == ("How are you?", " More")
