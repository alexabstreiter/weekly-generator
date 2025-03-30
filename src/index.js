import { Client, GatewayIntentBits, Events, Partials } from 'discord.js';
import dotenv from 'dotenv';
import OpenAI from 'openai';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import fs from 'fs';

// Load environment variables
dotenv.config();

// Initialize Discord client with necessary intents
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildMessageReactions,
  ],
  partials: [Partials.Channel, Partials.Message, Partials.Thread],
});

// Initialize OpenAI
const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

// Configuration
const DAYS_TO_LOOK_BACK = parseInt(process.env.DAYS_TO_LOOK_BACK) || 7;

// When the client is ready, run this code
client.once(Events.ClientReady, async () => {
  console.log(`Logged in as ${client.user.tag}`);
  
  try {
    // Get all guilds (servers) the bot is in
    for (const guild of client.guilds.cache.values()) {
      console.log(`Processing guild: ${guild.name}`);
      
      // Get all channels in the guild
      const channels = await guild.channels.fetch();
      
      // Process each text channel
      for (const [channelId, channel] of channels.filter(c => 
        c.type === 0 || // Text Channel
        c.type === 2 || // Voice Channel (may have text messages)
        c.type === 4 || // Guild Category
        c.type === 5 || // Announcement Channel
        c.type === 15   // Forum Channel
      )) {
        await processChannel(channel, guild);
      }
    }
    
    console.log('Processing complete! Bot will now exit.');
    process.exit(0);
  } catch (error) {
    console.error('Error during message processing:', error);
    process.exit(1);
  }
});

// Process a channel and its threads
async function processChannel(channel, guild) {
  try {
    // Skip categories since they don't have messages
    if (channel.type === 4) return;
    
    console.log(`Processing channel: ${channel.name}`);
    
    // Fetch messages from the last X days
    const messages = await fetchMessagesFromPast(channel, DAYS_TO_LOOK_BACK);
    
    if (messages.length === 0) {
      console.log(`No messages found in #${channel.name} for the past ${DAYS_TO_LOOK_BACK} days.`);
      return;
    }
    
    console.log(`Found ${messages.length} messages in #${channel.name}.`);
    
    // Generate a summary for this channel
    const summary = await generateSummary(channel, messages);
    
    // Print the summary
    console.log(`\n============= SUMMARY FOR #${channel.name} =============`);
    console.log(summary);
    console.log(`============= END SUMMARY =============\n`);
    
    // Process threads if this channel has any
    if (channel.threads && channel.threads.cache.size > 0) {
      for (const thread of channel.threads.cache.values()) {
        await processThread(thread);
      }
    }
    
    // For forum channels, fetch active threads
    if (channel.type === 15) {
      const activeThreads = await channel.threads.fetchActive();
      for (const thread of activeThreads.threads.values()) {
        await processThread(thread);
      }
    }
  } catch (error) {
    console.error(`Error processing channel ${channel.name}:`, error);
  }
}

// Process a thread
async function processThread(thread) {
  try {
    console.log(`Processing thread: ${thread.name}`);
    
    // Fetch messages from the thread
    const messages = await fetchMessagesFromPast(thread, DAYS_TO_LOOK_BACK);
    
    if (messages.length === 0) {
      console.log(`No messages found in thread ${thread.name} for the past ${DAYS_TO_LOOK_BACK} days.`);
      return;
    }
    
    console.log(`Found ${messages.length} messages in thread ${thread.name}.`);
    
    // Generate a summary for this thread
    const summary = await generateSummary(thread, messages, true);
    
    // Print the summary
    console.log(`\n============= SUMMARY FOR THREAD: ${thread.name} =============`);
    console.log(summary);
    console.log(`============= END SUMMARY =============\n`);
  } catch (error) {
    console.error(`Error processing thread ${thread.name}:`, error);
  }
}

