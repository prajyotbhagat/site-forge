"""Hosting adapter interface + the managed (local filesystem) implementation.

Other providers (Vercel, Netlify, S3+CloudFront, a customer's own account) implement the same
interface, so the deployment workflow doesn't change when a provider is added.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from .. import config
from ..sitegen.generator import checksum


class HostingAdapter(Protocol):
    name: str

    def upload(self, slug: str, release_id: str, build_dir: Path) -> None: ...
    def release_path(self, slug: str, release_id: str) -> Path: ...
    def health_check(self, slug: str, release_id: str, expected_checksum: str, pages: list[str]) -> dict: ...
    def delete_site(self, slug: str) -> None: ...


class ManagedHosting:
    name = "siteforge-managed"

    def _root(self, slug: str) -> Path:
        return config.settings.hosting_dir / slug / "releases"

    def release_path(self, slug: str, release_id: str) -> Path:
        return self._root(slug) / release_id

    def upload(self, slug: str, release_id: str, build_dir: Path) -> None:
        dst = self.release_path(slug, release_id)
        if dst.exists():  # idempotent re-upload
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(build_dir, dst)

    def health_check(self, slug: str, release_id: str, expected_checksum: str, pages: list[str]) -> dict:
        path = self.release_path(slug, release_id)
        checks = []
        exists = path.exists()
        checks.append({"check": "release-present", "passed": exists})
        if exists:
            actual = checksum(path)
            checks.append({"check": "checksum-matches-approved-build", "passed": actual == expected_checksum,
                           "detail": actual[:12]})
            for p in pages:
                f = path / p
                checks.append({"check": f"page-{p}", "passed": f.exists() and f.stat().st_size > 200})
        return {"healthy": all(c["passed"] for c in checks), "checks": checks}

    def delete_site(self, slug: str) -> None:
        shutil.rmtree(config.settings.hosting_dir / slug, ignore_errors=True)


hosting: HostingAdapter = ManagedHosting()
