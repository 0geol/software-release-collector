# Software Release Collector

제품별 **공식 출처와 파일명 규칙을 JSON 설정으로 분리**한 범용 소프트웨어 릴리스 메타데이터 수집기입니다.

## 지원 범위

설정 파일만 추가하면 다음과 같은 소프트웨어를 수집할 수 있습니다.

- 공식 다운로드 페이지에서 안정 버전을 정규식으로 식별할 수 있는 제품
- Apache/nginx 스타일 디렉터리 또는 파일 링크가 있는 공식 다운로드 페이지
- EXE, MSI, DMG, PKG, DEB, RPM, AppImage, ZIP, 7z, TAR 계열 파일
- 아키텍처·플랫폼별로 서로 다른 다운로드 위치를 가진 제품

모든 사이트의 구조가 서로 다르므로 **제품 이름만으로 인터넷의 모든 소프트웨어를 무오류 자동 수집하는 방식은 아닙니다.** 대신 제품별 공식 URL과 최소 파싱 규칙을 `configs/*.json`으로 추가하는 구조입니다. 핵심 엔진 코드는 제품마다 수정하지 않습니다.

## 보장하는 동작

- 최신 안정 버전, 릴리스 날짜(제공되는 경우), 릴리스 노트 URL 수집
- 플랫폼·아키텍처별 파일명, 형식, 공식 다운로드 URL, 크기, 서명·체크섬 URL 수집
- 실제 바이너리를 다운로드하거나 실행하지 않음
- 다운로드하지 않은 파일을 `verified`로 표시하지 않고 `URL confirmed`로 표시
- 찾을 수 없는 아키텍처는 `missing`으로 명시
- 동일 메타데이터 중복 억제
- 같은 버전의 설치 파일 메타데이터가 변경되면 기존 파일을 덮어쓰지 않고 새 리비전 생성
- 기준선 문서와 분리된 `releases/`에만 기록
- 스케줄러·자동 모니터링은 생성하지 않음

## 요구사항

- Python 3.11 이상
- 외부 Python 패키지 없음

## 실행

```bash
PYTHONPATH=src python3 -m software_collector.cli \
  --config configs/vlc.json \
  --output-root output
```

Obsidian의 기존 제품 폴더에 기록하려면:

```bash
PYTHONPATH=src python3 -m software_collector.cli \
  --config configs/vlc.json \
  --obsidian-product-dir "$HOME/Documents/Obsidian Vault/프로그램 조사/VLC Media Player"
```

`--obsidian-product-dir`를 사용하면 지정 폴더의 기존 기준선은 건드리지 않고 **`releases/` 아래만 변경**합니다.

## 새 제품 추가

1. `configs/template.json`을 제품 slug 이름으로 복사합니다.
2. `version_source.url`을 공식 다운로드·릴리스 페이지로 지정합니다.
3. `version_source.regex`에 `(?P<version>...)` 이름 그룹을 넣습니다.
4. 공식 릴리스 날짜를 찾을 수 있으면 `release_date_source`에 `(?P<date>...)` 그룹을 지정합니다.
5. 플랫폼·아키텍처별 공식 다운로드 페이지를 `artifact_sources`에 추가합니다.
6. `filename_regex`에 `{version}` 자리표시자를 사용합니다.
7. 실행 결과에서 `missing` 항목과 URL을 검토합니다.

정규식의 `{version}`은 발견된 버전 문자열을 안전하게 이스케이프하여 치환합니다.

## 출력

```text
output/<product-slug>/releases/
├── release-<version>.json
├── release-<version>.md
└── release-tracker.jsonl
```

`checked_at`만 달라진 재실행은 동일 결과로 간주합니다. 파일명·URL·크기·서명 URL 등 실질 메타데이터가 달라지면 `release-<version>-<UTC timestamp>.json/.md`가 추가됩니다.

## 테스트

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 현재 제공 설정

- `configs/vlc.json` — VLC media player 데스크톱판
- `configs/template.json` — 새 제품 등록용 템플릿

## 한계

- JavaScript 실행 후에만 링크가 생기는 사이트, 로그인·CAPTCHA가 필요한 사이트, GraphQL/전용 API만 제공하는 사이트는 별도 어댑터가 필요합니다.
- 라이선스·재배포·시스템 요구사항·무인 설치 분석은 느리게 변하는 **제품 기준선 조사**이므로 이 릴리스 수집기가 자동 갱신하지 않습니다.
- 서명 검증과 로컬 아카이브가 필요하면 별도의 명시적 검증 모드를 구현해야 합니다.
