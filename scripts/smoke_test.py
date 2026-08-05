import asyncio, os
from dotenv import load_dotenv
from twscrape import API, gather

load_dotenv()

async def main():
    api = API()
    token = os.environ["X_AUTH_TOKEN"]
    ct0 = os.environ["X_CT0"]
    await api.pool.add_account_cookies("burner1", f"auth_token={token}; ct0={ct0}")

    tweets = await gather(api.search("#nifty50 lang:en", limit=20))
    print(f"--- got {len(tweets)} tweets ---")
    for t in tweets[:5]:
        print(t.id, "|", t.user.username, "|", t.rawContent[:70].replace("\n", " "))

asyncio.run(main())