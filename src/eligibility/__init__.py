"""Financial Eligibility Engine v0.4.6 final conversational runtime and Web handoff closure."""

from eligibility.application_service import ApplicationService
from eligibility.conversation import ConversationOrchestrator
from eligibility.engine.evaluator import FinancialEligibilityEngine, RuleEvaluator
from eligibility.working_note import FileSearchWorkingNoteStore, InMemorySearchWorkingNoteStore

__version__ = "0.4.6"

__all__ = [
    "ApplicationService",
    "ConversationOrchestrator",
    "FinancialEligibilityEngine",
    "RuleEvaluator",
    "FileSearchWorkingNoteStore",
    "InMemorySearchWorkingNoteStore",
    "__version__",
]
