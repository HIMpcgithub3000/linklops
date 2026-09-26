"""Agent 2 tests. One per contract rule, named after the rule."""
from app.services.mentions import extract_mentions as ex


def test_basic_and_order_and_dedupe():
    assert ex("Hey @jane.doe, check this @bob and @jane.doe again") == ["jane.doe", "bob"]

def test_case_insensitive_lowercased_output():
    assert ex("@Jane and @JANE") == ["jane"]

def test_email_is_not_a_mention():
    assert ex("write to email@example.com please") == []

def test_fenced_code_block_is_not_a_mention():
    assert ex("see ```\n@notamention\n``` but @real counts") == ["real"]

def test_inline_code_is_not_a_mention():
    assert ex("use `@decorator` and ping @alice") == ["alice"]

def test_double_at_yields_nothing():
    assert ex("@@x") == []

def test_blanking_code_cannot_manufacture_a_mention():
    """Removing a code span could join '@' and 'user'. Blanking cannot."""
    assert ex("@`x`user") == []

def test_handle_length_is_bounded():
    assert ex("@" + "a" * 65) == ["a" * 64]

def test_trailing_sentence_punctuation_is_not_part_of_the_handle():
    assert ex("thanks @bob.") == ["bob"]
