import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta

# --- CENTRALIZED CONFIGURATION ---
PUBLIC_CHANNELS = [
    0 # By default, the bot will respond privately with messages. If you want the bot to openly reply in certain text channel,
      # then list the channels here
]
SUPER_USER_ID = 0 # This is the user who is desiganated for maintinace and upkeep, replace with their user ID. They will
                  # recieve notifications about the bot's online status and dont't need an allowed role to use the bot.

ALLOWED_ROLE_IDS = [
     # Here you can list of all of the discord IDs for the roles you want to be allowed to use this bot
]

# --- STARTUP FILE CHECKS ---
# Runs once when the script starts to ensure the audit log exists
if not os.path.exists('taskmasterAuditLog.json'):
    with open('taskmasterAuditLog.json', 'w') as file:
        json.dump({}, file) # <-- Changed from [] to {}

# --- BOT SETUP ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# --- BACKGROUND TASKS ---
@tasks.loop(minutes=30)
async def check_reminders():
    """High-frequency background loop resilient to server restarts."""
    tasks_data = load_json('tasks.json')
    changed = False

    # This naturally uses your home server's local timezone to calculate the date
    today = datetime.now().date()

    for task_id, task in tasks_data.items():
        if task.get("priority") == "Suggestion":
            continue

        date_str = task.get("target_date", "")
        target_date = parse_target_date(date_str)

        if not target_date:
            continue

        days_left = (target_date - today).days
        reminders_to_check = [14, 7, 3, 1, 0]

        if days_left in reminders_to_check:
            sent_reminders = task.get("reminders_sent", [])

            # The failsafe: Only send if this specific milestone hasn't been logged yet
            if days_left not in sent_reminders:
                assignee_ids = task.get("assignee_ids", [])
                if not assignee_ids and task.get("assignee_id"):
                    assignee_ids = [task.get("assignee_id")]

                if assignee_ids:
                    embed = build_single_task_embed(task_id, task)
                    day_word = "TODAY" if days_left == 0 else f"in {days_left} days"

                    # Send an individual, private DM to each person on the task
                    for uid in assignee_ids:
                        msg = f"⏰ **AUTOMATED REMINDER:** Your assigned task (`{task_id}`) is due **{day_word}** ({date_str})!"
                        await notify_user(uid, msg, embed=embed)

                # Permanently write this milestone to memory so it never sends again
                if "reminders_sent" not in task:
                    task["reminders_sent"] = []
                task["reminders_sent"].append(days_left)
                changed = True

    if changed:
        save_json('tasks.json', tasks_data)


@check_reminders.before_loop
async def before_reminders():
    """Ensures the bot is fully logged in before starting the loop."""
    await bot.wait_until_ready()


# --- HELPER FUNCTIONS ---
def is_valid_date_format(date_str: str) -> bool:
    """Strictly checks if a string matches the YYYY-MM-DD format."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def parse_target_date(date_str: str):
    """Attempts to parse a date string. Returns None if it's a legacy text string."""
    # List of formats to try (e.g., YYYY-MM-DD, MM/DD/YYYY)
    formats = ['%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y']
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
    return None

def chunk_text_by_lines(text: str, max_length: int = 1000) -> list:
    """Chunks a large string by newlines, ensuring no chunk exceeds max_length."""
    if len(text) <= max_length:
        return [text]

    lines = text.split('\n')
    chunks = []
    current_chunk = ""

    for line in lines:
        # Failsafe 1: If a single unbroken line is over 1000 characters
        if len(line) > max_length:
            raise ValueError(f"A single unbroken line is {len(line)} characters, which exceeds the {max_length} max limit.")

        # If adding this line pushes us over the limit, save the chunk and start a new one
        if len(current_chunk) + len(line) + 1 > max_length:
            chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    # Catch the last chunk
    if current_chunk:
        chunks.append(current_chunk.strip())

    # Failsafe 2: Final validation check before returning
    for chunk in chunks:
        if len(chunk) > max_length:
            raise ValueError("Validation failed: A finalized chunk exceeded the size limit.")

    return chunks

def load_json(filename: str) -> dict:
    """Generic function to load any JSON file."""
    if not os.path.exists(filename):
        return {}
    with open(filename, 'r') as file:
        return json.load(file)

