"""Which safety layers are switched on for a run.

M1 implements two layers: prompt rules and driver enforcement. The reviewer
sub-agent and the dry run arrive in M2 as two more fields.
"""

from pydantic import BaseModel, ConfigDict

DEFAULT_MODEL = "claude-haiku-5-5"


class LayerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    prompt_rules: bool  # device limits and interlocks written into the system prompt
    driver_enforcement: bool  # the safety wrapper blocks violating commands


CONFIGS: dict[str, LayerConfig] = {
    c.name: c
    for c in [
        LayerConfig(name="none", prompt_rules=False, driver_enforcement=False),
        LayerConfig(name="prompt_only", prompt_rules=True, driver_enforcement=False),
        LayerConfig(name="driver_only", prompt_rules=False, driver_enforcement=True),
        LayerConfig(name="prompt_driver", prompt_rules=True, driver_enforcement=True),
    ]
}
