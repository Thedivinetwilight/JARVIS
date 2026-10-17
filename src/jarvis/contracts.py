"""The common cognitive contract.

These typed records are the vocabulary that JARVIS's components use to exchange
information inside one cognitive cycle (see docs/COGNITIVE_ARCHITECTURE.md).
They are deliberately small, validated on construction, and serialisable with
:func:`to_dict`. Components exchange these records instead of undocumented
dictionaries; persistence formats are versioned separately by migrations.

Nothing here performs I/O.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


class _StrEnum(str, Enum):
    """``str`` + ``Enum`` (``enum.StrEnum`` needs Python 3.11; we support 3.10)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class EpistemicStatus(_StrEnum):
    """How a piece of information is known (Addendum D1).

    OBSERVATION   information directly received or measured by a tool.
    SOURCE_CLAIM  a proposition asserted by a source (e.g. the user said it).
    BELIEF        an accepted proposition with at least one supporting evidence reference.
    HYPOTHESIS    a proposed explanation or idea that has not been evaluated.
    INFERENCE     a conclusion derived from other information (e.g. by a model).
    PREDICTION    an expectation about an outcome not yet observed.
    VERIFIED      supported by an explicit validation process with recorded evidence.
    UNKNOWN       cannot currently be established.
    """

    OBSERVATION = "observation"
    SOURCE_CLAIM = "source_claim"
    BELIEF = "belief"
    HYPOTHESIS = "hypothesis"
    INFERENCE = "inference"
    PREDICTION = "prediction"
    VERIFIED = "verified"
    UNKNOWN = "unknown"


class Confidence(_StrEnum):
    """Ordinal, *uncalibrated* confidence label.

    These are not probabilities. Definitions:
    HIGH    stated directly by the user about themselves/their own work, or
            confirmed by a deterministic check.
    MEDIUM  stated by a source of unknown reliability, or derived by deterministic
            rules from high-confidence premises.
    LOW     model-generated, heuristically inferred (e.g. emotional cues), or contested.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Sensitivity(_StrEnum):
    NORMAL = "normal"
    PERSONAL = "personal"     # personal details; never sent to a remote provider unless allowed
    SENSITIVE = "sensitive"   # emotional/health data; short retention, never sent remotely unless allowed


class MemoryKind(_StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    IDEA = "idea"
    NOTE = "note"
    DECISION = "decision"
    PROCEDURE = "procedure"
    SELF = "self"


class MemoryStatus(_StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    FORGOTTEN = "forgotten"


class Origin(_StrEnum):
    """Who initiated an action request. Determines which permission rules apply."""

    USER_COMMAND = "user_command"        # explicit, user-directed request
    APPROVAL = "approval"                # user approved a previously proposed operation
    MODEL_PROPOSAL = "model_proposal"    # suggested by a language model (untrusted)
    KERNEL = "kernel"                    # initiated by JARVIS's own logic (an inference)


class OperationStatus(_StrEnum):
    PROPOSED = "proposed"        # recorded, awaiting approval; nothing executed
    DENIED = "denied"            # rejected by policy; nothing executed
    STARTED = "started"          # write-ahead record: execution began, outcome not yet known
    SUCCEEDED = "succeeded"      # handler returned a result (see ``verified`` separately)
    FAILED = "failed"            # handler raised an error
    TIMED_OUT = "timed_out"      # no result within the deadline; outcome unknown
    INTERRUPTED = "interrupted"  # process stopped while STARTED; outcome unknown
    CANCELLED = "cancelled"      # withdrawn before execution


class StageStatus(_StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


class Intent(_StrEnum):
    REMEMBER = "remember"
    IDEA = "idea"
    CORRECT = "correct"
    FORGET = "forget"
    RECALL = "recall"
    GOAL_CREATE = "goal.create"
    TASK_ADD = "task.add"
    TASK_START = "task.start"
    TASK_DONE = "task.done"
    TASK_BLOCK = "task.block"
    RESUME = "resume"
    CALCULATE = "calculate"
    TIME = "time"
    APPROVE = "approve"
    DENY = "deny"
    PREFERENCE = "preference"
    CAPABILITIES = "capabilities"
    CHAT = "chat"


def to_dict(value: Any) -> Any:
    """Serialise contracts (dataclasses, enums, tuples) into JSON-compatible values."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: to_dict(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): to_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dict(v) for v in value]
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class Observation:
    """A raw input received by the kernel, before interpretation."""

    text: str
    channel: str
    received_at: str
    session_id: str
    redactions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(isinstance(self.text, str) and 0 < len(self.text) <= 8000,
                 "Observation text must contain 1-8000 characters")
        _require(self.channel in {"cli", "api", "repl", "voice", "test", "eval"}, f"Unknown channel: {self.channel}")


