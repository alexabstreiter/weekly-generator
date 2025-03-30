import os
import sys
import datetime
from typing import List

import discord
from discord.ext import commands
from dotenv import load_dotenv
from openai import OpenAI

# Add parent directory to path to import summary_generator
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import summary_generator
from src.utils import (
    MessageData, DAYS_TO_LOOK_BACK, CHANNELS_TO_IGNORE,
    is_thread_recent, fetch_messages_from_past, save_data_to_file
)

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Check if required environment variables are set
if not DISCORD_TOKEN or not OPENAI_API_KEY:
    print("Error: DISCORD_TOKEN and OPENAI_API_KEY must be set in the .env file.")
    sys.exit(1)

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Set the model names as constants
GPT4O_MODEL = "gpt-4o"  # More powerful model for guild summaries
SMALLER_MODEL = "gpt-4o-mini"  # Smaller, cheaper model for thread summaries

# Initialize Discord client with necessary intents
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

client = commands.Bot(command_prefix='!', intents=intents)

# Generate a summary for thread using the smaller model
async def generate_thread_summary(thread, messages: List[MessageData]) -> str:
    # If no messages to summarize
    if not messages:
        return "No activity in this thread during the specified period."
    
    try:
        # Define a base system prompt template
        base_system_prompt = f"""You are a helpful assistant summarizing Discord messages. 
The following are messages from the past {DAYS_TO_LOOK_BACK} days in a Discord thread named "{thread.name}".
Provide a concise but comprehensive summary of the thread discussion. Make it brief but capture key points."""
        
        # Format messages for the API
        formatted_messages = []
        for msg in messages:
            content = msg.content
            
            # Add information about attachments if any
            if msg.attachments:
                content += f"\n[Shared {len(msg.attachments)} attachment(s)]"
            
            # Add information about embeds if any
            if msg.embeds > 0:
                content += f"\n[Shared {msg.embeds} embed(s)]"
            
            formatted_message = f"{msg.author} ({datetime.datetime.fromisoformat(msg.timestamp).strftime('%c')}): {content}"
            formatted_messages.append(formatted_message)
        
        formatted_messages_text = "\n\n".join(formatted_messages)
        
        print(f"Using {SMALLER_MODEL} to summarize thread: {thread.name}")
        
        # Get response from OpenAI
        response = openai_client.chat.completions.create(
            model=SMALLER_MODEL,
            messages=[
                {"role": "system", "content": base_system_prompt},
                {"role": "user", "content": formatted_messages_text}
            ],
            max_tokens=500,  # Shorter summary for threads
            temperature=0.0
        )
        
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error generating thread summary with OpenAI: {e}")
        return f"Error generating summary: {str(e)}"

# Process a thread
async def process_thread(thread, all_messages, thread_summaries):
    try:
        # Skip processing if the thread is older than DAYS_TO_LOOK_BACK
        if not is_thread_recent(thread, DAYS_TO_LOOK_BACK):
            print(f"Skipping thread: {thread.name} (older than {DAYS_TO_LOOK_BACK} days)")
            return
            
        print(f"Processing thread: {thread.name}")
        
        # Fetch messages from the thread
        thread_channel_name = thread.parent.name if hasattr(thread, 'parent') and thread.parent else "Unknown Channel"
        messages = await fetch_messages_from_past(thread, DAYS_TO_LOOK_BACK, is_thread=True, thread_name=thread.name)

        if not messages:
            print(f"No messages found in thread {thread.name} for the past {DAYS_TO_LOOK_BACK} days.")
            return
        
        print(f"Found {len(messages)} messages in thread {thread.name}.")
        
        # Add messages to the overall collection, not use for now because we have the summaries
        #all_messages.extend(messages)
        
        # Generate a summary for this thread
        print("Generating summary for thread: ", thread.name, " with ", len(messages), " messages")
        summary = await generate_thread_summary(thread, messages)
        
        # Store the thread summary
        thread_key = f"{thread_channel_name} > {thread.name}"
        thread_summaries[thread_key] = summary
        
        print(f"Added summary for thread: {thread.name}")
    except Exception as e:
        print(f"Error processing thread {thread.name}: {e}")

