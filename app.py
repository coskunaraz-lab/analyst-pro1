import os, json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
from urllib.parse import urlparse, parse_qs
from urllib.error import HTTPError

PORT = int(os.environ.get('PORT', 8765))
API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

YF_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Referer': 'https://finance.yahoo.com/',
}

def fetch_yahoo(url):
    req = Request(url, headers=YF_HEADERS)
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))

def call_claude(prompt):
    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]
    }).encode('utf-8')
    req = Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': API_KEY,
            'anthropic-version': '2023-06-01',
        },
        method='POST'
    )
    try:
        with urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode('utf-8'))
            raw = ''.join(b.get('text', '') for b in data.get('content', []))
            raw = raw.strip()
            for fence in ['```json', '```']:
                if raw.startswith(fence):
                    raw = raw[len(fence):]
            if raw.endswith('```'):
                raw = raw[:-3]
            return json.loads(raw.strip())
    except HTTPError as e:
        err_body = e.read().decode()
        try:
            msg = json.loads(err_body).get('error', {}).get('message', err_body)
        except Exception:
            msg = err_body
        raise Exception(msg)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        ext = os.path.splitext(path)[1].lstrip('.')
        ct = {
            'html': 'text/html',
            'css': 'text/css',
            'js': 'application/javascript',
            'ico': 'image/x-icon',
        }.get(ext, 'text/plain')
        try:
            with open(path, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ct + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ('/', '/index.html'):
            self.send_file(os.path.join(SCRIPT_DIR, 'index.html'))
            return

        if path == '/api/quote':
            ticker = qs.get('ticker', [''])[0].upper()
            if not ticker:
                self.send_json({'error': 'ticker gerekli'}, 400)
                return
            try:
                url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d'
                self.send_json(fetch_yahoo(url))
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/api/summary':
            ticker = qs.get('ticker', [''])[0].upper()
            if not ticker:
                self.send_json({'error': 'ticker gerekli'}, 400)
                return
            try:
                mods = 'price,summaryDetail,defaultKeyStatistics,financialData,assetProfile'
                url = f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules={mods}'
                self.send_json(fetch_yahoo(url))
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/health':
            self.send_json({'status': 'ok', 'key': bool(API_KEY)})
            return

        fpath = os.path.join(SCRIPT_DIR, path.lstrip('/'))
        if os.path.isfile(fpath):
            self.send_file(fpath)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            self.send_json({'error': 'Gecersiz JSON'}, 400)
            return

        if self.path == '/api/claude':
            if not API_KEY:
                self.send_json({'error': 'Sunucuda API key tanimli degil'}, 401)
                return
            prompt = payload.get('prompt', '')
            if not prompt:
                self.send_json({'error': 'prompt gerekli'}, 400)
                return
            try:
                result = call_claude(prompt)
                self.send_json({'result': result})
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        self.send_response(404)
        self.end_headers()

if __name__ == '__main__':
    print(f'Analyst Pro sunucu basliyor: port {PORT}')
    print(f'API key: {"tanimli" if API_KEY else "EKSIK"}')
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
