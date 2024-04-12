# 🦜🕸️LangGraph

[![Downloads](https://static.pepy.tech/badge/langgraph/month)](https://pepy.tech/project/langgraph)

⚡ Build language agents as graphs ⚡

## Overview

Suppose you're building a customer support assistant. You want your assistant to be able to:

1. Use tools to respond to questions
2. Connect with a human if needed
3. Be able to pause the process indefinitely and resume whenever the human responds

LangGraph makes this all easy. First install:

```bash
pip install -U langgraph
```

Then define your assistant:

```python
import json

from langchain_anthropic import ChatAnthropic
from langchain_community.tools.tavily_search import TavilySearchResults

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessageGraph
from langgraph.prebuilt.tool_node import ToolNode


# Define the function that determines whether to continue or not
def should_continue(messages):
    last_message = messages[-1]
    # If there is no function call, then we finish
    if not last_message.tool_calls:
        return END
    else:
        return "action"


# Define a new graph
workflow = MessageGraph()

tools = [TavilySearchResults(max_results=1)]
model = ChatAnthropic(model="claude-3-haiku-20240307").bind_tools(tools)
workflow.add_node("agent", model)
workflow.add_node("action", ToolNode(tools))

workflow.set_entry_point("agent")

# Conditional agent -> action OR agent -> END
workflow.add_conditional_edges(
    "agent",
    should_continue,
)

# Always transition `action` -> `agent`
workflow.add_edge("action", "agent")

memory = SqliteSaver.from_conn_string(":memory:") # Here we only save in-memory

# Setting the interrupt means that any time an action is called, the machine will stop
app = workflow.compile(checkpointer=memory, interrupt_before=["action"])
```

Now, run the graph:

```python
# Run the graph
thread = {"configurable": {"thread_id": "4"}}
for event in app.stream("what is the weather in sf currently", thread):
    for v in event.values():
        print(v)

```
We configured the graph to **wait** before executing the `action`. The `SqliteSaver` persists the state. Resume at any time.

```python
for event in app.stream(None, thread):
    for v in event.values():
        print(v)
```

The graph orchestrates everything:

- The `MessageGraph` contains the agent's "Memory"
- Conditional edges enable dynamic routing between the chatbot, tools, and the user
- Persistence makes it easy to stop, resume, and even rewind for full control over your application

With LangGraph, you can build complex, stateful agents without getting bogged down in manual state and interrupt management. Just define your nodes, edges, and state schema - and let the graph take care of the rest.


## Tutorials

Consult the [Tutorials](tutorials/index.md) to learn more about implementing advanced 

- persistence.ipynb
- async.ipynb
- streaming-tokens.ipynb
- human-in-the-loop.ipynb
- visualization.ipynb
- state-model.ipynb
- time-travel.ipynb

#### Use Cases

- **Agent Executors**: Chat and Langchain agents
- **Planning Agents**: Plan-and-Execute, ReWOO, LLMCompiler  
- **Reflection & Critique**: Improving quality via reflection
- **Multi-Agent Systems**: Collaboration, supervision, teams
- **Research & QA**: Web research, retrieval-augmented QA  
- **Applications**: Chatbots, code assist, web tasks
- **Evaluation & Analysis**: Simulation, self-discovery, swarms


## How-To Guides

Check out the [How-To Guides](how-tos/index.md) for instructions on handling common tasks with LangGraph.

- Manage State
- Tool Integration  
- Human-in-the-Loop
- Async Execution
- Streaming Responses
- Subgraphs & Branching
- Persistence, Visualization, Time Travel 
- Benchmarking

# Concepts

## Graphs

Inspired by [Google's Pregel](https://research.google/pubs/pregel-a-system-for-large-scale-graph-processing/), a `graph` represents a workflow or a set of steps to be executed. The graph consists of [nodes](#nodes) and [edges](#edges) that define the data and control flow.

The main entrypoint for creating a graph is a [`StateGraph`](./reference/graphs.md#StateGraph), which lets you define a [state](#state) machine.

## Nodes

Nodes represent individual units of work to be performed. Each node is associated with a unique key (a string) and a function.

When a node is executed, it receives the current state of the graph as input and returns an update to the state (usually a dictionary or list). The output of the node is then used to update the graph's state according to the schema.

To add a node to a graph, you can use the `add_node` method, specifying the node's key and action.

## Edges

Edges define the graph's control flow. Edges are directed, connecting a start node to one or more end nodes.

There are two types of edges in LangGraph:

1. Normal Edges: These edges represent a direct connection between two nodes. The output of the start node is passed as input to the end node unconditionally. You can add a normal edge using the `add_edge` method.

2. Conditional Edges: These edges allow for conditional branching based on the output of the start node. A `condition` function is passed to the `add_conditional_edges` method and returns the key(s) of the next node(s) to transition to.

## State

The state object represents the mutable components of the graph. The state is defined using a schema (a `type`,  typically a TypedDict or BaseModel class).

The typing annotations of the values in the schema can influence how the graph performs updates. For instance, using `Annotated[list, operator.add]` instructs the graph that this value is _append-only_.

## Persistence

LangGraph defines [checkpointers](./reference/checkpoints) to help you save the state of the graph at any point and resume execution from that point later.

Persistence is useful for several scenarios, such as:


- Time-travel debugging: You can rewind an graph's actions to a previous state, mutate it, then continue to better control the final outcome. 

- Recovering from failures: If the execution of the graph is interrupted due to an error or system failure, you can resume from the last persisted state instead of starting from scratch.

- Human-in-the-loop execution: Your agent can persist its full state, wait indefinitely until a human can weigh in, then resume.


## Why LangGraph?

LangGraph is framework agnostic (each node is a regular python function). It extends the core Runnable API (shared interface for streaming, async, and batch calls) to make it easy to:

- Seamless state management across multiple turns of conversation or tool usage
- The ability to flexibly route between nodes based on dynamic criteria 
- Smooth switching between LLMs and human intervention  
- Persistence for long-running, multi-session applications

If you're building a straightforward DAG, Runnables are a great fit. But for more complex, stateful applications with nonlinear flows, LangGraph is the perfect tool for the job.