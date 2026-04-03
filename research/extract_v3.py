import json
import re
try:
    with open('temp_chatgpt.html', 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # ChatGPT shares often store data in __NEXT_DATA__ script tag
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_content)
    if match:
        data = json.loads(match.group(1))
        # Navigate the JSON structure to find the messages
        # Structure: props -> pageProps -> sharedConversationResponse -> conversation_template -> mapping
        props = data.get("props", {})
        page_props = props.get("pageProps", {})
        shared_conv = page_props.get("sharedConversationResponse", {})
        
        # Sometimes it is here
        mapping = shared_conv.get("mapping", {})
        
        with open("chat_extracted.txt", "w", encoding="utf-8") as f_out:
            for node_id, node in mapping.items():
                message = node.get("message")
                if message:
                    author = message.get("author", {}).get("role", "unknown")
                    content = message.get("content", {})
                    parts = content.get("parts", [])
                    text = "".join([str(p) for p in parts])
                    if text:
                        f_out.write(f"ROLE: {author}\n")
                        f_out.write(f"TEXT: {text}\n")
                        f_out.write("-" * 50 + "\n")
        print("Success")
    else:
        print("NEXT_DATA not found")
except Exception as e:
    print(f"Error: {e}")
