"""
Backward-compatibility forwarder for ui.tabs.compose -> ui.compose.
"""
from ui.compose import *
from ui.compose import (
    _apply_variable_fallback,
    _missing_tokens,
    _resolve_all_recipients,
    _get_default_copy,
    _stub_contact,
)
