Build a CLI program `todo.py` in Python 3, in a single file, using only the Python standard library. Persist
data in the current working directory in any file format you choose.

The product's capabilities are specified below in our project-spec format (a subset — runtime metadata fields are
omitted).

```yaml
name: todo
summary: A small command-line todo manager.
capabilities:
  - id: add-todo
    title: Add a todo item
    state: planned
    user_story:
      as: user
      want: to add a new todo item from the command line
      benefit: I can capture things to do as they come up
    behaviors:
      - id: add-command
        description: |
          Append a new item with the given text to the end of the list.
        acceptance_criteria:
          - "Running `todo.py add <text>` appends `<text>` as a new open item."
          - "On success, stdout is empty and the exit code is 0."

  - id: list-todos
    title: List todos
    state: planned
    user_story:
      as: user
      want: to see all my todos at the command line
      benefit: I can review what I still need to do
    behaviors:
      - id: list-command
        description: |
          Print every item, one per line, in insertion order with 1-based
          numbering. Open items use the format `<n>. <text>`. Completed
          items use the format `<n>. [x] <text>`. An empty list prints
          nothing.
        acceptance_criteria:
          - "Running `todo.py list` prints every item, one per line, numbered from 1."
          - "Open items appear as `<n>. <text>`."
          - "Completed items appear as `<n>. [x] <text>`."
          - "An empty list prints nothing."
          - "Exit code is 0."

  - id: complete-todo
    title: Mark a todo done
    state: planned
    user_story:
      as: user
      want: to mark a todo as done
      benefit: I can keep track of what's left
    behaviors:
      - id: done-command
        description: |
          Mark the item at the given 1-based index as complete.
        acceptance_criteria:
          - "Running `todo.py done <n>` marks the item at position `n` (1-based) as complete."
          - "On success, stdout is empty and the exit code is 0."
          - "If `n` is non-integer or out of range, print a message to stderr and exit with a non-zero code."

non_goals:
  - id: no-third-party-deps
    text: External / third-party Python packages
    rationale: Standard library is sufficient for this scope.
  - id: no-external-persistence
    text: Database or network persistence
    rationale: A file in the working directory is sufficient.
```

Output your solution as a single Python code block.
