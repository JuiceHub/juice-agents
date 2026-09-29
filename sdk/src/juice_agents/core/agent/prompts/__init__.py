"""Default system prompts."""

from .codeact_agent_prompt import (
    CODEACT_PROMPT_SECTIONS,
    CODEACT_PROMPT_SECTIONS_EN,
    CODEACT_PROMPT_SECTIONS_ZH,
    DEFAULT_CODEACT_SYSTEM_PROMPT,
    DEFAULT_CODEACT_SYSTEM_PROMPT_EN,
    DEFAULT_CODEACT_SYSTEM_PROMPT_ZH,
    get_codeact_prompt_sections,
    get_codeact_prompt_segments,
    get_codeact_system_prompt,
)
from .react_agent_prompt import (
    DEFAULT_REACT_SYSTEM_PROMPT,
    DEFAULT_REACT_SYSTEM_PROMPT_EN,
    DEFAULT_REACT_SYSTEM_PROMPT_ZH,
    REACT_PROMPT_SECTIONS,
    REACT_PROMPT_SECTIONS_EN,
    REACT_PROMPT_SECTIONS_ZH,
    get_react_prompt_sections,
    get_react_prompt_segments,
    get_react_system_prompt,
)

__all__ = [
    "CODEACT_PROMPT_SECTIONS",
    "CODEACT_PROMPT_SECTIONS_EN",
    "CODEACT_PROMPT_SECTIONS_ZH",
    "DEFAULT_CODEACT_SYSTEM_PROMPT",
    "DEFAULT_CODEACT_SYSTEM_PROMPT_EN",
    "DEFAULT_CODEACT_SYSTEM_PROMPT_ZH",
    "DEFAULT_REACT_SYSTEM_PROMPT",
    "DEFAULT_REACT_SYSTEM_PROMPT_EN",
    "DEFAULT_REACT_SYSTEM_PROMPT_ZH",
    "REACT_PROMPT_SECTIONS",
    "REACT_PROMPT_SECTIONS_EN",
    "REACT_PROMPT_SECTIONS_ZH",
    "get_codeact_prompt_sections",
    "get_codeact_prompt_segments",
    "get_codeact_system_prompt",
    "get_react_prompt_sections",
    "get_react_prompt_segments",
    "get_react_system_prompt",
]
