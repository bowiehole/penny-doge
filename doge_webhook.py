"""Shared webhook helpers for Penny no-AI scripts. Never prints the key or URL."""
import json, os, re, stat, urllib.request, urllib.error

def norm_key(k):
    """Installers sometimes paste the panel's whole 'Authorization: Bearer <token>' header. Keep only the token."""
    k = str(k).strip()
    k = re.sub(r'^authorization\s*:\s*', '', k, flags=re.I)
    k = re.sub(r'^bearer(\s+|$)', '', k, flags=re.I)   # a lone 'Bearer' is not a key
    return k.strip()

def webhook(path):
    if not os.path.exists(path): return None
    if stat.S_IMODE(os.stat(path).st_mode) & 0o077: os.chmod(path, 0o600)  # keep it owner-only
    try:
        with open(path) as f: w = json.load(f)
    except Exception: return None
    if w.get('key'): w['key'] = norm_key(w['key'])
    return w if w.get('url') and w.get('key') else None

def post(w, body, timeout):
    req = urllib.request.Request(w['url'], data=json.dumps(body).encode(), method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + w['key'], 'X-Automation-Key': w['key']})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, None
    except urllib.error.HTTPError as e: return e.code, 'http'
    except Exception as e: return None, type(e).__name__   # class name only; no URL/key
