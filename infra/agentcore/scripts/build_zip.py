from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path


FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
REQUIRED_PATHS = (
    "agentcore_main.py",
    "bedrock_agentcore",
    "boto3",
    "feed_passport",
    "pydantic",
    "strands",
)


def validate_stage(stage: Path) -> None:
    names = {item.name for item in stage.iterdir()}
    missing = [name for name in REQUIRED_PATHS if name not in names]
    if missing:
        raise SystemExit(f"package stage is missing required paths: {', '.join(missing)}")
    forbidden = [item for item in stage.rglob("*") if item.suffix.lower() in {".dll", ".exe", ".pyd"}]
    if forbidden:
        raise SystemExit(f"Windows binaries are forbidden in AgentCore package: {forbidden[0]}")
    for shared_object in stage.rglob("*.so"):
        header = shared_object.read_bytes()[:20]
        if len(header) < 20 or header[:4] != b"\x7fELF":
            raise SystemExit(f"invalid ELF shared object: {shared_object}")
        byte_order = "little" if header[5] == 1 else "big"
        machine = int.from_bytes(header[18:20], byte_order)
        if machine != 183:
            raise SystemExit(
                f"non-ARM64 shared object in AgentCore package: {shared_object} (e_machine={machine})"
            )


def build(stage: Path, output: Path) -> None:
    validate_stage(stage)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = source.relative_to(stage).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    stage = args.stage.resolve(strict=True)
    output = args.output.resolve()
    if output == stage or stage in output.parents:
        raise SystemExit("output archive must not be inside the package stage")
    build(stage, output)
    print(output)


if __name__ == "__main__":
    main()
