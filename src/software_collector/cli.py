from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .collector import append_snapshot, collect_from_config


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="제품별 공식 출처 설정으로 소프트웨어 릴리스 메타데이터를 수집합니다.")
    value.add_argument("--config", required=True, type=Path, help="제품별 JSON 설정")
    destination = value.add_mutually_exclusive_group()
    destination.add_argument("--output-root", type=Path, default=Path("output"), help="제품 slug별 출력 루트")
    destination.add_argument("--obsidian-product-dir", type=Path, help="Obsidian 제품 폴더; releases/ 아래만 변경")
    value.add_argument("--timeout", type=float, default=30.0)
    value.add_argument("--print-json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        snapshot = collect_from_config(config, timeout=args.timeout)
        output = args.obsidian_product_dir / "releases" if args.obsidian_product_dir else args.output_root / snapshot["slug"] / "releases"
        result = append_snapshot(snapshot, output)
    except Exception as exc:
        print(f"수집 실패: {exc}", file=sys.stderr)
        return 1

    if args.print_json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print(f"status={'created' if result.created else 'unchanged'}")
    print(f"product={snapshot['product']}")
    print(f"version={snapshot['version']}")
    print(f"packages={len(snapshot['packages'])}")
    print(f"json={result.json_path}")
    print(f"markdown={result.markdown_path}")
    print(f"fingerprint={result.fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
