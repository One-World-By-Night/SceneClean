import discord
from discord.ext import commands
import datetime
import pytz
import io

TOKEN = 'token goes here'

intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent explicitly
intents.messages = True          # Enable message-related events
intents.members = True           # Enable member-related events to access display names

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')

def escape_discord_markdown(text):
    # Escape Discord markdown characters to prevent formatting issues
    return discord.utils.escape_markdown(text)

def indent_multiline_message(content, indent_length):
    # Indent each line of a multi-line message
    indent = ' ' * indent_length
    return '\n'.join([indent + line if index > 0 else line for index, line in enumerate(content.splitlines())])

async def generate_archive(ctx):
    messages = []
    members = set()
    first_message_date = None
    last_message_date = None

    async for message in ctx.channel.history(limit=None, oldest_first=True):
        # Skip messages from the bot itself
        if message.author == bot.user:
            continue

        # Fetch the correct display name (nickname) for the server
        author_member = ctx.guild.get_member(message.author.id)
        author_display_name = author_member.display_name if author_member else message.author.name
        members.add(author_display_name)
        
        # Process message content
        if message.attachments:
            content = "[image]"
        else:
            content = escape_discord_markdown(message.content)

            # Replace mentions with server-specific display names
            for user in message.mentions:
                member = ctx.guild.get_member(user.id)
                if member:
                    content = content.replace(f"<@{user.id}>", member.display_name)
                else:
                    content = content.replace(f"<@{user.id}>", user.name)

        # Indent multi-line messages
        content = indent_multiline_message(content, indent_length=22)

        # Set the date of the first message
        if not first_message_date:
            first_message_date = message.created_at
            
        last_message_date = message.created_at

        # Format each message with a timestamp, author's name, and the message content
        formatted_message = f"{message.created_at.strftime('%Y-%m-%d %H:%M')} - {author_display_name}: {content}"
        messages.append(formatted_message)

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

    return file_buffer

@bot.command(name='end_scene')
async def end_scene(ctx):
    file_buffer = None  # Initialize file_buffer to None
    try:
        # Generate the archive
        file_buffer = await generate_archive(ctx)

        # Send the file in a DM
        dm_channel = await ctx.author.create_dm()
        await dm_channel.send(file=discord.File(fp=file_buffer, filename="scene_archive.txt"))

        # Check if the bot posted anything to the channel
        async for message in ctx.channel.history(limit=10):
            if message.author == bot.user:
                # Delete the message if it was posted to the channel
                await message.delete()

    except Exception as e:
        # Notify the user if there's an error
        await ctx.send(f"An error occurred while generating the archive: {str(e)}")
    
    finally:
        # Clear the buffer after sending
        if file_buffer:
            file_buffer.close()

@bot.command(name='scene_wrap')
@commands.has_permissions(administrator=True)
async def scene_wrap(ctx, channel_name: str = None):
    if not channel_name:
        await ctx.send("Please provide the name of the channel you want to archive messages to. Usage: `!scene_wrap <channel_name>`")
        return

    # Get the target channel by name
    target_channel = discord.utils.get(ctx.guild.channels, name=channel_name)
    if not target_channel:
        await ctx.send(f"Channel {channel_name} not found.")
        return
    
    file_buffer = None  # Initialize file_buffer to None
    try:
        file_buffer = await generate_archive(ctx)
        
        # Send the archive to the target channel
        await target_channel.send(file=discord.File(fp=file_buffer, filename="scene_archive.txt"))

        # Notify that the messages have been archived
        await ctx.send("Messages have been archived and posted.")

    except Exception as e:
        # Notify the user if there's an error
        await ctx.send(f"An error occurred while generating the archive: {str(e)}")
    
    finally:
        # Clear the buffer after sending
        if file_buffer:
            file_buffer.close()

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
        
@bot.command(name='help_scene_clean')
async def help_scene_clean(ctx):
    help_text = """
    **Scene Clean Bot Help**

    **Commands:**
    1. `!end_scene`: Archives the current channel's messages and sends the archive to your DM.
    2. `!scene_wrap <channel_name>`: Archives messages from the current channel and sends them to the specified channel. Requires administrator permissions.

    **Usage Examples:**
    - `!end_scene`: Use this to archive the messages in the channel and receive them via DM.
    - `!scene_wrap archive_channel`: Archives messages and posts the archive in `#archive_channel`.
    
    **Note:** Only administrators can use `!scene_wrap`.
    """
    await ctx.send(help_text)
    
bot.run(TOKEN)