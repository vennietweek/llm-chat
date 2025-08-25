from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import html as html_module
import httpx
import os
import traceback
import tiktoken
import markdown
from sqlalchemy import select, delete

from .db import database, metadata, engine
from .models import chat_messages

# FastAPI instance
app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent

# Serve static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# LM Studio endpoint
LM_STUDIO_API_URL = "http://host.docker.internal:1234/v1/chat/completions"
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "google/gemma-3-12b")

async def get_model_info():
    """Get model information including token limits from LM Studio"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{LM_STUDIO_API_URL.replace('/chat/completions', '/models')}")
            if response.status_code == 200:
                models = response.json()
                for model in models.get("data", []):
                    if model.get("id") == LM_STUDIO_MODEL:
                        return {
                            "context_length": model.get("context_length", 4096),
                            "max_tokens": model.get("max_tokens", 4096)
                        }
    except Exception as e:
        print(f"[WARNING] Could not get model info: {e}")
    
    return {"context_length": 4096, "max_tokens": 4096}

def estimate_tokens(text):
    """Estimate token count for a given text using tiktoken"""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:
        print(f"[WARNING] tiktoken failed, falling back to estimation: {e}")

    return len(text) // 4

def count_message_tokens(messages):
    """Count total tokens in a list of messages"""
    total_tokens = 0
    for message in messages:
        content_tokens = estimate_tokens(message.get("content", "")) # Count tokens
        total_tokens += content_tokens + 10 # Add buffer
    return total_tokens

def truncate_message_history(messages, max_tokens, user_input):
    """Truncate message history to fit within token limits"""
    system_tokens = estimate_tokens("You are a helpful assistant.") + 10
    user_input_tokens = estimate_tokens(user_input) + 10
    reserved_tokens = system_tokens + user_input_tokens + 512
    
    available_tokens = max_tokens - reserved_tokens
    
    if available_tokens <= 0:
        # If we can't even fit the current input, return empty history
        return []
    
    # Start from the most recent messages and work backwards
    truncated_messages = []
    current_tokens = 0
    
    for message in reversed(messages):
        message_tokens = estimate_tokens(message.get("content", "")) + 10
        
        if current_tokens + message_tokens <= available_tokens:
            truncated_messages.insert(0, message) # Insert at the start to maintain order
            current_tokens += message_tokens
        else:
            break
    
    return truncated_messages

# Startup: connect to DB and create tables
@app.on_event("startup")
async def startup():
    import asyncio
    
    # Retry database connection
    max_retries = 5
    for attempt in range(max_retries):
        try:
            metadata.create_all(engine)
            if not database.is_connected:
                await database.connect()
                print("[DB] Connected to database")
            delete_query = delete(chat_messages)
            await database.execute(delete_query)
            print("[DB] Cleared all previous messages for fresh start")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[DB] Connection attempt {attempt + 1} failed: {e}")
                print(f"[DB] Retrying in 2 seconds...")
                await asyncio.sleep(2)
            else:
                print(f"[DB] Failed to connect after {max_retries} attempts: {e}")
                raise

# Shutdown: disconnect DB
@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

# LLM call + chat history logic
async def query_llm(user_input):
    try:
        # Load previous messages (most recent messages up to 20)
        query = select(chat_messages).order_by(chat_messages.c.timestamp.desc()).limit(21)  # Get one extra to potentially exclude current
        rows = await database.fetch_all(query)
        
        # Filter out the current user input if it's the most recent message
        filtered_rows = []
        for row in rows:
            if len(filtered_rows) == 0 and row["role"] == "user" and row["message"] == user_input:
                continue  # Skip the current user input being processed
            filtered_rows.append(row)
            if len(filtered_rows) >= 20:  # Limit to 20 messages
                break
        
        reversed_rows = reversed(filtered_rows)

        message_history = [
            {
                "role": "assistant" if row["role"] == "LLM" else row["role"],
                "content": row["message"]
            }
            for row in reversed_rows
        ]
        
        print(f"[LLM] Processing with {len(message_history)} history messages")

        # Get model info to determine token limits
        model_info = await get_model_info()
        max_tokens = model_info["max_tokens"]
        context_length = model_info["context_length"]

        # Truncate message history to fit within token limits
        truncated_history = truncate_message_history(message_history, max_tokens, user_input)
        
        # Log token usage information
        original_count = len(message_history)
        truncated_count = len(truncated_history)
        if original_count != truncated_count:
            print(f"[TOKENS] History truncated: {original_count} -> {truncated_count} messages")
            print(f"[TOKENS] Model limit: {max_tokens}, Context length: {context_length}")
        
        total_tokens = count_message_tokens(truncated_history) + estimate_tokens(user_input) + estimate_tokens("You are a helpful assistant.")
        print(f"[TOKENS] Estimated total tokens: {total_tokens}/{max_tokens}")
        
        final_history = truncated_history

        payload = {
            "model": LM_STUDIO_MODEL,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                *final_history,
                {"role": "user", "content": user_input}
            ]
        }

        # LM Studio request
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(LM_STUDIO_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            llm_response = data["choices"][0]["message"]["content"]

        # Clean up the LLM response - remove quotes and escape characters
        llm_response = llm_response.strip()
        if llm_response.startswith('"') and llm_response.endswith('"'):
            llm_response = llm_response[1:-1]  # Remove surrounding quotes
        llm_response = llm_response.replace('\\n', '\n').replace('\\"', '"')  # Fix escape characters

        # Save the LLM response
        await database.execute(chat_messages.insert().values(role="LLM", message=llm_response))
        print(f"[DB] Saved LLM response to database: {llm_response[:50]}...")

        return llm_response

    except Exception as e:
        print("\n[⚠️ LM STUDIO API ERROR]", traceback.format_exc())
        if "timeout" in str(e).lower():
            return "[LM Studio timed out - please try again with a shorter message or check if LM Studio is running]"
        else:
            return f"[LM Studio unavailable: {str(e)}]"

def add_chat_bubble(role, message, is_last=False, allow_html=False):
    """Generate HTML for a chat bubble"""
    css_class = "user-bubble" if role == "user" else "llm-bubble"
    anchor = ' id="last-message"' if is_last else ''
    
    # For LLM messages, parse markdown. For user messages, escape HTML
    if role == "LLM" or role == "llm":
        if allow_html:
            # For loading messages with HTML animations
            formatted_message = message
        else:
            try:
                # Convert markdown to HTML with common extensions
                formatted_message = markdown.markdown(
                    message, 
                    extensions=['nl2br', 'fenced_code', 'tables', 'codehilite']
                )
            except Exception as e:
                print(f"[WARNING] Markdown parsing failed: {e}")
                # Fallback to HTML escaping
                formatted_message = html_module.escape(message).replace('\n', '<br>')
    else:
        # For user messages, just escape HTML and convert newlines
        
        formatted_message = html_module.escape(message).replace('\n', '<br>')
    
    return f"""
        <div class="chat-bubble {css_class}"{anchor}>
            <div class="bubble-message">{formatted_message}</div>
        </div>
    """

# Serve the chat form and history
@app.get("/", response_class=HTMLResponse)
async def serve_form():
    # Fetch full history
    history_query = select(chat_messages).order_by(chat_messages.c.timestamp.asc())
    chat_rows = await database.fetch_all(history_query)

    # Generate chat history HTML
    history_html = ""
    message_count = len(chat_rows)
    for idx, row in enumerate(chat_rows):
        role = row["role"]
        message = row["message"]
        is_last = (idx == message_count - 1)
        history_html += add_chat_bubble(role, message, is_last)

    # Full HTML response with chat history and input form
    full_html = f"""
        <html>
        <head>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <link rel='stylesheet' href='/static/style.css?v=12'>
        </head>
        <body>
        <div class="wrapper">
            <div class="chat-window" id="chat-window">
            {history_html}
            </div>
        </div>
        <form method='post' action='/chat' class='input-form'>
        <input type='text' name='user_input' placeholder='Type your message...' required autofocus>
        <button type='submit'>Send</button>
        </form>
        </body>
        </html>
    """
    return HTMLResponse(content=full_html)


# Chat handler
@app.post("/chat", response_class=HTMLResponse)
async def handle_input(user_input: str = Form(...)):
    try:
        # Save the user message to display it immediately
        await database.execute(chat_messages.insert().values(role="user", message=user_input))
        
        # Show the user message immediately with a loading indicator
        history_query = select(chat_messages).order_by(chat_messages.c.timestamp.asc())
        chat_rows = await database.fetch_all(history_query)

        # Generate chat history HTML
        history_html = ""
        message_count = len(chat_rows)
        for idx, row in enumerate(chat_rows):
            role = row["role"]
            message = row["message"]
            is_last = (idx == message_count - 1)
            history_html += add_chat_bubble(role, message, is_last)
        
        # Add loading indicator
        history_html += add_chat_bubble("LLM", '<span class="loading-spinner"></span>', True, allow_html=True)

        # Return the loading page
        loading_html = f"""
            <html>
            <head>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
            <link rel='stylesheet' href='/static/style.css?v=12'>
            <meta http-equiv="refresh" content="1;url=/process">
            </head>
            <body>
            <div class="wrapper">
                <div class="chat-window" id="chat-window">
                {history_html}
                </div>
            </div>
            <form method='post' action='/chat' class='input-form'>
            <input type='text' name='user_input' placeholder='Type your message...' required autofocus>
            <button type='submit'>Send</button>
            </form>
            </body>
            </html>
        """
        return HTMLResponse(content=loading_html)
        
    except Exception as e:
        return HTMLResponse(content=f"<pre>Error: {str(e)}</pre>", status_code=500)

# Process LLM response
@app.get("/process", response_class=HTMLResponse)
async def process_llm():
    try:
        # Get the most recent user message that doesn't have a corresponding LLM response
        query = select(chat_messages).where(chat_messages.c.role == "user").order_by(chat_messages.c.timestamp.desc()).limit(1)
        latest_user_msg = await database.fetch_one(query)
        
        # If we found a user message, process it
        if latest_user_msg:
            user_input = latest_user_msg["message"]
            print(f"[PROCESS] Processing user input: {user_input}")
            
            llm_response = await query_llm(user_input)
            print(f"[PROCESS] Got LLM response: {llm_response[:100]}...")
            
        else:
            print("[PROCESS] No user message found to process")
            
        # Redirect back to main page
        return HTMLResponse(content="""
            <html>
            <head>
            <meta http-equiv="refresh" content="0;url=/">
            </head>
            <body>
            <p>Response received, redirecting...</p>
            </body>
            </html>
        """)
        
    except Exception as e:
        print(f"[PROCESS ERROR] {str(e)}")
        return HTMLResponse(content=f"<pre>Error: {str(e)}</pre>", status_code=500)
