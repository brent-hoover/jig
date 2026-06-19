# A simple, CLI-based todo list manager

## Requirements

1. A user should be able to add a task from the command line
2. The task should be able to include name, description, optional due date and priority 1-3 where 1 is the highest
3. A user should be able to view a list of all todo items
4. A user should be able to filter that list by due date or priority or sort it by due date or priority
5. A user should be able to mark a task complete, and it should be removed from the task list (but not deleted)
6. A user should be able to delete a task
7. A user should be able to update the values for any field on the task

## Validation steps

1. Add a task with a priority of 1
2. Add a second task with a due date of tomorrow
3. View the list of tasks, observe that you can see the name, date and priority
4. Mark the first task as completed
5. View the list and observe that it no longer shows up
6. Update the second task so that its due date is next Monday
7. View the list and observe that the due date has changed
8. Delete that task
9. Observe that the task list is now empty
