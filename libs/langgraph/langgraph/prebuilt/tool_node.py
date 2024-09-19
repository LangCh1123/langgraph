from __future__ import annotations

import asyncio
import json
from copy import copy
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
)

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import (
    get_config_list,
    get_executor_for_config,
)
from langchain_core.tools import BaseTool, InjectedToolArg, ToolException
from langchain_core.tools import tool as create_tool
from typing_extensions import Annotated, get_args, get_origin
from langgraph.utils.runnable import RunnableCallable
from pydantic import ValidationError

if TYPE_CHECKING:
    from pydantic import BaseModel

INVALID_TOOL_NAME_ERROR_TEMPLATE = (
    "Error: {requested_tool} is not a valid tool, try one of [{available_tools}]."
)
TOOL_CALL_ERROR_TEMPLATE = "Error: {error}\n Please fix your mistakes."


def str_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    else:
        try:
            return json.dumps(output, ensure_ascii=False)
        except Exception:
            return str(output)
        
def _handle_validation_error(
    e: ValidationError,
    *,
    flag: Union[Literal[True], str, Callable[[ValidationError], str]],
) -> str:
    if isinstance(flag, bool):
        content = TOOL_CALL_ERROR_TEMPLATE.format(error=repr(e))
    elif isinstance(flag, str):
        content = flag
    elif callable(flag):
        content = flag(e)
    else:
        raise ValueError(
            f"Got unexpected type of `handle_validation_error`. Expected bool, "
            f"str or callable. Received: {flag}"
        )
    return content

def _handle_tool_error(
    e: ToolException,
    *,
    flag: Optional[Union[Literal[True], str, Callable[[ToolException], str]]],
) -> str:
    if isinstance(flag, bool):
        content = TOOL_CALL_ERROR_TEMPLATE.format(error=repr(e))
    elif isinstance(flag, str):
        content = flag
    elif callable(flag):
        content = flag(e)
    else:
        raise ValueError(
            f"Got unexpected type of `handle_tool_error`. Expected bool, str "
            f"or callable. Received: {flag}"
        )
    return content