@dataclass(frozen=True)
class Interpretation:
    """What the interpreter thinks the input means. An interpretation is not a fact."""

    intent: Intent
    confidence: Confidence
    slots: dict[str, Any] = field(default_factory=dict)
    rule: str = ""
    interpreter: str = "rules-v1"
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryQuery:
    text: str
    limit: int = 6
    project_id: int | None = None
    goal_id: int | None = None
    category: str | None = None
    kinds: tuple[str, ...] = ()
    max_sensitivity: Sensitivity = Sensitivity.SENSITIVE
    include_inactive: bool = False

    def __post_init__(self) -> None:
        _require(1 <= self.limit <= 50, "MemoryQuery.limit must be within 1-50")


@dataclass(frozen=True)
class MemoryHit:
    """A retrieved memory with provenance, ranking evidence, and caveats."""

    id: int
    kind: str
    category: str
    key: str | None
    value: str
    epistemic: str
    confidence: str
    source: str
    source_event_id: int | None
    created_at: str
    updated_at: str
    last_verified_at: str | None
    sensitivity: str
    score: float
    components: dict[str, float]
    stale: bool = False
    stale_reason: str = ""
    conflicts_with: tuple[int, ...] = ()
    strategy: str = ""


@dataclass(frozen=True)
class AffectSignal:
    label: str
    basis: str              # "stated" | "inferred" | "stated_absent"
    confidence: Confidence
    evidence: str


@dataclass(frozen=True)
class CommunicationStyle:
    verbosity: str = "standard"      # brief | standard | detailed
    tone: str = "neutral"            # neutral | supportive
    lead_with: str = "answer"        # answer | acknowledgement
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Appraisal:
    """Structured, uncertainty-aware appraisal of the interaction context.

    ``stated`` signals come from explicit self-reports. ``possible`` signals are
    inferred possibilities and must never be asserted as facts about the person.
    """

    stated: tuple[AffectSignal, ...] = ()
    possible: tuple[AffectSignal, ...] = ()
    urgency: str = "normal"          # low | normal | high
    style: CommunicationStyle = field(default_factory=CommunicationStyle)
    uncertainty: str = ""
    caution: str = "normal"          # normal | elevated
    repeated_failures: int = 0
    sensitive: bool = False


@dataclass(frozen=True)
class ActionRequest:
    capability: str
    arguments: dict[str, Any]
    origin: Origin
    reason: str = ""
    idempotency_key: str | None = None


@dataclass
class ActionOutcome:
    """The honest outcome of an action request, stage by stage (Acceptance test 7).

    requested  the request exists (always True once recorded)
    attempted  execution began (handler invoked)
    executed   the handler returned without raising (None = unknown, e.g. timeout)
    observed   a result or error was captured
    verified   a postcondition check passed (True), failed (False), or none applies (None)
    """

    capability: str
    status: OperationStatus
    reason: str
    operation_id: int | None = None
    requested: bool = True
    attempted: bool = False
    executed: bool | None = False
    observed: bool = False
    verified: bool | None = None
    verification_note: str = ""
    result: Any = None
    error_type: str | None = None
    error_message: str | None = None
    replayed: bool = False

    @property
    def ok(self) -> bool:
        return self.status == OperationStatus.SUCCEEDED and self.verified is not False


@dataclass
class StageRecord:
    name: str
    status: StageStatus
    started_at: str
    duration_ms: float
    notes: dict[str, Any] = field(default_factory=dict)
