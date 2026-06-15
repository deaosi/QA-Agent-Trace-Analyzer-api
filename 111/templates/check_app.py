import sys
sys.stdout.reconfigure(encoding='utf-8')
f = open('E:\\2\\t1\\test01\\111\\app.py', 'rb')
data = f.read()
f.close()

start_marker = b'HTML = r"""'
start = data.find(start_marker)
end = data.find(b'"""', start + len(start_marker))
html_bytes = data[start:end]

# Find all invalid UTF-8 sequences
pos = 0
errors = []
while pos < len(html_bytes):
    try:
        html_bytes[pos:].decode('utf-8')
        break
    except UnicodeDecodeError as e:
        errors.append((e.start, e.end))
        pos = pos + e.end

print(f'Total decode errors: {len(errors)}')
if errors:
    for i, (s, e) in enumerate(errors[:10]):
        ctx_start = max(0, s-10)
        ctx_end = min(len(html_bytes), e+10)
        bad_bytes = html_bytes[s:e].hex(' ')
        ctx = html_bytes[ctx_start:ctx_end]
        print(f'  Error {i}: pos {s}-{e}, bytes: [{bad_bytes}], context: {ctx}')
