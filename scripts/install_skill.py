"""Copy the portable Skill into a user-selected local skill directory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

if __package__:
    from .package_skill import SKILL_NAME, SOURCE_ROOT, payload
else:
    from package_skill import SKILL_NAME, SOURCE_ROOT, payload


def install(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    destination = destination.expanduser().resolve()
    target = destination / SKILL_NAME
    if source == target or source.is_relative_to(target):
        raise ValueError("The installation must not replace the source folder")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Skill already exists; it has not been changed: {target}")
    files = payload(source)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mcmeal-install-", dir=destination) as temporary:
        staging = Path(temporary) / SKILL_NAME
        staging.mkdir()
        for name, content in files.items():
            candidate = staging / name
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(content)
        # Recheck after staging so a concurrent install cannot be overwritten.
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Skill appeared during installation: {target}")
        staging.rename(target)
    return {"installed": str(target), "files": len(files),
            "next": "在客户端新建对话并请求使用 mcmeal-mate；实时查询另需客户端连接麦当劳 MCP。"}


def main() -> None:
    parser = argparse.ArgumentParser(description="安装麦麦搭子到显式指定的 Skills 父目录；不会修改客户端设置或覆盖现有技能")
    parser.add_argument("--source", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--dest", required=True, type=Path, help="Skills 父目录，例如 .agents/skills；脚本自动添加 mcmeal-mate")
    args = parser.parse_args()
    try:
        result = install(args.source, args.dest)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Install failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
