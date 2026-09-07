from __future__ import annotations

from pydantic import BaseModel, Field

from nova.plugins.base import Plugin
from nova.tools.base import BaseTool, ToolError, ToolResult

UNITS_TO_M: dict[str, float] = {
    "m": 1.0,
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "ft": 0.3048,
    "in": 0.0254,
    "mi": 1609.344,
    "yd": 0.9144,
}

_TEMP = {"c", "f", "k"}
_WEIGHT_TO_KG: dict[str, float] = {
    "kg": 1.0,
    "g": 0.001,
    "mg": 1e-6,
    "lb": 0.45359237,
    "oz": 0.028349523125,
    "t": 1000.0,
}


def _convert_temp(value: float, source: str, target: str) -> float:
    if source == "c":
        celsius = value
    elif source == "f":
        celsius = (value - 32) * 5 / 9
    else:
        celsius = value - 273.15
    if target == "c":
        return celsius
    if target == "f":
        return celsius * 9 / 5 + 32
    return celsius + 273.15


class ConvertLengthArgs(BaseModel):
    value: float
    from_unit: str = Field(..., description="Source unit: m, km, cm, mm, ft, in, mi, yd")
    to_unit: str = Field(..., description="Target unit: m, km, cm, mm, ft, in, mi, yd")


class ConvertLengthTool(BaseTool):
    name = "convert_length"
    description = "Convert a length between metric and imperial units (m, km, cm, mm, ft, in, mi, yd)."
    input_schema = ConvertLengthArgs

    def execute(self, params: BaseModel) -> ToolResult:
        src, dst = params.from_unit.strip().lower(), params.to_unit.strip().lower()
        if src not in UNITS_TO_M or dst not in UNITS_TO_M:
            raise ToolError(f"unknown length unit. Available: {sorted(UNITS_TO_M)}")
        meters = params.value * UNITS_TO_M[src]
        result = meters / UNITS_TO_M[dst]
        return ToolResult.success(
            self.name,
            message=f"{params.value} {src} = {result:.6g} {dst}",
            data={"value": params.value, "from": src, "to": dst, "result": result},
        )


class ConvertWeightArgs(BaseModel):
    value: float
    from_unit: str = Field(..., description="Source unit: kg, g, mg, lb, oz, t")
    to_unit: str = Field(..., description="Target unit: kg, g, mg, lb, oz, t")


class ConvertWeightTool(BaseTool):
    name = "convert_weight"
    description = "Convert a weight between units (kg, g, mg, lb, oz, t)."
    input_schema = ConvertWeightArgs

    def execute(self, params: BaseModel) -> ToolResult:
        src, dst = params.from_unit.strip().lower(), params.to_unit.strip().lower()
        if src not in _WEIGHT_TO_KG or dst not in _WEIGHT_TO_KG:
            raise ToolError(f"unknown weight unit. Available: {sorted(_WEIGHT_TO_KG)}")
        kilograms = params.value * _WEIGHT_TO_KG[src]
        result = kilograms / _WEIGHT_TO_KG[dst]
        return ToolResult.success(
            self.name,
            message=f"{params.value} {src} = {result:.6g} {dst}",
            data={"value": params.value, "from": src, "to": dst, "result": result},
        )


class ConvertTempArgs(BaseModel):
    value: float
    from_unit: str = Field(..., description="Source unit: c, f, k")
    to_unit: str = Field(..., description="Target unit: c, f, k")


class ConvertTempTool(BaseTool):
    name = "convert_temperature"
    description = "Convert a temperature between Celsius (c), Fahrenheit (f) and Kelvin (k)."
    input_schema = ConvertTempArgs

    def execute(self, params: BaseModel) -> ToolResult:
        src, dst = params.from_unit.strip().lower(), params.to_unit.strip().lower()
        if src not in _TEMP or dst not in _TEMP:
            raise ToolError(f"unknown temperature unit. Available: {sorted(_TEMP)}")
        result = _convert_temp(params.value, src, dst)
        return ToolResult.success(
            self.name,
            message=f"{params.value} {src} = {result:.6g} {dst}",
            data={"value": params.value, "from": src, "to": dst, "result": result},
        )


class UnitsPlugin(Plugin):
    name = "units"
    description = "Unit conversions: length, weight and temperature."

    def tools(self) -> list[BaseTool]:
        return [ConvertLengthTool(), ConvertWeightTool(), ConvertTempTool()]
