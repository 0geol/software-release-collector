import json
import tempfile
import unittest
from pathlib import Path

from software_collector.collector import (
    append_snapshot,
    collect_from_config,
    parse_artifact_listing,
    validate_config,
)


PRODUCT_HTML = '<span>Stable version 1.2.3</span>'
NEWS_HTML = '<article><time>2026-08-30</time><h2>Example App 1.2.3</h2></article>'
LISTING_HTML = '''
<pre>
<a href="example-1.2.3-x64.exe">example-1.2.3-x64.exe</a> 30-Aug-2026 10:00 123456
<a href="example-1.2.3-x64.exe.asc">example-1.2.3-x64.exe.asc</a> 30-Aug-2026 10:01 195
<a href="example-1.2.3-x64.exe.sha256">example-1.2.3-x64.exe.sha256</a> 30-Aug-2026 10:01 90
<a href="example-1.2.3-debug.zip">example-1.2.3-debug.zip</a> 30-Aug-2026 10:02 999999
</pre>
'''


def example_config():
    return {
        "schema_version": 1,
        "product": "Example App",
        "slug": "example-app",
        "edition": "desktop",
        "channel": "stable",
        "version_source": {
            "url": "https://vendor.example/download",
            "regex": r"Stable version (?P<version>\d+\.\d+\.\d+)",
        },
        "release_date_source": {
            "url": "https://vendor.example/news",
            "regex": r"(?P<date>20\d{2}-\d{2}-\d{2}).{0,100}Example App {version}",
        },
        "release_page_url": "https://vendor.example/releases/{version}",
        "changelog_url": "https://vendor.example/releases/{version}/notes",
        "artifact_sources": [
            {
                "url": "https://vendor.example/releases/{version}/windows/",
                "platform": "windows",
                "architecture": "x64",
                "filename_regex": r"^example-{version}-x64\.(?:exe|msi)$",
                "exclude_regex": r"debug|symbols",
                "signature_suffix": ".asc",
                "checksum_suffix": ".sha256",
            }
        ],
    }


class GenericCollectorTests(unittest.TestCase):
    def test_config_requires_official_version_source(self):
        config = example_config()
        del config["version_source"]
        with self.assertRaisesRegex(ValueError, "version_source"):
            validate_config(config)

    def test_parses_artifact_metadata_without_downloading_binary(self):
        source = example_config()["artifact_sources"][0]
        packages = parse_artifact_listing(
            LISTING_HTML,
            source=source,
            version="1.2.3",
        )
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertEqual(package["filename"], "example-1.2.3-x64.exe")
        self.assertEqual(package["size_bytes"], 123456)
        self.assertEqual(package["status"], "URL confirmed")
        self.assertTrue(package["signature_url"].endswith(".asc"))
        self.assertTrue(package["checksum_url"].endswith(".sha256"))
        self.assertNotIn("sha256", package)

    def test_collects_product_using_only_its_config(self):
        pages = {
            "https://vendor.example/download": PRODUCT_HTML,
            "https://vendor.example/news": NEWS_HTML,
            "https://vendor.example/releases/1.2.3/windows/": LISTING_HTML,
        }

        def fake_fetch(url, timeout):
            return pages[url]

        result = collect_from_config(example_config(), fetcher=fake_fetch)
        self.assertEqual(result["product"], "Example App")
        self.assertEqual(result["version"], "1.2.3")
        self.assertEqual(result["release_date"], "2026-08-30")
        self.assertEqual(len(result["packages"]), 1)
        self.assertEqual(result["artifact_policy"], "metadata-only")

    def test_missing_release_date_is_explicitly_unknown(self):
        config = example_config()
        del config["release_date_source"]
        pages = {
            "https://vendor.example/download": PRODUCT_HTML,
            "https://vendor.example/releases/1.2.3/windows/": LISTING_HTML,
        }
        result = collect_from_config(config, fetcher=lambda url, timeout: pages[url])
        self.assertIsNone(result["release_date"])

    def test_append_only_deduplicates_unchanged_metadata(self):
        snapshot = {
            "product": "Example App",
            "slug": "example-app",
            "version": "1.2.3",
            "checked_at": "2026-09-01T23:00:00+09:00",
            "packages": [{"filename": "example.exe", "download_url": "https://vendor/a"}],
        }
        with tempfile.TemporaryDirectory() as td:
            first = append_snapshot(snapshot, Path(td))
            second = append_snapshot({**snapshot, "checked_at": "2026-09-02T23:00:00+09:00"}, Path(td))
            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.json_path, second.json_path)
            self.assertEqual(len(Path(td, "release-tracker.jsonl").read_text().splitlines()), 1)

    def test_changed_metadata_creates_revision_without_overwrite(self):
        snapshot = {
            "product": "Example App",
            "slug": "example-app",
            "version": "1.2.3",
            "checked_at": "2026-09-01T23:00:00+09:00",
            "packages": [{"filename": "example.exe", "download_url": "https://vendor/a"}],
        }
        with tempfile.TemporaryDirectory() as td:
            first = append_snapshot(snapshot, Path(td))
            changed = json.loads(json.dumps(snapshot))
            changed["checked_at"] = "2026-09-02T23:00:00+09:00"
            changed["packages"][0]["download_url"] = "https://vendor/b"
            second = append_snapshot(changed, Path(td))
            self.assertTrue(second.created)
            self.assertNotEqual(first.json_path, second.json_path)
            self.assertTrue(first.json_path.exists())


if __name__ == "__main__":
    unittest.main()
