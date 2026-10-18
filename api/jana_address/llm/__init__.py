"""Optional runtime AI path (Claude API or a local OpenAI-compatible model).

Off by default: the jury run is offline (brief, slide 11: «автономная работа без сети»), so the scored answer always
comes from the local rules ⊕ tagger ⊕ gazetteer. With `?ai=foreign|fallback|always` (or env JANA_AI_MODE) the service
sends foreign or low-confidence records to the model and merges the validated answer. See docs/AI_MODE.md.
"""
from .client import MODES, AIClient, AIConfig, get_client, set_client  # noqa: F401
from .prompt import OUTPUT_SCHEMA, prompt_version, system_prompt  # noqa: F401
