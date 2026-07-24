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


@lru_cache
def get_settings() -> Settings:
    """获取单例配置。"""
    return Settings()
