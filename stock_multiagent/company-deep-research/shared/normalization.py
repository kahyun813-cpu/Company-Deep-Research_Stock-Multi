import json
import re


def extract_json(text: str) -> dict:
    # 마크다운 코드블록 제거
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Common LLM failure modes:
        # - trailing commas: {"a": 1,} or [1,2,]
        # - extra prose before/after the JSON object
        repaired = text

        # If there is leading/trailing junk, try to slice the first JSON object.
        first_obj = repaired.find("{")
        last_obj = repaired.rfind("}")
        if first_obj != -1 and last_obj != -1 and last_obj > first_obj:
            repaired = repaired[first_obj : last_obj + 1]

        # Remove trailing commas before a closing brace/bracket.
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            raise ValueError(f"JSON 파싱 실패: {e}\n원문: {text[:200]}")


def normalize_output(raw: dict, required_keys: list) -> dict:
    for key in required_keys:
        if key not in raw:
            raw[key] = None
    return raw


def replace_none_with_unavailable_strings(value, *, field_name: str = "value"):
    """
    Replace Python `None` (serialized as JSON `null`) with explicit text:
      "Data unavailable for <field_name>"

    This is designed for downstream agents that expect a string, not null.
    """

    if value is None:
        return f"Data unavailable for {field_name}"

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = replace_none_with_unavailable_strings(v, field_name=str(k))
        return out

    if isinstance(value, list):
        # For lists, keep the same field_name for items; downstream only needs
        # a readable "X" token rather than an exact index-qualified path.
        return [
            replace_none_with_unavailable_strings(v, field_name=field_name) for v in value
        ]

    return value