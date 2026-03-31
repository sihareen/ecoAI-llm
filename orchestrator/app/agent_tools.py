from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class ToolCall:
    use_tool: bool
    tool_name: str
    tool_input: str
    reason: str


@dataclass
class ToolExecution:
    tool_name: str
    tool_input: str
    output: str
    success: bool


class ToolSimulator:
    supported_tools = {"web_search", "calculator", "api_call"}

    def execute(self, call: ToolCall, tool_traces_context: str = "") -> Optional[ToolExecution]:
        if not call.use_tool:
            return None
        if call.tool_name not in self.supported_tools:
            return ToolExecution(
                tool_name=call.tool_name,
                tool_input=call.tool_input,
                output=f"Unsupported tool: {call.tool_name}",
                success=False,
            )

        if call.tool_name == "web_search":
            output = self._simulate_web_search(call.tool_input, tool_traces_context)
            return ToolExecution(call.tool_name, call.tool_input, output, True)

        if call.tool_name == "calculator":
            try:
                result = self._simulate_calculator(call.tool_input)
                return ToolExecution(call.tool_name, call.tool_input, str(result), True)
            except Exception as exc:  # pylint: disable=broad-except
                return ToolExecution(call.tool_name, call.tool_input, f"Calculation error: {exc}", False)

        output = self._simulate_api_call(call.tool_input)
        return ToolExecution(call.tool_name, call.tool_input, output, True)

    def _simulate_web_search(self, query: str, tool_traces_context: str) -> str:
        lines = [line.strip() for line in tool_traces_context.splitlines() if line.strip()]
        query_tokens = set(re.findall(r"[a-zA-Z0-9_]{3,}", query.lower()))

        ranked: list[tuple[str, int]] = []
        for line in lines:
            line_tokens = set(re.findall(r"[a-zA-Z0-9_]{3,}", line.lower()))
            overlap = len(query_tokens.intersection(line_tokens))
            if overlap > 0:
                ranked.append((line, overlap))

        ranked.sort(key=lambda item: item[1], reverse=True)
        top_lines = [item[0] for item in ranked[:3]]
        if not top_lines:
            top_lines = ["No strong local web evidence found from tool traces."]

        bullets = "\n".join([f"- {line}" for line in top_lines])
        return f"Simulated web results for '{query}':\n{bullets}"

    def _simulate_calculator(self, expression: str) -> float:
        allowed = re.fullmatch(r"[0-9\.\+\-\*\/\(\)\%\s]+", expression)
        if not allowed:
            raise ValueError("Expression contains unsupported characters")

        # Safe eval with no builtins for arithmetic-only expressions.
        result = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
        if not isinstance(result, (int, float)):
            raise ValueError("Expression did not return a numeric value")
        return float(result)

    def _simulate_api_call(self, api_spec: str) -> str:
        compact_spec = " ".join(api_spec.split())
        return (
            "Simulated API response:\n"
            f"request={compact_spec}\n"
            "status=200\n"
            "body={\"message\": \"success\", \"source\": \"simulated_api\"}"
        )
