import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_forge import cli, updater
from dev_forge.core import (
    ExtensionRelease,
    PackagerError,
    auto_update,
    fetch_latest_vscode_version,
    load_config,
    load_extension_lock,
    update_config_vscode_version,
    write_extension_lock,
)


class UpdaterTests(unittest.TestCase):
    def test_fetch_latest_vscode_version_prefers_platform_endpoint(self):
        requested_urls: list[str] = []

        def fake_get(url: str):
            requested_urls.append(url)
            return {"productVersion": "1.148.0"}

        checked_urls: list[str] = []

        def fake_check(url: str) -> bool:
            checked_urls.append(url)
            return True

        version = fetch_latest_vscode_version(
            "user", "x64", json_getter=fake_get, url_checker=fake_check
        )

        self.assertEqual(version, "1.148.0")
        self.assertEqual(
            requested_urls,
            ["https://update.code.visualstudio.com/api/update/win32-x64-user/stable/latest"],
        )
        self.assertEqual(
            checked_urls,
            ["https://update.code.visualstudio.com/1.148.0/win32-x64-user/stable"],
        )

    def test_fetch_latest_vscode_version_falls_back_to_releases_list(self):
        def fake_get(url: str):
            if url.endswith("/latest"):
                return None
            return ["1.147.0", "1.148.1", "1.148.0", "invalid"]

        version = fetch_latest_vscode_version(
            "archive", "arm64", json_getter=fake_get, url_checker=lambda _u: True
        )
        self.assertEqual(version, "1.148.1")

    def test_fetch_latest_vscode_version_rejects_unavailable_installer(self):
        with self.assertRaisesRegex(PackagerError, "安装包尚未就绪"):
            fetch_latest_vscode_version(
                "user",
                "x64",
                json_getter=lambda _u: {"productVersion": "1.148.0"},
                url_checker=lambda _u: False,
            )

    def test_update_config_vscode_version_preserves_comments_and_formatting(self):
        original = (
            "{\n"
            "  // 顶部注释 \"version\": \"0.0.0\"\n"
            '  "other": {"version": "9.9.9"},\n'
            '  "vscode": {\n'
            "    // 版本号注释\n"
            '    "version": "1.147.0", /* inline comment */\n'
            '    "package": "user",\n'
            '    "arch": "x64"\n'
            "  },\n"
            '  "settings": "./config/settings.json"\n'
            "}\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "packager.jsonc"
            config_path.write_text(original, encoding="utf-8")

            changed = update_config_vscode_version(config_path, "1.148.0")
            self.assertTrue(changed)
            updated_text = config_path.read_text(encoding="utf-8")
            self.assertEqual(
                updated_text,
                original.replace('"version": "1.147.0"', '"version": "1.148.0"'),
            )
            self.assertFalse(update_config_vscode_version(config_path, "1.148.0"))

    def _write_sample_repo(
        self,
        root: Path,
        *,
        vscode_version: str = "1.147.0",
        ext_version: str = "1.0.0",
    ) -> tuple[Path, Path]:
        settings = root / "settings.json"
        settings.write_text("{}\n", encoding="utf-8")
        config_path = root / "packager.jsonc"
        config_path.write_text(
            "{\n"
            "  // VS Code 配置\n"
            '  "vscode": {\n'
            f'    "version": "{vscode_version}",\n'
            '    "package": "user",\n'
            '    "arch": "x64"\n'
            "  },\n"
            '  "extensions": {\n'
            '    "default": ["sample.ext"]\n'
            "  },\n"
            '  "settings": "./settings.json"\n'
            "}\n",
            encoding="utf-8",
        )
        lock_file = root / "packager.lock.json"
        write_extension_lock(
            lock_file,
            vscode_version,
            "x64",
            [
                ExtensionRelease(
                    "sample.ext",
                    ext_version,
                    "^1.100.0",
                    None,
                    f"https://example.test/sample.ext-{ext_version}.vsix",
                    "a" * 64,
                )
            ],
        )
        return config_path, lock_file

    def test_auto_update_noop_when_nothing_changed(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path, lock_file = self._write_sample_repo(Path(temp))
            download_calls = 0

            def fake_download(_url: str, _dest: Path) -> str:
                nonlocal download_calls
                download_calls += 1
                return "a" * 64

            result = auto_update(
                config_path,
                lock_file,
                vscode_version_fetcher=lambda _p, _a: "1.147.0",
                extension_query=lambda ext_id, _v, _a: ExtensionRelease(
                    ext_id,
                    "1.0.0",
                    "^1.100.0",
                    None,
                    "https://example.test/sample.ext-1.0.0.vsix",
                ),
                downloader=fake_download,
                extension_validator=lambda _p, _r: None,
                progress=lambda _: None,
            )

            self.assertFalse(result.changed)
            self.assertFalse(result.vscode_changed)
            self.assertFalse(result.lock_changed)
            self.assertEqual(download_calls, 0)

    def test_auto_update_upgrades_vscode_and_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path, lock_file = self._write_sample_repo(Path(temp))

            def fake_download(_url: str, dest: Path) -> str:
                dest.write_bytes(b"vsix")
                return "b" * 64

            result = auto_update(
                config_path,
                lock_file,
                vscode_version_fetcher=lambda _p, _a: "1.148.0",
                extension_query=lambda ext_id, v, _a: ExtensionRelease(
                    ext_id,
                    "1.1.0",
                    f"^{v}",
                    None,
                    "https://example.test/sample.ext-1.1.0.vsix",
                ),
                downloader=fake_download,
                extension_validator=lambda _p, _r: None,
                progress=lambda _: None,
            )

            self.assertTrue(result.changed)
            self.assertTrue(result.vscode_changed)
            self.assertTrue(result.lock_changed)
            self.assertEqual(result.previous_version, "1.147.0")
            self.assertEqual(result.target_version, "1.148.0")
            self.assertEqual(load_config(config_path).version, "1.148.0")
            locked = load_extension_lock(lock_file, "1.148.0", "x64", ["sample.ext"])
            self.assertEqual(locked["sample.ext"].version, "1.1.0")
            self.assertEqual(locked["sample.ext"].sha256, "b" * 64)

    def test_auto_update_updates_lock_only_when_extension_updates_and_never_downgrades(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path, lock_file = self._write_sample_repo(
                Path(temp), vscode_version="1.147.0", ext_version="1.0.0"
            )

            def fake_download(_url: str, dest: Path) -> str:
                dest.write_bytes(b"vsix")
                return "c" * 64

            result = auto_update(
                config_path,
                lock_file,
                # Upstream reports older version -> must not downgrade
                vscode_version_fetcher=lambda _p, _a: "1.146.0",
                extension_query=lambda ext_id, _v, _a: ExtensionRelease(
                    ext_id,
                    "1.2.0",
                    "^1.100.0",
                    None,
                    "https://example.test/sample.ext-1.2.0.vsix",
                ),
                downloader=fake_download,
                extension_validator=lambda _p, _r: None,
                progress=lambda _: None,
            )

            self.assertTrue(result.changed)
            self.assertFalse(result.vscode_changed)
            self.assertTrue(result.lock_changed)
            self.assertEqual(result.target_version, "1.147.0")
            self.assertEqual(load_config(config_path).version, "1.147.0")
            locked = load_extension_lock(lock_file, "1.147.0", "x64", ["sample.ext"])
            self.assertEqual(locked["sample.ext"].version, "1.2.0")

    def test_auto_update_leaves_files_untouched_on_download_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path, lock_file = self._write_sample_repo(Path(temp))
            before_config = config_path.read_text(encoding="utf-8")
            before_lock = lock_file.read_text(encoding="utf-8")

            def failing_download(_url: str, _dest: Path) -> str:
                raise PackagerError("network error")

            with self.assertRaisesRegex(PackagerError, "network error"):
                auto_update(
                    config_path,
                    lock_file,
                    vscode_version_fetcher=lambda _p, _a: "1.148.0",
                    extension_query=lambda ext_id, _v, _a: ExtensionRelease(
                        ext_id,
                        "1.1.0",
                        "^1.100.0",
                        None,
                        "https://example.test/sample.ext-1.1.0.vsix",
                    ),
                    downloader=failing_download,
                    extension_validator=lambda _p, _r: None,
                    progress=lambda _: None,
                )

            self.assertEqual(config_path.read_text(encoding="utf-8"), before_config)
            self.assertEqual(lock_file.read_text(encoding="utf-8"), before_lock)

    def test_cli_auto_update_writes_github_output_and_enforces_mutual_exclusion(self):
        for conflict in (
            ["--auto-update", "--locked"],
            ["--auto-update", "--update-lock"],
            ["--auto-update", "--no-lock"],
        ):
            with (
                self.subTest(conflict=conflict),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                cli.parser().parse_args(conflict)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path, lock_file = self._write_sample_repo(root)
            gh_output = root / "github_output.txt"

            fake_result = updater.UpdateResult(
                changed=True,
                vscode_changed=True,
                lock_changed=True,
                previous_version="1.147.0",
                target_version="1.148.0",
                extension_changes=(
                    updater.ExtensionChange("sample.ext", "1.0.0", "1.1.0"),
                ),
            )
            with (
                patch.object(cli, "auto_update", return_value=fake_result) as mocked,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                exit_code = cli.main(
                    [
                        "--config",
                        str(config_path),
                        "--lock-file",
                        str(lock_file),
                        "--auto-update",
                        "--github-output",
                        str(gh_output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            mocked.assert_called_once_with(
                config_path.resolve(), lock_file.resolve(), check_vscode=True
            )
            output_text = gh_output.read_text(encoding="utf-8")
            self.assertIn("changed=true\n", output_text)
            self.assertIn("vscode_changed=true\n", output_text)
            self.assertIn("target_version=1.148.0\n", output_text)
            self.assertIn("commit_message=feat: update version to 1.148.0\n", output_text)


if __name__ == "__main__":
    unittest.main()
