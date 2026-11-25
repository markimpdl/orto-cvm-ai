from cvm.models import Job
from cvm.pipeline import advance
from cvm.scripting.claude_runner import build_prompt


def test_prompt_includes_rewrite_feedback():
    job = Job(id=7, project_id=3, state="scripting")
    advance(job, "awaiting_approval")
    advance(job, "scripting", "gancho mais forte")
    prompt = build_prompt(job)
    assert "gancho mais forte" in prompt and "job_id=7" in prompt and "project 3" in prompt


def test_first_prompt_has_no_feedback():
    job = Job(id=1, project_id=1, state="scripting")
    assert "rejected" not in build_prompt(job)


def test_no_step_for_gpu_states():
    assert build_prompt(Job(id=1, project_id=1, state="voicing")) is None
