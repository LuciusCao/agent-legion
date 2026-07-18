from pathlib import Path


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
    lines = [
        "Execute the loaded node skill for this Video Hive workflow job.",
        "",
        f"Job ID: {job_id}",
        f"Node: {node_key}",
        f"Working directory: {job_dir}",
        f"Skill directory: {skill_dir}",
        f"Validator script: {validator_script}",
        "",
        "Declared inputs:",
        *(f"- {item}" for item in inputs),
        "",
        "Required outputs:",
        *(f"- {item}" for item in outputs),
        "",
        (
            "Write required outputs directly into the working directory. "
            "Never write outputs into the run/session directory (runs/); "
            "all declared outputs must live at the top level of the working directory. "
            "Do not modify inputs or create undeclared root-level artifacts. "
            "Finish after all required outputs are written and correct."
        ),
    ]
    if additional_prompt.strip():
        lines.extend(["", "Additional node instructions:", additional_prompt.strip()])
    return "\n".join(lines) + "\n"


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
