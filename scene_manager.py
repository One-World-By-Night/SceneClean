import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import datetime
import pytz
import io

# Ensure all necessary intents are enabled
intents = discord.Intents.all()
intents.guilds = True
intents.members = True
intents.dm_messages = True  # Make sure the bot can read messages
intents.guild_messages = True  # Make sure the bot can read messages

bot = commands.Bot(command_prefix='!', intents=intents)

# Connect to the SQLite database
conn = sqlite3.connect('server_settings.db')
cursor = conn.cursor()

# Create the necessary tables if they don't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS scene_limits (
        server_id INTEGER PRIMARY KEY,
        channel_limit INTEGER
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS default_channels (
        server_id INTEGER PRIMARY KEY,
        channel_id INTEGER
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS st_roles (
        server_id INTEGER PRIMARY KEY,
        role_id INTEGER
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS required_roles (
        server_id INTEGER PRIMARY KEY,
        role_id INTEGER
    )
''')
conn.commit()

# Bot event when it's ready
@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# Utility functions for archiving
def escape_discord_markdown(text):
    # Escape Discord markdown characters to prevent formatting issues
    return discord.utils.escape_markdown(text)

def indent_multiline_message(content, indent_length):
    # Indent each line of a multi-line message
    indent = ' ' * indent_length
    return '\n'.join([indent + line if index > 0 else line for index, line in enumerate(content.splitlines())])

async def generate_archive(channel, guild, author):
    messages = []
    members = set()
    first_message_date = None
    last_message_date = None

    async for message in channel.history(limit=None, oldest_first=True):
        if message.author == bot.user:
            continue

        author_member = guild.get_member(message.author.id)
        author_display_name = author_member.display_name if author_member else message.author.name
        members.add(author_display_name)

        content = "[image]" if message.attachments else escape_discord_markdown(message.content)

        for user in message.mentions:
            member = guild.get_member(user.id)
            if member:
                content = content.replace(f"<@{user.id}>", member.display_name)
            else:
                content = content.replace(f"<@{user.id}>", user.name)

        content = indent_multiline_message(content, indent_length=22)

        if not first_message_date:
            first_message_date = message.created_at

        last_message_date = message.created_at

        formatted_message = f"{message.created_at.strftime('%Y-%m-%d %H:%M')} - {author_display_name}: {content}"
        messages.append(formatted_message)

    date_covered = f"{first_message_date.strftime('%Y-%m-%d %H:%M')} to {last_message_date.strftime('%Y-%m-%d %H:%M')}"
    members_list = ', '.join(sorted(members))

    output_text = (
        f"**Channel:** {channel.name}\n"
        f"**Dates Covered:** {date_covered}\n"
        f"**Members:** {members_list}\n\n"
        + '\n'.join(messages)
    )

    file_buffer = io.BytesIO()
    file_buffer.write(output_text.encode('utf-8'))
    file_buffer.seek(0)

    # Generate a dynamic file name using server, channel, and date
    server_name = guild.name.replace(" ", "_").replace("/", "_")
    channel_name = channel.name.replace(" ", "_").replace("/", "_")
    current_date = datetime.datetime.now().strftime("%Y%m%d")
    file_name = f"{server_name}_{channel_name}_{current_date}.txt"

    return file_buffer, file_name

# Command to set the required role for using the bot
@bot.command(name='set_required_role')
@commands.has_permissions(administrator=True)
async def set_required_role(ctx, role_name: str):
    target_role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not target_role:
        await ctx.author.send(f"Role `{role_name}` not found. Please make sure you've entered the correct role name.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
        return

    try:
        cursor.execute('''
            INSERT INTO required_roles (server_id, role_id)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET role_id=excluded.role_id
        ''', (ctx.guild.id, target_role.id))
        conn.commit()

        await ctx.author.send(f"The required role for using the Scene Manager has been set to {target_role.name}.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
    except Exception as e:
        await ctx.author.send(f"An error occurred while setting the required role: {str(e)}")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
        print(f"Error in set_required_role command: {str(e)}")

# Function to check if a user has the required role
async def has_required_role(ctx):
    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (ctx.guild.id,))
    result = cursor.fetchone()
    if result:
        required_role = ctx.guild.get_role(result[0])
        if required_role:
            if required_role not in ctx.author.roles:
                await ctx.author.send(f"You do not have the required role ({required_role.name}) to use this command.")
                await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
                return False
    return True

# Modify existing commands to include the role check
@bot.command(name='create_scene')
async def create_scene(ctx, channel_name: str, *members_and_roles: str):
    # Add reaction to indicate that the command was received
    await ctx.message.add_reaction("📬")

    # Ensure the command is being used in a server (guild)
    if ctx.guild is None:
        await ctx.author.send("This command can only be used in a server.")
        return
    
    guild = ctx.guild
    category_name = "Scene Manager"

    # Check if the scene limit is set
    cursor.execute('SELECT channel_limit FROM scene_limits WHERE server_id = ?', (guild.id,))
    result = cursor.fetchone()
    if not result:
        await ctx.author.send("The scene limit has not been set. Please set the scene limit using the `!set_scene_limit` command before creating a scene.")
        return

    scene_manager_channel_limit = result[0]

    # Find the category
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name)
    
    # Check if a channel with the same name already exists
    existing_channel = discord.utils.get(guild.text_channels, name=channel_name, category=category)
    if existing_channel:
        await ctx.author.send(f"A channel named `{channel_name}` already exists in the `{category_name}` category.")
        return
    
    # Check if the number of channels exceeds the limit
    if len(category.channels) >= scene_manager_channel_limit:
        await ctx.author.send(f"The `{category_name}` category is currently full. The maximum number of channels ({scene_manager_channel_limit}) has been reached.")
        return

    # Create the overwrites for the channel
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }

    # Add ST role to the channel permissions if set
    cursor.execute('SELECT role_id FROM st_roles WHERE server_id = ?', (guild.id,))
    result = cursor.fetchone()
    if result:
        st_role = guild.get_role(result[0])
        if st_role:
            overwrites[st_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)

    # Add each member and role to the channel
    mentions = []
    for item in members_and_roles:
        member = discord.utils.get(guild.members, mention=item) or discord.utils.get(guild.members, name=item)
        if member:
            overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            mentions.append(member.mention)
            continue
        
        role = discord.utils.get(guild.roles, mention=item) or discord.utils.get(guild.roles, name=item)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            mentions.append(role.mention)
            continue

    # Create the channel under the category
    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

    await ctx.author.send(f"Channel `{channel_name}` created under `{category_name}` category, with access and management rights given to you and access given to {', '.join(mentions)}.")
    # Add a reaction to indicate successful completion
    await ctx.message.add_reaction("✅")

# Command to set the ST role
@bot.command(name='set_st_role')
@commands.has_permissions(administrator=True)
async def set_st_role(ctx, role_name: str):
    # Try to find the role by name
    target_role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not target_role:
        await ctx.author.send(f"Role `{role_name}` not found. Please make sure you've entered the correct role name.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
        return

    try:
        # Insert or update the ST role in the database
        cursor.execute('''
            INSERT INTO st_roles (server_id, role_id)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET role_id=excluded.role_id
        ''', (ctx.guild.id, target_role.id))
        conn.commit()

        await ctx.author.send(f"The ST role has been set to {target_role.name}.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
    except Exception as e:
        await ctx.author.send(f"An error occurred while setting the ST role: {str(e)}")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
        print(f"Error in set_st_role command: {str(e)}")

@bot.command(name='set_scene_limit')
@commands.has_permissions(administrator=True)
async def set_scene_limit(ctx, limit: int):
    if limit < 1:
        await ctx.author.send("The limit must be a positive integer.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
    else:
        guild_id = ctx.guild.id
        cursor.execute('''
            INSERT INTO scene_limits (server_id, channel_limit)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET channel_limit=excluded.channel_limit
        ''', (guild_id, limit))
        conn.commit()
        await ctx.author.send(f"The maximum number of channels in the 'Scene Manager' category has been set to {limit}.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message

# Command to get the current configuration of the bot for this server
@bot.command(name='get_scene_limit')
async def get_scene_limit(ctx):
    guild_id = ctx.guild.id
    cursor.execute('SELECT channel_limit FROM scene_limits WHERE server_id = ?', (guild_id,))
    result = cursor.fetchone()
    
    if result:
        await ctx.author.send(f"The current channel limit in the 'Scene Manager' category for this server is {result[0]}.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
    else:
        await ctx.author.send("No channel limit has been set for the 'Scene Manager' category on this server.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message

# Command to add a user to an existing scene channel
@bot.command(name='add_user_to_scene')
async def add_user_to_scene(ctx, channel_name: str, *members_and_roles: str):
    # Add reaction to indicate that the command was received
    await ctx.message.add_reaction("📬")
    
    # Ensure the command is being used in a server (guild)
    if ctx.guild is None:
        await ctx.author.send("This command can only be used in a server.")
        return
    
    guild = ctx.guild
    category_name = "Scene Manager"

    # Find the category
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        await ctx.author.send(f"Category `{category_name}` not found.")
        return

    # Find the channel within the category
    channel = discord.utils.get(category.channels, name=channel_name)
    if channel is None:
        await ctx.author.send(f"No channel named `{channel_name}` found in the `{category_name}` category.")
        return
    
    # Check if the command issuer has manage_channels permission in this channel
    if not channel.permissions_for(ctx.author).manage_channels and not ctx.author.guild_permissions.administrator:
        await ctx.author.send("You do not have permission to add users or roles to this channel.")
        return
    
    added_items = []
    for item in members_and_roles:
        # Try to resolve as a Member first
        member = discord.utils.get(guild.members, mention=item) or discord.utils.get(guild.members, name=item)
        if member:
            await channel.set_permissions(member, read_messages=True, send_messages=True)
            added_items.append(member.mention)
            continue
        
        # If not a Member, try to resolve as a Role
        role = discord.utils.get(guild.roles, mention=item) or discord.utils.get(guild.roles, name=item)
        if role:
            await channel.set_permissions(role, read_messages=True, send_messages=True)
            added_items.append(role.mention)
            continue

    if added_items:
        await ctx.author.send(f"The following have been added to the channel `{channel_name}`: {', '.join(added_items)}.")
    else:
        await ctx.author.send(f"No valid members or roles were found for the provided inputs.")

@bot.command(name='remove_user_from_scene')
async def remove_user_from_scene(ctx, channel_name: str, *members_and_roles: str):
    # Add reaction to indicate that the command was received
    await ctx.message.add_reaction("📬")
    
    # Ensure the command is being used in a server (guild)
    if ctx.guild is None:
        await ctx.author.send("This command can only be used in a server.")
        return
    
    guild = ctx.guild
    category_name = "Scene Manager"

    # Find the category
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        await ctx.author.send(f"Category `{category_name}` not found.")
        return

    # Find the channel within the category
    channel = discord.utils.get(category.channels, name=channel_name)
    if channel is None:
        await ctx.author.send(f"No channel named `{channel_name}` found in the `{category_name}` category.")
        return
    
    # Check if the command issuer has manage_channels permission in this channel
    if not channel.permissions_for(ctx.author).manage_channels and not ctx.author.guild_permissions.administrator:
        await ctx.author.send("You do not have permission to remove users or roles from this channel.")
        return
    
    removed_items = []
    not_found_items = []
    for item in members_and_roles:
        # Try to resolve as a Member first
        member = discord.utils.get(guild.members, mention=item) or discord.utils.get(guild.members, name=item)
        if member:
            if channel.permissions_for(member).read_messages:
                await channel.set_permissions(member, overwrite=None)
                removed_items.append(member.mention)
            else:
                not_found_items.append(member.mention)
            continue
        
        # If not a Member, try to resolve as a Role
        role = discord.utils.get(guild.roles, mention=item) or discord.utils.get(guild.roles, name=item)
        if role:
            if channel.permissions_for(role).read_messages:
                await channel.set_permissions(role, overwrite=None)
                removed_items.append(role.mention)
            else:
                not_found_items.append(role.mention)
            continue

    if removed_items:
        await ctx.author.send(f"The following have been removed from the channel `{channel_name}`: {', '.join(removed_items)}.")
    if not_found_items:
        await ctx.author.send(f"The following were not found in the channel `{channel_name}` or did not have access: {', '.join(not_found_items)}.")
    if not removed_items and not not_found_items:
        await ctx.author.send(f"No valid members or roles were found for the provided inputs.")

# Command to archive the current channel and send the archive via DM
@bot.command(name='end_scene')
async def end_scene(ctx):
    file_buffer, file_name = None, None  # Initialize file_buffer and file_name to None
    try:
        # Generate the archive
        file_buffer, file_name = await generate_archive(ctx.channel, ctx.guild, ctx.author)

        # Send the file in a DM
        dm_channel = await ctx.author.create_dm()
        await dm_channel.send(file=discord.File(fp=file_buffer, filename=file_name))
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message

        # Check if the bot posted anything to the channel
        async for message in ctx.channel.history(limit=10):
            if message.author == bot.user:
                # Delete the message if it was posted to the channel
                await message.delete()

    except Exception as e:
        # Notify the user if there's an error
        await ctx.author.send(f"An error occurred while generating the archive: {str(e)}")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message

    finally:
        # Clear the buffer after sending
        if file_buffer:
            file_buffer.close()

@bot.command(name='set_default_wrap_channel')
@commands.has_permissions(administrator=True)
async def set_default_wrap_channel(ctx, channel_name: str):
    # Try to find the channel by name
    target_channel = discord.utils.get(ctx.guild.channels, name=channel_name)
    if not target_channel:
        await ctx.author.send(f"Channel `{channel_name}` not found. Please make sure you've entered the correct channel name.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
        return

    try:
        # Insert or update the default channel in the database
        cursor.execute('''
            INSERT INTO default_channels (server_id, channel_id)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET channel_id=excluded.channel_id
        ''', (ctx.guild.id, target_channel.id))
        conn.commit()

        await ctx.author.send(f"The default channel for `!scene_wrap` has been set to {target_channel.name}.")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
    except Exception as e:
        await ctx.author.send(f"An error occurred while setting the default channel: {str(e)}")
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message
        print(f"Error in set_default_wrap_channel command: {str(e)}")

@bot.command(name='scene_wrap')
async def scene_wrap(ctx, channel_name: str = None):
    # Custom permission check: Only users with `manage_channels` permission can use this command
    if not ctx.channel.permissions_for(ctx.author).manage_channels:
        await ctx.author.send("You do not have permission to use this command in this channel.")
        await ctx.message.add_reaction("📬")
        return
    
    if not channel_name:
        # Retrieve the default channel ID from the database
        cursor.execute('SELECT channel_id FROM default_channels WHERE server_id = ?', (ctx.guild.id,))
        result = cursor.fetchone()
        if result:
            default_channel = bot.get_channel(result[0])
            if not default_channel:
                await ctx.author.send("The default channel set for archiving is not found. Please set a valid default channel.")
                await ctx.message.add_reaction("📬")
                return
            channel_name = default_channel.name
        else:
            await ctx.author.send("Please provide the name of the channel you want to archive messages to. Usage: `!scene_wrap <channel_name>`")
            await ctx.message.add_reaction("📬")
            return

    # Get the target channel by name
    target_channel = discord.utils.get(ctx.guild.channels, name=channel_name)
    if not target_channel:
        await ctx.author.send(f"Channel {channel_name} not found.")
        await ctx.message.add_reaction("📬")
        return
    
    file_buffer, file_name = None, None
    try:
        file_buffer, file_name = await generate_archive(ctx.channel, ctx.guild, ctx.author)

        # Send the archive to the target channel
        await target_channel.send(file=discord.File(fp=file_buffer, filename=file_name))

        # Notify that the messages have been archived
        await ctx.author.send("Messages have been archived and posted.")
        await ctx.message.add_reaction("📬")

    except Exception as e:
        # Notify the user if there's an error
        await ctx.author.send(f"An error occurred while generating the archive: {str(e)}")
        await ctx.message.add_reaction("📬")
    
    finally:
        # Clear the buffer after sending
        if file_buffer:
            file_buffer.close()

    utc = pytz.UTC
    fourteen_days_ago = datetime.datetime.now(utc) - datetime.timedelta(days=14)

    messages_to_delete_bulk = []
    messages_to_delete_individual = []

    async for message in ctx.channel.history(limit=None):
        if not message.pinned:
            if message.created_at > fourteen_days_ago:
                messages_to_delete_bulk.append(message)
            else:
                messages_to_delete_individual.append(message)

    while len(messages_to_delete_bulk) > 0:
        batch = messages_to_delete_bulk[:100]
        await ctx.channel.delete_messages(batch)
        messages_to_delete_bulk = messages_to_delete_bulk[100:]

    for message in messages_to_delete_individual:
        await message.delete()

@bot.command(name='archive_and_delete_scene')
@commands.has_permissions(manage_channels=True)
async def archive_and_delete_scene(ctx):
    # Add reaction to indicate that the command was received
    await ctx.message.add_reaction("📬")
    
    # Ensure the command is being used in a Scene Manager category
    if ctx.channel.category.name != "Scene Manager":
        await ctx.author.send("This command can only be used in channels under the 'Scene Manager' category.")
        return

    # Check if the default archive channel is set
    cursor.execute('SELECT channel_id FROM default_channels WHERE server_id = ?', (ctx.guild.id,))
    result = cursor.fetchone()
    if not result:
        await ctx.author.send("The default archive channel is not set. Please set it using `!set_default_wrap_channel` before using this command.")
        return

    default_archive_channel = bot.get_channel(result[0])
    if not default_archive_channel:
        await ctx.author.send("The default archive channel could not be found. Please check if the channel still exists.")
        return

    # Get the ST role if it is set
    cursor.execute('SELECT role_id FROM st_roles WHERE server_id = ?', (ctx.guild.id,))
    st_role_result = cursor.fetchone()
    st_role = ctx.guild.get_role(st_role_result[0]) if st_role_result else None

    # Ensure the user has permission to use this command
    overwrites = ctx.channel.overwrites_for(ctx.author)
    if not overwrites.manage_channels and not ctx.author.guild_permissions.administrator:
        await ctx.author.send("You do not have permission to use this command.")
        return

    # Generate the archive
    file_buffer, file_name = None, None
    try:
        file_buffer, file_name = await generate_archive(ctx.channel, ctx.guild, ctx.author)
        archive_data = file_buffer.getvalue()

        # Send the archive to the default archive channel
        file_buffer.seek(0)
        await default_archive_channel.send(file=discord.File(fp=file_buffer, filename=file_name))

        # Send the archive via DM to each member with access to the channel, excluding bots and those with the ST role, but always include the command issuer
        members_with_access = [member for member in ctx.channel.members if member != bot.user and not member.bot and ((st_role not in member.roles if st_role else True) or member == ctx.author)]
        for member in members_with_access:
            try:
                dm_buffer = io.BytesIO(archive_data)
                dm_buffer.seek(0)
                dm_channel = await member.create_dm()
                await dm_channel.send(file=discord.File(fp=dm_buffer, filename=file_name))
                dm_buffer.close()
            except Exception as dm_error:
                await ctx.author.send(f"Failed to send archive to {member.mention}: {str(dm_error)}")

        await ctx.author.send("The archive has been sent to the default archive channel and DMed to each applicable member. The channel will be deleted shortly.")
        await ctx.message.add_reaction("✅")

    except Exception as e:
        await ctx.author.send(f"An error occurred while archiving the channel: {str(e)}")
        return

    finally:
        if file_buffer:
            file_buffer.close()

    # Delete the channel after the archive is complete
    try:
        await ctx.channel.delete()
    except Exception as delete_error:
        await ctx.author.send(f"Failed to delete the channel: {str(delete_error)}")

# Command to check current Scene Manager settings
@bot.command(name='check_scene_manager_settings')
@commands.has_permissions(administrator=True)
async def check_scene_manager_settings(ctx):
    guild_id = ctx.guild.id

    # Fetch current settings from the database
    cursor.execute('SELECT channel_limit FROM scene_limits WHERE server_id = ?', (guild_id,))
    scene_limit = cursor.fetchone()
    scene_limit = scene_limit[0] if scene_limit else "Not set"

    cursor.execute('SELECT channel_id FROM default_channels WHERE server_id = ?', (guild_id,))
    default_channel = cursor.fetchone()
    default_channel = bot.get_channel(default_channel[0]).name if default_channel else "Not set"

    cursor.execute('SELECT role_id FROM st_roles WHERE server_id = ?', (guild_id,))
    st_role = cursor.fetchone()
    st_role = ctx.guild.get_role(st_role[0]).name if st_role else "Not set"

    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (guild_id,))
    required_role = cursor.fetchone()
    required_role = ctx.guild.get_role(required_role[0]).name if required_role else "Not set"

    # Construct the settings report
    settings_report = (
        f"**Scene Manager Settings for {ctx.guild.name}:**\n"
        f"**Scene Limit:** {scene_limit}\n"
        f"**Default Archive Channel:** {default_channel}\n"
        f"**ST Role:** {st_role}\n"
        f"**Required Role for Bot Usage:** {required_role}\n"
    )

    await ctx.author.send(settings_report)
    await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message

# Command to provide help information for scene management commands
@bot.command(name='help_scene_manager')
async def help_scene_manager(ctx):
    user_commands = """
    **User Commands:**
    1. `!end_scene`: Archives the current channel's messages and sends the archive to your DM. This works in any channel.
    2. `!create_scene <channel_name> <members>`: Creates a new "private scene" channel with specified members and roles. 
    3. `!add_user_to_scene <channel_name> <member>`: Adds a user or role to an existing "private scene" channel. 
    4. `!remove_user_from_scene <channel_name> <member>`: Removes a user or role from an existing "private scene" channel.       
    5. `!archive_and_delete_scene`: Archives the current "private scene" channel, sends a copy to the default archive channel and DMs a copy to each member in the channel, then deletes the channel. Can only be used by the user who created the scene or an administrator, and it only works in channels under the "Scene Manager" category.
    """

    admin_commands = """
    **Admin Commands:**
    1. `!scene_wrap <channel_name>`: Archives messages from the current channel and sends them to the specified channel. If no channel is specified, it will use the default channel. It then deletes all non-pinned messages. A user needs "manage channel" permissions to use this.
    2. `!set_scene_limit <limit>`: Sets the maximum number of channels allowed in the "Scene Manager" category. The create_scene command will not function unless you set some value here.
    3. `!set_default_wrap_channel <channel_name>`: Sets a default channel for `!scene_wrap` to use when no channel is specified.
    4. `!set_st_role <role_name>`: Sets a default "ST role" that will automatically be added to every channel created with `!create_scene`.
    5. `!set_required_role <role_name>`: Sets a required role for using the Scene Manager bot. Users without this role cannot use the bot's commands.
    6. `!check_scene_manager_settings`: Displays the current configuration settings for the Scene Manager bot.
    """

    usage_examples = """
    **Usage Examples:**
    - `!end_scene`: Archive the messages in the channel and receive them via DM.
    - `!scene_wrap archive_channel`: Archives messages and posts the archive in `#archive_channel`. Cleans room of all non-pinned messages
    - `!create_scene private_scene @user1 @user2 @role1`: Creates a new private channel with `@user1` and `@user2` and '@role1'.
    - `!add_user_to_scene private_scene @newuser`: Adds `@newuser` to the `private_scene` channel.
    - `!remove_user_from_scene private_scene @newuser`: Removes `@newuser` from the `private_scene` channel.
    - `!set_default_wrap_channel archive_channel`: Sets the `#archive_channel` as the default channel for `!scene_wrap`.
    - `!archive_and_delete_scene`: Archives and deletes the current scene channel.
    - `!set_st_role Storytellers`: Sets the `@Storytellers` role as the default role to be added to all new scene channels.
    - `!set_required_role Members`: Sets the `@Members` role as the required role to use the bot.
        
    **Note:** Only administrators can use `!set_scene_limit`, `!set_default_wrap_channel`, `!set_st_role`, and `!set_required_role`. Admnins and channels managers can use `!scene_wrap`.
    """

    help_texts = [user_commands, admin_commands, usage_examples]

    for text in help_texts:
        await ctx.author.send(text)
        await ctx.message.add_reaction("📬")  # Add an envelope reaction to the user's command message

# Slash Commands

@bot.tree.command(name="create_scene", description="Create a new scene channel")
@app_commands.describe(channel_name="Name of the channel to create", members="Members and roles to add (space-separated)")
async def slash_create_scene(interaction: discord.Interaction, channel_name: str, members: str = ""):
    await interaction.response.defer()

    # Check required role
    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (interaction.guild.id,))
    result = cursor.fetchone()
    if result:
        required_role = interaction.guild.get_role(result[0])
        if required_role and required_role not in interaction.user.roles:
            await interaction.followup.send(f"You do not have the required role ({required_role.name}) to use this command.", ephemeral=True)
            return

    guild = interaction.guild
    category_name = "Scene Manager"

    # Check if the scene limit is set
    cursor.execute('SELECT channel_limit FROM scene_limits WHERE server_id = ?', (guild.id,))
    result = cursor.fetchone()
    if not result:
        await interaction.followup.send("The scene limit has not been set. Please set the scene limit using the `/set_scene_limit` command before creating a scene.", ephemeral=True)
        return

    scene_manager_channel_limit = result[0]

    # Find the category
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name)

    # Check if a channel with the same name already exists
    existing_channel = discord.utils.get(guild.text_channels, name=channel_name, category=category)
    if existing_channel:
        await interaction.followup.send(f"A channel named `{channel_name}` already exists in the `{category_name}` category.", ephemeral=True)
        return

    # Check if the number of channels exceeds the limit
    if len(category.channels) >= scene_manager_channel_limit:
        await interaction.followup.send(f"The `{category_name}` category is currently full. The maximum number of channels ({scene_manager_channel_limit}) has been reached.", ephemeral=True)
        return

    # Create the overwrites for the channel
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }

    # Add ST role to the channel permissions if set
    cursor.execute('SELECT role_id FROM st_roles WHERE server_id = ?', (guild.id,))
    result = cursor.fetchone()
    if result:
        st_role = guild.get_role(result[0])
        if st_role:
            overwrites[st_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)

    # Parse members
    members_and_roles = members.split() if members else []
    mentions = []
    for item in members_and_roles:
        member = discord.utils.get(guild.members, mention=item) or discord.utils.get(guild.members, name=item)
        if member:
            overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            mentions.append(member.mention)
            continue

        role = discord.utils.get(guild.roles, mention=item) or discord.utils.get(guild.roles, name=item)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            mentions.append(role.mention)
            continue

    # Create the channel under the category
    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

    await interaction.followup.send(f"Channel `{channel_name}` created under `{category_name}` category, with access and management rights given to you and access given to {', '.join(mentions) if mentions else 'no one'}.", ephemeral=True)

@bot.tree.command(name="add_user_to_scene", description="Add users or roles to an existing scene channel")
@app_commands.describe(channel_name="Name of the scene channel", members="Members and roles to add (space-separated)")
async def slash_add_user_to_scene(interaction: discord.Interaction, channel_name: str, members: str):
    await interaction.response.defer()

    # Check required role
    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (interaction.guild.id,))
    result = cursor.fetchone()
    if result:
        required_role = interaction.guild.get_role(result[0])
        if required_role and required_role not in interaction.user.roles:
            await interaction.followup.send(f"You do not have the required role ({required_role.name}) to use this command.", ephemeral=True)
            return

    guild = interaction.guild
    category_name = "Scene Manager"

    # Find the category
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        await interaction.followup.send(f"Category `{category_name}` not found.", ephemeral=True)
        return

    # Find the channel within the category
    channel = discord.utils.get(category.channels, name=channel_name)
    if channel is None:
        await interaction.followup.send(f"No channel named `{channel_name}` found in the `{category_name}` category.", ephemeral=True)
        return

    # Check if the command issuer has manage_channels permission in this channel
    if not channel.permissions_for(interaction.user).manage_channels and not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("You do not have permission to add users or roles to this channel.", ephemeral=True)
        return

    added_items = []
    members_and_roles = members.split()
    for item in members_and_roles:
        # Try to resolve as a Member first
        member = discord.utils.get(guild.members, mention=item) or discord.utils.get(guild.members, name=item)
        if member:
            await channel.set_permissions(member, read_messages=True, send_messages=True)
            added_items.append(member.mention)
            continue

        # If not a Member, try to resolve as a Role
        role = discord.utils.get(guild.roles, mention=item) or discord.utils.get(guild.roles, name=item)
        if role:
            await channel.set_permissions(role, read_messages=True, send_messages=True)
            added_items.append(role.mention)
            continue

    if added_items:
        await interaction.followup.send(f"The following have been added to the channel `{channel_name}`: {', '.join(added_items)}.", ephemeral=True)
    else:
        await interaction.followup.send(f"No valid members or roles were found for the provided inputs.", ephemeral=True)

@bot.tree.command(name="remove_user_from_scene", description="Remove users or roles from an existing scene channel")
@app_commands.describe(channel_name="Name of the scene channel", members="Members and roles to remove (space-separated)")
async def slash_remove_user_from_scene(interaction: discord.Interaction, channel_name: str, members: str):
    await interaction.response.defer()

    # Check required role
    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (interaction.guild.id,))
    result = cursor.fetchone()
    if result:
        required_role = interaction.guild.get_role(result[0])
        if required_role and required_role not in interaction.user.roles:
            await interaction.followup.send(f"You do not have the required role ({required_role.name}) to use this command.", ephemeral=True)
            return

    guild = interaction.guild
    category_name = "Scene Manager"

    # Find the category
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        await interaction.followup.send(f"Category `{category_name}` not found.", ephemeral=True)
        return

    # Find the channel within the category
    channel = discord.utils.get(category.channels, name=channel_name)
    if channel is None:
        await interaction.followup.send(f"No channel named `{channel_name}` found in the `{category_name}` category.", ephemeral=True)
        return

    # Check if the command issuer has manage_channels permission in this channel
    if not channel.permissions_for(interaction.user).manage_channels and not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("You do not have permission to remove users or roles from this channel.", ephemeral=True)
        return

    removed_items = []
    not_found_items = []
    members_and_roles = members.split()
    for item in members_and_roles:
        # Try to resolve as a Member first
        member = discord.utils.get(guild.members, mention=item) or discord.utils.get(guild.members, name=item)
        if member:
            if channel.permissions_for(member).read_messages:
                await channel.set_permissions(member, overwrite=None)
                removed_items.append(member.mention)
            else:
                not_found_items.append(member.mention)
            continue

        # If not a Member, try to resolve as a Role
        role = discord.utils.get(guild.roles, mention=item) or discord.utils.get(guild.roles, name=item)
        if role:
            if channel.permissions_for(role).read_messages:
                await channel.set_permissions(role, overwrite=None)
                removed_items.append(role.mention)
            else:
                not_found_items.append(role.mention)
            continue

    response = ""
    if removed_items:
        response += f"The following have been removed from the channel `{channel_name}`: {', '.join(removed_items)}."
    if not_found_items:
        response += f"\nThe following were not found in the channel `{channel_name}` or did not have access: {', '.join(not_found_items)}."
    if not removed_items and not not_found_items:
        response = f"No valid members or roles were found for the provided inputs."

    await interaction.followup.send(response, ephemeral=True)

@bot.tree.command(name="end_scene", description="Archive the current channel's messages and send the archive to your DM")
async def slash_end_scene(interaction: discord.Interaction):
    await interaction.response.defer()

    # Check required role
    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (interaction.guild.id,))
    result = cursor.fetchone()
    if result:
        required_role = interaction.guild.get_role(result[0])
        if required_role and required_role not in interaction.user.roles:
            await interaction.followup.send(f"You do not have the required role ({required_role.name}) to use this command.", ephemeral=True)
            return

    file_buffer, file_name = None, None
    try:
        # Generate the archive
        file_buffer, file_name = await generate_archive(interaction.channel, interaction.guild, interaction.user)

        # Send the file in a DM
        dm_channel = await interaction.user.create_dm()
        await dm_channel.send(file=discord.File(fp=file_buffer, filename=file_name))
        await interaction.followup.send("Archive sent to your DM.", ephemeral=True)

        # Check if the bot posted anything to the channel
        async for message in interaction.channel.history(limit=10):
            if message.author == bot.user:
                # Delete the message if it was posted to the channel
                await message.delete()

    except Exception as e:
        # Notify the user if there's an error
        await interaction.followup.send(f"An error occurred while generating the archive: {str(e)}", ephemeral=True)

    finally:
        # Clear the buffer after sending
        if file_buffer:
            file_buffer.close()

@bot.tree.command(name="archive_and_delete_scene", description="Archive the current scene channel, send copies, and delete it")
async def slash_archive_and_delete_scene(interaction: discord.Interaction):
    await interaction.response.defer()

    # Check required role
    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (interaction.guild.id,))
    result = cursor.fetchone()
    if result:
        required_role = interaction.guild.get_role(result[0])
        if required_role and required_role not in interaction.user.roles:
            await interaction.followup.send(f"You do not have the required role ({required_role.name}) to use this command.", ephemeral=True)
            return

    # Ensure the command is being used in a Scene Manager category
    if interaction.channel.category.name != "Scene Manager":
        await interaction.followup.send("This command can only be used in channels under the 'Scene Manager' category.", ephemeral=True)
        return

    # Check if the default archive channel is set
    cursor.execute('SELECT channel_id FROM default_channels WHERE server_id = ?', (interaction.guild.id,))
    result = cursor.fetchone()
    if not result:
        await interaction.followup.send("The default archive channel is not set. Please set it using `/set_default_wrap_channel` before using this command.", ephemeral=True)
        return

    default_archive_channel = bot.get_channel(result[0])
    if not default_archive_channel:
        await interaction.followup.send("The default archive channel could not be found. Please check if the channel still exists.", ephemeral=True)
        return

    # Get the ST role if it is set
    cursor.execute('SELECT role_id FROM st_roles WHERE server_id = ?', (interaction.guild.id,))
    st_role_result = cursor.fetchone()
    st_role = interaction.guild.get_role(st_role_result[0]) if st_role_result else None

    # Ensure the user has permission to use this command
    overwrites = interaction.channel.overwrites_for(interaction.user)
    if not overwrites.manage_channels and not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("You do not have permission to use this command.", ephemeral=True)
        return

    # Generate the archive
    file_buffer, file_name = None, None
    try:
        file_buffer, file_name = await generate_archive(interaction.channel, interaction.guild, interaction.user)
        archive_data = file_buffer.getvalue()

        # Send the archive to the default archive channel
        file_buffer.seek(0)
        await default_archive_channel.send(file=discord.File(fp=file_buffer, filename=file_name))

        # Send the archive via DM to each member with access to the channel, excluding bots and those with the ST role, but always include the command issuer
        members_with_access = [member for member in interaction.channel.members if member != bot.user and not member.bot and ((st_role not in member.roles if st_role else True) or member == interaction.user)]
        for member in members_with_access:
            try:
                dm_buffer = io.BytesIO(archive_data)
                dm_buffer.seek(0)
                dm_channel = await member.create_dm()
                await dm_channel.send(file=discord.File(fp=dm_buffer, filename=file_name))
                dm_buffer.close()
            except Exception as dm_error:
                await interaction.followup.send(f"Failed to send archive to {member.mention}: {str(dm_error)}", ephemeral=True)

        await interaction.followup.send("The archive has been sent to the default archive channel and DMed to each applicable member. The channel will be deleted shortly.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"An error occurred while archiving the channel: {str(e)}", ephemeral=True)
        return

    finally:
        if file_buffer:
            file_buffer.close()

    # Delete the channel after the archive is complete
    try:
        await interaction.channel.delete()
    except Exception as delete_error:
        await interaction.followup.send(f"Failed to delete the channel: {str(delete_error)}", ephemeral=True)

@bot.tree.command(name="set_required_role", description="Set the required role for using the bot")
@app_commands.describe(role_name="Name of the role to set as required")
@commands.has_permissions(administrator=True)
async def slash_set_required_role(interaction: discord.Interaction, role_name: str):
    await interaction.response.defer()

    target_role = discord.utils.get(interaction.guild.roles, name=role_name)
    if not target_role:
        await interaction.followup.send(f"Role `{role_name}` not found. Please make sure you've entered the correct role name.", ephemeral=True)
        return

    try:
        cursor.execute('''
            INSERT INTO required_roles (server_id, role_id)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET role_id=excluded.role_id
        ''', (interaction.guild.id, target_role.id))
        conn.commit()

        await interaction.followup.send(f"The required role for using the Scene Manager has been set to {target_role.name}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"An error occurred while setting the required role: {str(e)}", ephemeral=True)

@bot.tree.command(name="set_st_role", description="Set the ST role for automatic channel access")
@app_commands.describe(role_name="Name of the role to set as ST role")
@commands.has_permissions(administrator=True)
async def slash_set_st_role(interaction: discord.Interaction, role_name: str):
    await interaction.response.defer()

    target_role = discord.utils.get(interaction.guild.roles, name=role_name)
    if not target_role:
        await interaction.followup.send(f"Role `{role_name}` not found. Please make sure you've entered the correct role name.", ephemeral=True)
        return

    try:
        cursor.execute('''
            INSERT INTO st_roles (server_id, role_id)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET role_id=excluded.role_id
        ''', (interaction.guild.id, target_role.id))
        conn.commit()

        await interaction.followup.send(f"The ST role has been set to {target_role.name}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"An error occurred while setting the ST role: {str(e)}", ephemeral=True)

@bot.tree.command(name="set_scene_limit", description="Set the maximum number of scene channels allowed")
@app_commands.describe(limit="Maximum number of channels (positive integer)")
@commands.has_permissions(administrator=True)
async def slash_set_scene_limit(interaction: discord.Interaction, limit: int):
    await interaction.response.defer()

    if limit < 1:
        await interaction.followup.send("The limit must be a positive integer.", ephemeral=True)
        return

    try:
        cursor.execute('''
            INSERT INTO scene_limits (server_id, channel_limit)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET channel_limit=excluded.channel_limit
        ''', (interaction.guild.id, limit))
        conn.commit()
        await interaction.followup.send(f"The maximum number of channels in the 'Scene Manager' category has been set to {limit}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"An error occurred while setting the scene limit: {str(e)}", ephemeral=True)

@bot.tree.command(name="get_scene_limit", description="Get the current scene channel limit")
async def slash_get_scene_limit(interaction: discord.Interaction):
    await interaction.response.defer()

    cursor.execute('SELECT channel_limit FROM scene_limits WHERE server_id = ?', (interaction.guild.id,))
    result = cursor.fetchone()

    if result:
        await interaction.followup.send(f"The current channel limit in the 'Scene Manager' category for this server is {result[0]}.", ephemeral=True)
    else:
        await interaction.followup.send("No channel limit has been set for the 'Scene Manager' category on this server.", ephemeral=True)

@bot.tree.command(name="set_default_wrap_channel", description="Set the default channel for scene_wrap archiving")
@app_commands.describe(channel_name="Name of the channel to set as default")
@commands.has_permissions(administrator=True)
async def slash_set_default_wrap_channel(interaction: discord.Interaction, channel_name: str):
    await interaction.response.defer()

    target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
    if not target_channel:
        await interaction.followup.send(f"Channel `{channel_name}` not found. Please make sure you've entered the correct channel name.", ephemeral=True)
        return

    try:
        cursor.execute('''
            INSERT INTO default_channels (server_id, channel_id)
            VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET channel_id=excluded.channel_id
        ''', (interaction.guild.id, target_channel.id))
        conn.commit()

        await interaction.followup.send(f"The default channel for `!scene_wrap` has been set to {target_channel.name}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"An error occurred while setting the default channel: {str(e)}", ephemeral=True)

@bot.tree.command(name="scene_wrap", description="Archive current channel messages and delete them")
@app_commands.describe(channel_name="Name of the channel to archive to (optional, uses default if not specified)")
async def slash_scene_wrap(interaction: discord.Interaction, channel_name: str = None):
    await interaction.response.defer()

    # Custom permission check: Only users with `manage_channels` permission can use this command
    if not interaction.channel.permissions_for(interaction.user).manage_channels:
        await interaction.followup.send("You do not have permission to use this command in this channel.", ephemeral=True)
        return

    if not channel_name:
        # Retrieve the default channel ID from the database
        cursor.execute('SELECT channel_id FROM default_channels WHERE server_id = ?', (interaction.guild.id,))
        result = cursor.fetchone()
        if result:
            default_channel = bot.get_channel(result[0])
            if not default_channel:
                await interaction.followup.send("The default channel set for archiving is not found. Please set a valid default channel.", ephemeral=True)
                return
            channel_name = default_channel.name
        else:
            await interaction.followup.send("Please provide the name of the channel you want to archive messages to.", ephemeral=True)
            return

    # Get the target channel by name
    target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
    if not target_channel:
        await interaction.followup.send(f"Channel {channel_name} not found.", ephemeral=True)
        return

    file_buffer, file_name = None, None
    try:
        file_buffer, file_name = await generate_archive(interaction.channel, interaction.guild, interaction.user)

        # Send the archive to the target channel
        await target_channel.send(file=discord.File(fp=file_buffer, filename=file_name))

        # Notify that the messages have been archived
        await interaction.followup.send("Messages have been archived and posted.", ephemeral=True)

    except Exception as e:
        # Notify the user if there's an error
        await interaction.followup.send(f"An error occurred while generating the archive: {str(e)}", ephemeral=True)

    finally:
        # Clear the buffer after sending
        if file_buffer:
            file_buffer.close()

    utc = pytz.UTC
    fourteen_days_ago = datetime.datetime.now(utc) - datetime.timedelta(days=14)

    messages_to_delete_bulk = []
    messages_to_delete_individual = []

    async for message in interaction.channel.history(limit=None):
        if not message.pinned:
            if message.created_at > fourteen_days_ago:
                messages_to_delete_bulk.append(message)
            else:
                messages_to_delete_individual.append(message)

    while len(messages_to_delete_bulk) > 0:
        batch = messages_to_delete_bulk[:100]
        await interaction.channel.delete_messages(batch)
        messages_to_delete_bulk = messages_to_delete_bulk[100:]

    for message in messages_to_delete_individual:
        await message.delete()

@bot.tree.command(name="check_scene_manager_settings", description="Display current Scene Manager settings")
@commands.has_permissions(administrator=True)
async def slash_check_scene_manager_settings(interaction: discord.Interaction):
    await interaction.response.defer()

    guild_id = interaction.guild.id

    # Fetch current settings from the database
    cursor.execute('SELECT channel_limit FROM scene_limits WHERE server_id = ?', (guild_id,))
    scene_limit = cursor.fetchone()
    scene_limit = scene_limit[0] if scene_limit else "Not set"

    cursor.execute('SELECT channel_id FROM default_channels WHERE server_id = ?', (guild_id,))
    default_channel = cursor.fetchone()
    default_channel = bot.get_channel(default_channel[0]).name if default_channel else "Not set"

    cursor.execute('SELECT role_id FROM st_roles WHERE server_id = ?', (guild_id,))
    st_role = cursor.fetchone()
    st_role = interaction.guild.get_role(st_role[0]).name if st_role else "Not set"

    cursor.execute('SELECT role_id FROM required_roles WHERE server_id = ?', (guild_id,))
    required_role = cursor.fetchone()
    required_role = interaction.guild.get_role(required_role[0]).name if required_role else "Not set"

    # Construct the settings report
    settings_report = (
        f"**Scene Manager Settings for {interaction.guild.name}:**\n"
        f"**Scene Limit:** {scene_limit}\n"
        f"**Default Archive Channel:** {default_channel}\n"
        f"**ST Role:** {st_role}\n"
        f"**Required Role for Bot Usage:** {required_role}\n"
    )

    await interaction.followup.send(settings_report, ephemeral=True)

@bot.tree.command(name="help_scene_manager", description="Get help information for Scene Manager commands")
async def slash_help_scene_manager(interaction: discord.Interaction):
    await interaction.response.defer()

    user_commands = """
    **User Commands:**
    1. `!end_scene` or `/end_scene`: Archives the current channel's messages and sends the archive to your DM. This works in any channel.
    2. `!create_scene <channel_name> <members>` or `/create_scene`: Creates a new "private scene" channel with specified members and roles.
    3. `!add_user_to_scene <channel_name> <member>` or `/add_user_to_scene`: Adds a user or role to an existing "private scene" channel.
    4. `!remove_user_from_scene <channel_name> <member>` or `/remove_user_from_scene`: Removes a user or role from an existing "private scene" channel.
    5. `!archive_and_delete_scene` or `/archive_and_delete_scene`: Archives the current "private scene" channel, sends a copy to the default archive channel and DMs a copy to each member in the channel, then deletes the channel. Can only be used by the user who created the scene or an administrator, and it only works in channels under the "Scene Manager" category.
    """

    admin_commands = """
    **Admin Commands:**
    1. `!scene_wrap <channel_name>` or `/scene_wrap`: Archives messages from the current channel and sends them to the specified channel. If no channel is specified, it will use the default channel. It then deletes all non-pinned messages. A user needs "manage channel" permissions to use this.
    2. `!set_scene_limit <limit>` or `/set_scene_limit`: Sets the maximum number of channels allowed in the "Scene Manager" category. The create_scene command will not function unless you set some value here.
    3. `!set_default_wrap_channel <channel_name>` or `/set_default_wrap_channel`: Sets a default channel for `!scene_wrap` to use when no channel is specified.
    4. `!set_st_role <role_name>` or `/set_st_role`: Sets a default "ST role" that will automatically be added to every channel created with `!create_scene`.
    5. `!set_required_role <role_name>` or `/set_required_role`: Sets a required role for using the Scene Manager bot. Users without this role cannot use the bot's commands.
    6. `!check_scene_manager_settings` or `/check_scene_manager_settings`: Displays the current configuration settings for the Scene Manager bot.
    7. `!help_scene_manager` or `/help_scene_manager`: Shows this help information.
    """

    usage_examples = """
    **Usage Examples:**
    - `!end_scene` or `/end_scene`: Archive the messages in the channel and receive them via DM.
    - `!scene_wrap archive_channel` or `/scene_wrap archive_channel`: Archives messages and posts the archive in `#archive_channel`. Cleans room of all non-pinned messages
    - `!create_scene private_scene @user1 @user2 @role1` or `/create_scene private_scene "@user1 @user2 @role1"`: Creates a new private channel with `@user1` and `@user2` and '@role1'.
    - `!add_user_to_scene private_scene @newuser` or `/add_user_to_scene private_scene "@newuser"`: Adds `@newuser` to the `private_scene` channel.
    - `!remove_user_from_scene private_scene @newuser` or `/remove_user_from_scene private_scene "@newuser"`: Removes `@newuser` from the `private_scene` channel.
    - `!set_default_wrap_channel archive_channel` or `/set_default_wrap_channel archive_channel`: Sets the `#archive_channel` as the default channel for `!scene_wrap`.
    - `!archive_and_delete_scene` or `/archive_and_delete_scene`: Archives and deletes the current scene channel.
    - `!set_st_role Storytellers` or `/set_st_role Storytellers`: Sets the `@Storytellers` role as the default role to be added to all new scene channels.
    - `!set_required_role Members` or `/set_required_role Members`: Sets the `@Members` role as the required role to use the bot.

    **Note:** Only administrators can use `!set_scene_limit`, `!set_default_wrap_channel`, `!set_st_role`, and `!set_required_role`. Admins and channel managers can use `!scene_wrap`.
    """

    help_texts = [user_commands, admin_commands, usage_examples]

    for text in help_texts:
        await interaction.followup.send(text, ephemeral=True)

# Load the bot token from the config file
def load_token():
    with open('config.txt', 'r') as file:
        token = file.readline().strip()
    return token
  
# Close the SQLite connection when the bot is closed
@bot.event
async def on_close():    
    conn.close()

token = load_token()
bot.run(token)
