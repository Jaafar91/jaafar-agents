import re
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL


class OpenAIClient:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

    def get_reply(self, prompt):
        if not self.client:
            raise RuntimeError("OpenAI API key is not configured")

        instruction = (
            "Return unified diff only. No markdown fences. No explanations. "
            "The diff must be applicable with `git apply`. "
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
        return response.output_text

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
