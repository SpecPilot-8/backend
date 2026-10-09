from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

VerdictStatus = Literal["implemented", "partial", "mismatch", "not_found", "needs_review",
                        "not_statically_verifiable"]
T = TypeVar("T")


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Page(DTO, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class MissingFeature(DTO):
    code: str
    message: str


class Capability(DTO):
    code: str
    status: Literal["ready", "partial", "not_implemented"]
    message: str


class ProjectCreate(DTO):
    name: str = Field(min_length=1, max_length=200)
    workspace_path: str = Field(min_length=1, max_length=4096)


class ProjectOut(DTO):
    id: str
    name: str
    workspace_path: str
    llm_config: dict
    config_version: int
    latest_snapshot_id: str | None
    created_at: str


class DocumentOut(DTO):
    id: str
    project_id: str
    filename: str
    format: str
    pipeline_status: str
    warnings: list[str]
    missing_features: list[MissingFeature]
    created_at: str


class DocumentDetail(DocumentOut):
    text: str
    blocks: list[dict]


class Checklist(DTO):
    category: Literal["API", "VALIDATION", "LOGIC", "DATA", "EXCEPTION", "ERROR", "VIEW", "REQUEST", "UX"]
    target: Literal["backend", "frontend"]
    item: str = Field(min_length=1, max_length=5000)


class RequirementSpec(DTO):
    req_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    original_text: str = Field(default="", max_length=30000)
    condition: str = Field(default="", max_length=10000)
    action: str = Field(default="", max_length=10000)
    expected: str = Field(default="", max_length=10000)
    checklists: list[Checklist] = Field(default_factory=list, max_length=100)


class RequirementCreate(RequirementSpec):
    project_id: str
    document_id: str | None = None


class EvidenceOut(DTO):
    chunk_id: str
    file_path: str
    absolute_path: str
    uri: str
    start_line: int
    end_line: int
    code_snippet: str


class VerificationOut(DTO):
    id: str
    requirement_id: str
    snapshot_id: str
    revision: int
    status: VerdictStatus
    reason: str
    evidence: list[EvidenceOut]
    suggested_code: str | None
    source: str


class RequirementOut(RequirementSpec):
    id: str
    project_id: str
    document_id: str | None
    revision: int
    created_at: str
    analysis_state: Literal["not_run", "manual_reviewed"]
    status: VerdictStatus | None
    verification: VerificationOut | None


class CodeIndexRequest(DTO):
    project_id: str
    relative_path: str = Field(default=".", max_length=4096)


class SnapshotOut(DTO):
    id: str
    project_id: str
    root_path: str
    content_hash: str
    file_count: int
    chunk_count: int
    warnings: list[str]
    created_at: str


class ChunkOut(DTO):
    id: str
    snapshot_id: str
    file_path: str
    start_line: int
    end_line: int
    chunk_type: str
    symbol: str | None
    content: str


class VerifyRequest(DTO):
    project_id: str
    snapshot_id: str
    requirement_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)


class ReverifyRequest(DTO):
    snapshot_id: str


class GenerateTestsRequest(DTO):
    project_id: str
    requirement_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)


class JobOut(DTO):
    id: str
    project_id: str
    operation: str
    status: str
    snapshot_id: str | None
    model_config_snapshot: dict
    missing_features: list[MissingFeature]
    created_at: str
    poll_url: str
    events_url: str


class VerificationImport(DTO):
    project_id: str
    requirement_id: str
    snapshot_id: str
    revision: int = Field(ge=1)
    status: VerdictStatus
    reason: str = Field(min_length=1, max_length=10000)
    evidence_chunk_ids: list[str] = Field(default_factory=list, max_length=100)
    suggested_code: str | None = Field(default=None, max_length=30000)

    @model_validator(mode="after")
    def require_evidence(self):
        if self.status in ("implemented", "partial", "mismatch") and not self.evidence_chunk_ids:
            raise ValueError("구현·부분구현·불일치 판정에는 근거 청크가 필요합니다")
        if self.suggested_code and self.status not in ("partial", "mismatch"):
            raise ValueError("수정 제안은 부분구현·불일치 판정에만 등록할 수 있습니다")
        return self


class TestCaseCreate(DTO):
    project_id: str
    requirement_id: str
    revision: int = Field(ge=1)
    test_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    type: Literal["positive", "negative", "boundary"]
    preconditions: str = Field(default="", max_length=10000)
    steps: list[str] = Field(min_length=1, max_length=100)
    input_data: str = Field(default="", max_length=10000)
    expected: str = Field(min_length=1, max_length=10000)
    actual: str = Field(default="", max_length=10000)
    result: Literal["PASS", "FAIL"] | None = None
    notes: str = Field(default="", max_length=10000)

    @field_validator("steps")
    @classmethod
    def valid_steps(cls, steps):
        if any(not s.strip() or len(s) > 5000 for s in steps):
            raise ValueError("각 시험절차는 1~5000자의 내용이 필요합니다")
        return steps


class TestCaseOut(TestCaseCreate):
    id: str
    source: str


class ExportRequest(DTO):
    project_id: str
    snapshot_id: str | None = None


class LLMSettings(DTO):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2000)

    @field_validator("base_url")
    @classmethod
    def public_endpoint(cls, value):
        from urllib.parse import urlsplit
        if value is not None:
            parsed = urlsplit(value)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                    parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("접속 주소에는 HTTP(S) 주소만 입력하며 인증 정보는 포함하지 마세요")
        return value


class ConnectionCheck(DTO):
    api_key: SecretStr | None = None
