"""统一错误码。客户端只看到这里定义的安全文案。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500
    default_message = "服务内部错误"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        self.upstream_status: int | None = None
        super().__init__(self.message)


class PlanningResourceNotFoundError(AppError):
    code = "PLANNING_RESOURCE_NOT_FOUND"
    status_code = 404
    default_message = "Conversation or plan not found"


class PlanningConflictError(AppError):
    code = "PLANNING_CONFLICT"
    status_code = 409
    default_message = "Plan state changed; refresh and retry"


class PlanningPolicyErrorResponse(AppError):
    code = "PLANNING_POLICY_REJECTED"
    status_code = 422
    default_message = "Planning action is not allowed"


class ConfigurationError(AppError):
    code = "CONFIGURATION_ERROR"
    status_code = 500
    default_message = "模型服务配置不完整"


class UpstreamAuthError(AppError):
    code = "UPSTREAM_AUTH_ERROR"
    status_code = 401
    default_message = "模型服务认证失败"


class UpstreamRateLimitError(AppError):
    code = "UPSTREAM_RATE_LIMIT"
    status_code = 429
    default_message = "模型服务请求过于频繁，请稍后重试"


class UpstreamTimeoutError(AppError):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    default_message = "模型服务请求超时"


class UpstreamError(AppError):
    code = "UPSTREAM_ERROR"
    status_code = 502
    default_message = "模型服务暂时不可用"


class InvalidModelResponseError(AppError):
    code = "INVALID_MODEL_RESPONSE"
    status_code = 502
    default_message = "模型返回了无法解析的响应"


class InvalidToolArgumentsError(AppError):
    code = "INVALID_TOOL_ARGUMENTS"
    status_code = 400
    default_message = "工具参数无效"


class ToolNotAllowedError(AppError):
    code = "TOOL_NOT_ALLOWED"
    status_code = 403
    default_message = "请求的工具不在允许列表中"


class ToolExecutionError(AppError):
    code = "TOOL_EXECUTION_ERROR"
    status_code = 500
    default_message = "工具执行失败"


class ToolRoundLimitError(AppError):
    code = "TOOL_ROUND_LIMIT"
    status_code = 422
    default_message = "已达到工具调用轮数上限"


class InternalError(AppError):
    code = "INTERNAL_ERROR"
    status_code = 500
    default_message = "服务内部错误"


class ModelNotAllowedError(AppError):
    code = "MODEL_NOT_ALLOWED"
    status_code = 400
    default_message = "请求的模型不在允许列表中"


class PromptNotFoundError(AppError):
    code = "PROMPT_NOT_FOUND"
    status_code = 404
    default_message = "未找到指定提示词"


class PromptVariableError(AppError):
    code = "PROMPT_VARIABLE_MISSING"
    status_code = 400

    def __init__(self, missing: list[str]) -> None:
        names = ", ".join(missing)
        super().__init__(f"提示词变量缺失: {names}")


class SkillFlowError(AppError):
    """技能发现或加载失败。安全拒绝不能当成普通的无匹配。"""

    default_message = "技能暂时不可用"

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.status_code = {
            "SKILL_NOT_FOUND": 404,
            "BUNDLE_NOT_FOUND": 404,
            "SKILL_NOT_ACCESSIBLE": 403,
            "SKILL_SCOPE_UNAVAILABLE": 403,
            "SKILL_RESOURCE_NOT_ALLOWED": 403,
            "SKILL_PACKAGE_INVALID": 422,
            "SKILL_PACKAGE_TOO_LARGE": 422,
            "SKILL_RESOURCE_TOO_LARGE": 422,
            "SKILL_PATH_INVALID": 422,
            "SKILL_DIGEST_MISMATCH": 422,
            "SKILL_FRONTMATTER_INVALID": 422,
            "SKILL_BODY_EMPTY": 422,
            "SKILL_CONTENT_TOO_LARGE": 422,
            "SKILL_CONTEXT_BUDGET_EXCEEDED": 422,
            "SKILL_NAME_CONFLICT": 409,
            "SKILL_STATE_INVALID": 409,
            "SKILL_IN_USE": 409,
            "SKILL_RUNTIME_UNAVAILABLE": 503,
        }.get(code, 409)
        super().__init__(message or self.default_message)


class InvalidRequestError(AppError):
    code = "REQUEST_VALIDATION_ERROR"
    status_code = 422
    default_message = "请求参数无效"


class AuthFailedError(AppError):
    code = "AUTH_FAILED"
    status_code = 401
    default_message = "邮箱或密码错误"


class AccountDisabledError(AppError):
    code = "ACCOUNT_DISABLED"
    status_code = 403
    default_message = "账户已停用"


class AccountExistsError(AppError):
    code = "ACCOUNT_EXISTS"
    status_code = 409
    default_message = "邮箱或用户名已被使用"


class LoginRequiredError(AppError):
    code = "LOGIN_REQUIRED"
    status_code = 401
    default_message = "请先登录"


class ConnectorNotFoundError(AppError):
    code = "CONNECTOR_NOT_FOUND"
    status_code = 404
    default_message = "未找到该连接服务"


class McpUnavailableError(AppError):
    code = "MCP_UNAVAILABLE"
    status_code = 503
    default_message = "外部连接暂不可用"


class DatabaseUnavailableError(AppError):
    code = "DATABASE_UNAVAILABLE"
    status_code = 503
    default_message = "数据库未就绪"


class AgentTaskNotFoundError(AppError):
    code = "AGENT_TASK_NOT_FOUND"
    status_code = 404
    default_message = "未找到指定任务"


class AgentTaskLimitError(AppError):
    code = "AGENT_TASK_LIMIT"
    status_code = 429
    default_message = "活动任务数量已达上限"


class AgentPolicyRejectedError(AppError):
    """策略拒绝。code 由 PolicyError 决定，文案保持固定。"""

    status_code = 422
    default_message = "请求策略无效"

    def __init__(self, code: str) -> None:
        self.code = code
        if code == "AGENT_TOOL_NOT_ALLOWED":
            self.status_code = 403
            self.default_message = "请求的工具不在允许列表中"
        elif code == "TOOLSET_UNKNOWN":
            self.status_code = 422
            self.default_message = "未知的工具集"
        elif code == "TOOLSET_UNAVAILABLE":
            self.status_code = 403
            self.default_message = "工具集当前不可用"
        super().__init__(self.default_message)


class FileDownloadError(AppError):
    """下载令牌无效或过期。响应不区分不存在和无权访问。"""

    def __init__(self, code: str) -> None:
        if code == "DOWNLOAD_EXPIRED":
            self.code = "DOWNLOAD_EXPIRED"
            self.status_code = 410
            self.default_message = "下载链接已过期"
        else:
            self.code = "ARTIFACT_NOT_FOUND"
            self.status_code = 404
            self.default_message = "文件不存在或无权访问"
        super().__init__(self.default_message)


def error_payload(exc: AppError, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "request_id": request_id,
        }
    }


def is_retryable_provider_error(exc: Exception) -> bool:
    """只重试超时、连接失败和 5xx。不重试认证失败、限流和工具链路。"""
    if isinstance(exc, AppError):
        return False
    if type(exc).__name__ in {"APITimeoutError", "APIConnectionError"}:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


def map_provider_exception(exc: Exception) -> AppError:
    if isinstance(exc, AppError):
        return exc
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if status == 402:
        # 对外仍是 UPSTREAM_ERROR。原始状态只留给 Agent 适配层识别余额不足。
        error = UpstreamError()
        error.upstream_status = 402
        return error
    if name in {"AuthenticationError", "PermissionDeniedError"} or status in {401, 403}:
        return UpstreamAuthError()
    if name == "RateLimitError" or status == 429:
        return UpstreamRateLimitError()
    if name == "APITimeoutError" or isinstance(exc, TimeoutError):
        return UpstreamTimeoutError()
    if name in {"InternalServerError", "APIConnectionError", "APIError"}:
        return UpstreamError()
    if isinstance(status, int) and status >= 500:
        return UpstreamError()
    return UpstreamError()
