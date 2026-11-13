from app.chat.ask_runs import can_replace_status


def test_terminal_status_is_not_replaced_by_running_or_a_second_terminal() -> None:
    assert can_replace_status("running", "completed")
    assert can_replace_status("running", "failed")
    assert can_replace_status("running", "cancelled")
    assert not can_replace_status("completed", "running")
    assert not can_replace_status("completed", "failed")
    assert not can_replace_status("failed", "completed")
    assert not can_replace_status("cancelled", "running")
    assert not can_replace_status("running", "running")
    assert not can_replace_status("unknown", "completed")
