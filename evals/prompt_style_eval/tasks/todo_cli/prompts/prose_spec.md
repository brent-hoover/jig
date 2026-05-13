Build a CLI program called `todo.py` that manages a list of todo items. It should be written in Python 3, live
in a single file, persist its data in the current working directory in any file format you choose, and use only
the Python standard library — no third-party packages.

The program supports three subcommands.

The first is `add`. As a user I want to add a new todo item from the command line so I can capture things to do
as they come up. Running `todo.py add <text>` appends `<text>` as a new open item to the end of the list. On
success, stdout is empty and the exit code is 0.

The second is `list`. As a user I want to see all my todos at the command line so I can review what I still
need to do. Running `todo.py list` prints every item, one per line, in insertion order with 1-based numbering.
Open items appear as `<n>. <text>`. Completed items appear as `<n>. [x] <text>`. An empty list prints nothing.
Exit code is 0.

The third is `done`. As a user I want to mark a todo as done so I can keep track of what's left. Running
`todo.py done <n>` marks the item at position `n` (1-based) as complete. On success, stdout is empty and the
exit code is 0. If `n` is non-integer or out of range, print a message to stderr and exit with a non-zero code.

Out of scope: third-party Python packages (the standard library is sufficient) and any database or network
persistence (a file in the working directory is sufficient).

Output your solution as a single Python code block.
