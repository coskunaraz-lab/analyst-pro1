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

def fetch_news(query):
    """Yahoo Finance haber arama"""
    news_items = []

def fetch_ohlcv(ticker, period='2y'):
    """Yahoo Finance'tan günlük OHLCV verisi çek"""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range={period}&includePrePost=false'
    data = fetch_yahoo(url)
    result = data['chart']['result'][0]
    meta = result['meta']
    timestamps = result['timestamp']
    q = result['indicators']['quote'][0]
    closes  = q['close']
    opens   = q['open']
    highs   = q['high']
    lows    = q['low']
    volumes = q['volume']
    bars = []
    for i in range(len(timestamps)):
        if closes[i] is None: continue
        bars.append({
            't': timestamps[i],
            'o': opens[i],
            'h': highs[i],
            'l': lows[i],
            'c': closes[i],
            'v': volumes[i] or 0,
        })
    return bars, meta

def sma(closes, n):
    result = [None] * len(closes)
    for i in range(n-1, len(closes)):
        result[i] = sum(closes[i-n+1:i+1]) / n
    return result

def ema(closes, n):
    result = [None] * len(closes)
    k = 2 / (n + 1)
    for i in range(len(closes)):
        if closes[i] is None:
            continue
        if result[i-1] is None and i >= n-1:
            result[i] = sum(closes[i-n+1:i+1]) / n
        elif result[i-1] is not None:
            result[i] = closes[i] * k + result[i-1] * (1 - k)
    return result

def rsi_series(closes, n=14):
    result = [None] * len(closes)
    if len(closes) < n+1: return result
    gains, losses = [], []
    for i in range(1, n+1):
        d = closes[i] - closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    avg_g = sum(gains)/n; avg_l = sum(losses)/n
    for i in range(n, len(closes)):
        if i > n:
            d = closes[i] - closes[i-1]
            avg_g = (avg_g*(n-1) + max(d,0)) / n
            avg_l = (avg_l*(n-1) + max(-d,0)) / n
        result[i] = 100 - (100/(1 + (avg_g/avg_l if avg_l else 1e9)))
    return result

def macd_series(closes, fast=12, slow=26, signal=9):
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [None]*len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]
    valid = [(i,v) for i,v in enumerate(macd_line) if v is not None]
    signal_line = [None]*len(closes)
    if len(valid) >= signal:
        idxs = [x[0] for x in valid]
        vals = [x[1] for x in valid]
        sig = ema(vals, signal)
        for j, idx in enumerate(idxs):
            signal_line[idx] = sig[j]
    return macd_line, signal_line

def bollinger(closes, n=20, k=2):
    upper = [None]*len(closes)
    lower = [None]*len(closes)
    mid   = sma(closes, n)
    for i in range(n-1, len(closes)):
        sl = closes[i-n+1:i+1]
        mn = sum(sl)/n
        sd = (sum((x-mn)**2 for x in sl)/n)**0.5
        upper[i] = mn + k*sd
        lower[i] = mn - k*sd
    return upper, lower, mid

