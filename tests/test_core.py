import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from dev_forge.core import (
    Config,
    ExtensionRelease,
    PackagerError,
    ProfileSettings,
    build_bundle,
    find_settings,
    load_config,
    query_extension,
    vscode_download,
)


class CoreTests(unittest.TestCase):
    def test_vscode_url(self):
        url, filename = vscode_download("1.95.3", "user", "x64")
        self.assertEqual(url, "https://update.code.visualstudio.com/1.95.3/win32-x64-user/stable")
        self.assertEqual(filename, "VSCodeUserSetup-x64-1.95.3.exe")

    def test_finds_settings_for_named_local_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            user_root = Path(temp)
            storage = user_root / "globalStorage" / "storage.json"
            storage.parent.mkdir()
            storage.write_text(json.dumps({
                "userDataProfiles": [
                    {"name": "Java", "location": "profile-id"},
                ],
            }), encoding="utf-8")
            settings = user_root / "profiles" / "profile-id" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text("{}", encoding="utf-8")

            with patch("dev_forge.core.user_data_root", return_value=user_root):
                self.assertEqual(find_settings("Java"), settings.resolve())
                self.assertIsNone(find_settings("Python"))

    def test_selects_latest_stable_compatible_platform(self):
        def fake_request(_url, _body):
            def release(version, engine, prerelease=False, target=None):
                props = [{"key": "Microsoft.VisualStudio.Code.Engine", "value": engine}]
                if prerelease:
                    props.append({"key": "Microsoft.VisualStudio.Code.PreRelease", "value": "true"})
                return {"version": version, "properties": props, "targetPlatform": target, "files": []}
            return {"results": [{"extensions": [{"versions": [
                release("3.0.0", "^1.99.0"),
                release("2.5.0", "^1.90.0", True),
                release("2.4.0", "^1.90.0", target="linux-x64"),
                release("2.3.0", "^1.90.0", target="win32-x64"),
                release("2.2.0", "^1.80.0"),
            ]}]}]}

        selected = query_extension("sample.extension", "1.95.3", "x64", fake_request)
        self.assertEqual(selected.version, "2.3.0")
        self.assertEqual(selected.target_platform, "win32-x64")

    def test_config_rejects_non_exact_vscode_version(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            settings = base / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(json.dumps({
                "vscode": {"version": "1.95", "package": "system", "arch": "x64"},
                "extensions": [],
                "settings": str(settings),
            }), encoding="utf-8")
            with self.assertRaises(PackagerError):
                load_config(config)

    def test_config_supports_comments_without_breaking_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            settings = base / "https://settings.json"
            settings.parent.mkdir()
            settings.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text('''{
                // VS Code 下载配置
                "vscode": {
                    "version": "1.95.3", /* 完整版本 */
                    "package": "system",
                    "arch": "x64"
                },
                "extensions": [],
                "settings": "https://settings.json",
                "output_dir": "dist"
            }''', encoding="utf-8")
            loaded = load_config(config)
            self.assertEqual(loaded.version, "1.95.3")
            self.assertEqual(loaded.settings, settings.resolve())

    def test_config_supports_extension_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            settings = base / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            python_settings = base / "python-settings.json"
            python_settings.write_text('{"python.analysis.typeCheckingMode":"basic"}', encoding="utf-8")
            config = base / "config.json"
            config.write_text(json.dumps({
                "vscode": {"version": "1.95.3"},
                "extensions": {
                    "default": ["Sample.Common", "sample.common"],
                    "profiles": {
                        "Backend Java": ["Sample.Common", "Sample.Java"],
                        "Python": ["Sample.Python"],
                    },
                },
                "settings": {
                    "default": str(settings),
                    "profiles": {
                        "Backend Java": {"use_default": True},
                        "Python": str(python_settings),
                    },
                },
            }), encoding="utf-8")

            loaded = load_config(config)

            self.assertEqual(loaded.extensions, ("sample.common",))
            self.assertEqual(loaded.extension_profiles, (
                ("Backend Java", ("sample.java",)),
                ("Python", ("sample.python",)),
            ))
            self.assertEqual(loaded.profile_settings, (
                ProfileSettings("Backend Java", None, True),
                ProfileSettings("Python", python_settings.resolve()),
            ))

    def test_config_rejects_settings_for_undeclared_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            settings = base / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(json.dumps({
                "vscode": {"version": "1.95.3"},
                "extensions": {"profiles": {"Java": ["sample.java"]}},
                "settings": {
                    "default": str(settings),
                    "profiles": {"Python": {"use_default": True}},
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(PackagerError, "未在 extensions.profiles 中声明"):
                load_config(config)

    def test_config_rejects_reserved_default_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            settings = base / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(json.dumps({
                "vscode": {"version": "1.95.3"},
                "extensions": {"profiles": {"Default": ["sample.extension"]}},
                "settings": str(settings),
            }), encoding="utf-8")
            with self.assertRaisesRegex(PackagerError, "Default 是保留名称"):
                load_config(config)

    def test_builds_expected_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            settings = base / "settings.json"
            settings.write_text('{"editor.fontSize": 15}', encoding="utf-8")
            python_settings = base / "python-settings.json"
            python_settings.write_text('{"python.analysis.typeCheckingMode":"basic"}', encoding="utf-8")
            config = Config(
                "1.95.3",
                "system",
                "x64",
                ("sample.extension",),
                settings,
                base / "out",
                (
                    ("Backend Java", ("redhat.java",)),
                    ("Python", ("ms-python.python",)),
                ),
                (
                    ProfileSettings("Backend Java", None, True),
                    ProfileSettings("Python", python_settings),
                ),
            )

            def fake_download(url, destination):
                destination.write_bytes(("content:" + url).encode())
                return "a" * 64

            def fake_query(extension_id, _version, _arch):
                return ExtensionRelease(extension_id, "2.3.0", "^1.90.0", None, "https://example.test/sample.vsix")

            output = build_bundle(config, downloader=fake_download, extension_query=fake_query, progress=lambda _: None)
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                prefix = "dev-forge-1.95.3-win32-x64/"
                self.assertIn(prefix + "manifest.json", names)
                self.assertIn(prefix + "install.ps1", names)
                self.assertIn(prefix + "user-data/default/settings.json", names)
                self.assertIn(prefix + "user-data/profiles/profile-2/settings.json", names)
                install_script = archive.read(prefix + "install.ps1").decode("utf-8-sig")
                self.assertIn("$ArchiveMode = $false", install_script)
                self.assertNotIn("if (false)", install_script)
                self.assertIn("-Wait -PassThru", install_script)
                self.assertIn("'extensions\\sample.extension-2.3.0.vsix'", install_script)
                self.assertIn("'Backend Java' = @(\n        'extensions\\redhat.java-2.3.0.vsix'", install_script)
                self.assertIn("'Python' = @(\n        'extensions\\ms-python.python-2.3.0.vsix'", install_script)
                self.assertNotIn("$JavaExtensions", install_script)
                self.assertIn("--profile', $Profile", install_script)
                self.assertIn("function Test-ProfileAvailable", install_script)
                self.assertIn("$ErrorActionPreference = 'Continue'", install_script)
                self.assertIn("'--list-extensions' 2>$null", install_script)
                self.assertIn("function Ensure-Profile", install_script)
                self.assertIn(
                    "& $CodePath '--profile' $Name '--list-extensions'",
                    install_script,
                )
                self.assertNotIn("--user-data-dir", install_script)
                self.assertIn("'--profile' $Name '--new-window' $BootstrapFolder", install_script)
                self.assertIn("VS Code 未能在 20 秒内创建 Profile", install_script)
                self.assertIn("Get-Process -Name 'Code'", install_script)
                self.assertLess(
                    install_script.index("Ensure-Profile -Name $ProfileName"),
                    install_script.index("Install-ProfileExtension -RelativePath $Extension -Profile $ProfileName"),
                )
                self.assertIn("$SharedSettingsProfiles = @(\n    'Backend Java'", install_script)
                self.assertIn("'Python' = 'user-data\\profiles\\profile-2\\settings.json'", install_script)
                self.assertIn("Set-ProfileSettingsInheritance", install_script)
                self.assertIn("useDefaultFlags", install_script)
                self.assertIn("New-Object System.Text.UTF8Encoding($false)", install_script)
                self.assertIn("[IO.File]::WriteAllText($StoragePath", install_script)
                self.assertNotIn("Set-Content -LiteralPath $StoragePath", install_script)
                manifest = json.loads(archive.read(prefix + "manifest.json"))
                self.assertEqual(manifest["extensions"][0]["version"], "2.3.0")
                self.assertEqual(manifest["schema_version"], 3)
                self.assertEqual(
                    manifest["extension_profiles"]["profiles"]["Backend Java"],
                    ["redhat.java"],
                )
                self.assertEqual(
                    manifest["settings"]["profiles"]["Backend Java"],
                    {"use_default": True},
                )

    def test_archive_bundle_generates_valid_powershell_boolean(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            settings = base / "settings.json"
            settings.write_text("{}", encoding="utf-8")
            config = Config("1.95.3", "archive", "arm64", (), None, base / "out")

            def fake_download(_url, destination):
                destination.write_bytes(b"archive")
                return "b" * 64

            output = build_bundle(config, downloader=fake_download, progress=lambda _: None)
            with zipfile.ZipFile(output) as archive:
                prefix = "dev-forge-1.95.3-win32-arm64/"
                script = archive.read(prefix + "install.ps1").decode("utf-8-sig")
                self.assertIn("$ArchiveMode = $true", script)
                self.assertIn("Expand-Archive", script)
                self.assertNotIn("if (true)", script)
                self.assertEqual(
                    archive.read(prefix + "user-data/default/settings.json").decode("utf-8"),
                    "{}\n",
                )


if __name__ == "__main__":
    unittest.main()