class ToolNode(RunnableCallable):
    """A node that runs the tools called in the last AIMessage.

    Args:
        tools (Sequence[Union[BaseTool, Callable]]): A sequence of tools that can be invoked.
        name (str, optional): The name of the node, defaults to "tools"
        tags (list[str], optional): Tags to associate with the node
        handle_tool_errors (union(callable, str, bool), optional): How to handle tool errors
            raised by tools inside the node. If False then errors will be raised unless dealt with by individual
            tools. If True, default error handler will be used. If string or callable, ToolMessage
            will be returned with correspopnding content.
        handle_validation_errors (union(callable, str, bool), optional): How to handle validation errors
            raised by tools inside the node. If False then errors will be raised unless dealt with by individual
            tools. If True, default error handler will be used. If string or callable, ToolMessage
            will be returned with correspopnding content.

    Example:

        @tool
        def complex_tool(int_arg: int, float_arg: float, dict_arg: dict) -> int:
            \"\"\"Do something complex with a complex tool.\"\"\"
            return int_arg * float_arg

        tools = [complex_tool]

        def handle_tool_errors(e, call):
            content = f"Error: {repr(e)}\n Please fix your mistakes."
            return ToolMessage(content, name=call["name"], tool_call_id=call["id"])

        tool_node = ToolNode(
                        tools,
                        name="my_tool_node",
                        tags=["my_tag_1","my_tag_2"],
                        handle_tool_errors = handle_tool_errors
                    )


    It can be used either in StateGraph with a "messages" key or in MessageGraph. If
    multiple tool calls are requested, they will be run in parallel. The output will be
    a list of ToolMessages, one for each tool call.

    The `ToolNode` is roughly analogous to:

    ```python
    tools_by_name = {tool.name: tool for tool in tools}
    def tool_node(state: dict):
        result = []
        for tool_call in state["messages"][-1].tool_calls:
            tool = tools_by_name[tool_call["name"]]
            observation = tool.invoke(tool_call["args"])
            result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"]))
        return {"messages": result}
    ```

    Important:
        - The state MUST contain a list of messages.
        - The last message MUST be an `AIMessage`.
        - The `AIMessage` MUST have `tool_calls` populated.
    """

    def __init__(
        self,
        tools: Sequence[Union[BaseTool, Callable]],
        *,
        name: str = "tools",
        tags: Optional[list[str]] = None,
        handle_tool_errors: Optional[Union[bool, str, Callable[[ToolException], str]]] = True,
        handle_validation_errors: Optional[Union[bool, str, Callable[[ValidationError], str]]] = True
    ) -> None:
        super().__init__(self._func, self._afunc, name=name, tags=tags, trace=False)
        self.tools_by_name: Dict[str, BaseTool] = {}
        self.handle_tool_errors = handle_tool_errors
        self.handle_validation_errors = handle_validation_errors
        for tool_ in tools:
            if not isinstance(tool_, BaseTool):
                tool_ = create_tool(tool_)
            self.tools_by_name[tool_.name] = tool_

    def _func(
        self,
        input: Union[
            list[AnyMessage],
            dict[str, Any],
            BaseModel,
        ],
        config: RunnableConfig,
    ) -> Any:
        tool_calls, output_type = self._parse_input(input)
        config_list = get_config_list(config, len(tool_calls))
        with get_executor_for_config(config) as executor:
            outputs = [*executor.map(self._run_one, tool_calls, config_list)]
        # TypedDict, pydantic, dataclass, etc. should all be able to load from dict
        return outputs if output_type == "list" else {"messages": outputs}

    async def _afunc(
        self,
        input: Union[
            list[AnyMessage],
            dict[str, Any],
            BaseModel,
        ],
        config: RunnableConfig,
    ) -> Any:
        tool_calls, output_type = self._parse_input(input)
        outputs = await asyncio.gather(
            *(self._arun_one(call, config) for call in tool_calls)
        )
        # TypedDict, pydantic, dataclass, etc. should all be able to load from dict
        return outputs if output_type == "list" else {"messages": outputs}

    def _run_one(self, call: ToolCall, config: RunnableConfig) -> ToolMessage:
        if invalid_tool_message := self._validate_tool_call(call):
            return invalid_tool_message

        input = {**call, **{"type": "tool_call"}}
        error_to_raise: Union[Exception, None] = None
        try:
            tool_message: ToolMessage = self.tools_by_name[call["name"]].invoke(
                input, config
            )
            # TODO: handle this properly in core
            tool_message.content = str_output(tool_message.content)
            return tool_message
        except ValidationError as e:
            if not self.handle_validation_errors:
                error_to_raise = e
            else:
                content = _handle_validation_error(
                        e, flag=self.handle_validation_errors
                    )
        except ToolException as e:
            if not self.handle_tool_errors:
                error_to_raise = e
            else:
                content = _handle_tool_error(e, flag=self.handle_tool_errors)

        if error_to_raise:
            raise error_to_raise
        return ToolMessage(content=content, name=call['name'], tool_call_id=call['id'], status="error")
        

    async def _arun_one(self, call: ToolCall, config: RunnableConfig) -> ToolMessage:
        if invalid_tool_message := self._validate_tool_call(call):
            return invalid_tool_message

        input = {**call, **{"type": "tool_call"}}
        error_to_raise: Union[Exception, None] = None
        try:
            try:
                tool_message: ToolMessage = await self.tools_by_name[call["name"]].ainvoke(
                    input, config
                )
                # TODO: handle this properly in core
                tool_message.content = str_output(tool_message.content)
                return tool_message
            except ValidationError as e:
                if not self.handle_validation_errors:
                    error_to_raise = e
                else:
                    content = _handle_validation_error(
                            e, flag=self.handle_validation_errors
                        )
            except ToolException as e:
                    raise e
            except Exception as e:
                raise ToolException(str(e)) from e
        except ToolException as e:
            if not self.handle_tool_errors:
                error_to_raise = e
            else:
                content = _handle_tool_error(e, flag=self.handle_tool_errors)

        if error_to_raise:
            raise error_to_raise
        return ToolMessage(content=content, name=call['name'], tool_call_id=call['id'], status="error")

    def _parse_input(
        self,
        input: Union[
            list[AnyMessage],
            dict[str, Any],
            BaseModel,
        ],
    ) -> Tuple[List[ToolCall], Literal["list", "dict"]]:
        if isinstance(input, list):
            output_type = "list"
            message: AnyMessage = input[-1]
        elif isinstance(input, dict) and (messages := input.get("messages", [])):
            output_type = "dict"
            message = messages[-1]
        elif messages := getattr(input, "messages", None):
            # Assume dataclass-like state that can coerce from dict
            output_type = "dict"
            message = messages[-1]
        else:
            raise ValueError("No message found in input")

        if not isinstance(message, AIMessage):
            raise ValueError("Last message is not an AIMessage")

        tool_calls = [
            self._inject_state(call, input)
            for call in cast(AIMessage, message).tool_calls
        ]
        return tool_calls, output_type

    def _validate_tool_call(self, call: ToolCall) -> Optional[ToolMessage]:
        if (requested_tool := call["name"]) not in self.tools_by_name:
            content = INVALID_TOOL_NAME_ERROR_TEMPLATE.format(
                requested_tool=requested_tool,
                available_tools=", ".join(self.tools_by_name.keys()),
            )
            return ToolMessage(content, name=requested_tool, tool_call_id=call["id"], status="error")
        else:
            return None

    def _inject_state(
        self,
        tool_call: ToolCall,
        input: Union[
            list[AnyMessage],
            dict[str, Any],
            BaseModel,
        ],
    ) -> ToolCall:
        if tool_call["name"] not in self.tools_by_name:
            return tool_call
        state_args = _get_state_args(self.tools_by_name[tool_call["name"]])
        if state_args and isinstance(input, list):
            required_fields = list(state_args.values())
            if (
                len(required_fields) == 1
                and required_fields[0] == "messages"
                or required_fields[0] is None
            ):
                input = {"messages": input}
            else:
                err_msg = (
                    f"Invalid input to ToolNode. Tool {tool_call['name']} requires "
                    f"graph state dict as input."
                )
                if any(state_field for state_field in state_args.values()):
                    required_fields_str = ", ".join(f for f in required_fields if f)
                    err_msg += f" State should contain fields {required_fields_str}."
                raise ValueError(err_msg)
        if isinstance(input, dict):
            tool_state_args = {
                tool_arg: input[state_field] if state_field else input
                for tool_arg, state_field in state_args.items()
            }

        else:
            tool_state_args = {
                tool_arg: getattr(input, state_field) if state_field else input
                for tool_arg, state_field in state_args.items()
            }

        tool_call_copy: ToolCall = copy(tool_call)
        tool_call_copy["args"] = {
            **tool_call_copy["args"],
            **tool_state_args,
        }
        return tool_call_copy