def run_backtest(bars, strategy, params):
    closes = [b['c'] for b in bars]
    n = len(closes)
    trades = []
    position = None  # {'entry_price', 'entry_idx', 'type'}

    # ── Build indicators ──────────────────────────────────
    if strategy == 'sma_cross':
        fast_n = int(params.get('fast', 20))
        slow_n = int(params.get('slow', 50))
        fast = sma(closes, fast_n)
        slow = sma(closes, slow_n)
        for i in range(1, n):
            if fast[i] is None or slow[i] is None or fast[i-1] is None or slow[i-1] is None:
                continue
            if position is None and fast[i-1] <= slow[i-1] and fast[i] > slow[i]:
                position = {'entry': closes[i], 'idx': i, 'type': 'long'}
            elif position is not None and fast[i-1] >= slow[i-1] and fast[i] < slow[i]:
                pnl = (closes[i] - position['entry']) / position['entry']
                trades.append({'entry': position['entry'], 'exit': closes[i], 'pnl': pnl,
                                'bars': i - position['idx']})
                position = None

    elif strategy == 'ema_cross':
        fast_n = int(params.get('fast', 12))
        slow_n = int(params.get('slow', 26))
        fast = ema(closes, fast_n)
        slow = ema(closes, slow_n)
        for i in range(1, n):
            if fast[i] is None or slow[i] is None or fast[i-1] is None or slow[i-1] is None:
                continue
            if position is None and fast[i-1] <= slow[i-1] and fast[i] > slow[i]:
                position = {'entry': closes[i], 'idx': i, 'type': 'long'}
            elif position is not None and fast[i-1] >= slow[i-1] and fast[i] < slow[i]:
                pnl = (closes[i] - position['entry']) / position['entry']
                trades.append({'entry': position['entry'], 'exit': closes[i], 'pnl': pnl,
                                'bars': i - position['idx']})
                position = None

    elif strategy == 'rsi_ob_os':
        rsi_n  = int(params.get('rsi_period', 14))
        ob     = float(params.get('overbought', 70))
        os_lvl = float(params.get('oversold', 30))
        rsi = rsi_series(closes, rsi_n)
        for i in range(1, n):
            if rsi[i] is None or rsi[i-1] is None: continue
            if position is None and rsi[i-1] < os_lvl and rsi[i] >= os_lvl:
                position = {'entry': closes[i], 'idx': i}
            elif position is not None and rsi[i-1] > ob and rsi[i] <= ob:
                pnl = (closes[i] - position['entry']) / position['entry']
                trades.append({'entry': position['entry'], 'exit': closes[i], 'pnl': pnl,
                                'bars': i - position['idx']})
                position = None

    elif strategy == 'macd_cross':
        macd_line, signal_line = macd_series(closes)
        for i in range(1, n):
            if macd_line[i] is None or signal_line[i] is None: continue
            if macd_line[i-1] is None or signal_line[i-1] is None: continue
            if position is None and macd_line[i-1] <= signal_line[i-1] and macd_line[i] > signal_line[i]:
                position = {'entry': closes[i], 'idx': i}
            elif position is not None and macd_line[i-1] >= signal_line[i-1] and macd_line[i] < signal_line[i]:
                pnl = (closes[i] - position['entry']) / position['entry']
                trades.append({'entry': position['entry'], 'exit': closes[i], 'pnl': pnl,
                                'bars': i - position['idx']})
                position = None

    elif strategy == 'bollinger_bounce':
        bb_n = int(params.get('bb_period', 20))
        bb_k = float(params.get('bb_std', 2.0))
        upper, lower, mid = bollinger(closes, bb_n, bb_k)
        for i in range(1, n):
            if lower[i] is None or mid[i] is None: continue
            if position is None and closes[i-1] < lower[i-1] and closes[i] > lower[i]:
                position = {'entry': closes[i], 'idx': i}
            elif position is not None and closes[i] >= mid[i]:
                pnl = (closes[i] - position['entry']) / position['entry']
                trades.append({'entry': position['entry'], 'exit': closes[i], 'pnl': pnl,
                                'bars': i - position['idx']})
                position = None

    elif strategy == 'rsi_divergence':
        rsi_n = int(params.get('rsi_period', 14))
        lookback = int(params.get('lookback', 5))
        rsi = rsi_series(closes, rsi_n)
        for i in range(lookback, n-1):
            if any(rsi[j] is None for j in range(i-lookback, i+1)): continue
            price_low = min(closes[i-lookback:i+1])
            rsi_low   = min(rsi[i-lookback:i+1])
            # Bullish divergence: price new low but RSI higher low
            if closes[i] <= price_low and rsi[i] > rsi_low + 3 and position is None:
                position = {'entry': closes[i], 'idx': i}
            elif position is not None and rsi[i] > 60:
                pnl = (closes[i] - position['entry']) / position['entry']
                trades.append({'entry': position['entry'], 'exit': closes[i], 'pnl': pnl,
                                'bars': i - position['idx']})
                position = None

    # Close any open position at last bar
    if position is not None:
        pnl = (closes[-1] - position['entry']) / position['entry']
        trades.append({'entry': position['entry'], 'exit': closes[-1], 'pnl': pnl,
                        'bars': n - position['idx'], 'open': True})

    # ── Metrics ───────────────────────────────────────────
    if not trades:
        return {'trades': [], 'metrics': {}, 'equity': []}

    wins  = [t for t in trades if t['pnl'] > 0]
    losses= [t for t in trades if t['pnl'] <= 0]
    win_rate = len(wins)/len(trades) if trades else 0
    total_gain = sum(t['pnl'] for t in wins)
    total_loss = abs(sum(t['pnl'] for t in losses))
    profit_factor = total_gain / total_loss if total_loss > 0 else float('inf')
    avg_win  = sum(t['pnl'] for t in wins)  / len(wins)   if wins   else 0
    avg_loss = sum(t['pnl'] for t in losses)/ len(losses) if losses else 0

    # Equity curve & max drawdown
    equity = 1.0
    peak   = 1.0
    max_dd = 0.0
    eq_curve = [1.0]
    for t in trades:
        equity *= (1 + t['pnl'])
        eq_curve.append(round(equity, 4))
        if equity > peak: peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd: max_dd = dd

    total_return = (equity - 1) * 100
    bah_return   = (closes[-1] - closes[0]) / closes[0] * 100  # Buy & Hold

    metrics = {
        'total_trades':   len(trades),
        'win_rate':       round(win_rate * 100, 1),
        'profit_factor':  round(profit_factor, 2) if profit_factor != float('inf') else 999,
        'max_drawdown':   round(max_dd * 100, 1),
        'total_return':   round(total_return, 1),
        'bah_return':     round(bah_return, 1),
        'avg_win':        round(avg_win * 100, 2),
        'avg_loss':       round(avg_loss * 100, 2),
        'avg_bars':       round(sum(t['bars'] for t in trades)/len(trades), 1),
        'best_trade':     round(max(t['pnl'] for t in trades)*100, 2),
        'worst_trade':    round(min(t['pnl'] for t in trades)*100, 2),
    }
    recent = [{'entry': round(t['entry'],2), 'exit': round(t['exit'],2),
               'pnl': round(t['pnl']*100,2), 'bars': t['bars']} for t in trades[-10:]]
    return {'trades': recent, 'metrics': metrics, 'equity': eq_curve[-50:]}

    """Yahoo Finance haber arama + DuckDuckGo fallback"""
    news_items = []

    # 1) Yahoo Finance news search
    try:
        q = query.replace(' ', '+')
        url = f'https://query1.finance.yahoo.com/v1/finance/search?q={q}&newsCount=8&quotesCount=0'
        data = fetch_yahoo(url)
        for item in (data.get('news') or [])[:8]:
            title = item.get('title', '')
            publisher = item.get('publisher', '')
            ptime = item.get('providerPublishTime', 0)
            link = item.get('link', '')
            if title:
                news_items.append({
                    'title': title,
                    'source': publisher,
                    'time': ptime,
                    'url': link
                })
    except Exception as e:
        print(f'Yahoo news error: {e}')

    # 2) DuckDuckGo news (no key required)
    if len(news_items) < 4:
        try:
            from urllib.parse import quote
            q2 = quote(query + ' stock news')
            ddg_url = f'https://api.duckduckgo.com/?q={q2}&format=json&t=analystpro'
            req2 = Request(ddg_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req2, timeout=10) as r:
                ddg = json.loads(r.read().decode('utf-8'))
                for topic in (ddg.get('RelatedTopics') or [])[:5]:
                    text = topic.get('Text', '')
                    url2 = topic.get('FirstURL', '')
                    if text:
                        news_items.append({'title': text, 'source': 'DuckDuckGo', 'time': 0, 'url': url2})
        except Exception as e:
            print(f'DDG error: {e}')

    return news_items

