"""页面清单：页面 → 权限映射的唯一事实来源。

为什么要有这一份清单
--------------------
「这个页面需要什么权限进入」和「这个页面里有哪些功能开关」是两个必须存在、
但只能存在一份的答案。以前它们分散在三处：后端权限目录里的 *.access、
前端 router/pageAccess.js 的 PAGES、后台角色面板里的 PAGE_ROUTES 手写表。
新增一个页面要改三个地方，漏一个就变成「勾了不生效」。

现在统一到这里：后台面板按它分组、前端守卫按它判定，
后端再加一条测试保证每个码都真实存在于 PERMISSION_CATALOG。

新增页面时的做法
----------------
1. 在这里加一条 PageSpec（页面名、地址、浏览权限码、该页功能码）；
2. 前端 routes.js 对应路由上标 `meta: { page: '<key>' }`。

除此之外不需要改任何逻辑——后台面板的分组、守卫的判定都会自动跟上。

字段说明
--------
- access：进入该页面需要的权限码（`*.access`）。None 表示登录即可。
- features：该页面下的功能/写操作权限。它们决定「进去之后能做什么」，
  与 access 正交：取消 access = 进不去，保留 access 去掉 features = 只读。
- public：对外公开页面（品牌首页、资讯等）。未登录访问照旧放行；已登录访问
  仍按 access 判定，这样「登录后能不能看」也能由角色收窄，而不是一刀切。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSpec:
    key: str
    name: str
    path: str
    access: str | None = None
    features: tuple[str, ...] = ()
    public: bool = False


#: 页面清单。顺序即后台面板的展示顺序（按用户实际动线排列）。
PAGE_CATALOG: tuple[PageSpec, ...] = (
    PageSpec(
        # 品牌官网：未登录也能看；登录后仍按 brand.access 判定，便于把某些角色
        # 收窄成「业务页面可见、官网不可见」这类组合。
        key="brand",
        name="品牌首页",
        path="/",
        access="brand.access",
        public=True,
    ),
    PageSpec(
        key="read",
        name="资讯",
        path="/muzi/read",
        access="read.access",
        public=True,
    ),
    PageSpec(
        key="chat",
        name="对话",
        path="/home",
        access="chat.access",
        features=("chat.write",),
    ),
    PageSpec(
        key="history",
        name="历史记录",
        path="/history",
        access="history.access",
        features=("history.write",),
    ),
    PageSpec(
        key="assets",
        name="资产",
        path="/assets",
        access="assets.access",
        # 资产目前没有独立的写权限：上传随创作/对话的写权限一起校验。
    ),
    PageSpec(
        key="creation",
        name="创作",
        path="/creation",
        access="creation.access",
        features=("content.write",),
    ),
    PageSpec(
        key="flow",
        name="画布",
        path="/creation/gen/flow",
        access="flow.access",
        features=("flow.write",),
    ),
    PageSpec(
        key="docs",
        name="文档空间",
        path="/muzidoc",
        access="docs.access",
        features=("docs.write",),
    ),
    PageSpec(
        key="apps",
        name="应用",
        path="/apps",
        access="plugin.access",
        features=("plugin.write",),
    ),
    PageSpec(
        key="connectors",
        name="连接器",
        path="/connectors",
        access="mcp.access",
        features=("mcp.connect", "mcp.execute"),
    ),
    PageSpec(
        key="me",
        name="个人中心",
        path="/me",
        # 个人中心也走浏览权限码：它虽然是登录后的默认落地页，但「能不能看自己
        # 的额度/充值入口」同样是运营上需要按角色收窄的能力，不再是无条件开放。
        access="me.access",
        features=("profile.write", "recharge.write"),
    ),
    PageSpec(
        key="admin",
        name="管理后台",
        path="/admin",
        access="admin.access",
        features=(
            "user.read",
            "user.manage",
            "user.delete",
            "role.read",
            "role.manage",
            "permission.read",
            "permission.manage",
            "audit.read",
            "quota.read",
            "quota.manage",
            "prompt.manage",
        ),
    ),
)

PAGE_BY_KEY: dict[str, PageSpec] = {spec.key: spec for spec in PAGE_CATALOG}

#: 所有「进入页面」用的权限码。由清单推导，不再手写第二份。
PAGE_ACCESS_CODES: frozenset[str] = frozenset(
    spec.access for spec in PAGE_CATALOG if spec.access
)

#: 所有归属于某个页面的功能码（写操作、管理动作）。用于从「其它权限」里排除，
#: 让后台面板可以把每个开关放到它真正生效的页面下面。
PAGE_FEATURE_CODES: frozenset[str] = frozenset(
    code for spec in PAGE_CATALOG for code in spec.features
)


def pages_requiring_permission() -> tuple[PageSpec, ...]:
    """有浏览权限码的页面。全员开放在清单里不存在这种页面时返回空元组。"""
    return tuple(spec for spec in PAGE_CATALOG if spec.access)


def page_for_access(code: str) -> PageSpec | None:
    """按浏览权限码反查页面（后台面板与测试用）。"""
    for spec in PAGE_CATALOG:
        if spec.access == code:
            return spec
    return None
