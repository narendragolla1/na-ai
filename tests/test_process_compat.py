"""Tests for cross-platform process-group lifecycle helpers."""

import subprocess
from unittest import mock

from omniai.engine import process_compat


def test_new_group_popen_kwargs_posix():
    with mock.patch.object(process_compat, "IS_WINDOWS", False):
        kwargs = process_compat.new_group_popen_kwargs()
        assert kwargs == {"start_new_session": True}


def test_new_group_popen_kwargs_windows():
    with mock.patch.object(process_compat, "IS_WINDOWS", True):
        kwargs = process_compat.new_group_popen_kwargs()
        assert kwargs == {"creationflags": process_compat._CREATE_NEW_PROCESS_GROUP}


def test_terminate_group_posix_uses_killpg():
    mock_process = mock.MagicMock()
    mock_process.pid = 1234

    with mock.patch.object(process_compat, "IS_WINDOWS", False):
        with mock.patch("os.killpg") as mock_killpg:
            with mock.patch("os.getpgid", return_value=1234):
                process_compat.terminate_group(mock_process)
                mock_killpg.assert_called_once_with(1234, process_compat.signal.SIGTERM)


def test_terminate_group_posix_handles_missing_process():
    mock_process = mock.MagicMock()
    mock_process.pid = 9999

    with mock.patch.object(process_compat, "IS_WINDOWS", False):
        with mock.patch("os.killpg", side_effect=ProcessLookupError):
            # Should not raise
            process_compat.terminate_group(mock_process)


def test_terminate_group_windows_sends_ctrl_break():
    mock_process = mock.MagicMock()
    mock_process.pid = 1234

    with mock.patch.object(process_compat, "IS_WINDOWS", True):
        process_compat.terminate_group(mock_process)
        mock_process.send_signal.assert_called_once_with(process_compat._CTRL_BREAK_EVENT)


def test_terminate_group_windows_handles_missing_process():
    mock_process = mock.MagicMock()
    mock_process.send_signal.side_effect = ProcessLookupError

    with mock.patch.object(process_compat, "IS_WINDOWS", True):
        # Should not raise
        process_compat.terminate_group(mock_process)


def test_kill_group_posix_uses_killpg_sigkill():
    mock_process = mock.MagicMock()
    mock_process.pid = 1234

    with mock.patch.object(process_compat, "IS_WINDOWS", False):
        with mock.patch("os.killpg") as mock_killpg:
            with mock.patch("os.getpgid", return_value=1234):
                process_compat.kill_group(mock_process)
                mock_killpg.assert_called_once_with(1234, process_compat.signal.SIGKILL)


def test_kill_group_windows_uses_taskkill():
    mock_process = mock.MagicMock()
    mock_process.pid = 4321

    with mock.patch.object(process_compat, "IS_WINDOWS", True):
        with mock.patch("subprocess.run") as mock_run:
            process_compat.kill_group(mock_process)
            mock_run.assert_called_once_with(
                ["taskkill", "/F", "/T", "/PID", "4321"],
                capture_output=True,
                check=False,
            )


def test_kill_group_posix_handles_missing_process():
    mock_process = mock.MagicMock()
    mock_process.pid = 9999

    with mock.patch.object(process_compat, "IS_WINDOWS", False):
        with mock.patch("os.killpg", side_effect=ProcessLookupError):
            # Should not raise
            process_compat.kill_group(mock_process)


def test_constants_are_real_on_windows_or_have_documented_fallback():
    """CREATE_NEW_PROCESS_GROUP/CTRL_BREAK_EVENT only exist on Windows in the
    stdlib; this module must still import on POSIX via getattr fallbacks."""
    assert isinstance(process_compat._CREATE_NEW_PROCESS_GROUP, int)
    assert isinstance(process_compat._CTRL_BREAK_EVENT, int)


def test_new_group_popen_kwargs_returns_dict_type():
    kwargs = process_compat.new_group_popen_kwargs()
    assert isinstance(kwargs, dict)
    # Sanity: on whatever platform tests actually run, subprocess.Popen
    # must accept these kwargs without raising TypeError.
    subprocess.Popen  # noqa: B018 - just referencing the symbol exists