def sanitize_json_string(raw):
    """JSON string'i temizle ve parse et — özel karakterler, bozuk tırnaklar vb."""
    raw = raw.strip()
    # Remove markdown fences
    for fence in ['```json', '```']:
        if raw.startswith(fence):
            raw = raw[len(fence):]
    if raw.endswith('```'):
        raw = raw[:-3]
    raw = raw.strip()

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object between first { and last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end+1])
        except json.JSONDecodeError:
            pass

    # Try to extract JSON array between first [ and last ]
    start = raw.find('[')
    end = raw.rfind(']')
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end+1])
        except json.JSONDecodeError:
            pass

    # Last resort: fix common issues
    # Replace smart quotes with standard quotes
    replacements = [
        ('\u201c', '"'), ('\u201d', '"'),  # " "
        ('\u2018', "'"), ('\u2019', "'"),  # ' '
        ('\u201e', '"'), ('\u201f', '"'),  # „ ‟
        ('\u00e2\u0080\u009c', '"'),       # UTF-8 mangled
        ('\u00e2\u0080\u009d', '"'),
    ]
    fixed = raw
    for old, new in replacements:
        fixed = fixed.replace(old, new)

    start = fixed.find('{')
    end = fixed.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(fixed[start:end+1])
        except json.JSONDecodeError as e:
            raise Exception(f'JSON parse hatası: {e} — Ham yanıt: {raw[:200]}')

    raise Exception(f'Geçerli JSON bulunamadı. Ham yanıt: {raw[:200]}')

