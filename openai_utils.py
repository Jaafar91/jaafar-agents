import re
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL


class OpenAIClient:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

    def _normalize_patch_reply(self, reply):
        if not reply:
            return ""

        cleaned = reply.strip()
        if not cleaned:
            return ""

        if cleaned.lower() == "ignored":
            return "Ignored"

        fenced = re.fullmatch(r"```(?:diff|text)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if fenced:
            cleaned = fenced.group(1).strip()

        if cleaned.lower().startswith("ignored"):
            return "Ignored"

        lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return ""

        if lines[0].lower().startswith("new file "):
            target = lines[0].split(" ", 1)[1].strip()
            body_lines = lines[1:]
            additions = []
            for line in body_lines:
                if line.startswith("+") and not line.startswith("+++ "):
                    additions.append(line)
                elif line.startswith("-"):
                    additions.append(line)
                else:
                    additions.append(f"+{line}")

            patch_lines = [
                f"diff --git a/{target} b/{target}",
                "new file mode 100644",
                "--- /dev/null",
                f"+++ b/{target}",
                f"@@ -0,0 +{len(additions)} @@",
            ]
            patch_lines.extend(additions)
            return "\n".join(patch_lines)

        return cleaned

    def get_reply(self, prompt):
        if not self.client:
            raise RuntimeError("OpenAI API key is not configured")

        instruction = (
            "Return a valid unified diff only. No markdown fences. No explanations. "
            "The diff must be directly applicable with `git apply`. "
            "Use standard unified diff format with `diff --git`, `---`, `+++`, and `@@ ... @@` headers. "
            "For new files, use `--- /dev/null` and `+++ b/<file>` with a proper hunk header such as `@@ -0,0 +1 @@` followed by `+<line>`. "
            "For existing files, include the correct old/new file headers and hunks. "
            "If the request is to delete, remove, erase, or clear content, respond with the single word: Ignored."
        )
        response = self.client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant for a Telegram bot that can also help manage a GitHub repository.",
                },
                {"role": "user", "content": f"{instruction}\n\n{prompt}"},
            ],
        )
        return self._normalize_patch_reply(response.output_text)

    def extract_code_from_reply(self, reply):
        code_blocks = re.findall(r"```(?:\w+)?\s*(.*?)```", reply, re.DOTALL)
        if code_blocks:
            return "\n\n".join(block.strip() for block in code_blocks if block.strip())

        lines = [line.strip() for line in reply.splitlines() if line.strip()]
        if not lines:
            return ""

        code_like_lines = []
        for line in lines:
            if any(token in line for token in ["def ", "class ", "import ", "from ", "return ", "if ", "for ", "while ", "print(", "#"]):
                code_like_lines.append(line)
        if code_like_lines:
            return "\n".join(code_like_lines)

        return ""
