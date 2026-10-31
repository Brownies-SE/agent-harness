"""Local data validation only; browsers render approved chart data without executing code."""

from copy import deepcopy

from ..core.executor import Tool
from ..models.charts import ChartSpec


def chart_tool():
    schema = ChartSpec.model_json_schema()
    # Expand only our fixed host-owned definitions into the bounded schema subset.
    definitions = schema.pop("$defs", {})
    schema["properties"]["series"]["items"] = deepcopy(definitions["ChartSeries"])
    schema["description"] = (
        "Create an inline interactive chart or Mermaid diagram from supplied data. "
        "kind: line/bar/scatter/pie with labels and named series values; or mermaid "
        "with source only. Example: {kind:bar,labels:[A,B],series:[{name:Count,values:[1,2]}]}. "
        "The chart is attached to your saved reply; explain it in your final answer. "
        "No Python, JavaScript, HTML, links, configuration directives or external resources."
    )

    def create(**arguments):
        return ChartSpec.model_validate(arguments).model_dump(mode="json")

    return Tool(
        "chart_tool",
        schema,
        (),
        create,
        input_model=ChartSpec,
        output_model=ChartSpec,
    )
