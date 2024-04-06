# GigaGraph

⚡ Разработка языковых агентов в виде графов ⚡

## Описание

GigaGraph — это библиотека, которая дает возможность работать с большими языковыми моделями (*LLM*) для создания приложений, которые используют множество взаимодействующих цепочек (акторов) и сохраняют данные о состоянии.
Так как в основе GigaGraph лежит [GigaChain](https://github.com/ai-forever/gigachain), предполагается совместное использование обоих библиотек.

Основной сценарий использования GigaGraph — добавление циклов в приложения с LLM. Для этого библиотека добавляет в [LangChain Expression Language](https://python.langchain.com/docs/expression_language/) возможность работать с множеством цепочек на каждой из итераций вычислительного цикла.
Использование циклов позволяет реализовать поведение агента, при котором приложению нужно многократно вызывать LLM и спрашивать, какое действие нужно выполнить следующим.

Следует отметить, что GigaGraph не предназначена для создания ориентированных ациклических графов (*DAG*).
Для решения этой задачи используйте стандартные возможности LangChain Expression Language.

## Установка

Для установки используйте менеджер пакетов pip:

```shell
pip install gigagraph
```

## Быстрый старт

Ниже приводится пример разработки агента, использующего вызов функций и несколько моделей.
Агент отображает каждое свое состояние в виде отдельных сообщений в списке.

Для работы агента потребуется установить некоторые пакеты LangChain и использовать в качестве демонстрации сервис [Tavily](https://app.tavily.com/sign-in):

```shell
pip install -U langchain langchain_openai tavily-python
```

Также для доступа к OpenAI и Tavily API понадобится задать переменные среды:

```shell
export OPENAI_API_KEY=sk-...
export TAVILY_API_KEY=tvly-...
```

При желании вы можете использовать [LangSmith](https://docs.smith.langchain.com/):

```shell
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY=ls__...
```

### Подготовьте инструменты

В первую очередь определите инструменты (`tools`), которые будет использовать приложение.
В качестве примера в этом разделе используется поиск, встроенный в Tavily, но вы также можете использовать собственные инструменты.
Подробнее о том, как создавать свои инструменты — в [документации](https://python.langchain.com/docs/modules/agents/tools/custom_tools).


```python
from langchain_community.tools.tavily_search import TavilySearchResults

tools = [TavilySearchResults(max_results=1)]
```

Оберните инструменты в GigaGraph `ToolExecutor` — класс, который принимает объекты запуска инструмента `ToolInvocation`, вызывает инструмент и возвращает ответ.
Объект `ToolInvocation` — произвольный класс с атрибутами `tool` и `tool_input`.

```python
from langgraph.prebuilt import ToolExecutor

tool_executor = ToolExecutor(tools)
```

### Задайте модель

Подключите модель, которую будет использовать приложение.
Для демонстрации в описываемом примере модель должна:

* поддерживать списки сообщений. Каждое свое состояние агент будет возвращать в виде сообщений, поэтому модель должна хорошо работать со списками сообщений.
* предоставлять интерфейсы вызова функций, аналогичные моделям OpenAI.


```python
from langchain_openai import ChatOpenAI

# Параметр streaming=True включает потоковую передачу токенов
# Подробнее в разделе Потоковая передача.
model = ChatOpenAI(temperature=0, streaming=True)
```

После подключения убедитесь, что модель знает, какие инструменты доступны ей.
Для этого преобразуйте инструменты GigaGraph в формат OpenAI-функций и привяжите их к классу модели.

```python
from langchain.tools.render import format_tool_to_openai_function

functions = [format_tool_to_openai_function(t) for t in tools]
model = model.bind_functions(functions)
```

### Определите состояние агента

Основным графом `gigagraph` является `StatefulGraph`.
Этот граф параметризован объектом состояния, который он передает каждой вершине.
В свою очередь каждая вершина возвращает операции для обновления состояния.
Операции могут либо задавать (SET) определенные атрибуты состояния (например, переписывать существующие значения), либо добавлять ()ADD данные к существующим атрибутам.
Будет операция задавать или добавлять данные, определяется аннотациями объекта состояния, который используется для создания графа.

В приведенном примере отслеживаемое состояние представлено в виде списка сообщений.
Поэтому нужно чтобы каждая вершина добавляла сообщения в список.

Для этого используйте `TypedDict` с одним ключом (`messages`) и аннотацией, указывающей на то, что в атрибут `messages` можно только добавлять данные.

```python
from typing import TypedDict, Annotated, Sequence
import operator
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
```

### Определите вершины графа

Теперь нужно определить несколько разных вершин графа.
В `langgraph` вершина может быть представлена в виде функции или [исполняемого интерфейса](https://python.langchain.com/docs/expression_language/).
Для описываемого примера понадобятся две основных вершины:

* Агент, который принимает решения когда и какие действия нужно выполнять.
* Функция для вызова инструментов. Если агент решает совершить действие, эта вершина его выполнит.

Также нужно определить ребра графа.
Часть ребер могут зависеть от условий (*условные ребра*).
Это связанно с тем, что в зависимости от вывода вершины могут быть реализованы различные пути развития событий.
При этом неизвестно какой путь будет выбран до момента обращения к вершине.
Какой путь выбрать LLM решает самостоятельно.

Разница между обычным и условным ребром графа:

* В случае условного ребра, после вызова агента:

  * если агент решает предпринять действие, нужно вызвать функцию для обращения к инструментам;
  * если агент решает, что действие завершено, операции должны быть прекращены.

* В случае обычного ребра после обращения к инструментам, нужно всегда возвращаться к агенту, чтобы он определил дальнейшие действия.


Определите вершины и функцию, которая будет решать какое из условных ребер выполнять.

```python
from langgraph.prebuilt import ToolInvocation
import json
from langchain_core.messages import FunctionMessage

# Задайте функцию, которая определяет нужно продолжать или нет
def should_continue(state):
    messages = state['messages']
    last_message = messages[-1]
    # Приложение останавливается, если нет вызова функции
    if "function_call" not in last_message.additional_kwargs:
        return "end"
    # В противном случае выполнение продолжается
    else:
        return "continue"

# Задайте функцию, которая будет обращаться к модели
def call_model(state):
    messages = state['messages']
    response = model.invoke(messages)
    # Возвращается список, который будет добавлен к существующему списку сообщений
    return {"messages": [response]}

# Задайте функцию, которая будет вызывать инструменты
def call_tool(state):
    messages = state['messages']
    # Благодаря условию continue
    # приложение знает, что последнее сообщение содержит вызов функции
    last_message = messages[-1]
    # Создание ToolInvocation из function_call
    action = ToolInvocation(
        tool=last_message.additional_kwargs["function_call"]["name"],
        tool_input=json.loads(last_message.additional_kwargs["function_call"]["arguments"]),
    )
    # Вызов tool_executor и получение ответа
    response = tool_executor.invoke(action)
    # Использование ответа для создания сообщения FunctionMessage
    function_message = FunctionMessage(content=str(response), name=action.tool)
    # Возвращение списка, который будет добавлен к существующему списку сообщений
    return {"messages": [function_message]}
```

### Определите граф

```python
from langgraph.graph import StateGraph, END
# Задайте новый граф
workflow = StateGraph(AgentState)

# Задайте две вершины, которые будут работать в цикле
workflow.add_node("agent", call_model)
workflow.add_node("action", call_tool)

# Задайте точку входа `agent`
# Точка входа указывает вершину, котора будет вызвана в первую очередь
workflow.set_entry_point("agent")

# Создайте условное ребро
workflow.add_conditional_edges(
    # Определите начальную вершину. В этом примере используется вершина `agent`.
    # Это задает ребра, которые будут использованы после вызова вершины `agent`.
    "agent",
    # Передайте функцию, которая определяет какую вершину вызвать дальше.
    should_continue,
    # Передайте структуру (map), в которой ключами будут строки, а значениями другие вершины.
    # END — зарезервированная вершина, указываящая на то, что граф должен завершиться.
    # После вызова `should_continue` вывод функции сравнивается с ключами в структуре.
    # После чего вызывается соотвествующая выводу вершина.
    {
        # If `tools`, then we call the tool node.
        # Если значение `tools`, вызывается вершина, ответственная за обращение к интрсументам.
        "continue": "action",
        # В противном случае граф заканчивается.
        "end": END
    }
)

# Добавьте обычное ребро, соединяющее вершины `tools` и `agent`.
# Ребро задает путь при котором после вызова вершины `tools`, вызывается вершина `agent`.
workflow.add_edge('action', 'agent')

# Скомпилируйте все предыдущие этапы в исполняемый интерфейс GigaChain.
# Теперь граф можно использовать также, как и другие исполняемые интерфейсы.
app = workflow.compile()
```

### Использование

Скомпилированный исполняемый интерфейс принимает на вход список сообщений:

```python
from langchain_core.messages import HumanMessage

inputs = {"messages": [HumanMessage(content="what is the weather in sf")]}
app.invoke(inputs)
```

Работа интерфейса занимает некоторое время.
Чтобы наблюдать за результатом работы в прямом эфире, вы можете включить потоковую передачу.

## Потоковая передача

GigaGraph поддерживает несколько разных способов потоковой передачи.

### Потоковая передача вывода вершины

GigaGraph предоставляет возможность потоковой передачи результата вызова каждой из вершин графа по мере обращения к ним.

```python
inputs = {"messages": [HumanMessage(content="what is the weather in sf")]}
for output in app.stream(inputs):
    # stream() возвращает словари с парами `Вершина графа — вывод`
    for key, value in output.items():
        print(f"Output from node '{key}':")
        print("---")
        print(value)
    print("\n---\n")
```

```python
Output from node 'agent':
---
{'messages': [AIMessage(content='', additional_kwargs={'function_call': {'arguments': '{\n  "query": "weather in San Francisco"\n}', 'name': 'tavily_search_results_json'}})]}

---

Output from node 'action':
---
{'messages': [FunctionMessage(content="[{'url': 'https://weatherspark.com/h/m/557/2024/1/Historical-Weather-in-January-2024-in-San-Francisco-California-United-States', 'content': 'January 2024 Weather History in San Francisco California, United States  Daily Precipitation in January 2024 in San Francisco Observed Weather in January 2024 in San Francisco  San Francisco Temperature History January 2024 Hourly Temperature in January 2024 in San Francisco  Hours of Daylight and Twilight in January 2024 in San FranciscoThis report shows the past weather for San Francisco, providing a weather history for January 2024. It features all historical weather data series we have available, including the San Francisco temperature history for January 2024. You can drill down from year to month and even day level reports by clicking on the graphs.'}]", name='tavily_search_results_json')]}

---

Output from node 'agent':
---
{'messages': [AIMessage(content="I couldn't find the current weather in San Francisco. However, you can visit [WeatherSpark](https://weatherspark.com/h/m/557/2024/1/Historical-Weather-in-January-2024-in-San-Francisco-California-United-States) to check the historical weather data for January 2024 in San Francisco.")]}

---

Output from node '__end__':
---
{'messages': [HumanMessage(content='what is the weather in sf'), AIMessage(content='', additional_kwargs={'function_call': {'arguments': '{\n  "query": "weather in San Francisco"\n}', 'name': 'tavily_search_results_json'}}), FunctionMessage(content="[{'url': 'https://weatherspark.com/h/m/557/2024/1/Historical-Weather-in-January-2024-in-San-Francisco-California-United-States', 'content': 'January 2024 Weather History in San Francisco California, United States  Daily Precipitation in January 2024 in San Francisco Observed Weather in January 2024 in San Francisco  San Francisco Temperature History January 2024 Hourly Temperature in January 2024 in San Francisco  Hours of Daylight and Twilight in January 2024 in San FranciscoThis report shows the past weather for San Francisco, providing a weather history for January 2024. It features all historical weather data series we have available, including the San Francisco temperature history for January 2024. You can drill down from year to month and even day level reports by clicking on the graphs.'}]", name='tavily_search_results_json'), AIMessage(content="I couldn't find the current weather in San Francisco. However, you can visit [WeatherSpark](https://weatherspark.com/h/m/557/2024/1/Historical-Weather-in-January-2024-in-San-Francisco-California-United-States) to check the historical weather data for January 2024 in San Francisco.")]}

---
```

### Потоковая передача токенов

Библиотека дает доступ к потоковой передаче токенов модели по мере их возникновения на каждой из вершин.
В приведенном примере только вершина `agent` может возвращать токены модели.

Функциональность доступна при работе с моделями, которые поддерживают потоковую передачу.

```python
inputs = {"messages": [HumanMessage(content="what is the weather in sf")]}
async for output in app.astream_log(inputs, include_types=["llm"]):
    # astream_log() yields the requested logs (here LLMs) in JSONPatch format
    for op in output.ops:
        if op["path"] == "/streamed_output/-":
            # this is the output from .stream()
            ...
        elif op["path"].startswith("/logs/") and op["path"].endswith(
            "/streamed_output/-"
        ):
            # because we chose to only include LLMs, these are LLM tokens
            print(op["value"])
```

```python
content='' additional_kwargs={'function_call': {'arguments': '', 'name': 'tavily_search_results_json'}}
content='' additional_kwargs={'function_call': {'arguments': '{\n', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': ' ', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': ' "', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': 'query', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': '":', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': ' "', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': 'weather', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': ' in', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': ' San', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': ' Francisco', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': '"\n', 'name': ''}}
content='' additional_kwargs={'function_call': {'arguments': '}', 'name': ''}}
content=''
content=''
content='I'
content="'m"
content=' sorry'
content=','
content=' but'
content=' I'
content=' couldn'
content="'t"
content=' find'
content=' the'
content=' current'
content=' weather'
content=' in'
content=' San'
content=' Francisco'
content='.'
content=' However'
content=','
content=' you'
content=' can'
content=' check'
content=' the'
content=' historical'
content=' weather'
content=' data'
content=' for'
content=' January'
content=' '
content='202'
content='4'
content=' in'
content=' San'
content=' Francisco'
content=' ['
content='here'
content=']('
content='https'
content='://'
content='we'
content='athers'
content='park'
content='.com'
content='/h'
content='/m'
content='/'
content='557'
content='/'
content='202'
content='4'
content='/'
content='1'
content='/H'
content='istorical'
content='-'
content='Weather'
content='-in'
content='-Jan'
content='uary'
content='-'
content='202'
content='4'
content='-in'
content='-S'
content='an'
content='-F'
content='r'
content='anc'
content='isco'
content='-Cal'
content='ifornia'
content='-'
content='United'
content='-'
content='States'
content=').'
content=''
```

## Область применения

Используйте библиотеку когда вам нужна поддержка циклов.

Если для решения ваших задач достаточно обычных цепочек, используйте основные возможности [LangChain Expression Language](https://python.langchain.com/docs/expression_language/).

## Руководства

Приведенные руководства демонстрируют сценарии использования GigaGraph.

### Асинхронная работа

При работе с асинхронными процессами вам может потребоваться создать с помощью GigaGraph граф с вершинами, которые будут асинхронными по умолчанию.
[Пример](https://github.com/langchain-ai/langgraph/blob/main/examples/async.ipynb).

### Потоковая передача токенов

Ответ модели может занимать продолжительное время и вам может потребоваться на лету отображать пользователям результат работы модели.
[Пример](https://github.com/langchain-ai/langgraph/blob/main/examples/streaming-tokens.ipynb).

### Устойчивость

GigaGraph позволяет сохранять состояние графа в определенный момент времени и потом возобновлять работу с этого состояния.
[Пример](https://github.com/langchain-ai/langgraph/blob/main/examples/persistence.ipynb).

### Человек-в-цикле

GigaGraph поддерживает процесс, при котором необходимо участие человека, проверяющего текущее состояние графа перед переходом к следующей вершине.
Пример такого подхода — в [документации](https://github.com/langchain-ai/langgraph/blob/main/examples/human-in-the-loop.ipynb).

## Примеры

### Исполнитель чат-агента с возможностью вызывать функции

Пример приложения-исполнителя принимает на вход список сообщений и возвращает список сообщений на выходе.
Состояние агента также представлено в виде списка сообщений.
Представленный пример использует вызов функций OpenAI.

- [Getting Started Notebook](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/base.ipynb). Базовый пример, демонстрирующий пошаговое создание приложения исполнителя агентов.
- [High Level Entrypoint](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/high-level.ipynb). Пример демонстрирует как можно использовать высокоуровневую точку входа для исполнителя чат-агента.

**Вариации примеров**

Примеры небольших изменений, которые можно сделать при разработке исполнителя чат-агента с возможностью вызвать функции.
Приведенные вариации основаны на примере [Getting Started Notebook](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/base.ipynb).

- [Human-in-the-loop](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/human-in-the-loop.ipynb). Пример демонстрирует, как реализовать подход «человек-в-цикле».
- [Принудительный вызов инструмента](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/force-calling-a-tool-first.ipynb). Пример демонстрирует, как всегда вызывать определенный инструмент в первую очередь.
- [Ответ в заданном формате](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/respond-in-format.ipynb). Пример демонстрирует, как принудительно получить ответ агента в заданном формате.
- [Динамический вывод результата использования инструмента](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/dynamically-returning-directly.ipynb). Пример демонстрирует, как агент может самостоятельно решать возвращать результат использования инструмента пользователю или нет.
- [Управление этапами работы агента](https://github.com/langchain-ai/langgraph/blob/main/examples/chat_agent_executor_with_function_calling/managing-agent-steps.ipynb). Пример демонстрирует, как можно более детально управлять промежуточными этапами работы агента.

### Исполнители агентов

Примеры приложений-исполнителей, использующих агенты LangChain.

- [Getting Started Notebook](https://github.com/langchain-ai/langgraph/blob/main/examples/agent_executor/base.ipynb). Базовый пример, демонстрирующий пошаговое создание приложения исполнителя агентов.
- [High Level Entrypoint](https://github.com/langchain-ai/langgraph/blob/main/examples/agent_executor/high-level.ipynb). Пример демонстрирует как можно использовать высокоуровневую точку входа для исполнителя чат-агента.

**Вариации примеров**

Примеры небольших изменений, которые можно сделать при разработке исполнителя чат-агента.
Приведенные вариации основаны на примере [Getting Started Notebook](https://github.com/langchain-ai/langgraph/blob/main/examples/agent_executor/base.ipynb).

- [Human-in-the-loop](https://github.com/langchain-ai/langgraph/blob/main/examples/agent_executor/human-in-the-loop.ipynb). Пример демонстрирует, как реализовать подход «человек-в-цикле».
- [Принудительный вызов инструмента](https://github.com/langchain-ai/langgraph/blob/main/examples/agent_executor/force-calling-a-tool-first.ipynb). Пример демонстрирует, как всегда вызывать определенный инструмент в первую очередь.
- [Управление этапами работы агента](https://github.com/langchain-ai/langgraph/blob/main/examples/agent_executor/managing-agent-steps.ipynb). Пример демонстрирует, как можно более детально управлять промежуточными этапами работы агента.

### Агент-планировщик

Блокноты содержат примеры прототипов архитектуры «планируй и выполняй».
При таком подходе LLM декомпозирует запрос пользователя в программу, после чего исполнитель выполняет ее и в завершении LLM, в зависимости от результата, собирает ответ и/или динамически изменяет план работы.

- [Планирование и выполнение](https://github.com/langchain-ai/langgraph/blob/main/examples/plan-and-execute/plan-and-execute.ipynb). Пример простого агента, состоящий из: планировщика, который создает многоэтапный список задач, исполнителя, который вызывает доступные инструменты согласно плану, и перепланирощика, который возвращает результат работы или обновляет план действий. Пример основан на работе [Plan-and-solve](https://arxiv.org/abs/2305.04091), за авторством Wang, и других.
- [Рассуждения без наблюдений](https://github.com/langchain-ai/langgraph/blob/main/examples/rewoo/rewoo.ipynb). Пример планировщика, создающего список задач, наблюдения в котором сохраняются как переменные. Переменные могут быть использованы в последующих задачах, чтобы уменьшить необходимость в дальнейшем перепланировании. Пример основан на работе [ReWOO](https://arxiv.org/abs/2305.18323), за авторством Xu и других.
- [LLM компилятор](https://github.com/langchain-ai/langgraph/blob/main/examples/llm-compiler/LLMCompiler.ipynb). Задачи передаются потоком и выполняются с целью уменьшения время выполнения инструмента. Пример планировщика, который создает ациклический граф (*DAG*) задач с переменными ответами. Пример основан на [работе](https://arxiv.org/abs/2312.04511) за авторством Kim и других.

### Размышления / самокритика

Для повышения качества работы агента, как правило используют сочетание «самокритики» или «размышлений» языковой модели, а также внешний контроль результатов работы системы. Представленные примеры демонстрируют исследования, которые реализуют такой подход в проектировании.

- [Общие размышления](https://github.com/ai-forever/gigagraph/blob/main/examples/reflection/reflection.ipynb). Пример показывает, как добавить в граф шаг простых «размышлений», подсказывающий системе пересмотреть результат работы.
- [Рефлексия](https://github.com/ai-forever/gigagraph/blob/main/examples/reflexion/reflexion.ipynb). Пример показывает, как критиковать недостающие и избыточные части ответа агента, чтобы направлять последующие шаги. Пример основан на работе [Reflexion](https://arxiv.org/abs/2303.11366) за авторством Shinn и других.
- [Поиск по дереву с помощью языкового агента](https://github.com/ai-forever/gigagraph/blob/main/examples/lats/lats.ipynb). Пример показывает параллельную работу нескольких агентов, с применением «размышлений» и внешнего положительного подкрепления для организации поиска по дереву Монте-Карло. Пример основан на работе [LATS](https://arxiv.org/abs/2310.04406/LanguageAgentTreeSearch/) за авторством Zhou и других.

### Примеры с несколькими агентами

- [Совместная работа нескольких агентов](https://github.com/langchain-ai/langgraph/blob/main/examples/multi_agent/multi-agent-collaboration.ipynb). Пример демонстрирует, как создать двух агентов, которые работают вместе для решения задачи.
- [Несколько агентов с «руководителем»](https://github.com/langchain-ai/langgraph/blob/main/examples/multi_agent/agent_supervisor.ipynb). Пример демонстрирует, как организовать работу агентов используя LLM в роли «руководителя», который решает как распределять работу.
- [Иерархичные команды агентов](https://github.com/langchain-ai/langgraph/blob/main/examples/multi_agent/hierarchical_agent_teams.ipynb). Пример демонстрирует, как организовать «команды» агентов, которые будут взаимодействовать для решения задачи, в виде вложенных графов.

### Симуляция для оценки чат-бота

Оценка работы чат-бота в многоэтапных сценариях может вызывать трудности. Для решения таких задач вы можете использовать симуляции.

- [Оценка чат-бота с помощью симуляции взаимодействия нескольких агентов](https://github.com/langchain-ai/langgraph/blob/main/examples/chatbot-simulation-evaluation/agent-simulation-evaluation.ipynb). В примере показано как симулировать диалог «виртуального пользователя» с чат-ботом.

## Справка

GigaGraph предоставляет доступ к нескольким новым интерфейсам.

### StateGraph

Основная точка входа — класс `StateGraph`.

```python
from langgraph.graph import StateGraph
```

Класс ответственный за создание графа.
Этот граф параметризован объектом состояния, который он передает каждой вершине.

#### `__init__`

```python
    def __init__(self, schema: Type[Any]) -> None:
```

При создании графа нужно передать схему состояния.
Каждая вершина будет возвращать операции для обновления этого состояния.
Операции могут либо задавать (SET) определенные атрибуты состояния (например, переписывать существующие значения), либо добавлять (ADD) данные к существующим атрибутам.
Будет операция задавать или добавлять данные, определяется аннотациями объекта состояния, который используется для создания графа.

Схему состояния рекомендуется задавать с помощью типизированного словаря: `from typing import TypedDict`

После создания схемы вы можете аннотировать атрибуты с помощью `from typing imoport Annotated`.
Сейчас поддерживается только одна аннотация — `import operator; operator.add`.
Аннотация указывает, что каждая вершина, которая возвращает этот атрибут, добавляет новые данные к существующему значению.

Пример состояния:

```python
from typing import TypedDict, Annotated, Union
from langchain_core.agents import AgentAction, AgentFinish
import operator


class AgentState(TypedDict):
   # Входная строка
   input: str
   # Результат вызова агента
   # Должен принимать `None` в качестве валидного типа, так как это начальное значение
   agent_outcome: Union[AgentAction, AgentFinish, None]
   # Список действий и соответвтующих шагов
   # Аннотация `operator.add` указывает что состояние должно дополняться (ADD) новыми данными,
   # а не перезаписываться
   intermediate_steps: Annotated[list[tuple[AgentAction, str]], operator.add]

```

Пример использования:

```python
# Инициализируйте StateGraph с помощью состояния AgentState
graph = StateGraph(AgentState)
# Создайте вершины и ребра
...
# Скомпилируйте граф
app = graph.compile()

# На вход должен передаваться словарь, так как состояние создано как TypedDict
inputs = {
   # Пример входный данныъ
   "input": "hi"
   # Предположим, что `agent_outcome` задается графом как некоторая точка
   # Передавать значение не нужно, по умолчанию оно будет None
   # Предположим, что граф со временем наполняет `intermediate_steps`
   # Передавать значение не нужно, по умолчанию список будет пустым
   # Список `intermediate_steps` будет представлен в виде пустого списка, а не None потому,
   # что он аннотирован с помощью `operator.add`
}
```

#### `.add_node`

```python
    def add_node(self, key: str, action: RunnableLike) -> None:
```

Добавляет вершину графа.
Принимает два параметра:

* `key` — Уникальная строка с названием вершины.
* `action` — действие, которое выполняется при вызове вершины. Выражается в виде функции или исполняемого интерфейса.

#### `.add_edge`

```python
    def add_edge(self, start_key: str, end_key: str) -> None:
```

Создает ребро графа, соединяющее начальную и конечную вершины.
Вывод начальной вершины передается в конечную.
Принимает два параметра:

- `start_key` — строка с названием начальной вершины. Название вершины должно быть зарегистрировано в графе.
- `end_key` — строка с названием конечной вершины. Название вершины должно быть зарегистрировано в графе.

#### `.add_conditional_edges`

```python
    def add_conditional_edges(
        self,
        start_key: str,
        condition: Callable[..., str],
        conditional_edge_mapping: Dict[str, str],
    ) -> None:
```

Создает условное ребро.
Позволяет задавать пути развития событий в зависимости от результата вызова начальной вершины.
Принимает три параметра:

- `start_key` — строка с названием начальной вершины. Название вершины должно быть зарегистрировано в графе.
- `condition` — функция, которая вызывается для определения пути развития событий. На вход принимает результат вызова начальной вершины. Возвращает строку, зарегистрированную в структуре `conditional_edge_mapping`, которая указывает в соответствии с каким ребром будут развиваться события.
- `conditional_edge_mapping` — структура (map) строка-строка. В качестве ключа задается название ребра, которое может вернуть `condition`. В качестве значения задается вершина, которые будет вызваны если `condition` вернет соответствующее название ребра.

#### `.set_entry_point`

```python
    def set_entry_point(self, key: str) -> None:
```
Точка входа в граф.
Задает вершину, которая будет вызвана в самом начале.
Принимает один параметр:

- `key` — название вершины, которую нужно вызывать в первую очередь.

#### `.add_conditional_edges`

```python
    def set_conditional_entry_point(
        self,
        condition: Callable[..., str],
        conditional_edge_mapping: Optional[Dict[str, str]] = None,
    ) -> None:
```

Добавляет условную точку входа.
При вызове графа метод проверяет условие (`condition: Callable[..., str],`), чтобы выбрать начальную вершину.

- `condition`. Функция, которую нужно вызвать, чтобы решить, что делать дальше. Входные данные функции используются при запуске графа. Функция должна возвращать строку из `conditional_edge_mapping`, указывающую на ребро, по которому пойдет выполнение графа.
- `conditional_edge_mapping`. Структура данных строка-строка. В качестве ключей задаются строки, которые может вернуть `condition`. В качестве значения задается вершина, которая будет вызвана при срабатывании условия.


#### `.set_finish_point`

```python
    def set_finish_point(self, key: str) -> None:
```

Точка выхода из графа.
При вызове заданной вершины, результат ее работы будет итоговым для графа.
Принимает один параметр:

- `key` — название вершины, результат вызова который будет считаться итоговым результатом работы графа.

Вершину не нужно вызывать, если на предыдущих шагах графа было создано ребро (условное или обычное), ведущее к зарезервированной вершине `END`.

### Graph

```python
from langgraph.graph import Graph

graph = Graph()
```

Класс предоставляет доступ к интерфейсу `StateGraph`, но отличается тем, что объект состояния не обновляется со временем, а класс передает все состояние целиком на каждом этапе.
Это означает, что данные, которые возвращаются в результате работы одной вершины, передаются на вход при вызове другой вершины в исходном состоянии.

### `END`

```python
from langgraph.graph import END
```

Зарезервированная вершина указывающая на завершение работы графа.
Все данные, которые передаются вершине при вызове, будут считаться результатом работы графа.
Вершину можно использовать в двух случая:

- В качестве ключа `end_key` в `add_edge`.
- В качестве значения в структуре `conditional_edge_mapping`, передаваемой `add_conditional_edges`.

## Готовые примеры

Представленные примеры содержат несколько методов, облегчающих работу с распространенными, готовыми графами и компонентами.

### ToolExecutor

```python
from langgraph.prebuilt import ToolExecutor
```

Вспомогательный класс для вызова инструментов.
В качестве параметров класс принимает список инструментов.

```python
tools = [...]
tool_executor = ToolExecutor(tools)
```

После инициализации класс дает доступ к [исполняемому интерфейсу](https://python.langchain.com/docs/expression_language/interface).
Используйте класс для вызова инструментов. Передайте [AgentAction](https://python.langchain.com/docs/modules/agents/concepts#agentaction) для автоматического определения подходящего инструмента и входных данных.

### chat_agent_executor.create_function_calling_executor

```python
from langgraph.prebuilt import chat_agent_executor
```

Вспомогательная функция для создания графа, который работает с генеративной моделью и может вызывать функции.
Для использования функции передайте на вход модель и список инструментов.
Модель должна поддерживать интерфейс вызова функций аналогичный OpenAI.

```python
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults
from langgraph.prebuilt import chat_agent_executor
from langchain_core.messages import HumanMessage

tools = [TavilySearchResults(max_results=1)]
model = ChatOpenAI()

app = chat_agent_executor.create_function_calling_executor(model, tools)

inputs = {"messages": [HumanMessage(content="какая погода в саратове")]}
for s in app.stream(inputs):
    print(list(s.values())[0])
    print("----")
```

### create_tool_calling_executor

```python
from langgraph.prebuilt import chat_agent_executor
```

Вспомогательная функция для создания графа, который работает с моделью, способной вызывать инструменты.
Принимает на вход модель и список инструментов.
Модель должна поддерживать вызов инструментов OpenAI.

```python
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults
from langgraph.prebuilt import chat_agent_executor
from langchain_core.messages import HumanMessage

tools = [TavilySearchResults(max_results=1)]
model = ChatOpenAI()

app = chat_agent_executor.create_tool_calling_executor(model, tools)

inputs = {"messages": [HumanMessage(content="what is the weather in sf")]}
for s in app.stream(inputs):
    print(list(s.values())[0])
    print("----")
```

### create_agent_executor

```python
from langgraph.prebuilt import create_agent_executor
```

Вспомогательная функция для работы с [агентами LangChain](https://python.langchain.com/docs/modules/agents/).
Для использования функции передайте на вход агента и список инструментов.

```python
from langgraph.prebuilt import create_agent_executor
from langchain_openai import ChatOpenAI
from langchain import hub
from langchain.agents import create_openai_functions_agent
from langchain_community.tools.tavily_search import TavilySearchResults

tools = [TavilySearchResults(max_results=1)]

# Подключите шаблон промпта. Вы можете выбрать любой шаблон
prompt = hub.pull("hwchase17/openai-functions-agent")

# Выберите модель, с которой будет работать агент
llm = ChatOpenAI(model="gpt-3.5-turbo-1106")

# Создайте агента OpenAI Functions
agent_runnable = create_openai_functions_agent(llm, tools, prompt)

app = create_agent_executor(agent_runnable, tools)

inputs = {"input": "what is the weather in sf", "chat_history": []}
for s in app.stream(inputs):
    print(list(s.values())[0])
    print("----")
```