def save_json(filename: str, data: dict):
    """Generic function to save data to any JSON file."""
    with open(filename, 'w') as file:
        json.dump(data, file, indent=4)


def log_audit_action(task_id: str, action: str, user: str, task_state: dict = None, status: str = "active"):
    """Logs an action and saves the task's full state for easy recovery."""
    logs = load_json('taskmasterAuditLog.json')

    # If this task isn't in the audit log yet, build its structure
    if task_id not in logs:
        logs[task_id] = {
            "history": [],
            "last_known_state": {},
            "status": "active"
        }

    # Append the new action to the timeline
    logs[task_id]["history"].append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "user": user
    })

    # Update the carbon copy of the data and its status
    if task_state is not None:
        logs[task_id]["last_known_state"] = task_state

    logs[task_id]["status"] = status

    save_json('taskmasterAuditLog.json', logs)


def get_next_id(tasks: dict) -> str:
    """Calculates the next ID using a highly condensed max() function."""
    audit_logs = load_json('taskmasterAuditLog.json')  # Using the new generic loader!

    # Extract all valid numeric IDs from both dictionaries
    task_ids = [int(k) for k in tasks.keys() if k.isdigit()]
    audit_ids = [int(k) for k in audit_logs.keys() if k.isdigit()]

    # Combine the lists, find the absolute highest, and add 1 (defaults to 0 if both are empty)
    highest_id = max(task_ids + audit_ids + [0])

    return str(highest_id + 1)


def is_hidden(interaction: discord.Interaction) -> bool:
    return interaction.channel_id not in PUBLIC_CHANNELS

def build_single_task_embed(task_id: str, task_data: dict) -> discord.Embed:
    """Builds the standardized specific task embed (used in /task and DMs)."""
    prio = task_data.get("priority", "Unassigned")

    if prio == "Suggestion":
        embed_desc = f"**Priority:** {prio}\n**Suggestion:** {task_data['description']}"
        embed_color = discord.Color.gold()
    else:
        date = task_data.get("target_date", "No date set")
        # --- NEW: Safely grab the plural list, fallback to singular for older tasks ---
        assigned = task_data.get("assignee_names", task_data.get("assignee_name", "Unassigned"))
        embed_desc = f"**Priority:** {prio}\n📅 **Target Date:** {date}\n👥 **Assigned To:** {assigned}\n**Task:** {task_data['description']}"
        embed_color = discord.Color.green()

    embed = discord.Embed(title=f"Task ID: {task_id}", description=embed_desc, color=embed_color)
    comments = task_data.get("comments", [])

    if not comments:
        embed.add_field(name="Comments", value="*No comments yet.*", inline=False)
    else:
        for index, c in enumerate(comments, 1):
            embed.add_field(name=f"Comment #{index} - {c['author']}", value=c['text'], inline=False)

    return embed

def build_task_list_embed(tasks: dict, title: str, color: discord.Color, filter_assignee_id: int = None) -> discord.Embed:
    high, med, low, unassigned, suggestions = [], [], [], [], []
    task_count = 0

    for task_id, data in tasks.items():
        # --- NEW: Get the list of IDs, fallback to a list with the singular ID for older tasks ---
        assignee_ids = data.get("assignee_ids", [])
        if not assignee_ids and data.get("assignee_id"):
            assignee_ids = [data.get("assignee_id")]

        # Filter out if this user isn't in the list
        if filter_assignee_id and filter_assignee_id not in assignee_ids:
            continue

        task_count += 1
        prio = data.get("priority", "Unassigned")
        desc = data["description"]
        author = data["author_name"]

        if prio == "Suggestion":
            suggestions.append(f"**`{task_id}`** | {desc} *(by {author})*")
        else:
            date = data.get("target_date", "No date set")
            assigned = data.get("assignee_names", data.get("assignee_name", "Unassigned"))
            task_str = f"**`{task_id}`** | {desc} *(by {author})* 📅 **Target:** {date} 👥 **Assigned:** {assigned}"

            if prio == "High": high.append(task_str)
            elif prio == "Medium": med.append(task_str)
            elif prio == "Low": low.append(task_str)
            else: unassigned.append(task_str)

    if task_count == 0:
        return None

    embed = discord.Embed(title=title, color=color)

    def add_chunked_field(name: str, item_list: list):
        if not item_list: return
        joined_text = "\n".join(item_list)
        chunks = chunk_text_by_lines(joined_text, max_length=1000)
        for i, chunk in enumerate(chunks):
            field_name = name if i == 0 else f"{name} (Cont.)"
            embed.add_field(name=field_name, value=chunk, inline=False)

    add_chunked_field("🔴 High Priority", high)
    add_chunked_field("🟡 Medium Priority", med)
    add_chunked_field("🟢 Low Priority", low)
    add_chunked_field("⚪ Unassigned Priority", unassigned)
    if not filter_assignee_id:
        add_chunked_field("💡 Suggestions", suggestions)

    return embed

