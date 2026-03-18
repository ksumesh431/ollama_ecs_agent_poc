import asyncio
import json
import sys
import ollama
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "qwen3.5:27b"
CUSTOM_MCP_URL = "http://localhost:8000/mcp"

# Initialize Rich Console
console = Console()

SYSTEM_PROMPT = """You are an AWS infrastructure expert.
You have access to tools that query live AWS data.
CRITICAL INSTRUCTIONS:
1. If you need to know a service name, use list_ecs_services first, then get_ecs_service_details.
2. NEVER output an empty response. 
3. When you have the data, ALWAYS write a clear, human-readable summary as your final answer.
"""

async def run_chat():
    console.print("\n[bold cyan]Initializing AWS Agent...[/bold cyan]")
    
    async with streamablehttp_client(CUSTOM_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            ollama_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema,
                    },
                }
                for t in tools_result.tools
            ]

            console.print(f"[bold cyan]📦 Loaded {len(ollama_tools)} AWS tools. Ready to chat! (Type 'exit' to quit)[/bold cyan]\n")

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            client = ollama.Client(host=OLLAMA_HOST)

            while True:
                try:
                    # Using Python's built-in input for a cleaner interactive prompt line, 
                    # but formatting it immediately after.
                    console.print("\n[bold green]👤 You:[/bold green] ", end="")
                    user_input = input().strip()
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[dim]Exiting...[/dim]")
                    break
                    
                if user_input.lower() in ["exit", "quit", "q"]:
                    console.print("[dim]Goodbye![/dim]")
                    break
                if not user_input:
                    continue

                messages.append({"role": "user", "content": user_input})

                while True:
                    stream = client.chat(
                        model=OLLAMA_MODEL,
                        messages=messages,
                        tools=ollama_tools,
                        stream=True
                    )

                    assistant_msg = {"role": "assistant", "content": ""}
                    
                    in_thinking = False
                    in_content = False
                    content_buffer = ""
                    
                    for chunk in stream:
                        msg = chunk.get("message", {})

                        # 1. Handle live thinking stream (raw terminal dim color)
                        think_piece = msg.get("thinking") or ""
                        if think_piece:
                            if not in_thinking:
                                console.print("\n[dim]💭 Thinking...[/dim]")
                                in_thinking = True
                            # Print thought directly to standard out to avoid Markdown parsing on raw thoughts
                            sys.stdout.write(f"\033[90m{think_piece}\033[0m")
                            sys.stdout.flush()

                        # 2. Handle live answer stream (using Rich Live Markdown)
                        content_piece = msg.get("content") or ""
                        if content_piece:
                            if in_thinking:
                                # We just finished thinking, print a newline and start the live render
                                sys.stdout.write("\n\n")
                                sys.stdout.flush()
                                console.print("[bold cyan]🤖 Agent:[/bold cyan]")
                                in_thinking = False
                                
                            if not in_content:
                                in_content = True
                                live_render = Live(console=console, refresh_per_second=15)
                                live_render.start()
                                
                            content_buffer += content_piece
                            assistant_msg["content"] += content_piece
                            # Dynamically update the markdown render
                            live_render.update(Markdown(content_buffer))

                        # 3. Handle tool calls
                        if msg.get("tool_calls"):
                            assistant_msg["tool_calls"] = msg["tool_calls"]

                    # Stop the live render cleanly if it was started
                    if in_content:
                        live_render.stop()

                    messages.append(assistant_msg)

                    # Tool logic handling (Premature stop fix)
                    if not assistant_msg.get("tool_calls"):
                        if not assistant_msg["content"].strip():
                            console.print("[yellow]⚠️ Model output empty string. Forcing it to summarize...[/yellow]")
                            messages.append({
                                "role": "user", 
                                "content": "You stopped without answering. Please provide the final human-readable summary."
                            })
                            continue
                        else:
                            break

                    # Execute requested tools
                    for tool_call in assistant_msg["tool_calls"]:
                        fn = tool_call["function"]
                        name = fn["name"]
                        args = fn.get("arguments", {})

                        console.print(f"\n[bold yellow]🔧 Executing Tool :[/bold yellow] [yellow]{name}[/yellow]")
                        console.print(f"[yellow]   Arguments      : {json.dumps(args)}[/yellow]")

                        try:
                            result = await session.call_tool(name, args)
                            tool_output = result.content[0].text if result.content else "No result."
                            console.print(f"[bold green]   ✓ Response     :[/bold green] [green]{len(tool_output)} chars[/green]")
                        except Exception as e:
                            tool_output = f"Error: {str(e)}"
                            console.print(f"[bold red]   ❌ Error       :[/bold red] [red]{tool_output}[/red]")

                        messages.append({
                            "role": "tool",
                            "content": tool_output,
                        })

if __name__ == "__main__":
    asyncio.run(run_chat())
