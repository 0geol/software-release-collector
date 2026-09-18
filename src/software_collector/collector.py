from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

USER_AGENT = "software-release-collector/0.1 (metadata-only; no artifact download)"
ALLOWED_FORMATS = {"exe", "msi", "dmg", "pkg", "deb", "rpm", "appimage", "zip", "7z", "tar", "gz", "xz"}


@dataclass(frozen=True)
class AppendResult:
    created: bool
    json_path: Path
    markdown_path: Path
    fingerprint: str


def fetch_text(url: str, timeout: float = 30.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.8"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _require_https_or_http(url: str, field: str) -> None:
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError(f"{field}에는 http/https 공식 URL이 필요합니다")


def validate_config(config: dict) -> None:
    for field in ("product", "slug", "version_source", "artifact_sources"):
        if field not in config:
            raise ValueError(f"필수 설정 누락: {field}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", config["slug"]):
        raise ValueError("slug는 소문자 영숫자와 하이픈만 사용할 수 있습니다")
    version_source = config["version_source"]
    for field in ("url", "regex"):
        if field not in version_source:
            raise ValueError(f"version_source.{field} 누락")
    _require_https_or_http(version_source["url"], "version_source.url")
    try:
        pattern = re.compile(version_source["regex"], re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        raise ValueError(f"version_source.regex 오류: {exc}") from exc
    if "version" not in pattern.groupindex:
        raise ValueError("version_source.regex에는 (?P<version>...) 그룹이 필요합니다")

    if "release_date_source" in config:
        source = config["release_date_source"]
        _require_https_or_http(source.get("url", ""), "release_date_source.url")
        if "regex" not in source or "(?P<date>" not in source["regex"]:
            raise ValueError("release_date_source.regex에는 (?P<date>...) 그룹이 필요합니다")

    if not isinstance(config["artifact_sources"], list) or not config["artifact_sources"]:
        raise ValueError("artifact_sources는 하나 이상의 항목이어야 합니다")
    for index, source in enumerate(config["artifact_sources"]):
        for field in ("url", "platform", "architecture", "filename_regex"):
            if field not in source:
                raise ValueError(f"artifact_sources[{index}].{field} 누락")
        _require_https_or_http(source["url"].replace("{version}", "0"), f"artifact_sources[{index}].url")


def _format_pattern(pattern: str, version: str) -> str:
    return pattern.replace("{version}", re.escape(version))


def _extract_named(text: str, pattern: str, group: str, *, version: str | None = None) -> str | None:
    if version is not None:
        pattern = _format_pattern(pattern, version)
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(group) if match else None


def _plain_line(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value))).strip()


def _file_format(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".appimage"):
        return "appimage"
    suffix = lower.rsplit(".", 1)[-1] if "." in lower else ""
    return suffix if suffix in ALLOWED_FORMATS else "other"


def parse_artifact_listing(listing_text: str, *, source: dict, version: str) -> list[dict]:
    base_url = source["url"].format(version=version)
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', listing_text, flags=re.IGNORECASE)
    filenames = {Path(href.split("?", 1)[0]).name for href in hrefs}
    include = re.compile(_format_pattern(source["filename_regex"], version), re.IGNORECASE)
    exclude = re.compile(source["exclude_regex"], re.IGNORECASE) if source.get("exclude_regex") else None
    packages: list[dict] = []

    for href in hrefs:
        filename = Path(href.split("?", 1)[0]).name
        if not filename or not include.search(filename) or (exclude and exclude.search(filename)):
            continue
        size_bytes = None
        for raw_line in listing_text.splitlines():
            if filename in raw_line:
                numbers = re.findall(r"(?<![-:])\b\d+\b", _plain_line(raw_line))
                if numbers:
                    size_bytes = int(numbers[-1])
                break

        signature_name = filename + source.get("signature_suffix", ".asc")
        checksum_name = filename + source.get("checksum_suffix", ".sha256")
        packages.append(
            {
                "platform": source["platform"],
                "architecture": source["architecture"],
                "format": _file_format(filename),
                "filename": filename,
                "size_bytes": size_bytes,
                "download_url": urljoin(base_url, href),
                "signature_url": urljoin(base_url, signature_name) if signature_name in filenames else None,
                "checksum_url": urljoin(base_url, checksum_name) if checksum_name in filenames else None,
                "status": "URL confirmed",
            }
        )
    return sorted(packages, key=lambda item: (item["format"], item["filename"]))


