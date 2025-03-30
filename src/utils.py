import os
import sys
from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import json
import re

# Add parent directory to path to import summary_generator
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PIPEDRIVE_API_KEY = os.getenv('PIPEDRIVE_API_KEY')
PIPEDRIVE_DOMAIN = os.getenv('PIPEDRIVE_DOMAIN')
DAYS_TO_LOOK_BACK = int(os.getenv('DAYS_TO_LOOK_BACK', '7'))

# Model names
GPT4O_MODEL = "gpt-4o"
SMALLER_MODEL = "gpt-4o-mini"

# List of channel names to ignore when processing
CHANNELS_TO_IGNORE = ['general', 'support', 'wfh', 'random', 'shoutouts', 'engineering']

class MessageData:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', '')
        self.content = kwargs.get('content', '')
        self.author = kwargs.get('author', '')
        self.timestamp = kwargs.get('timestamp', '')
        self.attachments = kwargs.get('attachments', [])
        self.embeds = kwargs.get('embeds', 0)
        self.channel_name = kwargs.get('channel_name', '')
        self.is_thread = kwargs.get('is_thread', False)
        self.thread_name = kwargs.get('thread_name', None)
        self.urls = kwargs.get('urls', [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'content': self.content,
            'author': self.author,
            'timestamp': self.timestamp,
            'attachments': self.attachments,
            'embeds': self.embeds,
            'channel_name': self.channel_name,
            'is_thread': self.is_thread,
            'thread_name': self.thread_name,
            'urls': self.urls
        }

    def to_sg_message_data(self):
        """Convert to SGMessageData for use with summary_generator functions."""
        return MessageData(
            id=self.id,
            content=self.content,
            author=self.author,
            timestamp=self.timestamp,
            attachments=self.attachments,
            embeds=self.embeds,
            channel_name=self.channel_name,
            is_thread=self.is_thread,
            thread_name=self.thread_name,
            urls=self.urls
        )

def extract_urls(text: str) -> List[str]:
    """Extract URLs from text using regex."""
    url_pattern = r'https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)'
    return re.findall(url_pattern, text)

async def fetch_messages_from_past(channel, days: int, is_thread: bool = False, thread_name: str = None) -> List[MessageData]:
    messages = []
    first_message = None
    
    # Calculate the date X days ago
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    try:
        # Only try to get the first message for threads
        if is_thread:
            try:
                first_message = await channel.parent.fetch_message(channel.id)
            except Exception as e:
                print(f"Error fetching first message from thread {channel.name}: {e}")
        
        last_id = None
        fetch_more = True
        
        # Discord API can only fetch 100 messages at a time, so we need to paginate
        while fetch_more:
            options = {}
            if last_id:
                options['before'] = last_id
            
            fetched_messages = []
            async for message in channel.history(limit=100, **options):
                fetched_messages.append(message)
            
            if not fetched_messages:
                fetch_more = False
                break
            
            # Store the ID of the oldest message for pagination
            last_id = fetched_messages[-1].id
            
            # Filter and process messages
            for message in fetched_messages:
                # Skip messages from before our cutoff date
                if message.created_at < cutoff_date:
                    fetch_more = False
                    break
                
                # Skip bot messages
                if message.author.bot:
                    continue
                
                # Skip if this is the first message (we'll add it separately for threads)
                if is_thread and first_message and message.id == first_message.id:
                    continue
                
                # Extract URLs from message content
                urls = extract_urls(message.content)
                urls = [url for url in urls if 'github.com' not in url.lower()]
                
                # Extract the relevant information
                message_data = MessageData(
                    id=str(message.id),
                    content=message.content,
                    author=message.author.name,
                    timestamp=message.created_at.isoformat(),
                    attachments=[a.url for a in message.attachments],
                    embeds=len(message.embeds),
                    channel_name=channel.name,
                    is_thread=is_thread,
                    thread_name=thread_name if is_thread else None,
                    urls=urls
                )
                
                messages.append(message_data)
            
            # If we fetched less than 100 messages, there are no more to fetch
            if len(fetched_messages) < 100:
                fetch_more = False
        
        # Sort messages by timestamp (oldest first)
        messages.sort(key=lambda x: x.timestamp)
        
        # If we found a first message and this is a thread, add it as additional context
        if is_thread and first_message and not first_message.author.bot:
            # Extract URLs from first message content
            urls = extract_urls(first_message.content)
            
            first_message_data = MessageData(
                id=str(first_message.id),
                content=f"[Thread Starter] {first_message.content}",  # Mark it as the thread starter
                author=first_message.author.name,
                timestamp=first_message.created_at.isoformat(),
                attachments=[a.url for a in first_message.attachments],
                embeds=len(first_message.embeds),
                channel_name=channel.name,
                is_thread=is_thread,
                thread_name=thread_name if is_thread else None,
                urls=urls
            )
            messages.insert(0, first_message_data)  # Add it at the beginning
        
        return messages
    except Exception as e:
        print(f"Error fetching messages from {channel.name}: {e}")
        return []

def is_thread_recent(thread, days: int) -> bool:
    """Check if a thread is recent enough to process."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    return thread.created_at and thread.created_at > cutoff_date

def smart_truncate(text: str, max_length: int) -> str:
    """Truncate text while preserving word boundaries."""
    if len(text) <= max_length:
        return text
    
    truncated = text[:max_length]
    last_space = truncated.rfind(' ')
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + '...'

def smart_truncate_start_end(text: str, max_length: int) -> str:
    """Truncate text while preserving word boundaries, showing both start and end of message."""
    if len(text) <= max_length:
        return text
    
    # Calculate how much space we have for each part
    # Reserve some space for the ellipsis
    part_length = (max_length - 3) // 2
    
    # Get the first part
    first_part = text[:part_length]
    last_space = first_part.rfind(' ')
    if last_space > 0:
        first_part = first_part[:last_space]
    
    # Get the last part
    last_part = text[-part_length:]
    first_space = last_part.find(' ')
    if first_space > 0:
        last_part = last_part[first_space:]
    
    return f"{first_part}...{last_part}"

def format_pipedrive_deals(deals: Dict[str, List[str]]) -> str:
    """Format Pipedrive deals information into markdown."""
    if not any(deals.values()):
        return "No deals updated in the last 7 days or Pipedrive API credentials not configured.\n"
    
    sections = [
        ("Converted", deals["converted"]),
        ("Churned", deals["churned"]),
        ("Lost Deals", deals["lost_deals"]),
        ("Upgrades", deals["upgrades"]),
        ("Downgrades", deals["downgrades"]),
        ("New Trials", deals["new_trials"])
    ]
    
    result = "## Recent Pipedrive Deals (Last 7 Days)\n\n"
    for title, items in sections:
        result += f"### {title}\n"
        if items:
            for item in items:
                result += f"- {item}\n"
        else:
            result += "- None\n"
        result += "\n"
    
    return result

def get_channel_counts(messages: List[MessageData]) -> Dict[str, int]:
    """Count messages per channel for analytics."""
    channel_counts = defaultdict(int)
    for msg in messages:
        if msg.is_thread:
            key = f"#{msg.channel_name} (thread: {msg.thread_name})"
        else:
            key = f"#{msg.channel_name}"
        channel_counts[key] += 1
    return channel_counts

def format_channel_samples(channels_sample: Dict[str, List[MessageData]]) -> str:
    """Format channel message samples into markdown."""
    result = "## Message Samples\n\n"
    for channel_name, messages in channels_sample.items():
        result += f"### #{channel_name}\n"
        for msg in messages[:10]:  # Include up to 10 messages per channel
            content = smart_truncate(msg.content, 200)
            result += f"- {msg.author}: {content}\n"
            if msg.urls:  # Add URLs if present
                result += "  URLs:\n"
                for url in msg.urls:
                    result += f"  - {url}\n"
        result += "\n"
    return result

def save_data_to_file(guild_name: str, all_messages: List[MessageData], thread_summaries: Dict[str, str]) -> str:
    """Save all data to a JSON file."""
    data = {
        'guild_name': guild_name,
        'timestamp': datetime.now().isoformat(),
        'messages': [msg.to_dict() for msg in all_messages],
        'thread_summaries': thread_summaries
    }
    
    # Create a filename with the guild name and timestamp
    filename = f"{guild_name.replace(' ', '_')}_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"Saved all data to {filename}")
    return filename 