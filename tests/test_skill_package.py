"""Behavioral checks for portable packaging and non-destructive installation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.install_skill import install
from scripts.package_skill import REQUIRED_FILES, build_archive


class SkillPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_root = ROOT / "local-runs" / "package-tests"
        cls.temporary_root.mkdir(parents=True, exist_ok=True)

    def make_source(self, root):
        source = root / "source"
        for relative in REQUIRED_FILES:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture " + relative, encoding="utf-8")
        for relative in (".env", "local-runs/private.json", ".git/config",
                         "demo/index.html", "examples/skill-demo/credentials.json"):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PRIVATE_SENTINEL_NOT_A_REAL_SECRET", encoding="utf-8")
        return source

    def test_package_allowlist_and_reproducibility(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            root = Path(directory)
            source = self.make_source(root)
            first = root / "one.zip"
            second = root / "two.zip"
            build_archive(source, first)
            build_archive(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertIn("mcmeal-mate/SKILL.md", archive.namelist())
                data = b"".join(archive.read(name) for name in archive.namelist())
                self.assertNotIn(b"PRIVATE_SENTINEL", data)
                manifest = json.loads(archive.read("mcmeal-mate/package-manifest.json"))
                for name, expected in manifest["files"].items():
                    self.assertEqual(hashlib.sha256(archive.read("mcmeal-mate/" + name)).hexdigest(), expected)

    def test_flat_layout_and_existing_output_preserved(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            root = Path(directory)
            source = self.make_source(root)
            output = root / "flat.zip"
            build_archive(source, output, flat=True)
            original = output.read_bytes()
            with zipfile.ZipFile(output) as archive:
                self.assertIn("SKILL.md", archive.namelist())
            with self.assertRaises(FileExistsError):
                build_archive(source, output)
            self.assertEqual(output.read_bytes(), original)

    def test_line_endings_do_not_change_portable_package(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            root=Path(directory)
            source=self.make_source(root)
            (source/'SKILL.md').write_bytes(b'first\r\nsecond\r\n')
            windows=root/'windows.zip';build_archive(source,windows)
            (source/'SKILL.md').write_bytes(b'first\nsecond\n')
            unix=root/'unix.zip';build_archive(source,unix)
            self.assertEqual(windows.read_bytes(),unix.read_bytes())

    def test_clean_install_and_existing_skill_preserved(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            root = Path(directory)
            source = self.make_source(root)
            destination = root / "chosen-skills"
            result = install(source, destination)
            installed = Path(result["installed"])
            self.assertEqual((installed / "SKILL.md").read_bytes(), (source / "SKILL.md").read_bytes())
            self.assertFalse((installed / ".env").exists())
            marker = installed / "my-local-customization.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                install(source, destination)
            self.assertEqual(marker.read_text(), "keep")
            self.assertEqual(len(list(destination.iterdir())), 1)

    def test_missing_source_file_leaves_destination_untouched(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            root = Path(directory)
            source = self.make_source(root)
            (source / "SKILL.md").unlink()
            destination = root / "chosen-skills"
            with self.assertRaises(ValueError):
                install(source, destination)
            self.assertFalse(destination.exists())

    def test_repository_payload_has_all_runtime_dependencies(self):
        # Run the installed helper with an unrelated cwd: no source checkout leaks.
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            root = Path(directory)
            result = install(ROOT, root / "skills")
            installed = Path(result["installed"])
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", str(installed / "scripts/mcmeal.py"), "--help"],
                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("usage:", completed.stdout)
            demo = subprocess.run(
                [sys.executable, '-X', 'utf8', str(installed/'scripts/mcmeal.py'), 'demo', '--out', str(root/'demo-result')],
                cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=15,
                env={key:value for key,value in os.environ.items() if key!='MCD_MCP_TOKEN'})
            self.assertEqual(demo.returncode,0,demo.stderr+demo.stdout)
            self.assertIn('demo_complete',demo.stdout)
            self.assertIn('虚构餐品和金额',(root/'demo-result/preference-share.md').read_text(encoding='utf-8'))


if __name__ == "__main__":
    unittest.main()
