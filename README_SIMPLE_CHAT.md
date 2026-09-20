# Simple LLM Chat - Alternative to Puter.js AI Chat

This is a simpler AI chat application that works directly with configured LLM API keys (OpenAI, Gemini, Anthropic, etc.) without requiring Puter.js authentication or third-party cookies.

## Features
- ✅ No authentication popups or cookie issues
- ✅ Works directly with your configured API keys
- ✅ Supports multiple LLM providers (OpenAI, Gemini, Anthropic, Groq, XKiro, Cerebras)
- ✅ Local message history persistence
- ✅ Simple, clean interface
- ✅ No external dependencies beyond fetch API

## How to Use

### 1. Configure Your API Keys
The chat app looks for API keys in `localStorage` under the key `autobleep_llm_keys`.

**To set up your API keys:**

**Option A: Use the browser console (quickest)**
1. Open `simple_llm_chat.html` in your browser
2. Press `F12` to open DevTools
3. Go to the Console tab
4. Paste this command (replace with your actual keys):
   ```javascript
   localStorage.setItem('autobleep_llm_keys', JSON.stringify({
     openai: "your-openai-api-key-here",
     gemini: "your-google-api-key-here", 
     anthropic: "your-anthropic-api-key-here"
   }));
   ```
5. Press Enter
6. Reload the page

**Option B: Set up once and reuse**
The keys will persist in your browser's localStorage until you clear them.

### 2. Get API Keys
- **OpenAI**: https://platform.openai.com/api-keys
- **Google Gemini**: https://makersuite.google.com/app/apikey
- **Anthropic**: https://console.anthropic.com/
- **Groq**: https://console.groq.com/keys
- **XKiro**: https://api.xkiro.com/
- **Cerebras**: https://cerebras.ai/cloud/apikey/

### 3. Run the Application
**Important**: This needs to be served via HTTP (not file://) for some API calls to work due to CORS.

**Option 1: Using python3 (recommended - works in your environment)**
```bash
cd D:/AutoBleepPro-git
python3 -m http.server 8000
# Then visit: http://localhost:8000/simple_llm_chat.html
```

**Option 2: Using npx http-server (what you already have working)**
```bash
cd D:/AutoBleepPro-git
npx http-server -p 8000
# Then visit: http://localhost:8000/simple_llm_chat.html
```

### 4. Using the Chat
1. Select your preferred AI provider from the dropdown
2. Type your message and press Enter or click Send
3. View the AI response
4. Your chat history is saved locally and persists between sessions

## Why This Is Easier Than Puter.js
- ❌ No popup windows for authentication
- ❌ No third-party cookie settings to configure
- ❌ No "Failed to sign in" errors from OAuth flows
- ❌ No need to manage Puter account permissions
- ✅ Direct API calls to LLM providers you already have keys for
- ✅ Works immediately after setting up API keys
- ✅ Familiar chat interface without extra complexity

## Troubleshooting
If you see errors:
1. **Check your API key** - make sure it's valid and has credit/quota
2. **Check the provider selection** - ensure you've selected one with a valid key
3. **Check network tab in DevTools** (`F12` → Network) for failed requests
4. **Common errors**:
   - `401 Unauthorized`: Invalid or missing API key
   - `429 Rate limit exceeded`: You've hit your provider's limits
   - `400 Bad Request`: Message too long or malformed request

## Integration with Your Existing Setup
This chat app complements your AutoBleepPro pipeline:
- Use it to test prompts for your LLM-based clip scoring (`autoreel/llm_highlights.py`)
- Experiment with different models before committing to API keys in your workflow
- Get AI suggestions for video editing, clip titles, or content ideas
- All without leaving your browser or dealing with complex authentication

---

**Ready to chat?** Just set up your API keys once and you're good to go! 🚀