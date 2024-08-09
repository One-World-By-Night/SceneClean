import discord
from discord.ext import commands
import aiofiles
import datetime
import os
import pytz
import io

TOKEN = 'YOUR_TOKEN_HERE'

intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent explicitly
intents.messages = True          # Enable message-related events

bot = commands.Bot(command_prefix='!', intents=intents)

# Check if user is an admin
def is_admin():
    async def predicate(ctx):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')

@bot.command(name='hello')
async def hello(ctx):
    print(f"Received command from {ctx.author}: {ctx.message.content}")  # Log the command received
    await ctx.send('Hello, World!')

@bot.command(name='end_scene')
async def end_scene(ctx):
    print(f"Received command from {ctx.author}: {ctx.message.content}")  # Log the command received
    
    messages = []
    members = set()
    first_message_date = None
    last_message_date = None

    async for message in ctx.channel.history(limit=None, oldest_first=True):
        # Skip messages with attachments
        if message.attachments:
            continue

        # Set the date of the first message
        if not first_message_date:
            first_message_date = message.created_at
            
        last_message_date = message.created_at
        members.add(message.author.display_name)  # Add member to the set
        messages.append(f"{message.created_at.strftime('%Y-%m-%d %H:%M')} - {message.author.display_name}: {message.content}")

    # Create a summary of the archived messages
    date_covered = f"{first_message_date.strftime('%Y-%m-%d %H:%M')} to {last_message_date.strftime('%Y-%m-%d %H:%M')}"
    members_list = ', '.join(sorted(members))
    
    output_text = (
        f"**Channel:** {ctx.channel.name}\n"
        f"**Dates Covered:** {date_covered}\n"
        f"**Members:** {members_list}\n\n"
        + '\n'.join(messages)
    )
    
 # Use an in-memory buffer to store the file content
    file_buffer = io.BytesIO()
    file_buffer.write(output_text.encode('utf-8'))
    file_buffer.seek(0)

    # Send the in-memory file as an attachment
    dm_channel = await ctx.author.create_dm()
    await dm_channel.send(file=discord.File(fp=file_buffer, filename="scene_archive.txt"))
    
    # Inform the user in the original channel that the archive has been sent
    await ctx.send("The archive has been sent to your DM.")
    print(f"Archived and sent all messages from {ctx.channel} to {ctx.author}'s DM")

@bot.command(name='scene_wrap')
@is_admin()
async def scene_wrap(ctx, channel_name: str = None):
    if not channel_name:
        await ctx.send("Please provide the name of the channel you want to archive messages to. Usage: `!scene_wrap <channel_name>`")
        return

    # Get the target channel by name
    target_channel = discord.utils.get(ctx.guild.channels, name=channel_name)
    if not target_channel:
        await ctx.send(f"Channel {channel_name} not found.")
        return
    
    messages = []
    members = set()
    first_message_date = None
    last_message_date = None

    async for message in ctx.channel.history(limit=None, oldest_first=True):
        # Skip messages with attachments
        if message.attachments:
            continue
        
        # Set the date of the first message
        if not first_message_date:
            first_message_date = message.created_at
            
        last_message_date = message.created_at
        members.add(message.author.display_name)  # Add member to the set
        messages.append(f"{message.created_at.strftime('%Y-%m-%d %H:%M')} - {message.author.display_name}: {message.content}")

    # Create a summary of the archived messages
    date_covered = f"{first_message_date.strftime('%Y-%m-%d %H:%M')} to {last_message_date.strftime('%Y-%m-%d %H:%M')}"
    members_list = ', '.join(sorted(members))
    
    output_text = (
        f"**Channel:** {ctx.channel.name}\n"
        f"**Dates Covered:** {date_covered}\n"
        f"**Members:** {members_list}\n\n"
        + '\n'.join(messages)
    )
    
 # Use an in-memory buffer to store the file content
    file_buffer = io.BytesIO()
    file_buffer.write(output_text.encode('utf-8'))
    file_buffer.seek(0)
    
    # Send the archive to the target channel
    await target_channel.send(file=discord.File(fp=file_buffer, filename="scene_archive.txt"))

    await ctx.send("Messages have been archived and posted.")

    # Bulk delete messages younger than 14 days and delete older ones individually
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

    # Bulk delete messages newer than 14 days in batches of 100
    while len(messages_to_delete_bulk) > 0:
        batch = messages_to_delete_bulk[:100]
        await ctx.channel.delete_messages(batch)
        messages_to_delete_bulk = messages_to_delete_bulk[100:]

    # Individually delete messages older than 14 days
    for message in messages_to_delete_individual:
        await message.delete()

bot.run(TOKEN)