def call_claude(prompt):
    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2000,
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
            return sanitize_json_string(raw)
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
                mods = 'price,summaryDetail,defaultKeyStatistics,financialData,assetProfile,balanceSheetHistory'
                url = f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules={mods}'
                self.send_json(fetch_yahoo(url))
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/health':
            self.send_json({'status': 'ok', 'key': bool(API_KEY)})
            return

        if path == '/api/news':
            query = qs.get('q', [''])[0]
            if not query:
                self.send_json({'error': 'q gerekli'}, 400)
                return
            try:
                items = fetch_news(query)
                self.send_json({'news': items})
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        if path == '/api/backtest':
            ticker   = qs.get('ticker',   [''])[0].upper()
            strategy = qs.get('strategy', ['sma_cross'])[0]
            period   = qs.get('period',   ['2y'])[0]
            # params as JSON string
            import json as _json
            params_raw = qs.get('params', ['{}'])[0]
            try:
                params = _json.loads(params_raw)
            except Exception:
                params = {}
            if not ticker:
                self.send_json({'error': 'ticker gerekli'}, 400)
                return
            try:
                bars, meta = fetch_ohlcv(ticker, period)
                result = run_backtest(bars, strategy, params)
                result['meta'] = {
                    'ticker':    ticker,
                    'strategy':  strategy,
                    'period':    period,
                    'bars_total': len(bars),
                    'currency':  meta.get('currency','USD'),
                    'name':      meta.get('longName', ticker),
                }
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
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

        if self.path == '/api/trading_plan':
            if not API_KEY:
                self.send_json({'error': 'API key eksik'}, 401)
                return
            market   = payload.get('market', '')
            profile  = payload.get('profile', '')
            context  = payload.get('context', '')
            live     = payload.get('live', '')
            date_str = payload.get('date', '')
            if not market:
                self.send_json({'error': 'market gerekli'}, 400)
                return
            ctx_part = ('Additional context: ' + context) if context else ''
            prompt = (
                f"You are a professional trading coach. Create a detailed daily trading plan "
                f"for {market} on {date_str}. Trader profile: {profile}. "
                f"{live} {ctx_part}\n\n"
                "Respond ONLY with a single valid JSON object. No markdown, no code fences, no extra text.\n"
                "All string values must be in Turkish. Use only standard ASCII double-quote characters.\n"
                "JSON structure:\n"
                '{"ozet":"...","piyasa_durumu":"yukselen|dusen|yatay","risk_seviyesi":"dusuk|orta|yuksek",'
                '"pre_market":{"saat":"08:00-09:30","baslik":"Pre-Market Tarama","gorevler":['
                '{"saat":"08:00","gorev":"...","detay":"..."},{"saat":"08:15","gorev":"...","detay":"..."},'
                '{"saat":"08:30","gorev":"...","detay":"..."},{"saat":"08:45","gorev":"...","detay":"..."},'
                '{"saat":"09:00","gorev":"...","detay":"..."},{"saat":"09:15","gorev":"...","detay":"..."}]},'
                '"acilis":{"saat":"09:30-11:00","baslik":"Acilis Stratejisi","gorevler":['
                '{"saat":"09:30","gorev":"...","detay":"..."},{"saat":"09:35","gorev":"...","detay":"..."},'
                '{"saat":"09:45","gorev":"...","detay":"..."},{"saat":"10:00","gorev":"...","detay":"..."},'
                '{"saat":"10:30","gorev":"...","detay":"..."}],'
                '"giris_kosullari":["...","...","..."],"kacin_kosullari":["...","..."]},'
                '"gun_ortasi":{"saat":"11:00-14:30","baslik":"Gun Ortasi Guncelleme","gorevler":['
                '{"saat":"11:00","gorev":"...","detay":"..."},{"saat":"12:00","gorev":"...","detay":"..."},'
                '{"saat":"13:00","gorev":"...","detay":"..."},{"saat":"14:00","gorev":"...","detay":"..."}],'
                '"pozisyon_kurallari":["...","...","..."]},'
                '"kapanis":{"saat":"14:30-18:00","baslik":"Kapanis Yaklasimi","gorevler":['
                '{"saat":"14:30","gorev":"...","detay":"..."},{"saat":"15:30","gorev":"...","detay":"..."},'
                '{"saat":"16:30","gorev":"...","detay":"..."},{"saat":"17:00","gorev":"...","detay":"..."},'
                '{"saat":"17:30","gorev":"...","detay":"..."}]},'
                '"gunden_sonra":{"baslik":"Gun Sonu Degerlendirme","gorevler":["...","...","..."]},'
                '"kritik_seviyeler":{"destek":["...","..."],"direnc":["...","..."],"pivot":"..."},'
                '"gun_kurallari":["...","...","...","..."],"max_islem":"5","risk_limiti":"..."}'
            )
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
