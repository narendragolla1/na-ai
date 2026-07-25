"""Tests for best-effort accelerator detection."""

import subprocess
from unittest import mock

from omniai.engine.device import DeviceType, detect_device_type


def _cuda_available_mock():
    """Patch context that reports nvidia-smi present and reporting a GPU."""
    return mock.patch(
        "omniai.engine.device.shutil.which",
        side_effect=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )


def test_detect_cuda_when_nvidia_smi_reports_gpu():
    with _cuda_available_mock():
        with mock.patch(
            "omniai.engine.device.subprocess.run",
            return_value=mock.MagicMock(returncode=0, stdout=b"GPU 0: Fake GPU\n"),
        ):
            assert detect_device_type() == DeviceType.CUDA


def test_detect_falls_back_when_nvidia_smi_missing():
    with mock.patch("omniai.engine.device.shutil.which", return_value=None):
        with mock.patch("omniai.engine.device.platform.system", return_value="Linux"):
            assert detect_device_type() == DeviceType.CPU


def test_detect_rocm_when_rocm_smi_present_and_no_nvidia():
    def which(name):
        return "/usr/bin/rocm-smi" if name == "rocm-smi" else None

    with mock.patch("omniai.engine.device.shutil.which", side_effect=which):
        with mock.patch("omniai.engine.device.platform.system", return_value="Linux"):
            assert detect_device_type() == DeviceType.ROCM


def test_detect_mps_on_apple_silicon_without_gpu_tools():
    with mock.patch("omniai.engine.device.shutil.which", return_value=None):
        with mock.patch("omniai.engine.device.platform.system", return_value="Darwin"):
            with mock.patch("omniai.engine.device.platform.machine", return_value="arm64"):
                assert detect_device_type() == DeviceType.MPS


def test_detect_cpu_on_intel_mac():
    with mock.patch("omniai.engine.device.shutil.which", return_value=None):
        with mock.patch("omniai.engine.device.platform.system", return_value="Darwin"):
            with mock.patch("omniai.engine.device.platform.machine", return_value="x86_64"):
                assert detect_device_type() == DeviceType.CPU


def test_detect_cpu_when_nvidia_smi_present_but_reports_no_gpu():
    """nvidia-smi installed but no GPU attached (e.g. driver present, card removed)."""
    with _cuda_available_mock():
        with mock.patch(
            "omniai.engine.device.subprocess.run",
            return_value=mock.MagicMock(returncode=0, stdout=b""),
        ):
            with mock.patch("omniai.engine.device.platform.system", return_value="Linux"):
                assert detect_device_type() == DeviceType.CPU


def test_detect_cpu_when_nvidia_smi_errors():
    with _cuda_available_mock():
        with mock.patch(
            "omniai.engine.device.subprocess.run",
            side_effect=subprocess.SubprocessError,
        ):
            with mock.patch("omniai.engine.device.platform.system", return_value="Linux"):
                assert detect_device_type() == DeviceType.CPU