def tools_condition(
    state: Union[list[AnyMessage], dict[str, Any], BaseModel],
) -> Literal["tools", "__end__"]:
    """Use in the conditional_edge to route to the ToolNode if the last message

    has tool calls. Otherwise, route to the end.

    Args:
        state (Union[list[AnyMessage], dict[str, Any], BaseModel]): The state to check for
            tool calls. Must have a list of messages (MessageGraph) or have the
            "messages" key (StateGraph).

    Returns:
        The next node to route to.


    Examples:
        Create a custom ReAct-style agent with tools.

        ```pycon
        >>> from langchain_anthropic import ChatAnthropic
        >>> from langchain_core.tools import tool
        ...
        >>> from langgraph.graph import StateGraph
        >>> from langgraph.prebuilt import ToolNode, tools_condition
        >>> from langgraph.graph.message import add_messages
        ...
        >>> from typing import TypedDict, Annotated
        ...
        >>> @tool
        >>> def divide(a: float, b: float) -> int:
        ...     \"\"\"Return a / b.\"\"\"
        ...     return a / b
        ...
        >>> llm = ChatAnthropic(model="claude-3-haiku-20240307")
        >>> tools = [divide]
        ...
        >>> class State(TypedDict):
        ...     messages: Annotated[list, add_messages]
        >>>
        >>> graph_builder = StateGraph(State)
        >>> graph_builder.add_node("tools", ToolNode(tools))
        >>> graph_builder.add_node("chatbot", lambda state: {"messages":llm.bind_tools(tools).invoke(state['messages'])})
        >>> graph_builder.add_edge("tools", "chatbot")
        >>> graph_builder.add_conditional_edges(
        ...     "chatbot", tools_condition
        ... )
        >>> graph_builder.set_entry_point("chatbot")
        >>> graph = graph_builder.compile()
        >>> graph.invoke({"messages": {"role": "user", "content": "What's 329993 divided by 13662?"}})
        ```
    """
    if isinstance(state, list):
        ai_message = state[-1]
    elif isinstance(state, dict) and (messages := state.get("messages", [])):
        ai_message = messages[-1]
    elif messages := getattr(state, "messages", []):
        ai_message = messages[-1]
    else:
        raise ValueError(f"No messages found in input state to tool_edge: {state}")
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        return "tools"
    return "__end__"


