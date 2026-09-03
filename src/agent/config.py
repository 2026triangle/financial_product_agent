"""
================================================================================
에이전트 환경 설정 및 경로 관리 모듈 (config.py)
--------------------------------------------------------------------------------
기능:
1. .env 파일 기반 API 키 및 Base URL 자동 로드
2. LLM 기본 모델 및 하이퍼파라미터 설정
3. DB 및 데이터 엔드포인트 경로 자동 해석 및 관리
================================================================================
"""

import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# 기본 경로 계산
# AGENT_DIR: c:/.../triangle/agent
AGENT_DIR = Path(__file__).resolve().parent
# TRIANGLE_ROOT: c:/.../triangle
TRIANGLE_ROOT = AGENT_DIR.parent
# DATA_DIR: c:/.../triangle/data
DATA_DIR = TRIANGLE_ROOT / "data"
# DB_PATH: c:/.../triangle/data/db/finance_master.db
DEFAULT_DB_PATH = DATA_DIR / "db" / "finance_master.db"

# 상위 경로를 sys.path에 추가하여 data/endpoint 등 모듈 참조 가능하게 설정
for path in [str(AGENT_DIR), str(TRIANGLE_ROOT), str(DATA_DIR)]:
    if path not in sys.path:
        sys.path.insert(0, path)

# .env 파일 로드 (.agent/.env 또는 agent/.env 또는 triangle/.env 우선순위 탐색)
env_paths = [
    AGENT_DIR / ".env",
    TRIANGLE_ROOT / ".env",
]
for p in env_paths:
    if p.exists():
        load_dotenv(p)
        break
else:
    load_dotenv()  # 시스템 환경변수 또는 기본 .env 로드


@dataclass
class ModelConfig:
    """LLM 모델 호출 관련 설정"""
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    default_model: str = field(default_factory=lambda: os.getenv("DEFAULT_MODEL", "gpt-4o-mini"))
    temperature: float = float(os.getenv("DEFAULT_TEMPERATURE", "0.1"))
    max_tokens: int = int(os.getenv("DEFAULT_MAX_TOKENS", "4096"))
    timeout: float = float(os.getenv("DEFAULT_TIMEOUT", "60.0"))
    max_retries: int = int(os.getenv("DEFAULT_MAX_RETRIES", "3"))


@dataclass
class DatabaseConfig:
    """DB 및 엔드포인트 관련 설정"""
    db_path: str = field(default_factory=lambda: os.getenv("FINANCE_DB_PATH", str(DEFAULT_DB_PATH)))
    bge_m3_model: str = field(default_factory=lambda: os.getenv("BGE_M3_MODEL", "BAAI/bge-m3"))
    meta_top_k: int = int(os.getenv("META_TOP_K", "5"))
    meta_alpha: float = float(os.getenv("META_ALPHA", "0.7"))


@dataclass
class AgentSettings:
    """에이전트 전체 통합 설정"""
    model: ModelConfig = field(default_factory=ModelConfig)
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    debug: bool = os.getenv("AGENT_DEBUG", "false").lower() in ("true", "1", "yes")


# 전역 기본 설정 인스턴스
config = AgentSettings()