async def notify_user(user_id: int, content: str, embed: discord.Embed = None):
    """Safely attempts to send a DM to a user."""
    try:
        user = await bot.fetch_user(user_id)
        if user:
            if embed:
                await user.send(content=content, embed=embed)
            else:
                await user.send(content=content)
    except discord.Forbidden:
        print(f"Could not send DM to {user_id}. Their server DMs might be closed.")
    except Exception as e:
        print(f"Failed to send DM to {user_id}: {e}")

def has_permission():
    def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == SUPER_USER_ID:
            return True
        if hasattr(interaction.user, 'roles'):
            if any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
                return True
        return False

    return app_commands.check(predicate)


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}!')

    # Start the background reminder loop
    if not check_reminders.is_running():
        check_reminders.start()
        print("Background reminder loop started.")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    if SUPER_USER_ID != 0:
        try:
            owner = await bot.fetch_user(SUPER_USER_ID)
            if owner:
                await owner.send("🟢 **Task Bot is now online and ready to work!**")
                print("Successfully sent startup DM.")
        except discord.Forbidden:
            print("Could not send DM. Ensure your privacy settings allow DMs from server members.")
        except Exception as e:
            print(f"Failed to send startup DM: {e}")


# --- SLASH COMMANDS ---
@bot.tree.command(name="ping", description="Tests if the bot is awake.")
@has_permission()
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! I am online and ready to manage tasks.",
                                            ephemeral=is_hidden(interaction))


@bot.tree.command(name="taskcreate", description="Creates a task. Requires a description, priority level, target date, and at least one assignee (but can have up to 3).")
@has_permission()
@app_commands.choices(priority=[
    app_commands.Choice(name="High 🔴", value="High"),
    app_commands.Choice(name="Medium 🟡", value="Medium"),
    app_commands.Choice(name="Low 🟢", value="Low")
])
@app_commands.describe(
    target_date="Format MUST be YYYY-MM-DD"
)
async def taskcreate(interaction: discord.Interaction, description: str, priority: app_commands.Choice[str],
                     target_date: str, assignee: discord.Member, assignee_2: discord.Member = None,
                     assignee_3: discord.Member = None):

    tasks = load_json('tasks.json')
    task_id = get_next_id(tasks)

    # 1. Compile our list of actual assigned users (filters out the blank ones)
    all_assignees = [u for u in [assignee, assignee_2, assignee_3] if u is not None]

    # 2. Create our formatted strings and lists for the JSON
    assignee_names = ", ".join([str(u) for u in all_assignees])
    assignee_mentions = ", ".join([u.mention for u in all_assignees])
    assignee_ids = [u.id for u in all_assignees]

    tasks[task_id] = {
        "description": description,
        "author_name": str(interaction.user),
        "author_id": interaction.user.id,
        "priority": priority.value,
        "target_date": target_date,
        "assignee_names": assignee_names,  # Notice this is plural now!
        "assignee_ids": assignee_ids  # Notice this is a list now!
    }
    save_json('tasks.json', tasks)

    # AUDIT LOG: Pass the newly created dictionary into the log
    log_audit_action(task_id, f"Created Task: \"{description}\" (Assigned to {assignee_names})", str(interaction.user),
                     task_state=tasks[task_id])

    # --- CARBON COPY NOTIFICATION ---
    embed = build_single_task_embed(task_id, tasks[task_id])

    # 3. Loop through EVERY assigned person and send them a DM
    for user in all_assignees:
        await notify_user(
            user.id,
            f"🔔 **You have been assigned a new task by {interaction.user.display_name}:**",
            embed=embed
        )

    await interaction.response.send_message(
        f"✅ **Task Created!**\n**ID:** `{task_id}`\n**Priority:** {priority.name}\n"
        f"📅 **Target Date:** {target_date}\n👥 **Assigned To:** {assignee_mentions}\n"
        f"**Task:** {description}\n**Logged by:** {interaction.user.mention}",
        ephemeral=is_hidden(interaction)
    )

