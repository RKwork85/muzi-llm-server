"""方舟文件只属于上传者。"""

from __future__ import annotations

import uuid

from app.api.v1.endpoints.ark import _usage_amount, owns_ark_file


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
