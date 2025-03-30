# Discord Summary Bot

A Discord bot that extracts messages from all channels over the last 7 days and uses OpenAI models to generate summaries, which are then printed to the console.

## Features

- Extracts all messages from all channels for the past 7 days
- Properly handles threaded messages and organizes them hierarchically
- Customizes prompts for different channel types (announcements, general, help, development, etc.)
- Uses OpenAI models to generate comprehensive summaries
- Prints summaries to the console

## Prerequisites

- Python 3.8 or higher
- A Discord bot token
- An OpenAI API key 

## Setup

1. Clone this repository or download the source code
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Configure the bot:
   - Edit the `.env` file to include your Discord token and OpenAI API key
   - Make sure your Discord bot has the necessary permissions (see below)

## Discord Bot Permissions

Your Discord bot needs the following permissions:
- Read Message History
- Read Messages/View Channels
- Send Messages (optional, this bot doesn't send messages to Discord)

When creating your bot on the [Discord Developer Portal](https://discord.com/developers/applications), make sure to enable the following "Privileged Gateway Intents":
- Presence Intent (optional)
- Server Members Intent (optional)
- Message Content Intent (required)

## Running the Bot

```
python run.py
```

The bot will connect to Discord, extract messages from all channels in all servers it has access to, generate summaries using GPT-4o, and print them to the console.

After processing all channels and threads, the bot will automatically exit.

## Customization

You can customize the bot by editing the following variables in the `.env` file:
- `DAYS_TO_LOOK_BACK`: Number of days to look back for messages (default: 7)

For more advanced customization, you can modify the channel-specific prompts in the `generate_summary.py`.

## Limitations

- The Discord API limits the rate at which messages can be fetched, so processing large servers may take time
- GPT-4o has a context limit, so very active channels may have their messages truncated for summarization
- The bot uses the OpenAI API, so be aware of usage costs

## License

MIT 