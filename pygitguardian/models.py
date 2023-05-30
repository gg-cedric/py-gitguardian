from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Dict, List, Optional, cast
from uuid import UUID

import marshmallow_dataclass
from marshmallow import (
    EXCLUDE,
    Schema,
    ValidationError,
    fields,
    post_load,
    pre_load,
    validate,
)

from .config import DOCUMENT_SIZE_THRESHOLD_BYTES, MULTI_DOCUMENT_LIMIT


class BaseSchema(Schema):
    class Meta:
        ordered = True
        unknown = EXCLUDE


class Base:
    SCHEMA: ClassVar[BaseSchema]

    def __init__(self, status_code: Optional[int] = None) -> None:
        self.status_code = status_code

    def to_json(self) -> str:
        """
        to_json converts model to JSON string.
        """
        return cast(str, self.SCHEMA.dumps(self))

    def to_dict(self) -> Dict:
        """
        to_dict converts model to a dictionary representation.
        """
        return cast(Dict, self.SCHEMA.dump(self))

    @property
    def success(self) -> bool:
        return self.__bool__()

    def __bool__(self) -> bool:
        return self.status_code == 200


class DocumentSchema(BaseSchema):
    filename = fields.String(validate=validate.Length(max=256), allow_none=True)
    document = fields.String(required=True)

    @staticmethod
    def validate_size(document: Dict[str, Any], maximum_size: int) -> None:
        """Raises a ValidationError if the content of the document is longer than
        `maximum_size`.

        This is not implemented as a Marshmallow validator because the maximum size can
        vary.
        """
        encoded = document["document"].encode("utf-8", errors="replace")
        if len(encoded) > maximum_size:
            raise ValidationError(
                f"file exceeds the maximum allowed size of {maximum_size}B"
            )

    @post_load
    def replace_0_bytes(self, in_data: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        doc = in_data["document"]
        # Our API does not accept 0 bytes in documents, so replace them with the replacement character
        in_data["document"] = doc.replace("\0", "\uFFFD")
        return in_data

    @post_load
    def force_utf_8_encoding(
        self, in_data: Dict[str, Any], **kwargs: Any
    ) -> Dict[str, Any]:
        doc = in_data["document"]
        # Force UTF-8 and substitute ? for encoding errors
        in_data["document"] = doc.encode("utf-8", errors="replace").decode("utf-8")
        return in_data


class Document(Base):
    """
    Document is a request object for communicating documents
    to the API

    Attributes:
        filename (optional,str): filename for filename evaluation
        document (str): text content
    """

    SCHEMA = DocumentSchema()

    def __init__(self, document: str, filename: Optional[str] = None, **kwargs: Any):
        super().__init__()
        self.document = document
        if filename:
            self.filename = filename

    def __repr__(self) -> str:
        return f"filename:{self.filename}, document:{self.document}"


class DetailSchema(BaseSchema):
    detail = fields.String(required=True)

    @pre_load
    def rename_errors(self, data: Dict, many: bool, **kwargs: Any) -> Dict:
        error = data.pop("error", None)
        if error is not None:
            data["detail"] = str(error)

        return data

    @post_load
    def make_detail_response(self, data: Dict[str, Any], **kwargs: Any) -> "Detail":
        return Detail(**data)


class Detail(Base):
    """Detail is a response object mostly returned on error or when the
    api output is a simple string.

    Attributes:
        detail (str): response string
    """

    SCHEMA = DetailSchema()

    def __init__(
        self, detail: str, status_code: Optional[int] = None, **kwargs: Any
    ) -> None:
        super().__init__(status_code=status_code)
        self.detail = detail

    def __repr__(self) -> str:
        return f"{self.status_code}:{self.detail}"


class MatchSchema(BaseSchema):
    match = fields.String(required=True)
    match_type = fields.String(data_key="type", required=True)
    line_start = fields.Int(allow_none=True)
    line_end = fields.Int(allow_none=True)
    index_start = fields.Int(allow_none=True)
    index_end = fields.Int(allow_none=True)

    @post_load
    def make_match(self, data: Dict[str, Any], **kwargs: Any) -> "Match":
        return Match(**data)


class Match(Base):
    """
    Match describes a found issue by GitGuardian.
    With info such as match location and type.
    Example:
    { "match": "cake.gitguardian.com",
    "index_end": 96,
    "index_start": 77,
    "type": "host",
    "line_end": 2,
    "line_start": 2 }
    """

    SCHEMA = MatchSchema()

    def __init__(
        self,
        match: str,
        match_type: str,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        index_start: Optional[int] = None,
        index_end: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.match = match
        self.match_type = match_type
        self.line_start = line_start
        self.line_end = line_end
        self.index_start = index_start
        self.index_end = index_end

    def __repr__(self) -> str:
        return (
            "match:{}, "
            "match_type:{}, "
            "line_start:{}, "
            "line_end:{}, "
            "index_start:{}, "
            "index_end:{}".format(
                self.match,
                self.match_type,
                repr(self.line_start),
                repr(self.line_end),
                repr(self.index_start),
                repr(self.index_end),
            )
        )


class PolicyBreakSchema(BaseSchema):
    break_type = fields.String(data_key="type", required=True)
    policy = fields.String(required=True)
    validity = fields.String(required=False, load_default=None, dump_default=None)
    known_secret = fields.Boolean(required=False, load_default=False, dump_default=None)
    incident_url = fields.String(required=False, load_default=False, dump_default=None)
    matches = fields.List(fields.Nested(MatchSchema), required=True)

    @post_load
    def make_policy_break(self, data: Dict[str, Any], **kwargs: Any) -> "PolicyBreak":
        return PolicyBreak(**data)


class PolicyBreak(Base):
    """
    PolicyBreak describes a GitGuardian policy break found
    in a scan.
    A PolicyBreak can contain multiple matches, for example,
    on secrets that have a client id and client secret.
    """

    SCHEMA = PolicyBreakSchema()

    def __init__(
        self,
        break_type: str,
        policy: str,
        validity: str,
        matches: List[Match],
        known_secret: bool = False,
        incident_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.break_type = break_type
        self.policy = policy
        self.validity = validity
        self.known_secret = known_secret
        self.incident_url = incident_url
        self.matches = matches

    @property
    def is_secret(self) -> bool:
        return self.policy == "Secrets detection"

    def __repr__(self) -> str:
        return (
            "break_type:{}, "
            "policy:{}, "
            "matches: {}".format(self.break_type, self.policy, repr(self.matches))
        )


class ScanResultSchema(BaseSchema):
    policy_break_count = fields.Integer(required=True)
    policies = fields.List(fields.String(), required=True)
    policy_breaks = fields.List(fields.Nested(PolicyBreakSchema), required=True)

    @post_load
    def make_scan_result(self, data: Dict[str, Any], **kwargs: Any) -> "ScanResult":
        return ScanResult(**data)


class ScanResult(Base):
    """ScanResult is a response object returned on a Content Scan

    Attributes:
        status_code (int): response status code
        policy_break_count (int): number of policy breaks
        policy_breaks (List): policy break list
        policies (List[str]): string list of policies evaluated
    """

    SCHEMA = ScanResultSchema()

    def __init__(
        self,
        policy_break_count: int,
        policy_breaks: List[PolicyBreak],
        policies: List[str],
        **kwargs: Any,
    ) -> None:
        """
        :param policy_break_count: number of policy breaks
        :type policy_break_count: int
        :param policy_breaks: policy break list
        :type policy_breaks: List
        :param policies: string list of policies evaluated
        :type policies: List[str]
        """
        super().__init__()
        self.policy_break_count = policy_break_count
        self.policies = policies
        self.policy_breaks = policy_breaks

    @property
    def has_policy_breaks(self) -> bool:
        """has_secrets is an easy way to check if your provided document has policy breaks

        >>> obj = ScanResult(2, [], [])
        >>> obj.has_policy_breaks
        True

        :return: true if there were policy breaks (including secrets) in the document
        :rtype: bool
        """

        return self.policy_break_count > 0

    @property
    def has_secrets(self) -> bool:
        """has_secrets is an easy way to check if your provided document has secrets

        :return: true if there were secrets in the document
        :rtype: bool
        """

        return any(policy_break.is_secret for policy_break in self.policy_breaks)

    def __repr__(self) -> str:
        return (
            "policy_break_count:{}, "
            "policies:{}, "
            "policy_breaks: {}".format(
                self.policy_break_count, self.policies, self.policy_breaks
            )
        )

    def __str__(self) -> str:
        return "{} policy breaks from the evaluated policies: {}".format(
            self.policy_break_count,
            ", ".join(policy_break.policy for policy_break in self.policy_breaks),
        )


class MultiScanResultSchema(BaseSchema):
    scan_results = fields.List(
        fields.Nested(ScanResultSchema),
        required=True,
        validate=validate.Length(min=1),
    )

    @post_load
    def make_scan_result(
        self, data: Dict[str, Any], **kwargs: Any
    ) -> "MultiScanResult":
        return MultiScanResult(**data)


class MultiScanResult(Base):
    """ScanResult is a response object returned on a Content Scan

    Attributes:
        status_code (int): response status code
        policy_break_count (int): number of policy breaks
        policy_breaks (List): policy break list
        policies (List[str]): string list of policies evaluated
    """

    SCHEMA = MultiScanResultSchema()

    def __init__(self, scan_results: List[ScanResult], **kwargs: Any) -> None:
        """
        :param scan_results: List of scan_results
        """
        super().__init__()
        self.scan_results = scan_results

    @property
    def has_policy_breaks(self) -> bool:
        """has_policy_breaks is an easy way to check if your provided document has policy breaks

        >>> obj = ScanResult(2, [], [])
        >>> obj.has_policy_breaks
        True

        :return: true if there were policy breaks (including secrets) in the documents
        :rtype: bool
        """

        return any(scan_result.has_policy_breaks for scan_result in self.scan_results)

    @property
    def has_secrets(self) -> bool:
        """has_secrets is an easy way to check if your provided document has secrets

        :return: true if there were secrets in the documents
        :rtype: bool
        """

        return any(scan_result.has_secrets for scan_result in self.scan_results)

    def __repr__(self) -> str:
        return f"scan_results:{self.scan_results}"

    def __str__(self) -> str:
        return "{} scan results containing {} policy breaks".format(
            len(self.scan_results),
            len(
                [
                    policy_break
                    for scan_result in self.scan_results
                    for policy_break in scan_result.policy_breaks
                ]
            ),
        )


class QuotaSchema(BaseSchema):
    count = fields.Int()
    limit = fields.Int()
    remaining = fields.Int()
    since = fields.Date()

    @post_load
    def make_quota(self, data: Dict[str, Any], **kwargs: Any) -> "Quota":
        return Quota(**data)


class Quota(Base):
    """
    Quota describes a quota category in the GitGuardian API.
    Allows you to check your current available quota.
    Example:
    {"count": 2,
    "limit": 5000,
    "remaining": 4998,
    "since": "2021-04-18"}
    """

    SCHEMA = QuotaSchema()

    def __init__(
        self, count: int, limit: int, remaining: int, since: date, **kwargs: Any
    ) -> None:
        super().__init__()
        self.count = count
        self.limit = limit
        self.remaining = remaining
        self.since = since

    def __repr__(self) -> str:
        return (
            "count:{}, "
            "limit:{}, "
            "remaining:{}, "
            "since:{}".format(
                self.count, self.limit, self.remaining, self.since.isoformat()
            )
        )


class QuotaResponseSchema(BaseSchema):
    content = fields.Nested(QuotaSchema)

    @post_load
    def make_quota_response(
        self, data: Dict[str, Any], **kwargs: Any
    ) -> "QuotaResponse":
        return QuotaResponse(**data)


class QuotaResponse(Base):
    """
    Quota describes a quota category in the GitGuardian API.
    Allows you to check your current available quota.
    Example:
    {"content": {
        "count": 2,
        "limit": 5000,
        "remaining": 4998,
        "since": "2021-04-18"}}
    """

    SCHEMA = QuotaResponseSchema()

    def __init__(self, content: Quota, **kwargs: Any) -> None:
        super().__init__()
        self.content = content

    def __repr__(self) -> str:
        return f"content:{repr(self.content)}"


class HoneytokenResponseSchema(BaseSchema):
    id = fields.UUID()
    name = fields.String()
    description = fields.String(allow_none=True)
    created_at = fields.AwareDateTime()
    gitguardian_url = fields.URL()
    status = fields.String()
    triggered_at = fields.AwareDateTime(allow_none=True)
    revoked_at = fields.AwareDateTime(allow_none=True)
    open_events_count = fields.Int(allow_none=True)
    type_ = fields.String(data_key="type")
    creator_id = fields.Int(allow_none=True)
    revoker_id = fields.Int(allow_none=True)
    creator_api_token_id = fields.String(allow_none=True)
    revoker_api_token_id = fields.String(allow_none=True)
    token = fields.Mapping(fields.String(), fields.String())
    tags = fields.List(fields.String())

    @post_load
    def make_honeytoken_response(
        self, data: Dict[str, Any], **kwargs: Any
    ) -> "HoneytokenResponse":
        return HoneytokenResponse(**data)


class HoneytokenResponse(Base):
    """
    honeytoken creation in the GitGuardian API.
    Allows users to create and get a honeytoken.
    Example:
        {
            "id": "d45a123f-b15d-4fea-abf6-ff2a8479de5b",
            "name": "honeytoken A",
            "description": "honeytoken used in the repository AA",
            "created_at": "2019-08-22T14:15:22Z",
            "gitguardian_url":
                "https://dashboard.gitguardian.com/workspace/1/honeytokens/d45a123f-b15d-4fea-abf6-ff2a8479de5b",
            "status": "active",
            "triggered_at": "2019-08-22T14:15:22Z",
            "revoked_at": "2019-08-22T14:15:22Z",
            "open_events_count": 122,
            "type": "AWS",
            "creator_id": 122,
            "revoker_id": 122,
            "creator_api_token_id": null,
            "revoker_api_token_id": null,
            "token": {
                "access_token_id": "AAAA",
                "secret_key": "BBB"
            },
        "tags": ["publicly_exposed"]
        }
    """

    SCHEMA = HoneytokenResponseSchema()

    def __init__(
        self,
        id: UUID,
        name: str,
        description: Optional[str],
        created_at: datetime,
        gitguardian_url: str,
        status: str,
        triggered_at: Optional[datetime],
        revoked_at: Optional[datetime],
        open_events_count: Optional[int],
        type_: str,
        creator_id: Optional[int],
        revoker_id: Optional[int],
        creator_api_token_id: Optional[str],
        revoker_api_token_id: Optional[str],
        token: Dict[str, str],
        tags: List[str],
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.id = id
        self.name = name
        self.description = description
        self.created_at = created_at
        self.gitguardian_url = gitguardian_url
        self.status = status
        self.triggered_at = triggered_at
        self.revoked_at = revoked_at
        self.open_events_count = open_events_count
        self.type_ = type_
        self.creator_id = creator_id
        self.revoker_id = revoker_id
        self.creator_api_token_id = creator_api_token_id
        self.revoker_api_token_id = revoker_api_token_id
        self.token = token
        self.tags = tags

    def __repr__(self) -> str:
        return f"honeytoken:{self.id} {self.name}"


class HealthCheckResponseSchema(BaseSchema):
    detail = fields.String(allow_none=False)
    status_code = fields.Int(allow_none=False)
    app_version = fields.String(allow_none=True)
    secrets_engine_version = fields.String(allow_none=True)


class HealthCheckResponse(Base):
    SCHEMA = HealthCheckResponseSchema()

    def __init__(
        self,
        detail: str,
        status_code: int,
        app_version: Optional[str] = None,
        secrets_engine_version: Optional[str] = None,
        **kwargs: Any,
    ):
        super().__init__()
        self.detail = detail
        self.status_code = status_code
        self.app_version = app_version
        self.secrets_engine_version = secrets_engine_version

    def __repr__(self) -> str:
        return (
            "detail:{}, "
            "status_code:{}, "
            "app version:{}, "
            "secrets engine version:{}".format(
                self.detail,
                self.status_code,
                self.app_version or "",
                self.secrets_engine_version or "",
            )
        )


@dataclass
class SecretScanPreferences:
    maximum_document_size: int = DOCUMENT_SIZE_THRESHOLD_BYTES
    maximum_documents_per_scan: int = MULTI_DOCUMENT_LIMIT


@dataclass
class ServerMetadata(Base):
    version: str
    preferences: Dict[str, Any]
    secret_scan_preferences: SecretScanPreferences = field(
        default_factory=SecretScanPreferences
    )


ServerMetadata.SCHEMA = marshmallow_dataclass.class_schema(
    ServerMetadata, base_schema=BaseSchema
)()
