"""Prompt registry for loading and composing system prompts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import structlog

from agent_kernel.core.schemas.context import ContextItem, ContextRef
from agent_kernel.core.schemas.trace import PromptPartRef
from agent_kernel.prompting.system_prompts import (
    is_system_prompt_ref,
    sort_prompt_refs,
)

logger = structlog.get_logger(__name__)

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


@dataclass(frozen=True)
class PromptPart:
    """Prompt part content loaded from disk."""

    prompt_id: str
    layer: str | None
    path: str
    hash: str
    text: str

    def to_provenance(self) -> PromptPartRef:
        """Convert to provenance-safe prompt reference."""
        return PromptPartRef(
            prompt_id=self.prompt_id,
            hash=self.hash,
            layer=self.layer,
            path=self.path,
        )


@dataclass(frozen=True)
class PromptBundle:
    """Composed prompt bundle."""

    text: str
    hash: str | None
    parts: list[PromptPart]

    def to_provenance_parts(self) -> list[PromptPartRef]:
        """Return prompt parts suitable for trace provenance."""
        return [part.to_provenance() for part in self.parts]


class PromptRegistry:
    """Loads prompts from disk and composes system prompt bundles."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else DEFAULT_PROMPTS_DIR

    def compose(self, prompt_refs: Iterable[ContextRef]) -> PromptBundle:
        """Compose a prompt bundle from prompt refs."""
        parts: list[PromptPart] = []
        for ref in sort_prompt_refs([ref for ref in prompt_refs if is_system_prompt_ref(ref)]):
            part = self._load_prompt_part(ref)
            if part:
                parts.append(part)

        if not parts:
            return PromptBundle(text="", hash=None, parts=[])

        combined_text = "\n\n".join(part.text for part in parts)
        bundle_hash = self._hash_text(combined_text)
        return PromptBundle(text=combined_text, hash=bundle_hash, parts=parts)

    def compose_from_items(self, items: Iterable[ContextItem]) -> PromptBundle:
        """Compose a prompt bundle from ContextItems."""
        refs = [item.ref for item in items if is_system_prompt_ref(item.ref)]
        return self.compose(refs)

    def _load_prompt_part(self, ref: ContextRef) -> PromptPart | None:
        uri = ref.uri or ""
        prompt_path = self._resolve_uri(uri)
        if prompt_path is None:
            logger.warning(
                "prompt_uri_unrecognized",
                ref_id=ref.ref_id,
                uri=uri,
            )
            return None

        full_path = self._base_dir / prompt_path
        if not full_path.exists():
            logger.warning(
                "prompt_file_missing",
                ref_id=ref.ref_id,
                path=str(full_path),
            )
            return None

        text = full_path.read_text(encoding="utf-8")
        meta = ref.metadata if isinstance(ref.metadata, dict) else {}
        layer = meta.get("layer")
        prompt_id = str(meta.get("prompt_id") or ref.ref_id)
        part_hash = self._hash_text(text)
        return PromptPart(
            prompt_id=prompt_id,
            layer=str(layer) if layer is not None else None,
            path=str(prompt_path),
            hash=part_hash,
            text=text.strip(),
        )

    def _resolve_uri(self, uri: str) -> str | None:
        if uri.startswith("prompts:///"):
            return uri[len("prompts:///") :].lstrip("/")
        if uri.startswith("prompts://"):
            return uri[len("prompts://") :].lstrip("/")
        if uri.startswith("prompts:"):
            return uri[len("prompts:") :].lstrip("/")
        return None

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
