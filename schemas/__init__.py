"""V2 workflow schema models — Pydantic BaseModel definitions.

All data files on disk remain JSON. These models define structure,
provide runtime validation, and enable typed PTC extraction.

Usage:
    from schemas import AgentState, SessionLog, AgentMessage
    state = AgentState.model_validate_json(raw_json)
    state.model_dump_json()
"""

from schemas._constants import (
    AGENT_ID_PATTERN,
    SUMMARY_MAX_LENGTH,
    HandoffReason,
    MessageType,
    TracingMixin,
)
from schemas.agent_state import (
    AgentModel,
    AgentRole,
    AgentState,
    AgentStatus,
    PendingAnnotation,
)
from schemas.annotation import (
    AnnotationEntry,
    AnnotationPriority,
    AnnotationSource,
    ThinkValidationLogEntry,
    ThinkValidationResult,
)
from schemas.decision_log import DecisionLogEntry
from schemas.delegation_prompt import (
    DelegationPrompt,
    DelegationPromptAdapter,
    ExplorationDelegation,
    ExplorationScope,
    FileCoordinate,
    GuidedDelegation,
    KnownContext,
    OutputContract,
    ResearchDelegation,
    ResearchScope,
    TargetedDelegation,
    TddChunkDelegation,
    TestSpecifications,
)
from schemas.delegation_return import (
    DelegationReturn,
    DelegationStatus,
    ExplorationReturn,
    GuidedReturn,
    ResearchReturn,
    TargetedReturn,
    TddChunkReturn,
    UnknownResolved,
)
from schemas.handoff import Handoff, HandoffDecision, TestSummary
from schemas.hook_output import HookOutput, HookSpecificOutput, PermissionDecision
from schemas.message_bus_log import MessageBusLogEntry
from schemas.message_protocol import (
    AgentMessage,
    BugReportMessage,
    BugReportPayload,
    ContextQueryMessage,
    ContextQueryPayload,
    ContextResponseMessage,
    ContextResponsePayload,
    HandoffMessage,
    HandoffPayload,
    InfoReadyMessage,
    InfoReadyPayload,
    InfoRequestContext,
    InfoRequestMessage,
    InfoRequestPayload,
    PeerNotifyMessage,
    PeerNotifyPayload,
    ShutdownMessage,
    ShutdownPayload,
    StatusUpdateMessage,
    StatusUpdatePayload,
    TaskAssignInputs,
    TaskAssignMessage,
    TaskAssignOutput,
    TaskAssignPayload,
    TaskCompleteMessage,
    TaskCompletePayload,
)
from schemas.messaging import (
    AgentRegistration,
    MessageEnvelope,
    TeamManifest,
    TeamRegistry,
    TeamRegistryEntry,
)
from schemas.preferences import (
    AuditMode,
    Preference,
    PreferenceConfidence,
    Preferences,
    PreferenceSource,
)
from schemas.session_log import (
    Decision,
    Deviation,
    FileEntry,
    InvariantEntry,
    KeyFileEntry,
    LearningSignal,
    QualityGateSnapshot,
    SessionLog,
    StateEntry,
    TestEntry,
)
from schemas.state_machine import (
    StateDefinition,
    StateMachineDefinition,
    TransitionDefinition,
)
from schemas.state_transition_log import StateTransitionEntry
from schemas.system_state import (
    PhaseHistoryEntry,
    RemediationCycleEntry,
    SystemState,
    WorkflowState,
)

__all__ = [
    # _constants
    "AGENT_ID_PATTERN",
    "SUMMARY_MAX_LENGTH",
    "HandoffReason",
    "MessageType",
    "TracingMixin",
    # agent_state
    "AgentModel",
    "AgentRole",
    "AgentState",
    "AgentStatus",
    "PendingAnnotation",
    # annotation
    "AnnotationEntry",
    "AnnotationPriority",
    "AnnotationSource",
    "ThinkValidationLogEntry",
    "ThinkValidationResult",
    # decision_log
    "DecisionLogEntry",
    # delegation_prompt
    "DelegationPrompt",
    "DelegationPromptAdapter",
    "ExplorationDelegation",
    "ExplorationScope",
    "FileCoordinate",
    "GuidedDelegation",
    "KnownContext",
    "OutputContract",
    "ResearchDelegation",
    "ResearchScope",
    "TargetedDelegation",
    "TddChunkDelegation",
    "TestSpecifications",
    # delegation_return
    "DelegationReturn",
    "DelegationStatus",
    "ExplorationReturn",
    "GuidedReturn",
    "ResearchReturn",
    "TargetedReturn",
    "TddChunkReturn",
    "UnknownResolved",
    # handoff
    "Handoff",
    "HandoffDecision",
    "TestSummary",
    # hook_output
    "HookOutput",
    "HookSpecificOutput",
    "PermissionDecision",
    # message_bus_log
    "MessageBusLogEntry",
    # message_protocol
    "AgentMessage",
    "BugReportMessage",
    "BugReportPayload",
    "ContextQueryMessage",
    "ContextQueryPayload",
    "ContextResponseMessage",
    "ContextResponsePayload",
    "HandoffMessage",
    "HandoffPayload",
    "InfoReadyMessage",
    "InfoReadyPayload",
    "InfoRequestContext",
    "InfoRequestMessage",
    "InfoRequestPayload",
    "PeerNotifyMessage",
    "PeerNotifyPayload",
    "ShutdownMessage",
    "ShutdownPayload",
    "StatusUpdateMessage",
    "StatusUpdatePayload",
    "TaskAssignInputs",
    "TaskAssignMessage",
    "TaskAssignOutput",
    "TaskAssignPayload",
    "TaskCompleteMessage",
    "TaskCompletePayload",
    # messaging
    "AgentRegistration",
    "MessageEnvelope",
    "TeamManifest",
    "TeamRegistry",
    "TeamRegistryEntry",
    # preferences
    "AuditMode",
    "Preference",
    "PreferenceConfidence",
    "PreferenceSource",
    "Preferences",
    # session_log
    "Decision",
    "Deviation",
    "FileEntry",
    "InvariantEntry",
    "KeyFileEntry",
    "LearningSignal",
    "QualityGateSnapshot",
    "SessionLog",
    "StateEntry",
    "TestEntry",
    # state_machine
    "StateDefinition",
    "StateMachineDefinition",
    "TransitionDefinition",
    # state_transition_log
    "StateTransitionEntry",
    # system_state
    "PhaseHistoryEntry",
    "RemediationCycleEntry",
    "SystemState",
    "WorkflowState",
]
