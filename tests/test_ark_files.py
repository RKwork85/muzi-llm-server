"""方舟文件只属于上传者。"""

from __future__ import annotations

import uuid

from app.api.v1.endpoints.ark import _public_image_usage, _usage_amount, owns_ark_file


def test_ark_file_is_visible_only_to_its_owner() -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    assert owns_ark_file(owner, owner) is True
    assert owns_ark_file(owner, other) is False


def test_image_usage_falls_back_to_output_tokens() -> None:
    total, prompt, completion = _usage_amount(
        {"usage": {"generated_images": 1, "output_tokens": 9408}}
    )
    assert (total, prompt, completion) == (9408, 0, 9408)


def test_image_usage_does_not_add_output_tokens_twice() -> None:
    total, prompt, completion = _usage_amount(
        {"usage": {"generated_images": 8, "output_tokens": 23107, "total_tokens": 23107}}
    )
    assert (total, prompt, completion) == (23107, 0, 23107)


def test_image_count_is_not_billed_as_tokens() -> None:
    assert _usage_amount({"usage": {"generated_images": 2}}) == (0, 0, 0)


def test_public_image_usage_matches_billed_tokens() -> None:
    public, meter = _public_image_usage(
        {"usage": {"generated_images": 1, "output_tokens": 4056, "total_tokens": 4056}},
        prompt="画一只猫",
        images=["https://example.com/ref.png"],
    )
    assert public["prompt_tokens"] == 0
    assert public["completion_tokens"] == 4056
    assert public["total_tokens"] == 4056
    assert public["generated_images"] == 1
    visible = [
        row for row in public["breakdown"]
        if row["attribution_method"] != "unavailable" and row["token_count"]
    ]
    user = next(row for row in visible if row["category"] == "user")
    image = next(row for row in visible if row["direction"] == "output" and row["category"] == "image")
    assert user["direction"] == "input"
    assert user["token_count"] == 4
    assert user["attribution_method"] == "heuristic_estimated"
    assert image["token_count"] == 4056
    assert image["attribution_method"] == "provider_reported"
    assert not any(row["category"] == "other_output" for row in public["breakdown"])
    assert meter is not None
    assert meter["usage_source"] == "image"


def test_public_image_usage_splits_provider_input_like_text() -> None:
    public, _meter = _public_image_usage(
        {"usage": {"input_tokens": 100, "output_tokens": 3900, "total_tokens": 4000}},
        prompt="猫",
    )
    assert (public["prompt_tokens"], public["completion_tokens"], public["total_tokens"]) == (100, 3900, 4000)
    user = next(row for row in public["breakdown"] if row["category"] == "user")
    other = next(row for row in public["breakdown"] if row["category"] == "other_input")
    image = next(row for row in public["breakdown"] if row["category"] == "image")
    assert user["token_count"] == 1
    assert other["token_count"] == 99
    assert other["attribution_method"] == "heuristic_estimated"
    assert image["token_count"] == 3900
    assert image["attribution_method"] == "provider_reported"