# Process a channel and its threads
async def process_channel(channel, guild, all_messages, thread_summaries):
    try:
        # Skip categories since they don't have messages
        if isinstance(channel, discord.CategoryChannel):
            return
        
        # Skip channels in the ignore list
        if channel.name.lower() in [name.lower() for name in CHANNELS_TO_IGNORE]:
            print(f"Skipping ignored channel: {channel.name}")
            return
            
        print(f"Processing channel: {channel.name}")
        
        # Fetch messages from the last X days
        messages = await fetch_messages_from_past(channel, DAYS_TO_LOOK_BACK)
        
        if not messages:
            print(f"No messages found in #{channel.name} for the past {DAYS_TO_LOOK_BACK} days.")
        else:
            print(f"Found {len(messages)} messages in #{channel.name} for the past {DAYS_TO_LOOK_BACK} days.")
            
            # Add messages to the overall collection
            all_messages.extend(messages)
        
        # Process threads if this channel has any
        if hasattr(channel, 'threads'):
            # Process active threads
            for thread in channel.threads:
                if is_thread_recent(thread, DAYS_TO_LOOK_BACK):
                    await process_thread(thread, all_messages, thread_summaries)
                #else:
                #    print(f"Skipping thread: {thread.name} (older than {DAYS_TO_LOOK_BACK} days)")
            
            # Process archived threads, but only fetch those from the last DAYS_TO_LOOK_BACK days
            async for thread in channel.archived_threads(limit=20, before=None):
                if is_thread_recent(thread, DAYS_TO_LOOK_BACK):
                    await process_thread(thread, all_messages, thread_summaries)
                #else:
                #    print(f"Skipping archived thread: {thread.name} (older than {DAYS_TO_LOOK_BACK} days)")
        
        # For forum channels, handle active and archived threads similarly
        if isinstance(channel, discord.ForumChannel):
            # Process active threads
            for thread in channel.threads:
                if is_thread_recent(thread, DAYS_TO_LOOK_BACK):
                    await process_thread(thread, all_messages, thread_summaries)
                else:
                    print(f"Skipping forum thread: {thread.name} (older than {DAYS_TO_LOOK_BACK} days)")
            
            # Process archived threads, but only fetch those from the last DAYS_TO_LOOK_BACK days
            async for thread in channel.archived_threads(limit=100, before=None):
                if is_thread_recent(thread, DAYS_TO_LOOK_BACK):
                    await process_thread(thread, all_messages, thread_summaries)
                else:
                    print(f"Skipping archived forum thread: {thread.name} (older than {DAYS_TO_LOOK_BACK} days)")
    except Exception as e:
        print(f"Error processing channel {channel.name}: {e}")

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    
    try:
        # Get all guilds (servers) the bot is in
        for guild in client.guilds:
            print(f"Processing guild: {guild.name}")
            
            # Collect all messages and thread summaries
            all_messages = []
            thread_summaries = {}
            
            # Process each text channel
            for channel in guild.channels:
                if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                    await process_channel(channel, guild, all_messages, thread_summaries)
            
            # Save all collected data to a file before generating the summary
            data_filename = save_data_to_file(guild.name, all_messages, thread_summaries)
            
            # Now that we've processed all channels and threads, generate a guild-wide summary
            print(f"Generating overall summary for guild: {guild.name}")
            
            # Convert our MessageData objects to SGMessageData objects for the summary generator
            sg_messages = [msg.to_sg_message_data() for msg in all_messages]
            
            # Use the imported generate_guild_summary function
            guild_summary = summary_generator.generate_guild_summary(guild.name, sg_messages, thread_summaries)
            
            # Print the summary
            print(f"\n============= SUMMARY FOR GUILD: {guild.name} =============")
            print(guild_summary)
            print(f"\n============= END SUMMARY =============\n")
            
            # Save the summary to a markdown file
            summary_filename = f"{guild.name.replace(' ', '_')}_summary_{datetime.datetime.now().strftime('%Y%m%d')}.md"
            with open(summary_filename, 'w', encoding='utf-8') as f:
                f.write(guild_summary)
            print(f"Summary saved to {summary_filename}")
            break
            
    except Exception as e:
        print(f"Error during summary generation: {e}")

def main():
    try:
        client.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"Error running the bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 