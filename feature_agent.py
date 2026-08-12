import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests

from config import GITHUB_TOKEN, MOBILE_APP_REPOSITORY, OPENAI_API_KEY, is_placeholder
from openai_utils import OpenAIClient

ALLOWED_PREFIXES = ("app/src/",)
ALLOWED_FILES = {"app/build.gradle.kts", "README.md"}


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
        return {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + GITHUB_TOKEN, "X-GitHub-Api-Version": "2022-11-28"}

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

    def _apply_patch_with_repair(self, repo, env, patch, prompt, context, temp):
        patch_file = Path(temp) / "feature.patch"
        error = ""
        for attempt in range(2):
            patch_file.write_text(patch, encoding="utf-8")
            check = subprocess.run(
                ["git", "apply", "--check", str(patch_file)],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if check.returncode == 0:
                self._run(["git", "apply", "--index", str(patch_file)], repo, env)
                return
            error = check.stderr.strip() or check.stdout.strip() or "unknown git apply error"
            if attempt == 0:
                self.logger.warning("Generated patch failed validation; requesting one repair: %s", error)
                repair_prompt = (
                    "Your previous patch failed git apply validation with this error:\n" + error + "\n\n"
                    "Return a complete corrected unified diff only. Every changed file must include "
                    "a diff --git header, --- and +++ lines, and valid @@ hunk headers. "
                    "Do not include explanations or Markdown fences.\n\n"
                    "Original task:\n" + prompt + "\n\n"
                    "Previous invalid patch:\n" + patch + "\n\n"
                    "Repository context:\n" + context
                )
                patch = OpenAIClient().get_reply(repair_prompt)
                if not patch or patch == "Ignored":
                    break
        raise RuntimeError("Generated patch was invalid after repair attempt: " + error)

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
            context = self._context(repo)
            prompt = (
                "Implement this Android feature: " + request + "\n"
                "Return only a valid unified diff. Every changed file must have diff --git, --- , "
                "+++ , and valid @@ headers. Only edit app/src/ or app/build.gradle.kts. "
                "Never edit secrets, Gradle wrappers, settings, permissions, networking, or GitHub workflows. "
                "Do not delete existing functionality. Use existing Kotlin Jetpack Compose style.\n"
                "Repository context:" + context
            )
            patch = OpenAIClient().get_reply(prompt)
            if not patch or patch == "Ignored":
                raise RuntimeError("No usable patch was generated")
            self._apply_patch_with_repair(repo, env, patch, prompt, context, temp)
            changed = self._run(["git", "diff", "--cached", "--name-only"], repo, env).splitlines()
            forbidden = [name for name in changed if name not in ALLOWED_FILES and not name.startswith(ALLOWED_PREFIXES)]
            if not changed or forbidden:
                raise RuntimeError("Generated patch attempted forbidden changes")
            self._run(["git", "config", "user.name", "Telegram Feature Bot"], repo, env)
            self._run(["git", "config", "user.email", "telegram-feature-bot@users.noreply.github.com"], repo, env)
            self._run(["git", "commit", "-m", "Add requested Android feature"], repo, env)
            self._run(["git", "push", "origin", branch], repo, env)
        response = requests.post(
            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls",
            headers=self._headers(),
            json={"title": "Telegram feature: " + request[:72], "head": branch, "base": "master", "draft": True,
                  "body": "Generated from an authorized Telegram request. Review before sending approval."},
            timeout=20,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError("GitHub could not create the pull request")
        item = response.json()
        return item["number"], item["html_url"]

    def approve_and_merge(self, number):
        self._check_config()
        ready = requests.patch(
            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls/" + str(number),
            headers=self._headers(),
            json={"draft": False},
            timeout=20,
        )
        if ready.status_code != 200:
            raise RuntimeError("GitHub could not mark this PR ready for review")
        response = requests.put(
            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls/" + str(number) + "/merge",
            headers=self._headers(),
            json={"merge_method": "squash", "commit_title": "Merge Telegram feature PR #" + str(number)},
            timeout=20,
        )
        if response.status_code != 200 or not response.json().get("merged"):
            raise RuntimeError("GitHub did not merge this PR")
        return "https://github.com/" + MOBILE_APP_REPOSITORY + "/actions"
