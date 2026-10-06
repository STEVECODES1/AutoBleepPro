# Puter.js AI Chat App

This is a simple AI chat application built with Puter.js that allows you to chat with 500+ AI models from providers like OpenAI, Anthropic, Google, and more.

## Features

- Sign in with your Puter account
- Browse and select from 500+ available AI models
- Chat interface with message history
- Responsive design
- Powered by Puter.js SDK

## How to Use

### 1. Get a Puter Account
If you don't have one already, sign up at [https://puter.com](https://puter.com)

### 2. Run the Application
**Important**: Puter.js requires the app to be served via HTTP (not file://) for security reasons.

You can run it locally using one of these methods:

#### Option A: Using Python (if available)
```bash
# Navigate to the directory containing the HTML file
cd /path/to/AutoBleepPro-git

# Start a simple HTTP server
python -m http.server 8000

# Then open your browser to: http://localhost:8000/puter_ai_chat.html
```

#### Option B: Using Node.js (if available)
```bash
# Install http-server if you don't have it
npm install -g http-server

# Start the server
http-server -p 8000

# Then open your browser to: http://localhost:8000/puter_ai_chat.html
```

#### Option C: Use Puter's Hosting
You can also upload this file to your Puter cloud storage and host it directly on Puter:
1. Upload `puter_ai_chat.html` to your Puter account
2. Right-click the file and select "Host as website"
3. Visit the provided URL

### 3. Using the App
1. Click "Sign in with Puter" and authorize the application
2. Once signed in, you'll see your user info and storage stats
3. Select an AI model from the dropdown (default selects the first available)
4. Type your message and press Enter or click Send
5. The AI will respond using the selected model

## Technical Details

- Uses `puter.ai.chat()` for AI conversations
- Uses `puter.ai.listModels()` to get available models
- Uses `puter.auth` for authentication flow
- All processing happens client-side via Puter's servers
- No backend code required - pure frontend application

## Models Available
Puter provides access to 500+ AI models including:
- OpenAI: GPT-4o, GPT-4 Turbo, GPT-3.5 Turbo
- Anthropic: Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku
- Google: Gemini 1.5 Pro, Gemini 1.5 Flash
- Meta: Llama 3.1 405B, Llama 3.1 70B
- And many more from providers like Mistral, Cohere, Groq, etc.

## Notes
- The first time you select a model, there may be a slight delay as it loads
- Chat history is not persisted between sessions (you could extend this to use Puter's cloud storage or KV database)
- Make sure to keep your Puter credentials secure - the app uses OAuth so your password is never exposed to this application
- For production use, you might want to add error handling, persistence, and additional features

## Extending the App
You could extend this basic chat app to:
- Save chat histories to Puter cloud storage or KV database
- Add image generation with `puter.ai.txt2img()`
- Add text-to-speech with `puter.ai.txt2speech()`
- Add file upload capabilities for multimodal chats
- Add conversation branching or sharing features

---

Built with Puter.js - [https://docs.puter.com](https://docs.puter.com)