@bot.tree.command(name="tasksuggestcreate", description="Submits a new suggestion for review. Only requires a description.")
@has_permission()
async def tasksuggestcreate(interaction: discord.Interaction, description: str):
    tasks = load_json('tasks.json')
    task_id = get_next_id(tasks)

    tasks[task_id] = {
        "description": description,
        "author_name": str(interaction.user),
        "author_id": interaction.user.id,
        "priority": "Suggestion"
    }
    save_json('tasks.json', tasks)

    # AUDIT LOG: Pass the new suggestion dictionary into the log
    log_audit_action(task_id, f"Created Suggestion: \"{description}\"", str(interaction.user),
                     task_state=tasks[task_id])

    await interaction.response.send_message(
        f"💡 **Suggestion Submitted!**\n**ID:** `{task_id}`\n**Suggestion:** {description}\n**Logged by:** {interaction.user.mention}",
        ephemeral=is_hidden(interaction)
    )


@bot.tree.command(name="tasklist", description="Lists all active tasks and suggestions.")
@has_permission()
async def tasklist(interaction: discord.Interaction):
    tasks = load_json('tasks.json')

    try:
        embed = build_task_list_embed(tasks, "📋 Active Tasks & Suggestions", discord.Color.blue())
    except ValueError as e:
        # Failsafe triggered: Inform the user
        await interaction.response.send_message(
            "❌ **Critical Error:** A task description is too long to display properly without line breaks.",
            ephemeral=True)
        return

    if not embed:
        await interaction.response.send_message("🎉 There are currently no active tasks. You're all caught up!",
                                                ephemeral=is_hidden(interaction))
        return

    await interaction.response.send_message(embed=embed, ephemeral=is_hidden(interaction))


@bot.tree.command(name="mytasklist", description="Lists all active tasks currently assigned to you.")
@has_permission()
async def mytasklist(interaction: discord.Interaction):
    tasks = load_json('tasks.json')

    try:
        embed = build_task_list_embed(tasks, f"📋 Tasks Assigned to {interaction.user.display_name}",
                                      discord.Color.purple(), filter_assignee_id=interaction.user.id)
    except ValueError as e:
        # Failsafe triggered: Inform the user
        await interaction.response.send_message(
            "❌ **Critical Error:** A task description is too long to display properly without line breaks.",
            ephemeral=True)
        return

    if not embed:
        await interaction.response.send_message("🎉 You have no assigned tasks. Enjoy your day!",
                                                ephemeral=is_hidden(interaction))
        return

    await interaction.response.send_message(embed=embed, ephemeral=is_hidden(interaction))

@bot.tree.command(name="taskcomment", description="Adds a comment to an existing task or suggestion.")
@has_permission()
async def taskcomment(interaction: discord.Interaction, task_id: str, comment: str):
    tasks = load_json('tasks.json')

    if task_id not in tasks:
        await interaction.response.send_message(f"❌ Could not find a task with ID `{task_id}`.", ephemeral=True)
        return

    if "comments" not in tasks[task_id]:
        tasks[task_id]["comments"] = []

    tasks[task_id]["comments"].append({
        "author": str(interaction.user),
        "text": comment
    })
    save_json('tasks.json', tasks)

    log_audit_action(task_id, f"Added comment: \"{comment}\"", str(interaction.user), task_state=tasks[task_id])

    # --- CARBON COPY NOTIFICATION ---
    assignee_ids = tasks[task_id].get("assignee_ids", [])
    if not assignee_ids and tasks[task_id].get("assignee_id"):
        assignee_ids = [tasks[task_id].get("assignee_id")]

    if assignee_ids:
        embed = build_single_task_embed(task_id, tasks[task_id])
        for u_id in assignee_ids:
            await notify_user(
                u_id,
                f"💬 **New Comment on your assigned task (`{task_id}`)** by {interaction.user.display_name}:\n> {comment}",
                embed=embed
            )

    await interaction.response.send_message(
        f"💬 Comment added to Task `{task_id}` by {interaction.user.mention}!\n> {comment}",
        ephemeral=is_hidden(interaction))