// Fetch messages from the past X days
async function fetchMessagesFromPast(channel, days) {
  const messages = [];
  
  // Calculate the date X days ago
  const cutoffDate = new Date();
  cutoffDate.setDate(cutoffDate.getDate() - days);
  
  try {
    let lastId = null;
    let fetchMore = true;
    
    // Discord API can only fetch 100 messages at a time, so we need to paginate
    while (fetchMore) {
      const options = { limit: 100 };
      if (lastId) options.before = lastId;
      
      const fetchedMessages = await channel.messages.fetch(options);
      
      if (fetchedMessages.size === 0) {
        fetchMore = false;
        break;
      }
      
      // Store the ID of the oldest message for pagination
      lastId = fetchedMessages.last().id;
      
      // Filter and process messages
      for (const message of fetchedMessages.values()) {
        // Skip messages from before our cutoff date
        if (message.createdAt < cutoffDate) {
          fetchMore = false;
          break;
        }
        
        // Skip bot messages
        if (message.author.bot) continue;
        
        // Extract the relevant information
        const messageData = {
          id: message.id,
          content: message.content,
          author: message.author.username,
          timestamp: message.createdAt.toISOString(),
          attachments: [...message.attachments.values()].map(a => a.url),
          embeds: message.embeds.length
        };
        
        messages.push(messageData);
      }
      
      // If we fetched less than 100 messages, there are no more to fetch
      if (fetchedMessages.size < 100) {
        fetchMore = false;
      }
    }
    
    // Sort messages by timestamp (oldest first)
    messages.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    
    return messages;
  } catch (error) {
    console.error(`Error fetching messages from ${channel.name}:`, error);
    return [];
  }
}

// Generate a summary using OpenAI's GPT-4o
async function generateSummary(channel, messages, isThread = false) {
  // If no messages to summarize
  if (messages.length === 0) {
    return "No activity in this channel during the specified period.";
  }
  
  try {
    // Define a base system prompt template
    let baseSystemPrompt = `You are a helpful assistant summarizing Discord messages. 
The following are messages from the past ${DAYS_TO_LOOK_BACK} days in a Discord ${isThread ? 'thread' : 'channel'} named "${channel.name}".
Provide a concise but comprehensive summary of the main points, discussions, and notable events.`;
    
    // Channel-specific customization based on the channel name
    const channelName = channel.name.toLowerCase();
    
    // Different prompts based on channel type
    if (channelName.includes('announcement')) {
      baseSystemPrompt += `\nThis is an announcements channel, so focus on summarizing official updates, news, and important information shared with the community.`;
    } else if (channelName.includes('general')) {
      baseSystemPrompt += `\nThis is a general discussion channel, so focus on summarizing the main conversation topics, any questions asked and answered, and recurring themes.`;
    } else if (channelName.includes('help') || channelName.includes('support')) {
      baseSystemPrompt += `\nThis is a help/support channel, so focus on summarizing common issues raised, solutions provided, and unresolved problems.`;
    } else if (channelName.includes('dev') || channelName.includes('development')) {
      baseSystemPrompt += `\nThis is a development channel, so focus on summarizing technical discussions, code problems, solutions, and development updates.`;
    } else if (channelName.includes('idea') || channelName.includes('suggestion')) {
      baseSystemPrompt += `\nThis is an ideas/suggestions channel, so focus on summarizing proposed ideas, community reactions, and potential consensus.`;
    } else if (channelName.includes('feedback')) {
      baseSystemPrompt += `\nThis is a feedback channel, so focus on summarizing user feedback, criticism, praise, and response patterns.`;
    } else if (isThread) {
      baseSystemPrompt += `\nThis is a thread discussion, so focus on the specific topic being discussed, perspectives shared, and any conclusions reached.`;
    }
    
    // Format messages for the API
    const formattedMessages = messages.map(msg => {
      let content = msg.content;
      
      // Add information about attachments if any
      if (msg.attachments.length > 0) {
        content += `\n[Shared ${msg.attachments.length} attachment(s)]`;
      }
      
      // Add information about embeds if any
      if (msg.embeds > 0) {
        content += `\n[Shared ${msg.embeds} embed(s)]`;
      }
      
      return `${msg.author} (${new Date(msg.timestamp).toLocaleString()}): ${content}`;
    }).join('\n\n');
    
    // Get response from OpenAI
    const response = await openai.chat.completions.create({
      model: "gpt-4o",
      messages: [
        { role: "system", content: baseSystemPrompt },
        { role: "user", content: formattedMessages }
      ],
      max_tokens: 1500,
      temperature: 0.5
    });
    
    return response.choices[0].message.content;
  } catch (error) {
    console.error('Error generating summary with OpenAI:', error);
    return `Error generating summary: ${error.message}`;
  }
}

// Log in to Discord
client.login(process.env.DISCORD_TOKEN); 