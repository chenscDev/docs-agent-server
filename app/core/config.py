"""应用配置：从环境变量 / .env 加载。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """服务运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"
    database_url: str = "sqlite:///./data/docs_agent.db"

    # 客户端 API 鉴权（P2-D4 / P3-D13）；有效 Token 非空则要求 Bearer
    api_token: str = ""
    # 多个有效 Token，逗号/分号/空白分隔（与 api_token 并集）
    api_tokens: str = ""
    # 已作废 Token（仍写在 API_TOKENS 里也会被拒绝）
    api_tokens_revoked: str = ""
    # 热作废文件：每行一个 Token；改文件无需重启进程
    api_tokens_revoked_file: str = "./data/api_tokens_revoked.local"

    # 上传与解析（D2）
    upload_dir: str = "./data/uploads"
    extracted_dir: str = "./data/extracted"
    max_upload_mb: int = 20

    # 切分（D3）
    chunk_size: int = 500
    chunk_overlap: int = 80

    # 向量索引（D4）；通义 embedding 单批上限为 10
    faiss_dir: str = "./data/faiss"
    embedding_batch_size: int = 10
    # P3-D11：增量索引；失败自动全量 rebuild
    faiss_incremental: bool = True

    # P3-D1 检索重排
    rerank_enabled: bool = True
    rerank_model: str = "gte-rerank-v2"
    rerank_candidate_k: int = 20
    rerank_api_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )
    rerank_timeout_sec: float = 20.0
    rerank_max_doc_chars: int = 1200

    # P3-D2 引用：默认不「无 [n] 仍挂前 3 条」；演示可开
    citation_fallback_top3: bool = False

    # P3-D5 扫描 PDF OCR（文本层不足时走通义 qwen-vl-ocr）
    ocr_enabled: bool = True
    ocr_model: str = "qwen-vl-ocr-latest"
    ocr_max_pages: int = 20
    ocr_min_chars: int = 40
    ocr_timeout_sec: float = 60.0

    # AI 短视频（/v1/video/*，与问答主路径隔离）
    # auto | remotion | ffmpeg
    video_renderer: str = "auto"
    video_output_dir: str = "./data/video_out"
    video_public_base_url: str = ""  # 空则用相对 /cdn/video/
    remotion_project_dir: str = "./video-renderer"
    video_render_timeout_sec: int = 300
    video_max_concurrent: int = 1


@lru_cache
def get_settings() -> Settings:
    """获取单例配置。"""
    return Settings()
