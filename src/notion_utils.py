import os
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from notion_client import Client
from dotenv import load_dotenv
import re

# Load environment variables
load_dotenv()

# Get Notion credentials from environment variables
NOTION_TOKEN = os.getenv('NOTION_TOKEN')
NOTION_DATABASE_ID = os.getenv('NOTION_DATABASE_ID')

def get_notion_client() -> Optional[Client]:
    """Initialize and return a Notion client if credentials are available."""
    if not NOTION_TOKEN:
        print("Warning: NOTION_TOKEN not set in environment variables")
        return None
    return Client(auth=NOTION_TOKEN)

def parse_rich_text_with_links(text: str) -> List[Dict[str, Any]]:
    """
    Parse text containing markdown links, bold, and italic formatting into Notion rich text blocks.
    
    Args:
        text: Text that may contain markdown links [text](url), bold **text**, and italic *text*
        
    Returns:
        List of rich text blocks with proper formatting
    """
    rich_text = []
    current_pos = 0
    
    # Find all markdown links, bold, and italic patterns
    patterns = [
        (r'\[([^\]]+)\]\(([^)]+)\)', 'link'),  # Links
        (r'\*\*([^*]+)\*\*', 'bold'),         # Bold
        (r'\*([^*]+)\*', 'italic')            # Italic
    ]
    
    while current_pos < len(text):
        # Find the next occurrence of any pattern
        next_match = None
        next_pattern_type = None
        next_pos = len(text)
        
        for pattern, pattern_type in patterns:
            match = re.search(pattern, text[current_pos:])
            if match:
                match_start = current_pos + match.start()
                if match_start < next_pos:
                    next_match = match
                    next_pattern_type = pattern_type
                    next_pos = match_start
        
        # If no more patterns found, add remaining text
        if not next_match:
            if current_pos < len(text):
                rich_text.append({
                    "type": "text",
                    "text": {"content": text[current_pos:]}
                })
            break
        
        # Add text before the pattern
        if next_pos > current_pos:
            rich_text.append({
                "type": "text",
                "text": {"content": text[current_pos:next_pos]}
            })
        
        # Handle the pattern
        if next_pattern_type == 'link':
            link_text = next_match.group(1)
            link_url = next_match.group(2)
            rich_text.append({
                "type": "text",
                "text": {
                    "content": link_text,
                    "link": {"url": link_url}
                }
            })
        elif next_pattern_type == 'bold':
            bold_text = next_match.group(1)
            rich_text.append({
                "type": "text",
                "text": {
                    "content": bold_text,
                },
                "annotations": {"bold": True}
            })
        elif next_pattern_type == 'italic':
            italic_text = next_match.group(1)
            rich_text.append({
                "type": "text",
                "text": {
                    "content": italic_text,
                },
                "annotations": {"italic": True}
            })
        
        current_pos = next_pos + len(next_match.group(0))
    
    return rich_text if rich_text else [{"type": "text", "text": {"content": text}}]

def convert_markdown_to_blocks(markdown: str) -> List[Dict[str, Any]]:
    """
    Convert markdown text to Notion blocks.
    
    Args:
        markdown: The markdown text to convert
        
    Returns:
        List of Notion blocks
    """
    blocks = []
    lines = markdown.split('\n')
    
    for line in lines:
        # Skip empty lines
        if not line.strip():
            continue
            
        # Handle headings
        if line.startswith('#'):
            level = len(re.match('^#+', line).group())
            content = line.lstrip('#').strip()
            # Strip markdown formatting from heading content
            content = re.sub(r'\*\*|\*|\[([^\]]+)\]\(([^)]+)\)', lambda m: m.group(1) if m.group(1) else '', content)
            block_type = f"heading_{min(level, 3)}"  # Notion only supports h1-h3
            blocks.append({
                "object": "block",
                "type": block_type,
                block_type: {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": content}
                    }]
                }
            })
            continue
            
        # Handle bullet points
        if line.strip().startswith('- '):
            content = line.strip()[2:]
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": parse_rich_text_with_links(content)
                }
            })
            continue
            
        # Handle numbered lists
        numbered_match = re.match(r'^\d+\.\s+(.+)$', line.strip())
        if numbered_match:
            content = numbered_match.group(1)
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": parse_rich_text_with_links(content)
                }
            })
            continue
            
        # Handle code blocks
        if line.strip().startswith('```'):
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": line.strip()[3:]}
                    }]
                }
            })
            continue
            
        # Default to paragraph for regular text
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": parse_rich_text_with_links(line)
            }
        })
    
    return blocks

def add_summary_to_notion(summary: str, guild_name: str) -> bool:
    """
    Add the generated summary to the newest entry in the Notion database.
    The summary will be properly formatted using markdown.
    
    Args:
        summary: The markdown summary to add
        guild_name: The name of the Discord guild
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not NOTION_DATABASE_ID:
        print("Warning: NOTION_DATABASE_ID not set in environment variables")
        return False
        
    notion = get_notion_client()
    if not notion:
        return False
        
    try:
        # Get the most recent page from the database
        response = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            sorts=[{
                "property": "Created time",
                "direction": "descending"
            }],
            page_size=1
        )
        
        if not response.get('results'):
            print("No pages found in the Notion database")
            return False
            
        latest_page = response['results'][0]
        page_id = latest_page['id']
        
        # Create title block
        blocks = [
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{
                        "type": "text",
                        "text": {
                            "content": f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        }
                    }]
                }
            }
        ]
        
        # Convert markdown to Notion blocks
        content_blocks = convert_markdown_to_blocks(summary)
        blocks.extend(content_blocks)

        notion.blocks.children.append(
            block_id=page_id,
            children=blocks
        )
        
        print(f"Successfully added summary to Notion page: {latest_page.get('properties', {}).get('Name', {}).get('title', [{}])[0].get('text', {}).get('content', 'Unknown')}")
        return True
        
    except Exception as e:
        print(f"Error adding summary to Notion: {e}")
        return False 