class InjectedState(InjectedToolArg):
    """Annotation for a Tool arg that is meant to be populated with the graph state.

    Any Tool argument annotated with InjectedState will be hidden from a tool-calling
    model, so that the model doesn't attempt to generate the argument. If using
    ToolNode, the appropriate graph state field will be automatically injected into
    the model-generated tool args.

    Args:
        field: The key from state to insert. If None, the entire state is expected to
            be passed in.

    Example:
        ```python
        from typing import List
        from typing_extensions import Annotated, TypedDict

        from langchain_core.messages import BaseMessage, AIMessage
        from langchain_core.tools import tool

        from langgraph.prebuilt import InjectedState, ToolNode


        class AgentState(TypedDict):
            messages: List[BaseMessage]
            foo: str

        @tool
        def state_tool(x: int, state: Annotated[dict, InjectedState]) -> str:
            '''Do something with state.'''
            if len(state["messages"]) > 2:
                return state["foo"] + str(x)
            else:
                return "not enough messages"

        @tool
        def foo_tool(x: int, foo: Annotated[str, InjectedState("foo")]) -> str:
            '''Do something else with state.'''
            return foo + str(x + 1)

        node = ToolNode([state_tool, foo_tool])

        tool_call1 = {"name": "state_tool", "args": {"x": 1}, "id": "1", "type": "tool_call"}
        tool_call2 = {"name": "foo_tool", "args": {"x": 1}, "id": "2", "type": "tool_call"}
        state = {
            "messages": [AIMessage("", tool_calls=[tool_call1, tool_call2])],
            "foo": "bar",
        }
        node.invoke(state)
        ```

        ```pycon
        [
            ToolMessage(content='not enough messages', name='state_tool', tool_call_id='1'),
            ToolMessage(content='bar2', name='foo_tool', tool_call_id='2')
        ]
        ```
    """  # noqa: E501

    def __init__(self, field: Optional[str] = None) -> None:
        self.field = field


def _get_state_args(tool: BaseTool) -> Dict[str, Optional[str]]:
    full_schema = tool.get_input_schema()
    tool_args_to_state_fields: Dict = {}

    def _is_injection(type_arg: Any):
        if isinstance(type_arg, InjectedState) or (
            isinstance(type_arg, type) and issubclass(type_arg, InjectedState)
        ):
            return True
        origin_ = get_origin(type_arg)
        if origin_ is Union or origin_ is Annotated:
            return any(_is_injection(ta) for ta in get_args(type_arg))
        return False

    for name, type_ in full_schema.__annotations__.items():
        injections = [
            type_arg for type_arg in get_args(type_) if _is_injection(type_arg)
        ]
        if len(injections) > 1:
            raise ValueError(
                "A tool argument should not be annotated with InjectedState more than "
                f"once. Received arg {name} with annotations {injections}."
            )
        elif len(injections) == 1:
            injection = injections[0]
            if isinstance(injection, InjectedState) and injection.field:
                tool_args_to_state_fields[name] = injection.field
            else:
                tool_args_to_state_fields[name] = None
        else:
            pass
    return tool_args_to_state_fields
