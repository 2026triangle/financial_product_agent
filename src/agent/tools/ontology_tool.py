"""
================================================================================
온톨로지 검증 도구 연동 모듈 (tools/ontology_tool.py)
--------------------------------------------------------------------------------
기능:
1. src/ontology/endpoint/ontology_validator.py 의 OntologyValidator 연동
2. 논리 검증 트리플의 모순 검사 및 허용값 조회를 위한 lazy 싱글톤 제공
3. ToolManager 등록용 Tool 래퍼 및 OpenAI 호환 도구 스키마 제공

참고: QueryParser(agents/query_parser.py)는 이 모듈의 get_ontology_validator()를
직접 호출해 트리플을 검증한다 — LLM Tool Calling 왕복이나 ToolManager.execute()를
거치지 않는다. 검증은 결정론적 딕셔너리 조회이므로 그럴 필요가 없다
(dev_docs.md: "main에서 절대 도구를 호출하면 안됨" — 검증은 QueryParser 내부 로직).
register_ontology_tool()이 만드는 Tool은 다른 에이전트가 필요 시 LLM Tool Calling으로
허용값을 조회할 수 있도록 남겨두는 보조 경로다.

경로 참고(Task 6/8 이관): 온톨로지 TTL은 레포 루트 `ontology/`(대회 제출 규격),
검증기는 `src/ontology/endpoint/`에 있다. 데이터(`DATA_DIR`)는 네이버클라우드 DB로
이전될 예정이므로 온톨로지 경로가 거기 의존하지 않게 `ONTOLOGY_DIR`을 쓴다.
================================================================================
"""

import os
import sys
import logging
from typing import Dict, Any, List, Optional

# 상위 경로 모듈 로드 보장 (config 임포트 자체가 sys.path에 REPO_ROOT/SRC_DIR을 넣는 부수효과를 가짐)
from config import ONTOLOGY_DIR, SRC_DIR

try:
    from ontology.endpoint.ontology_validator import OntologyValidator
except ImportError:
    # config가 이미 sys.path에 SRC_DIR을 넣어두지만, 다른 진입점에서 config를 거치지
    # 않고 이 모듈이 먼저 임포트된 경우를 대비한 보정 (meta_search_tool.py와 동일한 패턴).
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    from ontology.endpoint.ontology_validator import OntologyValidator

from core.tool_manager import Tool, ToolManager, get_tool_manager

logger = logging.getLogger("agent.tools.ontology")

# 글로벌 단일 OntologyValidator 인스턴스 (Lazy Loading)
_validator_instance: Optional[OntologyValidator] = None


def get_ontology_validator(ontology_dir: Optional[str] = None) -> OntologyValidator:
    """
    OntologyValidator 단일 인스턴스를 가져오거나 초기화합니다.
    rdflib 로드 + 인덱스 구축은 프로세스당 1회만 수행됩니다.

    :param ontology_dir: TTL 파일이 있는 디렉터리 (생략 시 레포 루트의 ontology/)
    """
    global _validator_instance
    if _validator_instance is None:
        target_dir = ontology_dir or str(ONTOLOGY_DIR)
        logger.info(f"🚀 OntologyValidator 인스턴스 초기화 시작 [dir: {target_dir}]")
        _validator_instance = OntologyValidator(ontology_dir=target_dir)
    return _validator_instance


def validate_ontology_triples(triples: List[str]) -> Dict[str, Any]:
    """
    LLM이 질의로부터 추출한 논리 검증 트리플(예: 'fp:Bond fp:hasCreditRating "AAAA"')이
    온톨로지와 모순되지 않는지 검사합니다. LLM을 다시 호출하지 않는 결정론적 조회입니다.

    :param triples: '주어 술어 목적어' 형태의 트리플 문자열 리스트
    :return: {"valid": bool, "violations": [{"triple","reason","suggestion"}, ...]}
    """
    validator = get_ontology_validator()
    return validator.validate_triples(triples)


def list_ontology_allowed_values(concept: str) -> Dict[str, Any]:
    """
    특정 개념(RiskGrade, CreditRating, AssetClass, Region, BondCategory 등)의 허용값
    목록을 반환합니다. 질의가 모순일 때 사용자에게 되물을 역질문 문구를 만드는 데 사용합니다.

    :param concept: 'CreditRating', 'RiskGrade', 'AssetClass', 'Region', 'BondCategory' 등 개념명
    :return: {"concept": ..., "allowed_values": [...]}
    """
    validator = get_ontology_validator()
    return {"concept": concept, "allowed_values": validator.list_allowed_values(concept)}


def register_ontology_tool(
    tool_manager: Optional[ToolManager] = None
) -> Tool:
    """
    온톨로지 허용값 조회 도구를 ToolManager에 등록합니다.

    :param tool_manager: 등록할 ToolManager (생략 시 전역 기본 ToolManager)
    :return: 등록된 Tool 객체
    """
    tm = tool_manager or get_tool_manager()

    schema = {
        "type": "function",
        "function": {
            "name": "list_ontology_allowed_values",
            "description": (
                "금융상품 온톨로지의 통제 어휘(신용등급, 위험등급, 투자자산군, 투자지역, "
                "채권분류 등) 허용값 목록을 조회합니다. 사용자가 존재하지 않는 값을 언급했을 때 "
                "역질문 문구를 만들기 위해 호출합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "조회할 개념명 (예: 'CreditRating', 'RiskGrade', 'AssetClass', 'Region', 'BondCategory')"
                    }
                },
                "required": ["concept"],
                "additionalProperties": False
            }
        }
    }

    ontology_tool = Tool(
        name="list_ontology_allowed_values",
        func=list_ontology_allowed_values,
        description=schema["function"]["description"],
        schema=schema
    )

    tm.register(ontology_tool)
    logger.info("✅ 'list_ontology_allowed_values' 도구가 ToolManager에 성공적으로 등록되었습니다.")
    return ontology_tool
