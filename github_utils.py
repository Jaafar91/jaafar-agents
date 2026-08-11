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

    with open(os.path.join(repo_dir, file_path), "a", encoding="utf-8") as f:
        f.write(content)

    run_git_command(["git", "config", "user.name", commit_name], repo_dir)
    run_git_command(["git", "config", "user.email", commit_email], repo_dir)
    run_git_command(["git", "add", file_path], repo_dir)
    commit_message = _summarize_change(content, content)
    run_git_command(["git", "commit", "-m", commit_message], repo_dir)
    run_git_command(["git", "push", "origin", branch], repo_dir)

    return "https://github.com/" + repo_url.split("github.com/")[-1].replace(".git", "") + "/commit/HEAD"
