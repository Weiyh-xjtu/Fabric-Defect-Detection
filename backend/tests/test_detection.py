"""Detection API regression tests."""

import os

import pytest
import torch

from app.api.detection import _resolve_camera_device


def test_camera_cpu_device_does_not_hide_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU mode must not mutate process-wide CUDA visibility."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")

    device = _resolve_camera_device("cpu")

    assert device == torch.device("cpu")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"


def test_camera_gpu_device_does_not_mutate_cuda_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU mode should select CUDA through torch.device only."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    device = _resolve_camera_device("gpu")

    assert device == torch.device("cuda:0")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"


def test_camera_gpu_device_reports_unavailable_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU mode should fail clearly when CUDA is unavailable."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    with pytest.raises(RuntimeError, match="未检测到 CUDA 设备"):
        _resolve_camera_device("gpu")


def test_camera_device_rejects_unknown_mode() -> None:
    """Only the modes exposed by the frontend are accepted."""
    with pytest.raises(ValueError, match="不支持的检测模式"):
        _resolve_camera_device("auto")
