"""One door for every language-model call, so that each call is cached, logged and repeatable.

- The cache key is a hash of (backend, model, system prompt, user text, image hashes, schema).
  Re-running a stage therefore costs nothing and returns identical results; changing a prompt
  or the model invalidates exactly the affected calls.
- Each cache file keeps the prompt hash, model, token usage and timestamp next to the response.
- Backends: `anthropic` (Claude API), `ollama` (Ollama's own API: lets us set the context window
  and switch off "thinking"), `openai_compatible` (llama.cpp, LM Studio, vLLM, or any provider
  exposing /chat/completions), `none` (cache only).
- Settings under `llm.stages.<stage>` override the general `llm` settings for that stage, so that
  e.g. transcription can use a small OCR model and enrichment a larger general one.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import Config, now, read_json, sha256_file, sha256_text, write_json


class LLMUnavailable(RuntimeError):
    pass


class ServerRejected(RuntimeError):
    """The server answered that the request itself is wrong (HTTP 4xx). Repeating it would not help."""


def strip_keys(node, keys=("pattern", "default", "title")):
    """Schema without the keywords that local grammar-based JSON decoders tend to refuse.
    Nothing is lost: replies are still checked against the full record structure afterwards."""
    if isinstance(node, dict):
        return {k: strip_keys(v, keys) for k, v in node.items() if k not in keys or isinstance(v, dict)}
    if isinstance(node, list):
        return [strip_keys(v, keys) for v in node]
    return node


def _image_b64(path: Path, max_px: int) -> str:
    from PIL import Image

    im = Image.open(path).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def parse_json_reply(content: str) -> dict:
    """Local models often wrap JSON in reasoning tags, code fences or a sentence; unwrap it."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        a, b = content.find("{"), content.rfind("}")
        if a < 0 or b <= a:
            raise
        return json.loads(content[a:b + 1])


