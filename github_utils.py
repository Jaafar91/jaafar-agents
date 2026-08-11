import os
import re
import subprocess
from urllib.parse import urlparse, urlunparse


def run_git_command(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def build_repo_remote_url(repo_url, token=None):
    if not token or not repo_url.startswith("https://"):
        return repo_url

    parsed = urlparse(repo_url)
    if parsed.netloc.startswith("github.com"):
        netloc = f"x-access-token:{token}@github.com"
    else:
        netloc = f"x-access-token:{token}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def _summarize_change(prompt, code_content):
    prompt_text = (prompt or "").strip()
    code_text = (code_content or "").strip()
    if code_text:
        if "def " in code_text:
            return "Add generated Python function"
        if "class " in code_text:
            return "Add generated class"
        if "import " in code_text or "from " in code_text:
            return "Add generated code snippet"
        return "Add generated code update"

    if prompt_text:
        words = re.findall(r"\b\w+\b", prompt_text.lower())
        if words:
            topic = words[0]
            return f"Update based on: {topic}"

    return "Update from OpenAI bot"


def _resolve_repo_path(repo_dir, file_path):
    normalized = os.path.normpath(file_path).replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in {"", "."}:
        return None

    target_path = os.path.join(repo_dir, normalized)
    if os.path.exists(target_path):
        return normalized

    repo_root = repo_dir
    entries = []
    for entry in os.listdir(repo_root):
        if entry.lower() == normalized.lower():
            return entry
        if normalized.lower().endswith(entry.lower()):
            entries.append(entry)
    if len(entries) == 1:
        return entries[0]
    return normalized


def _normalize_git_patch(patch_text):
    if not patch_text:
        return ""

    normalized = patch_text.strip()

    def replace_header(match):
        return f"{match.group(1)}{match.group(2)},{match.group(2)}{match.group(3)}"

    normalized = re.sub(r"(@@ -\d+(?:,\d+)? \+)(\d+)( @@)", replace_header, normalized)
    return normalized


def _extract_new_file_target(patch_text):
    if not patch_text:
        return None

    lines = [line.strip() for line in patch_text.splitlines() if line.strip()]
    if len(lines) < 5:
        return None

    if not lines[0].startswith("diff --git"):
        return None

    try:
        target = lines[0].split(" a/", 1)[1].split(" b/", 1)[0]
    except IndexError:
        return None

    if target.startswith("/"):
        return None

    return target


def create_commit_and_push(repo_dir, repo_url, token, branch, commit_name, commit_email, file_path, content, logger):
    remote_url = build_repo_remote_url(repo_url, token)
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        logger.info("Cloning repository into %s", repo_dir)
        run_git_command(["git", "clone", remote_url, repo_dir], os.getcwd())
    else:
        logger.info("Updating remote URL for repository in %s", repo_dir)
        run_git_command(["git", "remote", "set-url", "origin", remote_url], repo_dir)

    try:
        logger.info("Checking out branch %s", branch)
        run_git_command(["git", "checkout", branch], repo_dir)
    except RuntimeError:
        logger.warning("Branch %s does not exist, creating it", branch)
        run_git_command(["git", "checkout", "-b", branch], repo_dir)

    if content and content.strip():
        normalized_content = _normalize_git_patch(content)
        patch_path = os.path.join(repo_dir, "pending.patch")
        with open(patch_path, "w", encoding="utf-8") as patch_file:
            patch_file.write(normalized_content)

        try:
            run_git_command(["git", "apply", "--index", patch_path], repo_dir)
        except RuntimeError as exc:
            logger.warning("git apply failed: %s", exc)
            if "No valid patches in input" in str(exc):
                logger.info("No valid patch content received; skipping repository patch application")
                return None
            target = _extract_new_file_target(normalized_content)
            if target:
                logger.info("Falling back to direct file write for %s", target)
                target_path = os.path.join(repo_dir, target)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                file_lines = []
                for line in normalized_content.splitlines():
                    if line.startswith("+") and not line.startswith("+++ "):
                        file_lines.append(line[1:])
                if file_lines:
                    with open(target_path, "w", encoding="utf-8") as target_file:
                        target_file.write("\n".join(file_lines) + "\n")
                else:
                    with open(target_path, "w", encoding="utf-8") as target_file:
                        target_file.write("")
            else:
                raise
        finally:
            if os.path.exists(patch_path):
                os.remove(patch_path)

    if not content or not content.strip():
        logger.info("No repository patch content received; skipping commit and push")
        return None

    resolved_target = _resolve_repo_path(repo_dir, file_path)
    if not resolved_target:
        resolved_target = file_path

    run_git_command(["git", "config", "user.name", commit_name], repo_dir)
    run_git_command(["git", "config", "user.email", commit_email], repo_dir)
    run_git_command(["git", "add", resolved_target], repo_dir)
    commit_message = _summarize_change(content, content)
    run_git_command(["git", "commit", "-m", commit_message], repo_dir)
    run_git_command(["git", "push", "origin", branch], repo_dir)

    return "https://github.com/" + repo_url.split("github.com/")[-1].replace(".git", "") + "/commit/HEAD"


def delete_file_and_push(repo_dir, repo_url, token, branch, commit_name, commit_email, file_path, logger):
    remote_url = build_repo_remote_url(repo_url, token)
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        logger.info("Cloning repository into %s", repo_dir)
        run_git_command(["git", "clone", remote_url, repo_dir], os.getcwd())
    else:
        logger.info("Updating remote URL for repository in %s", repo_dir)
        run_git_command(["git", "remote", "set-url", "origin", remote_url], repo_dir)

    try:
        logger.info("Checking out branch %s", branch)
        run_git_command(["git", "checkout", branch], repo_dir)
    except RuntimeError:
        logger.warning("Branch %s does not exist, creating it", branch)
        run_git_command(["git", "checkout", "-b", branch], repo_dir)

    resolved_path = _resolve_repo_path(repo_dir, file_path)
    if not resolved_path:
        raise RuntimeError("No file path provided for deletion")

    absolute_path = os.path.join(repo_dir, resolved_path)
    if not os.path.exists(absolute_path):
        raise RuntimeError(f"File not found: {resolved_path}")

    run_git_command(["git", "rm", "--ignore-unmatch", resolved_path], repo_dir)
    run_git_command(["git", "config", "user.name", commit_name], repo_dir)
    run_git_command(["git", "config", "user.email", commit_email], repo_dir)
    run_git_command(["git", "commit", "-m", f"Delete {resolved_path}"], repo_dir)
    run_git_command(["git", "push", "origin", branch], repo_dir)

    return "https://github.com/" + repo_url.split("github.com/")[-1].replace(".git", "") + "/commit/HEAD"
