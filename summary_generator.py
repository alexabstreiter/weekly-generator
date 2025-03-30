#!/usr/bin/env python3
import os
import sys
import json
import datetime
from datetime import datetime, timedelta, UTC
import argparse
import requests
from typing import List, Dict, Any, Optional
from collections import defaultdict

import openai
from dotenv import load_dotenv

# Add src directory to path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.utils import (
    MessageData, OPENAI_API_KEY, PIPEDRIVE_API_KEY, PIPEDRIVE_DOMAIN,
    DAYS_TO_LOOK_BACK, GPT4O_MODEL, smart_truncate, smart_truncate_start_end
)
from src.notion_utils import add_summary_to_notion

# Load environment variables
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PIPEDRIVE_API_KEY = os.getenv('PIPEDRIVE_API_KEY')
PIPEDRIVE_DOMAIN = os.getenv('PIPEDRIVE_DOMAIN')
DAYS_TO_LOOK_BACK = int(os.getenv('DAYS_TO_LOOK_BACK', '7'))
GPT4O_MODEL = "gpt-4o"  # Model for generating the summary
PIPEDRIVE_CUSTOM_FIELD_MEMBER_COUNT = "45b5cafd52c526bbc3d81cb4387fb4107ea77035"

# Check if OpenAI API key is available
if not OPENAI_API_KEY:
    print("Error: OPENAI_API_KEY must be set in the .env file.")
    sys.exit(1)

# Initialize OpenAI client
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

def calculate_total_won_value():
    won_deals = get_won_deals()
    total_value = sum(deal.get("value", 0) for deal in won_deals if deal)
    return round(total_value)

