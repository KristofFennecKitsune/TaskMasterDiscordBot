This bot requires a server to run on. It uses 2 JSON files that it creates for everything. One is for all the current tasks, and the other is an audit log where nothing is deleted. Each new task is given an ID that is permanently tied to it.

There are 8 commands (and a /ping command for confirming the bot is online and will respond)

/task - Pulls up the details about a task, requires a task ID

/tasklist - Pulls up all tasks and suggestions and groups them by priority level.

/mytasklist - Like /tasklist, but filters for only tasks that the sender is assigned to

/taskcreate - Creates a task and assigns it an ID. Requires a description, priority level, target date in YYYY-MM-DD format, and at least one person assigned to the task. (However, up to 3 people may be assigned)

/tasksuggestcreat - Like /taskcreate, but there's only room for a description. It will also assign it an ID.

/taskcomment - Requires a task ID. Allows a comment to be left on a task

/taskdetailupdate - Requires a task ID. Allows for an assignee, the target date, or the priority level to be changed. 

/taskdelete - Requires a task ID and a reason for deletion. Deletes a task from the task list, but not from the audit log