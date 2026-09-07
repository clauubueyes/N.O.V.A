from __future__ import annotations

import base64
import re
import uuid as _uuid

from pydantic import BaseModel, Field

from nova.plugins.base import Plugin
from nova.tools.base import BaseTool, ToolError, ToolResult


class Base64EncodeArgs(BaseModel):
    text: str = Field(..., description="Text to base64-encode")


class Base64EncodeTool(BaseTool):
    name = "text_base64_encode"
    description = "Encode a string to base64."
    input_schema = Base64EncodeArgs

    def execute(self, params: BaseModel) -> ToolResult:
        encoded = base64.b64encode(params.text.encode("utf-8")).decode("ascii")
        return ToolResult.success(self.name, data={"encoded": encoded})


class Base64DecodeArgs(BaseModel):
    text: str = Field(..., description="Base64 string to decode")


class Base64DecodeTool(BaseTool):
    name = "text_base64_decode"
    description = "Decode a base64 string to plain text."
    input_schema = Base64DecodeArgs

    def execute(self, params: BaseModel) -> ToolResult:
        try:
            decoded = base64.b64decode(params.text.encode("ascii"), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ToolError(f"invalid base64 input: {exc}") from exc
        return ToolResult.success(self.name, data={"decoded": decoded})


class SlugifyArgs(BaseModel):
    text: str = Field(..., description="Text to turn into a URL-safe slug")


class SlugifyTool(BaseTool):
    name = "text_slugify"
    description = "Convert a string into a lowercase URL-safe slug (e.g. 'Hola Mundo!' -> 'hola-mundo')."
    input_schema = SlugifyArgs

    def execute(self, params: BaseModel) -> ToolResult:
        slug = re.sub(r"[^a-z0-9]+", "-", params.text.lower()).strip("-")
        return ToolResult.success(self.name, data={"slug": slug})


class UuidArgs(BaseModel):
    pass


class UuidTool(BaseTool):
    name = "text_uuid"
    description = "Generate a random UUID (v4) string."
    input_schema = UuidArgs

    def execute(self, params: BaseModel) -> ToolResult:
        return ToolResult.success(self.name, data={"uuid": str(_uuid.uuid4())})


class TextToolsPlugin(Plugin):
    name = "text_tools"
    description = "String utilities: base64 encode/decode, slugify and UUID generation."

    def tools(self) -> list[BaseTool]:
        return [
            Base64EncodeTool(),
            Base64DecodeTool(),
            SlugifyTool(),
            UuidTool(),
        ]
