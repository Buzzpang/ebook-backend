import os
import logging
from dotenv import load_dotenv
from openai import OpenAI
from typing import Optional, Dict, Any, List, Callable

# ---------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------
load_dotenv()


# ---------------------------------------------------------
# Initialize logging
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("GPTService")


# ---------------------------------------------------------
# Model priorities (modify anytime)
# ---------------------------------------------------------
PRIMARY_MODEL = "gpt-5.1"
FALLBACK_MODELS = [
    "gpt-4.1",
    "gpt-4.1-mini"
]


# ---------------------------------------------------------
# GPT Service Class
# ---------------------------------------------------------
class GPTService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing from .env")

        self.client = OpenAI(api_key=api_key)

    # -----------------------------------------------------
    # CORE CALLER (with auto fallback)
    # -----------------------------------------------------
    def _execute(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        *,
        functions: Optional[List[Dict[str, Any]]] = None,
        function_call: Optional[str] = None,
        stream: bool = False,
    ):
        try:
            logger.info(f"Calling OpenAI model: {model}")

            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                functions=functions,
                function_call=function_call,
                stream=stream
            )
            return response

        except Exception as e:
            logger.error(f"Model {model} failed: {str(e)}")
            return None

    # -----------------------------------------------------
    # PUBLIC: Core chat method with fallback sequence
    # -----------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        functions: Optional[List[Dict[str, Any]]] = None,
        function_call: Optional[str] = None,
        stream: bool = False,
        return_usage: bool = True
    ):
        # Try primary model first
        models_to_try = [PRIMARY_MODEL] + FALLBACK_MODELS

        for model in models_to_try:
            response = self._execute(
                model=model,
                messages=messages,
                functions=functions,
                function_call=function_call,
                stream=stream
            )

            # Streaming is a generator -> immediately return it
            if stream and response:
                return response

            # Normal (non-streaming) response
            if response:
                try:
                    content = response.choices[0].message.content
                except Exception:
                    content = None

                result = {
                    "content": content,
                    "model_used": model
                }

                if return_usage:
                    try:
                        result["usage"] = {
                            "prompt_tokens": response.usage.prompt_tokens,
                            "completion_tokens": response.usage.completion_tokens,
                            "total_tokens": response.usage.total_tokens
                        }
                    except Exception:
                        result["usage"] = None

                return result

        # If nothing worked:
        raise RuntimeError("All OpenAI models failed — check logs.")

    # -----------------------------------------------------
    # SIMPLE HELPER FOR "just text" prompts
    # -----------------------------------------------------
    def ask(self, prompt: str) -> str:
        messages = [
            {"role": "user", "content": prompt}
        ]

        response = self.chat(messages)
        return response["content"]


# ---------------------------------------------------------
# Singleton instance for import
# ---------------------------------------------------------
gpt_service = GPTService()
