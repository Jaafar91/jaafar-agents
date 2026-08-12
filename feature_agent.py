import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from config import GITHUB_TOKEN, MOBILE_APP_REPOSITORY, OPENAI_API_KEY, is_placeholder
from openai_utils import OpenAIClient

ALLOWED_PREFIXES = ("app/src/",)
ALLOWED_FILES = {"app/build.gradle.kts", "README.md"}
MIN_CHANGED_LINES = 5


class FeatureAgent:
    def __init__(self, logger):
        self.logger = logger

    def _check_config(self):
        if is_placeholder(GITHUB_TOKEN) or is_placeholder(OPENAI_API_KEY):
            raise RuntimeError("OpenAI or GitHub credentials are missing")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", MOBILE_APP_REPOSITORY or ""):
            raise RuntimeError("MOBILE_APP_REPOSITORY must be owner/repository")

    @staticmethod
    def _run(command, cwd, env):
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Git command failed")
        return result.stdout

    def _git_env(self, temp_dir):
        script = Path(temp_dir) / "git_askpass.sh"
        script.write_text("#!/bin/sh\ncase \"$1\" in\n*Username*) echo x-access-token ;;\n*) echo \"$GITHUB_TOKEN\" ;;\nesac\n", encoding="utf-8")
        script.chmod(0o700)
        env = os.environ.copy()
        env["GIT_ASKPASS"] = str(script)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GITHUB_TOKEN"] = GITHUB_TOKEN
        return env

    @staticmethod
    def _headers():
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + GITHUB_TOKEN,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _context(self, repo):
        chunks, total = [], 0
        for path in sorted(Path(repo).rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            name = path.relative_to(repo).as_posix()
            if not (name.startswith("app/src/") or name in {"app/build.gradle.kts", "build.gradle.kts", "settings.gradle.kts"}):
                continue
            if path.stat().st_size > 30000:
                continue
            text = "\n--- " + name + " ---\n" + path.read_text(encoding="utf-8", errors="ignore")
            if total + len(text) > 80000:
                break
            chunks.append(text)
            total += len(text)
        return "".join(chunks)

    @staticmethod
    def _safe_path(repo, relative):
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RuntimeError("Generated an unsafe file path")
        normalized = candidate.as_posix()
        if normalized not in ALLOWED_FILES and not normalized.startswith(ALLOWED_PREFIXES):
            raise RuntimeError("Generated a forbidden file change: " + normalized)
        target = (Path(repo) / candidate).resolve()
        if Path(repo).resolve() not in target.parents:
            raise RuntimeError("Generated an unsafe file path")
        return target, normalized

    def _write_changes(self, repo, changes):
        if not 1 <= len(changes) <= 5:
            raise RuntimeError("Generator must return between one and five files")
        seen = set()
        for change in changes:
            if not isinstance(change, dict):
                raise RuntimeError("Generator returned an invalid file change")
            path = change.get("path")
            content = change.get("content")
            if not isinstance(path, str) or not isinstance(content, str) or len(content) > 100000:
                raise RuntimeError("Generator returned invalid file content")
            target, normalized = self._safe_path(repo, path)
            if normalized in seen:
                raise RuntimeError("Generator returned the same file twice")
            seen.add(normalized)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content.rstrip() + "\n", encoding="utf-8")

    def _validate_changes(self, repo, env):
        self._run(["git", "add", "--all"], repo, env)
        self._run(["git", "diff", "--cached", "--check"], repo, env)
        changed = self._run(["git", "diff", "--cached", "--name-only"], repo, env).splitlines()
        if not changed:
            raise RuntimeError("Generated feature did not change any files")
        for path in changed:
            self._safe_path(repo, path)
        stats = self._run(["git", "diff", "--cached", "--numstat"], repo, env).splitlines()
        changed_lines = sum(int(item.split("\t")[0]) + int(item.split("\t")[1]) for item in stats if item and item.split("\t")[0].isdigit())
        if changed_lines < MIN_CHANGED_LINES:
            raise RuntimeError("Generated feature was too small to be a complete implementation")

    def create_draft_pr(self, request):
        self._check_config()
        request = request.strip()
        if not 8 <= len(request) <= 1000:
            raise RuntimeError("Feature request must be between 8 and 1000 characters")
        slug = re.sub(r"[^a-z0-9]+", "-", request.lower()).strip("-")[:36]
        branch = "telegram/feature-" + slug + "-" + os.urandom(3).hex()

        with tempfile.TemporaryDirectory(prefix="telegram-feature-") as temp:
            env = self._git_env(temp)
            repo = Path(temp) / "android-app"
            self._run(["git", "clone", "--depth", "1", "https://github.com/" + MOBILE_APP_REPOSITORY + ".git", str(repo)], temp, env)
            self._run(["git", "checkout", "-b", branch], repo, env)
            prompt = (
                "Implement this Android feature request: " + request + "\n\n"
                "Return JSON only with full replacement content for every changed source file. "
                "Make a complete working implementation, not imports or placeholders. "
                "Only change app/src/ or app/build.gradle.kts. Never change workflow files, secrets, permissions, networking, Gradle wrappers, or settings. "
                "Do not delete functionality. Preserve the existing Kotlin and Jetpack Compose style.\n"
                "Repository context:" + self._context(repo)
            )
            changes = OpenAIClient().get_file_changes(prompt)
            self._write_changes(repo, changes)
            self._validate_changes(repo, env)
            self._run(["git", "config", "user.name", "Telegram Feature Bot"], repo, env)
            self._run(["git", "config", "user.email", "telegram-feature-bot@users.noreply.github.com"], repo, env)
            self._run(["git", "commit", "-m", "Add requested Android feature"], repo, env)
            self._run(["git", "push", "origin", branch], repo, env)

        response = requests.post(
            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls",
            headers=self._headers(),
            json={
                "title": "Telegram feature: " + request[:72],
                "head": branch,
                "base": "master",
                "draft": True,
                "body": "Generated from an authorized Telegram request. Review before approval.",
            },
            timeout=20,
        )
        if response.status_code not in {200, 201}:
            self.logger.error("GitHub pull request creation failed: %s %s", response.status_code, response.text)
            raise RuntimeError("GitHub could not create the pull request (" + str(response.status_code) + ")")
        item = response.json()
        return item["number"], item["html_url"]

    def approve_after_successful_build(self, number):
        self._check_config()
        ready = requests.patch(
            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls/" + str(number),
            headers=self._headers(),
            json={"draft": False},
            timeout=20,
        )
        if ready.status_code != 200:
            raise RuntimeError("GitHub could not mark this PR ready for review")

        pull = requests.get(
            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls/" + str(number),
            headers=self._headers(),
            timeout=20,
        )
        if pull.status_code != 200:
            raise RuntimeError("GitHub could not read this PR")
        sha = pull.json()["head"]["sha"]

        for _ in range(45):
            runs = requests.get(
                "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/actions/runs?head_sha=" + sha,
                headers=self._headers(),
                timeout=20,
            )
            if runs.status_code == 200:
                matching = [
                    run for run in runs.json().get("workflow_runs", [])
                    if run.get("name") == "Android APK and Firebase distribution"
                ]
                if matching:
                    run = matching[0]
                    if run.get("status") == "completed":
                        if run.get("conclusion") != "success":
                            raise RuntimeError("Android validation failed: " + str(run.get("html_url")))
                        merge = requests.put(
                            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls/" + str(number) + "/merge",
                            headers=self._headers(),
                            json={"merge_method": "squash", "commit_title": "Merge Telegram feature PR #" + str(number)},
                            timeout=20,
                        )
                        if merge.status_code != 200 or not merge.json().get("merged"):
                            raise RuntimeError("Build passed, but GitHub did not merge this PR")
                        return run.get("html_url") or "https://github.com/" + MOBILE_APP_REPOSITORY + "/actions"
            time.sleep(20)
        raise RuntimeError("Timed out waiting for the Android validation build")
