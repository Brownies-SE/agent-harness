"""Bounded data-only visualization specifications; no executable chart programs."""

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .base import StrictModel

Label = Annotated[str, Field(max_length=80)]
Number = Annotated[float, Field(ge=-1e12, le=1e12, allow_inf_nan=False)]


class ChartSeries(StrictModel):
    name: str = Field(default="", max_length=80)
    values: list[Number] = Field(min_length=1, max_length=200)


class ChartSpec(StrictModel):
    kind: Literal["line", "bar", "scatter", "pie", "mermaid"]
    title: str = Field(default="", max_length=120)
    x_label: str = Field(default="", max_length=80)
    y_label: str = Field(default="", max_length=80)
    labels: list[Label] = Field(
        default_factory=list,
        max_length=200,
        description=(
            "X labels as strings; scatter needs numeric coordinates such as ['1','2','3']."
        ),
    )
    series: list[ChartSeries] = Field(
        default_factory=list,
        max_length=6,
        description=("Named numeric y series; every values array must match labels in length."),
    )
    mermaid: str = Field(default="", max_length=12000)

    @field_validator("mermaid")
    @classmethod
    def safe_diagram(cls, source):
        if not source:
            return source
        lines = source.splitlines()
        meaningful = [
            line.strip() for line in lines if line.strip() and not line.lstrip().startswith("%%")
        ]
        if (
            len(lines) > 200
            or not meaningful
            or not re.match(
                r"^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|mindmap|timeline|pie)\b",
                meaningful[0],
            )
            or re.search(
                r"@\s*\{|%%\s*\{|^\s*---|</?[A-Za-z!]|\b(?:click|href|callback|call|classDef|style|linkStyle|cssClass)\b|"
                r"(?:[a-z][a-z0-9+.-]*://|https?\s*:|javascript\s*:|data\s*:|//)|"
                r"\b(?:img|image|icon)\s*[\"']?\s*:|!\[|url\s*\(|@import",
                source,
                re.I | re.M,
            )
            or any(ord(char) < 32 and char not in "\n\r\t" for char in source)
        ):
            raise ValueError("unsupported or unsafe diagram")
        return source

    @model_validator(mode="after")
    def consistent(self):
        strings = [
            self.title,
            self.x_label,
            self.y_label,
            *self.labels,
            *(s.name for s in self.series),
        ]
        if any("\x00" in text for text in strings):
            raise ValueError("invalid chart text")
        if self.kind == "mermaid":
            if not self.mermaid.strip() or self.labels or self.series:
                raise ValueError("diagram needs source only")
        elif self.mermaid or not self.labels or not self.series:
            raise ValueError("chart needs labels and series")
        elif any(len(series.values) != len(self.labels) for series in self.series):
            raise ValueError("chart data lengths differ")
        if self.kind == "pie" and (
            len(self.series) != 1
            or any(value < 0 for value in self.series[0].values)
            or sum(self.series[0].values) <= 0
        ):
            raise ValueError("pie needs one nonnegative series with a positive total")
        if self.kind == "scatter":
            import math

            try:
                if any(
                    not re.fullmatch(
                        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?",
                        label.strip(),
                        re.I,
                    )
                    or not math.isfinite(float(label))
                    or abs(float(label)) > 1e12
                    for label in self.labels
                ):
                    raise ValueError("invalid scatter coordinates")
            except ValueError as exc:
                raise ValueError("scatter needs numeric x labels") from exc
        return self


class ChartBlock(StrictModel):
    type: Literal["chart"] = "chart"
    spec: ChartSpec


class ChartResult(ChartSpec):
    chart_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    reference: str = Field(pattern=r"^\[\[chart:[a-f0-9]{32}\]\]$")

    @model_validator(mode="after")
    def matching_reference(self):
        if self.reference != f"[[chart:{self.chart_id}]]":
            raise ValueError("invalid chart reference")
        return self
