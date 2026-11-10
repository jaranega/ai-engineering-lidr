"""Golden hashes of the published prompt version v3.

v3 is published and immutable (prompt-templates). If this test fails, a change touched v3:
create a new prompt version instead of editing it.
"""

import hashlib

import pytest

from app.prompts.loader import render_estimation_prompt
from tests.conftest import make_input

REFS = [
    {"name": "Clinic app", "summary": "Booking web app with reminders.", "actual_hours": 640},
    {"name": "Booking portal", "summary": "Customer portal with payments."},
]

# (id, input overrides, num_examples, system sha256, user sha256)
CASES = [
    (
        "A",
        {},
        3,
        "d450ee4764e59744254f4cf7cdc00e01d32c5c52acafad06e001889eb3d0bf11",
        "f47fc3886ae476d0920bf0d50cc962102721fa59fcb94d5b355c685213822fae",
    ),
    (
        "B",
        {"project_type": "mobile_app", "detail_level": "summary", "output_format": "narrative"},
        0,
        "6fe96b03434b7f84a0e48ff91dbdab98cb1fc1b70b780fe29a48ef97c7c1032a",
        "3e261e5723618688352d3b65b8956945582f435fc5c2293cb2fe213f386972ab",
    ),
    (
        "C",
        {
            "project_type": "data_pipeline",
            "detail_level": "detailed",
            "output_format": "phases_table",
            "reference_projects": REFS,
        },
        3,
        "ba07387b0d7e6e868d36e92ae9ff36166a02c02c2bfb50b3c363ff231d7d4822",
        "a947eb74cbb4cefa5fc46a0436a0d85be369853f037a0ce030c1583be7adcd59",
    ),
    (
        "D",
        {
            "project_type": "internal_tool",
            "output_format": "line_items",
            "reference_projects": REFS,
        },
        0,
        "d773cf9a9752bd4e706801068d6a92eb74bf05af350c5302e94fe0b1e2ba359c",
        "8d1350523b049a06c13e526bf72a171a1be47598c41773148252c0b1463deceb",
    ),
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("case", "overrides", "num_examples", "system_sha", "user_sha"), CASES)
def test_v3_render_matches_golden_hashes(case, overrides, num_examples, system_sha, user_sha):
    """prompt-templates: v3 is frozen; its rendered system and user match the golden hashes."""
    request = make_input(**overrides)
    system, user = render_estimation_prompt(request, "v3", num_examples=num_examples)
    assert _sha(system) == system_sha
    assert _sha(user) == user_sha
