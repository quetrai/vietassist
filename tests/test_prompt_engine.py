from services.prompt_engine import (
    GIRL_KEYWORDS,
    KEEP_FACE_KEYWORDS,
    build_image_prompt_instruction,
    build_text_prompt_instruction,
    prompt_spec,
)


def test_prompt_reference_mode_has_attachment_semantics():
    spec = prompt_spec("giữ mặt tôi trong ảnh, đổi bối cảnh")
    assert spec.mode == "reference"
    assert "attached reference image" in spec.subject_phrase
    assert "đính kèm" in spec.hint


def test_prompt_locked_mode_has_fixed_character():
    spec = prompt_spec("cô gái 20 tuổi mặc áo dài")
    assert spec.mode == "locked"
    assert "20-year-old Vietnamese woman" in spec.identity_lock_block
    assert "same 20-year-old Vietnamese woman" in spec.subject_phrase


def test_prompt_default_mode_is_self_contained_text():
    spec = prompt_spec("một cô gái đứng bên hồ")
    assert spec.mode == "described"
    assert "Never refer to a reference image" in spec.subject_rule
    assert "face shape" in spec.subject_rule


def test_reference_keyword_wins_over_girl_keyword():
    spec = prompt_spec("giữ mặt cô gái 20 tuổi")
    assert spec.mode == "reference"


def test_text_instruction_contains_framing_and_pose_requirements():
    instruction, spec = build_text_prompt_instruction("cô gái đứng trước quán cà phê")
    assert spec.mode == "described"
    assert "aspect ratio" in instruction
    assert "headroom" in instruction
    assert "each arm and elbow" in instruction
    assert "--ar" in instruction


def test_image_instruction_reads_reference_and_rejects_example_copying():
    instruction, spec = build_image_prompt_instruction("giữ mặt tôi, đổi sang áo dài")
    assert spec.mode == "reference"
    assert "Inspect the attached reference image carefully" in instruction
    assert "never copy the neutral example's scene" in instruction
    assert "attached reference image" in instruction
    assert "<user_request>" in instruction


def test_keywords_are_case_insensitive_and_vietnamese_friendly():
    assert any(k in "giữ mặt tôi".casefold() for k in KEEP_FACE_KEYWORDS)
    assert any(k in "CÔ GÁI 20".casefold() for k in GIRL_KEYWORDS)
