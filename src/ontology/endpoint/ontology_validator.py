"""
================================================================================
OntologyValidator — 온톨로지 런타임 검증기 (endpoint/ontology_validator.py)
--------------------------------------------------------------------------------
기능:
1. common.ttl + 4개 도메인 TTL을 rdflib로 1회 로드하여 클래스/속성/통제어휘를
   인메모리 딕셔너리로 전개 (data/endpoint/meta_column_table.py의 preload 관습과 동일)
2. LLM이 뽑아낸 "주어 술어 목적어" 트리플 문자열의 논리적 모순을 결정론적으로 검사
   (LLM 호출 없음 — 순수 조회)
3. 카테고리→소스테이블 해석, 부등호(이상/이하) 질의의 SQL IN절 변환, 상품군별
   원천값↔표준값 정규화 등 QueryParser·Text-to-SQL 라우팅에 필요한 조회 인터페이스 제공
================================================================================
"""
import re
import logging
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional

import rdflib
from rdflib import RDF, RDFS, OWL, Namespace
from rdflib.namespace import XSD

logger = logging.getLogger("ontology.endpoint.validator")

FP = Namespace("http://mafest.ai/product#")

ONTOLOGY_FILES = ["common.ttl", "bond_kr.ttl", "etf_kr.ttl", "etf_gl.ttl", "fund_pub.ttl"]

# QueryParser(agent/agents/query_parser.py)가 쓰는 카테고리 문자열 → 온톨로지 클래스 로컬명
CATEGORY_TO_CLASS = {
    "domestic_bond": "Bond",
    "domestic_etf": "DomesticETF",
    "foreign_etf": "ForeignETF",
    "fund": "Fund",
}

# rdfs:range가 이 클래스들을 가리킬 때만 "허용값 검사"를 수행한다.
# (fp:Institution / fp:BenchmarkIndex 는 통제 어휘가 아니라 개방형 개체라 값 검사 대상이 아님)
CONTROLLED_VOCAB_CONCEPTS = {"RiskGrade", "CreditRating", "AssetClass", "Region", "BondCategory"}

# ('>=','<=','>','<','=') → fp:rank 비교. common.ttl 주석 "작을수록 상위"를 따른다.
# "AA- 이상"(rank<=4), "AA- 이하"(rank>=4) 같은 한국어 부등호 관용을 그대로 반영.
_RANK_OPS = {
    ">=": lambda rank, target: rank <= target,
    "<=": lambda rank, target: rank >= target,
    ">": lambda rank, target: rank < target,
    "<": lambda rank, target: rank > target,
    "=": lambda rank, target: rank == target,
    "==": lambda rank, target: rank == target,
}

# "subject predicate object" 형태의 단순 트리플 문자열 파서.
# object는 "AAAA" 같은 큰따옴표 리터럴이거나 ex:True 같은 IRI/접두어 토큰일 수 있다.
_TRIPLE_RE = re.compile(r'^\s*(\S+)\s+(\S+)\s+(.+?)\s*\.?\s*$')

# 큰따옴표 리터럴 + 선택적 언어 태그(@ko)/데이터타입 접미사(^^xsd:string).
# 이스케이프된 따옴표(\")는 리터럴 내용에 포함되게 둔다.
_LITERAL_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"(?:@[A-Za-z][A-Za-z0-9-]*|\^\^\S+)?$')


def _local(uri) -> str:
    """rdflib URI/Literal에서 마지막 '#' 또는 '/' 뒤의 로컬명을 뽑는다."""
    s = str(uri)
    if "#" in s:
        return s.rsplit("#", 1)[-1]
    return s.rsplit("/", 1)[-1]


def _strip_prefix(name: str) -> str:
    """'fp:Bond' -> 'Bond', 'Bond' -> 'Bond'"""
    name = name.strip()
    if ":" in name:
        return name.split(":", 1)[1]
    return name


