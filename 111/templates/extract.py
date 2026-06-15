import sys
sys.stdout.reconfigure(encoding='utf-8')

f = open('E:\\2\\t1\\test01\\111\\app.py', 'rb')
data = f.read()
f.close()

start_marker = b'HTML = r"""'
start = data.find(start_marker)
end = data.find(b'"""', start + len(start_marker))

# Extract pre-HTML (from start to 'HTML = r"""\n')
pre_html = data[:start + len(start_marker) + 1]  # include newline after HTML = r"""

# Extract post-HTML (from closing """ to end)
post_html = data[end:]

# Write them to temp files
with open('E:\\2\\t1\\test01\\111\\templates\\_pre.py', 'wb') as f:
    f.write(pre_html)
with open('E:\\2\\t1\\test01\\111\\templates\\_post.py', 'wb') as f:
    f.write(post_html)

print(f'Pre: {len(pre_html)} bytes')
print(f'Post: {len(post_html)} bytes')
print('Done')