def collect_from_config(
    config: dict,
    *,
    timeout: float = 30.0,
    fetcher: Callable[[str, float], str] = fetch_text,
) -> dict:
    validate_config(config)
    version_source = config["version_source"]
    version_text = fetcher(version_source["url"], timeout)
    version = _extract_named(version_text, version_source["regex"], "version")
    if not version:
        raise ValueError("공식 버전 출처에서 안정 버전을 찾지 못했습니다")
    if re.search(r"(?:alpha|beta|preview|nightly|dev|rc)", version, re.IGNORECASE):
        raise ValueError(f"프리릴리스 버전은 stable 수집에서 제외됩니다: {version}")

    release_date = None
    if config.get("release_date_source"):
        date_source = config["release_date_source"]
        try:
            date_text = fetcher(date_source["url"].format(version=version), timeout)
            release_date = _extract_named(date_text, date_source["regex"], "date", version=version)
        except Exception:
            release_date = None

    packages: list[dict] = []
    cache: dict[str, str] = {}
    for source in config["artifact_sources"]:
        url = source["url"].format(version=version)
        try:
            if url not in cache:
                cache[url] = fetcher(url, timeout)
            found = parse_artifact_listing(cache[url], source=source, version=version)
        except Exception as exc:
            found = []
            error = str(exc)
        else:
            error = None
        if found:
            packages.extend(found)
        else:
            packages.append(
                {
                    "platform": source["platform"],
                    "architecture": source["architecture"],
                    "format": None,
                    "filename": None,
                    "size_bytes": None,
                    "download_url": None,
                    "signature_url": None,
                    "checksum_url": None,
                    "status": "missing",
                    "error": error,
                    "source_url": url,
                }
            )

    packages.sort(key=lambda p: (p["platform"], p["architecture"], p.get("format") or "", p.get("filename") or ""))
    return {
        "schema_version": 1,
        "product": config["product"],
        "slug": config["slug"],
        "edition": config.get("edition"),
        "channel": config.get("channel", "stable"),
        "version": version,
        "installer_build": config.get("installer_build", version),
        "release_date": release_date,
        "release_page_url": config.get("release_page_url", "").format(version=version) or None,
        "changelog_url": config.get("changelog_url", "").format(version=version) or None,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "artifact_policy": "metadata-only",
        "packages": packages,
    }


def snapshot_fingerprint(snapshot: dict) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "checked_at"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def _link(label: str, url: str | None) -> str:
    return f"[{label}]({url})" if url else "없음"


def render_markdown(snapshot: dict, fingerprint: str) -> str:
    lines = [
        "---",
        f'product: "{snapshot["product"]}"',
        "status: release-metadata",
        f'version: "{snapshot["version"]}"',
        f'checked_at: "{snapshot["checked_at"]}"',
        "artifact_status: URL confirmed or missing",
        f"fingerprint: {fingerprint}",
        "---",
        "",
        f'# {snapshot["product"]} {snapshot["version"]} 릴리스 메타데이터',
        "",
        f'- 릴리스 날짜: {snapshot.get("release_date") or "unknown"}',
        f'- 릴리스 페이지: {snapshot.get("release_page_url") or "unknown"}',
        f'- 변경 로그: {snapshot.get("changelog_url") or "unknown"}',
        "- 수집 수준: 메타데이터만 수집; 바이너리 다운로드·실행·해시 계산·서명 검증 미수행",
        "",
        "| 플랫폼 | 아키텍처 | 형식 | 파일명 | 크기(B) | 다운로드 | 서명 | 체크섬 | 상태 |",
        "|---|---|---|---|---:|---|---|---|---|",
    ]
    for package in snapshot.get("packages", []):
        lines.append(
            "| {platform} | {architecture} | {format} | {filename} | {size} | {download} | {signature} | {checksum} | {status} |".format(
                platform=package.get("platform") or "unknown",
                architecture=package.get("architecture") or "unknown",
                format=package.get("format") or "없음",
                filename=f'`{package["filename"]}`' if package.get("filename") else "없음",
                size=package.get("size_bytes") if package.get("size_bytes") is not None else "unknown",
                download=_link("URL", package.get("download_url")),
                signature=_link("ASC", package.get("signature_url")),
                checksum=_link("SHA-256", package.get("checksum_url")),
                status=package.get("status") or "URL confirmed",
            )
        )
    lines.extend(["", "> append-only 릴리스 출력이며 제품 기준선 문서를 수정하지 않는다.", ""])
    return "\n".join(lines)


def _iter_tracker(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _timestamp_suffix(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def append_snapshot(snapshot: dict, output_dir: Path) -> AppendResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    tracker = output_dir / "release-tracker.jsonl"
    fingerprint = snapshot_fingerprint(snapshot)
    for entry in _iter_tracker(tracker):
        if entry.get("fingerprint") == fingerprint:
            return AppendResult(False, output_dir / entry["json_file"], output_dir / entry["markdown_file"], fingerprint)

    stem = f'release-{snapshot["version"]}'
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    if json_path.exists() or markdown_path.exists():
        stem += f'-{_timestamp_suffix(snapshot["checked_at"])}'
        json_path = output_dir / f"{stem}.json"
        markdown_path = output_dir / f"{stem}.md"

    _atomic_write(json_path, json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(markdown_path, render_markdown(snapshot, fingerprint))
    entry = {
        "version": snapshot["version"],
        "checked_at": snapshot["checked_at"],
        "fingerprint": fingerprint,
        "json_file": json_path.name,
        "markdown_file": markdown_path.name,
    }
    with tracker.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return AppendResult(True, json_path, markdown_path, fingerprint)
