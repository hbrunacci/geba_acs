from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from access_control.services.intelectron.api3000_wrapper.api3000.errors import Api3000Error
from access_control.services.intelectron.api3000_wrapper.api3000.native import (
    NativeLibrary,
    WINDOWS_LIB_FILENAME,
    resolve_library_candidates,
)


class ResolveLibraryCandidatesTestCase(SimpleTestCase):
    def test_adds_windows_fallback_when_no_explicit_path(self):
        candidates = resolve_library_candidates()

        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[-1], WINDOWS_LIB_FILENAME)

    def test_does_not_add_windows_fallback_with_explicit_path(self):
        custom_path = "/tmp/custom-lib.so"

        candidates = resolve_library_candidates(custom_path)

        self.assertEqual(candidates, [custom_path])


class NativeLibraryFallbackTestCase(SimpleTestCase):
    @patch("access_control.services.intelectron.api3000_wrapper.api3000.native.CDLL")
    @patch("access_control.services.intelectron.api3000_wrapper.api3000.native.resolve_library_candidates")
    @patch("access_control.services.intelectron.api3000_wrapper.api3000.native.NativeLibrary._configure_signatures")
    def test_tries_windows_after_linux_failure(self, configure_mock, candidates_mock, cdll_mock):
        linux_path = "libitkcom.so.0.0.0"
        windows_path = "itkcom.dll"
        candidates_mock.return_value = [linux_path, windows_path]
        cdll_mock.side_effect = [OSError("linux fail"), MagicMock()]

        native = NativeLibrary()

        self.assertEqual(native.lib_path, windows_path)
        self.assertEqual(cdll_mock.call_count, 2)
        cdll_mock.assert_any_call(linux_path)
        cdll_mock.assert_any_call(windows_path)
        configure_mock.assert_called_once()

    @patch("access_control.services.intelectron.api3000_wrapper.api3000.native.CDLL")
    @patch("access_control.services.intelectron.api3000_wrapper.api3000.native.resolve_library_candidates")
    def test_raises_with_attempts_when_all_candidates_fail(self, candidates_mock, cdll_mock):
        linux_path = "libitkcom.so.0.0.0"
        windows_path = "itkcom.dll"
        candidates_mock.return_value = [linux_path, windows_path]
        cdll_mock.side_effect = [OSError("linux fail"), OSError("windows fail")]

        with self.assertRaises(Api3000Error) as excinfo:
            NativeLibrary()

        self.assertIn(linux_path, str(excinfo.exception))
        self.assertIn(windows_path, str(excinfo.exception))
