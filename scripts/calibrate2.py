# scripts/calibrate2.py
import asyncio, os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from twscrape import API, gather

load_dotenv()

CANDIDATES = [
    "#nifty50 OR #nifty OR $NIFTY",                      # baseline was 25
    "nifty",
    "nifty OR sensex OR banknifty",
    "sensex",
    "banknifty OR \"bank nifty\"",
    "NSE OR BSE",
    "\"stock market\" (india OR indian)",
    "\"share market\"",
    "intraday OR scalping OR \"option chain\"",
    "\"FII DII\" OR \"gap up\" OR \"gap down\"",
    "nifty (support OR resistance OR target OR breakout)",
    "reliance OR infosys OR tcs OR hdfc",
]

async def main():
    api = API()
    await api.pool.add_account_cookies(
        "burner1",
        f"auth_token={os.environ['X_AUTH_TOKEN']}; ct0={os.environ['X_CT0']}"
    )
    now = datetime.now(timezone.utc)
    end = int((now - timedelta(hours=8)).timestamp())
    start = int((now - timedelta(hours=9)).timestamp())

    for terms in CANDIDATES:
        q = f"{terms} since_time:{start} until_time:{end}"   # no lang, no RT filter
        n = len(await gather(api.search(q, limit=300)))
        print(f"{n:4d}  {terms}")

asyncio.run(main())