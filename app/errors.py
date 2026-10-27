from __future__ import annotations


class AppError(Exception):
    status_code = 400
    title = "请求无法完成"

    def __init__(self, message: str, *, title: str | None = None):
        super().__init__(message)
        self.message = message
        if title:
            self.title = title


class ValidationError(AppError):
    status_code = 400
    title = "数据校验失败"


class AuthRequired(AppError):
    status_code = 401
    title = "需要登录"


class PermissionDenied(AppError):
    status_code = 403
    title = "没有权限"


class NotFoundError(AppError):
    status_code = 404
    title = "资源不存在"


class ConflictError(AppError):
    status_code = 409
    title = "数据冲突"


class RateLimited(AppError):
    status_code = 429
    title = "请求过于频繁"