class LLM:
    def __init__(self, cfg: Config, stage: str | None = None):
        self.cfg = cfg
        base = {k: v for k, v in cfg["llm"].items() if k != "stages"}
        self.c = {**base, **(cfg["llm"].get("stages", {}) or {}).get(stage, {})}
        if os.environ.get("RCTB_BASE_URL"):  # set by hpc/run_pipeline.sh: the port differs from job to job
            self.c["base_url"] = os.environ["RCTB_BASE_URL"]
        self.cache_dir = cfg.work / "llm_cache"
        self._schema_refused = False

    @property
    def enabled(self) -> bool:
        return self.c["backend"] != "none"

    def call(self, task: str, system: str, text: str, images: list[Path], schema: dict | None,
             image_max_px: int | None = None, validate=None) -> tuple[dict, dict]:
        """Returns (result, metadata). With a schema the result is a dict matching it; with
        schema=None the model answers in plain text and the result is {"text": ...}.
        `validate` (e.g. a pydantic model) is applied before caching; a failure triggers a retry."""
        max_px = image_max_px or self.c["image_max_px"]
        if self.c.get("images", True) is False:  # text-only model for this stage: work from the transcription alone
            images = []
        key = sha256_text(json.dumps({
            "backend": self.c["backend"], "model": self.c["model"], "system": system, "text": text,
            "images": [sha256_file(p) for p in images], "max_px": max_px, "schema": schema,
        }, sort_keys=True, ensure_ascii=False))
        path = self.cache_dir / task / f"{key[:24]}.json"
        cached = read_json(path)
        if cached:
            return cached["result"], {**cached["meta"], "cached": True}
        if not self.enabled:
            raise LLMUnavailable("llm.backend is 'none' and this call is not in the cache.")

        b64 = [_image_b64(p, max_px) for p in images]
        backend = {"anthropic": self._anthropic, "openai_compatible": self._openai,
                   "ollama": self._ollama}.get(self.c["backend"])
        if backend is None:
            raise ValueError(f"unknown llm.backend {self.c['backend']!r}")
        attempts, last_error, feedback = 1 + int(self.c.get("retries", 2)), None, None
        for attempt in range(1, attempts + 1):
            try:
                ask = text
                if feedback:  # tell the model what was wrong with its previous reply; a blind repeat would repeat the error
                    ask += ("\n\nYour previous reply was not valid for the record structure:\n" + feedback
                            + "\nReply again with a corrected, complete JSON object.")
                result, usage = backend(system, ask, b64, schema, attempt)
                if validate is not None:
                    validate(**result)
                break
            except (ValueError, KeyError, TypeError) as e:  # unparsable or schema-violating reply
                last_error, feedback = e, str(e)[:800]
            except OSError as e:  # server busy, restarting or unreachable: wait and try again
                last_error = e
                time.sleep(min(60, 5 * attempt))
        else:
            raise RuntimeError(f"{task}: no valid reply after {attempts} attempts: {last_error}")
        meta = {"backend": self.c["backend"], "model": self.c["model"], "time": now(),
                "system_sha": sha256_text(system)[:12], "usage": usage, "cache_key": key[:24], "attempts": attempt}
        write_json(path, {"meta": meta, "task": task, "images": [str(p) for p in images],
                          "user_text": text, "result": result})
        return result, {**meta, "cached": False}

    # ------------------------------------------------------------------ backends
    def _anthropic(self, system, text, b64, schema, attempt=1):
        import anthropic  # pip install anthropic

        client = anthropic.Anthropic(api_key=os.environ[self.c["api_key_env"]])
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": d}} for d in b64]
        content.append({"type": "text", "text": text})
        kw = dict(model=self.c["model"], max_tokens=self.c["max_tokens"], messages=[{"role": "user", "content": content}])
        if system:
            kw["system"] = system
        if schema is not None:  # structured output through a forced tool call
            kw["tools"] = [{"name": "record", "description": "Record the result.", "input_schema": schema}]
            kw["tool_choice"] = {"type": "tool", "name": "record"}
        msg = client.messages.create(**kw)
        usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens,
                 "stop_reason": msg.stop_reason}
        if schema is None:
            return {"text": "".join(b.text for b in msg.content if b.type == "text")}, usage
        return next(b.input for b in msg.content if b.type == "tool_use"), usage

    def _post(self, url: str, body: dict, headers: dict | None = None) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        host = urllib.parse.urlparse(url).hostname
        # a server on this machine is reached directly, even where a web proxy is configured (HPC nodes)
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
                  if host in ("127.0.0.1", "localhost", "::1") else urllib.request.build_opener())
        try:
            with opener.open(req, timeout=self.c.get("timeout", 900)) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            if 400 <= e.code < 500 and e.code not in (408, 429):
                raise ServerRejected(f"HTTP {e.code} from {url}: {detail}") from None
            raise OSError(f"HTTP {e.code} from {url}: {detail}") from None

    def ollama_host(self) -> str:
        """config `host`, else the OLLAMA_HOST variable (set by `module load ollama`), else the default port."""
        h = self.c.get("host") or os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434"
        return (h if h.startswith("http") else f"http://{h}").rstrip("/")

    def _ollama(self, system, text, b64, schema, attempt=1):
        user = {"role": "user", "content": text}
        if b64:
            user["images"] = b64
        num_ctx = int(self.c.get("num_ctx", 32768))
        body = {"model": self.c["model"], "stream": False,
                "messages": ([{"role": "system", "content": system}] if system else []) + [user],
                "options": {"num_ctx": num_ctx, "num_predict": self.c["max_tokens"],
                            "temperature": self.c.get("temperature", 0),
                            "seed": int(self.c.get("seed", 0)) + attempt - 1},
                "keep_alive": self.c.get("keep_alive", "30m")}
        if self.c.get("think") is not None:  # false = direct answer for reasoning models (qwen3.x); omit for others
            body["think"] = self.c["think"]
        note = None
        if schema is not None:
            plain = {**user, "content": text + "\n\nReply with one JSON object and nothing else. It must follow this "
                                              "JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)}
            if self._schema_refused:
                body["format"], body["messages"][-1] = "json", plain
            else:
                body["format"] = strip_keys(schema)  # Ollama constrains the reply to this JSON schema
        try:
            out = self._post(self.ollama_host() + "/api/chat", body)
        except ServerRejected as e:
            if schema is None or body.get("format") == "json":
                raise
            # The server refuses this schema as a decoding constraint: ask for plain JSON and put the schema in the prompt.
            self._schema_refused = True
            note = f"schema not accepted as `format` ({str(e)[:200]}); plain JSON mode used"
            body["format"], body["messages"][-1] = "json", plain
            out = self._post(self.ollama_host() + "/api/chat", body)
        reply = (out.get("message") or {}).get("content") or ""
        usage = {"input_tokens": out.get("prompt_eval_count"), "output_tokens": out.get("eval_count"),
                 "seconds": round((out.get("total_duration") or 0) / 1e9, 1), "stop_reason": out.get("done_reason")}
        if (usage["input_tokens"] or 0) >= num_ctx - 64:
            usage["warning"] = f"prompt filled the context window (num_ctx={num_ctx}): input was probably truncated"
        if out.get("done_reason") == "length":
            usage["warning"] = "reply cut off at max_tokens"
        if note or (schema is not None and body.get("format") == "json"):
            usage["note"] = note or "plain JSON mode (schema in the prompt)"
        if schema is None:
            return {"text": re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()}, usage
        return parse_json_reply(reply), usage

    def _openai(self, system, text, b64, schema, attempt=1):
        if schema is not None and self.c.get("schema_in_prompt", True):
            # Smaller models follow a schema more reliably when they can also read it.
            text += ("\n\nReply with one JSON object and nothing else. It must follow this JSON Schema:\n"
                     + json.dumps(schema, ensure_ascii=False))
        content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{d}"}} for d in b64]
        content.append({"type": "text", "text": text})
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        body = {"model": self.c["model"], "max_tokens": self.c["max_tokens"], "messages": messages,
                "temperature": self.c.get("temperature", 0), "seed": int(self.c.get("seed", 0)) + attempt - 1}
        mode = self.c.get("json_mode", "schema")  # schema | object | none - what the server supports
        if schema is not None and mode == "schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "record", "schema": schema}}
        elif schema is not None and mode == "object":
            body["response_format"] = {"type": "json_object"}
        out = self._post(self.c["base_url"].rstrip("/") + "/chat/completions", body,
                         {"Authorization": f"Bearer {os.environ.get(self.c['api_key_env'], 'none')}"})
        reply = out["choices"][0]["message"]["content"] or ""
        usage = out.get("usage", {})
        if schema is None:
            return {"text": re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()}, usage
        return parse_json_reply(reply), usage


def load_prompt(cfg: Config, name: str) -> str:
    return (cfg.p("prompts_dir") / f"{name}.md").read_text(encoding="utf-8")
