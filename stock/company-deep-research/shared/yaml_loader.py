import yaml
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(filename: str) -> dict:
    path = PROMPTS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_system_prompt(filename: str) -> str:
    data = load_prompt(filename)
    return data["system"]


def get_user_prompt(filename: str, **kwargs) -> str:
    data = load_prompt(filename)
    template = data["user_template"]
    return template.format(**kwargs)
