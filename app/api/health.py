"""健康检查。"""

from fastapi import APIRouter, Response

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """供本地与部署探活。"""
    return {"status": "ok"}


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """WebView 常自动请求；返回空响应避免 401/404 干扰页内播放。"""
    return Response(status_code=204)
