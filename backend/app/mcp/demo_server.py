"""A small MCP server for demos and tests: a clock, a calculator, and a fake order system.

Run it over stdio (how the platform starts it when registered as a stdio server):
    uv run python -m app.mcp.demo_server
Or over HTTP, then register it as an http server with url http://127.0.0.1:8765/mcp:
    uv run python -m app.mcp.demo_server --http 8765

Tools raise `ToolError` for expected failures, so the message reaches the model.
`cancel_order` is marked destructive, so the platform keeps it switched off until an admin enables it.
Orders live in memory and reset when the server restarts.
"""

import argparse
import ast
import operator
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

server = MCPServer(
    "demo",
    instructions="Demo tools: current time, arithmetic, and a sample order system.",
    version="1.0.0",
)

ORDERS: dict[str, dict] = {
    "A-1001": {"order_id": "A-1001", "customer": "Acme Corp", "status": "shipped", "total": 1249.00},
    "A-1002": {"order_id": "A-1002", "customer": "Globex", "status": "processing", "total": 310.50},
    "A-1003": {"order_id": "A-1003", "customer": "Initech", "status": "delivered", "total": 89.99},
}

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


@server.tool(annotations=_READ_ONLY)
def get_time(timezone: str = "UTC") -> str:
    """Current date and time in an IANA time zone, e.g. "Europe/London" or "Asia/Kolkata"."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolError(f"Unknown time zone {timezone!r}. Use an IANA name like 'America/New_York'.") from exc
    return datetime.now(zone).strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")


@server.tool(annotations=_READ_ONLY)
def calculate(expression: str) -> str:
    """Evaluate arithmetic: numbers, + - * / // % ** and parentheses. Example: "(17.5 * 4) / 3"."""
    if len(expression) > 200:
        raise ToolError("Expression is too long (max 200 characters).")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Not a valid expression: {expression!r}") from exc
    value = _evaluate(tree.body)
    return f"{expression} = {round(value, 10) if isinstance(value, float) else value}"


def _evaluate(node: ast.AST) -> int | float:
    """Walk the parsed expression. Only numbers and arithmetic operators are allowed."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ToolError("Exponent too large (max 100).")
        try:
            return _OPERATORS[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise ToolError("Division by zero.") from exc
    raise ToolError("Only numbers and + - * / // % ** ( ) are allowed.")


@server.tool(annotations=_READ_ONLY)
def lookup_order(order_id: str) -> dict:
    """Look up an order by its id (e.g. "A-1001"): customer, status and total."""
    order = ORDERS.get(order_id.strip().upper())
    if order is None:
        raise ToolError(f"No order {order_id!r}. Known orders: {', '.join(sorted(ORDERS))}.")
    return order


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def cancel_order(order_id: str, reason: str = "") -> str:
    """Cancel an order. This changes data: only use it when the user clearly asks to cancel."""
    order = ORDERS.get(order_id.strip().upper())
    if order is None:
        raise ToolError(f"No order {order_id!r}.")
    if order["status"] in ("delivered", "cancelled"):
        raise ToolError(f"Order {order['order_id']} is {order['status']} and can't be cancelled.")
    order["status"] = "cancelled"
    return f"Order {order['order_id']} cancelled." + (f" Reason: {reason}" if reason else "")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--http", type=int, metavar="PORT", help="serve over HTTP on this port instead of stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    if args.http:
        server.run("streamable-http", host=args.host, port=args.http)
    else:
        server.run()


if __name__ == "__main__":
    main()
