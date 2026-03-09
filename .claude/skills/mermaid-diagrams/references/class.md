# UML Class Diagram Reference

## When to Use

- Object-oriented design and type hierarchies
- Interface definitions and implementations
- Design pattern documentation (adapter, strategy, observer)
- Pydantic model relationships (inheritance, composition)
- API contract visualization

## Canonical Example

```mermaid
classDiagram
    class AgentEngine {
        <<interface>>
        +engine_id: str
        +propose(context_packet, agent_profile) Plan
        +revise(plan, observations) Plan
    }

    class CustomEngine {
        -llm_service: LLMService
        -prompt_builder: PromptBuilder
        +engine_id: str
        +propose(context_packet, agent_profile) Plan
        +revise(plan, observations) Plan
        -_build_messages(packet, profile) list
    }

    class LangGraphAdapter {
        -graph: StateGraph
        +engine_id: str
        +propose(context_packet, agent_profile) Plan
    }

    class ContextPacket {
        +packet_id: str
        +intent: str
        +items: list~ContextItem~
        +budget: ContextBudget
        +generated_at: datetime
    }

    class Plan {
        +plan_id: str
        +intent: str
        +summary: str
        +actions: list~ActionRequest~
        +risk: RiskAssessment
        +context_refs_used: list~ContextRef~
    }

    class ActionRequest {
        +action_id: str
        +capability_name: str
        +args: dict
        +side_effect: SideEffect
        +requires_approval: bool
    }

    AgentEngine <|.. CustomEngine : implements
    AgentEngine <|.. LangGraphAdapter : implements
    AgentEngine ..> ContextPacket : receives
    AgentEngine ..> Plan : produces
    Plan *-- ActionRequest : contains
```

## Syntax Quick Reference

### Visibility Modifiers

| Symbol | Meaning |
|--------|---------|
| `+` | Public |
| `-` | Private |
| `#` | Protected |
| `~` | Package/internal |

### Method Syntax

```
+method_name(param1, param2) ReturnType
-_private_helper() void
#protected_method(arg: str) bool
+static_method()$ ReturnType     %% $ = static
+abstract_method()* ReturnType   %% * = abstract
```

### Stereotypes

```mermaid
class MyInterface {
    <<interface>>
}

class MyAbstract {
    <<abstract>>
}

class MyEnum {
    <<enumeration>>
    VALUE_A
    VALUE_B
}

class MyService {
    <<service>>
}
```

### Relationship Arrows

| Syntax | Type | Use for |
|--------|------|---------|
| `A <\|-- B` | Inheritance | B extends A |
| `A <\|.. B` | Implementation | B implements interface A |
| `A *-- B` | Composition | A contains B (strong ownership) |
| `A o-- B` | Aggregation | A has B (weak ownership) |
| `A --> B` | Association | A uses B |
| `A ..> B` | Dependency | A depends on B (dashed) |
| `A -- B` | Link | Unspecified association |

### Cardinality

```mermaid
A "1" --> "*" B : contains
A "0..1" --> "1..*" B : references
```

### Generics

Use `~T~` syntax (NOT `<T>` which breaks Mermaid parsing):

```mermaid
class Repository~T~ {
    +get(id: str) T
    +list() list~T~
    +save(item: T) void
}
```

### Annotations and Notes

```mermaid
class MyClass {
    +field: str
}
note for MyClass "This is a note\nthat spans lines"
```

## Common Patterns

### 1. Interface + Implementations

```mermaid
classDiagram
    class Store {
        <<interface>>
        +get(id: str) dict
        +put(id: str, data: dict) void
        +delete(id: str) void
    }

    class SQLiteStore {
        -db_path: str
        -connection: Connection
        +get(id: str) dict
        +put(id: str, data: dict) void
        +delete(id: str) void
    }

    class PostgresStore {
        -conn_string: str
        -pool: Pool
        +get(id: str) dict
        +put(id: str, data: dict) void
        +delete(id: str) void
    }

    Store <|.. SQLiteStore
    Store <|.. PostgresStore
```

### 2. Composition

```mermaid
classDiagram
    class DecisionTrace {
        +trace_id: str
        +plan: Plan
        +tool_calls: list~ToolCallRecord~
        +outcome: Outcome
    }

    class Plan {
        +plan_id: str
        +actions: list~ActionRequest~
    }

    class ToolCallRecord {
        +tool_call_id: str
        +status: CallStatus
    }

    class Outcome {
        +status: OutcomeStatus
        +artifacts: list~ContextRef~
    }

    DecisionTrace *-- Plan
    DecisionTrace *-- ToolCallRecord
    DecisionTrace *-- Outcome
```

### 3. Adapter Pattern

```mermaid
classDiagram
    class ToolAdapter {
        <<interface>>
        +execute(action: ActionRequest) ToolCallRecord
    }

    class LocalFunctionAdapter {
        -registry: dict
        +execute(action) ToolCallRecord
    }

    class HTTPAdapter {
        -base_url: str
        -client: HttpClient
        +execute(action) ToolCallRecord
    }

    class ToolBroker {
        -adapters: dict~str, ToolAdapter~
        +register(name, adapter) void
        +execute(action, profile) ToolCallRecord
    }

    ToolAdapter <|.. LocalFunctionAdapter
    ToolAdapter <|.. HTTPAdapter
    ToolBroker o-- ToolAdapter : uses
```

## Pitfalls

### Generics Use `~T~` Not `<T>`

Angle brackets break Mermaid's parser. Always use tildes:

```
%% BAD — parse error
class List<T> {
    +get(index: int) T
}

%% GOOD
class List~T~ {
    +get(index: int) T
}
```

### classDef Doesn't Work

Class diagrams do **not** support `classDef` styling. You cannot color
individual classes. If you need colors, consider a flowchart with class-shaped
nodes. Use stereotypes (`<<interface>>`, `<<abstract>>`) for visual
differentiation instead.

### Method Parentheses

Methods require parentheses even with no parameters:

```
%% BAD — treated as attribute
+getName str

%% GOOD
+getName() str
```

### Large Class Diagrams

- Show only fields/methods relevant to the design point you're making
- Omit getters/setters and boilerplate methods
- Group related classes; don't show the entire codebase
- For Pydantic models, show key fields not all inherited methods

### Relationship Labels

Add labels to clarify non-obvious relationships:

```mermaid
Engine ..> Plan : "produces"
Executor ..> Plan : "validates"
```

Without labels, it's unclear what the association means.
