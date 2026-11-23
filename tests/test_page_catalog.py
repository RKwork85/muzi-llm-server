"""页面清单的自洽性校验（不依赖数据库）。

页面清单是「页面 → 权限」的唯一事实来源，所以它一旦写错，后台面板与路由
守卫会同时错，而且不会有任何报错——只能靠静态校验把关。
"""

from __future__ import annotations

from app.authorization.domain.catalog import (
    PERMISSION_BY_CODE,
    is_page_permission,
    permission_min_rank,
    role_grant_rank,
)
from app.authorization.domain.pages import (
    PAGE_ACCESS_CODES,
    PAGE_BY_KEY,
    PAGE_CATALOG,
    PAGE_FEATURE_CODES,
    page_for_access,
)


def test_page_keys_are_unique() -> None:
    keys = [spec.key for spec in PAGE_CATALOG]
    assert len(keys) == len(set(keys))


def test_every_page_permission_exists_in_catalog() -> None:
    """清单里出现的每个码都必须真实存在，否则后台勾了也存不进去。"""
    for spec in PAGE_CATALOG:
        if spec.access is not None:
            assert spec.access in PERMISSION_BY_CODE, f"{spec.key}: {spec.access}"
            assert is_page_permission(spec.access), f"{spec.key}: {spec.access}"
        for code in spec.features:
            assert code in PERMISSION_BY_CODE, f"{spec.key}: {code}"


def test_page_paths_are_site_absolute() -> None:
    for spec in PAGE_CATALOG:
        assert spec.path.startswith("/"), spec.key


def test_every_page_has_browse_permission() -> None:
    """所有一级页面都必须有浏览权限码。

    「登录即可进入」的页面等于绕过了页面授权：管理员无法把某个角色收窄成
    「不能看这一页」。所以清单里不允许出现 access=None，新增页面时漏写浏览码
    会在这里直接失败，而不是等到线上发现某个页面谁都能进。
    """
    missing = [spec.key for spec in PAGE_CATALOG if spec.access is None]
    assert not missing, "以下页面缺少浏览权限码：" + ", ".join(missing)


def test_public_pages_still_have_browse_permission() -> None:
    """对外公开页面也要有浏览码：未登录放行，登录后仍可被角色收窄。"""
    for spec in PAGE_CATALOG:
        if spec.public:
            assert spec.access, f"{spec.key} 标了 public 却没有浏览码"
            assert spec.access in PERMISSION_BY_CODE, f"{spec.key}: {spec.access}"


def test_access_codes_are_derived_not_handwritten() -> None:
    expected = {spec.access for spec in PAGE_CATALOG if spec.access}
    assert PAGE_ACCESS_CODES == expected
    assert PAGE_FEATURE_CODES == {code for spec in PAGE_CATALOG for code in spec.features}


def test_access_code_maps_back_to_its_page() -> None:
    """后台按浏览权限码反查页面（显示路径/名称），必须一对一。"""
    for code in PAGE_ACCESS_CODES:
        spec = page_for_access(code)
        assert spec is not None
        assert spec.key in PAGE_BY_KEY


def test_admin_page_features_are_above_lowest_rank() -> None:
    """后台入口的功能码门槛必须高于最低等级角色（guest, rank 20）。

    页面浏览码（admin.access）对所有角色开放配置，但页面内的管理功能
    （user.read / role.manage 等）仍只授予 admin 及以上——保证低等级角色
    就算被误勾了 admin.access，进去后也调不动任何数据。
    """
    admin = PAGE_BY_KEY["admin"]
    for code in admin.features:
        assert permission_min_rank(code) > role_grant_rank("guest"), (
            f"admin 页面功能码 {code} 的门槛必须高于访客等级"
        )


def test_page_browse_is_never_harder_to_grant_than_its_features() -> None:
    """能勾功能却勾不了页面的配置是坏配置。

    如果某个页面的浏览码门槛高于它的功能码，后台就会出现「功能能勾、浏览被过滤」
    的残缺分组，管理员无法表达「能进这个页面但不能写」这种最常见的最小授权。
    """
    for spec in PAGE_CATALOG:
        if not spec.access or not spec.features:
            continue
        access_rank = permission_min_rank(spec.access)
        feature_rank = min(permission_min_rank(code) for code in spec.features)
        assert access_rank <= feature_rank, (
            f"{spec.key}: 浏览码门槛 {access_rank} 高于功能码门槛 {feature_rank}"
        )


def test_every_page_feature_is_enforced_somewhere() -> None:
    """面板上的每个功能开关都必须在后端真的被检查过。

    只在前端生效的开关等于装饰：管理员取消勾选，界面挡住了，接口照旧放行。
    这条测试扫一遍 app/（排除权限目录本身），确保每个功能码都出现在后端代码里。
    新增页面功能码时如果忘了加校验，这里会直接失败。
    """
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[1] / "app"
    domain = app_root / "authorization" / "domain"
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in app_root.rglob("*.py")
        if domain not in path.parents
    )
    missing = sorted(code for code in PAGE_FEATURE_CODES if f'"{code}"' not in sources)
    assert not missing, "以下功能开关在后端没有任何校验：" + ", ".join(missing)