@bot.tree.command(name="task", description="Views a specific task/suggestion and its comments.")
@has_permission()
async def task(interaction: discord.Interaction, task_id: str):
    tasks = load_json('tasks.json')

    if task_id not in tasks:
        await interaction.response.send_message(f"❌ Could not find a task with ID `{task_id}`.", ephemeral=True)
        return

    # Use the new helper function to build the embed!
    embed = build_single_task_embed(task_id, tasks[task_id])
    await interaction.response.send_message(embed=embed, ephemeral=is_hidden(interaction))


@bot.tree.command(name="taskdetailupdate",
                  description="Updates either the date, assignees, or priority of an existing task.")
@has_permission()
@app_commands.choices(new_priority=[
    app_commands.Choice(name="High 🔴", value="High"),
    app_commands.Choice(name="Medium 🟡", value="Medium"),
    app_commands.Choice(name="Low 🟢", value="Low")
])
@app_commands.describe(
    new_date="Format MUST be YYYY-MM-DD (e.g., 2026-10-31) for reminders."
)
async def taskdetailupdate(interaction: discord.Interaction, task_id: str, new_date: str = None,
                           new_assignee: discord.Member = None, new_assignee_2: discord.Member = None,
                           new_assignee_3: discord.Member = None,
                           new_priority: app_commands.Choice[str] = None):
    if new_date and not is_valid_date_format(new_date):
        await interaction.response.send_message(
            "❌ **Invalid Date Format!** You must use exactly `YYYY-MM-DD` (e.g., `2026-10-31`).",
            ephemeral=True
        )
        return

    tasks = load_json('tasks.json')

    if task_id not in tasks:
        await interaction.response.send_message(f"❌ Could not find a task with ID `{task_id}`.", ephemeral=True)
        return

    # Check to make sure they are only updating ONE category
    provided_date = new_date is not None
    provided_priority = new_priority is not None
    provided_assignees = any([new_assignee, new_assignee_2, new_assignee_3])

    if sum([provided_date, provided_priority, provided_assignees]) != 1:
        await interaction.response.send_message(
            "❌ Please provide exactly **ONE** detail group to update (Date, Assignees, OR Priority).", ephemeral=True)
        return

    task_data = tasks[task_id]

    if task_data.get("priority") == "Suggestion":
        await interaction.response.send_message(
            "❌ **Update Failed:** You cannot change the details of a suggestion.", ephemeral=True)
        return

    # Safely get old assignees for our DM logic
    old_assignee_ids = task_data.get("assignee_ids", [])
    if not old_assignee_ids and task_data.get("assignee_id"):
        old_assignee_ids = [task_data.get("assignee_id")]

    if "comments" not in task_data:
        task_data["comments"] = []

    update_msg = ""
    audit_action = ""

    if new_date:
        old_date = task_data.get("target_date", "No date set")
        task_data["target_date"] = new_date
        update_msg = f"Target date changed from **{old_date}** to **{new_date}**"
        audit_action = f"Updated target date from {old_date} to {new_date}"

    elif provided_assignees:
        all_new_assignees = [u for u in [new_assignee, new_assignee_2, new_assignee_3] if u is not None]
        new_assignee_names = ", ".join([str(u) for u in all_new_assignees])
        new_assignee_mentions = ", ".join([u.mention for u in all_new_assignees])
        new_assignee_ids = [u.id for u in all_new_assignees]

        old_names = task_data.get("assignee_names", task_data.get("assignee_name", "Unassigned"))

        task_data["assignee_names"] = new_assignee_names
        task_data["assignee_ids"] = new_assignee_ids

        # Clean up old legacy keys if they exist so they don't cause confusion later
        if "assignee_name" in task_data: del task_data["assignee_name"]
        if "assignee_id" in task_data: del task_data["assignee_id"]

        update_msg = f"Assignees changed from **{old_names}** to {new_assignee_mentions}"
        audit_action = f"Updated assignees from {old_names} to {new_assignee_names}"

    elif new_priority:
        old_prio = task_data.get("priority", "Unassigned")
        task_data["priority"] = new_priority.value
        update_msg = f"Priority changed from **{old_prio}** to **{new_priority.name}**"
        audit_action = f"Updated priority from {old_prio} to {new_priority.value}"

    task_data["comments"].append({
        "author": "System Auto-Log",
        "text": f"⚙️ {update_msg} by {interaction.user.display_name}."
    })

    save_json('tasks.json', tasks)
    log_audit_action(task_id, audit_action, str(interaction.user), task_state=task_data)

    # --- CARBON COPY NOTIFICATIONS ---
    embed = build_single_task_embed(task_id, task_data)

    if provided_assignees:
        # Notify removed assignees
        for old_id in old_assignee_ids:
            if old_id not in new_assignee_ids:
                await notify_user(old_id,
                                  f"ℹ️ **Task Update:** You have been unassigned from Task `{task_id}`. It is now assigned to {new_assignee_names}.")

        # Notify new & remaining assignees
        for new_id in new_assignee_ids:
            if new_id not in old_assignee_ids:
                await notify_user(new_id,
                                  f"🔔 **You have been assigned a new task by {interaction.user.display_name}:**",
                                  embed=embed)
            else:
                await notify_user(new_id,
                                  f"⚙️ **Task Update:** The team for your assigned task (`{task_id}`) was updated.",
                                  embed=embed)
    else:
        # Date/Priority change: message everyone currently on the task
        for c_id in old_assignee_ids:
            await notify_user(c_id,
                              f"⚙️ **Task Update:** A detail on your assigned task (`{task_id}`) was changed by {interaction.user.display_name}:\n> {update_msg}",
                              embed=embed)

    await interaction.response.send_message(f"✅ **Task `{task_id}` Updated!**\n{update_msg}.",
                                            ephemeral=is_hidden(interaction))


