from server.app.pipeline.phases import AGENT_PHASES


def test_agent_reference_prompts_pass_video_dir_as_skill_io():
    for phase in AGENT_PHASES.values():
        text = phase.reference_path.read_text(encoding="utf-8")

        assert "input_dir=Video directory" in text
        assert "output_dir=Video directory" in text

    content_review_text = AGENT_PHASES["content_review"].reference_path.read_text(encoding="utf-8")
    assert "review_result.json" in content_review_text


def test_interaction_prompt_does_not_require_preassemble_metadata():
    text = AGENT_PHASES["interaction_generate"].reference_path.read_text(encoding="utf-8")

    assert "metadata.json" in text
    assert "不存在" in text
    assert "不要依赖" in text