class OntologyValidator:
    """
    common.ttl + 도메인 TTL을 로드해 클래스/속성/통제어휘 딕셔너리를 구성하고,
    이후에는 순수 딕셔너리 조회만으로 트리플 검증·값 정규화를 수행하는 검증기.

    초기화(=rdflib 파싱 + 인덱스 구축)는 무거우므로 프로세스당 1회만 일어나야 한다.
    이 클래스 자체는 매번 새로 만들 수 있는 평범한 클래스이고, 싱글톤 보장은
    agent/tools/ontology_tool.py 의 get_ontology_validator() 가 담당한다
    (data/endpoint/meta_column_table.py + agent/tools/meta_search_tool.py 의 관습과 동일).
    """

    def __init__(self, ontology_dir: str = "ontology"):
        self.ontology_dir = self._resolve_ontology_dir(ontology_dir)
        self.graph = rdflib.Graph()
        self._load_graph()
        self._build_indexes()

    # --------------------------------------------------------------------------
    # 초기화 — TTL 로드 및 인메모리 인덱스 구축 (프로세스당 1회)
    # --------------------------------------------------------------------------
    def _resolve_ontology_dir(self, ontology_dir: str) -> Path:
        p = Path(ontology_dir)
        if p.is_absolute() and p.is_dir():
            return p

        cwd_candidate = Path.cwd() / ontology_dir
        if cwd_candidate.is_dir() and (cwd_candidate / "common.ttl").exists():
            return cwd_candidate

        # src/ontology/endpoint/ontology_validator.py -> parents[3] == 레포 루트.
        # TTL 5개는 이 패키지 옆(src/ontology)이 아니라 레포 루트의 ontology/에 있다
        # (대회 제출 규격 트리 — ontology/ 아래엔 TTL과 README만 둔다).
        repo_root = Path(__file__).resolve().parents[3]
        candidate = repo_root / (ontology_dir if ontology_dir not in (".", "") else "ontology")
        if (candidate / "common.ttl").exists():
            return candidate

        raise FileNotFoundError(
            f"온톨로지 디렉터리를 찾을 수 없습니다: '{ontology_dir}' "
            f"(cwd={Path.cwd()}, 대체 경로={candidate})"
        )

    def _load_graph(self) -> None:
        for filename in ONTOLOGY_FILES:
            path = self.ontology_dir / filename
            if not path.exists():
                logger.warning(f"온톨로지 파일 누락, 건너뜀: {path}")
                continue
            self.graph.parse(str(path), format="turtle")
        logger.info(f"✅ OntologyValidator: {len(self.graph)} 트리플 로드 완료 [{self.ontology_dir}]")

    def _build_indexes(self) -> None:
        g = self.graph

        # 1) 클래스 집합 + 상속 관계 (도메인 서브섬션 검사용)
        self.classes = {_local(c) for c in g.subjects(RDF.type, OWL.Class)}
        self._parents: Dict[str, set] = defaultdict(set)
        for child, parent in g.subject_objects(RDFS.subClassOf):
            self._parents[_local(child)].add(_local(parent))

        # 2) fp:sourceTable — 클래스별 소스 테이블 (resolve_source_table 용)
        self.class_source_table: Dict[str, str] = {}
        for cls in self.classes:
            val = g.value(FP[cls], FP.sourceTable)
            if val is not None:
                self.class_source_table[cls] = str(val)

        # 3) 속성(DatatypeProperty/ObjectProperty) → domain/range 로컬명
        self.predicates: Dict[str, Dict[str, Any]] = {}
        prop_subjects = set(g.subjects(RDF.type, OWL.DatatypeProperty)) | \
            set(g.subjects(RDF.type, OWL.ObjectProperty))
        for p in prop_subjects:
            local = _local(p)
            domain = g.value(p, RDFS.domain)
            range_ = g.value(p, RDFS.range)
            self.predicates[local] = {
                "domain": _local(domain) if domain is not None else None,
                "range": _local(range_) if range_ is not None else None,
                "range_is_class": (range_ is not None and _local(range_) in self.classes),
            }

        # 4) 통제 어휘 인스턴스 — concept(local class name) → [{local, label, rank,
        #    value_codes:[...], synonyms:[...], level, source_column, parents:[local,...]}]
        # level/source_column/parents는 fp:BondCategory처럼 DAG형 어휘에서만 채워지고
        # 나머지 개념(CreditRating 등)에는 전부 None/[]로 들어간다.
        self.instances_by_concept: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for inst, cls in g.subject_objects(RDF.type):
            cls_local = _local(cls)
            if cls_local not in self.classes:
                continue
            label = g.value(inst, RDFS.label)
            rank = g.value(inst, FP.rank)
            level = g.value(inst, FP.categoryLevel)
            source_column = g.value(inst, FP.sourceColumn)
            entry = {
                "local": _local(inst),
                "label": str(label) if label is not None else _local(inst),
                "rank": int(rank) if rank is not None else None,
                "value_codes": [str(v) for v in g.objects(inst, FP.valueCode)],
                "alt_value_codes": [str(v) for v in g.objects(inst, FP.altValueCode)],
                "synonyms": [str(v) for v in g.objects(inst, FP.hasSynonym)],
                "level": str(level) if level is not None else None,
                "source_column": str(source_column) if source_column is not None else None,
                "parents": [_local(p) for p in g.objects(inst, FP.parentCategory)],
            }
            self.instances_by_concept[cls_local].append(entry)

        for concept, insts in self.instances_by_concept.items():
            insts.sort(key=lambda i: (i["rank"] is None, i["rank"] if i["rank"] is not None else 0))

        # 5) 위 인스턴스들의 local→dict 조회 + 부모→자식 역인덱스 (DAG 순회용: resolve_category/expand_category)
        self._instance_by_local: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        self._children_by_concept: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for concept, insts in self.instances_by_concept.items():
            for inst in insts:
                self._instance_by_local[concept][inst["local"]] = inst
            for inst in insts:
                for parent_local in inst["parents"]:
                    self._children_by_concept[concept][parent_local].append(inst["local"])

    # --------------------------------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------------------------------
    def _ancestors(self, cls_local: str) -> set:
        """cls_local의 (자기 자신을 포함한) 조상 클래스 로컬명 집합 — 도메인 서브섬션 검사용."""
        seen = {cls_local}
        stack = [cls_local]
        while stack:
            cur = stack.pop()
            for parent in self._parents.get(cur, ()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def _find_instance(self, concept: str, label_or_synonym: str) -> Optional[Dict[str, Any]]:
        """라벨/동의어뿐 아니라 fp:valueCode 원천값 자체(테이블 접두어 제외)도 유효한 표기로 인정한다.
        예: Region_China의 fp:valueCode에만 있고 hasSynonym엔 없는 'Greater China'도 유효값으로 인식해야
        LLM이 DB 원천 표기를 그대로 뽑아냈을 때(예: 'Germany') 오탐으로 컷되지 않는다.
        인스턴스 IRI 로컬명(예: 'CreditRating_AAA', 'RiskGrade1')도 인정한다 — LLM이 라벨
        문자열 대신 온톨로지의 실제 인스턴스를 그대로 참조(fp:CreditRating_AAA)할 수 있다."""
        needle = label_or_synonym.strip()
        for inst in self.instances_by_concept.get(concept, []):
            if inst["local"] == needle or inst["label"] == needle or needle in inst["synonyms"]:
                return inst
            for vc in inst["value_codes"] + inst["alt_value_codes"]:
                _, _, val = vc.partition(":")
                if val == needle:
                    return inst
        return None

    def _parse_triple(self, triple: str):
        m = _TRIPLE_RE.match(triple)
        if not m:
            return None
        subj, pred, obj_raw = m.group(1), m.group(2), m.group(3).strip()
        if obj_raw.endswith("."):
            obj_raw = obj_raw[:-1].strip()
        lit_m = _LITERAL_RE.match(obj_raw)
        if lit_m:
            # "AAA"/"AAA"@ko/"AAA"^^xsd:string 전부 순수 값 "AAA"만 남긴다.
            obj_value, obj_is_literal = lit_m.group(1), True
        else:
            obj_value, obj_is_literal = obj_raw, False
        return subj, pred, obj_value, obj_is_literal

    # --------------------------------------------------------------------------
    # 공개 인터페이스
    # --------------------------------------------------------------------------
    def validate_triples(self, triples: List[str]) -> Dict[str, Any]:
        """QueryParser가 뽑은 트리플의 모순 검사.
        return {"valid": bool, "violations": [{"triple":..., "reason":..., "suggestion":...}]}
        """
        violations: List[Dict[str, Any]] = []

        for triple in triples:
            parsed = self._parse_triple(triple)
            if parsed is None:
                violations.append({
                    "triple": triple,
                    "reason": "트리플 형식을 해석할 수 없습니다 ('주어 술어 목적어' 형태여야 합니다).",
                    "suggestion": None,
                })
                continue

            subj_raw, pred_raw, obj_value, obj_is_literal = parsed
            subj_local = _strip_prefix(subj_raw)
            pred_local = _strip_prefix(pred_raw)

            # 1) 주어 클래스가 온톨로지에 존재하는가
            if subj_local not in self.classes:
                violations.append({
                    "triple": triple,
                    "reason": f"'{subj_raw}'는 온톨로지에 존재하지 않는 클래스입니다.",
                    "suggestion": None,
                })
                continue

            # 술어 존재 여부
            pred_info = self.predicates.get(pred_local)
            if pred_info is None:
                violations.append({
                    "triple": triple,
                    "reason": f"'{pred_raw}'는 온톨로지에 정의되지 않은 속성입니다.",
                    "suggestion": None,
                })
                continue

            # 2) 술어의 rdfs:domain이 주어 클래스(또는 그 상위클래스)와 일치하는가
            domain_local = pred_info["domain"]
            if domain_local is not None and domain_local not in self._ancestors(subj_local):
                violations.append({
                    "triple": triple,
                    "reason": f"'{pred_raw}'는 fp:{domain_local}에만 적용되는 속성이며 fp:{subj_local}에는 적용할 수 없습니다.",
                    "suggestion": f"fp:{domain_local}(및 하위 클래스)에 대한 질의인지 확인해 주세요.",
                })
                continue

            # 3) 목적어가 해당 개념의 허용값인가 (range가 통제 어휘 클래스일 때만)
            # rdfs:label 뿐 아니라 fp:hasSynonym(예: "AA", "주식형", "매우높은위험")도
            # 유효한 표기이므로 _find_instance로 라벨+동의어를 함께 검사한다.
            # (list_allowed_values만 쓰면 정상적인 동의어 질의까지 오탐으로 거부하게 됨)
            # obj_is_literal 여부는 따지지 않는다 — LLM이 따옴표를 빠뜨리고
            # `fp:hasCreditRating AAAA` 처럼 뽑아내도(문법적으로 완전히 잘못된 형식은 아님)
            # 통제 어휘 검사를 건너뛰면 안 된다. 값 자체는 obj_value에 그대로 들어있다.
            # 리터럴이 아니면(예: `ex:AAA`, `fp:AAA`) 접두어를 뗀 값으로 비교한다 —
            # 주어/술어는 이미 _strip_prefix를 거치는데 목적어만 접두어가 그대로 남아
            # 유효한 값도 "ex:AAA" 자체로 비교돼 오탐 거부되는 걸 막는다.
            range_local = pred_info["range"]
            if pred_info["range_is_class"] and range_local in CONTROLLED_VOCAB_CONCEPTS:
                check_value = obj_value if obj_is_literal else _strip_prefix(obj_value)
                allowed = self.list_allowed_values(range_local)
                if allowed and self._find_instance(range_local, check_value) is None:
                    # fp:BondCategory는 61개라 전부 나열하면 역질문이 안 읽힌다 — 대분류 6개만 제시.
                    # 다른 개념(CreditRating 등)은 기존처럼 전체를 제시한다.
                    suggestion_values = (
                        self.list_allowed_values(range_local, level="major")
                        if range_local == "BondCategory"
                        else allowed
                    )
                    violations.append({
                        "triple": triple,
                        "reason": f"'{check_value}'는 존재하지 않는 {range_local} 값입니다.",
                        "suggestion": f"{', '.join(suggestion_values)} 중 어느 것을 말씀하시나요?",
                    })
                    continue

        return {"valid": len(violations) == 0, "violations": violations}

    def resolve_source_table(self, category: str) -> Optional[str]:
        """'domestic_bond' → 'tb_domestic_bond'"""
        cls_local = CATEGORY_TO_CLASS.get(category, _strip_prefix(category))
        return self.class_source_table.get(cls_local)

    def list_allowed_values(self, concept: str, level: Optional[str] = None) -> List[str]:
        """'CreditRating' → ['AAA','AA+',...]  — 역질문 문구 생성용 (fp:rank 오름차순)
        level을 주면 그 레벨('major'|'mid'|'minor')만 필터링한다 — fp:BondCategory처럼
        레벨이 있는 DAG형 어휘 전용 옵션이며, 다른 개념·기존 호출부는 그대로 동작한다."""
        concept = _strip_prefix(concept)
        insts = self.instances_by_concept.get(concept, [])
        if level is not None:
            insts = [i for i in insts if i["level"] == level]
        return [inst["label"] for inst in insts]

    def expand_rank_filter(self, concept: str, label: str, op: str) -> List[str]:
        """('CreditRating','AA-','>=') → ['AAA','AA+','AA0','AA-']
        fp:rank 기반. '이상/이하' 부등호 질의를 SQL IN 절로 결정론적 변환"""
        concept = _strip_prefix(concept)
        cmp_fn = _RANK_OPS.get(op)
        if cmp_fn is None:
            raise ValueError(f"지원하지 않는 연산자: '{op}' (허용: {sorted(_RANK_OPS)})")

        target = self._find_instance(concept, label)
        if target is None or target["rank"] is None:
            return []

        matched = [
            inst for inst in self.instances_by_concept.get(concept, [])
            if inst["rank"] is not None and cmp_fn(inst["rank"], target["rank"])
        ]
        matched.sort(key=lambda i: i["rank"])
        return [inst["label"] for inst in matched]

    def normalize_value(self, concept: str, raw: str, table: str) -> Optional[str]:
        """fp:valueCode / fp:altValueCode / fp:hasSynonym 역인덱스로 상품군별 원천값 ↔ 표준값 변환.
        예: ('AssetClass','주식','tb_foreign_etf') → 'Equity'

        fp:valueCode(대표값)와 fp:altValueCode(별칭)를 구분해서 다룬다 — 성격이 다르기 때문이다.
          - fp:valueCode: 같은 테이블 안에서 여러 개면 서로 다른 행을 가리키는 peer 값들이다
            (예: Region_Europe의 tb_foreign_etf 값이 Europe/Germany/UK/... 여럿). raw가 그중
            하나와 일치하면 그대로 반환한다 — 안 그러면 항상 첫 값으로 뭉개져 다른 행을 가리키게 된다.
          - fp:altValueCode: 같은 행의 다른 컬럼이 갖는 별칭이다(예: 국내ETF 위험등급의
            pd_risk_nm 이름형은 pd_risk_cd 코드형의 별칭). raw가 별칭으로 매칭되면 별칭 자체가
            아니라 같은 테이블의 fp:valueCode(대표값)로 정규화해서 반환한다.
        """
        concept = _strip_prefix(concept)
        raw_norm = raw.strip()

        target = None
        matched_as_alt = False
        for inst in self.instances_by_concept.get(concept, []):
            value_parts = {vc.partition(":")[2] for vc in inst["value_codes"] if ":" in vc}
            alt_parts = {vc.partition(":")[2] for vc in inst["alt_value_codes"] if ":" in vc}
            # _find_instance/resolve_filter_value와 동일하게 인스턴스 IRI 로컬명도 인정한다
            # (교차검증에서 지적됨 — validate_triples는 통과시키는데 여기서 거부하면 불일치).
            if (raw_norm in value_parts or raw_norm == inst["label"]
                    or raw_norm in inst["synonyms"] or raw_norm == inst["local"]):
                target = inst
                break
            if raw_norm in alt_parts:
                target = inst
                matched_as_alt = True
                break
        if target is None:
            return None

        if not matched_as_alt:
            # raw가 target 테이블 자신의 valueCode(peer 값) 자체라면 그대로 반환한다.
            exact = f"{table}:{raw_norm}"
            if exact in target["value_codes"]:
                return raw_norm

        # 대표값(fp:valueCode)이 있으면 우선 반환한다 — alt로 매칭됐거나(이름→코드 정규화),
        # raw가 이 테이블의 valueCode 자체는 아니었던 경우(예: 다른 테이블 라벨/동의어로 찾은 경우).
        for vc in target["value_codes"]:
            tbl, _, val = vc.partition(":")
            if tbl == table:
                return val
        # 이 테이블에 대표값이 없으면 별칭이라도 반환한다.
        for vc in target["alt_value_codes"]:
            tbl, _, val = vc.partition(":")
            if tbl == table:
                return val
        return None

    def resolve_filter_value(self, concept: str, raw: str, table: str) -> Dict[str, Any]:
        """트리플 목적어를 특정 테이블의 SQL 필터 값으로 해석한다 (Task 10, qp_task10_multicategory.md).

        normalize_value()는 "변환할 값이 없다"를 전부 None 하나로 뭉갠다. 그런데 None이
        실제로 뜻하는 것은 세 가지다 — (1) 이 개념이 이 테이블에 아예 없음(자산군@채권),
        (2) 정규화가 애초에 불필요함(신용등급 — 라벨이 곧 DB 원천값이라 fp:valueCode를
        안 붙였다), (3) 개념은 있으나 이 값만 이 테이블에 없음(원자재@펀드). 호출부가
        (1)/(2)/(3)을 구분 못 하면, (2)를 (1)로 오인해서 "신용등급 개념이 채권에 없다"는
        사실과 반대인 판단을 내리고 필터를 통째로 날려버린다(다중 카테고리 질의 버그).

        normalize_value()는 건드리지 않는다 — 이 메서드는 그 옆에 추가하는 새 조회
        창구다. 내부적으로 normalize_value와 같은 인덱스(fp:valueCode/fp:altValueCode)와
        헬퍼(_find_instance/_strip_prefix)를 재사용하지만, 판정 결과를 구분해서 돌려준다.

        return {
            "applicable": bool,       # 이 (값, 테이블) 조합으로 필터를 만들 수 있는가
            "value": str | None,      # 필터에 넣을 값
            "reason": str,            # unknown_concept / unknown_value / as_is /
                                      # not_in_table / normalized / value_not_in_table
            "concept": str,
        }

        판정 순서가 중요하다 — "as_is"를 "not_in_table"보다 먼저 봐야 한다. 개념 전체가
        (신용등급처럼) 아무 테이블에도 fp:valueCode/fp:altValueCode가 없으면, 그건
        "이 테이블에 없다"가 아니라 "변환이 필요 없다"이므로 라벨을 그대로 쓴다.

        raw가 이미 대상 테이블 자신의 fp:valueCode(peer 값) 자체인 경우, 그 값을 그대로
        반환한다 — 이건 normalize_value()에서 이미 고친 Region 버그(한 테이블에 원천값이
        여러 개 몰릴 때 raw가 어떤 값이든 항상 첫 값을 반환해버리던 문제, 예:
        Region_Europe의 tb_foreign_etf 쪽에 Europe/Germany/UK/...가 모두 있는데 raw="Germany"를
        물어도 "Europe"이 나와버림)와 정확히 같은 함정이라 여기서도 같은 방식으로 막는다.
        _find_instance만으로는 raw가 label/synonym으로 매칭됐는지 value_codes 자체로
        매칭됐는지 alt_value_codes(별칭)로 매칭됐는지 구분이 안 되므로, normalize_value와
        동일하게 이 메서드 안에서 직접 매칭한다(_find_instance를 통째로 재사용하지 않음).
        """
        concept = _strip_prefix(concept)
        raw_norm = raw.strip()
        insts = self.instances_by_concept.get(concept, [])

        if not insts:
            return {"applicable": False, "value": None, "reason": "unknown_concept", "concept": concept}

        inst = None
        matched_as_alt = False
        for i in insts:
            value_parts = {vc.partition(":")[2] for vc in i["value_codes"] if ":" in vc}
            alt_parts = {vc.partition(":")[2] for vc in i["alt_value_codes"] if ":" in vc}
            # _find_instance와 동일하게 인스턴스 IRI 로컬명(예: 'CreditRating_AAA')도 인정한다 —
            # validate_triples가 이미 이걸 유효한 표기로 통과시키는데 여기서 인정 안 하면
            # 검증은 통과했는데 필터는 못 만드는(unknown_value) 불일치가 생긴다(교차검증에서 지적됨).
            if (raw_norm in value_parts or raw_norm == i["label"]
                    or raw_norm in i["synonyms"] or raw_norm == i["local"]):
                inst = i
                break
            if raw_norm in alt_parts:
                inst = i
                matched_as_alt = True
                break

        if inst is None:
            # 방어용이다 — 정상 흐름에서는 validate_triples()가 먼저 걸러낸다.
            return {"applicable": False, "value": None, "reason": "unknown_value", "concept": concept}

        # 개념 전체가 (테이블 불문) 원천값을 하나도 안 가지면 라벨이 곧 원천값이다.
        concept_has_any_source_value = any(
            i["value_codes"] or i["alt_value_codes"] for i in insts
        )
        if not concept_has_any_source_value:
            return {"applicable": True, "value": inst["label"], "reason": "as_is", "concept": concept}

        # 개념은 값을 가지지만, 이 테이블에 대해서는 어떤 인스턴스도 값을 안 가지면
        # 이 테이블엔 개념 자체가 없는 것이다(예: 자산군@국내채권, 위험등급@해외ETF).
        table_covered = any(
            any(vc.partition(":")[0] == table for vc in (i["value_codes"] + i["alt_value_codes"]))
            for i in insts
        )
        if not table_covered:
            return {"applicable": False, "value": None, "reason": "not_in_table", "concept": concept}

        if not matched_as_alt:
            # raw가 이 테이블의 valueCode(peer 값) 자체라면 그대로 반환한다.
            exact = f"{table}:{raw_norm}"
            if exact in inst["value_codes"]:
                return {"applicable": True, "value": raw_norm, "reason": "normalized", "concept": concept}

        # 대표값(fp:valueCode)이 있으면 우선 반환한다 — 별칭(alt)으로 매칭됐거나(이름→코드
        # 정규화), raw가 이 테이블의 valueCode 자체는 아니었던 경우(다른 테이블 라벨/동의어로
        # 찾은 경우).
        for vc in inst["value_codes"]:
            tbl, _, val = vc.partition(":")
            if tbl == table:
                return {"applicable": True, "value": val, "reason": "normalized", "concept": concept}
        for vc in inst["alt_value_codes"]:
            tbl, _, val = vc.partition(":")
            if tbl == table:
                return {"applicable": True, "value": val, "reason": "normalized", "concept": concept}

        # 개념은 이 테이블을 커버하지만(다른 값들은 있음), 이 값만 이 테이블에 없다.
        return {"applicable": False, "value": None, "reason": "value_not_in_table", "concept": concept}

    # --------------------------------------------------------------------------
    # 채권분류(BondCategory) DAG 전용 조회 — Task 4 (ot_instructions/ot_v1.md)
    # --------------------------------------------------------------------------
    _BOND_CATEGORY_LEVEL_PRIORITY = {"major": 0, "mid": 1, "minor": 2}

    def _describe_bond_category(self, inst: Dict[str, Any]) -> Dict[str, Any]:
        concept = "BondCategory"
        by_local = self._instance_by_local.get(concept, {})
        parent_labels = [by_local[p]["label"] for p in inst["parents"] if p in by_local]
        child_locals = self._children_by_concept.get(concept, {}).get(inst["local"], [])
        child_labels = [by_local[c]["label"] for c in child_locals if c in by_local]
        return {
            "level": inst["level"],
            "column": inst["source_column"],
            "value": inst["label"],
            "parents": parent_labels,
            "children": child_labels,
        }

    def resolve_category(self, label: str) -> Optional[Dict[str, Any]]:
        """'국고채' -> {"level":"mid", "column":"std_pd_scls_nm", "value":"국고채",
                        "parents":["국공채"], "children":["국고채권"], "ambiguous":[]}
        Text-to-SQL이 WHERE <column> = <value> 를 바로 만들 수 있게 하는 것이 이 메서드의 목적.
        동명이인('특수은행채','개인투자용국채')은 더 상위 레벨(major > mid > minor)을 우선 반환하고,
        반환 dict의 "ambiguous"에 다른 후보들을 같은 형식으로 담는다."""
        concept = "BondCategory"
        needle = label.strip()
        matches = [
            inst for inst in self.instances_by_concept.get(concept, [])
            if inst["label"] == needle or needle in inst["synonyms"]
        ]
        if not matches:
            return None

        matches.sort(key=lambda i: self._BOND_CATEGORY_LEVEL_PRIORITY.get(i["level"], 99))
        primary, *rest = matches

        result = self._describe_bond_category(primary)
        result["ambiguous"] = [self._describe_bond_category(m) for m in rest]
        return result

    def expand_category(self, label: str) -> List[str]:
        """'국공채' -> 하위 전 레벨 라벨(중복 제거). 역질문·설명 문구 생성 전용.
        DAG이므로 방문 집합으로 순환/중복을 막는다.
        ※ 이 결과를 SQL IN 절로 쓰지 말 것 — bd_knd NULL 924건이 하위 전개에서 누락된다."""
        concept = "BondCategory"
        needle = label.strip()
        matches = [
            inst for inst in self.instances_by_concept.get(concept, [])
            if inst["label"] == needle or needle in inst["synonyms"]
        ]
        if not matches:
            return []
        matches.sort(key=lambda i: self._BOND_CATEGORY_LEVEL_PRIORITY.get(i["level"], 99))
        start_local = matches[0]["local"]

        by_local = self._instance_by_local.get(concept, {})
        children_index = self._children_by_concept.get(concept, {})

        visited = {start_local}
        stack = [start_local]
        expanded_labels: List[str] = []
        while stack:
            cur = stack.pop()
            for child_local in children_index.get(cur, ()):
                if child_local in visited:
                    continue
                visited.add(child_local)
                child_inst = by_local.get(child_local)
                if child_inst:
                    expanded_labels.append(child_inst["label"])
                stack.append(child_local)

        # 라벨 기준 중복 제거(등장 순서 유지) — 동명이인 노드가 있어도 문구엔 한 번만.
        seen = set()
        deduped = []
        for lbl in expanded_labels:
            if lbl not in seen:
                seen.add(lbl)
                deduped.append(lbl)
        return deduped
