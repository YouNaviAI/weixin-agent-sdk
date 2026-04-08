"""weixin_agent_sdk.util.random_util 的测试用例。"""

import re

import pytest

from weixin_agent_sdk.util.random_util import generate_id, temp_file_name


# ---------------------------------------------------------------------------
# generate_id
# ---------------------------------------------------------------------------

class TestGenerateId:
    PATTERN = re.compile(r"^.+:\d+-[0-9a-f]{8}$")

    def test_format(self):
        result = generate_id("msg")
        assert self.PATTERN.match(result), f"格式不符：{result!r}"

    def test_prefix_preserved(self):
        assert generate_id("req").startswith("req:")
        assert generate_id("upload").startswith("upload:")

    def test_uniqueness(self):
        ids = {generate_id("x") for _ in range(1000)}
        assert len(ids) == 1000

    def test_contains_timestamp(self):
        import time
        before = int(time.time() * 1000)
        result = generate_id("t")
        after = int(time.time() * 1000)
        ts_part = int(result.split(":")[1].split("-")[0])
        assert before <= ts_part <= after

    def test_empty_prefix(self):
        result = generate_id("")
        assert result.startswith(":")

    def test_hex_part_is_lowercase(self):
        for _ in range(20):
            hex_part = generate_id("x").split("-")[-1]
            assert hex_part == hex_part.lower()


# ---------------------------------------------------------------------------
# temp_file_name
# ---------------------------------------------------------------------------

class TestTempFileName:
    PATTERN = re.compile(r"^.+-\d+-[0-9a-f]{8}\..+$")

    def test_format_with_dot_ext(self):
        result = temp_file_name("tmp", ".png")
        assert self.PATTERN.match(result), f"格式不符：{result!r}"

    def test_ext_appended(self):
        result = temp_file_name("media", ".mp4")
        assert result.endswith(".mp4")

    def test_prefix_preserved(self):
        result = temp_file_name("upload", ".jpg")
        assert result.startswith("upload-")

    def test_uniqueness(self):
        names = {temp_file_name("f", ".bin") for _ in range(1000)}
        assert len(names) == 1000

    def test_ext_without_dot_raises(self):
        with pytest.raises(ValueError, match=r"'.'"):
            temp_file_name("tmp", "png")

    def test_empty_ext_allowed(self):
        result = temp_file_name("tmp", "")
        assert "-" in result
        assert not result.endswith(".")

    def test_various_extensions(self):
        for ext in (".jpg", ".mp4", ".pdf", ".wav", ".bin"):
            result = temp_file_name("f", ext)
            assert result.endswith(ext)
