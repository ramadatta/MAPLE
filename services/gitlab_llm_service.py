"""GitLab Duo LLM service via Personal Access Token and AI Gateway proxy."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional, Type, TypeVar

import requests
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from services.llm_service import (
    LLMServiceBase,
    SCIENTIFIC_SYSTEM_INSTRUCTION,
    fallback_instance,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_GITLAB_HOST = "https://gitlab.com"
DEFAULT_AI_GATEWAY = "https://cloud.gitlab.com"
DEFAULT_OPENAI_PROXY = f"{DEFAULT_AI_GATEWAY}/ai/v1/proxy/openai/v1"
DIRECT_ACCESS_TTL_SECONDS = 25 * 60


class GitLabLLMService(LLMServiceBase):
    """
    LLM backend using a GitLab Personal Access Token (glpat-...).

    Primary path: exchange PAT for a short-lived AI Gateway token via
    ``POST /api/v4/ai/third_party_agents/direct_access``, then call the
    OpenAI-compatible proxy at cloud.gitlab.com.

    Fallback: GitLab Duo Chat REST API ``POST /api/v4/chat/completions``.
    """

    def __init__(
        self,
        api_key: str,
        host: Optional[str] = None,
        model: Optional[str] = None,
        ai_gateway_url: Optional[str] = None,
    ):
        self.pat = api_key
        self.host = (host or os.getenv("GITLAB_HOST", DEFAULT_GITLAB_HOST)).rstrip("/")
        self.model = model or os.getenv("GITLAB_MODEL", "gpt-5-mini-2025-08-07")
        self.ai_gateway_url = (
            ai_gateway_url or os.getenv("GITLAB_AI_GATEWAY_URL", DEFAULT_OPENAI_PROXY)
        ).rstrip("/")
        self._direct_access: Optional[dict] = None
        self._direct_access_expires = 0.0
        self._openai_client: Optional[OpenAI] = None
        self._use_chat_fallback = False
        self._auth_failed = False

    def _disable_auth(self, reason: str) -> None:
        """Stop further GitLab API calls after auth failure."""
        if not self._auth_failed:
            logger.warning("GitLab LLM disabled: %s", reason)
        self._auth_failed = True
        self._use_chat_fallback = True
        self._openai_client = None

    @staticmethod
    def is_gitlab_token(token: str) -> bool:
        """Return True if token looks like a GitLab PAT."""
        return token.startswith(("glpat-", "glptt-", "glpat_"))

    @staticmethod
    def _auth_header_variants(token: str) -> list[dict[str, str]]:
        """GitLab accepts Bearer or PRIVATE-TOKEN depending on endpoint/version."""
        return [
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            {"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
        ]

    @classmethod
    def verify_pat(cls, token: str, host: Optional[str] = None) -> tuple[bool, str]:
        """
        Validate PAT against GitLab /user and AI direct_access.

        Returns (ok, message).
        """
        host = (host or os.getenv("GITLAB_HOST", DEFAULT_GITLAB_HOST)).rstrip("/")
        user_url = f"{host}/api/v4/user"
        last_error = "Unknown authentication error"

        for headers in cls._auth_header_variants(token):
            try:
                resp = requests.get(user_url, headers=headers, timeout=20)
                if resp.status_code == 401:
                    last_error = "Token rejected (401 Unauthorized)"
                    continue
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
        else:
            return (
                False,
                f"{last_error}. Create a new PAT at {host}/-/user_settings/personal_access_tokens "
                "with **api** and **ai_features** scopes (or use OPENAI_API_KEY instead).",
            )

        direct_url = f"{host}/api/v4/ai/third_party_agents/direct_access"
        for headers in cls._auth_header_variants(token):
            try:
                resp = requests.post(
                    direct_url,
                    headers=headers,
                    json={"feature_flags": {"DuoAgentPlatformNext": True}},
                    timeout=30,
                )
                if resp.status_code == 401:
                    last_error = "AI direct_access rejected token (401)"
                    continue
                if resp.status_code == 403:
                    return (
                        False,
                        "GitLab Duo is not enabled for your account or token lacks **ai_features** "
                        "scope. Enable Duo in your GitLab plan and recreate the PAT.",
                    )
                resp.raise_for_status()
                return True, "GitLab Duo token verified."
            except requests.RequestException as exc:
                last_error = str(exc)
                continue

        return (
            False,
            f"{last_error}. Ensure PAT has **api** + **ai_features** scopes and GitLab Duo is enabled.",
        )

    def _gitlab_post(self, url: str, json_body: Optional[dict] = None) -> requests.Response:
        """POST with Bearer/PRIVATE-TOKEN fallback on 401."""
        last_resp: Optional[requests.Response] = None
        for headers in self._auth_header_variants(self.pat):
            resp = requests.post(url, headers=headers, json=json_body or {}, timeout=120)
            if resp.status_code != 401:
                return resp
            last_resp = resp
        if last_resp is None:
            raise RuntimeError("No response received from GitLab API after all auth variants")
        return last_resp

    def _gitlab_get_direct_access(self) -> requests.Response:
        """POST direct_access with Bearer/PRIVATE-TOKEN fallback on 401."""
        url = f"{self.host}/api/v4/ai/third_party_agents/direct_access"
        return self._gitlab_post(
            url,
            json_body={"feature_flags": {"DuoAgentPlatformNext": True}},
        )

    def _get_direct_access(self) -> dict:
        """Fetch or return cached AI Gateway direct-access credentials."""
        now = time.time()
        if self._direct_access and self._direct_access_expires > now:
            return self._direct_access

        resp = self._gitlab_get_direct_access()
        if resp.status_code == 401:
            raise PermissionError(
                "GitLab token unauthorized (401). Recreate your PAT with **api** and "
                "**ai_features** scopes at "
                f"{self.host}/-/user_settings/personal_access_tokens"
            )
        if resp.status_code == 403:
            raise PermissionError(
                "GitLab Duo access denied. Ensure your account has GitLab Duo enabled "
                "and your token has the ai_features scope."
            )
        resp.raise_for_status()
        data = resp.json()
        self._direct_access = {
            "token": data["token"],
            "headers": data.get("headers", {}),
        }
        self._direct_access_expires = now + DIRECT_ACCESS_TTL_SECONDS
        return self._direct_access

    def _get_openai_client(self) -> OpenAI:
        """Build OpenAI client pointed at GitLab AI Gateway proxy."""
        if self._openai_client is not None:
            return self._openai_client

        direct = self._get_direct_access()
        headers = dict(direct["headers"])
        headers["Authorization"] = f"Bearer {direct['token']}"

        self._openai_client = OpenAI(
            api_key="gitlab-duo",
            base_url=self.ai_gateway_url,
            default_headers=headers,
        )
        return self._openai_client

    def _chat_completions_fallback(self, content: str) -> str:
        """Fallback to GitLab Duo Chat REST API."""
        url = f"{self.host}/api/v4/chat/completions"
        resp = self._gitlab_post(url, json_body={"content": content, "with_clean_history": True})
        if resp.status_code == 401:
            raise PermissionError(
                "GitLab chat API unauthorized (401). Your token is invalid, expired, or "
                "missing **ai_features** scope."
            )
        resp.raise_for_status()
        try:
            parsed = resp.json()
            if isinstance(parsed, str):
                return parsed
            if isinstance(parsed, dict):
                return parsed.get("content", parsed.get("response", str(parsed)))
            return str(parsed)
        except json.JSONDecodeError:
            return resp.text.strip('"')

    def _extract_text(self, response) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "message":
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", None) == "output_text":
                        parts.append(content.text)
        return "".join(parts)

    def _complete_via_proxy(self, system: str, user: str, json_schema: Optional[dict] = None) -> str:
        client = self._get_openai_client()
        input_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict = {
            "model": self.model,
            "input": input_messages,
        }
        if json_schema:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": json_schema.get("title", "response"),
                    "schema": json_schema,
                    "strict": True,
                }
            }
        response = client.responses.create(**kwargs)
        return self._extract_text(response)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: Type[T],
        extra_system: Optional[str] = None,
    ) -> T:
        if self._auth_failed:
            return fallback_instance(schema)

        full_system = SCIENTIFIC_SYSTEM_INSTRUCTION
        if extra_system:
            full_system = f"{full_system}\n\n{extra_system}"
        if system:
            full_system = f"{full_system}\n\n{system}"

        json_schema = schema.model_json_schema()

        for attempt in range(2):
            try:
                prompt = user
                if attempt == 1:
                    prompt = (
                        f"{user}\n\nReturn ONLY valid JSON matching:\n"
                        f"{json.dumps(json_schema, indent=2)}"
                    )

                if self._use_chat_fallback:
                    chat_content = (
                        f"{full_system}\n\n---\n\n{prompt}\n\n"
                        f"Respond with ONLY valid JSON matching this schema:\n"
                        f"{json.dumps(json_schema, indent=2)}"
                    )
                    text = self._chat_completions_fallback(chat_content)
                else:
                    try:
                        text = self._complete_via_proxy(full_system, prompt, json_schema)
                    except PermissionError as exc:
                        self._disable_auth(str(exc))
                        return fallback_instance(schema)
                    except Exception as proxy_exc:
                        if isinstance(proxy_exc, PermissionError):
                            self._disable_auth(str(proxy_exc))
                            return fallback_instance(schema)
                        logger.warning(
                            "GitLab AI Gateway proxy failed, falling back to chat API: %s",
                            proxy_exc,
                        )
                        self._use_chat_fallback = True
                        self._openai_client = None
                        try:
                            chat_content = (
                                f"{full_system}\n\n---\n\n{prompt}\n\n"
                                f"Respond with ONLY valid JSON."
                            )
                            text = self._chat_completions_fallback(chat_content)
                        except PermissionError as exc:
                            self._disable_auth(str(exc))
                            return fallback_instance(schema)

                text = text.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                data = json.loads(text)
                return schema.model_validate(data)
            except PermissionError as exc:
                self._disable_auth(str(exc))
                return fallback_instance(schema)
            except (json.JSONDecodeError, ValidationError, Exception) as exc:
                logger.warning("GitLab LLM JSON attempt %d failed: %s", attempt + 1, exc)
                if attempt == 1:
                    return fallback_instance(schema)

        return fallback_instance(schema)

    def complete_text(
        self,
        system: str,
        user: str,
        extra_system: Optional[str] = None,
    ) -> str:
        if self._auth_failed:
            return ""

        full_system = SCIENTIFIC_SYSTEM_INSTRUCTION
        if extra_system:
            full_system = f"{full_system}\n\n{extra_system}"
        if system:
            full_system = f"{full_system}\n\n{system}"

        try:
            if self._use_chat_fallback:
                return self._chat_completions_fallback(f"{full_system}\n\n---\n\n{user}")
            try:
                return self._complete_via_proxy(full_system, user)
            except PermissionError as exc:
                self._disable_auth(str(exc))
                return ""
            except Exception as proxy_exc:
                logger.warning("GitLab proxy text call failed, using chat fallback: %s", proxy_exc)
                self._use_chat_fallback = True
                self._openai_client = None
                try:
                    return self._chat_completions_fallback(f"{full_system}\n\n---\n\n{user}")
                except PermissionError as exc:
                    self._disable_auth(str(exc))
                    return ""
        except PermissionError as exc:
            self._disable_auth(str(exc))
            return ""
        except Exception as exc:
            logger.warning("GitLab LLM text call failed: %s", exc)
            return ""
