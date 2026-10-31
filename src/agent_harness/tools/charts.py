"""Local data validation only; browsers render approved chart data without executing code."""

from copy import deepcopy
from uuid import uuid4

from ..core.executor import Tool
from ..models.charts import ChartResult, ChartSpec


def chart_tool():
    schema = ChartSpec.model_json_schema()
    # Expand only our fixed host-owned definitions into the bounded schema subset.
    definitions = schema.pop("$defs", {})
    schema["properties"]["series"]["items"] = deepcopy(definitions["ChartSeries"])
    schema["examples"] = [
        {
            "kind": "line",
            "labels": ["Jan", "Feb"],
            "series": [{"name": "Sales", "values": [10, 15]}],
        },
        {
            "kind": "scatter",
            "labels": ["1", "2", "3"],
            "series": [{"name": "Score", "values": [50, 65, 80]}],
        },
    ]
    schema["description"] = (
        "Create an inline interactive chart or Mermaid diagram from supplied data. "
        "kind: line/bar/scatter/pie with labels and named series values; or mermaid "
        "with source only. Example: {kind:bar,labels:[A,B],series:[{name:Count,values:[1,2]}]}. "
        "The result includes chart_id and a reference string. Copy its reference into "
        "your final answer exactly where that chart belongs, with explanation before "
        "or after it. Copy the returned ID exactly; never invent an ID. "
        "No Python, JavaScript, HTML, links, configuration directives or external resources."
    )

    def create(**arguments):
        spec = ChartSpec.model_validate(arguments).model_dump(mode="json")
        chart_id = uuid4().hex
        return ChartResult(**spec, chart_id=chart_id, reference=f"[[chart:{chart_id}]]").model_dump(
            mode="json"
        )

    return Tool(
        "chart_tool",
        schema,
        (),
        create,
        input_model=ChartSpec,
        output_model=ChartResult,
    )
