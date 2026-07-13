from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from app import local_exec


def test_launcher_rejects_wrong_arity_and_missing_target(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["local_exec"])
    assert local_exec.main() == 2

    missing = tmp_path / "missing.py"
    monkeypatch.setattr(
        sys, "argv", ["local_exec", str(missing), "10", "268435456", "65536"]
    )
    assert local_exec.main() == 2


def test_launcher_executes_target_with_sanitized_argv(monkeypatch, tmp_path):
    target = tmp_path / "implementation.py"
    target.write_text("assert __import__('sys').argv == [__file__]\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["local_exec", str(target), "0", "1", "1", "ignored"]
    )
    # The launcher accepts exactly its four resource arguments and exposes only
    # the target path to the implementation.
    assert local_exec.main() == 2

    monkeypatch.setattr(
        sys, "argv", ["local_exec", str(target), "1", "268435456", "65536"]
    )
    with patch.object(local_exec, "_set_limits") as set_limits:
        assert local_exec.main() == 0
    set_limits.assert_called_once_with(1, 268435456, 65536)


@pytest.mark.parametrize("value", ["not-an-int", "1.5", ""])
def test_launcher_rejects_invalid_resource_arguments(monkeypatch, tmp_path, value):
    target = tmp_path / "implementation.py"
    target.write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["local_exec", str(target), value, "268435456", "65536"]
    )
    with pytest.raises(ValueError):
        local_exec.main()
