from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import subprocess


@dataclass
class FirmwareRepoOptions:
    name: str
    path: str | None
    refresh: bool = False


def inspect_firmware_repo(options: FirmwareRepoOptions) -> dict[str, Any]:
    if not options.path:
        return {
            "name": options.name,
            "available": False,
            "status": "not_configured",
            "message": f"{options.name} path is not configured.",
        }

    root = Path(options.path).expanduser()
    if not root.exists():
        return {
            "name": options.name,
            "available": False,
            "status": "missing_path",
            "path": str(root),
            "message": f"{options.name} path does not exist: {root}",
        }

    if not (root / ".git").exists():
        return {
            "name": options.name,
            "available": False,
            "status": "not_git_repo",
            "path": str(root),
            "message": f"{options.name} path is not a git repository: {root}",
        }

    fetch_result = None
    if options.refresh:
        fetch_result = run_git(root, ["fetch", "--tags", "--prune"])

    describe = run_git(root, ["describe", "--tags", "--always", "--dirty"])
    branch = run_git(root, ["branch", "--show-current"])
    head = run_git(root, ["rev-parse", "--short=12", "HEAD"])
    full_head = run_git(root, ["rev-parse", "HEAD"])
    remote_url = run_git(root, ["config", "--get", "remote.origin.url"])
    upstream = run_git(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    dirty = run_git(root, ["status", "--porcelain"])
    tracked_dirty = run_git(root, ["status", "--porcelain", "--untracked-files=no"])

    update_info = update_status(root, upstream.stdout.strip() if upstream.ok else None)
    version_options = firmware_version_options(
        root=root,
        current_version=describe.stdout.strip() if describe.ok else None,
        current_head=head.stdout.strip() if head.ok else None,
        branch=branch.stdout.strip() if branch.ok else None,
        upstream=upstream.stdout.strip() if upstream.ok else None,
    )

    return {
        "name": options.name,
        "available": True,
        "status": "ok",
        "path": str(root),
        "project": detect_project(options.name, remote_url.stdout, describe.stdout),
        "version": describe.stdout.strip() if describe.ok else None,
        "branch": branch.stdout.strip() if branch.ok else None,
        "head": head.stdout.strip() if head.ok else None,
        "full_head": full_head.stdout.strip() if full_head.ok else None,
        "remote": remote_url.stdout.strip() if remote_url.ok else None,
        "upstream": upstream.stdout.strip() if upstream.ok else None,
        "dirty": bool(dirty.stdout.strip()) if dirty.ok else None,
        "tracked_dirty": bool(tracked_dirty.stdout.strip()) if tracked_dirty.ok else None,
        "version_options": version_options,
        "update_check": {
            **update_info,
            "refreshed": options.refresh,
            "refresh_status": fetch_result.returncode if fetch_result else None,
            "refresh_error": fetch_result.stderr.strip() if fetch_result and not fetch_result.ok else None,
        },
    }


def inspect_firmware_sources(
    klipper_path: str | None,
    kalico_path: str | None,
    refresh: bool = False,
) -> dict[str, Any]:
    sources = [
        inspect_firmware_repo(FirmwareRepoOptions("klipper", klipper_path, refresh)),
        inspect_firmware_repo(FirmwareRepoOptions("kalico", kalico_path, refresh)),
    ]
    available = [source for source in sources if source.get("available")]
    active = available[0] if available else None

    return {
        "sources": sources,
        "active_source": active.get("name") if active else None,
        "default_project": active.get("project", active["name"]) if active else None,
        "version_options": active.get("version_options", []) if active else [],
        "update_available": any(
            source.get("update_check", {}).get("update_available") is True for source in available
        ),
    }


def switch_firmware_ref(
    *,
    firmware_ref: str,
    klipper_path: str | None,
    kalico_path: str | None,
    allow_dirty: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    sources = inspect_firmware_sources(klipper_path=klipper_path, kalico_path=kalico_path, refresh=True)
    active_name = sources.get("active_source")
    active = None
    for source in sources.get("sources", []):
        if source.get("name") == active_name and source.get("available"):
            active = source
            break

    if not active:
        raise ValueError("No available Klipper/Kalico git repository was found.")

    root = Path(str(active["path"])).expanduser()
    target = resolve_checkout_ref(active, firmware_ref)
    dirty = bool(active.get("dirty"))
    tracked_dirty = bool(active.get("tracked_dirty"))
    result: dict[str, Any] = {
        "action": "switch_firmware_ref",
        "execute": execute,
        "status": "planned",
        "project": active.get("project"),
        "repository": {
            "name": active.get("name"),
            "path": str(root),
            "current_version": active.get("version"),
            "current_head": active.get("head"),
            "dirty": dirty,
            "tracked_dirty": tracked_dirty,
        },
        "target": target,
        "commands_preview": [f"git -C {root} checkout {target['checkout_ref']}"],
    }

    if tracked_dirty and not allow_dirty:
        result["status"] = "blocked"
        result["message"] = "Repository has modified tracked files. Commit, stash, or allow dirty checkout before switching versions."
        return result

    if not execute:
        return result

    checkout = run_git(root, ["checkout", str(target["checkout_ref"])])
    if not checkout.ok:
        result["status"] = "failed"
        result["error"] = checkout.stderr.strip() or checkout.stdout.strip()
        return result

    refreshed = inspect_firmware_repo(FirmwareRepoOptions(str(active["name"]), str(root), refresh=False))
    result["status"] = "ok"
    result["repository_after"] = {
        "version": refreshed.get("version"),
        "head": refreshed.get("head"),
        "full_head": refreshed.get("full_head"),
        "branch": refreshed.get("branch"),
        "dirty": refreshed.get("dirty"),
    }
    return result


def resolve_checkout_ref(active: dict[str, Any], firmware_ref: str) -> dict[str, Any]:
    if firmware_ref == "previous_artifact":
        raise ValueError("previous_artifact is a firmware artifact selection, not a git repository version.")

    if firmware_ref == "current":
        return {
            "kind": "current",
            "requested": firmware_ref,
            "checkout_ref": active.get("full_head") or active.get("head") or "HEAD",
        }

    if firmware_ref == "latest":
        upstream = active.get("upstream")
        if not upstream:
            raise ValueError("Cannot switch to latest because this repository has no upstream tracking branch.")
        return {
            "kind": "latest",
            "requested": firmware_ref,
            "checkout_ref": upstream,
        }

    for option in active.get("version_options", []):
        if option.get("value") == firmware_ref:
            return {
                "kind": option.get("kind"),
                "requested": firmware_ref,
                "checkout_ref": option.get("ref") or option.get("value"),
                "label": option.get("label"),
            }

    return {
        "kind": "ref",
        "requested": firmware_ref,
        "checkout_ref": firmware_ref,
    }


def firmware_version_options(
    root: Path,
    current_version: str | None,
    current_head: str | None,
    branch: str | None,
    upstream: str | None,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    options.append(
        {
            "kind": "current",
            "value": "current",
            "label": f"Current ({current_version or current_head or 'unknown'})",
            "ref": current_head,
        }
    )

    if upstream:
        options.append(
            {
                "kind": "latest",
                "value": "latest",
                "label": f"Latest from {upstream}",
                "ref": upstream,
            }
        )

    if branch:
        options.append(
            {
                "kind": "branch",
                "value": branch,
                "label": f"Branch {branch}",
                "ref": branch,
            }
        )

    for tag in list_recent_tags(root):
        options.append(
            {
                "kind": "tag",
                "value": tag,
                "label": tag,
                "ref": tag,
            }
        )

    options.append(
        {
            "kind": "previous_artifact",
            "value": "previous_artifact",
            "label": "Previous built firmware artifact",
            "ref": None,
            "available": False,
        }
    )
    return dedupe_version_options(options)


def list_recent_tags(root: Path, limit: int = 20) -> list[str]:
    result = run_git(
        root,
        [
            "for-each-ref",
            "--sort=-creatordate",
            f"--count={limit}",
            "--format=%(refname:short)",
            "refs/tags",
        ],
    )
    if not result.ok:
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def dedupe_version_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for option in options:
        key = (str(option.get("kind")), str(option.get("value")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)

    return deduped


def update_status(root: Path, upstream: str | None) -> dict[str, Any]:
    if not upstream:
        return {
            "available": False,
            "status": "no_upstream",
            "message": "No upstream tracking branch is configured.",
        }

    result = run_git(root, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
    if not result.ok:
        return {
            "available": False,
            "status": "failed",
            "message": result.stderr.strip() or result.stdout.strip(),
        }

    parts = result.stdout.strip().split()
    ahead = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    behind = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    return {
        "available": True,
        "status": "ok",
        "ahead": ahead,
        "behind": behind,
        "update_available": behind > 0,
    }


def detect_project(name: str, remote: str, describe: str) -> str:
    text = f"{name} {remote} {describe}".lower()
    if "kalico" in text:
        return "kalico"
    if "klipper" in text:
        return "klipper"
    return name


def run_git(root: Path, args: list[str]) -> "GitResult":
    result = subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return GitResult(result.returncode, result.stdout, result.stderr)


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0
