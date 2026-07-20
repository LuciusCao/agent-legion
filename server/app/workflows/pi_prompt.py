from pathlib import Path

from server.app.workflows.pi_protocol import build_prompt


def build_pi_prompt(
    *,
    job_id: str,
    node_key: str,
    job_dir: Path | str,
    skill_dir: Path | str,
    validator_script: Path | str,
    inputs: list[str],
    outputs: list[str],
    additional_prompt: str = "",
) -> str:
    # validator_script is kept for signature compatibility; the shared protocol
    # derives it as skill_dir/scripts/validate_output.py (all callers pass that).
    manifest = {
        "job_id": job_id,
        "node_key": node_key,
        "inputs": inputs,
        "expected_outputs": outputs,
        "additional_prompt": additional_prompt,
    }
    return build_prompt(manifest, job_dir=Path(job_dir), skill_dir=Path(skill_dir))


def build_pi_prompt_preview(
    node_key: str,
    skill_key: str,
    inputs: list[str],
    outputs: list[str],
    additional_prompt: str = "",
) -> str:
    return build_pi_prompt(
        job_id="<job_id>",
        node_key=node_key,
        job_dir="<job_working_directory>",
        skill_dir=f"<skill_root>/{skill_key}",
        validator_script=f"<skill_root>/{skill_key}/scripts/validate_output.py",
        inputs=inputs,
        outputs=outputs,
        additional_prompt=additional_prompt,
    )
