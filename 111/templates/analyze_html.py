import sys
sys.stdout.reconfigure(encoding='utf-8')

f = open('E:\\2\\t1\\test01\\111\\app.py', 'rb')
data = f.read()
f.close()

start_marker = b'HTML = r"""'
start = data.find(start_marker)
end = data.find(b'"""', start + len(start_marker))

html_bytes = data[start + len(start_marker) + 1:end]
html = html_bytes.decode('utf-8', errors='replace')

# Output HTML structure (tags only, no Chinese)
import re
# Find all HTML tags and their attributes
tags = re.findall(r'<(/?\w+)[^>]*>', html)
print('HTML tags found:', len(tags))

# Find all element IDs
ids = re.findall(r'id="([^"]+)"', html)
print('IDs:', ids)

# Find all onclick handlers
onclicks = re.findall(r'onclick="([^"]+)"', html)
print('onclick handlers:', onclicks)
