Build a small command-line todo manager called `todo.py` in Python 3, in a single file, using only the standard
library. Data should persist in the working directory.

The product's capabilities are specified below in our project-spec format (a subset — runtime metadata fields
are omitted).

```yaml
name: todo
summary: A small command-line todo manager.
capabilities:
  - id: add-items
    title: Add items
    state: planned
    user_story:
      as: user
      want: to capture things to do as they come up
      benefit: I don't have to hold them all in my head
    behaviors:
      - id: add
        description: Add a new item to the list.
        acceptance_criteria:
          - "After adding an item, it appears when I list."
          - "Newly added items are not yet completed."
          - "When I add several items, they show up in the order I added them."

  - id: list-items
    title: List items
    state: planned
    user_story:
      as: user
      want: to see what's still outstanding
      benefit: I know what to work on next
    behaviors:
      - id: list
        description: Show every item.
        acceptance_criteria:
          - "Every item I've added appears in the output."
          - "Items appear in the order they were added."
          - "Each item is numbered, so I can refer to it later."
          - "Completed items are visually distinguishable from open items."
          - "An empty list produces no output."

  - id: complete-items
    title: Mark items complete
    state: planned
    user_story:
      as: user
      want: to mark items done once I've finished them
      benefit: my list reflects where I actually am
    behaviors:
      - id: complete
        description: Mark an item complete by referring to its position in the list.
        acceptance_criteria:
          - "After I mark item N complete, it appears as completed the next time I list."
          - "Referring to a position that doesn't exist (or isn't a number) is treated as an error and reported clearly rather than silently doing nothing."

non_goals:
  - id: no-third-party-deps
    text: External / third-party Python packages
    rationale: Standard library is sufficient for this scope.
  - id: no-external-persistence
    text: Database or network persistence
    rationale: A file in the working directory is sufficient.
```

Output your solution as a single Python code block.
