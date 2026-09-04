"""
ontology/endpoint — 온톨로지 런타임 검증 엔드포인트 패키지

data/endpoint/ 패키지와 동일한 관습을 따른다: 무거운 초기화(rdflib 로드)는
클래스 생성 시 1회만 수행하고, 이후 도구 계층(agent/tools/ontology_tool.py)에서
싱글톤으로 감싸 재사용한다.
"""
from .ontology_validator import OntologyValidator

__all__ = ["OntologyValidator"]
