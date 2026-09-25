from datetime import datetime, timedelta
from pathlib import Path

out = Path('/tmp/observer_smoke')
out.mkdir(exist_ok=True)
for name, start, scale in [('eurusd', 1.08, 0.0005), ('xauusd', 2350.0, 2.5)]:
    price = start
    lines = ['datetime,open,high,low,close,volume']
    t = datetime(2024, 1, 1)
    for i in range(180):
        drift = scale * (1 if (i // 18) % 2 == 0 else -1)
        shock = scale * 5 if i in (70, 125) else 0
        old = price
        price = max(0.0001, price + drift + shock)
        high = max(old, price) + scale
        low = min(old, price) - scale
        lines.append(f'{(t + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")},{old:.8f},{high:.8f},{low:.8f},{price:.8f},{1000+i}')
    (out / f'{name}.csv').write_text('\n'.join(lines) + '\n')
print(out)
