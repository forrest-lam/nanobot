from nanobot.qqchat_compat.tool_policy import ToolPolicy


def test_tool_policy_blocks_exec_file_and_cron() -> None:
    policy = ToolPolicy(allowed_tools={"web_search", "search_messages"})

    assert policy.is_allowed("web_search") is True
    assert policy.is_allowed("search_messages") is True
    assert policy.is_allowed("exec") is False
    assert policy.is_allowed("read_file") is False
    assert policy.is_allowed("write_file") is False
    assert policy.is_allowed("list_dir") is False
    assert policy.is_allowed("cron") is False