@bot.tree.command(name="taskdelete", description="Permanently deletes a task and its comments. Requires a reason.")
async def taskdelete(interaction: discord.Interaction, task_id: str, reason: str):
    tasks = load_json('tasks.json')

    if task_id not in tasks:
        await interaction.response.send_message(f"❌ Could not find a task with ID `{task_id}`.", ephemeral=True)
        return

    task_data = tasks[task_id]
    is_author = interaction.user.id == task_data.get("author_id")
    is_owner = interaction.user.id == SUPER_USER_ID

    is_admin = hasattr(interaction.user, 'roles') and any(r.id in ALLOWED_ROLE_IDS for r in interaction.user.roles)

    if not (is_author or is_owner or is_admin):
        await interaction.response.send_message(
            "❌ You do not have permission to delete this task. Only the author or a Steward can remove it.",
            ephemeral=True)
        return

    # --- NEW: Inject the deletion reason as a final comment ---
    if "comments" not in task_data:
        task_data["comments"] = []

    task_data["comments"].append({
        "author": "System Auto-Log",
        "text": f"🗑️ **DELETED by {interaction.user.display_name}. Reason:** {reason}"
    })

    # Grab the description and assignees BEFORE we delete the task
    deleted_desc = task_data.get("description", "Unknown Description")
    assignee_ids = task_data.get("assignee_ids", [])
    if not assignee_ids and task_data.get("assignee_id"):
        assignee_ids = [task_data.get("assignee_id")]

    # AUDIT LOG: Capture the final state (with the new comment) and log the reason in the action timeline
    log_audit_action(task_id, f"Deleted item. Reason: \"{reason}\"", str(interaction.user), task_state=task_data,
                     status="deleted")

    del tasks[task_id]
    save_json('tasks.json', tasks)

    # --- CARBON COPY NOTIFICATION ---
    for u_id in assignee_ids:
        await notify_user(
             u_id,
            f"🗑️ **Task Canceled:** The task you were assigned to (`{task_id}`) has been deleted by {interaction.user.display_name}. You are off the hook!\n> **Reason:** {reason}\n> **Task:** {deleted_desc}"
        )

    await interaction.response.send_message(f"🗑️ **Task `{task_id}` has been successfully deleted.**\n> **Reason:** {reason}",
                                            ephemeral=is_hidden(interaction))


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, (app_commands.MissingRole, app_commands.MissingAnyRole, app_commands.CheckFailure)):
        await interaction.response.send_message("❌ You do not have the required permissions to use this command.",
                                                ephemeral=True)
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"⚠️ **A code error occurred!** Check your terminal.\nError: `{error}`", ephemeral=True)
        print(f"An error occurred: {error}")


# --- SECURE LOGIN ---
with open('APIkey.json', 'r') as file:
    secrets = json.load(file)

TOKEN = secrets["token"]
bot.run(TOKEN)