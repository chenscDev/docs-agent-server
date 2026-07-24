"""健康检查。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """供本地与部署探活。"""
    return {"status": "ok"}
