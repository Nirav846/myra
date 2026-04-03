import re
import html
try:
    with open('temp_chatgpt.html', 'r', encoding='utf-8') as f:
        content = f.read()
    # ChatGPT share data structure usually has conversation inside a script tag
    # We look for the text fields within that JSON
    matches = re.findall(r'"text"\s*:\s*"(.*?)"', content)
    with open('chat_extracted.txt', 'w', encoding='utf-8') as out:
        for m in matches:
            decoded = bytes(m, "utf-8").decode("unicode_escape")
            out.write(decoded + "\n" + "-"*40 + "\n")
except Exception as e:
    print(f"Error: {e}")
