"""内置角色出厂文案的同步规则。

只测纯判断函数，不碰数据库：这里的重点是「不许把管理员改过的名字刷回去」。
"""

from app.authorization.domain.catalog import ROLE_BY_CODE
from app.authorization.seed import should_sync_builtin_text
from app.models.rbac import Role


def _role(code: str, name: str, *, is_system: bool = True) -> Role:
    return Role(code=code, name=name, is_system=is_system, status="active")


def test_legacy_name_is_synced() -> None:
    """老库里的「受限访客」要跟着代码改成「受限用户」。"""
    role = _role("guest", "受限访客")
    assert should_sync_builtin_text(role, ROLE_BY_CODE["guest"]) is True


def test_empty_name_is_synced() -> None:
    assert should_sync_builtin_text(_role("guest", ""), ROLE_BY_CODE["guest"]) is True


def test_admin_rename_is_kept() -> None:
    """管理员在后台改过的名字不能被 seed 覆盖。"""
    role = _role("guest", "试用账号")
    assert should_sync_builtin_text(role, ROLE_BY_CODE["guest"]) is False


def test_up_to_date_name_is_left_alone() -> None:
    role = _role("guest", "受限用户")
    assert should_sync_builtin_text(role, ROLE_BY_CODE["guest"]) is False


def test_custom_role_is_never_touched() -> None:
    role = _role("guest", "受限访客", is_system=False)
    assert should_sync_builtin_text(role, ROLE_BY_CODE["guest"]) is False


def test_guest_is_a_restricted_account_not_a_visitor() -> None:
    """guest 是已登录的受限账号：只有最低限度的页面权限，不含任何管理动作。"""
    guest = ROLE_BY_CODE["guest"]
    assert guest.name == "受限用户"
    # 个人中心是登录账号的必备附属页（改密码 / 看额度），其内部写操作仍受控。
    # 侧边栏只给它进得去的那几个入口，避免受限账号看到一堆点不开的图标。
    assert guest.permissions == frozenset(
        {
            "chat.access",
            "chat.write",
            "flow.access",
            "me.access",
            "sidebar.show",
            "sidebar.entry.home",
            "sidebar.entry.chat",
            "sidebar.entry.flow",
            "sidebar.entry.me",
        }
    )