def get_won_deals():
    if not PIPEDRIVE_API_KEY or not PIPEDRIVE_DOMAIN:
        return []
    
    try:
        url = f"https://{PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/deals"
        params = {
            'api_token': PIPEDRIVE_API_KEY,
            'status': 'won'
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        if data.get('success', False):
            return data.get('data', [])
        return []
    except Exception as e:
        print(f"Error fetching won deals: {e}")
        return []

def filter_deals_with_value_change(deals):
    filtered_deals = []
    
    for deal in deals.get('data', []):
        
        update_time = deal.get("update_time")
        title = deal.get("title")  # Check if Pipedrive provides a separate timestamp
        won_time = deal.get('won_time')
        seven_days_ago = (datetime.now(UTC) - timedelta(days=DAYS_TO_LOOK_BACK)).isoformat()
        if update_time >= seven_days_ago and won_time is not None and won_time < seven_days_ago:
            print(title, update_time, won_time)
            filtered_deals.append(deal)
    
    return filtered_deals

def get_new_organizations():
    """
    Fetch all organizations that have been added to Pipedrive in the past 7 days.
    
    Returns:
        List[Dict[str, str]]: List of dictionaries containing organization names and URLs
    """
    if not PIPEDRIVE_API_KEY or not PIPEDRIVE_DOMAIN:
        return []
    
    try:
        url = f"https://{PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/organizations"
        params = {
            'api_token': PIPEDRIVE_API_KEY,
            'start': 0,
            'limit': 50,
            'sort': 'add_time DESC'
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        if not data.get('success', False):
            print(f"Error fetching organizations from Pipedrive: {data.get('error', 'Unknown error')}")
            return []
        
        seven_days_ago = (datetime.now(UTC) - timedelta(days=DAYS_TO_LOOK_BACK)).isoformat()
        new_orgs = []
        
        for org in data.get('data', []):
            add_time = org.get('add_time')
            if add_time and add_time >= seven_days_ago:
                org_name = org.get('name', 'Unnamed organization')
                org_member_count = org.get(PIPEDRIVE_CUSTOM_FIELD_MEMBER_COUNT, '?')
                new_orgs.append({
                    'name': org_name,
                    'member_count': org_member_count
                })
        
        return new_orgs
        
    except Exception as e:
        print(f"Error fetching new organizations from Pipedrive: {e}")
        return []

def fetch_recent_pipedrive_deals() -> Dict[str, Any]:
    if not PIPEDRIVE_API_KEY or not PIPEDRIVE_DOMAIN:
        print("Warning: Pipedrive API credentials not set. Skipping deal retrieval.")
        return {
            "converted": [],
            "churned": [],
            "lost_deals": [],
            "upgrades": [],
            "downgrades": [],
            "new_trials": [],
            "new_organizations": []
        }
    
    try:
        # Calculate the date 7 days ago from today
        seven_days_ago = (datetime.now() - timedelta(days=DAYS_TO_LOOK_BACK)).strftime('%Y-%m-%d')
        
        # Build the URL for Pipedrive API v1 request
        url = f"https://{PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/deals"
        
        params = {
            'api_token': PIPEDRIVE_API_KEY,
            'filter_id': 0,  # All deals
            'start': 0,
            'limit': 500,
            'sort': 'update_time DESC',
            'status': 'all_not_deleted'
        }
        
        # Make the request to Pipedrive API v1
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        deals_data = response.json()

        downgrades = []
        upgrades = []
        for deal in filter_deals_with_value_change(deals_data):
            deal_change = get_deal_value_change(deal.get('id'), deal.get('title'))
            if deal_change.startswith("Downgrade"):
                downgrades.append(deal_change)
            else:
                upgrades.append(deal_change)
        print(upgrades, downgrades)
        
        if not deals_data.get('success', False):
            print(f"Error fetching deals from Pipedrive: {deals_data.get('error', 'Unknown error')}")
            return {
                "converted": [],
                "churned": [],
                "lost_deals": [],
                "upgrades": [],
                "downgrades": [],
                "new_trials": [],
                "to_convert": [],
                "new_organizations": []
            }
        
        # Process the deals
        converted = []
        churned = []
        lost_deals = []
        to_convert = []
        new_trials = []
        
        for deal in deals_data.get('data', []):
            try:
                # Skip deals that weren't updated in the last 7 days
                update_time = deal.get('update_time')
                if not update_time:
                    continue
                    
                deal_title = deal.get('title', 'Unnamed deal')
                deal_value = deal.get('value', 0)
                deal_status = deal.get('status')
                deal_id = deal.get('id')
                deal_lost_reason = deal.get('lost_reason', 'No reason provided')
                deal_company = deal.get('org_id', {}).get('name', 'Unknown company')

                # Extract just the date part from the datetime string
                update_date = update_time.split(' ')[0]
                if update_date < seven_days_ago and deal_status != 'open':
                    continue
                    
                # Process the deal based on its status and stage
                if deal_status == 'won':
                    won_time = deal.get('won_time')
                    if won_time and won_time.split(' ')[0] >= seven_days_ago:
                        converted.append(f"{deal_company} +{deal_value}€/mo")
                elif deal_status == 'lost':
                    lost_time = deal.get('lost_time')
                    if lost_time and lost_time.split(' ')[0] >= seven_days_ago:
                        # Check if this was previously a won deal (churn) or never won (lost deal)
                        is_churn = 'churn' in deal_title.lower() or check_if_previously_won(deal_id)
                        if is_churn:
                            churned.append(f"{deal_company} -{deal_value}€/mo ({deal_lost_reason})")
                        else:
                            lost_deals.append(f"{deal_company} ({deal_lost_reason})")
                elif deal_status == 'open':
                    to_convert.append(deal_company)
                elif 'upgrade' in deal_title.lower():
                    upgrades.append(f"{deal_company} +{deal_value}€/mo")
                elif 'downgrade' in deal_title.lower():
                    downgrades.append(f"{deal_company} -{deal_value}€/mo")
                elif 'trial' in deal_title.lower() and deal_status == 'open':
                    new_trials.append(deal_company)
            except Exception as e:
                print(f"Error processing deal {deal.get('title', 'Unknown')}: {e}")
                continue
        
        # Fetch new organizations
        new_organizations = get_new_organizations()
        
        result = {
            "converted": converted,
            "churned": churned,
            "lost_deals": lost_deals,
            "upgrades": upgrades,
            "downgrades": downgrades,
            "new_trials": new_trials,
            "to_convert": to_convert,
            "new_organizations": new_organizations
        }
        
        print("Processed Pipedrive deals:", result)
        return result
        
    except Exception as e:
        print(f"Error fetching deals from Pipedrive: {e}")
        return {
            "converted": [],
            "churned": [],
            "lost_deals": [],
            "upgrades": [],
            "downgrades": [],
            "new_trials": [],
            "to_convert": [],
            "new_organizations": []
        }

def check_if_previously_won(deal_id: str) -> bool:
    """
    Check the history of a deal to determine if it was previously in a won state.
    
    Args:
        deal_id: The ID of the deal to check
        
    Returns:
        bool: True if the deal was previously won, False otherwise
    """
    if not PIPEDRIVE_API_KEY or not PIPEDRIVE_DOMAIN:
        return False
    
    try:
        # Build the URL for Pipedrive API request to get deal flow (history)
        url = f"https://{PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/deals/{deal_id}/flow"
        
        params = {
            'api_token': PIPEDRIVE_API_KEY
        }
        
        # Make the request to Pipedrive API
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        flow_data = response.json()
        
        if not flow_data.get('success', False):
            print(f"Error fetching deal flow for deal {deal_id}: {flow_data.get('error', 'Unknown error')}")
            return False
        
        # Check the flow data to see if the deal was ever won
        for flow_item in flow_data.get('data', []):
            # Look for a change to a won status in the history
            object_type = flow_item.get('object_type')
            if object_type == 'dealStatus' and flow_item.get('to_value') == 'won':
                print(f"Deal {deal_id} was previously won, now lost (churn)")
                return True
                
            if flow_item.get('data', []).get('old_value') == 'won':
                print(f"Deal {deal_id} was previously won, now lost (churn)")
                return True
        
        return False
        
    except Exception as e:
        print(f"Error checking deal history for deal {deal_id}: {e}")
        return False

def get_deal_value_change(deal_id: str, deal_title: str) -> Optional[str]:
    """
    Extract the deal value change from Pipedrive flow data.
    
    Args:
        deal_id: The ID of the deal to check
        
    Returns:
        str: A formatted string with old value, new value, and change, or None if no change found
    """
    if not PIPEDRIVE_API_KEY or not PIPEDRIVE_DOMAIN:
        return None
    
    try:
        url = f"https://{PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/deals/{deal_id}/flow"
        params = {'api_token': PIPEDRIVE_API_KEY}
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        flow_data = response.json()

        if not flow_data.get('success', False):
            print(f"Error fetching deal flow for deal {deal_id}: {flow_data.get('error', 'Unknown error')}")
            return None
        
        # Look for value changes in the flow data
        for flow_item in flow_data.get('data', []):
            if flow_item.get('object') == 'dealChange' and flow_item.get('timestamp') >= (datetime.now(UTC) - timedelta(days=DAYS_TO_LOOK_BACK)).isoformat():
                flow_item_data = flow_item.get('data', {})
                old_value = float(flow_item_data.get('old_value', '0'))
                new_value = float(flow_item_data.get('new_value', '0'))
                change = new_value - old_value
                print(old_value, new_value, change)
                if change < 0:
                    return f"Downgrade: {deal_title} {old_value}€ → {new_value}€ ({change}€)"
                else:
                    if change > 0:
                        return f"Upgrade: {deal_title} {old_value}€ → {new_value}€ ({change}€)"
        
        return None
        
    except Exception as e:
        print(f"Error checking deal value change for deal {deal_id}: {e}")
        return None

def load_data_from_file(filename: str) -> Dict[str, Any]:
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Convert message dictionaries back to MessageData objects
        messages = [MessageData(**msg) for msg in data['messages']]
        data['messages'] = messages
        
        return data
    except Exception as e:
        print(f"Error loading data from {filename}: {e}")
        sys.exit(1)

def generate_guild_summary(guild_name: str, all_messages: List[MessageData], thread_summaries: Dict[str, str]) -> str:
    # If no messages to summarize
    if not all_messages:
        return "# Discord Server Summary\n\nNo activity in this server during the specified period."
    
    try:
        # Count messages per channel for analytics
        channel_counts = defaultdict(int)
        for msg in all_messages:
            if msg.is_thread:
                key = f"#{msg.channel_name} (thread: {msg.thread_name})"
            else:
                key = f"#{msg.channel_name}"
            channel_counts[key] += 1
        
        # Create an activity overview
        activity_overview = "## Channel Activity\n\n"
        for channel, count in sorted(channel_counts.items(), key=lambda x: x[1], reverse=True):
            activity_overview += f"- **{channel}**: {count} messages\n"
        
        # Organize messages by channel for context
        channels_sample = {}
        for msg in all_messages:
            channel_key = msg.channel_name
            if channel_key not in channels_sample:
                channels_sample[channel_key] = []
            
            # Keep only a sample of messages per channel to avoid context limits
            if len(channels_sample[channel_key]) < 30:  # Sample up to 30 messages per channel
                channels_sample[channel_key].append(msg)
        
        # Format thread summaries for inclusion
        thread_summary_text = ""
        if thread_summaries:
            thread_summary_text = "## Thread Summaries\n\n"
            for thread_key, summary in thread_summaries.items():
                thread_summary_text += f"### {thread_key}\n{summary}\n\n"
        
        # Fetch recent deals from Pipedrive
        print("Fetching recent deals from Pipedrive...")
        pipedrive_deals = fetch_recent_pipedrive_deals()
        
        # Format Pipedrive deals information
        pipedrive_info = "## Recent Pipedrive Deals (Last 7 Days)\n\n"
        if any(pipedrive_deals.values()):
            pipedrive_info += "### Converted\n-"
            if pipedrive_deals["converted"]:
                for deal in pipedrive_deals["converted"]:
                    pipedrive_info += f"{deal}, "
            else:
                pipedrive_info += "None"
                
            pipedrive_info += "\n\n### Churned\n"
            if pipedrive_deals["churned"]:
                for deal in pipedrive_deals["churned"]:
                    pipedrive_info += f"{deal}, "
            else:
                pipedrive_info += "None"
                
            pipedrive_info += "\n\n### Lost Deals\n-"
            if pipedrive_deals["lost_deals"]:
                for deal in pipedrive_deals["lost_deals"]:
                    pipedrive_info += f"{deal}, "
            else:
                pipedrive_info += "None"
                
            pipedrive_info += "\n\n### Upgrades\n-"
            if pipedrive_deals["upgrades"]:
                for deal in pipedrive_deals["upgrades"]:
                    pipedrive_info += f"{deal}, "
            else:
                pipedrive_info += "None"
                
            pipedrive_info += "\n\n### Downgrades\n-"
            if pipedrive_deals["downgrades"]:
                for deal in pipedrive_deals["downgrades"]:
                    pipedrive_info += f"{deal}, "
            else:
                pipedrive_info += "None"
                
            pipedrive_info += "\n\n### New Trials\n-"
            if pipedrive_deals["new_trials"]:
                for deal in pipedrive_deals["new_trials"]:
                    pipedrive_info += f"{deal}, "
            else:
                pipedrive_info += "None"
            
            pipedrive_info += "\n\n### To convert\n-"
            if pipedrive_deals["to_convert"]:
                for deal in pipedrive_deals["to_convert"]:
                    pipedrive_info += f"{deal}, "
            else:
                pipedrive_info += "None"
            
            pipedrive_info += "\n\n### New Free Trials\n-"
            if pipedrive_deals["new_organizations"]:
                for org in pipedrive_deals["new_organizations"]:
                    pipedrive_info += f"{org['name']} ({org['member_count']}), "
        else:
            pipedrive_info += "No deals updated in the last 7 days or Pipedrive API credentials not configured.\n"
        
        print("PIPEDRIVE INFO")
        print(pipedrive_info)

        mrr = calculate_total_won_value()
        # Create a prompt for the overall summary
        base_system_prompt = f"""You are a helpful assistant summarizing Discord server activity.
 
Your task is to create a comprehensive summary of all activity in the "{guild_name}" Discord server.

The summary must:
1. Be in Markdown format
2. Contain 10-50 bullet points total
3. Cover the most important discussions, announcements, and activities
4. Group them into sections Sales & Marketing, HR & Ops, and Product & Engineering
5. Prioritize information based on importance, not just recency
6. In each bullet point, link relevant pages in Notion, Pluno.ai, or LinkedIn when relevant
7. Include the Pipedrive deals data in the Sales & Marketing section
8. When referencing URLs from messages, use them to create hyperlinks in the bullet points

I'll provide you with:
- A sample of messages from each channel (including any URLs found in the messages)
- Summaries of thread discussions
- Recent Pipedrive deals data

Here's an example of a summary. Make sure to use the same heading h2 for the sections. Leave the next week section empty.
## **Sales & Marketing**
*Key achievements & learnings:*
- MRR: 86,420€
    - Converted: Company1 +190€/mo, Company2 +640€/mo, Company3 +290€/mo
    - Churned: Company4 -149€/mo (low activity)
    - Lost deals: Company5 (unresponsive)
    - Downgrades: Company6 220€ to 79€ due to activity → lost MRR 141€
    - New free trials: Company7 (30K+), Company8 (27K+), Company9 (437)
- Social posts: [Blockscout shoutout](https://www.linkedin.com/posts/blockscout_shoutout-to-the-pluno-formerly-awesomeqa-activity-7310218553872289792-t7jA), [Customer Support Job Roundup](https://www.linkedin.com/posts/pluno-ai_customersupport-customersuccess-hiringnow-activity-7310638527921238016-_A4p), [Databutton testimonial](https://www.notion.so/d870b5eff2f74b90a29efae56d05d5b0?pvs=21), [Zendesk product update](https://www.linkedin.com/posts/pluno-ai_customersupport-customersuccess-activity-7309917194807644161-A2pS?utm_source=share&utm_medium=member_desktop&rcm=ACoAABpOoTEB_Ik5ojDb3TRyHRVesV0CxHRlbqI)
- 6 Discovery meetings
- Medical Company signed design partnership contract for initial evaluation phase 🚀
- Crafted [design partnership contract draft for packaging company](https://docs.google.com/document/d/ABCDEF/edit?usp=sharing)
- Sent proposal with workflow prototype, now they're discussing it internally even though we didn't agree to develop features before they sign with us.
- No leads from the email outreach so far (haven't tried enough yet)
- Started list of [Feature blocker](https://www.notion.so/Feature-blocker?pvs=21) for design partnerships / adoption of our product
- Updated the individual blog entries to ensure that they were showing their unique page descriptions in search results.
- Bowling night documented on [Life at Pluno instagram](https://www.instagram.com/life.at.pluno/).
- Best Wallet Success story published to website.
- Changelog published to website

*Next week:*

## **HR & Ops**
*Key achievements & learnings:*
- Marek's Anniversary
- Restructured milestone 4
- 1x Final interview, 2x 2nd interviews → All rejected, only 1 final interview in the pipeline

*Next week:*

## **Product & Engineering**
*Key achievements & learnings:*
- Zendesk:
    - Integration is live on production.
    - Whenever someone installs our bot, we now import their past tickets to provide more accurate and suitable responses.
    - Setup steps are updated with automated webhook and conversations integrations generations + setup guideline is updated accordingly
    - Authentication and security measures in Zendesk ticket sidebar are deployed
    - Websocket connection re-authentication issue and Zendesk ticket sidebar double scroll behavior is fixed
- Implemented channel-based knowledge separation
- Fixed: onboarding issue for superuses, zoom issue on iPhones

*Next week:*

Strictly fill in the following template and don't use information from the above example for the summary. All sections that should be filled are marked with <>:
## **Sales & Marketing**
*Key achievements & learnings:*
- MRR: {mrr}€
    - Converted: <list all converted customers with the deal value per month, e.g. Rings Protocol +190€/mo, Nova +640€/mo>
    - Churned: <list all churned customers from messages & Pipedrive with the deal value per month>
    - Lost deals: <list all lost deals from messages & Pipedrive>
    - Upgrades: <list all upgraded customers from messages & Pipedrive with the change in deal value per month>
    - Downgrades: <list all downgraded customers from Pipedrive with the change in deal value per month>
    - New free trials: <list all new free trials from Pipedrive with their member count in brackets>
- Social posts: <list all shared social posts with short 2 to 5 words description that are each hyperlinked to LinkedIn and comma separated>
<list other relevant updates in bullet points>
<list all ops updates for Sales & Marketing in bullet points>

*Next week:*
- Convert <list all companies to convert from Pipedrive>

## **HR & Ops**
*Key achievements & learnings:*
<list all relevant updates in bullet points but only include updates that are not about Sales/Marketing or Engineering>

*Next week:*

## **Product & Engineering**
*Key achievements & learnings:*
<for every message in #product-updates add a bullet point with 5 to 15 words describing the update depending on its complexity, don't include fixes or small issues>
- Fixes: <list all fixes in bullet points, also include fixes for specific customers>

*Next week:*


Focus on extracting key insights and important information rather than summarizing every message."""
        
        # Prepare a summary of message samples for each channel
        channel_samples_text = "## Message Samples\n\n"
        for channel_name, messages in channels_sample.items():
            channel_samples_text += f"### #{channel_name}\n"
            for msg in messages[:20]:  # Include up to 10 messages per channel in the prompt
                content = smart_truncate_start_end(msg.content, 303) if (channel_name == "customer-feedback") else smart_truncate(msg.content, 500) if (channel_name == "product-updates" or channel_name == "product-fixes") else smart_truncate(msg.content, 200)  # Use smart truncation instead of simple truncation
                content = content.replace('- ', '* ')
                channel_samples_text += f"- {msg.author}: {content}\n"
                if msg.urls:  # Add URLs if present
                    channel_samples_text += "  URLs:\n"
                    for url in msg.urls:
                        channel_samples_text += f"  - {url}\n"
            channel_samples_text += "\n"
        
        # For GPT-4o, we'll combine all the information but keep the prompt focused
        prompt = f"""Here's the information about the Discord server:

{activity_overview}

{thread_summary_text}

{pipedrive_info}

{channel_samples_text}

Please create a comprehensive summary following the guidelines in my system message. The summary should be in Markdown format with 10-50 bullet points covering the key activities, discussions and developments in the server. 
Combine info from messages and Pipedrive to create a comprehensive updates on deals and customers. When adding links make sure to directly hyperlink the 2 to 4 relevant words of the bullet point and only link the url that's from that message.
"""
        
        print(f"Using {GPT4O_MODEL} to generate overall guild summary for: {guild_name}")
        print("===================")
        print(prompt)
        print("===================")
        # Get response from OpenAI for the guild summary
        response = openai_client.chat.completions.create(
            model=GPT4O_MODEL,
            messages=[
                {"role": "system", "content": base_system_prompt},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2500,
            temperature=0
        )
        
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error generating guild summary with OpenAI: {e}")
        return f"# Error Generating Summary\n\nThere was an error generating the summary: {str(e)}"


def main():
    parser = argparse.ArgumentParser(description='Generate a summary from Discord data')
    parser.add_argument('data_file', help='Path to the JSON data file')
    args = parser.parse_args()
    
    # Load the data
    data = load_data_from_file(args.data_file)
    
    # Generate the summary
    summary = generate_guild_summary(
        data['guild_name'],
        data['messages'],
        data['thread_summaries']
    )
    
    # Print the summary
    print(summary)
    
    # Save the summary to a markdown file
    summary_filename = f"{data['guild_name'].replace(' ', '_')}_summary_{datetime.now().strftime('%Y%m%d')}.md"
    with open(summary_filename, 'w', encoding='utf-8') as f:
        f.write(summary)
    print(f"\nSummary saved to {summary_filename}")
    
    # Add summary to Notion
    if add_summary_to_notion(summary, data['guild_name']):
        print("Summary successfully added to Notion")
    else:
        print("Failed to add summary to Notion")

if __name__ == '__main__':
    main() 