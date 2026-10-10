"""Build the portable Skill using an explicit, credential-free file list."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


SKILL_NAME = "mcmeal-mate"
SOURCE_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FILES = (
    "SKILL.md",
    "LICENSE",
    "NOTICE.md",
    "CONTEST_DECLARATION.md",
    "scripts/mcmeal.py",
    "mcmeal/__init__.py",
    "mcmeal/native.py",
    "mcmeal/planner.py",
    "references/native-workflow.md",
    "references/input-schema.md",
    "examples/skill-demo/session.json",
    "examples/skill-demo/README.md",
    "docs/INSTALL.md",
)
OPTIONAL_FILES = ("agents/openai.yaml",)


def payload(source: Path = SOURCE_ROOT) -> dict[str, bytes]:
    """Read only reviewed distribution files, never a recursive repository copy."""
    source = source.resolve(strict=True)
    result: dict[str, bytes] = {}
    for relative in REQUIRED_FILES + OPTIONAL_FILES:
        candidate = source / relative
        if relative in OPTIONAL_FILES and not candidate.exists():
            continue
        if not candidate.is_file():
            raise ValueError(f"Missing required Skill file: {relative}")
        # Do not follow even an internal symlink: distribution is a real folder.
        if any(part.is_symlink() for part in (candidate, *candidate.parents)
               if part != source and source in part.parents):
            raise ValueError(f"Skill file must not be a symbolic link: {relative}")
        if not candidate.resolve().is_relative_to(source):
            raise ValueError(f"Skill file is outside the source folder: {relative}")
        # The allowlist contains UTF-8 text only. Match Git's canonical LF blobs
        # so Windows autocrlf does not change the published payload or manifest.
        data = candidate.read_bytes()
        data.decode('utf-8')
        result[relative] = data.replace(b'\r\n', b'\n')
    manifest = {
        "name": SKILL_NAME,
        "files": {name: hashlib.sha256(data).hexdigest()
                  for name, data in sorted(result.items())},
    }
    result["package-manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    return result


def build_archive(source: Path, output: Path, *, flat: bool = False) -> dict:
    files = payload(source)
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("The package output must end in .zip")
    if output.exists():
        raise FileExistsError(f"Output already exists; use another path: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive_name = name if flat else f"{SKILL_NAME}/{name}"
            info = zipfile.ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return {"archive": str(output), "files": len(files), "flat": flat,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description="打包麦麦搭子 Skill（仅包含明确列出的运行文件）")
    parser.add_argument("--source", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--out", type=Path, default=SOURCE_ROOT / "dist/mcmeal-mate-skill.zip")
    parser.add_argument("--flat", action="store_true", help="将 SKILL.md 放在 ZIP 根目录，供要求此结构的导入器使用")
    args = parser.parse_args()
    try:
        result = build_archive(args.source, args.out, flat=args.flat)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Package failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
